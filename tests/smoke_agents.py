"""Three real Agent processes on one PC; not a substitute for three physical PCs."""
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from common.protocol import recv_message, send_message
from tests.processes import stop_process
from tests.smoke_reconnect import start_server


def snapshot(port):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as admin:
        send_message(admin, {"type": "REGISTER", "role": "admin"})
        assert recv_message(admin).get("ok") is True
        send_message(admin, {"type": "LIST_DEVICES"})
        return {d["pc_id"]: d for d in recv_message(admin)["devices"]}


def wait_for(port, condition, description):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        devices = snapshot(port)
        if condition(devices):
            return devices
        time.sleep(.2)
    raise AssertionError(description)


def main():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    processes = []
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        try:
            processes.append(start_server(port))
            for index in range(3):
                processes.append(subprocess.Popen(
                    [sys.executable, "-u", "-m", "agent.main", "--port", str(port),
                     "--pc-id", f"week2-test-{index}", "--pc-name", f"TEST-{index}",
                     "--live", "--log-file", str(directory / f"agent-{index}.log")],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            first = wait_for(port, lambda ds: len(ds) == 3 and all(
                d["online"] and d["metrics"] and
                isinstance(d["metrics"]["cpu_percent"], (int, float)) for d in ds.values()),
                "Three real Agents did not send metrics")
            rejected = subprocess.run(
                [sys.executable, "-m", "agent.main", "--port", str(port),
                 "--pc-id", "week2-test-0", "--live", "--log-file", str(directory / "duplicate.log")],
                capture_output=True, text=True, timeout=10)
            assert rejected.returncode == 2, rejected.stderr
            assert "Retrying" not in rejected.stderr
            stop_process(processes[2])
            wait_for(port, lambda ds: not ds["week2-test-1"]["online"] and all(
                ds[key]["online"] and ds[key]["metrics"]["timestamp"] != first[key]["metrics"]["timestamp"]
                for key in ("week2-test-0", "week2-test-2")),
                "One disconnect affected other Agents or their data stopped updating")
        finally:
            for process in reversed(processes):
                stop_process(process)
    print("PASS: 3 real Agent processes, distinct IDs and metrics; duplicate rejected; one offline, others keep updating")


if __name__ == "__main__":
    main()
