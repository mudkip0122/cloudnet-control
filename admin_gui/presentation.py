"""Display rules shared by the local dashboard and its tests."""
from datetime import datetime, timezone
import math


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
    elif device.get("metrics_stale") is True or age > 6:
        state = "데이터 지연"
    else:
        state = "온라인"
    valid = state == "온라인"

    def fmt(field, places):
        value = metrics.get(field)
        numeric = type(value) in (int, float) and math.isfinite(value) and value >= 0
        if field in ("cpu_percent", "ram_percent") and numeric:
            numeric = value <= 100
        return f"{value:.{places}f}" if valid and numeric else "—"

    rtt = "—"
    if valid and device.get("metrics_stale") is False:
        status = metrics.get("rtt_status")
        rtt = {"pending": "측정 대기", "timeout": "응답 지연"}.get(status, "—")
        if status == "ok":
            try:
                stamp = datetime.fromisoformat(metrics["rtt_timestamp"].replace("Z", "+00:00"))
                value = metrics.get("rtt_ms")
                if (stamp.tzinfo is not None and -6 <= (now - stamp).total_seconds() <= 8
                        and type(value) in (int, float) and math.isfinite(value) and value >= 0):
                    rtt = f"{value:.1f} ms"
            except (KeyError, ValueError, TypeError, AttributeError):
                pass

    return {
        "name": device["pc_name"] + " / " + device["pc_id"],
        "state": state, "rtt": rtt,
        "cpu": fmt("cpu_percent", 1), "ram": fmt("ram_percent", 1),
        "up": fmt("upload_mbps", 2), "down": fmt("download_mbps", 2),
        "time": metrics.get("timestamp", "—"),
        "received": device.get("last_metrics_received", device.get("last_received")) or "—",
        "age": f"{max(0, age):.1f}초" if age is not None else "—",
    }
