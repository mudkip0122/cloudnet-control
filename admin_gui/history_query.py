"""Client for the proposed week-four history contract."""
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4

from admin_gui.charts import FIELDS, metric_value
from admin_gui.exam_events import timestamp
from common.protocol import recv_message, send_message
from common.security import connect

ALERT_LABELS = {
    "CPU_HIGH": "CPU 임계치", "RAM_HIGH": "RAM 임계치", "DEVICE_OFFLINE": "PC 오프라인",
}


def history_request(pc_id, minutes):
    pc_id = pc_id.strip()
    if not pc_id or len(pc_id) > 128 or any(ord(char) < 32 for char in pc_id):
        raise ValueError("PC ID를 확인하세요.")
    end = datetime.now(timezone.utc)
    return dict(type="GET_HISTORY", version=1, request_id=str(uuid4()), pc_id=pc_id,
                start_at=(end - timedelta(minutes=minutes)).isoformat(), end_at=end.isoformat())


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
            if reply.get("code") == "HISTORY_UNSUPPORTED":
                raise ValueError("서버가 이력 조회를 지원하지 않습니다.")
            raise ValueError("서버가 이력 조회를 완료하지 못했습니다.")
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
    if context is None or not token:
        raise ValueError("이력 조회에는 TLS 관리자 인증이 필요합니다.")
    with connect(host, port, context, server_name) as conn:
        send_message(conn, dict(type="REGISTER", role="admin", token=token))
        reply = recv_message(conn)
        if reply.get("type") != "REGISTER_RESULT" or reply.get("ok") is not True:
            raise ValueError("이력 조회 관리자 인증에 실패했습니다.")
        if reply.get("history_v1") is not True:
            raise ValueError("서버가 이력 조회를 지원하지 않습니다.")
        send_message(conn, request)
        deadline = time.monotonic() + 10

        def receive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            conn.settimeout(remaining)
            return recv_message(conn)

        return collect_history(request, receive)


def sample_history(request):
    end = timestamp(request["end_at"])
    metrics, events = [], []
    if request["pc_id"] == "sample-pc-01":
        duration = int((end - timestamp(request["start_at"])).total_seconds())
        for index in range(min(duration, 720) // 6):
            measured = end - timedelta(seconds=6 * (index + 1) + (30 if index > 35 else 0))
            if measured < timestamp(request["start_at"]):
                break
            cpu = 94 if 11 <= index <= 14 else 20 + index % 13
            metrics.append(dict(received_at=measured.isoformat(), cpu_percent=cpu, ram_percent=60 + index % 7,
                                upload_mbps=.5 + index % 5, download_mbps=8 + index % 11))
        for alert_id, kind, raised, resolved in (("cpu-1", "CPU_HIGH", 90, 60),
                                                  ("offline-1", "DEVICE_OFFLINE", 250, 220)):
            for state, offset in (("raised", raised), ("resolved", resolved)):
                events.append(dict(alert_id=alert_id, kind=kind, state=state,
                                   occurred_at=(end - timedelta(seconds=offset)).isoformat()))
        events.append(events[0].copy())
    response = dict(type="HISTORY_RESULT", version=1, request_id=request["request_id"], pc_id=request["pc_id"],
                    ok=True, page=0, done=True, metrics=metrics, events=events)
    return collect_history(request, lambda: response)
