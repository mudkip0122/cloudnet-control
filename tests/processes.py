"""Clean up only subprocess trees created by integration tests."""
import os
import subprocess


def stop_process(process):
    if process.poll() is None:
        if os.name == "nt":
            # Windows venv python.exe can be a launcher with a child interpreter.
            # terminate() alone leaves that child and its sockets/log files alive.
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           check=True, capture_output=True, timeout=10)
        else:
            process.terminate()
    process.wait(timeout=5)
