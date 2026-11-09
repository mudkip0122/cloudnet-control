import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "runtime" / "launcher.json"
DEFAULTS = {
    "instance_id": "", "region": "ap-southeast-2", "profile": "default",
    "ssh_key": str(Path.home() / ".ssh" / "sheep_jae0_key.pem"),
    "ssh_user": "ubuntu", "ssh_host_key_alias": "32.236.13.91",
    "server_directory": "/home/ubuntu/cloudnet-direct", "server_port": 5000,
    "certificate_name": "32.236.13.91",
    "admin_ca_file": "secrets/clients/admin/server.crt",
    "admin_token_file": "secrets/clients/admin/secrets/token.txt",
    "agent_token_file": "secrets/agent/token.txt",
    "pc_id": "PC-01", "start_local_agent": True,
}


def load_config(path=CONFIG_FILE):
    if not path.exists():
        return dict(DEFAULTS)
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or set(saved) - set(DEFAULTS):
        raise ValueError("Unknown launcher settings; check runtime/launcher.json")
    return DEFAULTS | saved


def local_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def validate(config):
    if not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", config["instance_id"]):
        raise ValueError("EC2 인스턴스 ID(i-...)를 입력하세요.")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", config["region"]):
        raise ValueError("리전을 확인하세요. 예: ap-southeast-2")
    if not re.fullmatch(r"/[A-Za-z0-9_/-]+", config["server_directory"]):
        raise ValueError("서버 폴더는 공백 없는 Linux 절대 경로여야 합니다.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", config["ssh_user"]):
        raise ValueError("SSH 사용자 이름을 확인하세요.")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", config["ssh_host_key_alias"]):
        raise ValueError("기존에 확인한 SSH 호스트 이름/IP를 지정하세요.")
    if not isinstance(config["server_port"], int) or not 1 <= config["server_port"] <= 65535:
        raise ValueError("서버 포트가 잘못되었습니다.")
    for key in ("ssh_key", "admin_ca_file", "admin_token_file"):
        if not local_path(config[key]).is_file():
            raise ValueError(f"파일이 없습니다: {local_path(config[key])}")
    if config["start_local_agent"] and not local_path(config["agent_token_file"]).is_file():
        raise ValueError("본인 PC Agent 토큰 파일이 없습니다.")


def save_config(config, path=CONFIG_FILE):
    # Only known non-secret settings are saved. AWS credentials stay in the AWS profile.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({k: config[k] for k in DEFAULTS}, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
