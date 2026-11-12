"""Server-only durable events and command replay protection (no credentials)."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
import time


class AuditStore:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.next_prune = 0
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS exam_events (
                event_id TEXT PRIMARY KEY, pc_id TEXT NOT NULL, exam_id TEXT NOT NULL,
                received_at TEXT NOT NULL, received_epoch REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS command_audit (
                command_id TEXT PRIMARY KEY, pc_id TEXT NOT NULL, executor TEXT NOT NULL,
                received_at TEXT NOT NULL, received_epoch REAL NOT NULL,
                request TEXT NOT NULL, result TEXT, outcome TEXT NOT NULL, result_received_at TEXT);
            CREATE INDEX IF NOT EXISTS exam_events_received ON exam_events(received_epoch);
            CREATE INDEX IF NOT EXISTS command_audit_received ON command_audit(received_epoch);
        """)
        self.prune()

    def prune(self):
        # Never retain process or exam records indefinitely by default.
        cutoff = time.time() - 30 * 86400
        with self.lock, self.db:
            if time.monotonic() < self.next_prune:
                return
            self.db.execute("DELETE FROM exam_events WHERE received_epoch < ?", (cutoff,))
            self.db.execute("DELETE FROM command_audit WHERE received_epoch < ?", (cutoff,))
            self.next_prune = time.monotonic() + 60

    def event(self, message, received_at):
        self.prune()
        with self.lock, self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO exam_events VALUES (?, ?, ?, ?, ?, ?)",
                (message["event_id"], message["pc_id"], message["exam_id"], received_at,
                 time.time(), json.dumps(message, allow_nan=False)))
            return cursor.rowcount == 1

    def reserve(self, request, executor, received_at):
        self.prune()
        fields = ("type", "command_id", "target_id", "action", "expires_at", "enabled", "exam_id",
                  "forbidden_processes", "poll_interval", "targets", "pid", "create_time")
        clean = {key: request[key] for key in fields if key in request}
        with self.lock, self.db:
            cursor = self.db.execute("""INSERT OR IGNORE INTO command_audit
                (command_id, pc_id, executor, received_at, received_epoch, request, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (request["command_id"], request["target_id"], executor, received_at, time.time(),
                 json.dumps(clean, allow_nan=False), "accepted_result_unconfirmed"))
            return cursor.rowcount == 1

    def result(self, command_id, message, complete=True):
        # Store a bounded result, excluding any unexpected Agent-supplied fields.
        fields = ("type", "pc_id", "command_id", "ok", "code", "reason", "exam_mode",
                  "all_terminated", "counts", "results", "page", "pages")
        clean = {key: message[key] for key in fields if key in message}
        with self.lock, self.db:
            self.db.execute("UPDATE command_audit SET result=?, outcome=?, result_received_at=? WHERE command_id=?",
                (json.dumps(clean, allow_nan=False), "result_received" if complete else "partial_result_received",
                 datetime.now(timezone.utc).isoformat(), command_id))

    def close(self):
        with self.lock:
            self.db.close()
