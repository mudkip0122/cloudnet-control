"""Connection-scoped application RTT using a monotonic clock."""
from datetime import datetime, timezone
import math
import threading
import time
from uuid import uuid4


def validate_rtt_settings(interval, timeout):
    for value in (interval, timeout):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("RTT settings must be finite numbers")
    if interval != 0 and not 1 <= interval <= 60:
        raise ValueError("--rtt-interval must be 0 (disabled) or 1-60 seconds")
    if not .1 <= timeout <= 60 or (interval and timeout > interval):
        raise ValueError("--rtt-timeout must be 0.1-60 seconds and no greater than the interval")


class RttTracker:
    def __init__(self, pc_id, interval=5, timeout=3):
        validate_rtt_settings(interval, timeout)
        self.pc_id, self.interval, self.timeout = pc_id, interval, timeout
        self.lock = threading.Lock()
        self.pending = None
        self.value = None
        self.status = "pending"
        self.timestamp = None
        self.completed = None

    def finish(self, value, status, now):
        self.value, self.status, self.completed = value, status, now
        self.timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.pending = None

    def expire(self, now):
        if self.pending and now - self.pending[1] >= self.timeout:
            self.finish(None, "timeout", now)

    def begin(self):
        """Call only after acquiring the connection send lock, just before send."""
        with self.lock:
            now = time.monotonic()
            self.expire(now)
            if self.pending is not None:
                raise ValueError("An RTT probe is already in flight")
            probe_id = str(uuid4())
            self.pending = (probe_id, now)
            return {"type": "RTT_PING", "pc_id": self.pc_id, "probe_id": probe_id}

    def accept(self, message):
        if (message.get("pc_id") != self.pc_id or not isinstance(message.get("probe_id"), str)
                or not 1 <= len(message["probe_id"]) <= 128):
            raise ValueError("Invalid RTT response identity")
        with self.lock:
            now = time.monotonic()
            self.expire(now)
            if self.pending is None or self.pending[0] != message["probe_id"]:
                return False  # Late, duplicated or previous-session response.
            self.finish(round(max(0, now - self.pending[1]) * 1000, 3), "ok", now)
            return True

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            self.expire(now)
            if self.status == "ok" and now - self.completed > self.interval + self.timeout:
                self.value, self.status = None, "stale"
            return {"rtt_ms": self.value, "rtt_status": self.status, "rtt_timestamp": self.timestamp}
