"""Bounded explicit batch termination with per-process safety checks."""
from argparse import Namespace
from datetime import datetime, timedelta, timezone
import json
import socket
import threading
import unittest
from unittest.mock import Mock, patch

import psutil

from agent.main import stream
from agent.process_commands import ProcessCommands
from agent.processes import ProcessManager
from common.protocol import MAX_MESSAGE_BYTES, recv_message, send_message


def request(**changes):
    return {"type": "COMMAND", "command_id": "batch-1", "target_id": "pc",
            "action": "STOP_PROCESSES", "targets": [{"pid": 10, "create_time": 100.5},
                                                        {"pid": 11, "create_time": 200.5}],
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()} | changes


class BatchTests(unittest.TestCase):
    def test_invalid_whole_batch_never_stops_anything(self):
        invalid = [None, [], "all.exe", [None], [{"pid": True, "create_time": 1}],
                   [{"pid": 10, "create_time": float("nan")}], [{"pid": 10, "create_time": True}],
                   [{"pid": 10, "create_time": 1}, {"pid": 10, "create_time": 2}],
                   [{"pid": i + 1, "create_time": 1} for i in range(101)],
                   [{"pid": 10, "create_time": 1}, {"pid": 11}]]
        for targets in invalid:
            commands = ProcessCommands("pc", enabled=True)
            commands.manager = Mock()
            with self.subTest(targets=targets):
                self.assertEqual(commands.handle(request(targets=targets))[0]["code"], "INVALID_TARGETS")
                commands.manager.stop_many.assert_not_called()

    def test_authorization_target_expiry_and_duplicates(self):
        commands = ProcessCommands("pc")
        commands.manager = Mock()
        self.assertEqual(commands.handle(request())[0]["code"], "PROCESS_CONTROL_DISABLED")
        commands.enabled = True
        self.assertEqual(commands.handle(request(target_id="other"))[0]["code"], "WRONG_TARGET")
        self.assertEqual(commands.handle(request(expires_at="2020-01-01T00:00:00Z"))[0]["code"], "EXPIRED_OR_TOO_FAR")
        commands.manager.stop_many.assert_not_called()
        commands.manager.stop_many.return_value = [{"pid": 10, "create_time": 100.5, "ok": True, "code": "TERMINATED"}]
        self.assertTrue(commands.handle(request())[0]["ok"])
        self.assertEqual(commands.handle(request())[0]["code"], "DUPLICATE_COMMAND")
        commands.manager.stop_many.assert_called_once()

    def test_every_target_rechecks_live_identity_and_protection_and_continues(self):
        manager = ProcessManager()
        targets = [{"pid": i, "create_time": 100} for i in (10, 11, 12, 13, 14)]
        processes = {i: Mock(pid=i) for i in (10, 11, 12, 13, 14)}
        for proc in processes.values():
            proc.create_time.return_value = 100
        processes[11].create_time.return_value = 999
        processes[13].terminate.side_effect = psutil.AccessDenied(13)
        processes[14].wait.side_effect = psutil.TimeoutExpired(.1)
        with patch("agent.processes.psutil.Process", side_effect=lambda pid: processes[pid]), \
             patch.object(manager, "protection", side_effect=lambda proc: "PROTECTED_NAME" if proc.pid == 12 else None):
            outcomes = manager.stop_many(targets, datetime.now(timezone.utc) + timedelta(seconds=30))
        self.assertEqual([row["code"] for row in outcomes],
                         ["TERMINATED", "PID_REUSED", "PROTECTED", "ACCESS_DENIED", "TERMINATION_REQUESTED"])
        processes[11].terminate.assert_not_called()
        processes[12].terminate.assert_not_called()
        processes[10].terminate.assert_called_once_with()
        processes[14].terminate.assert_called_once_with()

    def test_batch_time_limit_marks_remaining_targets_without_signalling(self):
        manager = ProcessManager()
        with patch.object(manager, "stop", return_value={"ok": True, "code": "TERMINATED"}) as stop, \
             patch("agent.processes.time.monotonic", side_effect=[0, 0, 6]):
            outcomes = manager.stop_many(request()["targets"], datetime.now(timezone.utc) + timedelta(seconds=30))
        self.assertEqual([row["code"] for row in outcomes], ["TERMINATED", "BATCH_TIME_LIMIT"])
        stop.assert_called_once()

    def test_expiry_mid_batch_skips_remaining_targets(self):
        manager = ProcessManager()
        now = datetime.now(timezone.utc)
        with patch.object(manager, "stop", return_value={"ok": True, "code": "TERMINATED"}) as stop, \
             patch("agent.processes.datetime") as clock:
            clock.now.side_effect = [now, now + timedelta(seconds=2)]
            outcomes = manager.stop_many(request()["targets"], now + timedelta(seconds=1))
        self.assertEqual([row["code"] for row in outcomes], ["TERMINATED", "EXPIRED_DURING_BATCH"])
        stop.assert_called_once()

    def test_summary_distinguishes_partial_success_and_frame_is_bounded(self):
        commands = ProcessCommands("pc", enabled=True)
        commands.manager = Mock()
        commands.manager.stop_many.return_value = [
            {"pid": i + 1, "create_time": 100.5, "ok": i % 2 == 0,
             "code": "TERMINATED" if i % 2 == 0 else "PROTECTED",
             **({"reason": "SYSTEM_OR_UNKNOWN_EXECUTABLE"} if i % 2 else {})}
            for i in range(100)]
        result, = commands.handle(request(targets=[{"pid": i + 1, "create_time": 100.5} for i in range(100)]))
        self.assertEqual(result["counts"], {"TERMINATED": 50, "PROTECTED": 50})
        self.assertFalse(result["all_terminated"])
        self.assertTrue(result["ok"])
        self.assertLess(len(json.dumps(result).encode("utf-8")), MAX_MESSAGE_BYTES)

    def test_agent_socket_returns_one_batch_result_and_keeps_metrics(self):
        client, server = socket.socketpair()
        server.settimeout(3)
        done = threading.Event()
        commands = ProcessCommands("pc", enabled=True)
        commands.manager = Mock()
        commands.manager.stop_many.return_value = [
            {"pid": 10, "create_time": 100.5, "ok": True, "code": "TERMINATED"},
            {"pid": 11, "create_time": 200.5, "ok": False, "code": "PROTECTED", "reason": "PROTECTED_NAME"}]
        failures = []

        def samples(_):
            done.wait(4)
            yield {"type": "METRICS", "pc_id": "pc"}

        def run():
            try:
                stream(client, Namespace(pc_id="pc", process_commands=commands))
            except BaseException as exc:
                failures.append(exc)

        with client, server, patch("agent.collect.samples", samples):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                send_message(server, request())
                result = recv_message(server)
                self.assertEqual(result["code"], "PROCESS_BATCH_RESULT")
                self.assertEqual(result["counts"], {"TERMINATED": 1, "PROTECTED": 1})
                done.set()
                self.assertEqual(recv_message(server)["type"], "METRICS")
            finally:
                done.set()
                worker.join(6)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
