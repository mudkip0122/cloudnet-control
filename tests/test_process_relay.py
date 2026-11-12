import socket
import ssl
import unittest
from unittest.mock import Mock

from admin_gui.process_control import command_request
from server.main import MonitoringServer
from tests.test_gui_processes import page


class ProcessRelayTests(unittest.TestCase):
    def setUp(self):
        self.state = MonitoringServer({"admin_token": "admin-token", "agents": {"pc": "agent-token"}})
        self.admin = Mock(spec=ssl.SSLSocket)
        self.agent = Mock(spec=ssl.SSLSocket)
        self.state.sessions["pc"] = self.agent
        self.state.send = Mock()
        self.state.notify_admin = self.state.send

    def test_plaintext_control_rejected(self):
        admin = Mock(spec=socket.socket)
        self.state.route_command(admin, command_request("pc", "LIST_PROCESSES"))
        self.assertEqual(self.state.send.call_args.args[1]["code"], "TLS_REQUIRED")
        self.assertFalse(self.state.pending_commands)

    def test_pages_go_only_to_requesting_admin_and_replay_blocked(self):
        request = command_request("pc", "LIST_PROCESSES")
        self.state.route_command(self.admin, request)
        self.state.send.assert_called_once_with(self.agent, request)
        for index in (1, 0):
            result = page(index, command_id=request["command_id"])
            self.state.route_result(self.agent, "pc", result)
            self.state.send.assert_called_with(self.admin, result)
        self.assertNotIn(request["command_id"], self.state.pending_commands)
        self.state.route_command(self.admin, request)
        self.assertEqual(self.state.send.call_args.args[1]["code"], "DUPLICATE_COMMAND")

    def test_wrong_agent_cannot_supply_result(self):
        request = command_request("pc", "LIST_PROCESSES")
        self.state.route_command(self.admin, request)
        with self.assertRaises(ValueError):
            self.state.route_result(Mock(spec=ssl.SSLSocket), "pc", page(0, command_id=request["command_id"]))

    def test_closed_admin_does_not_disconnect_agent(self):
        request = command_request("pc", "LIST_PROCESSES")
        self.state.route_command(self.admin, request)
        self.state.send.side_effect = OSError("closed")
        self.state.route_result(self.agent, "pc", page(0, pages=1, command_id=request["command_id"]))
        self.assertNotIn(request["command_id"], self.state.pending_commands)


if __name__ == "__main__":
    unittest.main()
