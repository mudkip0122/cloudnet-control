from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psutil

from agent.processes import ProcessManager
from agent.process_commands import ProcessCommands
from common.protocol import MAX_MESSAGE_BYTES


class ProcessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.manager = ProcessManager()
        self.me = Mock(pid=100)
        self.me.parents.return_value = [Mock(pid=99)]
        self.me.username.return_value = "PC\\user"
        self.target = Mock(pid=123)
        self.target.name.return_value = "test.exe"
        self.target.username.return_value = "PC\\user"
        self.target.exe.return_value = "C:\\Apps\\test.exe"
        self.target.create_time.return_value = 1234.5
        self.patches = [patch("agent.processes.psutil.Process", side_effect=lambda *a: self.target if a else self.me),
                        patch("agent.processes.windows_critical", return_value=False),
                        patch("agent.processes.under_windows", return_value=False)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_self_ancestors_and_system_pids_are_denied(self):
        for pid in (0, 4, 99, 100):
            self.target.pid = pid
            result = self.manager.stop(pid, 1234.5)
            self.assertFalse(result["ok"])
            self.target.terminate.assert_not_called()

    def test_core_name_and_custom_names_are_protected(self):
        for name in ("LSASS.EXE", "svchost.exe", "explorer.exe"):
            self.target.name.return_value = name
            self.assertEqual(self.manager.stop(123, 1234.5)["reason"], "PROTECTED_NAME")
        self.target.name.return_value = "MyApp.EXE"
        self.assertEqual(ProcessManager(["myapp.exe"]).stop(123, 1234.5)["code"], "PROTECTED")
        self.target.terminate.assert_not_called()

    def test_other_user_unknown_metadata_and_system_path_are_denied(self):
        self.target.username.return_value = "NT AUTHORITY\\SYSTEM"
        self.assertEqual(self.manager.stop(123, 1234.5)["reason"], "OTHER_USER")
        self.target.username.return_value = "PC\\user"
        self.target.exe.return_value = ""
        self.assertEqual(self.manager.stop(123, 1234.5)["code"], "PROTECTED")
        self.target.exe.return_value = "C:\\Apps\\test.exe"
        with patch("agent.processes.under_windows", return_value=True):
            self.assertEqual(self.manager.stop(123, 1234.5)["code"], "PROTECTED")
        self.target.terminate.assert_not_called()

    def test_critical_and_unreadable_critical_status_are_denied(self):
        for value in (True, None):
            with patch("agent.processes.windows_critical", return_value=value):
                self.assertEqual(self.manager.stop(123, 1234.5)["reason"], "CRITICAL_OR_UNKNOWN")
        self.target.name.side_effect = psutil.AccessDenied(123)
        self.assertEqual(self.manager.stop(123, 1234.5)["code"], "PROTECTED")
        self.target.terminate.assert_not_called()

    def test_stale_identity_and_bad_input_never_terminate(self):
        self.assertEqual(self.manager.stop(123, 999)["code"], "PID_REUSED")
        for pid, created in ((True, 1234.5), (123, True), (123, float("nan")), (123, None)):
            self.assertEqual(self.manager.stop(pid, created)["code"], "INVALID_IDENTITY")
        self.target.terminate.assert_not_called()

    def test_terminate_status_and_permission_failure(self):
        self.assertEqual(self.manager.stop(123, 1234.5)["code"], "TERMINATED")
        self.target.terminate.assert_called_once_with()
        self.target.wait.side_effect = psutil.TimeoutExpired(2)
        self.assertEqual(self.manager.stop(123, 1234.5)["code"], "TERMINATION_REQUESTED")
        self.target.terminate.side_effect = psutil.AccessDenied(123)
        self.assertEqual(self.manager.stop(123, 1234.5)["code"], "ACCESS_DENIED")

    def test_exited_process_is_reported(self):
        self.target.create_time.side_effect = psutil.NoSuchProcess(123)
        self.assertEqual(self.manager.stop(123, 1234.5)["code"], "NOT_FOUND")

    def test_inventory_keeps_inaccessible_rows_and_marks_protection(self):
        self.target.memory_info.return_value = SimpleNamespace(rss=4096)
        self.target.status.return_value = "running"
        hidden = Mock(pid=5)
        for field in ("name", "username", "create_time", "memory_info", "status"):
            getattr(hidden, field).side_effect = psutil.AccessDenied(5)
        with patch("agent.processes.psutil.process_iter", return_value=[self.target, hidden]):
            rows = self.manager.inventory()
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0]["can_stop"])
        self.assertIsNone(rows[0]["name"])
        self.assertTrue(rows[1]["can_stop"])
        self.assertEqual(rows[1]["memory_bytes"], 4096)


class CommandTests(unittest.TestCase):
    def request(self, **changes):
        return {"type": "COMMAND", "command_id": "test-1", "target_id": "PC-02",
                "action": "STOP_PROCESS", "pid": 123, "create_time": 1234.5,
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()} | changes

    def test_disabled_target_expiry_and_actions_never_reach_stop(self):
        commands = ProcessCommands("PC-02")
        commands.manager = Mock()
        self.assertEqual(commands.handle(self.request())[0]["code"], "PROCESS_CONTROL_DISABLED")
        commands.enabled = True
        for changes, code in (({"target_id": "other"}, "WRONG_TARGET"),
                              ({"expires_at": "bad"}, "INVALID_EXPIRY"),
                              ({"expires_at": "2020-01-01T00:00:00Z"}, "EXPIRED_OR_TOO_FAR"),
                              ({"action": "SHELL"}, "UNSUPPORTED_ACTION")):
            self.assertEqual(commands.handle(self.request(**changes))[0]["code"], code)
        commands.manager.stop.assert_not_called()

    def test_duplicate_stop_runs_only_once(self):
        commands = ProcessCommands("PC-02", enabled=True)
        commands.manager = Mock()
        commands.manager.stop.return_value = {"ok": True, "code": "TERMINATED"}
        request = self.request()
        self.assertTrue(commands.handle(request)[0]["ok"])
        self.assertEqual(commands.handle(request)[0]["code"], "DUPLICATE_COMMAND")
        commands.manager.stop.assert_called_once_with(123, 1234.5)

    def test_inventory_is_split_into_bounded_frames(self):
        commands = ProcessCommands("PC-02", enabled=True)
        commands.manager = Mock()
        commands.manager.inventory.return_value = [
            {"pid": i, "name": "한" * 128, "username": "한" * 128} for i in range(101)]
        results = commands.handle(self.request(action="LIST_PROCESSES"))
        self.assertEqual([len(page["processes"]) for page in results], [40, 40, 21])
        self.assertTrue(all(len(json.dumps(page, ensure_ascii=False).encode("utf-8")) <= MAX_MESSAGE_BYTES
                            for page in results))


if __name__ == "__main__":
    unittest.main()
