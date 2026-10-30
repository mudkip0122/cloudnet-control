import socket
import threading
import unittest
from common.protocol import recv_message, send_message
from server.main import MonitoringServer


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.state = MonitoringServer()
        self.clients = []
        self.threads = []

    def connect(self, registration):
        client, server = socket.socketpair()
        client.settimeout(2)
        def serve():
            with server:
                self.state.handle(server)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.clients.append(client)
        self.threads.append(thread)
        send_message(client, registration)
        return client, recv_message(client)

    def tearDown(self):
        for conn in self.clients:
            conn.close()
        for thread in self.threads:
            thread.join(timeout=2)

    def agent(self):
        return self.connect({"type": "REGISTER", "role": "agent", "pc_id": "pc-b", "pc_name": "B"})

    def metric(self):
        return {"type": "METRICS", "pc_id": "pc-b", "timestamp": "2026-10-30T03:00:00Z",
                "cpu_percent": 20.5, "ram_percent": 50.0, "upload_mbps": None, "download_mbps": 1.2}

    def test_metrics_snapshot_and_disconnect(self):
        agent, _ = self.agent()
        admin, _ = self.connect({"type": "REGISTER", "role": "admin"})
        self.state.update_metrics("pc-b", self.metric())
        send_message(admin, {"type": "LIST_DEVICES"})
        device = recv_message(admin)["devices"][0]
        self.assertTrue(device["online"])
        self.assertEqual(device["metrics"], self.metric())
        agent.shutdown(socket.SHUT_RDWR)
        self.threads[0].join(timeout=2)
        send_message(admin, {"type": "LIST_DEVICES"})
        self.assertFalse(recv_message(admin)["devices"][0]["online"])

    def test_duplicate_id_does_not_disconnect_original(self):
        self.agent()
        _, response = self.agent()
        self.assertEqual(response["type"], "ERROR")
        self.assertTrue(self.state.snapshot()["devices"][0]["online"])

    def test_invalid_metrics_rejected(self):
        for changes in ({"pc_id": "other"}, {"cpu_percent": 101}, {"ram_percent": True},
                        {"upload_mbps": -1}, {"timestamp": "missing-timezone"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.state.update_metrics("pc-b", self.metric() | changes)


if __name__ == "__main__":
    unittest.main()
