"""Opt-in process-name observation for an exam; never stops applications."""
from datetime import datetime, timezone
import math
import re
import uuid

import psutil


class ExamMonitor:
    def __init__(self, pc_id, exam_id, forbidden_names, interval=1):
        if not isinstance(exam_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", exam_id):
            raise ValueError("--exam-id requires 1-64 letters, digits, _, . or -")
        names = set()
        for name in forbidden_names:
            if (not isinstance(name, str) or not name or name != name.strip()
                    or len(name) > 128 or any(c in name for c in '/\\:*?"<>|')
                    or any(ord(c) < 32 for c in name)):
                raise ValueError("Forbidden processes must be exact executable names, without paths or wildcards")
            names.add(name.casefold())
        if not 1 <= len(names) <= 100:
            raise ValueError("Specify 1-100 distinct --forbid-process names")
        if (isinstance(interval, bool) or not isinstance(interval, (int, float))
                or not math.isfinite(interval) or not .5 <= interval <= 60):
            raise ValueError("--exam-poll-interval must be between 0.5 and 60 seconds")
        self.pc_id = pc_id
        self.exam_id = exam_id
        self.names = names
        self.interval = interval
        self.seen = set()
        self.health = None

    def event(self, kind, **fields):
        return dict(type="EXAM_EVENT", version=1, event_id=str(uuid.uuid4()),
                    pc_id=self.pc_id, exam_id=self.exam_id, kind=kind,
                    timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    **fields)

    def scan(self):
        """Report each observed (PID, creation time) once while present.

        Keep identities for unreadable PIDs so temporary access failures do not
        create duplicate detections. A complete disappearance permits re-alerting.
        """
        current = set()
        unreadable = set()
        events = []
        scan_failed = False
        try:
            for process in psutil.process_iter():
                try:
                    name = process.name()
                    if not name:
                        unreadable.add(process.pid)
                        continue
                    if name.casefold() not in self.names:
                        continue
                    created = process.create_time()
                    if not isinstance(created, (int, float)) or not math.isfinite(created) or created <= 0:
                        unreadable.add(process.pid)
                        continue
                    identity = (process.pid, created)
                    current.add(identity)
                    if identity not in self.seen:
                        events.append(self.event("FORBIDDEN_PROCESS_DETECTED",
                                                 process={"pid": process.pid, "name": name[:128],
                                                          "create_time": created}))
                except psutil.NoSuchProcess:
                    continue
                except (psutil.Error, OSError):
                    unreadable.add(process.pid)
        except (psutil.Error, OSError):
            scan_failed = True
        self.seen = current | (self.seen if scan_failed else
                               {key for key in self.seen if key[0] in unreadable})
        health = ("unavailable" if scan_failed else "partial" if unreadable else "ok")
        if health != self.health:
            events.insert(0, self.event("MONITOR_STATUS", status=health,
                                        unreadable_count=len(unreadable),
                                        poll_interval=self.interval))
            self.health = health
        return events
