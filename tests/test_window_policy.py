"""Window eligibility affects ordinary apps but never hides remote-control tools."""
import unittest
from unittest.mock import Mock, patch

from agent.exam import ExamMonitor
from agent.window_inventory import displayed_process_ids
from tests.test_exam import process


class WindowPolicyTests(unittest.TestCase):
    def scan(self, monitor, windows):
        with patch("agent.exam.displayed_process_ids", return_value=windows), \
             patch("agent.exam.psutil.process_iter", return_value=[
                 process(1, "Discord.exe"), process(2, "AnyDesk.exe"),
                 process(3, "firefox.exe"), process(4, "custom.exe")]):
            return monitor.scan()

    def detections(self, events):
        return {e["process"]["pid"] for e in events if e["kind"] == "FORBIDDEN_PROCESS_DETECTED"}

    def test_background_ordinary_apps_ignored_remote_detected(self):
        self.assertEqual(self.detections(self.scan(ExamMonitor("pc", "exam"), set())), {2})

    def test_displayed_ordinary_apps_detected(self):
        self.assertEqual(self.detections(self.scan(ExamMonitor("pc", "exam"), {1, 3})), {1, 2, 3})

    def test_restore_realerts_without_realerting_remote_process(self):
        monitor = ExamMonitor("pc", "exam")
        self.assertEqual(self.detections(self.scan(monitor, {1})), {1, 2})
        self.assertEqual(self.detections(self.scan(monitor, {1})), set())
        self.assertEqual(self.detections(self.scan(monitor, set())), set())
        self.assertEqual(self.detections(self.scan(monitor, {1})), {1})

    def test_window_failure_is_partial_and_retains_deduplication(self):
        monitor = ExamMonitor("pc", "exam")
        self.scan(monitor, {1})
        self.assertEqual(self.detections(self.scan(monitor, None)), set())
        self.assertEqual(monitor.health, "partial")
        self.assertEqual(self.detections(self.scan(monitor, {1})), set())

    def test_unknown_custom_name_still_detects_background(self):
        monitor = ExamMonitor("pc", "exam", ["custom.exe"])
        self.assertEqual(self.detections(self.scan(monitor, set())), {4})

    def test_non_windows_is_unknown(self):
        with patch("agent.window_inventory.sys.platform", "linux"):
            self.assertIsNone(displayed_process_ids())

    def test_native_hidden_minimized_and_cloaked_windows_are_excluded(self):
        user, dwm = Mock(), Mock()
        user.IsWindowVisible.side_effect = lambda hwnd: hwnd != 2
        user.IsIconic.side_effect = lambda hwnd: hwnd == 3
        def cloak(hwnd, attribute, output, size):
            output._obj.value = int(hwnd == 4)
            return 0
        def owner(hwnd, output):
            output._obj.value = 100 + hwnd
            return 1
        def enumerate_windows(callback, _):
            for hwnd in (1, 2, 3, 4):
                callback(hwnd, 0)
            return True
        user.EnumWindows.side_effect = enumerate_windows
        user.GetWindowThreadProcessId.side_effect = owner
        dwm.DwmGetWindowAttribute.side_effect = cloak
        with patch("agent.window_inventory.sys.platform", "win32"), \
             patch("agent.window_inventory.ctypes.WinDLL", side_effect=[user, dwm], create=True):
            self.assertEqual(displayed_process_ids(), {101})
