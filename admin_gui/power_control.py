"""Track power requests independently of dashboard selection."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from admin_gui.process_control import command_request


def boot_time(value):
    if isinstance(value, str) and len(value) <= 128:
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                return stamp.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            pass
    return None


def power_target(device, sample=False):
    if not device or not device.get("online"):
        return None
    if sample:
        return "simulate", None, None
    mode, boot = device.get("power_control_mode"), device.get("boot_id")
    if mode == "simulate" and (boot is None or boot_time(boot)):
        return mode, boot, device.get("agent_session_id")
    if mode == "execute" and boot_time(boot):
        return mode, boot, device.get("agent_session_id")
    return None


@dataclass
class PowerOperation:
    request: dict
    session_id: str | None
    submitted_at: datetime
    code: str = "SENT"
    accepted: bool = False
    execution_started: bool = False
    confirmed_boot: bool = False

    @classmethod
    def start(cls, pc_id, action, target):
        mode, boot, session = target
        request = command_request(pc_id, action)
        request.update(power_control_mode=mode, target_boot_id=boot)
        return cls(request, session, datetime.now(timezone.utc))

    @property
    def awaiting_reboot(self):
        return (self.request["action"] == "REBOOT_PC" and self.request["power_control_mode"] == "execute"
                and (self.code in ("POWER_ACCEPTED", "POWER_EXECUTION_STARTED", "POWER_EXECUTION_UNKNOWN")
                     or self.accepted and self.code in ("COMMAND_TIMEOUT", "TRANSPORT_ERROR")))

    @property
    def busy(self):
        return self.code in ("SENT", "POWER_ACCEPTED") or self.awaiting_reboot

    def result(self, result):
        self.execution_started |= result["code"] == "POWER_EXECUTION_STARTED"
        if self.code in ("REBOOT_COMPLETED", "REBOOT_UNCONFIRMED"):
            return
        self.code = result["code"]
        self.accepted |= self.code == "POWER_ACCEPTED"
        if self.awaiting_reboot and self.confirmed_boot:
            self.code = "REBOOT_COMPLETED"

    def observe(self, device, now):
        if not (self.awaiting_reboot or self.code == "SENT" and self.request["action"] == "REBOOT_PC"
                and self.request["power_control_mode"] == "execute"):
            return
        if now - self.submitted_at >= timedelta(minutes=5):
            self.code = "REBOOT_UNCONFIRMED"
            return
        if not device or not device.get("online") or device.get("pc_id") != self.request["target_id"]:
            return
        boot = boot_time(device.get("boot_id"))
        previous = boot_time(self.request["target_boot_id"])
        session = device.get("agent_session_id")
        if boot and previous and boot > previous and self.session_id and session and session != self.session_id:
            self.confirmed_boot = True
            if self.awaiting_reboot:
                self.code = "REBOOT_COMPLETED"

    def label(self):
        labels = {
            "SENT": "요청 전송 중",
            "POWER_ACCEPTED": "요청 접수",
            "POWER_SIMULATED": "모의 실행 완료",
            "POWER_EXECUTION_STARTED": "실행 시작",
            "POWER_EXECUTION_UNKNOWN": "실행 결과 미확인",
            "REBOOT_COMPLETED": "재부팅 완료",
            "REBOOT_UNCONFIRMED": "재부팅 완료 미확인",
            "COMMAND_TIMEOUT": "응답 시간 초과 · 결과 미확인",
            "TRANSPORT_ERROR": "연결 오류 · 결과 미확인",
            "INVALID_PROCESS_RESPONSE": "응답 오류 · 결과 미확인",
            "POWER_EXECUTION_FAILED": "실행 실패",
            "POWER_UNSUPPORTED": "서버가 전원 제어를 지원하지 않습니다",
            "TARGET_OFFLINE": "요청 거절 · 대상 PC 오프라인",
            "BOOT_ID_MISMATCH": "요청 거절 · 대상 PC가 재부팅되었습니다",
            "POWER_MODE_MISMATCH": "요청 거절 · 전원 실행 모드가 변경되었습니다",
        }
        steps = []
        if self.accepted and self.code != "POWER_ACCEPTED":
            steps.append(labels["POWER_ACCEPTED"])
        if self.execution_started and self.code != "POWER_EXECUTION_STARTED":
            steps.append(labels["POWER_EXECUTION_STARTED"])
        steps.append(labels.get(self.code, f"요청 거절 ({self.code})"))
        if self.awaiting_reboot:
            steps.append("새 부팅 등록 대기")
        return " → ".join(steps)
