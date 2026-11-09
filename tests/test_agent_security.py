from argparse import Namespace
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent.main import RegistrationError, register, run
from common.security import client_settings, connect


class ClientSecurityTests(unittest.TestCase):
    def args(self, **changes):
        values = dict(host="localhost", port=5000, ca_file=None, token_file=None,
                      pc_id="test-pc", pc_name="Test", live=True, token=None)
        return Namespace(**(values | changes))

    def test_plaintext_local_and_remote_requirement(self):
        self.assertEqual(client_settings(self.args()), (None, None))
        with self.assertRaises(ValueError):
            client_settings(self.args(host="32.236.13.91"))

    def test_credentials_must_be_supplied_together_and_nonempty(self):
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / "token.txt"
            token.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                client_settings(self.args(ca_file="ca.pem", token_file=token))
            token.write_text("example-token", encoding="utf-8")
            with self.assertRaises(ValueError):
                client_settings(self.args(token_file=token))
            with self.assertRaises(ValueError):
                client_settings(self.args(ca_file="ca.pem"))

    def test_tls_context_keeps_certificate_and_hostname_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / "token.txt"
            token.write_text("example-token\n", encoding="utf-8")
            context = ssl.create_default_context()
            with patch("common.security.ssl.create_default_context", return_value=context) as create:
                result, value = client_settings(self.args(ca_file="ca.pem", token_file=token))
            create.assert_called_once_with(cafile="ca.pem")
            self.assertEqual(value, "example-token")
            self.assertTrue(result.check_hostname)
            self.assertEqual(result.verify_mode, ssl.CERT_REQUIRED)
            self.assertGreaterEqual(result.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_failed_handshake_closes_socket(self):
        conn, context = Mock(), Mock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError("untrusted")
        with patch("common.security.socket.create_connection", return_value=conn):
            with self.assertRaises(ssl.SSLCertVerificationError):
                connect("server.example", 5000, context)
        context.wrap_socket.assert_called_once_with(conn, server_hostname="server.example")
        conn.close.assert_called_once()

    def test_registration_sends_token_without_logging_it(self):
        args = self.args(token="do-not-log-this-token")
        with patch("agent.main.send_message") as send, \
             patch("agent.main.recv_message", return_value={"type": "REGISTER_RESULT", "ok": True, "pc_id": args.pc_id}), \
             self.assertLogs("cloudnet.agent", level="INFO") as logs:
            register(Mock(), args)
        self.assertEqual(send.call_args.args[1]["token"], args.token)
        self.assertNotIn(args.token, "\n".join(logs.output))

    def test_certificate_and_auth_errors_do_not_retry(self):
        for error in (ssl.SSLCertVerificationError("bad cert"), RegistrationError("denied")):
            with self.subTest(error=type(error).__name__), \
                 patch("agent.main.connect", side_effect=error) as connection, \
                 patch("agent.main.time.sleep") as sleep:
                self.assertEqual(run(self.args()), 2)
                connection.assert_called_once()
                sleep.assert_not_called()

    def test_tls_disconnect_reconnects_and_registers_again(self):
        context = Mock()
        conn = Mock()
        connection = Mock()
        connection.__enter__ = Mock(return_value=conn)
        connection.__exit__ = Mock(return_value=False)
        with patch("agent.main.connect", return_value=connection) as create, \
             patch("agent.main.register") as registration, \
             patch("agent.main.stream", side_effect=[ssl.SSLEOFError("closed"), RegistrationError("stop test")]), \
             patch("agent.main.time.sleep") as sleep:
            self.assertEqual(run(self.args(), context), 2)
        self.assertEqual(create.call_count, 2)
        self.assertEqual(registration.call_count, 2)
        sleep.assert_called_once_with(3)
        self.assertEqual(create.call_args.args[2], context)
