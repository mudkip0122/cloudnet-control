"""A acceptance of B b6f76c0 events; never switches real user windows."""
from argparse import Namespace
from datetime import datetime, timedelta, timezone
import socket
import queue
import threading
import time
import unittest
from unittest.mock import patch

from agent.exam import ExamMonitor
from agent.exam_commands import ExamCommands
from agent.main import stream
from admin_gui.exam_events import ExamEventClient, ExamStore, event_columns
from common.protocol import recv_message, send_message
from server.contracts import exam_event
from tests import test_server_exam_stream as tls


def process(name="chrome.exe", pid=100):
    return dict(pid=pid, name=name, create_time=1234.5)


def event(kind="EXAM_WINDOW_CHANGED", **fields):
    result = dict(type="EXAM_EVENT", version=1, pc_id="PC-01", exam_id="exam-1", event_id="fg-1",
                  timestamp=datetime.now(timezone.utc).isoformat(), kind=kind)
    if kind == "EXAM_WINDOW_CHANGED":
        result.update(transition="left", previous_process=process(), process=process("notepad.exe", 200))
    else:
        result["status"] = "unavailable"
    return result | fields


class ForegroundContractTests(unittest.TestCase):
    def test_b_generated_transition_and_gap_events_are_accepted(self):
        monitor = ExamMonitor("PC-01", "exam-1", ["demo.exe"], foreground_enabled=True)
        samples = [process(), process("notepad.exe", 200), None, process("notepad.exe", 200), process()]
        observed = []
        with patch("agent.exam.psutil.process_iter", return_value=[]), \
                patch("agent.foreground.foreground_process", side_effect=samples):
            for _ in samples:
                for message in monitor.scan():
                    self.assertEqual(exam_event(message, "PC-01"), message)
                    observed.append(message)
        self.assertEqual([e["transition"] for e in observed if e["kind"] == "EXAM_WINDOW_CHANGED"],
                         ["left", "returned"])
        self.assertEqual([e["status"] for e in observed if e["kind"] == "FOREGROUND_MONITOR_STATUS"],
                         ["ok", "unavailable", "ok"])

    def test_invalid_transition_fields_and_process_identities(self):
        for changes in (dict(transition="unknown"), dict(transition="returned"), dict(process=None),
                        dict(previous_process=None), dict(process=process("chrome.exe", 200)),
                        dict(process=process("C:/notepad.exe", 200)),
                        dict(process=process("notepad.exe", True)),
                        dict(process=process("notepad.exe", 200) | dict(create_time=float("inf"))),
                        dict(process=process("notepad.exe", 200) | dict(create_time=0)),
                        dict(pc_id="other"), dict(version=True)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                exam_event(event(**changes), "PC-01")
        self.assertEqual(exam_event(event(previous_process=process("Chrome.EXE")), "PC-01")["transition"], "left")

    def test_foreground_status_is_independent_and_strict(self):
        for status in ("ok", "unavailable"):
            sample = event("FOREGROUND_MONITOR_STATUS", status=status)
            self.assertEqual(exam_event(sample, "PC-01"), sample)
        for status in (None, "partial", "off", True):
            with self.assertRaises(ValueError):
                exam_event(event("FOREGROUND_MONITOR_STATUS", status=status), "PC-01")


class ForegroundTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tls.ExamStreamTests.setUpClass.__func__(cls)

    setUp = tls.ExamStreamTests.setUp
    tearDown = tls.ExamStreamTests.tearDown
    open_tls = tls.ExamStreamTests.open_tls
    connect = tls.ExamStreamTests.connect
    barrier = tls.ExamStreamTests.barrier
    enable = tls.ExamStreamTests.enable

    def test_capability_requires_audit_storage_and_secure_connection(self):
        self.state.audit = None
        _, response = self.connect("agent")
        self.assertNotIn("exam_foreground_v1", response)

    def test_store_relay_dedup_and_independent_process_monitor_state(self):
        agent, response = self.connect("agent")
        self.assertIs(response["exam_foreground_v1"], True)
        subscriber, _ = self.connect(subscribe_exam=True)
        normal, _ = self.connect()
        self.enable(agent, normal)
        samples = [event("FOREGROUND_MONITOR_STATUS", status="ok", event_id="fg-ok"), event(),
                   event(transition="returned", previous_process=process("notepad.exe", 200),
                         process=process(), event_id="fg-returned"),
                   event("FOREGROUND_MONITOR_STATUS", event_id="fg-gap")]
        for sample in samples:
            send_message(agent, sample)
            self.assertEqual(recv_message(subscriber), sample)
        send_message(agent, samples[-1])
        self.barrier(agent)
        subscriber.settimeout(.1)
        with self.assertRaises(socket.timeout):
            recv_message(subscriber)
        send_message(normal, dict(type="LIST_DEVICES"))
        device = recv_message(normal)["devices"][0]
        self.assertEqual(device["exam_mode"]["monitor_status"], "starting")
        rows = self.audit.db.execute("SELECT payload, received_at FROM exam_events").fetchall()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(received for _, received in rows))

    def test_malformed_event_is_not_stored_or_relayed(self):
        agent, _ = self.connect("agent")
        subscriber, _ = self.connect(subscribe_exam=True)
        send_message(agent, event(transition="returned"))
        self.assertEqual(recv_message(agent)["type"], "ERROR")
        self.assertEqual(self.audit.db.execute("SELECT count(*) FROM exam_events").fetchone()[0], 0)
        # No foreground event is leaked to the subscriber, which remains usable.
        send_message(subscriber, dict(type="LIST_DEVICES"))
        self.assertEqual(recv_message(subscriber)["type"], "DEVICE_LIST")

    def test_c_b257588_receives_foreground_history_gaps_and_state_reset(self):
        agent, response = self.connect("agent")
        self.assertIs(response["exam_foreground_v1"], True)
        commander, _ = self.connect()
        subscription = self.open_tls()
        store = ExamStore()

        def wait_until(condition):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    _, connected, _ = client.connection.get_nowait()
                    store.foreground_stream_connected = connected
                except queue.Empty:
                    pass
                try:
                    _, message = client.events.get(timeout=.1)
                except queue.Empty:
                    continue
                if message["type"] == "DEVICE_LIST":
                    store.snapshot(message["devices"])
                elif message["type"] == "STATUS_UPDATE":
                    store.status_update(message["device"])
                else:
                    store.accept(message)
                if condition():
                    return
            self.fail("C did not receive the expected foreground event/state from A TLS")

        with patch("admin_gui.exam_events.connect", return_value=subscription):
            client = ExamEventClient("localhost", 0, self.client_context, self.auth["admin_token"])
            try:
                wait_until(lambda: "PC-01" in store.devices)
                self.enable(agent, commander)
                wait_until(lambda: (store.devices["PC-01"].get("exam_mode") or {}).get("enabled") is True)
                samples = [event("FOREGROUND_MONITOR_STATUS", status="ok", event_id="c-fg-ok"),
                           event(event_id="c-left"),
                           event(transition="returned", previous_process=process("notepad.exe", 200),
                                 process=process(), event_id="c-returned"),
                           event("FOREGROUND_MONITOR_STATUS", event_id="c-gap"),
                           event("FOREGROUND_MONITOR_STATUS", status="ok", event_id="c-recovery")]
                for sample in samples:
                    send_message(agent, sample)
                    wait_until(lambda: sample["event_id"] in store.seen)
                    if sample.get("status") == "unavailable":
                        self.assertIn("감시 공백", store.foreground_state("PC-01", True))
                self.assertIn("조회 가능", store.foreground_state("PC-01", True))
                self.assertEqual(event_columns(store.history[1])[0], "Chrome 이탈")
                self.assertEqual(event_columns(store.history[2])[0], "Chrome 복귀")
                self.assertIsNone(store.latest_warning)
                send_message(agent, samples[1])
                self.barrier(agent)
                request = dict(type="COMMAND", action="SET_EXAM_MODE", command_id="c-fg-off", target_id="PC-01",
                    expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                    enabled=False, exam_id="exam-1")
                send_message(commander, request)
                recv_message(agent)
                send_message(agent, self.handler.handle(request)[0])
                self.assertEqual(recv_message(commander)["code"], "EXAM_MODE_DISABLED")
                wait_until(lambda: (store.devices["PC-01"].get("exam_mode") or {}).get("enabled") is False)
                self.assertIn("감시 중지", store.foreground_state("PC-01", True))
                self.assertEqual(len(store.history), 5)
                self.assertNotIn("PC-01", store.foreground)
                self.assertEqual(self.audit.db.execute("SELECT count(*) FROM exam_events").fetchone()[0], 5)
            finally:
                client.close()
                client.worker.join(timeout=3)
            self.assertFalse(client.worker.is_alive())

    def test_actual_b_stream_reports_left_returned_and_monitor_gap(self):
        agent, registered = self.connect("agent")
        subscriber, _ = self.connect(subscribe_exam=True)
        commander, _ = self.connect()
        done, errors = threading.Event(), []
        args = Namespace(pc_id="PC-01", session_foreground_enabled=registered["exam_foreground_v1"],
                         exam_commands=ExamCommands("PC-01", enabled=True))
        samples = iter([process(), process("notepad.exe", 200), None, process(),
                        process("notepad.exe", 200), process()])

        def foreground():
            return next(samples, process())

        def metrics(pc_id):
            while not done.is_set():
                yield dict(type="METRICS", pc_id=pc_id, timestamp=datetime.now(timezone.utc).isoformat(),
                           cpu_percent=1, ram_percent=2, upload_mbps=0, download_mbps=0)
                done.wait(.1)

        def run():
            try:
                stream(agent, args)
            except BaseException as exc:
                errors.append(exc)

        with patch("agent.collect.samples", metrics), patch("agent.exam.psutil.process_iter", return_value=[]), \
                patch("agent.foreground.foreground_process", foreground):
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            try:
                request = dict(type="COMMAND", action="SET_EXAM_MODE", command_id="foreground-on", target_id="PC-01",
                    expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(), enabled=True,
                    exam_id="exam-1", forbidden_processes=["demo.exe"], poll_interval=.5)
                send_message(commander, request)
                self.assertEqual(recv_message(commander)["code"], "EXAM_MODE_ENABLED")
                kinds, transitions, statuses = set(), set(), set()
                while transitions != {"left", "returned"} or statuses != {"ok", "unavailable"}:
                    message = recv_message(subscriber)
                    kinds.add(message["kind"])
                    if message["kind"] == "EXAM_WINDOW_CHANGED":
                        transitions.add(message["transition"])
                    elif message["kind"] == "FOREGROUND_MONITOR_STATUS":
                        statuses.add(message["status"])
                self.assertIn("MONITOR_STATUS", kinds)
                self.assertEqual(self.state.snapshot()["devices"][0]["exam_mode"]["monitor_status"], "ok")
            finally:
                done.set()
                worker.join(timeout=4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
