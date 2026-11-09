from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import sys
import types

from launcher.config import DEFAULTS, load_config, save_config
from launcher.lifecycle import AwsInstance, Cancelled, Session, SshService, client_command


def instance(state, host="203.0.113.10"):
    return {"State": {"Name": state}, "PublicIpAddress": host}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.record = Path(folder.name) / "session.json"
        self.config = DEFAULTS | {"instance_id": "i-0123456789abcdef0"}
        self.aws = Mock()
        self.service = Mock()
        self.session = Session(self.config, self.aws, self.service, wait=lambda delay: None, record_file=self.record)
        self.session.verify_server = Mock()

    @patch("launcher.lifecycle.socket.create_connection", return_value=nullcontext())
    def test_stopped_instance_starts_then_stops_after_service(self, connection):
        self.aws.describe.side_effect = [instance("stopped"), instance("pending"), instance("running")]
        self.assertEqual(self.session.start(), "203.0.113.10")
        self.aws.start.assert_called_once_with()
        self.service.run.assert_called_once_with("203.0.113.10", "start")
        self.assertTrue(json.loads(self.record.read_text())["needs_stop"])
        events = Mock()
        events.attach_mock(self.aws.stop, "stop_instance")
        events.attach_mock(self.service.run, "service")
        self.aws.describe.side_effect = [instance("running"), instance("stopped")]
        with patch("launcher.lifecycle.time.sleep"):
            self.assertEqual(self.session.stop(), [])
        self.assertEqual(events.mock_calls[0].args, ("203.0.113.10", "stop"))
        self.assertEqual(events.mock_calls[1][0], "stop_instance")
        self.assertFalse(json.loads(self.record.read_text())["needs_stop"])

    @patch("launcher.lifecycle.socket.create_connection", return_value=nullcontext())
    def test_preexisting_running_instance_adopted_and_stopped(self, connection):
        self.aws.describe.return_value = instance("running")
        self.session.start()
        self.aws.start.assert_not_called()
        self.aws.describe.side_effect = [instance("running"), instance("stopped")]
        with patch("launcher.lifecycle.time.sleep"):
            self.assertEqual(self.session.stop(), [])
        self.aws.stop.assert_called_once_with()

    def test_cancel_before_start_does_not_mutate_ec2(self):
        self.session.cancel.set()
        with self.assertRaises(Cancelled):
            self.session.start()
        self.assertEqual(self.session.stop(), [])
        self.aws.start.assert_not_called()
        self.aws.stop.assert_not_called()

    def test_cancel_while_starting_still_cleans_up(self):
        self.aws.describe.return_value = instance("stopped")
        self.aws.start.side_effect = lambda: self.session.cancel.set()
        with self.assertRaises(Cancelled):
            self.session.start()
        self.aws.describe.side_effect = [instance("pending"), instance("running"), instance("stopped")]
        with patch("launcher.lifecycle.time.sleep"):
            self.assertEqual(self.session.stop(), [])
        self.aws.stop.assert_called_once_with()

    def test_server_shutdown_failure_still_stops_ec2(self):
        self.session.host = "203.0.113.10"
        self.session.must_stop = self.session.service_attempted = True
        self.service.run.side_effect = RuntimeError("SSH disconnected")
        self.aws.describe.side_effect = [instance("running"), instance("stopped")]
        with patch("launcher.lifecycle.time.sleep"):
            errors = self.session.stop()
        self.assertIn("SSH disconnected", errors)
        self.aws.stop.assert_called_once_with()
        self.assertFalse(self.session.must_stop)

    def test_stop_failure_retains_recovery_record(self):
        self.session.must_stop = True
        self.session.record()
        self.aws.describe.return_value = instance("running")
        self.aws.stop.side_effect = RuntimeError("AccessDenied")
        self.assertTrue(self.session.stop())
        self.assertTrue(json.loads(self.record.read_text())["needs_stop"])

    def test_terminated_instance_is_not_started(self):
        self.aws.describe.return_value = instance("terminated")
        with self.assertRaises(RuntimeError):
            self.session.start()
        self.aws.start.assert_not_called()

    @patch("launcher.lifecycle.socket.create_connection", return_value=nullcontext())
    def test_setup_failure_does_not_stop_unmanaged_running_instance(self, connection):
        self.aws.describe.return_value = instance("running")
        self.service.run.side_effect = RuntimeError("Host key mismatch")
        with self.assertRaises(RuntimeError):
            self.session.start()
        self.session.stop()
        self.aws.stop.assert_not_called()


class LauncherConfigurationTests(unittest.TestCase):
    def test_roundtrip_ignores_secrets_and_unknown_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            save_config(DEFAULTS | {"aws_secret_access_key": "DO-NOT-SAVE"}, path)
            self.assertNotIn("DO-NOT-SAVE", path.read_text())
            self.assertEqual(load_config(path), DEFAULTS)

    def test_start_and_stop_api_only_target_configured_instance(self):
        aws = AwsInstance.__new__(AwsInstance)
        aws.client = Mock()
        aws.instance_id = "i-0123456789abcdef0"
        aws.start()
        aws.stop()
        aws.client.start_instances.assert_called_once_with(InstanceIds=[aws.instance_id])
        aws.client.stop_instances.assert_called_once_with(InstanceIds=[aws.instance_id])
        aws.client.terminate_instances.assert_not_called()

    def test_permission_check_uses_only_dry_run_and_rejects_denied_stop(self):
        class ClientError(Exception):
            def __init__(self, code):
                self.response = {"Error": {"Code": code}}
        fake = types.ModuleType("botocore.exceptions")
        fake.ClientError = ClientError
        aws = AwsInstance.__new__(AwsInstance)
        aws.instance_id = "i-0123456789abcdef0"
        aws.client = Mock()
        aws.client.start_instances.side_effect = ClientError("DryRunOperation")
        aws.client.stop_instances.side_effect = ClientError("DryRunOperation")
        with patch.dict(sys.modules, {"botocore.exceptions": fake}):
            aws.verify_permissions()
            aws.client.start_instances.assert_called_with(InstanceIds=[aws.instance_id], DryRun=True)
            aws.client.stop_instances.assert_called_with(InstanceIds=[aws.instance_id], DryRun=True)
            aws.client.stop_instances.side_effect = ClientError("UnauthorizedOperation")
            with self.assertRaises(ClientError):
                aws.verify_permissions()

    def test_child_uses_current_host_and_verifies_original_cert_identity(self):
        config = DEFAULTS | {"certificate_name": "32.236.13.91"}
        command = client_command(config, "203.0.113.10", "admin_gui.main", config["admin_token_file"])
        self.assertEqual(command[command.index("--host") + 1], "203.0.113.10")
        self.assertEqual(command[command.index("--server-name") + 1], "32.236.13.91")
        self.assertNotIn("--insecure", command)

    @patch("launcher.lifecycle.shutil.which", return_value="ssh.exe")
    @patch("launcher.lifecycle.subprocess.run")
    def test_ssh_checks_saved_host_key_and_keeps_paths_as_arguments(self, run, which):
        run.return_value = Mock(returncode=0, stdout="CLOUDNET_SERVER_STARTED", stderr="")
        service = SshService(DEFAULTS)
        service.run("203.0.113.10", "start")
        args = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("HostKeyAlias=32.236.13.91", args)
        self.assertIn("ubuntu@203.0.113.10", args)
        self.assertFalse(run.call_args.kwargs.get("shell", False))


if __name__ == "__main__":
    unittest.main()
