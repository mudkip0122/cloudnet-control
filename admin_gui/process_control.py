"""Process command transport and complete-snapshot assembly, independent of Tk."""
from datetime import datetime, timedelta, timezone
import math
import queue
import socket
import threading
import time
from uuid import uuid4

from common.protocol import recv_message, send_message
from common.security import connect


class ProcessProtocolError(ValueError):
    pass


class ProcessPages:
    """Never expose a partially received inventory to a caller."""
    def __init__(self, command_id, pc_id):
        self.command_id, self.pc_id = command_id, pc_id
        self.total = None
        self.parts = {}

    def add(self, message):
        if (message.get("command_id") != self.command_id or message.get("pc_id") != self.pc_id):
            raise ProcessProtocolError("응답 대상 또는 명령 ID 불일치")
        page, pages = message.get("page"), message.get("pages")
        rows = message.get("processes")
        if (message.get("ok") is not True or message.get("code") != "PROCESS_LIST"
                or type(page) is not int or type(pages) is not int
                or not 1 <= pages <= 4096 or not 0 <= page < pages
                or not isinstance(rows, list) or len(rows) > 40
                or (self.total is not None and pages != self.total) or page in self.parts):
            raise ProcessProtocolError("프로세스 페이지 형식 오류")
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("pid")) is not int or row["pid"] < 0
                    or type(row.get("can_stop")) is not bool):
                raise ProcessProtocolError("프로세스 필드 오류")
            for key in ("name", "username", "protected_reason"):
                if row.get(key) is not None and not isinstance(row[key], str):
                    raise ProcessProtocolError("프로세스 문자열 필드 오류")
            memory, created = row.get("memory_bytes"), row.get("create_time")
            if memory is not None and (type(memory) is not int or memory < 0):
                raise ProcessProtocolError("메모리 필드 오류")
            if created is not None and (type(created) not in (int, float) or not math.isfinite(created) or created < 0):
                raise ProcessProtocolError("생성 시각 필드 오류")
            if row["can_stop"] and (created is None or created <= 0 or row.get("protected_reason")):
                raise ProcessProtocolError("종료 가능한 프로세스의 식별·보호 정보 오류")
        self.total = pages
        self.parts[page] = rows
        if len(self.parts) != pages:
            return None
        combined = [row for index in range(pages) for row in self.parts[index]]
        if len({row["pid"] for row in combined}) != len(combined):
            raise ProcessProtocolError("중복 PID")
        return combined


def batch_targets(rows):
    targets = {}
    for row in rows:
        pid, created = row.get("pid"), row.get("create_time")
        if (row.get("can_stop") is True and type(pid) is int and pid > 0
                and type(created) in (int, float) and math.isfinite(created) and created > 0):
            targets[pid] = {"pid": pid, "create_time": created}
    if not 1 <= len(targets) <= 100:
        raise ValueError("종료 대상은 1~100개여야 합니다. 선택 범위를 줄이세요.")
    return list(targets.values())


def command_request(pc_id, action, row=None):
    request = {"type": "COMMAND", "command_id": str(uuid4()), "target_id": pc_id,
               "action": action,
               "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()}
    if action == "STOP_PROCESS":
        if row is None or row.get("can_stop") is not True or row.get("create_time") is None:
            raise ValueError("종료할 수 없는 프로세스")
        request.update(pid=row["pid"], create_time=row["create_time"])
    elif action == "STOP_PROCESSES":
        request["targets"] = batch_targets(row or [])
    return request


class ProcessClient:
    """One worker serializes commands on a dedicated authenticated admin session.

    Results are lossless; the dashboard's latest-only metric queue is not used.
    Commands are never automatically resent after disconnection.
    """
    def __init__(self, host, port, context, token, timeout=15, server_name=None):
        self.host, self.port, self.context, self.token = host, port, context, token
        self.timeout = timeout
        self.server_name = server_name
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.stop = threading.Event()
        self.sock = None
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def submit(self, request):
        if self.stop.is_set():
            raise ValueError("프로세스 연결이 닫혔습니다")
        self.jobs.put(request)

    def run(self):
        while not self.stop.is_set():
            try:
                request = self.jobs.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                result = self.execute(request)
            except ProcessProtocolError as exc:
                result = {"ok": False, "code": "INVALID_PROCESS_RESPONSE", "message": str(exc)}
            except TimeoutError:
                result = {"ok": False, "code": "COMMAND_TIMEOUT"}
            except (OSError, EOFError, ValueError, UnicodeError):
                result = {"ok": False, "code": "TRANSPORT_ERROR",
                          "message": "연결·인증 또는 응답 오류. 종료 결과는 불명확할 수 있습니다. 재조회하세요."}
            self.results.put((request, result))

    def execute(self, request):
        if self.context is None or not self.token:
            raise ValueError("프로세스 제어에는 TLS 관리자 인증이 필요합니다")
        with connect(self.host, self.port, self.context, self.server_name) as conn:
            self.sock = conn
            try:
                if self.stop.is_set():
                    raise EOFError()
                send_message(conn, {"type": "REGISTER", "role": "admin", "token": self.token})
                response = recv_message(conn)
                if response.get("type") != "REGISTER_RESULT" or response.get("ok") is not True:
                    raise ProcessProtocolError("관리자 인증 실패")
                send_message(conn, request)
                deadline = time.monotonic() + self.timeout
                assembly = ProcessPages(request["command_id"], request["target_id"])
                while not self.stop.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    conn.settimeout(remaining)
                    message = recv_message(conn)
                    if message.get("type") in ("DEVICE_LIST", "METRICS", "HEARTBEAT", "HEARTBEAT_ACK", "STATUS_UPDATE"):
                        continue
                    if message.get("type") == "COMMAND_RESULT" and message.get("command_id") != request["command_id"]:
                        continue
                    if (message.get("type") != "COMMAND_RESULT"
                            or message.get("pc_id") != request["target_id"]
                            or type(message.get("ok")) is not bool):
                        raise ProcessProtocolError("명령 결과 식별 오류")
                    if request["action"] == "LIST_PROCESSES" and message["ok"]:
                        rows = assembly.add(message)
                        if rows is None:
                            continue
                        return {"ok": True, "code": "PROCESS_LIST", "processes": rows}
                    return message
                raise EOFError()
            finally:
                self.sock = None

    def close(self):
        self.stop.set()
        conn = self.sock
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
