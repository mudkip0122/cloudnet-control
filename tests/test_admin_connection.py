"""Admin lifecycle checks without creating a GUI window."""
import queue
import socket
import ssl
import threading
import unittest
from unittest.mock import patch

from admin_gui.main import Dashboard
from common.protocol import recv_message, send_message
from server.main import MonitoringServer


class AdminConnectionTests(unittest.TestCase):
    def dashboard(self):
        dashboard = Dashboard.__new__(Dashboard)
        dashboard.host, dashboard.port = "localhost", 0
        dashboard.context, dashboard.token = None, "admin-secret"
        dashboard.stop = threading.Event()
        dashboard.updates = queue.Queue(maxsize=1)
        dashboard.sock = None
        return dashboard

    def test_auth_and_certificate_errors_stop_without_retry_or_secret(self):
        for certificate in (False, True):
            dashboard = self.dashboard()
            client, peer = socket.socketpair()
            with client, peer:
                if not certificate:
                    send_message(peer, {"type": "ERROR", "code": "UNAUTHORIZED", "message": dashboard.token})
                with patch("admin_gui.main.connect", side_effect=ssl.SSLCertVerificationError()
                           if certificate else None, return_value=client) as connect:
                    dashboard.receive()
                self.assertEqual(connect.call_count, 1)
                connected, text = dashboard.updates.get_nowait()
                self.assertFalse(connected)
                self.assertIn("인증서 오류" if certificate else "인증 실패", text)
                self.assertNotIn(dashboard.token, text)

    def test_role_token_authorization_and_initial_snapshot(self):
        state = MonitoringServer("admin-secret", "agent-secret")
        for token, expected in (("agent-secret", "ERROR"), ("admin-secret", "REGISTER_RESULT")):
            client, peer = socket.socketpair()
            with client, peer:
                worker = threading.Thread(target=state.handle, args=(peer,))
                worker.start()
                send_message(client, {"type": "REGISTER", "role": "admin", "token": token})
                self.assertEqual(recv_message(client)["type"], expected)
                if expected == "REGISTER_RESULT":
                    send_message(client, {"type": "LIST_DEVICES"})
                    self.assertEqual(recv_message(client), {"type": "DEVICE_LIST", "devices": []})
                client.shutdown(socket.SHUT_RDWR)
                worker.join(2)
                self.assertFalse(worker.is_alive())

    def test_reconnect_authenticates_and_requests_full_snapshot_again(self):
        dashboard = self.dashboard()
        sessions = []
        workers = []
        requests = []

        def connect(*args):
            client, peer = socket.socketpair()
            sessions.append(client)

            def serve():
                with peer:
                    requests.append(recv_message(peer))
                    send_message(peer, {"type": "REGISTER_RESULT", "ok": True})
                    requests.append(recv_message(peer))
                    send_message(peer, {"type": "DEVICE_LIST", "devices": []})
                    if len(sessions) == 2:
                        dashboard.stop.set()
            worker = threading.Thread(target=serve)
            worker.start()
            workers.append(worker)
            return client

        with patch("admin_gui.main.connect", side_effect=connect), \
                patch.object(dashboard.stop, "wait", return_value=False):
            dashboard.receive()
        for worker in workers:
            worker.join(2)
        self.assertEqual(len(sessions), 2)
        self.assertEqual([message["type"] for message in requests],
                         ["REGISTER", "LIST_DEVICES"] * 2)
        self.assertTrue(all(message["token"] == "admin-secret" for message in requests[::2]))
        self.assertIsNone(dashboard.sock)


if __name__ == "__main__":
    unittest.main()
