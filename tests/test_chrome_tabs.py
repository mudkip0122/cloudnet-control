from io import BytesIO
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import ssl
import struct
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.chrome_native_host import read_message, serve
from agent.chrome_tabs import ChromeTabMonitor, MAX_EVENTS, TabStore
from agent.exam import ExamMonitor
from agent.main import register, stream
from common.protocol import recv_message


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
        self.assertEqual([kind for kind, _ in events],
                         ["monitor_attached", "left", "returned", "closed", "monitor_detached"])
        self.assertEqual(self.send(attach, {"kind": "transition", "transition": "left", "url": "example"}),
                         ["monitoring", "invalid_message"])
        self.assertEqual([kind for kind, _ in self.store.poll("exam-session")[2]],
                         ["monitor_attached", "monitor_detached"])

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
        self.assertEqual([kind for kind, _ in self.store.poll("session")[2]],
                         ["monitor_attached", "camera_on", "monitor_detached"])

    def test_expired_host_is_reported_as_unavailable_and_can_be_rebound(self):
        with patch("agent.chrome_tabs.time.time", return_value=100):
            self.store.start("session")
            self.store.attach("host-one")
        with patch("agent.chrome_tabs.time.time", return_value=106):
            self.store.poll("session")
        with patch("agent.chrome_tabs.time.time", return_value=109):
            self.assertEqual(self.store.poll("session"), (False, 0, [("monitor_detached", 108)]))
            self.assertEqual(self.store.report("session", "host-one", "left"), "exam_off")
            self.assertEqual(self.store.attach("host-two"), ("monitoring", "session"))

    def test_queue_is_bounded_and_drained_once(self):
        self.store.start("session")
        self.store.attach("host")
        for _ in range(MAX_EVENTS):
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

    def test_timeline_uses_observation_times_and_keeps_quick_disconnect_and_reconnect(self):
        exam = ExamMonitor("PC-02", "timeline")
        monitor = ChromeTabMonitor(exam.pc_id, self.store)
        sent = []
        self.addCleanup(monitor.stop)
        with patch("agent.chrome_tabs.time.monotonic", side_effect=[10, 11, 12]), \
             patch("agent.chrome_tabs.time.time", return_value=100) as clock:
            monitor.poll(exam, sent.append)
            self.store.attach("host-one")
            clock.return_value = 100.25
            self.store.report(monitor.session, "host-one", "left")
            self.store.report(monitor.session, "host-one", "camera_on")
            clock.return_value = 101
            self.store.detach(monitor.session, "host-one")
            clock.return_value = 102
            self.store.attach("host-two")
            self.store.report(monitor.session, "host-two", "camera_off")
            monitor.poll(exam, sent.append)
            monitor.poll(exam, sent.append)

        self.assertEqual([(e["kind"], e.get("transition") or e["status"]) for e in sent], [
            ("EXAM_TAB_MONITOR_STATUS", "unavailable"), ("EXAM_TAB_MONITOR_STATUS", "monitoring"),
            ("EXAM_TAB_CHANGED", "left"), ("EXAM_CAMERA_STATUS", "on"),
            ("EXAM_TAB_MONITOR_STATUS", "unavailable"), ("EXAM_TAB_MONITOR_STATUS", "monitoring"),
            ("EXAM_CAMERA_STATUS", "off"),
        ])
        self.assertEqual(sent[2]["timestamp"], "1970-01-01T00:01:40.250000Z")
        self.assertEqual(sent[4]["timestamp"], "1970-01-01T00:01:41Z")
        self.assertEqual(len({event["event_id"] for event in sent}), len(sent))
        for event in sent:
            self.assertEqual((event["type"], event["version"], event["pc_id"], event["exam_id"]),
                             ("EXAM_EVENT", 1, "PC-02", "timeline"))

    def test_timeline_replaces_a_full_queue_with_coverage_notice_after_the_delivered_events(self):
        exam = ExamMonitor("PC-02", "timeline")
        monitor = ChromeTabMonitor(exam.pc_id, self.store)
        self.addCleanup(monitor.stop)
        sent = []
        with patch("agent.chrome_tabs.time.monotonic", side_effect=[10, 11]):
            monitor.poll(exam, sent.append)
            self.store.attach("host")
            for _ in range(MAX_EVENTS):
                self.store.report(monitor.session, "host", "returned")
            monitor.poll(exam, sent.append)
        self.assertEqual(sent[-2]["reason"], "event_buffer_overflow")
        self.assertEqual(sent[-2]["dropped_count"], 1)
        self.assertEqual(sent[-1]["status"], "monitoring")

    def test_timeline_capability_requires_authenticated_tls_and_is_reset_on_reconnect(self):
        args = Namespace(pc_id="PC-02", pc_name="Test", host="localhost", port=5000, token="test")
        connection = Mock(spec=ssl.SSLSocket)
        accepted = {"type": "REGISTER_RESULT", "ok": True, "pc_id": args.pc_id, "exam_events_v1": True}
        capability = accepted | {"exam_chrome_timeline_v1": True}
        with patch("agent.main.send_message"), patch("agent.main.recv_message", side_effect=[
                capability, accepted, capability | {"exam_events_v1": False}, capability, capability]):
            register(connection, args)
            self.assertTrue(args.session_chrome_timeline_enabled)
            register(connection, args)
            self.assertFalse(args.session_chrome_timeline_enabled)
            register(connection, args)
            self.assertFalse(args.session_chrome_timeline_enabled)
            register(Mock(), args)
            self.assertFalse(args.session_chrome_timeline_enabled)
            args.token = None
            register(connection, args)
            self.assertFalse(args.session_chrome_timeline_enabled)

    def test_stream_sends_negotiated_timeline_messages_over_the_agent_socket(self):
        args = Namespace(pc_id="PC-02", exam_mode=True, exam_id="timeline", forbid_process=["notepad.exe"],
                         exam_poll_interval=60, watch_chrome_tabs=True, session_chrome_timeline_enabled=True)
        monitor = ChromeTabMonitor(args.pc_id, self.store)
        client, peer = socket.socketpair()
        peer.settimeout(4)
        finished = Event()

        def samples(_pc_id):
            finished.wait(4)
            yield {"type": "METRICS", "pc_id": args.pc_id}

        with client, peer, patch("agent.main.ChromeTabMonitor", return_value=monitor), \
             patch("agent.collect.samples", samples), patch.object(ExamMonitor, "scan", return_value=[]), \
             ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(stream, client, args)
            try:
                self.assertEqual(recv_message(peer)["reason"], "awaiting_extension")
                self.assertEqual(self.send({"kind": "attach"}, {"kind": "transition", "transition": "left"},
                                           {"kind": "camera", "state": "on"}),
                                 ["monitoring", "monitoring", "monitoring"])
                events = [recv_message(peer) for _ in range(4)]
                self.assertEqual([event["kind"] for event in events], ["EXAM_TAB_MONITOR_STATUS",
                                 "EXAM_TAB_CHANGED", "EXAM_CAMERA_STATUS", "EXAM_TAB_MONITOR_STATUS"])
                self.assertEqual(events[-1]["status"], "unavailable")
            finally:
                finished.set()
            self.assertEqual(result.result(timeout=4), 1)
