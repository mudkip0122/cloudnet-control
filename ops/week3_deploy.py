"""Package only A Python sources and deploy using the existing SSH identity."""
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SSH = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
       "-o", "HostKeyAlias=32.236.13.91", "-i", str(Path.home() / ".ssh/sheep_jae0_key.pem")]
HOST = "ubuntu@13.238.97.144"


def main():
    action = sys.argv[1]
    if action not in ("deploy", "verify"):
        raise ValueError("Use deploy or verify")
    revision = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    if action == "deploy":
        archive = ROOT / "dist" / ("cloudnet-week3-" + revision + ".zip")
        archive.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
            for folder in ("server", "common"):
                for path in (ROOT / folder).rglob("*.py"):
                    package.write(path, path.relative_to(ROOT).as_posix())
        subprocess.run(["scp", *SSH, str(archive), HOST + ":/home/ubuntu/" + archive.name], check=True)
    remote = ["/home/ubuntu/cloudnet-direct/.venv/bin/python", "-", action]
    if action == "deploy":
        remote.append(revision)
    subprocess.run(["ssh", *SSH, HOST, *remote],
                   input=(ROOT / "ops/week3_remote.py").read_bytes(), check=True)


if __name__ == "__main__":
    main()
