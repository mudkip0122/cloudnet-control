"""Foreground transitions and capability gating without manipulating user windows."""
from argparse import Namespace
import unittest
from unittest.mock import Mock, patch

import psutil

from agent.exam import ExamMonitor
from agent.exam_commands import ExamCommands
from agent.foreground import ForegroundMonitor, foreground_process
from agent.main import register
from tests.test_exam_commands import request


def process(name, pid=1):
    return {"pid": pid, "name": name, "create_time": 123.0}


class ForegroundTests(unittest.TestCase):
    def scan_sequence(self, samples):
        monitor = ForegroundMonitor()
        event = lambda kind, **fields: {"kind": kind, **fields}
        with patch("agent.foreground.foreground_process", side_effect=samples):
            return [monitor.scan(event) for _ in samples]

    def test_chrome_exit_return_and_no_duplicate_events(self):
        chrome, other = process("Chrome.EXE"), process("notepad.exe", 2)
        scans = self.scan_sequence([chrome, chrome, other, other, chrome])
        self.assertEqual(scans[0], [{"kind": "FOREGROUND_MONITOR_STATUS", "status": "ok"}])
        self.assertEqual(scans[1], [])
        self.assertEqual(scans[2][0]["transition"], "left")
        self.assertEqual(scans[2][0]["process"], other)
        self.assertEqual(scans[3], [])
        self.assertEqual(scans[4][0]["transition"], "returned")

    def test_start_outside_chrome_is_baseline_and_chrome_windows_do_not_alert(self):
        scans = self.scan_sequence([process("notepad.exe"), process("calc.exe"),
                                    process("chrome.exe"), process("chrome.exe", 2)])
        self.assertEqual(len(scans[0]), 1)
        self.assertEqual(scans[1], [])
        self.assertEqual(scans[2][0]["transition"], "returned")
        self.assertEqual(scans[3], [])

    def test_gap_clears_baseline_without_inventing_transition(self):
        scans = self.scan_sequence([process("chrome.exe"), None, None, process("notepad.exe")])
        self.assertEqual(scans[1], [{"kind": "FOREGROUND_MONITOR_STATUS", "status": "unavailable"}])
        self.assertEqual(scans[2], [])
        self.assertEqual(scans[3], [{"kind": "FOREGROUND_MONITOR_STATUS", "status": "ok"}])

    def test_access_denied_does_not_stop_forbidden_process_monitor(self):
        monitor = ExamMonitor("pc", "exam", foreground_enabled=True)
        with patch("agent.exam.psutil.process_iter", return_value=[]), \
             patch("agent.foreground.foreground_process", side_effect=psutil.AccessDenied(1)):
            events = monitor.scan()
        self.assertEqual(events[-1]["status"], "unavailable")
        self.assertEqual(monitor.health, "ok")

    def test_disabled_feature_never_reads_foreground(self):
        with patch("agent.foreground.foreground_process") as read, \
             patch("agent.exam.psutil.process_iter", return_value=[]):
            ExamMonitor("pc", "exam").scan()
        read.assert_not_called()

    def test_remote_mode_uses_session_setting_and_new_monitor_resets_baseline(self):
        commands = ExamCommands("pc", enabled=True)
        commands.foreground_enabled = True
        commands.handle(request())
        first = commands.monitor.foreground
        first.previous = process("chrome.exe")
        commands.handle(request())
        self.assertIsNot(commands.monitor.foreground, first)
        self.assertIsNone(commands.monitor.foreground.previous)

    def test_registration_requires_literal_true_and_resets_on_reconnect(self):
        args = Namespace(pc_id="pc", pc_name="pc", host="localhost", port=5000)
        response = {"type": "REGISTER_RESULT", "ok": True, "pc_id": "pc"}
        with patch("agent.main.send_message"), patch("agent.main.recv_message") as receive:
            for value, expected in [(True, True), (1, False), ("true", False), (None, False)]:
                receive.return_value = response | {"exam_foreground_v1": value}
                register(Mock(), args)
                self.assertEqual(args.session_foreground_enabled, expected)

    def test_non_windows_reports_gap(self):
        with patch("agent.foreground.sys.platform", "linux"):
            self.assertIsNone(foreground_process())

    def test_no_foreground_window_does_not_read_process(self):
        api = Mock()
        api.GetForegroundWindow.return_value = 0
        with patch("agent.foreground.sys.platform", "win32"), \
             patch("agent.foreground.ctypes.WinDLL", return_value=api, create=True), \
             patch("agent.foreground.psutil.Process") as read:
            self.assertIsNone(foreground_process())
        read.assert_not_called()

    def test_disabling_exam_stops_foreground_sampling(self):
        commands = ExamCommands("pc", enabled=True)
        commands.foreground_enabled = True
        commands.handle(request())
        commands.handle(request(enabled=False))
        with patch("agent.foreground.foreground_process") as read:
            commands.poll(Mock())
        read.assert_not_called()

    def test_windows_api_identity_and_pointer_signatures(self):
        api = Mock()
        api.GetForegroundWindow.return_value = 2 ** 40
        def owner(window, output):
            output._obj.value = 42
            return 7
        api.GetWindowThreadProcessId.side_effect = owner
        proc = Mock()
        proc.oneshot.return_value.__enter__ = Mock()
        proc.oneshot.return_value.__exit__ = Mock(return_value=False)
        proc.name.return_value = "chrome.exe"
        proc.create_time.return_value = 123.0
        with patch("agent.foreground.sys.platform", "win32"), \
             patch("agent.foreground.ctypes.WinDLL", return_value=api, create=True), \
             patch("agent.foreground.psutil.Process", return_value=proc):
            self.assertEqual(foreground_process(), process("chrome.exe", 42))
        self.assertEqual(len(api.GetWindowThreadProcessId.argtypes), 2)
