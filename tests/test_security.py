import json
import os
import queue
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime, timezone

from common.protocol import recv_message, send_message
from common.security import connect


class SecureConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        openssl = shutil.which("openssl")
        windows_openssl = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        if not openssl and windows_openssl.exists():
            openssl = str(windows_openssl)
        if not openssl:
            raise unittest.SkipTest("OpenSSL required for TLS integration tests")
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.config = Path(cls.temp.name) / "ec2"
        env = os.environ.copy()
        env["PATH"] = str(Path(openssl).parent) + os.pathsep + env.get("PATH", "")
        subprocess.run([sys.executable, "-m", "server.provision", "--public-ip", "127.0.0.1",
                        "--output", str(cls.config)], check=True, capture_output=True, env=env)
        cls.auth = json.loads((cls.config / "auth.json").read_text())

    def setUp(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.process = subprocess.Popen([
            sys.executable, "-u", "-m", "server.main", "--port", str(self.port),
            "--tls-cert", str(self.config / "server.crt"),
            "--tls-key", str(self.config / "server.key"),
            "--auth-file", str(self.config / "auth.json")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self.stop_server)
        self.assertIn("TLS/auth: True", self.process.stdout.readline())
        self.context = ssl.create_default_context(cafile=str(self.config / "server.crt"))

    def stop_server(self):
        self.process.terminate()
        self.process.wait(timeout=5)
        self.process.stdout.close()

    def register(self, role="agent", pc_id="PC-01", token=None):
        conn = connect("127.0.0.1", self.port, self.context)
        self.addCleanup(conn.close)
        message = {"type": "REGISTER", "role": role, "pc_id": pc_id, "pc_name": pc_id}
        if token is not None:
            message["token"] = token
        send_message(conn, message)
        return conn, recv_message(conn)

    def test_three_agents_and_admin_over_verified_tls(self):
        for pc_id, token in self.auth["agents"].items():
            agent, result = self.register(pc_id=pc_id, token=token)
            self.assertTrue(result["ok"])
            send_message(agent, {"type": "METRICS", "pc_id": pc_id,
                         "timestamp": datetime.now(timezone.utc).isoformat(),
                         "cpu_percent": 21.5, "ram_percent": 42.0,
                         "upload_mbps": 0.1, "download_mbps": 0.2})
        admin, result = self.register("admin", token=self.auth["admin_token"])
        self.assertTrue(result["ok"])
        for _ in range(20):
            send_message(admin, {"type": "LIST_DEVICES"})
            devices = recv_message(admin)["devices"]
            if len(devices) == 3 and all(d["metrics"] for d in devices):
                break
            time.sleep(0.05)
        self.assertEqual({d["pc_id"] for d in devices}, {"PC-01", "PC-02", "PC-03"})
        self.assertTrue(all(d["online"] for d in devices))
        self.assertTrue(all(d["metrics"]["cpu_percent"] == 21.5 for d in devices))

    def test_client_packages_only_contain_assigned_credentials(self):
        for name, expected in [*self.auth["agents"].items(), ("admin", self.auth["admin_token"])]:
            with zipfile.ZipFile(self.config / f"{name}.zip") as bundle:
                self.assertEqual(bundle.read("secrets/token.txt").decode().strip(), expected)
                self.assertNotIn("server.key", bundle.namelist())
                self.assertNotIn("auth.json", bundle.namelist())
                contents = b"".join(bundle.read(n) for n in bundle.namelist())
                for other in [*self.auth["agents"].values(), self.auth["admin_token"]]:
                    if other != expected:
                        self.assertNotIn(other.encode(), contents)

    def test_real_agent_streams_metrics_over_tls(self):
        token_file = self.config / "test-agent-token.txt"
        token_file.write_text(self.auth["agents"]["PC-01"], encoding="utf-8")
        agent = subprocess.Popen([
            sys.executable, "-m", "agent.main", "--host", "127.0.0.1", "--port", str(self.port),
            "--pc-id", "PC-01", "--live", "--ca-file", str(self.config / "server.crt"),
            "--token-file", str(token_file), "--log-file", str(self.config / "agent.log")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            admin, response = self.register("admin", token=self.auth["admin_token"])
            self.assertTrue(response["ok"])
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                self.assertIsNone(agent.poll(), "Agent exited before sending metrics")
                send_message(admin, {"type": "LIST_DEVICES"})
                devices = recv_message(admin)["devices"]
                if devices and devices[0].get("metrics"):
                    self.assertIsInstance(devices[0]["metrics"]["cpu_percent"], (int, float))
                    break
                time.sleep(0.1)
            else:
                self.fail("No real Agent metrics over TLS")
        finally:
            agent.terminate()
            agent.wait(timeout=5)

    def test_gui_worker_uses_tls_and_admin_auth_without_widgets(self):
        from admin_gui.main import Dashboard
        _, response = self.register(token=self.auth["agents"]["PC-01"])
        self.assertTrue(response["ok"])
        dashboard = Dashboard.__new__(Dashboard)
        dashboard.host, dashboard.port = "127.0.0.1", self.port
        dashboard.context, dashboard.token = self.context, self.auth["admin_token"]
        dashboard.stop, dashboard.updates = threading.Event(), queue.Queue(maxsize=1)
        dashboard.sock = None
        worker = threading.Thread(target=dashboard.receive, daemon=True)
        worker.start()
        try:
            connected, devices = dashboard.updates.get(timeout=5)
            self.assertTrue(connected, str(devices))
            self.assertEqual(devices[0]["pc_id"], "PC-01")
        finally:
            dashboard.stop.set()
            worker.join(timeout=6)
            self.assertFalse(worker.is_alive())

    def test_reject_missing_wrong_and_cross_role_tokens(self):
        for role, pc_id, token in [
            ("agent", "PC-01", None), ("agent", "PC-01", "wrong"),
            ("agent", "PC-01", self.auth["agents"]["PC-02"]),
            ("admin", "PC-01", self.auth["agents"]["PC-01"]),
            ("agent", "unknown", self.auth["agents"]["PC-01"]),
            ("agent", "PC-01", "한글"), ("admin", "PC-01", self.auth["admin_token"] + "x"),
        ]:
            with self.subTest(role=role, pc_id=pc_id):
                _, response = self.register(role, pc_id, token)
                self.assertEqual(response["type"], "ERROR")
                self.assertEqual(response["message"], "Authentication failed")

    def test_agent_cannot_list_or_spoof_metrics(self):
        for message in [
            {"type": "LIST_DEVICES"}, {"type": "METRICS", "pc_id": "PC-02"}
        ]:
            conn, response = self.register(token=self.auth["agents"]["PC-01"])
            self.assertTrue(response["ok"])
            send_message(conn, message)
            self.assertEqual(recv_message(conn)["type"], "ERROR")
            conn.close()
            # Ensure the session's finally block has released the ID.
            time.sleep(0.05)

    def test_reject_untrusted_certificate(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            connect("127.0.0.1", self.port, ssl.create_default_context())

    def test_reject_wrong_server_identity(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as raw:
            with self.assertRaises(ssl.SSLCertVerificationError):
                self.context.wrap_socket(raw, server_hostname="wrong.example")

    def test_separate_transport_address_still_verifies_certificate_name(self):
        with connect("localhost", self.port, self.context, "127.0.0.1") as conn:
            send_message(conn, {"type": "REGISTER", "role": "admin", "token": self.auth["admin_token"]})
            self.assertTrue(recv_message(conn)["ok"])
        with self.assertRaises(ssl.SSLCertVerificationError):
            connect("127.0.0.1", self.port, self.context, "wrong.example")

    def test_plaintext_does_not_register(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as conn:
            send_message(conn, {"type": "REGISTER", "role": "admin"})
            with self.assertRaises((OSError, EOFError, ValueError)):
                recv_message(conn)


class ExternalConfigurationTests(unittest.TestCase):
    def test_external_server_requires_tls(self):
        result = subprocess.run([sys.executable, "-m", "server.main", "--host", "0.0.0.0"],
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("require TLS", result.stderr)

    def test_external_agent_requires_tls(self):
        result = subprocess.run([sys.executable, "-m", "agent.main", "--host", "192.0.2.1", "--pc-id", "test"],
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--ca-file and --token-file together", result.stderr)


if __name__ == "__main__":
    unittest.main()
