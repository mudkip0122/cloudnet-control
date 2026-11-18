"""Bounded, local-only handoff between Chrome's native host and an active exam."""
from contextlib import closing, contextmanager
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
            current = db.execute("SELECT host_until, dropped FROM tab_session WHERE session = ?", (session,)).fetchone()
            if current is None:
                return None
            events = db.execute("SELECT transition, observed FROM tab_events ORDER BY id").fetchall()
            db.execute("DELETE FROM tab_events")
            db.execute("UPDATE tab_session SET agent_until = ?, dropped = 0", (now + LEASE_SECONDS,))
            host_until, dropped = current
            return host_until is not None and host_until > now, dropped, events

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
                count = db.execute("SELECT COUNT(*) FROM tab_events").fetchone()[0]
                if count >= MAX_EVENTS:
                    db.execute("UPDATE tab_session SET dropped = dropped + 1")
                    return "monitoring"
                db.execute("INSERT INTO tab_events (transition, observed) VALUES (?, ?)", (transition, time.time()))
            return "monitoring"

    def detach(self, session, host):
        with self.transaction() as db:
            db.execute("UPDATE tab_session SET host = NULL, host_until = NULL WHERE session = ? AND host = ?",
                       (session, host))


class ChromeTabMonitor:
    def __init__(self, pc_id, store=None):
        self.pc_id = pc_id
        self.store = store or TabStore()
        self.exam = None
        self.session = None
        self.status = None
        self.next_poll = 0

    def poll(self, exam):
        if exam is not self.exam:
            self.stop()
            self.exam = exam
        now = time.monotonic()
        if exam is None or now < self.next_poll:
            return
        self.next_poll = now + 0.5
        try:
            if self.session is None:
                session = uuid4().hex
                if not self.store.start(session):
                    self.set_status("unavailable")
                    return
                self.session = session
            observation = self.store.poll(self.session)
            if observation is None:
                self.session = None
                self.set_status("unavailable")
                return
            attached, dropped, events = observation
            for transition, observed in events:
                if transition.startswith("camera_"):
                    LOG.info("Chrome exam camera %s: pc=%s exam=%s observed_epoch=%.3f",
                             transition.removeprefix("camera_"), self.pc_id, exam.exam_id, observed)
                else:
                    LOG.info("Chrome exam tab %s: pc=%s exam=%s observed_epoch=%.3f",
                             transition, self.pc_id, exam.exam_id, observed)
            if dropped:
                LOG.warning("Chrome tab events dropped: pc=%s exam=%s count=%s", self.pc_id, exam.exam_id, dropped)
            self.set_status("monitoring" if attached else "unavailable")
        except (OSError, sqlite3.Error):
            self.set_status("unavailable")

    def set_status(self, status):
        if status != self.status:
            self.status = status
            LOG.info("Chrome tab monitor %s: pc=%s exam=%s", status, self.pc_id, self.exam.exam_id)

    def stop(self):
        if self.session is not None:
            try:
                self.store.stop(self.session)
            except (OSError, sqlite3.Error):
                LOG.warning("Chrome tab monitor could not close local state; session will expire")
        self.exam = self.session = self.status = None
        self.next_poll = 0
