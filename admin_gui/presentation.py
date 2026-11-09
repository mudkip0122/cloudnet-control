"""Display rules shared by the local dashboard and its tests."""
from datetime import datetime, timezone


def device_view(device, connected=True, now=None):
    now = now or datetime.now(timezone.utc)
    metrics = device.get("metrics") or {}
    age = None
    try:
        stamp = datetime.fromisoformat(metrics["timestamp"].replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            age = (now - stamp).total_seconds()
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    if not connected:
        state = "서버 연결 끊김"
    elif not device.get("online"):
        state = "오프라인"
    elif not metrics:
        state = "데이터 없음"
    elif age is None or age < -6:
        state = "측정 시각 오류"
    elif age > 6:
        state = "데이터 지연"
    else:
        state = "온라인"
    valid = state == "온라인"

    def fmt(field, places):
        value = metrics.get(field)
        return f"{value:.{places}f}" if valid and value is not None else "—"

    return {
        "name": device["pc_name"] + " / " + device["pc_id"],
        "state": state,
        "cpu": fmt("cpu_percent", 1), "ram": fmt("ram_percent", 1),
        "up": fmt("upload_mbps", 2), "down": fmt("download_mbps", 2),
        "time": metrics.get("timestamp", "—"),
        "received": device.get("last_received", "—"),
        "age": f"{max(0, age):.1f}초" if age is not None else "—",
    }
