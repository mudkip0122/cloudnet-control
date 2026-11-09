"""Testable lifecycle; GUI never holds AWS keys or runs shell-built local commands."""
import json
from pathlib import Path
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time

from common.protocol import recv_message, send_message
from common.security import connect
from launcher.config import ROOT, local_path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(Exception):
    pass


class AwsInstance:
    def __init__(self, config):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise RuntimeError("AWS 모듈이 없습니다. Launch CloudNet.cmd로 실행하세요.") from exc
        session = boto3.Session(profile_name=config["profile"] or None, region_name=config["region"])
        self.client = session.client("ec2", config=Config(connect_timeout=5, read_timeout=15,
                                                        retries={"max_attempts": 2}))
        self.instance_id = config["instance_id"]

    def verify_permissions(self):
        from botocore.exceptions import ClientError
        for method in (self.client.start_instances, self.client.stop_instances):
            try:
                method(InstanceIds=[self.instance_id], DryRun=True)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "DryRunOperation":
                    raise
            else:
                raise RuntimeError("AWS 권한 확인 응답을 확인하지 못했습니다.")

    def describe(self):
        reservations = self.client.describe_instances(InstanceIds=[self.instance_id])["Reservations"]
        instances = [i for r in reservations for i in r["Instances"]]
        if len(instances) != 1:
            raise RuntimeError("EC2 인스턴스가 없습니다. ID와 리전을 확인하세요.")
        return instances[0]

    def start(self):
        self.client.start_instances(InstanceIds=[self.instance_id])

    def stop(self):
        # Never call terminate_instances: instance and disk must be preserved.
        self.client.stop_instances(InstanceIds=[self.instance_id])


