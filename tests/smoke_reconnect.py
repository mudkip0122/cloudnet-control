"""Verify a running Agent reconnects after a real server restart."""
import socket
import subprocess
import sys
import time

from common.protocol import recv_message, send_message


def start_server(port):
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "server.main", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(process.stderr.read())
        try:
            with socket.create_connection(("localhost", port), timeout=0.3):
                return process
        except OSError:
            time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    raise AssertionError("Server did not start")


def wait_for_metrics(port):
    with socket.create_connection(("localhost", port), timeout=2) as admin:
        send_message(admin, {"type": "REGISTER", "role": "admin"})
        assert recv_message(admin).get("ok") is True
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            send_message(admin, {"type": "LIST_DEVICES"})
            devices = recv_message(admin)["devices"]
            if devices and devices[0]["online"] and devices[0]["metrics"]:
                device = devices[0]
                assert device["pc_id"] == "reconnect-pc"
                assert isinstance(device["metrics"]["cpu_percent"], (int, float))
                return device["metrics"]["timestamp"]
            time.sleep(0.2)
    raise AssertionError("Agent did not register and send real metrics")


def main():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = agent = None
    try:
        server = start_server(port)
        agent = subprocess.Popen(
            [sys.executable, "-u", "-m", "agent.main", "--host", "localhost", "--port", str(port),
             "--pc-id", "reconnect-pc", "--live"],
        )
        first = wait_for_metrics(port)
        print("Before restart:", first, flush=True)
        rejected = subprocess.run(
            [sys.executable, "-m", "agent.main", "--host", "localhost", "--port", str(port),
             "--pc-id", "reconnect-pc", "--live", "--log-file", "runtime/logs/rejected-test.log"],
            capture_output=True, text=True, timeout=8,
        )
        assert rejected.returncode == 2, rejected.stderr
        print("Duplicate registration rejected without retry loop", flush=True)
        for round_number in range(1, 4):
            server.terminate()
            server.wait(timeout=5)
            time.sleep(1)
            server = start_server(port)
            second = wait_for_metrics(port)
            assert second != first
            assert agent.poll() is None
            print(f"Restart {round_number}/3 recovered:", second, flush=True)
            first = second
        print("PASS: 3 restarts, same Agent process and PC ID; real metrics resumed", flush=True)
    finally:
        for process in (agent, server):
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
