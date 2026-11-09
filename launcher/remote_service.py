"""Executed via authenticated SSH stdin; no AWS credentials or tokens cross stdin."""
from pathlib import Path
import os
import pwd
import re
import signal
import subprocess
import sys
import time

UNIT = "cloudnet-control-launcher.service"


def command(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, timeout=45, **kwargs)


def checked(args, **kwargs):
    result = command(args, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Command failed: {' '.join(args[:3])}\n{result.stderr.strip()}")
    return result.stdout


def main():
    action, directory, port_text = sys.argv[1:]
    if action not in ("start", "stop") or not re.fullmatch(r"/[A-Za-z0-9_/-]+", directory):
        raise ValueError("Invalid action or directory")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    if action == "stop":
        if command(["systemctl", "show", UNIT, "-p", "LoadState", "--value"]).stdout.strip() != "not-found":
            checked(["sudo", "-n", "systemctl", "stop", UNIT])
        print("CLOUDNET_SERVER_STOPPED", flush=True)
        return
    directory = str(Path(directory).resolve(strict=True))
    for relative in (".venv/bin/python", "server/main.py", "secrets/ec2/server.crt",
                     "secrets/ec2/server.key", "secrets/ec2/auth.json"):
        if not (Path(directory) / relative).is_file():
            raise ValueError(f"Missing server file: {relative}")
    checked(["sudo", "-n", "true"])
    # Migrate only this user's known foreground CloudNet process, never arbitrary listeners.
    if command(["systemctl", "is-active", "--quiet", UNIT]).returncode:
        listeners = checked(["ss", "-ltnp", f"sport = :{port}"])
        pids = {int(p) for p in re.findall(r"pid=(\d+)", listeners)}
        if len(listeners.strip().splitlines()) > 1 and not pids:
            raise RuntimeError("Port is in use by an unidentified process; stop it manually")
        for pid in pids:
            proc = Path(f"/proc/{pid}")
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            is_server = any(argv[i:i+2] == [b"-m", b"server.main"] for i in range(len(argv)-1))
            if proc.stat().st_uid != os.getuid() or os.readlink(proc / "cwd") != directory or not is_server:
                raise RuntimeError("Port is owned by another program; refusing to stop it")
            os.kill(pid, signal.SIGTERM)
        if pids:
            for _ in range(50):
                if len(checked(["ss", "-ltn", f"sport = :{port}"]).strip().splitlines()) <= 1:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("Old server did not release the port")
    user = pwd.getpwuid(os.getuid()).pw_name
    unit = f"""[Unit]
Description=CloudNet Control launcher-managed TLS server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={directory}
ExecStart={directory}/.venv/bin/python -u -m server.main --host 0.0.0.0 --port {port} --tls-cert secrets/ec2/server.crt --tls-key secrets/ec2/server.key --auth-file secrets/ec2/auth.json
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
"""
    checked(["sudo", "-n", "tee", f"/etc/systemd/system/{UNIT}"], input=unit)
    checked(["sudo", "-n", "systemctl", "daemon-reload"])
    checked(["sudo", "-n", "systemctl", "start", UNIT])
    checked(["systemctl", "is-active", "--quiet", UNIT])
    print("CLOUDNET_SERVER_STARTED", flush=True)


if __name__ == "__main__":
    main()