class SshService:
    def __init__(self, config):
        self.config = config
        self.executable = shutil.which("ssh")
        if not self.executable:
            candidate = Path("C:/Windows/System32/OpenSSH/ssh.exe")
            if candidate.exists():
                self.executable = str(candidate)
        if not self.executable:
            raise RuntimeError("Windows OpenSSH Client가 필요합니다.")

    def run(self, host, action):
        config = self.config
        script = (ROOT / "launcher" / "remote_service.py").read_text(encoding="utf-8")
        remote = "python3 - " + " ".join(shlex.quote(s) for s in (
            action, config["server_directory"], str(config["server_port"])))
        args = [self.executable, "-i", str(local_path(config["ssh_key"])),
                "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "CheckHostIP=no", "-o", f"HostKeyAlias={config['ssh_host_key_alias']}",
                "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5",
                "-o", "ServerAliveCountMax=2", f"{config['ssh_user']}@{host}", remote]
        result = subprocess.run(args, input=script, text=True, encoding="utf-8", errors="replace",
                                capture_output=True, timeout=150, creationflags=CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError("SSH/서버 설정 실패: " + result.stderr[-2500:])
        marker = "CLOUDNET_SERVER_STARTED" if action == "start" else "CLOUDNET_SERVER_STOPPED"
        if marker not in result.stdout:
            raise RuntimeError("서버 상태 확인 응답이 없습니다.")


class Session:
    def __init__(self, config, aws, service, report=lambda message: None, cancel=None,
                 wait=None, record_file=None):
        self.config, self.aws, self.service, self.report = config, aws, service, report
        self.cancel = cancel if cancel is not None else threading.Event()
        self.wait = wait if wait is not None else self.cancel.wait
        self.record_file = record_file or ROOT / "runtime" / "launcher-session.json"
        self.host = None
        self.must_stop = False
        self.service_attempted = False

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled()

    def record(self):
        self.record_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.record_file.with_suffix(".tmp")
        temp.write_text(json.dumps({"instance_id": self.config["instance_id"],
                                   "region": self.config["region"], "profile": self.config["profile"],
                                   "host": self.host, "needs_stop": self.must_stop}), encoding="utf-8")
        temp.replace(self.record_file)

    def wait_state(self, allowed, attempts=180):
        for _ in range(attempts):
            self.check_cancel()
            instance = self.aws.describe()
            state = instance["State"]["Name"]
            if state in allowed:
                return instance
            if state in ("terminated", "shutting-down"):
                raise RuntimeError("종료된 인스턴스는 다시 시작할 수 없습니다.")
            self.wait(2)
        raise TimeoutError("EC2 상태 변경 대기 시간이 초과되었습니다.")

    def start(self):
        self.check_cancel()
        instance = self.aws.describe()
        state = instance["State"]["Name"]
        if state == "stopping":
            self.report("EC2가 중지되는 중입니다. 기다리는 중…")
            instance = self.wait_state({"stopped"})
            state = "stopped"
        if state == "stopped":
            # Record before the API call, so ambiguous network failures can be cleaned up.
            self.must_stop = True
            self.record()
            self.report("EC2 인스턴스를 시작하는 중…")
            self.aws.start()
        elif state not in ("running", "pending"):
            raise RuntimeError(f"시작할 수 없는 EC2 상태: {state}")
        instance = self.wait_state({"running"})
        self.host = instance.get("PublicIpAddress")
        if not self.host:
            raise RuntimeError("EC2에 퍼블릭 IPv4 주소가 없습니다.")
        self.record()
        self.report(f"현재 EC2 주소: {self.host} · SSH 준비 대기 중…")
        # SSH can take a little longer than EC2's running transition.
        for _ in range(90):
            self.check_cancel()
            try:
                with socket.create_connection((self.host, 22), timeout=2):
                    break
            except OSError:
                self.wait(2)
        else:
            raise TimeoutError("SSH 22번에 접속할 수 없습니다. 보안 그룹의 내 IP를 확인하세요.")
        self.check_cancel()
        self.service_attempted = True
        self.report("EC2 서버 프로그램을 서비스로 실행하는 중…")
        self.service.run(self.host, "start")
        # Once the service is managed, closing this owner's launcher stops even a previously running instance.
        self.must_stop = True
        self.record()
        self.check_cancel()
        self.verify_server()
        self.report("서버 연결 확인 완료")
        return self.host

    def verify_server(self):
        config = self.config
        context = ssl.create_default_context(cafile=str(local_path(config["admin_ca_file"])))
        token = local_path(config["admin_token_file"]).read_text(encoding="utf-8").strip()
        for attempt in range(20):
            self.check_cancel()
            try:
                with connect(self.host, config["server_port"], context, config["certificate_name"]) as conn:
                    send_message(conn, {"type": "REGISTER", "role": "admin", "token": token})
                    result = recv_message(conn)
                    if result.get("type") != "REGISTER_RESULT" or result.get("ok") is not True:
                        raise ValueError("관리자 인증에 실패했습니다.")
                    return
            except ssl.SSLCertVerificationError:
                raise
            except OSError:
                if attempt == 19:
                    raise
                self.wait(1)

    def stop(self):
        errors = []
        if self.host and self.service_attempted:
            self.report("서버 프로그램을 종료하는 중…")
            try:
                self.service.run(self.host, "stop")
            except Exception as exc:
                errors.append(str(exc))
        if not self.must_stop:
            return errors
        self.report("EC2 인스턴스를 중지하는 중…")
        try:
            instance = self.aws.describe()
            # StartInstances is asynchronous; do not issue StopInstances during pending.
            for _ in range(180):
                if instance["State"]["Name"] != "pending":
                    break
                time.sleep(2)
                instance = self.aws.describe()
            state = instance["State"]["Name"]
            if state == "running":
                self.aws.stop()
            elif state not in ("stopping", "stopped"):
                raise RuntimeError(f"중지 요청을 할 수 없는 상태: {state}")
            for _ in range(180):
                if instance["State"]["Name"] == "stopped":
                    self.must_stop = False
                    self.record()
                    self.report("서버 종료 및 EC2 중지 완료")
                    return errors
                time.sleep(2)
                instance = self.aws.describe()
            raise TimeoutError("EC2 중지 확인 시간이 초과됐습니다. AWS 콘솔을 확인하세요.")
        except Exception as exc:
            errors.append(str(exc))
            self.report("EC2 중지를 확인하지 못했습니다. AWS 콘솔에서 상태를 확인하세요.")
        return errors


def client_command(config, host, module, token_file, pc_id=None):
    command = [sys.executable, "-m", module, "--host", host, "--port", str(config["server_port"]),
               "--ca-file", str(local_path(config["admin_ca_file"])),
               "--token-file", str(local_path(token_file)), "--server-name", config["certificate_name"]]
    if pc_id:
        command.extend(["--pc-id", pc_id, "--live"])
    return command
