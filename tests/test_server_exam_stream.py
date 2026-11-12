"""A -> C contract over real TLS using B's unchanged exam command handler."""
from datetime import datetime, timedelta, timezone
from argparse import Namespace
import socket
import queue
import ssl
import threading
import time
import unittest
from unittest.mock import Mock, patch

from agent.exam_commands import ExamCommands
from agent.main import stream
from admin_gui.exam_events import ExamEventClient, ExamStore
from admin_gui.presentation import device_view
from common.protocol import recv_message, send_message
from server.audit import AuditStore
from server.main import MonitoringServer
from tests import test_security as security


class ExamStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This existing fixture creates only disposable test credentials.
        security.SecureConnectionTests.setUpClass.__func__(cls)

    def setUp(self):
        self.audit = AuditStore(":memory:")
        self.state = MonitoringServer(self.auth, audit=self.audit)
        self.server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server_context.load_cert_chain(self.config / "server.crt", self.config / "server.key")
        self.client_context = ssl.create_default_context(cafile=str(self.config / "server.crt"))
        self.clients, self.threads = [], []

    def tearDown(self):
        for client in self.clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        for thread in self.threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.audit.close()

    def open_tls(self):
        raw_client, raw_server = socket.socketpair()
        def serve():
            with raw_server, self.server_context.wrap_socket(raw_server, server_side=True) as secured:
                self.state.handle(secured)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.threads.append(thread)
        client = self.client_context.wrap_socket(raw_client, server_hostname="127.0.0.1")
        client.settimeout(2)
        self.clients.append(client)
        return client

    def connect(self, role="admin", **fields):
        client = self.open_tls()
        request = dict(type="REGISTER", role=role)
        if role == "agent":
            request.update(pc_id="PC-01", pc_name="test", token=self.auth["agents"]["PC-01"])
        else:
            request["token"] = self.auth["admin_token"]
        send_message(client, request | fields)
        return client, recv_message(client)

    def barrier(self, agent):
        send_message(agent, dict(type="RTT_PING", pc_id="PC-01", probe_id="barrier"))
        self.assertEqual(recv_message(agent)["type"], "RTT_PONG")

    def event(self, **fields):
        return dict(type="EXAM_EVENT", version=1, pc_id="PC-01", event_id="event-1", exam_id="exam-1",
                    kind="MONITOR_STATUS", timestamp=datetime.now(timezone.utc).isoformat(),
                    status="partial", unreadable_count=2, poll_interval=1) | fields

    def enable(self, agent, admin):
        request = dict(type="COMMAND", action="SET_EXAM_MODE", command_id="enable-1", target_id="PC-01",
                       expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                       enabled=True, exam_id="exam-1", forbidden_processes=["demo.exe"], poll_interval=1)
        send_message(admin, request)
        self.assertEqual(recv_message(agent), request)
        handler = ExamCommands("PC-01", enabled=True)
        handler.start_session()
        self.handler = handler
        result = handler.handle(request)[0]
        send_message(agent, result)
        self.assertEqual(recv_message(admin), result)
        return result

    def test_raw_event_only_to_subscribers_after_durable_deduplication(self):
        agent, registered = self.connect("agent")
        self.assertTrue(registered["exam_events_v1"])
        self.assertTrue(registered["exam_control_v1"])
        subscriber, _ = self.connect(subscribe_exam=True)
        normal, _ = self.connect()
        event = self.event()
        send_message(agent, event)
        self.assertEqual(recv_message(subscriber), event)
        send_message(agent, event)
        self.barrier(agent)
        subscriber.settimeout(.1)
        with self.assertRaises(socket.timeout):
            recv_message(subscriber)
        send_message(normal, dict(type="LIST_DEVICES"))
        self.assertEqual(recv_message(normal)["type"], "DEVICE_LIST")
        self.assertEqual(self.audit.db.execute("SELECT count(*) FROM exam_events").fetchone()[0], 1)

    def test_other_admin_receives_mode_and_monitor_status_updates(self):
        agent, _ = self.connect("agent")
        observer, _ = self.connect(subscribe_status=True, subscribe_exam=True)
        commander, _ = self.connect()
        result = self.enable(agent, commander)
        status = recv_message(observer)
        self.assertEqual(status["type"], "STATUS_UPDATE")
        self.assertEqual(status["device"]["exam_mode"], result["exam_mode"])
        self.assertIsNotNone(status["device"]["exam_state_received_at"])
        event = self.event()
        send_message(agent, event)
        self.assertEqual(recv_message(observer), event)
        self.assertEqual(recv_message(observer)["device"]["exam_mode"]["monitor_status"], "partial")
        send_message(commander, dict(type="LIST_DEVICES"))
        device = recv_message(commander)["devices"][0]
        self.assertEqual(device["exam_mode"]["monitor_status"], "partial")

    def test_agent_reconnect_invalidates_exam_and_measurements(self):
        agent, _ = self.connect("agent")
        observer, _ = self.connect(subscribe_status=True)
        commander, _ = self.connect()
        self.enable(agent, commander)
        session_id = recv_message(observer)["device"]["agent_session_id"]
        agent.shutdown(socket.SHUT_RDWR)
        offline = recv_message(observer)["device"]
        self.assertFalse(offline["online"])
        self.assertIsNone(offline["exam_mode"])
        self.assertIsNone(offline["exam_state_received_at"])
        self.connect("agent")
        online = recv_message(observer)["device"]
        self.assertTrue(online["online"])
        self.assertNotEqual(online["agent_session_id"], session_id)
        self.assertIsNone(online["exam_mode"])
        self.assertIsNone(online["metrics"])
        self.assertTrue(online["rtt_stale"])

    def test_disable_result_is_reflected_on_other_admin(self):
        agent, _ = self.connect("agent")
        observer, _ = self.connect(subscribe_status=True)
        commander, _ = self.connect()
        self.enable(agent, commander)
        recv_message(observer)
        request = dict(type="COMMAND", action="SET_EXAM_MODE", command_id="disable-1", target_id="PC-01",
                       expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                       enabled=False, exam_id="exam-1")
        send_message(commander, request)
        self.assertEqual(recv_message(agent), request)
        result = self.handler.handle(request)[0]
        send_message(agent, result)
        self.assertEqual(recv_message(commander), result)
        mode = recv_message(observer)["device"]["exam_mode"]
        self.assertFalse(mode["enabled"])
        self.assertEqual(mode["monitor_status"], "off")

    def test_mode_result_after_requester_disconnect_is_still_broadcast_and_audited(self):
        agent, _ = self.connect("agent")
        observer, _ = self.connect(subscribe_status=True)
        commander, _ = self.connect()
        request = dict(type="COMMAND", action="GET_EXAM_STATUS", command_id="closed-1", target_id="PC-01",
                       expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat())
        send_message(commander, request)
        recv_message(agent)
        commander.shutdown(socket.SHUT_RDWR)
        self.threads[-1].join(timeout=2)
        handler = ExamCommands("PC-01", enabled=True)
        handler.start_session()
        send_message(agent, handler.handle(request)[0])
        self.assertFalse(recv_message(observer)["device"]["exam_mode"]["enabled"])
        self.assertEqual(self.audit.db.execute("SELECT outcome FROM command_audit").fetchone()[0], "result_received")

    def test_rtt_repeat_does_not_refresh_independent_server_age(self):
        self.connect("agent")
        sample = dict(type="METRICS", pc_id="PC-01", timestamp="2020-01-01T00:00:00Z",
                      cpu_percent=1, ram_percent=2, upload_mbps=None, download_mbps=0,
                      rtt_ms=24.2, rtt_status="ok", rtt_timestamp="2020-01-01T00:00:00Z")
        with patch("server.main.time.monotonic", return_value=100):
            self.state.update_metrics("PC-01", sample)
        first = self.state.devices["PC-01"]["last_metrics_received"]
        with patch("server.main.time.monotonic", return_value=109):
            self.state.update_metrics("PC-01", sample)
            device = self.state.snapshot()["devices"][0]
        self.assertFalse(device["metrics_stale"])
        self.assertTrue(device["rtt_stale"])
        self.assertEqual(device["rtt_age_seconds"], 9)
        self.assertEqual(device["last_rtt_received"], first)
        self.assertEqual(device["metrics"]["rtt_timestamp"], sample["rtt_timestamp"])
        with patch("server.main.time.monotonic", return_value=110):
            self.state.update_metrics("PC-01", sample | dict(rtt_timestamp="2020-01-01T00:00:05Z"))
            device = self.state.snapshot()["devices"][0]
        self.assertFalse(device["rtt_stale"])
        self.assertEqual(device["rtt_age_seconds"], 0)

    def test_subscription_flag_and_role_are_validated(self):
        for role, fields in (("agent", dict(subscribe_exam=True)), ("admin", dict(subscribe_exam="true"))):
            _, result = self.connect(role, **fields)
            self.assertEqual(result["type"], "ERROR")

    def test_c_35004c3_client_receives_a_events_mode_rtt_and_reconnect(self):
        agent, _ = self.connect("agent")
        commander, _ = self.connect()
        subscription = self.open_tls()
        store = ExamStore()

        def wait_until(condition):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
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
            self.fail("Actual C client did not receive the expected A state/event")

        # Only dialing is replaced: C's unchanged worker uses the real TLS A
        # connection, registration, parsing, queues, keepalive and polling.
        with patch("admin_gui.exam_events.connect", return_value=subscription):
            client = ExamEventClient("localhost", 0, self.client_context, self.auth["admin_token"])
            try:
                wait_until(lambda: "PC-01" in store.devices)
                old_session = store.devices["PC-01"]["agent_session_id"]
                self.enable(agent, commander)
                detection = self.event(kind="FORBIDDEN_PROCESS_DETECTED",
                                       process=dict(pid=12345, name="demo.exe", create_time=1.25))
                send_message(agent, detection)
                send_message(agent, detection)
                sample = dict(type="METRICS", pc_id="PC-01", timestamp=datetime.now(timezone.utc).isoformat(),
                              cpu_percent=10, ram_percent=20, upload_mbps=0, download_mbps=0,
                              rtt_ms=24.2, rtt_status="ok", rtt_timestamp=datetime.now(timezone.utc).isoformat())
                send_message(agent, sample)
                wait_until(lambda: store.latest_warning is not None
                           and (store.devices["PC-01"].get("exam_mode") or {}).get("enabled") is True
                           and (store.devices["PC-01"].get("metrics") or {}).get("rtt_status") == "ok")
                self.assertEqual(len(store.history), 1)
                self.assertEqual(device_view(store.devices["PC-01"])["rtt"], "24.2 ms")
                request = dict(type="COMMAND", action="SET_EXAM_MODE", command_id="c-off", target_id="PC-01",
                               expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                               enabled=False, exam_id="exam-1")
                send_message(commander, request)
                recv_message(agent)
                send_message(agent, self.handler.handle(request)[0])
                self.assertEqual(recv_message(commander)["code"], "EXAM_MODE_DISABLED")
                wait_until(lambda: (store.devices["PC-01"].get("exam_mode") or {}).get("enabled") is False)
                self.assertIn("꺼짐", store.state("PC-01", True))
                agent.shutdown(socket.SHUT_RDWR)
                self.threads[0].join(timeout=2)
                self.connect("agent")
                wait_until(lambda: store.devices["PC-01"].get("agent_session_id") != old_session)
                self.assertIsNone(store.devices["PC-01"]["exam_mode"])
                self.assertIn("미확인", store.state("PC-01", True))
                self.assertEqual(len(store.history), 1)
            finally:
                client.close()
                client.worker.join(timeout=3)
            self.assertFalse(client.worker.is_alive())

    def test_unchanged_b_stream_exam_detection_disable_and_rtt(self):
        agent, registered = self.connect("agent")
        observer, _ = self.connect(subscribe_exam=True, subscribe_status=True)
        commander, _ = self.connect()
        done = threading.Event()
        errors = []
        args = Namespace(pc_id="PC-01", session_heartbeat_interval=registered["heartbeat_interval"],
                         session_rtt_enabled=registered["rtt_v1"], rtt_interval=1, rtt_timeout=.2,
                         exam_commands=ExamCommands("PC-01", enabled=True))
        process = Mock(pid=12345)
        process.name.return_value = "demo.exe"
        process.create_time.return_value = 1.25

        def samples(pc_id):
            while not done.is_set():
                yield dict(type="METRICS", pc_id=pc_id, timestamp=datetime.now(timezone.utc).isoformat(),
                           cpu_percent=1, ram_percent=2, upload_mbps=0, download_mbps=0)
                done.wait(.05)

        def run():
            try:
                stream(agent, args)
            except BaseException as exc:
                errors.append(exc)

        def command(action, command_id, **fields):
            send_message(commander, dict(type="COMMAND", action=action, command_id=command_id,
                target_id="PC-01", expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(), **fields))
            return recv_message(commander)

        with patch("agent.collect.samples", samples), patch("agent.exam.psutil.process_iter", return_value=[process]):
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            try:
                result = command("SET_EXAM_MODE", "stream-on", enabled=True, exam_id="exam-1",
                                 forbidden_processes=["demo.exe"], poll_interval=.5)
                self.assertEqual(result["code"], "EXAM_MODE_ENABLED")
                while True:
                    message = recv_message(observer)
                    if message["type"] == "EXAM_EVENT" and message["kind"] == "FORBIDDEN_PROCESS_DETECTED":
                        break
                self.assertEqual(message["process"]["pid"], 12345)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    send_message(commander, dict(type="LIST_DEVICES"))
                    device = recv_message(commander)["devices"][0]
                    if (device.get("metrics") or {}).get("rtt_status") == "ok":
                        break
                    time.sleep(.05)
                self.assertEqual(device["metrics"]["rtt_status"], "ok")
                self.assertFalse(device["rtt_stale"])
                self.assertEqual(command("SET_EXAM_MODE", "stream-off", enabled=False, exam_id="exam-1")["code"],
                                 "EXAM_MODE_DISABLED")
                self.assertFalse(command("GET_EXAM_STATUS", "stream-status")["exam_mode"]["enabled"])
            finally:
                done.set()
                worker.join(timeout=4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
