"""Monitoring server with optional TLS and per-client authentication."""
import argparse
from datetime import datetime, timezone
import math
import hmac
import logging
import queue
import socket
import ssl
import threading
import time
import sqlite3
import json
from uuid import uuid4
from common.protocol import MAX_MESSAGE_BYTES, recv_message, send_message
from common.security import load_auth, loopback
from server.audit import AuditStore
from server.contracts import command_error, command_result, exam_event, rtt_fields, text
from server.contracts import exam_id
from server.camera import camera_frame

LOG = logging.getLogger("cloudnet.server")
POWER_ACTIONS = ("SHUTDOWN_PC", "REBOOT_PC")


class Session:
    """Serialize complete frames, including unsolicited status notifications."""
    def __init__(self, conn):
        self.conn = conn
        self.send_lock = threading.Lock()
        self.notifications = queue.Queue(maxsize=32)
        self.stopped = threading.Event()
        self.writer = None
        self.camera_writer = None
        self.camera_frames = queue.Queue(maxsize=1)
        self.executor_id = str(uuid4())

    def send(self, message):
        with self.send_lock:
            send_message(self.conn, message)

    def close(self):
        self.stopped.set()
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def start_notifications(self):
        def write():
            while not self.stopped.is_set():
                try:
                    message = self.notifications.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    self.send(message)
                except (OSError, ValueError):
                    self.close()
        self.writer = threading.Thread(target=write, daemon=True)
        self.writer.start()

    def notify(self, message):
        try:
            self.notifications.put_nowait(message)
        except queue.Full:
            # A stalled administrator must not delay Agent registration or expiry.
            self.close()

    def start_camera(self, allowed):
        def write():
            while not self.stopped.is_set():
                try:
                    message, queued = self.camera_frames.get(timeout=.2)
                except queue.Empty:
                    continue
                if time.monotonic() - queued > 3 or not allowed(message):
                    continue
                try:
                    self.send(message)
                except (OSError, ValueError):
                    self.close()
        self.camera_writer = threading.Thread(target=write, daemon=True)
        self.camera_writer.start()

    def notify_camera(self, message):
        try:
            self.camera_frames.put_nowait((message, time.monotonic()))
        except queue.Full:
            try:
                self.camera_frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.camera_frames.put_nowait((message, time.monotonic()))
            except queue.Full:
                pass


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MonitoringServer:
    def __init__(self, auth=None, heartbeat_timeout=15, audit=None):
        if not math.isfinite(heartbeat_timeout) or heartbeat_timeout <= 0:
            raise ValueError("Heartbeat timeout must be positive and finite")
        self.auth = auth
        self.audit = audit
        self.heartbeat_timeout = heartbeat_timeout
        self.lock = threading.Lock()
        self.devices = {}
        self.sessions = {}
        self.admins = set()
        self.exam_admins = set()
        self.alert_admins = set()
        self.camera_admins = {}
        self.camera_status = {}
        self.camera_last_frame = {}
        self.connections = {}
        self.pending_commands = {}
        self.command_sweeper = None
        self.seen_commands = {}
        self.metrics_received_at = {}
        self.exam_states = {}
        self.event_rates = {}
        self.rtt_received_at = {}
        self.alert_states = {}
        if self.audit is not None:
            for pc_id, kind, alert_id in self.audit.recover_after_restart():
                field = {"CPU_HIGH": "cpu_percent", "RAM_HIGH": "ram_percent"}.get(kind)
                if field:
                    self.alert_states.setdefault(pc_id, {})[field] = {
                        "since": None, "last": None, "active": True, "alert_id": alert_id}

    def device_snapshot(self, pc_id):
        """Called under lock; freshness uses server time, never Agent clock."""
        device = dict(self.devices[pc_id])
        device["server_timestamp"] = utc_now()
        received = self.metrics_received_at.get(pc_id)
        now = time.monotonic()
        device["metrics_age_seconds"] = None if received is None else round(max(0, now - received), 3)
        device["metrics_stale"] = (not device["online"] or received is None
                                   or now - received > 6)
        mode = self.exam_states.get(pc_id)
        device["exam_mode"] = None if mode is None else dict(mode)
        rtt = self.rtt_received_at.get(pc_id)
        device["last_rtt_received"] = None if rtt is None else rtt[2]
        device["rtt_age_seconds"] = None if rtt is None else round(max(0, now - rtt[1]), 3)
        device["rtt_stale"] = (device["metrics_stale"] or rtt is None or now - rtt[1] > 8
                               or (device.get("metrics") or {}).get("rtt_status") != "ok")
        return device

    def notify_admin(self, conn, message):
        with self.lock:
            session = self.connections.get(conn)
        if session is None:
            raise OSError("Administrator disconnected")
        # Never wait for an administrator's socket on an Agent reader thread.
        session.notify(message)

    def send(self, conn, message):
        with self.lock:
            session = self.connections.get(conn)
        if session is None:
            raise OSError("Session closed")
        session.send(message)

    def notify_status(self, pc_id):
        with self.lock:
            timestamp = utc_now()
            message = {"type": "STATUS_UPDATE", "device": self.device_snapshot(pc_id),
                       "timestamp": timestamp, "server_timestamp": timestamp}
            admins = tuple(self.admins)
        for admin in admins:
            admin.notify(message)

    def watch_activity(self, session, activity, stopped):
        # Whole valid frames must arrive before the deadline; partial bytes do not
        # extend it, even if recv() never reaches its individual socket timeout.
        while not stopped.wait(min(0.2, self.heartbeat_timeout / 4)):
            if time.monotonic() - activity[0] >= self.heartbeat_timeout:
                LOG.info("Session timed out")
                session.close()
                return

    def snapshot(self):
        with self.lock:
            return {"type": "DEVICE_LIST", "devices": [self.device_snapshot(pc_id) for pc_id in self.devices]}

    def alert_snapshot_locked(self):
        rows = self.audit.open_alerts()
        if len(rows) > 600:
            raise ValueError('Too many active alerts for snapshot')
        now = time.monotonic()
        result = []
        for pc_id, kind, alert_id in rows:
            device = self.devices.get(pc_id, {})
            field = {'CPU_HIGH': 'cpu_percent', 'RAM_HIGH': 'ram_percent'}.get(kind)
            received = self.metrics_received_at.get(pc_id)
            known = (field is None or (device.get('online') and received is not None and now - received <= 6
                                      and (device.get('metrics') or {}).get(field) is not None))
            result.append({'pc_id': pc_id, 'kind': kind, 'alert_id': alert_id,
                           'state': 'raised', 'observation': 'known' if known else 'unknown'})
        return result

    def alert_event_locked(self, pc_id, event, epoch):
        if event is None:
            return
        message = {'type': 'ALERT_EVENT', 'version': 1, 'pc_id': pc_id, **event,
                   'occurred_at': datetime.fromtimestamp(epoch, timezone.utc).isoformat()}
        for admin in self.alert_admins:
            admin.notify(message)

    def sweep_commands(self):
        while True:
            time.sleep(.2)
            expired = []
            with self.lock:
                now = time.monotonic()
                for command_id, pending in list(self.pending_commands.items()):
                    if pending['until'] > now:
                        continue
                    del self.pending_commands[command_id]
                    if self.audit is not None:
                        try:
                            self.audit.unconfirmed(command_id, 'timeout_unconfirmed')
                        except sqlite3.Error:
                            LOG.error('Could not record command timeout')
                    session = self.connections.get(pending['admin'])
                    if session is not None:
                        expired.append((session, {'type': 'COMMAND_RESULT', 'pc_id': pending['target'],
                                                 'command_id': command_id, 'ok': False, 'code': 'COMMAND_TIMEOUT'}))
                if not self.pending_commands:
                    self.command_sweeper = None
                    done = True
                else:
                    done = False
            for session, result in expired:
                session.notify(result)
            if done:
                return

    def handle(self, conn):
        pc_id = None
        session = Session(conn)
        with self.lock:
            self.connections[conn] = session
        stopped = threading.Event()
        activity = [time.monotonic()]
        watcher = None
        try:
            conn.settimeout(5)
            message = recv_message(conn)
            if message["type"] != "REGISTER":
                raise ValueError("First message must be REGISTER")
            role = message.get("role")
            if role not in ("agent", "admin"):
                raise ValueError("Role must be agent or admin")
            subscribe = message.get("subscribe_status", False)
            if not isinstance(subscribe, bool) or (subscribe and role != "admin"):
                raise ValueError("subscribe_status requires an admin and a boolean")
            subscribe_exam = message.get("subscribe_exam", False)
            if not isinstance(subscribe_exam, bool) or (subscribe_exam and role != "admin"):
                raise ValueError("subscribe_exam requires an admin and a boolean")
            exam_supported = self.audit is not None and self.auth is not None and isinstance(conn, ssl.SSLSocket)
            if subscribe_exam and not exam_supported:
                raise ValueError("Exam subscription requires TLS, authentication and audit storage")
            subscribe_alerts = message.get('subscribe_alerts', False)
            if type(subscribe_alerts) is not bool or (subscribe_alerts and (role != 'admin' or not exam_supported)):
                raise ValueError('Alert subscription requires authenticated TLS admin and audit storage')
            camera_scope = message.get("subscribe_camera")
            if camera_scope is not None and (role != 'admin' or not exam_supported or
                    not isinstance(camera_scope, dict) or set(camera_scope) != {'pc_id', 'exam_id'} or
                    not text(camera_scope.get('pc_id')) or not exam_id(camera_scope.get('exam_id')) or
                    camera_scope['pc_id'] not in self.auth['agents']):
                raise ValueError("Camera subscription requires authorized TLS admin and PC/exam scope")
            if self.auth is not None:
                candidate_id = message.get("pc_id")
                expected = (self.auth["admin_token"] if role == "admin" else
                            self.auth["agents"].get(candidate_id) if isinstance(candidate_id, str) else None)
                token = message.get("token")
                if (not expected or not isinstance(token, str) or not token.isascii()
                        or not hmac.compare_digest(token, expected)):
                    raise ValueError("Authentication failed")
            if role == "agent":
                for field in ("pc_id", "pc_name"):
                    value = message.get(field)
                    if not isinstance(value, str) or not value.strip() or len(value) > 128:
                        raise ValueError(field + " must be a nonempty string of at most 128 characters")
                candidate = message["pc_id"]
                power_mode, boot_id = message.get("power_control_mode"), message.get("boot_id")
                if power_mode not in (None, "simulate", "execute"):
                    raise ValueError("Invalid power control mode")
                if boot_id is not None:
                    if not text(boot_id):
                        raise ValueError("Invalid boot ID")
                    parsed_boot = datetime.fromisoformat(boot_id.replace("Z", "+00:00"))
                    if parsed_boot.tzinfo is None:
                        raise ValueError("Boot ID requires a timezone")
                if power_mode == "execute" and boot_id is None:
                    raise ValueError("Execute mode requires boot ID")
                with self.lock:
                    if candidate in self.sessions:
                        raise ValueError("PC ID is already connected")
                    if self.audit is not None:
                        registered_at = time.time()
                        alert = self.audit.register_device(candidate, message["pc_name"], registered_at, boot_id)
                        self.alert_event_locked(candidate, alert, registered_at)
                    pc_id = candidate
                    self.sessions[pc_id] = conn
                    self.metrics_received_at.pop(pc_id, None)
                    self.exam_states.pop(pc_id, None)
                    self.event_rates.pop(pc_id, None)
                    self.rtt_received_at.pop(pc_id, None)
                    self.camera_status.pop(pc_id, None)
                    self.camera_last_frame.pop(pc_id, None)
                    for state in self.alert_states.get(pc_id, {}).values():
                        state.update(since=None, last=None)
                    self.devices[pc_id] = {"pc_id": pc_id, "pc_name": message["pc_name"],
                                           "online": True, "last_received": utc_now(),
                                           "last_metrics_received": None, "metrics": None,
                                           "agent_session_id": session.executor_id,
                                           "exam_state_received_at": None,
                                           "boot_id": boot_id, "power_control_mode": power_mode}
            response = {"type": "REGISTER_RESULT", "ok": True}
            if pc_id is not None:
                response["pc_id"] = pc_id
                response.update(heartbeat_interval=min(5, self.heartbeat_timeout / 3), rtt_v1=True)
            if exam_supported:
                response.update(exam_events_v1=True, exam_control_v1=True, exam_foreground_v1=True,
                                exam_chrome_timeline_v1=True, history_v1=True, power_control_v1=True,
                                exam_camera_stream_v1=True, alert_stream_v1=True)
            if exam_supported and role == "admin":
                response["audit_history_v1"] = True
            session.send(response)
            if role == "admin":
                session.start_notifications()
            if role == "admin" and subscribe:
                with self.lock:
                    self.admins.add(session)
            if role == "admin" and subscribe_exam:
                with self.lock:
                    self.exam_admins.add(session)
            if subscribe_alerts:
                with self.lock:
                    opened = self.alert_snapshot_locked()
                    for offset in range(0, max(1, len(opened)), 20):
                        session.notify({'type': 'ALERT_SNAPSHOT', 'version': 1, 'page': offset // 20,
                                        'done': offset + 20 >= len(opened), 'alerts': opened[offset:offset + 20]})
                    self.alert_admins.add(session)
            if camera_scope is not None:
                with self.lock:
                    self.camera_admins[session] = (camera_scope['pc_id'], camera_scope['exam_id'])
                session.start_camera(lambda frame: self.camera_allowed(session, frame))
            LOG.info("Registered role=%s pc_id=%s", role, pc_id)
            activity[0] = time.monotonic()
            watcher = threading.Thread(target=self.watch_activity,
                                       args=(session, activity, stopped), daemon=True)
            watcher.start()
            if pc_id is not None:
                self.notify_status(pc_id)
            conn.settimeout(self.heartbeat_timeout)
            while True:
                message = recv_message(conn)
                if role == "admin" and message["type"] == "LIST_DEVICES":
                    session.send(self.snapshot())
                elif role == "admin" and message["type"] == "GET_HISTORY":
                    if message.get("version") == 1 and type(message.get("version")) is int:
                        self.history_v1(session, message)
                    else:
                        self.history(session, message)
                elif role == "admin" and message["type"] == "GET_AUDIT_HISTORY":
                    self.audit_history(session, message, exam_supported)
                elif role == "admin" and message["type"] == "COMMAND":
                    self.route_command(conn, message)
                elif role == "agent" and message["type"] == "COMMAND_RESULT":
                    self.route_result(conn, pc_id, message)
                elif role == "agent" and message["type"] == "METRICS":
                    self.update_metrics(pc_id, message)
                elif role == "agent" and message["type"] == "RTT_PING":
                    if message.get("pc_id") != pc_id or not text(message.get("probe_id")):
                        raise ValueError("Invalid RTT probe identity")
                    session.send({"type": "RTT_PONG", "pc_id": pc_id, "probe_id": message["probe_id"]})
                elif role == "agent" and message["type"] == "EXAM_EVENT":
                    self.receive_exam_event(pc_id, message)
                elif role == "agent" and message["type"] == "EXAM_CAMERA_FRAME":
                    if not exam_supported:
                        raise ValueError('Camera frames require authenticated TLS')
                    self.receive_camera_frame(pc_id, message)
                elif role == "agent" and message["type"] == "HEARTBEAT":
                    if message.get("pc_id") != pc_id:
                        raise ValueError("PC ID must match the registered session")
                    with self.lock:
                        self.devices[pc_id]["last_received"] = utc_now()
                else:
                    raise ValueError("Message is not allowed for this role")
                activity[0] = time.monotonic()
        except (ValueError, UnicodeError) as exc:
            try:
                LOG.warning("Rejected invalid message (%s)", type(exc).__name__)
                session.send({"type": "ERROR", "code": "INVALID_MESSAGE", "message": str(exc)})
            except OSError:
                pass
        except sqlite3.Error:
            LOG.error("Audit storage unavailable; closing session without retrying a command")
        except (OSError, EOFError):
            pass
        finally:
            stopped.set()
            session.close()
            with self.lock:
                self.admins.discard(session)
                self.exam_admins.discard(session)
                self.alert_admins.discard(session)
                self.camera_admins.pop(session, None)
                self.connections.pop(conn, None)
                # Retain accepted requests until result/expiry even if their GUI
                # closes: audit and other supervisors still need the outcome.
            if pc_id is not None:
                disconnected = False
                with self.lock:
                    if self.sessions.get(pc_id) is conn:
                        del self.sessions[pc_id]
                        self.devices[pc_id]["online"] = False
                        self.exam_states.pop(pc_id, None)
                        self.camera_status.pop(pc_id, None)
                        self.camera_last_frame.pop(pc_id, None)
                        self.devices[pc_id]["exam_state_received_at"] = None
                        for state in self.alert_states.get(pc_id, {}).values():
                            state.update(since=None, last=None)
                        if self.audit is not None:
                            try:
                                disconnected_at = time.time()
                                alert = self.audit.disconnect_device(pc_id, disconnected_at)
                                self.alert_event_locked(pc_id, alert, disconnected_at)
                            except sqlite3.Error:
                                LOG.error("Could not persist offline event")
                        disconnected = True
                if disconnected:
                    LOG.info("Disconnected pc_id=%s", pc_id)
                    self.notify_status(pc_id)
            if watcher is not None:
                watcher.join(timeout=1)
            if session.writer is not None:
                session.writer.join(timeout=1)
            if session.camera_writer is not None:
                session.camera_writer.join(timeout=1)

    def route_command(self, admin, request):
        command_id, target = request.get("command_id"), request.get("target_id")
        if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 128:
            raise ValueError("Invalid command ID")
        if not isinstance(target, str) or not target:
            raise ValueError("Invalid target ID")
        error = None
        if not isinstance(admin, ssl.SSLSocket) or self.auth is None:
            error = "TLS_REQUIRED"
        elif request.get("action") not in ("LIST_PROCESSES", "STOP_PROCESS", "STOP_PROCESSES",
                                           "SET_EXAM_MODE", "GET_EXAM_STATUS", *POWER_ACTIONS):
            error = "UNSUPPORTED_ACTION"
        elif any(key in request for key in ('shell', 'command', 'cmd', 'args')):
            error = "INVALID_COMMAND_FIELDS"
        error = error or command_error(request)
        try:
            expires = datetime.fromisoformat(request["expires_at"].replace("Z", "+00:00"))
            remaining = (expires - datetime.now(timezone.utc)).total_seconds()
            if not 0 < remaining <= 60:
                error = error or "EXPIRED_OR_TOO_FAR"
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            error = error or "INVALID_EXPIRY"
            remaining = 0
        now = time.monotonic()
        with self.lock:
            self.seen_commands = {key: until for key, until in self.seen_commands.items() if until > now}
            agent = self.sessions.get(target)
            if agent is None:
                error = error or "TARGET_OFFLINE"
            elif not isinstance(agent, ssl.SSLSocket) or self.auth is None:
                error = error or "TLS_REQUIRED"
            if request.get("action") in POWER_ACTIONS:
                device = self.devices.get(target, {})
                mode = device.get("power_control_mode")
                if self.audit is None:
                    error = error or "POWER_STATE_UNAVAILABLE"
                elif mode not in ("simulate", "execute"):
                    error = error or "POWER_CONTROL_DISABLED"
                elif request.get("power_control_mode") != mode:
                    error = error or "POWER_MODE_MISMATCH"
                elif "target_boot_id" not in request or request["target_boot_id"] != device.get("boot_id"):
                    error = error or "BOOT_ID_MISMATCH"
            if command_id in self.seen_commands:
                error = error or "DUPLICATE_COMMAND"
            if len(self.seen_commands) >= 4096:
                error = error or "COMMAND_CAPACITY"
            if error is None and self.audit is not None:
                executor = self.connections.get(admin)
                if not self.audit.reserve(request, executor.executor_id if executor else "unknown", utc_now()):
                    error = "DUPLICATE_COMMAND"
            if error is None:
                self.seen_commands[command_id] = now + 60
                self.pending_commands[command_id] = {"admin": admin, "target": target, "agent": agent,
                    "until": now + remaining, "pages": None, "received": set(), "action": request["action"],
                    "request": request, "power_accepted": False}
                if self.command_sweeper is None:
                    self.command_sweeper = threading.Thread(target=self.sweep_commands, daemon=True)
                    self.command_sweeper.start()
        if error is not None:
            self.send(admin, {"type": "COMMAND_RESULT", "pc_id": target, "command_id": command_id,
                              "ok": False, "code": error})
            return
        try:
            self.send(agent, request)
        except OSError:
            with self.lock:
                self.pending_commands.pop(command_id, None)
                if self.audit is not None:
                    try:
                        self.audit.unconfirmed(command_id, 'delivery_unconfirmed')
                    except sqlite3.Error:
                        LOG.error('Could not record uncertain command delivery')
            self.send(admin, {"type": "COMMAND_RESULT", "pc_id": target, "command_id": command_id,
                              "ok": False, "code": "TARGET_OFFLINE"})

    def route_result(self, agent, pc_id, message):
        command_id = message.get("command_id")
        if not isinstance(command_id, str) or message.get("pc_id") != pc_id:
            raise ValueError("Command result identity mismatch")
        mode_changed = False
        with self.lock:
            pending = self.pending_commands.get(command_id)
            # Late responses belong to an expired or disconnected request.
            if pending is None:
                return
            if pending["target"] != pc_id or pending["agent"] is not agent:
                raise ValueError("Command result session mismatch")
            if pending["until"] <= time.monotonic():
                return
            command_result(pending["request"], message)
            finished = True
            if pending["action"] in POWER_ACTIONS:
                code, ok, terminal = message.get("code"), message.get("ok"), message.get("terminal")
                if type(terminal) is not bool:
                    raise ValueError("Invalid power result terminal flag")
                if code == "POWER_ACCEPTED":
                    if not ok or terminal or pending["power_accepted"]:
                        raise ValueError("Invalid power acceptance")
                    pending["power_accepted"] = True
                    pending["until"] = max(pending["until"], time.monotonic() + 5)
                    finished = False
                else:
                    expected_success = ("POWER_SIMULATED" if pending["request"]["power_control_mode"] == "simulate"
                                        else "POWER_EXECUTION_STARTED")
                    failure_codes = ("POWER_EXECUTION_UNKNOWN", "POWER_EXECUTION_FAILED", "POWER_CONTROL_DISABLED",
                        "POWER_UNAVAILABLE", "POWER_MODE_MISMATCH", "BOOT_ID_MISMATCH", "POWER_STATE_UNAVAILABLE",
                        "INVALID_COMMAND_ID", "WRONG_TARGET", "INVALID_EXPIRY", "EXPIRED_OR_TOO_FAR",
                        "DUPLICATE_COMMAND", "COMMAND_CAPACITY", "UNSUPPORTED_ACTION")
                    if not terminal or (ok and (code != expected_success or not pending["power_accepted"])) or (not ok and code not in failure_codes):
                        raise ValueError("Invalid final power result")
            if pending["action"] == "LIST_PROCESSES" and message.get("ok") is True:
                page, pages = message.get("page"), message.get("pages")
                if (message.get("code") != "PROCESS_LIST" or type(page) is not int or type(pages) is not int
                        or not 1 <= pages <= 4096 or not 0 <= page < pages
                        or (pending["pages"] is not None and pages != pending["pages"])
                        or page in pending["received"]):
                    raise ValueError("Invalid process result pages")
                pending["pages"] = pages
                pending["received"].add(page)
                finished = len(pending["received"]) == pages
            admin = pending["admin"]
            if self.audit is not None:
                self.audit.result(command_id, message, complete=finished)
            if (pending["action"] in ("SET_EXAM_MODE", "GET_EXAM_STATUS")
                    and message.get("ok") is True and isinstance(message.get("exam_mode"), dict)):
                self.exam_states[pc_id] = dict(message["exam_mode"])
                if not message["exam_mode"].get("enabled"):
                    self.camera_status.pop(pc_id, None)
                if pc_id in self.devices:
                    self.devices[pc_id]["exam_state_received_at"] = utc_now()
                    mode_changed = True
            if finished:
                del self.pending_commands[command_id]
        try:
            self.notify_admin(admin, message)
        except OSError:
            # A closed admin must not disconnect the Agent.
            pass
        if mode_changed:
            self.notify_status(pc_id)

    def receive_exam_event(self, pc_id, message):
        clean = exam_event(message, pc_id)
        if self.audit is None:
            raise ValueError("Exam event storage is not configured")
        now = time.monotonic()
        with self.lock:
            started, count = self.event_rates.get(pc_id, (now, 0))
            if now - started >= 1:
                started, count = now, 0
            if count >= 200:
                raise ValueError("Exam event rate limit exceeded")
            self.event_rates[pc_id] = (started, count + 1)
        received = utc_now()
        if not self.audit.event(clean, received):
            return False
        mode_changed = False
        with self.lock:
            mode = self.exam_states.get(pc_id)
            if clean['kind'] == 'EXAM_CAMERA_STATUS':
                self.camera_status[pc_id] = (clean['exam_id'], clean['status'])
            if (clean["kind"] == "MONITOR_STATUS" and mode is not None
                    and mode.get("enabled") and mode.get("exam_id") == clean["exam_id"]):
                self.exam_states[pc_id] = dict(mode, monitor_status=clean["status"])
                self.devices[pc_id]["exam_state_received_at"] = received
                mode_changed = True
            admins = tuple(self.exam_admins)
        for admin in admins:
            admin.notify(clean)
        if mode_changed:
            self.notify_status(pc_id)
        return True

    def camera_allowed(self, session, frame):
        with self.lock:
            pc_id, exam = frame['pc_id'], frame['exam_id']
            mode = self.exam_states.get(pc_id) or {}
            return (not session.stopped.is_set() and self.camera_admins.get(session) == (pc_id, exam)
                    and pc_id in self.sessions and mode.get('enabled') is True and mode.get('exam_id') == exam
                    and self.camera_status.get(pc_id) == (exam, 'on'))

    def receive_camera_frame(self, pc_id, message):
        clean = camera_frame(message, pc_id)
        now = time.monotonic()
        with self.lock:
            mode = self.exam_states.get(pc_id) or {}
            if (not mode.get('enabled') or mode.get('exam_id') != clean['exam_id']
                    or self.camera_status.get(pc_id) != (clean['exam_id'], 'on')):
                return
            if now - self.camera_last_frame.get(pc_id, -1e9) < .45:
                return
            self.camera_last_frame[pc_id] = now
            viewers = tuple(session for session, scope in self.camera_admins.items()
                            if scope == (pc_id, clean['exam_id']))
        for viewer in viewers:
            viewer.notify_camera(clean)

    def update_metrics(self, pc_id, message):
        if message.get("pc_id") != pc_id:
            raise ValueError("PC ID must match the registered session")
        timestamp = message.get("timestamp")
        if not isinstance(timestamp, str):
            raise ValueError("Timestamp is required")
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timestamp requires a timezone")
        clean = {"type": "METRICS", "pc_id": pc_id, "timestamp": timestamp}
        clean.update(rtt_fields(message))
        for field in ("cpu_percent", "ram_percent", "upload_mbps", "download_mbps"):
            if field not in message:
                raise ValueError("Missing metric: " + field)
            value = message[field]
            if value is not None:
                if (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
                        or (isinstance(value, float) and not math.isfinite(value))):
                    raise ValueError("Invalid metric: " + field)
                if value > 1.7976931348623157e308:
                    raise ValueError("Metric exceeds finite numeric range: " + field)
                if field.endswith("percent") and value > 100:
                    raise ValueError("Percent must be between 0 and 100")
            clean[field] = value
        # Only consecutive fresh samples establish a sustained threshold.
        now = time.monotonic()
        with self.lock:
            states = self.alert_states.setdefault(pc_id, {})
            updated = {key: dict(value) for key, value in states.items()}
            events = []
            for field in ("cpu_percent", "ram_percent"):
                value = clean[field]
                state = updated.setdefault(field, {"since": None, "last": None, "active": False})
                if value is None or (state["last"] is not None and now - state["last"] > 6):
                    state["since"] = None
                if value is not None and value >= 90:
                    if state["since"] is None:
                        state["since"] = now
                    if not state["active"] and now - state["since"] >= 10:
                        state["active"] = True
                        state["alert_id"] = str(uuid4())
                        events.append({"kind": "CPU_HIGH" if field == "cpu_percent" else "RAM_HIGH",
                                       "state": "raised", "alert_id": state["alert_id"]})
                elif value is not None:
                    state["since"] = None
                    if state["active"] and value < 85:
                        state["active"] = False
                        events.append({"kind": "CPU_HIGH" if field == "cpu_percent" else "RAM_HIGH",
                                       "state": "resolved", "alert_id": state.pop("alert_id")})
                state["last"] = now if value is not None else None
            if self.audit is not None:
                received_epoch = self.audit.metric(pc_id, time.time(), clean, events)
                for event in events:
                    self.alert_event_locked(pc_id, event, received_epoch)
            self.alert_states[pc_id] = updated
        with self.lock:
            received = utc_now()
            self.devices[pc_id].update(metrics=clean, last_received=received,
                                       last_metrics_received=received)
            self.metrics_received_at[pc_id] = time.monotonic()
            rtt_timestamp = clean.get("rtt_timestamp")
            previous_rtt = self.rtt_received_at.get(pc_id)
            if rtt_timestamp is None:
                self.rtt_received_at.pop(pc_id, None)
            elif previous_rtt is None or previous_rtt[0] != rtt_timestamp:
                self.rtt_received_at[pc_id] = (rtt_timestamp, time.monotonic(), received)

    def history(self, session, request):
        pc_id, kind = request.get("pc_id"), request.get("kind")
        start, end, limit = request.get("start_epoch"), request.get("end_epoch"), request.get("limit", 100)
        valid_time = lambda value: (type(value) in (int, float) and 0 <= value <= 253402300799
                                    and math.isfinite(value))
        if (not isinstance(pc_id, str) or not pc_id.strip() or len(pc_id) > 128
                or kind not in ("metrics", "events") or not valid_time(start) or not valid_time(end)
                or start >= end or end - start > 30 * 86400 or type(limit) is not int or not 1 <= limit <= 100):
            session.send({"type": "HISTORY_ERROR", "code": "INVALID_REQUEST"})
            return
        if self.audit is None:
            session.send({"type": "HISTORY_ERROR", "code": "STORAGE_UNAVAILABLE"})
            return
        try:
            rows, truncated = self.audit.history(pc_id, start, end, kind, limit)
        except sqlite3.Error:
            LOG.error("History query failed")
            session.send({"type": "HISTORY_ERROR", "code": "STORAGE_UNAVAILABLE"})
            return
        session.send({"type": "HISTORY_RESULT", "pc_id": pc_id, "kind": kind, "rows": rows, "truncated": truncated})

    def audit_history(self, session, request, authorized):
        request_id, pc_id, kind = (request.get(key) for key in ("request_id", "pc_id", "kind"))
        base = dict(type="AUDIT_HISTORY_RESULT", version=1,
                    request_id=request_id if text(request_id) else None,
                    pc_id=pc_id if text(pc_id) else None, kind=kind if kind in ("exam", "power") else None)
        error = "INVALID_REQUEST"
        if not authorized:
            error = "TLS_REQUIRED"
        elif (type(request.get("version")) is int and request["version"] == 1
              and all(base[key] is not None for key in ("pc_id", "request_id", "kind"))):
            try:
                bounds = [datetime.fromisoformat(request[key].replace("Z", "+00:00"))
                          for key in ("start_at", "end_at")]
                if any(bound.tzinfo is None for bound in bounds):
                    raise ValueError("Timezone required")
                start, end = (bound.timestamp() for bound in bounds)
                if not 0 < end - start <= 30 * 86400:
                    raise ValueError("Invalid interval")
            except (KeyError, AttributeError, ValueError, OverflowError, OSError):
                pass
            else:
                try:
                    records = self.audit.audit_history(pc_id, start, end, kind)
                    error = "HISTORY_TOO_LARGE"
                    if len(records) <= 2000:
                        pages, current = [], []
                        size = overhead = len(json.dumps(dict(base, ok=True, page=2000, done=False, rows=[]),
                                                         ensure_ascii=False).encode("utf-8"))
                        for record in records:
                            row_size = len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")) + 2
                            if overhead + row_size > MAX_MESSAGE_BYTES:
                                break
                            if current and (len(current) == 100 or size + row_size > MAX_MESSAGE_BYTES):
                                pages.append(current)
                                current, size = [], overhead
                            current.append(record)
                            size += row_size
                        else:
                            pages.append(current)
                            for page, rows in enumerate(pages):
                                session.send(dict(base, ok=True, page=page, done=page == len(pages)-1, rows=rows))
                            return
                except (sqlite3.Error, ValueError) as exc:
                    LOG.error("Audit history read failed: %s", type(exc).__name__)
                    error = "STORAGE_UNAVAILABLE"
        session.send(dict(base, ok=False, code=error))

    def history_v1(self, session, request):
        request_id, pc_id = request.get("request_id"), request.get("pc_id")
        base = {"type": "HISTORY_RESULT", "version": 1, "request_id": request_id, "pc_id": pc_id}
        try:
            if not text(request_id) or not text(pc_id):
                raise ValueError("INVALID_REQUEST")
            start = datetime.fromisoformat(request["start_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(request["end_at"].replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("INVALID_REQUEST")
            start, end = start.timestamp(), end.timestamp()
            if not 0 <= start < end or end - start > 30 * 86400:
                raise ValueError("INVALID_REQUEST")
            if self.audit is None:
                raise sqlite3.OperationalError()
            metrics, events = self.audit.history_snapshot(pc_id, start, end)
            stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
            fields = ("cpu_percent", "ram_percent", "upload_mbps", "download_mbps")
            rows = []
            for received, payload in metrics:
                data = json.loads(payload)
                rows.append(("metrics", {"received_at": stamp(received), **{key: data[key] for key in fields}}))
            for received, payload in events:
                data = json.loads(payload)
                if data.get("kind") in ("CPU_HIGH", "RAM_HIGH", "DEVICE_OFFLINE") and data.get("state") in ("raised", "resolved") and text(data.get("alert_id")):
                    rows.append(("events", {"occurred_at": stamp(received), **{key: data[key] for key in ("kind", "state", "alert_id")}}))
            # Bound encoded frames as well as row count before sending any page.
            pages, part, size = [], [], len(json.dumps(base, ensure_ascii=True).encode()) + 256
            for kind, row in rows:
                row_size = len(json.dumps(row, ensure_ascii=True).encode()) + 2
                if part and (len(part) == 200 or size + row_size > 60000):
                    pages.append(part)
                    part, size = [], len(json.dumps(base, ensure_ascii=True).encode()) + 256
                part.append((kind, row))
                size += row_size
            if part or not pages:
                pages.append(part)
            if len(pages) > 64:
                raise ValueError("HISTORY_TOO_LARGE")
            for page, part in enumerate(pages):
                session.send({**base, "ok": True, "page": page, "done": page == len(pages) - 1,
                              "metrics": [row for kind, row in part if kind == "metrics"],
                              "events": [row for kind, row in part if kind == "events"]})
        except (KeyError, AttributeError, TypeError, ValueError, OverflowError, sqlite3.Error) as exc:
            code = "STORAGE_UNAVAILABLE" if isinstance(exc, sqlite3.Error) else "INVALID_REQUEST"
            if isinstance(exc, ValueError) and str(exc) == "HISTORY_TOO_LARGE":
                code = "HISTORY_TOO_LARGE"
            session.send({**base, "ok": False, "code": code})


def handle_client(conn):
    MonitoringServer().handle(conn)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument("--auth-file")
    parser.add_argument("--heartbeat-timeout", type=float, default=15)
    parser.add_argument("--audit-db", default="runtime/server-audit.sqlite3")
    args = parser.parse_args()
    if not math.isfinite(args.heartbeat_timeout) or args.heartbeat_timeout <= 0:
        parser.error("--heartbeat-timeout must be positive and finite")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    secure = all((args.tls_cert, args.tls_key, args.auth_file))
    if any((args.tls_cert, args.tls_key, args.auth_file)) and not secure:
        parser.error("Use --tls-cert, --tls-key and --auth-file together")
    if not loopback(args.host) and not secure:
        parser.error("External listeners require TLS and authentication")
    context = None
    try:
        auth = load_auth(args.auth_file) if secure else None
        if secure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(args.tls_cert, args.tls_key)
    except (ValueError, OSError, ssl.SSLError) as exc:
        parser.error(str(exc))
    try:
        audit = AuditStore(args.audit_db)
    except (OSError, sqlite3.Error):
        parser.error("Cannot open audit database; check the configured writable path")
    state = MonitoringServer(auth, args.heartbeat_timeout, audit=audit)
    def serve(conn):
        try:
            with conn:
                conn.settimeout(5)
                if context:
                    with context.wrap_socket(conn, server_side=True) as secured:
                        state.handle(secured)
                else:
                    state.handle(conn)
        except OSError:
            pass
    with socket.socket() as listener:
        listener.bind((args.host, args.port))
        listener.listen(16)
        print(f"Monitoring server: {args.host}:{args.port} (TLS/auth: {secure})", flush=True)
        try:
            while True:
                conn, _ = listener.accept()
                threading.Thread(target=serve, args=(conn,), daemon=True).start()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
