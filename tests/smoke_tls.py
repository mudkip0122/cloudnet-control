"""Exercise the real Agent against a temporary TLS/token server.

Run: python -m tests.smoke_tls (requires OpenSSL on PATH or Git for Windows).
Only test-generated keys and tokens are used; no deployment secrets are read.
"""
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading

from common.protocol import recv_message, send_message


def main():
    openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
    if not Path(openssl).is_file():
        raise SystemExit("OpenSSL is required for this TLS integration test")
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        ca, key = directory / "test.crt", directory / "test.key"
        config = directory / "openssl.cnf"
        config.write_text(
            "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=v3\n"
            "[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n"
            "extendedKeyUsage=serverAuth\n", encoding="ascii",
        )
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-config", str(config), "-keyout", str(key), "-out", str(ca)],
                       check=True, capture_output=True, timeout=15)
        token = directory / "token.txt"
        token.write_text("test-token-for-local-integration-only", encoding="utf-8")
        wrong = directory / "wrong-token.txt"
        wrong.write_text("wrong-test-token", encoding="utf-8")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(ca, key)
        complete = threading.Event()
        errors, received = [], []
        agent = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(4)
            listener.settimeout(20)
            port = listener.getsockname()[1]

            def serve():
                try:
                    # One rejected token, then two authenticated sessions.
                    for index in range(3):
                        raw, _ = listener.accept()
                        with raw:
                            raw.settimeout(8)
                            with context.wrap_socket(raw, server_side=True) as conn:
                                request = recv_message(conn)
                                if index == 0:
                                    assert request.get("token") == "wrong-test-token"
                                    send_message(conn, {"type": "ERROR", "code": "UNAUTHORIZED"})
                                    continue
                                assert request["pc_id"] == "tls-test-pc"
                                assert request.get("token") == token.read_text(encoding="utf-8")
                                send_message(conn, {"type": "REGISTER_RESULT", "ok": True, "pc_id": request["pc_id"]})
                                message = recv_message(conn)
                                assert message["type"] == "METRICS"
                                assert message["pc_id"] == "tls-test-pc"
                                assert isinstance(message["cpu_percent"], (int, float))
                                received.append(message)
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    complete.set()

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            base = [sys.executable, "-u", "-m", "agent.main", "--host", "127.0.0.1", "--port", str(port),
                    "--ca-file", str(ca), "--pc-id", "tls-test-pc", "--live"]
            try:
                rejected = subprocess.run(base + ["--token-file", str(wrong), "--log-file", str(directory / "reject.log")],
                                          capture_output=True, text=True, timeout=12)
                assert rejected.returncode == 2, rejected.stderr
                assert "wrong-test-token" not in rejected.stderr
                agent = subprocess.Popen(base + ["--token-file", str(token), "--log-file", str(directory / "live.log")],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                assert complete.wait(25), "TLS Agent did not reconnect and send metrics"
                if errors:
                    raise errors[0]
                assert len(received) == 2
                assert received[0]["timestamp"] != received[1]["timestamp"]
                assert token.read_text(encoding="utf-8") not in (directory / "live.log").read_text(encoding="utf-8")
                print("PASS: token rejected without retry; verified TLS registration, real metrics, reconnect and re-authentication")
            finally:
                if agent is not None:
                    agent.terminate()
                    agent.wait(timeout=5)
                worker.join(timeout=1)


if __name__ == "__main__":
    main()
