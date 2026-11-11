"""Bounded, expiring process commands sent over an authenticated TLS session."""
from datetime import datetime, timezone
import logging
import time

from agent.processes import ProcessManager, validate_batch_targets

LOG = logging.getLogger("cloudnet.agent")


class ProcessCommands:
    def __init__(self, pc_id, enabled=False, protected_names=()):
        self.pc_id = pc_id
        self.enabled = enabled
        self.manager = ProcessManager(protected_names)
        self.seen = {}

    def handle(self, request):
        command_id = request.get("command_id")
        result = {"type": "COMMAND_RESULT", "pc_id": self.pc_id,
                  "command_id": command_id if isinstance(command_id, str) and len(command_id) <= 128 else None}

        def reject(code):
            return [result | {"ok": False, "code": code}]

        if not self.enabled:
            return reject("PROCESS_CONTROL_DISABLED")
        if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 128:
            return reject("INVALID_COMMAND_ID")
        if request.get("target_id") != self.pc_id:
            return reject("WRONG_TARGET")
        try:
            expiry = datetime.fromisoformat(request["expires_at"].replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                return reject("INVALID_EXPIRY")
            remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
            if not 0 < remaining <= 60:
                return reject("EXPIRED_OR_TOO_FAR")
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return reject("INVALID_EXPIRY")
        now = time.monotonic()
        self.seen = {key: until for key, until in self.seen.items() if until > now}
        if command_id in self.seen:
            return reject("DUPLICATE_COMMAND")
        if len(self.seen) >= 4096:
            return reject("COMMAND_CAPACITY")
        # Retain IDs across reconnects for the full maximum expiry window.
        self.seen[command_id] = now + 60
        action = request.get("action")
        if action == "LIST_PROCESSES":
            rows = self.manager.inventory()
            pages = [rows[i:i + 40] for i in range(0, len(rows), 40)] or [[]]
            return [result | {"ok": True, "code": "PROCESS_LIST", "page": i,
                              "pages": len(pages), "processes": page}
                    for i, page in enumerate(pages)]
        if action == "STOP_PROCESS":
            outcome = self.manager.stop(request.get("pid"), request.get("create_time"))
            LOG.info("Process stop result: pid=%s code=%s", request.get("pid")
                     if type(request.get("pid")) is int else "invalid", outcome["code"])
            return [result | outcome]
        if action == "STOP_PROCESSES":
            targets = request.get("targets")
            try:
                validate_batch_targets(targets)
            except ValueError:
                return reject("INVALID_TARGETS")
            outcomes = self.manager.stop_many(targets, expiry)
            counts = {}
            for outcome in outcomes:
                code = outcome["code"]
                counts[code] = counts.get(code, 0) + 1
                LOG.info("Batch process stop result: pid=%s code=%s", outcome["pid"], code)
            return [result | {"ok": True, "code": "PROCESS_BATCH_RESULT",
                              "all_terminated": all(row["code"] == "TERMINATED" for row in outcomes),
                              "results": outcomes, "counts": counts}]
        return reject("UNSUPPORTED_ACTION")
