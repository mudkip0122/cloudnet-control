"""Explicitly permitted remote exam controls, independent of process termination."""
from datetime import datetime, timezone
import threading
import time

from agent.exam import ExamMonitor

EXAM_ACTIONS = ("SET_EXAM_MODE", "GET_EXAM_STATUS")


class ExamCommands:
    def __init__(self, pc_id, enabled=False):
        self.pc_id = pc_id
        self.enabled = enabled
        self.lock = threading.RLock()
        self.seen = {}
        self.monitor = None
        self.last_exam_id = None
        self.next_scan = 0
        self.foreground_enabled = False

    def start_session(self, initial_monitor=None):
        # Remote enable is connection-scoped. Never silently resume an exam
        # after a monitoring gap; CLI startup mode is explicitly reapplied.
        with self.lock:
            self.monitor = initial_monitor
            self.last_exam_id = initial_monitor.exam_id if initial_monitor else None
            self.next_scan = 0

    def state(self):
        monitor = self.monitor
        return {"enabled": monitor is not None,
                "exam_id": monitor.exam_id if monitor else self.last_exam_id,
                "forbidden_processes": sorted(monitor.names) if monitor else [],
                "poll_interval": monitor.interval if monitor else None,
                "monitor_status": (monitor.health or "starting") if monitor else "off"}

    def handle(self, request):
        with self.lock:
            return self._handle(request)

    def _handle(self, request):
        command_id = request.get("command_id")
        result = {"type": "COMMAND_RESULT", "pc_id": self.pc_id,
                  "command_id": command_id if isinstance(command_id, str) and len(command_id) <= 128 else None}

        def reject(code):
            return [result | {"ok": False, "code": code}]

        if not self.enabled:
            return reject("EXAM_CONTROL_DISABLED")
        if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 128:
            return reject("INVALID_COMMAND_ID")
        if request.get("target_id") != self.pc_id:
            return reject("WRONG_TARGET")
        try:
            expiry = datetime.fromisoformat(request["expires_at"].replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                return reject("INVALID_EXPIRY")
            if not 0 < (expiry - datetime.now(timezone.utc)).total_seconds() <= 60:
                return reject("EXPIRED_OR_TOO_FAR")
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return reject("INVALID_EXPIRY")
        now = time.monotonic()
        self.seen = {key: until for key, until in self.seen.items() if until > now}
        if command_id in self.seen:
            return reject("DUPLICATE_COMMAND")
        if len(self.seen) >= 4096:
            return reject("COMMAND_CAPACITY")
        self.seen[command_id] = now + 60
        action = request.get("action")
        if action == "GET_EXAM_STATUS":
            return [result | {"ok": True, "code": "EXAM_STATUS", "exam_mode": self.state()}]
        if action != "SET_EXAM_MODE":
            return reject("UNSUPPORTED_ACTION")
        if type(request.get("enabled")) is not bool:
            return reject("INVALID_EXAM_SETTINGS")
        if request["enabled"]:
            names = request.get("forbidden_processes")
            if "forbidden_processes" in request and (not isinstance(names, list) or not 1 <= len(names) <= 100):
                return reject("INVALID_EXAM_SETTINGS")
            try:
                monitor = ExamMonitor(self.pc_id, request.get("exam_id"), names,
                                      request.get("poll_interval", 1),
                                      foreground_enabled=self.foreground_enabled)
            except (ValueError, TypeError):
                return reject("INVALID_EXAM_SETTINGS")
            # Validated completely before changing the running configuration.
            self.monitor = monitor
            self.last_exam_id = monitor.exam_id
            self.next_scan = 0
            code = "EXAM_MODE_ENABLED"
        else:
            if (not isinstance(request.get("exam_id"), str) or not self.last_exam_id
                    or request["exam_id"] != self.last_exam_id):
                return reject("EXAM_ID_MISMATCH")
            self.monitor = None
            code = "EXAM_MODE_DISABLED"
        return [result | {"ok": True, "code": code, "exam_mode": self.state()}]

    def poll(self, send):
        # Stream command handling holds this same lock through result sending.
        # Start result precedes detections; no old events follow a disable result.
        with self.lock:
            if self.monitor is None or time.monotonic() < self.next_scan:
                return
            for event in self.monitor.scan():
                send(event)
            self.next_scan = time.monotonic() + self.monitor.interval
