"""Exercise the GUI's staged power responses without controlling a machine."""
from datetime import datetime, timezone
import queue
from unittest import TestCase
from unittest.mock import MagicMock, patch

from admin_gui.power_control import PowerOperation, power_target
from admin_gui.process_control import ProcessClient


class PowerTests(TestCase):
    def test_staged_transport_and_reboot_identity(self):
        device = dict(pc_id="PC-02", agent_session_id="old", online=True,
                      power_control_mode="execute", boot_id="2026-11-18T01:00:00Z")
        operation = PowerOperation.start("PC-02", "REBOOT_PC", power_target(device))
        request = operation.request
        result = dict(type="COMMAND_RESULT", pc_id="PC-02", command_id=request["command_id"], ok=True)
        accepted = dict(result, code="POWER_ACCEPTED", terminal=False)
        started = dict(result, code="POWER_EXECUTION_STARTED", terminal=True)
        register = dict(type="REGISTER_RESULT", ok=True, power_control_v1=True)
        with patch.object(ProcessClient, "run"):
            client = ProcessClient("localhost", 0, object(), "synthetic")
        try:
            with patch("admin_gui.process_control.connect", return_value=MagicMock()), \
                    patch("admin_gui.process_control.send_message") as send, \
                    patch("admin_gui.process_control.recv_message", side_effect=[register, accepted, started]):
                final = client.execute(request)
            self.assertEqual(send.call_args.args[1], request)
            self.assertEqual(client.results.get_nowait(), (request, accepted))
            with self.assertRaises(queue.Empty):
                client.results.get_nowait()
        finally:
            client.close()
        operation.result(accepted)
        self.assertEqual(operation.code, "POWER_ACCEPTED")
        operation.result(final)
        self.assertEqual(operation.code, "POWER_EXECUTION_STARTED")
        now = datetime.now(timezone.utc)
        operation.observe(dict(device, online=False), now)
        operation.observe(dict(device, agent_session_id="new", boot_id="2026-11-18T00:00:00Z"), now)
        operation.observe(dict(device, agent_session_id="new", boot_id="2026-11-18T01:00:00+00:00"), now)
        operation.observe(dict(device, pc_id="other", agent_session_id="new", boot_id="2026-11-18T02:00:00Z"), now)
        self.assertEqual(operation.code, "POWER_EXECUTION_STARTED")
        operation.observe(dict(device, agent_session_id="new", boot_id="2026-11-18T02:00:00Z"), now)
        self.assertEqual(operation.code, "REBOOT_COMPLETED")

    def test_unsupported_server_receives_no_power_command(self):
        request = PowerOperation.start("PC-02", "SHUTDOWN_PC", ("simulate", None, None)).request
        with patch.object(ProcessClient, "run"):
            client = ProcessClient("localhost", 0, object(), "synthetic")
        try:
            with patch("admin_gui.process_control.connect", return_value=MagicMock()), \
                    patch("admin_gui.process_control.send_message") as send, \
                    patch("admin_gui.process_control.recv_message",
                          return_value=dict(type="REGISTER_RESULT", ok=True)):
                result = client.execute(request)
            self.assertEqual(result["code"], "POWER_UNSUPPORTED")
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1]["type"], "REGISTER")
        finally:
            client.close()

    def test_uncertain_delivery_is_reported_without_retry(self):
        request = PowerOperation.start("PC-02", "REBOOT_PC", ("simulate", None, None)).request
        with patch.object(ProcessClient, "execute", side_effect=TimeoutError) as execute:
            client = ProcessClient("localhost", 0, object(), "synthetic")
            try:
                client.submit(request)
                sent, result = client.results.get(timeout=2)
                self.assertEqual(sent["command_id"], request["command_id"])
                self.assertEqual(result["code"], "COMMAND_TIMEOUT")
                with self.assertRaises(queue.Empty):
                    client.results.get(timeout=.15)
                execute.assert_called_once_with(request)
            finally:
                client.close()
                client.worker.join(timeout=1)
