"""Real TLS inventory/stop test. Only terminates a disposable child created here."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

import psutil

from common.protocol import recv_message, send_message
from tests.processes import stop_process


def main():
    openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        ca, key = directory / "test.crt", directory / "test.key"
        config = directory / "openssl.cnf"
        config.write_text(
            "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=v3\n"
            "[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n"
            "extendedKeyUsage=serverAuth\n", encoding="ascii")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-config", str(config), "-keyout", str(key), "-out", str(ca)],
                       check=True, capture_output=True, timeout=15)
        token = directory / "token.txt"
        token.write_text("local-process-test-only", encoding="ascii")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(ca, key)
        agent = target = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(10)
            try:
                # Use the actual interpreter, avoiding Windows venv launcher children.
                target = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(120)"])
                created = psutil.Process(target.pid).create_time()
                agent = subprocess.Popen(
                    [sys.executable, "-m", "agent.main", "--host", "127.0.0.1",
                     "--port", str(listener.getsockname()[1]), "--ca-file", str(ca),
                     "--token-file", str(token), "--pc-id", "process-test", "--live",
                     "--enable-process-control", "--log-file", str(directory / "agent.log")],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                raw, _ = listener.accept()
                with raw, context.wrap_socket(raw, server_side=True) as conn:
                    conn.settimeout(8)
                    registration = recv_message(conn)
                    assert registration["token"] == "local-process-test-only"
                    send_message(conn, {"type": "REGISTER_RESULT", "ok": True,
                                        "pc_id": "process-test", "heartbeat_interval": 1})

                    def command(command_id, action, **fields):
                        send_message(conn, {"type": "COMMAND", "command_id": command_id,
                                            "target_id": "process-test", "action": action,
                                            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                                            **fields})
                        deadline = time.monotonic() + 10
                        results = []
                        while time.monotonic() < deadline:
                            message = recv_message(conn)
                            if message["type"] in ("METRICS", "HEARTBEAT"):
                                continue
                            assert message["type"] == "COMMAND_RESULT", message
                            assert message["command_id"] == command_id
                            results.append(message)
                            if len(results) == message.get("pages", 1):
                                return results
                        raise AssertionError("No command result")

                    pages = command("list", "LIST_PROCESSES")
                    rows = [row for page in pages for row in page["processes"]]
                    row = next(row for row in rows if row["pid"] == target.pid)
                    assert row["can_stop"], row
                    assert row["create_time"] == created
                    agent_row = next(row for row in rows if row["pid"] == agent.pid)
                    assert not agent_row["can_stop"]
                    assert command("protected", "STOP_PROCESS", pid=agent.pid,
                                   create_time=agent_row["create_time"])[0]["code"] == "PROTECTED"
                    assert agent.poll() is None
                    assert command("stale", "STOP_PROCESS", pid=target.pid, create_time=created - 1)[0]["code"] == "PID_REUSED"
                    assert target.poll() is None
                    assert command("stop", "STOP_PROCESS", pid=target.pid, create_time=created)[0]["code"] == "TERMINATED"
                    target.wait(timeout=5)
                    assert command("stop", "STOP_PROCESS", pid=target.pid, create_time=created)[0]["code"] == "DUPLICATE_COMMAND"
                    assert command("gone", "STOP_PROCESS", pid=target.pid, create_time=created)[0]["code"] == "NOT_FOUND"
                    assert agent.poll() is None
            finally:
                for process in (agent, target):
                    if process is not None:
                        stop_process(process)
    print("PASS: real TLS paginated inventory, protected Agent, stale PID rejected, disposable child terminated, replay blocked")


if __name__ == "__main__":
    main()
