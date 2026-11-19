"""Client for the proposed week-four history contract."""
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4

from admin_gui.charts import FIELDS, metric_value
from admin_gui.exam_events import exam_event, timestamp
from common.protocol import recv_message, send_message
from common.security import connect

ALERT_LABELS = {
    "CPU_HIGH": "CPU 임계치", "RAM_HIGH": "RAM 임계치", "DEVICE_OFFLINE": "PC 오프라인",
}
LOCAL_TIME_FORMAT = "%Y-%m-%d %H:%M"
MAX_HISTORY_PERIOD = timedelta(days=30)


def history_request(pc_id, minutes=None, *, start_at=None, end_at=None):
    pc_id = pc_id.strip()
    if not pc_id or len(pc_id) > 128 or any(ord(char) < 32 for char in pc_id):
        raise ValueError("PC ID를 확인하세요.")
    if minutes is None:
        try:
            start, end = (datetime.strptime(value.strip(), LOCAL_TIME_FORMAT).astimezone(timezone.utc)
                          for value in (start_at, end_at))
        except (AttributeError, ValueError, OverflowError, OSError) as exc:
            raise ValueError("시작·종료 시각을 YYYY-MM-DD HH:MM 형식으로 입력하세요.") from exc
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
    if start >= end:
        raise ValueError("종료 시각은 시작 시각보다 늦어야 합니다.")
    if end - start > MAX_HISTORY_PERIOD:
        raise ValueError("조회 기간은 최대 30일입니다.")
    return dict(type="GET_HISTORY", version=1, request_id=str(uuid4()), pc_id=pc_id,
                start_at=start.isoformat(), end_at=end.isoformat())


def collect_history(request, receive):
    start, end = (timestamp(request[key]) for key in ("start_at", "end_at"))
    points, alerts = {}, {}
    for page in range(64):
        reply = receive()
        if (reply.get("type") != "HISTORY_RESULT" or reply.get("request_id") != request["request_id"]
                or reply.get("pc_id") != request["pc_id"] or type(reply.get("version")) is not int
                or reply["version"] != 1):
            raise ValueError("이력 응답의 요청 식별 정보가 일치하지 않습니다.")
        if reply.get("ok") is False:
            errors = {
                "HISTORY_UNSUPPORTED": "서버가 이력 조회를 지원하지 않습니다.",
                "HISTORY_TOO_LARGE": "결과가 너무 많습니다. 더 짧은 기간으로 조회하세요.",
                "INVALID_REQUEST": "서버가 PC ID 또는 조회 기간을 거절했습니다.",
                "STORAGE_UNAVAILABLE": "서버 이력 저장소를 사용할 수 없습니다.",
            }
            code = reply.get("code")
            detail = errors.get(code) if isinstance(code, str) else None
            raise ValueError(detail or "서버가 이력 조회를 완료하지 못했습니다.")
        metrics, events = reply.get("metrics"), reply.get("events")
        if (reply.get("ok") is not True or type(reply.get("page")) is not int or reply["page"] != page
                or type(reply.get("done")) is not bool or not isinstance(metrics, list)
                or not isinstance(events, list) or len(metrics) + len(events) > 200):
            raise ValueError("이력 응답 형식이 올바르지 않습니다.")
        for row in metrics:
            if not isinstance(row, dict) or any(key not in row for key in FIELDS):
                raise ValueError("측정 기록 형식이 올바르지 않습니다.")
            observed = timestamp(row.get("received_at"))
            if not start <= observed < end:
                raise ValueError("측정 기록의 조회 범위가 일치하지 않습니다.")
            values = {key: metric_value(row[key], key in FIELDS[:2]) for key in FIELDS}
            if any(row[key] is not None and values[key] is None for key in FIELDS):
                raise ValueError("측정값 형식이 올바르지 않습니다.")
            if observed in points and points[observed] != values:
                raise ValueError("같은 시각의 측정 기록이 중복됩니다.")
            points[observed] = values
        for row in events:
            if not isinstance(row, dict):
                raise ValueError("경고 기록 형식이 올바르지 않습니다.")
            observed = timestamp(row.get("occurred_at"))
            episode, kind, state = row.get("alert_id"), row.get("kind"), row.get("state")
            if (not start <= observed < end or not isinstance(episode, str) or not 1 <= len(episode) <= 128
                    or kind not in tuple(ALERT_LABELS) or state not in ("raised", "resolved")):
                raise ValueError("경고 기록 형식이 올바르지 않습니다.")
            key = (episode, kind, state)
            alerts[key] = min(observed, alerts.get(key, observed))
        if reply["done"]:
            rows = [(observed, values) for observed, values in sorted(points.items())]
            transitions = sorted(((observed, kind, state) for (_, kind, state), observed in alerts.items()), reverse=True)
            return rows, transitions
    raise ValueError("이력 응답이 최대 페이지 수를 초과했습니다.")


def query_history(host, port, context, token, server_name, request):
    return fetch_history(host, port, context, token, server_name, request, "history_v1",
                         "이력 조회", collect_history)


def query_audit_history(host, port, context, token, server_name, request):
    return fetch_history(host, port, context, token, server_name, request, "audit_history_v1",
                         "감사 이력 조회", collect_audit_history)


