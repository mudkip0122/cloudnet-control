from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent.main import register
from agent.power_commands import PowerCommands, request_windows_power


class PowerCommandTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(folder) / "power.sqlite3"
        self.boot_id = "2026-11-18T01:02:03Z"
        self.request = {"type": "COMMAND", "action": "REBOOT_PC", "command_id": "test-1",
                        "target_id": "PC-02", "target_boot_id": self.boot_id,
                        "power_control_mode": "simulate",
                        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()}

    def handler(self, mode="simulate", path=None):
        return PowerCommands("PC-02", mode, self.boot_id, path or self.path)

    def send(self, handler=None, supported=True, **changes):
        results = []
        (handler or self.handler()).handle(self.request | changes, results.append, server_supported=supported)
        for result in results:
            self.assertEqual(result["type"], "COMMAND_RESULT")
            self.assertEqual(result["pc_id"], "PC-02")
        return results

    def test_simulates_both_actions_and_persists_replay_protection(self):
        with patch("agent.power_commands.request_windows_power") as execute:
            for index, action in enumerate(("REBOOT_PC", "SHUTDOWN_PC")):
                options = {"action": action, "command_id": f"test-{index}"}
                results = self.send(**options)
                self.assertEqual([(r["code"], r["ok"], r["terminal"]) for r in results], [
                    ("POWER_ACCEPTED", True, False), ("POWER_SIMULATED", True, True)])
                self.assertEqual(self.send(**options)[0]["code"], "DUPLICATE_COMMAND")
            execute.assert_not_called()

    def test_concurrent_reservation_allows_only_one_handler(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.send)
            second = executor.submit(self.send)
            self.assertCountEqual([first.result()[0]["code"], second.result()[0]["code"]],
                                  ["POWER_ACCEPTED", "DUPLICATE_COMMAND"])

    def test_rejects_disabled_wrong_target_stale_boot_and_invalid_requests(self):
        cases = [({"handler": self.handler("off")}, "POWER_CONTROL_DISABLED"),
                 ({"supported": False}, "POWER_CONTROL_DISABLED"),
                 ({"action": "CMD"}, "UNSUPPORTED_ACTION"),
                 ({"target_id": "PC-03"}, "WRONG_TARGET"),
                 ({"target_boot_id": "stale-boot"}, "BOOT_ID_MISMATCH"),
                 ({"power_control_mode": "execute"}, "POWER_MODE_MISMATCH"),
                 ({"command_id": " "}, "INVALID_COMMAND_ID"),
                 ({"expires_at": "bad"}, "INVALID_EXPIRY"),
                 ({"expires_at": "2000-01-01T00:00:00"}, "INVALID_EXPIRY"),
                 ({"expires_at": "2000-01-01T00:00:00Z"}, "EXPIRED_OR_TOO_FAR"),
                 ({"expires_at": "2099-01-01T00:00:00Z"}, "EXPIRED_OR_TOO_FAR"),
                 ({"handler": self.handler(path=self.path.parent)}, "POWER_STATE_UNAVAILABLE")]
        with patch("agent.power_commands.request_windows_power") as execute:
            for options, code in cases:
                with self.subTest(code=code):
                    result, = self.send(**options)
                    self.assertEqual((result["code"], result["ok"], result["terminal"]), (code, False, True))
            execute.assert_not_called()

    def test_reports_os_outcomes_without_executing_power_commands(self):
        cases = [(None, "POWER_EXECUTION_STARTED"), (OSError(), "POWER_EXECUTION_FAILED"),
                 (subprocess.TimeoutExpired("shutdown.exe", 4), "POWER_EXECUTION_UNKNOWN")]
        handler = self.handler("execute")
        for index, (error, code) in enumerate(cases):
            with self.subTest(code=code), patch("agent.power_commands.os.name", "nt"), \
                 patch("agent.power_commands.request_windows_power", side_effect=error) as execute:
                results = self.send(handler, power_control_mode="execute", command_id=f"execute-{index}")
                self.assertEqual([result["code"] for result in results], ["POWER_ACCEPTED", code])
                execute.assert_called_once_with("REBOOT_PC")

    def test_failed_ack_does_not_execute_and_id_remains_reserved(self):
        handler = self.handler("execute")
        request = self.request | {"power_control_mode": "execute"}
        with patch("agent.power_commands.os.name", "nt"), \
             patch("agent.power_commands.request_windows_power") as execute:
            with self.assertRaises(ConnectionError):
                handler.handle(request, Mock(side_effect=ConnectionError()), server_supported=True)
            self.assertEqual(self.send(self.handler("execute"), power_control_mode="execute")[0]["code"],
                             "DUPLICATE_COMMAND")
            execute.assert_not_called()

    @unittest.skipUnless(hasattr(subprocess, "CREATE_NO_WINDOW"), "Windows subprocess flags")
    def test_windows_arguments_are_fixed_and_never_use_a_shell(self):
        with patch("agent.power_commands.windows_system_directory", return_value=self.path.parent) as directory, \
             patch("agent.power_commands.subprocess.run") as run:
            request_windows_power("SHUTDOWN_PC")
            request_windows_power("REBOOT_PC")
            self.assertEqual(directory.call_count, 2)
            for call, option in zip(run.call_args_list, ("/s", "/r"), strict=True):
                self.assertEqual(call.args[0], [str(self.path.parent / "shutdown.exe"), option, "/t", "0"])
                self.assertFalse(call.kwargs.get("shell", False))

    def test_registration_advertises_boot_and_renegotiates_after_reconnect(self):
        args = Namespace(pc_id="PC-02", pc_name="test", host=socket.gethostname(), port=5000,
                         power_commands=self.handler())
        accepted = {"type": "REGISTER_RESULT", "ok": True, "pc_id": args.pc_id}
        with patch("agent.main.send_message") as send, patch("agent.main.recv_message", side_effect=[
                accepted | {"power_control_v1": True}, accepted]):
            register(Mock(), args)
            self.assertTrue(args.session_power_enabled)
            self.assertEqual(send.call_args.args[1]["boot_id"], self.boot_id)
            self.assertEqual(send.call_args.args[1]["power_control_mode"], "simulate")
            register(Mock(), args)
            self.assertFalse(args.session_power_enabled)
