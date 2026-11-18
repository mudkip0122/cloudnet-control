from io import BytesIO
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.chrome_native_host import read_message, serve
from agent.chrome_tabs import ChromeTabMonitor, MAX_EVENTS, TabStore


def frame(**request):
    body = json.dumps(request).encode("utf-8")
    return struct.pack("=I", len(body)) + body


class ChromeTabTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.store = TabStore(Path(directory) / "chrome-tabs.sqlite3")

    def send(self, *requests):
        output = BytesIO()
        serve(BytesIO(b"".join(frame(**request) for request in requests)), output, self.store)
        output.seek(0)
        statuses = []
        while (response := read_message(output)) is not None:
            statuses.append(response["status"])
        return statuses

    def test_native_messages_require_active_exam_and_report_only_transitions(self):
        attach = {"kind": "attach"}
        self.assertEqual(self.send(attach), ["exam_off"])
        self.assertFalse(self.store.path.exists())
        self.assertTrue(self.store.start("exam-session"))
        self.assertEqual(self.send(attach, {"kind": "heartbeat"},
                                   {"kind": "transition", "transition": "left"},
                                   {"kind": "transition", "transition": "returned"},
                                   {"kind": "transition", "transition": "closed"}),
                         ["monitoring", "monitoring", "monitoring", "monitoring", "tab_closed"])
        attached, dropped, events = self.store.poll("exam-session")
        self.assertFalse(attached)
        self.assertEqual(dropped, 0)
        self.assertEqual([kind for kind, _ in events], ["left", "returned", "closed"])
        self.assertEqual(self.send(attach, {"kind": "transition", "transition": "left", "url": "example"}),
                         ["monitoring", "invalid_message"])
        self.assertEqual(self.store.poll("exam-session")[2], [])

    def test_binding_is_exclusive_and_old_exam_cannot_report_to_new_exam(self):
        self.assertTrue(self.store.start("first"))
        self.assertEqual(self.store.attach("host-one"), ("monitoring", "first"))
        self.assertEqual(self.store.attach("host-two"), ("tab_already_selected", None))
        self.assertFalse(self.store.start("second"))
        self.store.stop("first")
        self.assertTrue(self.store.start("second"))
        self.assertEqual(self.store.report("first", "host-one", "left"), "exam_off")
        self.assertEqual(self.store.poll("second"), (False, 0, []))
        self.assertEqual(self.store.attach("host-two"), ("monitoring", "second"))

    def test_waiting_for_exam_and_camera_status_never_accept_media_payloads(self):
        waiting = {"kind": "attach", "wait": True}
        self.assertEqual(self.send(waiting, waiting), ["waiting_for_exam", "waiting_for_exam"])
        self.assertFalse(self.store.path.exists())
        self.store.start("session")
        self.assertEqual(self.send(waiting, waiting, {"kind": "camera", "state": "on"},
                                   {"kind": "camera", "state": "off", "frame": "unexpected"}),
                         ["monitoring", "monitoring", "monitoring", "invalid_message"])
        self.assertEqual([kind for kind, _ in self.store.poll("session")[2]], ["camera_on"])

    def test_expired_host_is_reported_as_unavailable_and_can_be_rebound(self):
        with patch("agent.chrome_tabs.time.time", return_value=100):
            self.store.start("session")
            self.store.attach("host-one")
        with patch("agent.chrome_tabs.time.time", return_value=106):
            self.store.poll("session")
        with patch("agent.chrome_tabs.time.time", return_value=109):
            self.assertEqual(self.store.poll("session"), (False, 0, []))
            self.assertEqual(self.store.report("session", "host-one", "left"), "exam_off")
            self.assertEqual(self.store.attach("host-two"), ("monitoring", "session"))

    def test_queue_is_bounded_and_drained_once(self):
        self.store.start("session")
        self.store.attach("host")
        for _ in range(MAX_EVENTS + 1):
            self.store.report("session", "host", "left")
        attached, dropped, events = self.store.poll("session")
        self.assertTrue(attached)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(events), MAX_EVENTS)
        self.assertEqual(self.store.poll("session"), (True, 0, []))

    def test_agent_logs_and_releases_state_when_exam_ends(self):
        monitor = ChromeTabMonitor("PC-02", self.store)
        exam = SimpleNamespace(exam_id="demo")
        with self.assertLogs("cloudnet.agent", level="INFO") as logs:
            with patch("agent.chrome_tabs.time.monotonic", side_effect=[20, 21, 22]):
                monitor.poll(exam)
                status, session = self.store.attach("host")
                self.assertEqual(status, "monitoring")
                monitor.poll(exam)
                self.store.report(session, "host", "left")
                monitor.poll(exam)
            monitor.stop()
        self.assertIn("Chrome tab monitor unavailable", "\n".join(logs.output))
        self.assertIn("Chrome tab monitor monitoring", "\n".join(logs.output))
        self.assertIn("Chrome exam tab left: pc=PC-02 exam=demo", "\n".join(logs.output))
        self.assertEqual(self.store.report(session, "host", "returned"), "exam_off")

    def test_rejects_invalid_frame_before_reading_body(self):
        with self.assertRaises(ValueError):
            read_message(BytesIO(struct.pack("=I", 1025)))
