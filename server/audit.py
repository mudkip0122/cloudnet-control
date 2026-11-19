"""Server-only durable events and command replay protection (no credentials)."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4


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
            CREATE INDEX IF NOT EXISTS command_audit_pc_time ON command_audit(pc_id, received_epoch);
            CREATE INDEX IF NOT EXISTS exam_events_pc_time ON exam_events(pc_id, received_epoch);
            CREATE TABLE IF NOT EXISTS command_transitions (
                id INTEGER PRIMARY KEY, command_id TEXT NOT NULL, received_epoch REAL NOT NULL,
                stage TEXT NOT NULL, code TEXT);
            CREATE INDEX IF NOT EXISTS command_transitions_received ON command_transitions(received_epoch);
            CREATE INDEX IF NOT EXISTS command_transitions_command ON command_transitions(command_id, id);
            CREATE TABLE IF NOT EXISTS metric_history (id INTEGER PRIMARY KEY, pc_id TEXT NOT NULL, received_epoch REAL NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS metric_history_lookup ON metric_history(pc_id, received_epoch, id);
            CREATE TABLE IF NOT EXISTS monitoring_events (id INTEGER PRIMARY KEY, pc_id TEXT NOT NULL, received_epoch REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS monitoring_events_lookup ON monitoring_events(pc_id, received_epoch, id);
            CREATE TABLE IF NOT EXISTS devices (pc_id TEXT PRIMARY KEY, pc_name TEXT NOT NULL, last_seen_epoch REAL NOT NULL, online INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS active_alerts (pc_id TEXT NOT NULL, kind TEXT NOT NULL, alert_id TEXT NOT NULL, PRIMARY KEY(pc_id, kind));
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(command_audit)")}
        for name in ('reboot_confirmed_at', 'observed_boot_id'):
            if name not in columns:
                self.db.execute(f"ALTER TABLE command_audit ADD COLUMN {name} TEXT")
        self.db.commit()
        self.prune()

    def prune(self):
        # Never retain process or exam records indefinitely by default.
        cutoff = time.time() - 30 * 86400
        with self.lock, self.db:
            if time.monotonic() < self.next_prune:
                return
            self.db.execute("DELETE FROM exam_events WHERE received_epoch < ?", (cutoff,))
            self.db.execute("DELETE FROM command_audit WHERE received_epoch < ?", (cutoff,))
            self.db.execute("DELETE FROM command_transitions WHERE received_epoch < ?", (cutoff,))
            self.db.execute("DELETE FROM metric_history WHERE received_epoch < ?", (cutoff,))
            self.db.execute("DELETE FROM monitoring_events WHERE received_epoch < ?", (cutoff,))
            self.next_prune = time.monotonic() + 60

    def metric(self, pc_id, received_epoch, payload, events):
        self.prune()
        with self.lock, self.db:
            received_epoch = round(received_epoch * 1_000_000) / 1_000_000
            while self.db.execute("SELECT 1 FROM metric_history WHERE pc_id=? AND received_epoch=? LIMIT 1", (pc_id, received_epoch)).fetchone():
                received_epoch = round((received_epoch + .000001) * 1_000_000) / 1_000_000
            self.db.execute("INSERT INTO metric_history(pc_id, received_epoch, payload) VALUES (?, ?, ?)",
                            (pc_id, received_epoch, json.dumps(payload, allow_nan=False)))
            for event in events:
                self.db.execute("INSERT INTO monitoring_events(pc_id, received_epoch, kind, payload) VALUES (?, ?, ?, ?)",
                                (pc_id, received_epoch, event['kind'], json.dumps(event, allow_nan=False)))
                self._update_alert(pc_id, event)
            self.db.execute("UPDATE devices SET last_seen_epoch=? WHERE pc_id=?", (received_epoch, pc_id))
        return received_epoch

    def open_alerts(self):
        with self.lock:
            return self.db.execute("SELECT pc_id, kind, alert_id FROM active_alerts ORDER BY pc_id, kind").fetchall()

    def _update_alert(self, pc_id, event):
        if event['state'] == 'raised':
            self.db.execute("INSERT INTO active_alerts VALUES (?, ?, ?)", (pc_id, event['kind'], event['alert_id']))
        else:
            self.db.execute("DELETE FROM active_alerts WHERE pc_id=? AND kind=? AND alert_id=?", (pc_id, event['kind'], event['alert_id']))

    def _transition(self, pc_id, kind, state, alert_id, now):
        event = {'kind': kind, 'state': state, 'alert_id': alert_id}
        self.db.execute("INSERT INTO monitoring_events(pc_id, received_epoch, kind, payload) VALUES (?, ?, ?, ?)",
                        (pc_id, now, kind, json.dumps(event)))
        self._update_alert(pc_id, event)
        return event

    def recover_after_restart(self):
        # A previous process cannot still own an Agent session. Detection is now,
        # not a claim about the unknown time at which the previous process stopped.
        with self.lock, self.db:
            now = time.time()
            interrupted = self.db.execute("SELECT command_id FROM command_audit WHERE outcome IN ('accepted_result_unconfirmed', 'partial_result_received')").fetchall()
            for (command_id,) in interrupted:
                self._command_transition(command_id, 'server_restart_unconfirmed', None, now)
            self.db.execute("UPDATE command_audit SET outcome='server_restart_unconfirmed' WHERE outcome IN ('accepted_result_unconfirmed', 'partial_result_received')")
            for (pc_id,) in self.db.execute("SELECT pc_id FROM devices WHERE online=1").fetchall():
                self._transition(pc_id, 'DEVICE_OFFLINE', 'raised', str(uuid4()), now)
            self.db.execute("UPDATE devices SET online=0 WHERE online=1")
            return self.db.execute("SELECT pc_id, kind, alert_id FROM active_alerts").fetchall()

    def register_device(self, pc_id, pc_name, now, boot_id=None):
        self.prune()
        with self.lock, self.db:
            existing = self.db.execute("SELECT alert_id FROM active_alerts WHERE pc_id=? AND kind='DEVICE_OFFLINE'", (pc_id,)).fetchone()
            transition = None
            if existing:
                transition = self._transition(pc_id, 'DEVICE_OFFLINE', 'resolved', existing[0], now)
            self.db.execute("INSERT INTO devices VALUES (?, ?, ?, 1) ON CONFLICT(pc_id) DO UPDATE SET pc_name=excluded.pc_name, last_seen_epoch=excluded.last_seen_epoch, online=1", (pc_id, pc_name, now))
            if boot_id is not None:
                pending = self.db.execute("SELECT command_id, request, result, received_epoch FROM command_audit WHERE pc_id=? AND received_epoch>=? AND received_epoch<=? AND outcome IN ('partial_result_received', 'result_received', 'timeout_unconfirmed', 'server_restart_unconfirmed') AND reboot_confirmed_at IS NULL", (pc_id, now - 300, now)).fetchall()
                for command_id, raw_request, raw_result, requested_at in pending:
                    request = json.loads(raw_request)
                    result = json.loads(raw_result) if raw_result else {}
                    old_boot = request.get('target_boot_id')
                    if (request.get('action') == 'REBOOT_PC' and request.get('power_control_mode') == 'execute'
                            and isinstance(old_boot, str) and result.get('code') in
                            ('POWER_ACCEPTED', 'POWER_EXECUTION_STARTED', 'POWER_EXECUTION_UNKNOWN')):
                        old_time = datetime.fromisoformat(old_boot.replace('Z', '+00:00'))
                        new_time = datetime.fromisoformat(boot_id.replace('Z', '+00:00'))
                        if (old_time.tzinfo is not None and new_time.tzinfo is not None
                                and new_time > old_time and new_time.timestamp() >= requested_at - 1):
                            self.db.execute("UPDATE command_audit SET reboot_confirmed_at=?, observed_boot_id=? WHERE command_id=?", (datetime.fromtimestamp(now, timezone.utc).isoformat(), boot_id, command_id))
                            self._command_transition(command_id, 'boot_changed', None, now)
            return transition

    def disconnect_device(self, pc_id, now):
        self.prune()
        with self.lock, self.db:
            existing = self.db.execute("SELECT alert_id FROM active_alerts WHERE pc_id=? AND kind='DEVICE_OFFLINE'", (pc_id,)).fetchone()
            transition = None
            if not existing:
                transition = self._transition(pc_id, 'DEVICE_OFFLINE', 'raised', str(uuid4()), now)
            self.db.execute("UPDATE devices SET online=0, last_seen_epoch=? WHERE pc_id=?", (now, pc_id))
            return transition

    def monitoring_event(self, pc_id, kind, received_epoch, payload=None):
        self.prune()
        with self.lock, self.db:
            self.db.execute("INSERT INTO monitoring_events(pc_id, received_epoch, kind, payload) VALUES (?, ?, ?, ?)",
                            (pc_id, received_epoch, kind, json.dumps(payload or {'kind': kind})))

    def audit_history(self, pc_id, start, end, kind):
        with self.lock:
            if kind == "exam":
                rows = self.db.execute(
                    """SELECT payload, received_epoch FROM exam_events
                       WHERE pc_id=? AND received_epoch>=? AND received_epoch<?
                       ORDER BY received_epoch, event_id LIMIT 2001""", (pc_id, start, end)).fetchall()
                return [{"event": json.loads(payload),
                         "received_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat()}
                        for payload, epoch in rows]
            rows = self.db.execute(
                """SELECT command_id, received_at, request, result, outcome, result_received_at, reboot_confirmed_at
                   FROM command_audit WHERE pc_id=? AND received_epoch>=? AND received_epoch<?
                   AND json_extract(request, '$.action') IN ('SHUTDOWN_PC', 'REBOOT_PC')
                   ORDER BY received_epoch, command_id LIMIT 2001""", (pc_id, start, end)).fetchall()
            records = []
            for command_id, received, raw, result, outcome, result_at, confirmed in rows:
                request = json.loads(raw)
                stages = self.db.execute(
                    "SELECT stage, code, received_epoch FROM command_transitions WHERE command_id=? ORDER BY id",
                    (command_id,)).fetchall()
                records.append(dict(command_id=command_id, pc_id=pc_id, action=request["action"],
                    power_control_mode=request.get("power_control_mode"), requested_at=received,
                    outcome=outcome, result_code=json.loads(result).get("code") if result else None,
                    result_received_at=result_at, reboot_confirmed_at=confirmed,
                    stages=[dict(stage=stage, code=code, occurred_at=datetime.fromtimestamp(epoch, timezone.utc).isoformat())
                            for stage, code, epoch in stages]))
            return records

    def history_snapshot(self, pc_id, start, end):
        # Read both tables under one lock; cap work and reject instead of silently truncating.
        with self.lock:
            metrics = self.db.execute("SELECT received_epoch, payload FROM metric_history WHERE pc_id=? AND received_epoch>=? AND received_epoch<? ORDER BY received_epoch, id LIMIT 12801", (pc_id, start, end)).fetchall()
            events = self.db.execute("SELECT received_epoch, payload FROM monitoring_events WHERE pc_id=? AND received_epoch>=? AND received_epoch<? ORDER BY received_epoch, id LIMIT 12801", (pc_id, start, end)).fetchall()
        if len(metrics) + len(events) > 12800:
            raise ValueError('HISTORY_TOO_LARGE')
        return metrics, events

    def history(self, pc_id, start, end, kind, limit):
        table = {'metrics': 'metric_history', 'events': 'monitoring_events'}[kind]
        with self.lock:
            rows = self.db.execute(f"SELECT received_epoch, payload FROM {table} WHERE pc_id=? AND received_epoch>=? AND received_epoch<? ORDER BY received_epoch DESC, id DESC LIMIT ?",
                                   (pc_id, start, end, limit + 1)).fetchall()
        return [{'received_epoch': row[0], 'data': json.loads(row[1])} for row in rows[:limit]], len(rows) > limit

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
                  "forbidden_processes", "poll_interval", "targets", "pid", "create_time",
                  "power_control_mode", "target_boot_id")
        clean = {key: request[key] for key in fields if key in request}
        with self.lock, self.db:
            cursor = self.db.execute("""INSERT OR IGNORE INTO command_audit
                (command_id, pc_id, executor, received_at, received_epoch, request, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (request["command_id"], request["target_id"], executor, received_at, time.time(),
                 json.dumps(clean, allow_nan=False), "accepted_result_unconfirmed"))
            if cursor.rowcount == 1:
                self._command_transition(request['command_id'], 'request_accepted', None, time.time())
                return True
            return False

    def _command_transition(self, command_id, stage, code, received_epoch):
        self.db.execute("INSERT INTO command_transitions(command_id, received_epoch, stage, code) VALUES (?, ?, ?, ?)",
                        (command_id, received_epoch, stage, code))

    def result(self, command_id, message, complete=True):
        # Store a bounded result, excluding any unexpected Agent-supplied fields.
        fields = ("type", "pc_id", "command_id", "ok", "code", "reason", "exam_mode",
                  "all_terminated", "counts", "results", "page", "pages", "terminal")
        clean = {key: message[key] for key in fields if key in message}
        with self.lock, self.db:
            self.db.execute("UPDATE command_audit SET result=?, outcome=?, result_received_at=? WHERE command_id=?",
                (json.dumps(clean, allow_nan=False), "result_received" if complete else "partial_result_received",
                 datetime.now(timezone.utc).isoformat(), command_id))
            self._command_transition(command_id, 'result' if complete else 'partial_result', clean.get('code'), time.time())

    def unconfirmed(self, command_id, outcome):
        if outcome not in ('timeout_unconfirmed', 'delivery_unconfirmed'):
            raise ValueError('Invalid command outcome')
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE command_audit SET outcome=? WHERE command_id=? AND outcome IN ('accepted_result_unconfirmed', 'partial_result_received')", (outcome, command_id))
            if cursor.rowcount:
                self._command_transition(command_id, outcome, None, time.time())

    def close(self):
        with self.lock:
            self.db.close()
