"""Remote exam lifecycle and Agent-only transport integration."""
from argparse import Namespace
from datetime import datetime, timedelta, timezone
import socket
import threading
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from agent.exam_commands import ExamCommands
from agent.main import RegistrationError, main, register, stream
from common.protocol import recv_message, send_message


def request(**changes):
    return {"type": "COMMAND", "command_id": str(uuid4()), "target_id": "pc",
            "action": "SET_EXAM_MODE", "enabled": True, "exam_id": "exam-1",
            "forbidden_processes": ["forbidden.exe"], "poll_interval": .5,
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()} | changes


class ExamCommandTests(unittest.TestCase):
    def test_omitted_list_uses_defaults_but_empty_or_null_is_invalid(self):
        commands = ExamCommands("pc", enabled=True)
        message = request()
        del message["forbidden_processes"]
        response = commands.handle(message)[0]
        self.assertTrue(response["ok"])
        self.assertIn("discord.exe", response["exam_mode"]["forbidden_processes"])
        self.assertNotIn("chrome.exe", response["exam_mode"]["forbidden_processes"])
        original = commands.monitor
        for names in ([], None):
            self.assertEqual(commands.handle(request(forbidden_processes=names))[0]["code"],
                             "INVALID_EXAM_SETTINGS")
            self.assertIs(commands.monitor, original)

    def test_disabled_and_invalid_requests_do_not_change_mode(self):
        commands = ExamCommands("pc")
        self.assertEqual(commands.handle(request())[0]["code"], "EXAM_CONTROL_DISABLED")
        commands.enabled = True
        for change, code in [({"target_id": "other"}, "WRONG_TARGET"),
                             ({"command_id": ""}, "INVALID_COMMAND_ID"),
                             ({"expires_at": "bad"}, "INVALID_EXPIRY"),
                             ({"expires_at": "2020-01-01T00:00:00Z"}, "EXPIRED_OR_TOO_FAR"),
                             ({"enabled": "true"}, "INVALID_EXAM_SETTINGS"),
                             ({"forbidden_processes": "a.exe"}, "INVALID_EXAM_SETTINGS"),
                             ({"forbidden_processes": [None]}, "INVALID_EXAM_SETTINGS"),
                             ({"poll_interval": float("nan")}, "INVALID_EXAM_SETTINGS"),
                             ({"action": "SHELL"}, "UNSUPPORTED_ACTION")]:
            with self.subTest(change=change):
                self.assertEqual(commands.handle(request(**change))[0]["code"], code)
                self.assertIsNone(commands.monitor)

    def test_enable_status_disable_and_no_detection_after_disable(self):
        commands = ExamCommands("pc", enabled=True)
        first = commands.handle(request())[0]
        self.assertEqual(first["code"], "EXAM_MODE_ENABLED")
        self.assertEqual(first["exam_mode"]["monitor_status"], "starting")
        monitor = commands.monitor
        monitor.scan = Mock(return_value=[{"type": "EXAM_EVENT"}])
        send = Mock()
        commands.poll(send)
        send.assert_called_once()
        status = commands.handle(request(action="GET_EXAM_STATUS"))[0]
        self.assertTrue(status["exam_mode"]["enabled"])
        self.assertEqual(commands.handle(request(enabled=False, exam_id="other"))[0]["code"], "EXAM_ID_MISMATCH")
        stopped = commands.handle(request(enabled=False))[0]
        self.assertEqual(stopped["code"], "EXAM_MODE_DISABLED")
        self.assertFalse(stopped["exam_mode"]["enabled"])
        commands.poll(send)
        send.assert_called_once()
        self.assertTrue(commands.handle(request(enabled=False))[0]["ok"])

    def test_bad_update_keeps_existing_policy_and_duplicate_is_rejected(self):
        commands = ExamCommands("pc", enabled=True)
        original = request()
        commands.handle(original)
        monitor = commands.monitor
        self.assertEqual(commands.handle(original)[0]["code"], "DUPLICATE_COMMAND")
        commands.handle(request(forbidden_processes=["*.exe"]))
        self.assertIs(commands.monitor, monitor)

    def test_reconnect_turns_remote_mode_off_and_retains_duplicate_cache(self):
        commands = ExamCommands("pc", enabled=True)
        original = request()
        commands.handle(original)
        commands.start_session()
        self.assertFalse(commands.state()["enabled"])
        self.assertEqual(commands.handle(original)[0]["code"], "DUPLICATE_COMMAND")

    def test_command_cache_is_bounded(self):
        commands = ExamCommands("pc", enabled=True)
        import time
        commands.seen = {str(i): time.monotonic() + 60 for i in range(4096)}
        self.assertEqual(commands.handle(request())[0]["code"], "COMMAND_CAPACITY")

    def test_remote_permission_requires_tls_and_live(self):
        for context, token, live in [(None, None, True), (Mock(), "test", False)]:
            argv = ["agent", "--allow-remote-exam-control"] + (["--live"] if live else [])
            with patch("sys.argv", argv), patch("agent.main.configure_logging"), \
                 patch("agent.main.client_settings", return_value=(context, token)), \
                 patch("agent.main.resolve_id", return_value="pc"), patch("agent.main.run") as run:
                self.assertEqual(main(), 2)
                run.assert_not_called()

    def test_remote_registration_needs_both_capabilities(self):
        args = Namespace(pc_id="pc", pc_name="PC", host="localhost", port=5000,
                         allow_remote_exam_control=True)
        base = {"type": "REGISTER_RESULT", "ok": True, "pc_id": "pc"}
        with patch("agent.main.send_message"), patch("agent.main.recv_message") as response:
            for caps in ({}, {"exam_events_v1": True}, {"exam_control_v1": True},
                         {"exam_events_v1": True, "exam_control_v1": 1}):
                response.return_value = base | caps
                with self.assertRaises(RegistrationError):
                    register(Mock(), args)
            response.return_value = base | {"exam_events_v1": True, "exam_control_v1": True}
            register(Mock(), args)

    def test_remote_socket_start_detect_stop_and_query_without_process_control(self):
        client, server = socket.socketpair()
        server.settimeout(3)
        done = threading.Event()
        commands = ExamCommands("pc", enabled=True)
        args = Namespace(pc_id="pc", exam_commands=commands)
        failures = []
        proc = Mock(pid=321)
        proc.name.return_value = "Forbidden.exe"
        proc.create_time.return_value = 1234.5

        def samples(_):
            done.wait(4)
            yield {"type": "METRICS", "pc_id": "pc"}

        def run():
            try:
                stream(client, args)
            except BaseException as exc:
                failures.append(exc)

        with client, server, patch("agent.collect.samples", samples), \
             patch("agent.exam.psutil.process_iter", return_value=[proc]):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                send_message(server, request())
                self.assertEqual(recv_message(server)["code"], "EXAM_MODE_ENABLED")
                self.assertEqual(recv_message(server)["kind"], "MONITOR_STATUS")
                self.assertEqual(recv_message(server)["kind"], "FORBIDDEN_PROCESS_DETECTED")
                send_message(server, request(enabled=False))
                self.assertEqual(recv_message(server)["code"], "EXAM_MODE_DISABLED")
                send_message(server, request(action="GET_EXAM_STATUS"))
                self.assertFalse(recv_message(server)["exam_mode"]["enabled"])
                done.set()
                self.assertEqual(recv_message(server)["type"], "METRICS")
            finally:
                done.set()
                worker.join(6)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
        self.assertFalse(any(t.name == "agent-exam" for t in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()
