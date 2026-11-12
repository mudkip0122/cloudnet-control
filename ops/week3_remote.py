"""A-only EC2 deployment helper, streamed over verified SSH stdin."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

ROOT = Path("/home/ubuntu/cloudnet-direct")
UNIT = "cloudnet-control-launcher.service"
DROPIN = Path("/etc/systemd/system") / (UNIT + ".d") / "week3-audit.conf"


def run(args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=50)
    if check and result.returncode:
        raise RuntimeError("Command failed: " + " ".join(args[:3]))
    return result


def protected_hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("agent", "admin_gui", "secrets") for p in (ROOT / folder).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def verify():
    # Authenticate using files on the instance; never return credentials.
    sys.path.insert(0, str(ROOT))
    import socket
    import ssl
    from uuid import uuid4
    from datetime import datetime, timedelta, timezone
    from common.protocol import recv_message, send_message
    auth = json.loads((ROOT / "secrets/ec2/auth.json").read_text())
    cert = ROOT / "secrets/ec2/server.crt"
    names = ssl._ssl._test_decode_cert(str(cert))["subjectAltName"]
    identity = next(value for kind, value in names if kind in ("IP Address", "DNS"))
    context = ssl.create_default_context(cafile=str(cert))
    deadline = time.monotonic() + 10
    while True:
        try:
            raw = socket.create_connection(("127.0.0.1", 5000), timeout=2)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.2)
    with raw:
        with context.wrap_socket(raw, server_hostname=identity) as conn:
            conn.settimeout(10)
            send_message(conn, dict(type="REGISTER", role="admin", token=auth["admin_token"]))
            registered = recv_message(conn)
            if registered.get("ok") is not True:
                raise RuntimeError("Administrator authentication failed")
            snapshots = []
            for _ in range(2):
                send_message(conn, dict(type="LIST_DEVICES"))
                snapshots.append(recv_message(conn)["devices"])
                if len(snapshots) == 1:
                    time.sleep(3)
            results = []
            for device in snapshots[-1]:
                previous = next((d for d in snapshots[0] if d["pc_id"] == device["pc_id"]), {})
                metrics = device.get("metrics") or {}
                result = {"pc_id": device["pc_id"], "online": device["online"],
                          "metrics_stale": device["metrics_stale"],
                          "last_metrics_received": device.get("last_metrics_received"),
                          "updated": previous.get("last_metrics_received") != device.get("last_metrics_received"),
                          "cpu_percent": metrics.get("cpu_percent"), "ram_percent": metrics.get("ram_percent")}
                if device["online"]:
                    command_id = str(uuid4())
                    send_message(conn, dict(type="COMMAND", command_id=command_id,
                        target_id=device["pc_id"], action="LIST_PROCESSES",
                        expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()))
                    pages, count = set(), 0
                    while True:
                        try:
                            message = recv_message(conn)
                        except socket.timeout:
                            result["process_result"] = "TIMEOUT_UNCONFIRMED"
                            results.append(result)
                            print(json.dumps({"tls_admin": True, "devices": results,
                                              "remaining_process_queries": "not_attempted_after_timeout"}))
                            return
                        if message.get("command_id") != command_id or message.get("pc_id") != device["pc_id"]:
                            raise RuntimeError("Process response identity mismatch")
                        result["process_result"] = message.get("code")
                        if not message.get("ok"):
                            break
                        pages.add(message["page"])
                        count += len(message.get("processes", []))
                        if len(pages) == message["pages"]:
                            result.update(process_count=count, process_pages=len(pages))
                            break
                results.append(result)
            print(json.dumps({"tls_admin": True, "capabilities": {key: registered.get(key) for key in
                ("exam_events_v1", "exam_control_v1", "exam_foreground_v1")}, "devices": results}))


def deploy(revision):
    if not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        raise ValueError("Invalid revision")
    archive = Path("/home/ubuntu") / ("cloudnet-week3-" + revision + ".zip")
    before = protected_hashes()
    initial_active = run(["systemctl", "is-active", UNIT], check=False).stdout.strip() == "active"
    backup_dir = Path("/home/ubuntu/cloudnet-backups") / ("week3-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    backup_dir.mkdir(parents=True, mode=0o700)
    backup = backup_dir / "server-common.tar.gz"
    with tarfile.open(backup, "w:gz") as bundle:
        for name in ("server", "common"):
            bundle.add(ROOT / name, arcname=name)
    previous_dropin = DROPIN.read_bytes() if DROPIN.exists() else None
    if previous_dropin is not None:
        (backup_dir / "week3-audit.conf").write_bytes(previous_dropin)
    (backup_dir / "state.json").write_text(json.dumps(dict(revision=revision, previously_active=initial_active)))
    with tempfile.TemporaryDirectory(dir=ROOT.parent, prefix="cloudnet-week3-stage-") as directory:
        stage = Path(directory)
        with zipfile.ZipFile(archive) as package:
            for name in package.namelist():
                parts = Path(name).parts
                if (not parts or parts[0] not in ("server", "common") or ".." in parts
                        or Path(name).is_absolute() or not name.endswith(".py")):
                    raise ValueError("Package contains a non-A source file")
            package.extractall(stage)
        python = str(ROOT / ".venv/bin/python")
        run([python, "-m", "compileall", "-q", str(stage / "server"), str(stage / "common")])
        try:
            run(["sudo", "-n", "systemctl", "stop", UNIT])
            # Only replace Python source files owned by A; preserve all other trees.
            for path in stage.rglob("*.py"):
                target = ROOT / path.relative_to(stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
            run(["sudo", "-n", "install", "-d", "-m", "0700", "-o", "ubuntu", "-g", "ubuntu",
                 "/var/lib/cloudnet-control"])
            run(["sudo", "-n", "mkdir", "-p", str(DROPIN.parent)])
            config = ("[Service]\nStateDirectory=cloudnet-control\nStateDirectoryMode=0700\n"
                      "ExecStart=\nExecStart=" + python + " -u -m server.main --host 0.0.0.0 --port 5000 "
                      "--tls-cert secrets/ec2/server.crt --tls-key secrets/ec2/server.key "
                      "--auth-file secrets/ec2/auth.json --audit-db /var/lib/cloudnet-control/server-audit.sqlite3\n")
            local_config = backup_dir / "new-week3-audit.conf"
            local_config.write_text(config)
            run(["sudo", "-n", "install", "-m", "0644", str(local_config), str(DROPIN)])
            run(["sudo", "-n", "systemctl", "daemon-reload"])
            run(["sudo", "-n", "systemctl", "start", UNIT])
            for _ in range(30):
                if run(["systemctl", "is-active", UNIT], check=False).stdout.strip() == "active":
                    break
                time.sleep(.2)
            if protected_hashes() != before:
                raise RuntimeError("Protected B/C or credential files changed")
            (backup_dir / "protected-files-unchanged.txt").write_text("true\n")
            print(json.dumps(dict(deployed=revision, backup=str(backup_dir), protected_files_unchanged=True,
                                  active=run(["systemctl", "is-active", UNIT]).stdout.strip())))
            verify()
        except BaseException:
            run(["sudo", "-n", "systemctl", "stop", UNIT], check=False)
            with tarfile.open(backup) as bundle:
                bundle.extractall(ROOT, filter="data")
            if previous_dropin is not None:
                run(["sudo", "-n", "install", "-m", "0644", str(backup_dir / "week3-audit.conf"), str(DROPIN)])
            else:
                run(["sudo", "-n", "rm", "-f", str(DROPIN)])
            run(["sudo", "-n", "systemctl", "daemon-reload"])
            if initial_active:
                run(["sudo", "-n", "systemctl", "start", UNIT])
            print(json.dumps(dict(rolled_back=True, backup=str(backup_dir))))
            raise


if __name__ == "__main__":
    if sys.argv[1] == "deploy":
        deploy(sys.argv[2])
    elif sys.argv[1] == "verify":
        verify()
    else:
        raise ValueError("Use deploy REVISION or verify")
