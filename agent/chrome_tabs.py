"""Local Chrome handoff and optional timeline events for an active exam."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

LOG = logging.getLogger("cloudnet.agent")
DEFAULT_DB = Path(__file__).resolve().parents[1] / "runtime" / "chrome-tabs.sqlite3"
LEASE_SECONDS = 8
MAX_EVENTS = 128
TRANSITIONS = ("left", "returned", "closed")
CAMERA_STATES = ("on", "permission_denied", "unavailable", "off")


class TabStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = path

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=0.25)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS tab_session (
                slot INTEGER PRIMARY KEY CHECK(slot = 1), session TEXT NOT NULL,
                agent_until REAL NOT NULL, host TEXT, host_until REAL, dropped INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS tab_events (
                id INTEGER PRIMARY KEY, transition TEXT NOT NULL, observed REAL NOT NULL)""")
            yield db

    def start(self, session):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            now = time.time()
            active = db.execute("SELECT 1 FROM tab_session WHERE agent_until > ?", (now,)).fetchone()
            if active:
                return False
            db.execute("DELETE FROM tab_events")
            db.execute("INSERT OR REPLACE INTO tab_session VALUES (1, ?, ?, NULL, NULL, 0)",
                       (session, now + LEASE_SECONDS))
            return True

    def poll(self, session):
        with self.transaction() as db:
            now = time.time()
            current = db.execute("SELECT host_until FROM tab_session WHERE session = ?", (session,)).fetchone()
            if current is None:
                return None
            host_until, = current
            attached = host_until is not None and host_until > now
            if host_until is not None and not attached:
                db.execute("UPDATE tab_session SET host = NULL, host_until = NULL")
                self.record(db, "monitor_detached", host_until)
            events = db.execute("SELECT transition, observed FROM tab_events ORDER BY id").fetchall()
            dropped, = db.execute("SELECT dropped FROM tab_session").fetchone()
            db.execute("DELETE FROM tab_events")
            db.execute("UPDATE tab_session SET agent_until = ?, dropped = 0", (now + LEASE_SECONDS,))
            return attached, dropped, events

    def stop(self, session):
        with self.transaction() as db:
            if db.execute("DELETE FROM tab_session WHERE session = ?", (session,)).rowcount:
                db.execute("DELETE FROM tab_events")

    def attach(self, host):
        if not self.path.is_file():
            return "exam_off", None
        with self.transaction() as db:
            now = time.time()
            current = db.execute("SELECT session, host, host_until FROM tab_session WHERE agent_until > ?",
                                 (now,)).fetchone()
            if current is None:
                return "exam_off", None
            session, current_host, host_until = current
            if current_host is not None and current_host != host and host_until > now:
                return "tab_already_selected", None
            if host_until is not None and host_until <= now:
                self.record(db, "monitor_detached", host_until)
            if host_until is None or host_until <= now:
                self.record(db, "monitor_attached", now)
            db.execute("UPDATE tab_session SET host = ?, host_until = ?", (host, now + LEASE_SECONDS))
            return "monitoring", session

    def report(self, session, host, transition=None):
        with self.transaction() as db:
            now = time.time()
            active = db.execute("""UPDATE tab_session SET host_until = ? WHERE session = ? AND host = ?
                                  AND agent_until > ? AND host_until > ?""",
                                (now + LEASE_SECONDS, session, host, now, now)).rowcount
            if not active:
                return "exam_off"
            if transition is not None:
                self.record(db, transition, now)
            return "monitoring"

    @staticmethod
    def record(db, transition, observed):
        count, = db.execute("SELECT COUNT(*) FROM tab_events").fetchone()
        if count >= MAX_EVENTS:
            db.execute("UPDATE tab_session SET dropped = dropped + 1")
        else:
            db.execute("INSERT INTO tab_events (transition, observed) VALUES (?, ?)", (transition, observed))

    def detach(self, session, host):
        with self.transaction() as db:
            now = time.time()
            current = db.execute("SELECT host_until FROM tab_session WHERE session = ? AND host = ?",
                                 (session, host)).fetchone()
            if current is not None:
                db.execute("UPDATE tab_session SET host = NULL, host_until = NULL")
                self.record(db, "monitor_detached", min(current[0], now))


class ChromeTabMonitor:
    def __init__(self, pc_id, store=None):
        self.pc_id = pc_id
        self.store = store or TabStore()
        self.exam = None
        self.session = None
        self.status = None
        self.next_poll = 0

    def poll(self, exam, send=None):
        if exam is not self.exam:
            self.stop()
            self.exam = exam
        now = time.monotonic()
        if exam is None or now < self.next_poll:
            return
        self.next_poll = now + 0.5
        observation = self.read_store()
        if observation is None:
            self.set_status("unavailable", "local_store_unavailable", send)
            return

        attached, dropped, events = observation
        if self.status is None and not events:
            self.set_status("unavailable", "awaiting_extension", send)
        for transition, observed in events:
            if transition == "monitor_attached":
                self.set_status("monitoring", "extension_connected", send, observed=observed)
            elif transition == "monitor_detached":
                self.set_status("unavailable", "extension_disconnected", send, observed=observed)
            elif transition.startswith("camera_"):
                status = transition.removeprefix("camera_")
                LOG.info("Chrome exam camera %s: pc=%s exam=%s observed_epoch=%.3f",
                         status, self.pc_id, exam.exam_id, observed)
                self.emit(send, "EXAM_CAMERA_STATUS", observed, status=status)
            elif transition in TRANSITIONS:
                LOG.info("Chrome exam tab %s: pc=%s exam=%s observed_epoch=%.3f",
                         transition, self.pc_id, exam.exam_id, observed)
                self.emit(send, "EXAM_TAB_CHANGED", observed, transition=transition)
        if dropped:
            LOG.warning("Chrome tab events dropped: pc=%s exam=%s count=%s", self.pc_id, exam.exam_id, dropped)
            self.set_status("unavailable", "event_buffer_overflow", send, dropped_count=dropped)
        if attached:
            self.set_status("monitoring", "extension_connected", send)
        else:
            self.set_status("unavailable", "extension_disconnected", send)

    def read_store(self):
        try:
            if self.session is None:
                session = uuid4().hex
                if not self.store.start(session):
                    return None
                self.session = session
            observation = self.store.poll(self.session)
            if observation is None:
                self.session = None
            return observation
        except (OSError, sqlite3.Error):
            return None

    def emit(self, send, kind, observed=None, **fields):
        if send is None:
            return
        message = self.exam.event(kind, **fields)
        if observed is not None:
            message["timestamp"] = datetime.fromtimestamp(observed, timezone.utc).isoformat().replace("+00:00", "Z")
        send(message)

    def set_status(self, status, reason, send, *, observed=None, dropped_count=0):
        if status != self.status or dropped_count:
            self.status = status
            LOG.info("Chrome tab monitor %s: pc=%s exam=%s", status, self.pc_id, self.exam.exam_id)
            fields = {"status": status, "reason": reason}
            if dropped_count:
                fields["dropped_count"] = dropped_count
            self.emit(send, "EXAM_TAB_MONITOR_STATUS", observed, **fields)

    def stop(self):
        if self.session is not None:
            try:
                self.store.stop(self.session)
            except (OSError, sqlite3.Error):
                LOG.warning("Chrome tab monitor could not close local state; session will expire")
        self.exam = self.session = self.status = None
        self.next_poll = 0
