"""RTT correlation, missing values and real framed session integration."""
from argparse import Namespace
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from agent.main import register, stream
from agent.rtt import RttTracker, validate_rtt_settings
from common.protocol import recv_message, send_message


class RttTests(unittest.TestCase):
    def test_monotonic_ms_and_duplicate_response(self):
        tracker = RttTracker("pc")
        with patch("agent.rtt.time.monotonic", return_value=100) as clock:
            probe = tracker.begin()
            clock.return_value = 100.025
            self.assertTrue(tracker.accept(probe | {"type": "RTT_PONG"}))
            snapshot = tracker.snapshot()
            self.assertEqual(snapshot["rtt_ms"], 25)
            self.assertEqual(snapshot["rtt_status"], "ok")
            self.assertTrue(snapshot["rtt_timestamp"].endswith("Z"))
            self.assertFalse(tracker.accept(probe))

    def test_timeout_late_unknown_response_and_recovery(self):
        tracker = RttTracker("pc", interval=1, timeout=.2)
        with patch("agent.rtt.time.monotonic", return_value=10) as clock:
            old = tracker.begin()
            self.assertEqual(tracker.snapshot()["rtt_status"], "pending")
            self.assertIsNone(tracker.snapshot()["rtt_ms"])
            clock.return_value = 10.3
            self.assertFalse(tracker.accept(old))
            self.assertEqual(tracker.snapshot()["rtt_status"], "timeout")
            self.assertIsNone(tracker.snapshot()["rtt_ms"])
            current = tracker.begin()
            clock.return_value = 10.31
            self.assertFalse(tracker.accept(old))
            self.assertTrue(tracker.accept(current))
            self.assertEqual(tracker.snapshot()["rtt_ms"], 10)
            clock.return_value = 12
            self.assertEqual(tracker.snapshot()["rtt_status"], "stale")
            self.assertIsNone(tracker.snapshot()["rtt_ms"])

    def test_wrong_pc_and_bad_probe_are_rejected(self):
        tracker = RttTracker("pc")
        probe = tracker.begin()
        for changes in ({"pc_id": "other"}, {"probe_id": None}, {"probe_id": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                tracker.accept(probe | changes)

    def test_reconnect_does_not_reuse_old_measurements_or_ids(self):
        old = RttTracker("pc").begin()
        new = RttTracker("pc")
        new.begin()
        self.assertFalse(new.accept(old))
        self.assertIsNone(new.snapshot()["rtt_ms"])

    def test_settings_validation(self):
        for interval, timeout in ((5, 3), (1, .1), (0, 3), (60, 60)):
            validate_rtt_settings(interval, timeout)
        for interval, timeout in ((True, 1), (5, False), (float("nan"), 1), (5, float("inf")),
                                  (.5, .1), (61, 1), (5, 6), (5, 0)):
            with self.subTest(interval=interval, timeout=timeout), self.assertRaises(ValueError):
                validate_rtt_settings(interval, timeout)

    def test_registration_enables_only_explicit_support_and_resets(self):
        args = Namespace(pc_id="pc", pc_name="PC", host="localhost", port=5000)
        base = {"type": "REGISTER_RESULT", "ok": True, "pc_id": "pc"}
        with patch("agent.main.send_message"), patch("agent.main.recv_message") as response:
            response.return_value = base | {"rtt_v1": True}
            register(Mock(), args)
            self.assertTrue(args.session_rtt_enabled)
            for caps in ({}, {"rtt_v1": 1}, {"rtt_v1": "true"}):
                response.return_value = base | caps
                register(Mock(), args)
                self.assertFalse(args.session_rtt_enabled)
            args.rtt_interval = 0
            response.return_value = base | {"rtt_v1": True}
            register(Mock(), args)
            self.assertFalse(args.session_rtt_enabled)

    def test_socket_roundtrip_is_in_metrics_and_worker_stops(self):
        client, server = socket.socketpair()
        server.settimeout(3)
        accepted = threading.Event()
        failures = []
        args = Namespace(pc_id="pc", session_rtt_enabled=True, rtt_interval=1, rtt_timeout=.5)
        original_accept = RttTracker.accept

        def accept(tracker, message):
            result = original_accept(tracker, message)
            accepted.set()
            return result

        def samples(_):
            if not accepted.wait(3):
                raise TimeoutError("RTT response not processed")
            yield {"type": "METRICS", "pc_id": "pc"}

        def run():
            try:
                stream(client, args)
            except BaseException as exc:
                failures.append(exc)

        with client, server, patch("agent.collect.samples", samples), patch.object(RttTracker, "accept", accept):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                ping = recv_message(server)
                self.assertEqual(ping["type"], "RTT_PING")
                send_message(server, ping | {"type": "RTT_PONG"})
                metrics = recv_message(server)
                self.assertEqual(metrics["type"], "METRICS")
                self.assertEqual(metrics["rtt_status"], "ok")
                self.assertGreaterEqual(metrics["rtt_ms"], 0)
                self.assertLess(metrics["rtt_ms"], 500)
            finally:
                accepted.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
        self.assertFalse(any(t.name == "agent-rtt" for t in threading.enumerate()))

    def test_no_reply_reports_null_without_disconnect(self):
        client, server = socket.socketpair()
        server.settimeout(3)
        failures = []
        args = Namespace(pc_id="pc", session_rtt_enabled=True, rtt_interval=1, rtt_timeout=.1)

        def samples(_):
            threading.Event().wait(.25)
            yield {"type": "METRICS", "pc_id": "pc"}

        def run():
            try:
                stream(client, args)
            except BaseException as exc:
                failures.append(exc)

        with client, server, patch("agent.collect.samples", samples):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertEqual(recv_message(server)["type"], "RTT_PING")
                metrics = recv_message(server)
                self.assertEqual(metrics["type"], "METRICS")
                self.assertIsNone(metrics["rtt_ms"])
                self.assertEqual(metrics["rtt_status"], "timeout")
            finally:
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
