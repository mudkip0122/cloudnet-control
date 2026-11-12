"""A-owned acceptance tests; no Agent or GUI implementation imports."""
import socket
import ssl
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from common.protocol import recv_message, send_message
from server.main import MonitoringServer, Session


def metric(pc_id="pc", **changes):
    return dict(type="METRICS", pc_id=pc_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                cpu_percent=20, ram_percent=40, upload_mbps=None,
                download_mbps=0) | changes


class Week3Tests(unittest.TestCase):
    def setUp(self):
        self.state = MonitoringServer({"admin_token": "test-admin",
                                      "agents": {pc: "test-" + pc for pc in ("pc", "pc2", "pc3")}})
        self.clients = []
        self.threads = []

    def tearDown(self):
        for conn in self.clients:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        for thread in self.threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def connect(self, role="agent", pc_id="pc", token=None):
        client, server = socket.socketpair()
        client.settimeout(2)
        self.clients.append(client)
        def serve():
            with server:
                self.state.handle(server)
        thread = threading.Thread(target=serve, daemon=True)
        self.threads.append(thread)
        thread.start()
        send_message(client, dict(type="REGISTER", role=role, pc_id=pc_id, pc_name=pc_id,
                                  token=token or ("test-admin" if role == "admin" else "test-" + pc_id)))
        result = recv_message(client)
        return client, result

    def barrier(self, client):
        # An invalid next message proves all preceding frames were processed.
        send_message(client, {"type": "BARRIER"})
        self.assertEqual(recv_message(client)["type"], "ERROR")

    def test_three_authenticated_streams_and_load_changes_remain_separate(self):
        for index, pc_id in enumerate(("pc", "pc2", "pc3")):
            conn, result = self.connect(pc_id=pc_id)
            self.assertTrue(result["ok"])
            send_message(conn, metric(pc_id, cpu_percent=index * 10))
            send_message(conn, metric(pc_id, cpu_percent=index * 10 + 5))
            self.barrier(conn)
        admin, result = self.connect(role="admin")
        self.assertTrue(result["ok"])
        send_message(admin, {"type": "LIST_DEVICES"})
        devices = recv_message(admin)["devices"]
        self.assertEqual({d["pc_id"]: d["metrics"]["cpu_percent"] for d in devices},
                         {"pc": 5, "pc2": 15, "pc3": 25})
        self.assertTrue(all(d["last_metrics_received"] for d in devices))

    def test_wrong_token_and_spoofed_id_cannot_publish(self):
        _, result = self.connect(token="wrong")
        self.assertEqual(result["type"], "ERROR")
        self.assertFalse(self.state.devices)
        conn, _ = self.connect()
        send_message(conn, metric("pc2"))
        self.assertEqual(recv_message(conn)["type"], "ERROR")
        self.assertIsNone(self.state.snapshot()["devices"][0]["metrics"])

    def test_invalid_samples_leave_last_valid_sample_unchanged(self):
        self.connect()
        original = metric()
        self.state.update_metrics("pc", original)
        for changes in ({"cpu_percent": 101}, {"ram_percent": -1}, {"ram_percent": True},
                        {"upload_mbps": -1}, {"download_mbps": float("inf")},
                        {"download_mbps": 10 ** 400}, {"timestamp": "2026-11-12"},
                        {"timestamp": "bad"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.state.update_metrics("pc", original | changes)
            self.assertEqual(self.state.snapshot()["devices"][0]["metrics"], original)
        missing = dict(original)
        del missing["ram_percent"]
        with self.assertRaises(ValueError):
            self.state.update_metrics("pc", missing)

    def test_freshness_is_independent_of_heartbeat_and_agent_clock(self):
        conn, _ = self.connect()
        self.assertTrue(self.state.snapshot()["devices"][0]["metrics_stale"])
        self.state.update_metrics("pc", metric(timestamp="2020-01-01T00:00:00Z"))
        received = self.state.snapshot()["devices"][0]["last_metrics_received"]
        self.assertFalse(self.state.snapshot()["devices"][0]["metrics_stale"])
        with self.state.lock:
            self.state.metrics_received_at["pc"] = time.monotonic() - 7
        send_message(conn, {"type": "HEARTBEAT", "pc_id": "pc"})
        # Wait on the lock-protected observable value rather than a fixed sleep.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            device = self.state.snapshot()["devices"][0]
            if device["last_received"] != received:
                break
            time.sleep(0.01)
        self.assertNotEqual(device["last_received"], received)
        self.assertTrue(device["online"])
        self.assertTrue(device["metrics_stale"])
        self.assertEqual(device["last_metrics_received"], received)
        self.state.update_metrics("pc", metric())
        self.assertFalse(self.state.snapshot()["devices"][0]["metrics_stale"])

    def test_reconnect_clears_previous_metrics(self):
        conn, _ = self.connect()
        self.state.update_metrics("pc", metric())
        self.barrier(conn)
        self.threads[0].join(timeout=2)
        self.connect()
        device = self.state.snapshot()["devices"][0]
        self.assertIsNone(device["metrics"])
        self.assertIsNone(device["last_metrics_received"])
        self.assertTrue(device["metrics_stale"])

    def test_process_result_queues_without_waiting_for_slow_admin(self):
        admin = Mock(spec=ssl.SSLSocket)
        agent = Mock(spec=ssl.SSLSocket)
        session = Session(admin)
        self.state.connections[admin] = session
        self.state.sessions["pc"] = agent
        request = dict(type="COMMAND", command_id="list-1", target_id="pc", action="LIST_PROCESSES",
                       expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat())
        with patch.object(self.state, "send") as send:
            self.state.route_command(admin, request)
            send.assert_called_once_with(agent, request)
            # Holding the writer lock models a blocked administrator send.
            with session.send_lock:
                for index in (1, 0):
                    result = dict(type="COMMAND_RESULT", pc_id="pc", command_id="list-1",
                                  ok=True, code="PROCESS_LIST", page=index, pages=2, processes=[])
                    self.state.route_result(agent, "pc", result)
                    self.assertEqual(session.notifications.get_nowait(), result)
            send.assert_called_once_with(agent, request)
        self.assertFalse(self.state.pending_commands)

    def test_full_admin_queue_disconnects_only_that_admin(self):
        slow = Session(Mock(spec=socket.socket))
        healthy = Session(Mock(spec=socket.socket))
        self.state.connections[slow.conn] = slow
        self.state.connections[healthy.conn] = healthy
        for _ in range(slow.notifications.maxsize):
            slow.notifications.put_nowait({"type": "TEST"})
        self.state.notify_admin(slow.conn, {"type": "COMMAND_RESULT"})
        self.assertTrue(slow.stopped.is_set())
        self.state.notify_admin(healthy.conn, {"type": "COMMAND_RESULT"})
        self.assertFalse(healthy.stopped.is_set())
        self.assertEqual(healthy.notifications.get_nowait()["type"], "COMMAND_RESULT")
        self.connect()
        self.state.update_metrics("pc", metric())
        self.assertIsNotNone(self.state.snapshot()["devices"][0]["metrics"])


if __name__ == "__main__":
    unittest.main()
