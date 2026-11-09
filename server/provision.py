"""Create EC2 TLS credentials and separate Windows client packages locally on EC2."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-ip", required=True)
    parser.add_argument("--output", default="secrets/ec2")
    args = parser.parse_args()
    ip = str(ipaddress.IPv4Address(args.public_ip))
    os.umask(0o077)
    dest = Path(args.output).resolve()
    dest.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    cert, key = dest / "server.crt", dest / "server.key"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-nodes",
        "-days", "365", "-keyout", str(key), "-out", str(cert),
        "-subj", "/CN=CloudNet Control",
        "-addext", f"subjectAltName=IP:{ip}",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "extendedKeyUsage=serverAuth",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    auth = {"agents": {f"PC-{i:02}": secrets.token_hex(32) for i in range(1, 4)},
            "admin_token": secrets.token_hex(32)}
    (dest / "auth.json").write_text(json.dumps(auth, indent=2), encoding="utf-8")
    for name, token in [*auth["agents"].items(), ("admin", auth["admin_token"])]:
        is_admin = name == "admin"
        module = "admin_gui.main" if is_admin else "agent.main"
        extra = "" if is_admin else f" --pc-id {name} --live"
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            "Set-Location -LiteralPath $PSScriptRoot\n"
            f"& '.\\.venv\\Scripts\\python.exe' -m {module} --host {ip} --port 5000 "
            f"--ca-file server.crt --token-file secrets/token.txt{extra}\n"
        )
        with zipfile.ZipFile(dest / f"{name}.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            for folder in ("common", "admin_gui" if is_admin else "agent"):
                for source in (root / folder).glob("*.py"):
                    bundle.write(source, source.relative_to(root).as_posix())
            bundle.write(root / "requirements.txt", "requirements.txt")
            bundle.write(cert, "server.crt")
            bundle.writestr("secrets/token.txt", token + "\n")
            bundle.writestr("run.ps1", script)
            bundle.writestr(".gitignore", "secrets/\n.venv/\n")
    print(f"Prepared server credentials and PC-01.zip, PC-02.zip, PC-03.zip, admin.zip in {dest}")
    print("Keep server.key and auth.json on EC2. Give each agent package only to its assigned PC.")


if __name__ == "__main__":
    main()
