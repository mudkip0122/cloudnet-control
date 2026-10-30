"""Run from repository root: python tests/smoke_local.py."""
import json
import subprocess
import sys


def stop(process):
    process.terminate()
    process.wait(timeout=5)


server = subprocess.Popen(
    [sys.executable, "-u", "-m", "server.main", "--port", "15001"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
try:
    print(server.stdout.readline().strip())
    result = subprocess.run(
        [sys.executable, "-m", "agent.main", "--port", "15001", "--pc-id", "integration-pc"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    print(result.stdout.strip())
finally:
    stop(server)

collector = subprocess.Popen(
    [sys.executable, "-u", "-m", "agent.collect", "--pc-id", "integration-pc"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
try:
    output, _ = collector.communicate(timeout=5.5)
except subprocess.TimeoutExpired:
    collector.terminate()
    output, errors = collector.communicate(timeout=5)
    samples = [json.loads(line) for line in output.splitlines()]
    assert len(samples) >= 2, errors
    assert samples[0]["upload_mbps"] is None
    assert isinstance(samples[1]["upload_mbps"], (int, float))
    print("Collector: first null and second numeric sample OK")
else:
    raise AssertionError("Collector exited unexpectedly: " + output)