def fetch_history(host, port, context, token, server_name, request, capability, label, collect):
    if context is None or not token:
        raise ValueError(f"{label}에는 TLS 관리자 인증이 필요합니다.")
    with connect(host, port, context, server_name) as conn:
        send_message(conn, dict(type="REGISTER", role="admin", token=token))
        reply = recv_message(conn)
        if reply.get("type") != "REGISTER_RESULT" or reply.get("ok") is not True:
            raise ValueError(f"{label} 관리자 인증에 실패했습니다.")
        if reply.get(capability) is not True:
            raise ValueError(f"서버가 {label}를 지원하지 않습니다.")
        send_message(conn, request)
        deadline = time.monotonic() + 10

        def receive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            conn.settimeout(remaining)
            return recv_message(conn)

        return collect(request, receive)


def collect_audit_history(request, receive):
    expected = {key: request[key] for key in ("request_id", "pc_id", "kind")}
    start, end = (timestamp(request[key]) for key in ("start_at", "end_at"))
    records = []
    for page in range(2000):
        reply = receive()
        if (reply.get("type") != "AUDIT_HISTORY_RESULT" or type(reply.get("version")) is not int
                or reply["version"] != 1 or any(reply.get(key) != value for key, value in expected.items())):
            raise ValueError("감사 이력 응답의 요청 식별 정보가 일치하지 않습니다.")
        if reply.get("ok") is False:
            code = reply.get("code")
            errors = {
                "HISTORY_TOO_LARGE": "결과가 너무 많습니다. 더 짧은 기간으로 조회하세요.",
                "INVALID_REQUEST": "서버가 PC ID 또는 조회 기간을 거절했습니다.",
                "TLS_REQUIRED": "TLS 관리자 인증이 필요합니다.",
                "STORAGE_UNAVAILABLE": "서버 감사 저장소를 사용할 수 없습니다.",
            }
            raise ValueError(errors.get(code, "서버가 감사 이력 조회를 완료하지 못했습니다.")
                             if isinstance(code, str) else "서버 감사 이력 오류")
        rows = reply.get("rows")
        if (reply.get("ok") is not True or type(reply.get("page")) is not int or reply["page"] != page
                or type(reply.get("done")) is not bool or not isinstance(rows, list)
                or len(rows) > 100 or len(records) + len(rows) > 2000
                or not rows and not reply["done"]):
            raise ValueError("감사 이력 페이지 형식이 올바르지 않습니다.")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("감사 기록 형식이 올바르지 않습니다.")
            if request["kind"] == "exam":
                event = exam_event(row.get("event"))
                observed = timestamp(row.get("received_at"))
                record = {"event": event, "received_at": observed}
            else:
                event = power_record(row)
                observed = event["requested_at"]
                record = event
            if event["pc_id"] != request["pc_id"] or not start <= observed < end:
                raise ValueError("감사 기록의 PC 또는 조회 범위가 일치하지 않습니다.")
            records.append(record)
        if reply["done"]:
            return records
    raise ValueError("감사 이력 응답이 최대 페이지 수를 초과했습니다.")


def power_record(row):
    def required_text(value):
        return isinstance(value, str) and 0 < len(value) <= 128

    if (any(not required_text(row.get(key)) for key in ("pc_id", "command_id", "outcome"))
            or row.get("action") not in ("SHUTDOWN_PC", "REBOOT_PC")
            or row.get("power_control_mode") not in (None, "simulate", "execute")
            or not isinstance(row.get("stages"), list)):
        raise ValueError("전원 명령 기록 형식이 올바르지 않습니다.")
    result = {key: row.get(key) for key in
              ("pc_id", "command_id", "action", "power_control_mode", "outcome", "result_code")}
    if result["result_code"] is not None and not required_text(result["result_code"]):
        raise ValueError("전원 명령 결과 형식이 올바르지 않습니다.")
    result["requested_at"] = timestamp(row.get("requested_at"))
    for key in ("result_received_at", "reboot_confirmed_at"):
        result[key] = timestamp(row[key]) if row.get(key) is not None else None
    result["stages"] = []
    for stage in row["stages"]:
        if (not isinstance(stage, dict) or not required_text(stage.get("stage"))
                or stage.get("code") is not None and not required_text(stage["code"])):
            raise ValueError("전원 명령 단계 형식이 올바르지 않습니다.")
        result["stages"].append((timestamp(stage.get("occurred_at")), stage["stage"], stage.get("code")))
    return result


def sample_history(request):
    start, end = (timestamp(request[key]) for key in ("start_at", "end_at"))
    metrics, events = [], []
    if request["pc_id"] == "sample-pc-01":
        duration = int((end - start).total_seconds())
        for index in range(min(duration, 720) // 6):
            measured = end - timedelta(seconds=6 * (index + 1) + (30 if index > 35 else 0))
            if measured < start:
                break
            cpu = 94 if 11 <= index <= 14 else 20 + index % 13
            metrics.append(dict(received_at=measured.isoformat(), cpu_percent=cpu, ram_percent=60 + index % 7,
                                upload_mbps=.5 + index % 5, download_mbps=8 + index % 11))
        for alert_id, kind, raised, resolved in (("cpu-1", "CPU_HIGH", 90, 60),
                                                  ("offline-1", "DEVICE_OFFLINE", 250, 220)):
            for state, offset in (("raised", raised), ("resolved", resolved)):
                occurred = end - timedelta(seconds=offset)
                if start <= occurred:
                    events.append(dict(alert_id=alert_id, kind=kind, state=state, occurred_at=occurred.isoformat()))
        if events:
            events.append(events[0].copy())
    response = dict(type="HISTORY_RESULT", version=1, request_id=request["request_id"], pc_id=request["pc_id"],
                    ok=True, page=0, done=True, metrics=metrics, events=events)
    return collect_history(request, lambda: response)
