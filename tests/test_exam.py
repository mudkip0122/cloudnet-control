"""Exam detection and wire/session checks without deployment credentials."""
from argparse import Namespace
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch

import psutil

from agent.exam import ExamMonitor
from agent.main import RegistrationError, main, register, stream
from common.protocol import recv_message


def process(pid=21, name="Forbidden.EXE", created=100):
    item = Mock(pid=pid)
    item.name.return_value = name
    item.create_time.return_value = created
    return item


class ExamTests(unittest.TestCase):
    def test_default_policy_detects_messenger_but_allows_exam_chrome(self):
        monitor = ExamMonitor("pc", "exam")
        with patch("agent.exam.displayed_process_ids", return_value={21, 24}), patch("agent.exam.psutil.process_iter", return_value=[
                process(21, "Discord.EXE"), process(22, "chrome.exe"),
                process(23, "AnyDesk.exe"), process(24, "firefox.exe")]):
            events = self.detections(monitor.scan())
        self.assertEqual({event["process"]["pid"] for event in events}, {21, 23, 24})
        self.assertEqual(ExamMonitor("pc", "exam", ["custom.exe"]).names, {"custom.exe"})

    def test_real_process_inventory_detects_this_test_process_without_stopping_it(self):
        own = psutil.Process()
        monitor = ExamMonitor("test-pc", "local-test", [own.name()])
        events = self.detections(monitor.scan())
        self.assertTrue(any(event["process"]["pid"] == own.pid
                            and event["process"]["create_time"] == own.create_time()
                            for event in events))
        self.assertTrue(own.is_running())

    def monitor(self):
        return ExamMonitor("PC-02", "exam-1", ["forbidden.exe"])

    def detections(self, events):
        return [event for event in events if event["kind"] == "FORBIDDEN_PROCESS_DETECTED"]

    def test_existing_process_exact_case_insensitive_match_and_no_private_fields(self):
        with patch("agent.exam.psutil.process_iter", return_value=[process(), process(22, "notforbidden.exe")]):
            events = self.monitor().scan()
        self.assertEqual(events[0]["status"], "ok")
        event, = self.detections(events)
        self.assertEqual(event["pc_id"], "PC-02")
        self.assertEqual(event["exam_id"], "exam-1")
        self.assertEqual(event["process"], {"pid": 21, "name": "Forbidden.EXE", "create_time": 100})
        self.assertTrue(event["timestamp"].endswith("Z"))
        self.assertEqual(set(event), {"type", "version", "event_id", "pc_id", "exam_id", "kind", "timestamp", "process"})

    def test_dedup_exit_restart_and_pid_reuse(self):
        monitor = self.monitor()
        with patch("agent.exam.psutil.process_iter") as listing:
            listing.return_value = [process()]
            self.assertEqual(len(self.detections(monitor.scan())), 1)
            self.assertEqual(monitor.scan(), [])
            listing.return_value = [process(created=200)]
            self.assertEqual(len(self.detections(monitor.scan())), 1)
            listing.return_value = []
            monitor.scan()
            listing.return_value = [process(created=200)]
            self.assertEqual(len(self.detections(monitor.scan())), 1)

    def test_unreadable_process_reports_partial_and_recovers_without_duplicate(self):
        monitor = self.monitor()
        denied = process()
        denied.name.side_effect = psutil.AccessDenied(21)
        with patch("agent.exam.psutil.process_iter") as listing:
            listing.return_value = [process()]
            monitor.scan()
            listing.return_value = [denied]
            self.assertEqual(monitor.scan()[0]["status"], "partial")
            self.assertEqual(monitor.scan(), [])
            listing.return_value = [process()]
            events = monitor.scan()
            self.assertEqual(events[0]["status"], "ok")
            self.assertEqual(self.detections(events), [])

    def test_scan_error_does_not_claim_no_violations_or_repeat_existing(self):
        monitor = self.monitor()
        with patch("agent.exam.psutil.process_iter", return_value=[process()]):
            monitor.scan()
        with patch("agent.exam.psutil.process_iter", side_effect=OSError()):
            self.assertEqual(monitor.scan()[0]["status"], "unavailable")
        with patch("agent.exam.psutil.process_iter", return_value=[process()]):
            self.assertEqual(self.detections(monitor.scan()), [])

    def test_exited_process_is_ignored(self):
        gone = process()
        gone.name.side_effect = psutil.NoSuchProcess(21)
        with patch("agent.exam.psutil.process_iter", return_value=[gone]):
            self.assertEqual(self.detections(self.monitor().scan()), [])

    def test_invalid_configuration(self):
        for exam_id, names, interval in [(None, ["a.exe"], 1), ("space id", ["a.exe"], 1),
                                         ("exam", [], 1), ("exam", ["C:\\app.exe"], 1),
                                         ("exam", ["*.exe"], 1), ("exam", [" a.exe"], 1),
                                         ("exam", ["a.exe"], float("nan")), ("exam", ["a.exe"], 0)]:
            with self.subTest(exam_id=exam_id, names=names, interval=interval), self.assertRaises(ValueError):
                ExamMonitor("pc", exam_id, names, interval)

    def test_registration_requires_explicit_capability_each_time(self):
        args = Namespace(pc_id="pc", pc_name="PC", host="localhost", port=5000, exam_mode=True)
        base = {"type": "REGISTER_RESULT", "ok": True, "pc_id": "pc"}
        with patch("agent.main.send_message"), patch("agent.main.recv_message") as response:
            response.return_value = base | {"exam_events_v1": True}
            register(Mock(), args)
            for capability in (None, False, 1, "true"):
                response.return_value = base | {"exam_events_v1": capability}
                with self.assertRaises(RegistrationError):
                    register(Mock(), args)
            args.exam_mode = False
            response.return_value = base
            register(Mock(), args)

    def test_cli_requires_live_authenticated_tls(self):
        with patch("sys.argv", ["agent", "--exam-mode", "--exam-id", "test", "--forbid-process", "a.exe"]), \
             patch("agent.main.configure_logging"), patch("agent.main.client_settings", return_value=(None, None)), \
             patch("agent.main.resolve_id", return_value="pc"), patch("agent.main.run") as run:
            self.assertEqual(main(), 2)
            run.assert_not_called()

    def test_exam_events_and_metrics_share_framing_and_worker_stops(self):
        client, server = socket.socketpair()
        server.settimeout(3)
        args = Namespace(pc_id="pc", exam_mode=True, exam_id="test", forbid_process=["forbidden.exe"],
                         exam_poll_interval=.5)
        failures = []

        def samples(_):
            time.sleep(.15)
            yield {"type": "METRICS", "pc_id": "pc"}

        def run():
            try:
                stream(client, args)
            except BaseException as exc:
                failures.append(exc)

        with client, server, patch("agent.collect.samples", samples), \
             patch("agent.exam.psutil.process_iter", return_value=[process()]):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                messages = [recv_message(server) for _ in range(3)]
                self.assertEqual([m["type"] for m in messages], ["EXAM_EVENT", "EXAM_EVENT", "METRICS"])
            finally:
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
        self.assertFalse(any(t.name == "agent-exam" for t in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()
