"""Real TLS server/admin integration using disposable test credentials."""
from pathlib import Path
import queue
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

from admin_gui.main import Dashboard
from common.protocol import recv_message, send_message


def main():
    openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        cert, key = directory / "test.crt", directory / "test.key"
        config = directory / "openssl.cnf"
        config.write_text(
            "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=v3\n"
            "[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n"
            "extendedKeyUsage=serverAuth\n", encoding="ascii")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-config", str(config), "-keyout", str(key), "-out", str(cert)],
                       check=True, capture_output=True, timeout=15)
        admin_token, agent_token = directory / "admin.txt", directory / "agent.txt"
        admin_token.write_text("test-admin-only", encoding="utf-8")
        agent_token.write_text("test-agent-only", encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = subprocess.Popen([sys.executable, "-u", "-m", "server.main", "--port", str(port),
                                   "--cert-file", str(cert), "--key-file", str(key),
                                   "--admin-token-file", str(admin_token), "--agent-token-file", str(agent_token)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert "TLS=True" in server.stdout.readline()

            def dashboard(token, context):
                result = Dashboard.__new__(Dashboard)
                result.host, result.port = "localhost", port
                result.context, result.token = context, token
                result.stop = threading.Event()
                result.updates = queue.Queue(maxsize=1)
                result.sock = None
                return result

            context = ssl.create_default_context(cafile=cert)
            for token, tls_context, expected in (
                    ("wrong", context, "인증 실패"),
                    ("test-admin-only", ssl.create_default_context(), "인증서 오류")):
                rejected = dashboard(token, tls_context)
                rejected.receive()
                connected, text = rejected.updates.get(timeout=2)
                assert not connected and expected in text, text

            active = dashboard("test-admin-only", context)
            worker = threading.Thread(target=active.receive)
            worker.start()
            try:
                assert active.updates.get(timeout=5) == (True, [])
                from common.security import connect
                with connect("localhost", port, context) as agent:
                    send_message(agent, {"type": "REGISTER", "role": "agent", "token": "test-agent-only",
                                         "pc_id": "tls-pc", "pc_name": "TLS PC"})
                    assert recv_message(agent)["ok"]
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        connected, devices = active.updates.get(timeout=5)
                        if connected and devices and devices[0]["online"]:
                            break
                    else:
                        raise AssertionError("Online state did not propagate")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    connected, devices = active.updates.get(timeout=5)
                    if connected and not devices[0]["online"]:
                        break
                else:
                    raise AssertionError("Offline state did not propagate")
            finally:
                active.stop.set()
                if active.sock:
                    try:
                        active.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                worker.join(6)
                assert not worker.is_alive()
            print("PASS: actual TLS verification, auth rejection, empty list, online/offline and worker cleanup")
        finally:
            server.terminate()
            server.wait(timeout=5)


if __name__ == "__main__":
    main()
