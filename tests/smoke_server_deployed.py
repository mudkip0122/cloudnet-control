"""Run on EC2: reuse existing credentials on a temporary loopback TLS server.

Does not change production sessions, generate credentials, or expose tokens.
Run from the deployment root: .venv/bin/python -m tests.smoke_server_deployed
"""
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time

from common.protocol import recv_message, send_message
from common.security import connect


def main():
    directory = Path("secrets/ec2")
    auth = json.loads((directory / "auth.json").read_text())
    identities = list(auth["agents"])[:3]
    assert len(identities) == 3, "Three configured Agent IDs are required"
    cert = directory / "server.crt"
    names = ssl._ssl._test_decode_cert(str(cert))["subjectAltName"]
    identity = next(value for kind, value in names if kind in ("IP Address", "DNS"))
    context = ssl.create_default_context(cafile=str(cert))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen([
        sys.executable, "-u", "-m", "server.main", "--port", str(port),
        "--tls-cert", str(cert), "--tls-key", str(directory / "server.key"),
        "--auth-file", str(directory / "auth.json"), "--heartbeat-timeout", "2"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    clients, threads = [], []
    stopped = threading.Event()

    def register(role, pc_id=None, token=None, subscribe=False):
        conn = connect("127.0.0.1", port, context, identity)
        clients.append(conn)
        send_message(conn, {"type": "REGISTER", "role": role, "pc_id": pc_id,
                            "pc_name": pc_id, "token": token, "subscribe_status": subscribe})
        return conn, recv_message(conn)

    def heartbeat(conn, pc_id):
        while not stopped.wait(0.25):
            try:
                send_message(conn, {"type": "HEARTBEAT", "pc_id": pc_id})
            except OSError:
                return

    try:
        assert "TLS/auth: True" in process.stdout.readline(), "Server failed to start"
        admin, result = register("admin", token=auth["admin_token"], subscribe=True)
        assert result.get("ok") is True
        for pc_id in identities:
            conn, result = register("agent", pc_id, auth["agents"][pc_id])
            assert result.get("ok") is True
            update = recv_message(admin)
            assert update["type"] == "STATUS_UPDATE" and update["device"]["pc_id"] == pc_id
            assert update["device"]["online"] is True
            if pc_id != identities[1]:
                thread = threading.Thread(target=heartbeat, args=(conn, pc_id), daemon=True)
                thread.start()
                threads.append(thread)
        print("PASS: existing TLS credentials, three simultaneous Agents and admin status subscription")
        _, duplicate = register("agent", identities[0], auth["agents"][identities[0]])
        assert duplicate["type"] == "ERROR"
        _, wrong = register("agent", identities[0], auth["agents"][identities[1]])
        assert wrong["type"] == "ERROR"
        print("PASS: duplicate ID and cross-device token rejected")
        # The middle Agent goes silent; peers send independent Heartbeats.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            send_message(admin, {"type": "LIST_DEVICES"})
            message = recv_message(admin)
            if message["type"] == "STATUS_UPDATE":
                assert message["device"]["pc_id"] == identities[1]
                assert message["device"]["online"] is False
                message = recv_message(admin)
            devices = {d["pc_id"]: d for d in message["devices"]}
            if not devices[identities[1]]["online"]:
                assert all(devices[pc_id]["online"] for pc_id in (identities[0], identities[2]))
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Silent Agent did not expire")
        _, result = register("agent", identities[1], auth["agents"][identities[1]])
        assert result.get("ok") is True
        print("PASS: silent Agent timeout, other Heartbeats stay online, same ID reconnects")
    finally:
        stopped.set()
        for thread in threads:
            thread.join(timeout=2)
        for conn in clients:
            conn.close()
        process.terminate()
        process.wait(timeout=5)
        process.stdout.close()


if __name__ == "__main__":
    main()
