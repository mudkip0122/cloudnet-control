"""Agent session lifecycle and heartbeat tests, without deployment secrets."""
from argparse import Namespace
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch

from agent.main import RegistrationError, heartbeat_interval, register, stream
from common.protocol import recv_message, send_message


class HeartbeatTests(unittest.TestCase):
    def test_interval_validation(self):
        for value in (0, 1, 5, 60):
            self.assertEqual(heartbeat_interval(value), value)
        for value in (-1, .5, 61, True, None, "5", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                heartbeat_interval(value)

    def test_registration_negotiates_and_resets_each_session(self):
        args = Namespace(pc_id="pc", pc_name="PC", host="localhost", port=5000,
                         heartbeat_interval=5)
        response = {"type": "REGISTER_RESULT", "ok": True, "pc_id": "pc"}
        with patch("agent.main.send_message"), patch("agent.main.recv_message") as receive:
            receive.return_value = response | {"heartbeat_interval": 2}
            register(Mock(), args)
            self.assertEqual(args.session_heartbeat_interval, 2)
            receive.return_value = response
            register(Mock(), args)
            self.assertEqual(args.session_heartbeat_interval, 5)
            receive.return_value = response | {"heartbeat_interval": "bad"}
            with self.assertRaises(ValueError):
                register(Mock(), args)

    def test_heartbeat_survives_slow_collection_and_accepts_ack(self):
        client, server = socket.socketpair()
        client.settimeout(3)
        server.settimeout(3)
        args = Namespace(pc_id="pc", session_heartbeat_interval=1)
        failures = []

        def slow_samples(_):
            time.sleep(2.3)
            yield {"type": "METRICS", "pc_id": "pc"}

        def run():
            try:
                stream(client, args)
            except BaseException as exc:
                failures.append(exc)

        with client, server, patch("agent.collect.samples", slow_samples):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                for _ in range(2):
                    message = recv_message(server)
                    self.assertEqual(message["type"], "HEARTBEAT")
                    self.assertEqual(message["pc_id"], "pc")
                    self.assertTrue(message["timestamp"].endswith("Z"))
                    send_message(server, {"type": "HEARTBEAT_ACK", "pc_id": "pc"})
                self.assertEqual(recv_message(server)["type"], "METRICS")
            finally:
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
        self.assertFalse(any(t.name in ("agent-receiver", "agent-heartbeat")
                             for t in threading.enumerate()))

    def test_server_error_stops_session_and_workers(self):
        client, server = socket.socketpair()
        client.settimeout(3)
        server.settimeout(3)
        args = Namespace(pc_id="pc", session_heartbeat_interval=1)

        def delayed_samples(_):
            time.sleep(.3)
            yield {"type": "METRICS", "pc_id": "pc"}

        with client, server, patch("agent.collect.samples", delayed_samples):
            send_message(server, {"type": "ERROR", "code": "UNAUTHORIZED"})
            with self.assertRaises(RegistrationError):
                stream(client, args)
        self.assertFalse(any(t.name in ("agent-receiver", "agent-heartbeat")
                             for t in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()
