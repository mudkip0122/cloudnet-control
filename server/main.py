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
from common.protocol import recv_message, send_message
from common.security import load_auth, loopback

LOG = logging.getLogger("cloudnet.server")


class Session:
    """Serialize complete frames, including unsolicited status notifications."""
    def __init__(self, conn):
        self.conn = conn
        self.send_lock = threading.Lock()
        self.notifications = queue.Queue(maxsize=32)
        self.stopped = threading.Event()
        self.writer = None

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


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MonitoringServer:
    def __init__(self, auth=None, heartbeat_timeout=15):
        if not math.isfinite(heartbeat_timeout) or heartbeat_timeout <= 0:
            raise ValueError("Heartbeat timeout must be positive and finite")
        self.auth = auth
        self.heartbeat_timeout = heartbeat_timeout
        self.lock = threading.Lock()
        self.devices = {}
        self.sessions = {}
        self.admins = set()
        self.connections = {}
        self.pending_commands = {}
        self.seen_commands = {}

    def send(self, conn, message):
        with self.lock:
            session = self.connections.get(conn)
        if session is None:
            raise OSError("Session closed")
        session.send(message)

    def notify_status(self, pc_id):
        with self.lock:
            message = {"type": "STATUS_UPDATE", "device": dict(self.devices[pc_id]),
                       "timestamp": utc_now()}
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
            return {"type": "DEVICE_LIST", "devices": [dict(d) for d in self.devices.values()]}

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
                with self.lock:
                    if candidate in self.sessions:
                        raise ValueError("PC ID is already connected")
                    pc_id = candidate
                    self.sessions[pc_id] = conn
                    self.devices[pc_id] = {"pc_id": pc_id, "pc_name": message["pc_name"],
                                           "online": True, "last_received": utc_now(), "metrics": None}
            response = {"type": "REGISTER_RESULT", "ok": True}
            if pc_id is not None:
                response["pc_id"] = pc_id
            session.send(response)
            if role == "admin" and subscribe:
                session.start_notifications()
                with self.lock:
                    self.admins.add(session)
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
                elif role == "admin" and message["type"] == "COMMAND":
                    self.route_command(conn, message)
                elif role == "agent" and message["type"] == "COMMAND_RESULT":
                    self.route_result(conn, pc_id, message)
                elif role == "agent" and message["type"] == "METRICS":
                    self.update_metrics(pc_id, message)
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
        except (OSError, EOFError):
            pass
        finally:
            stopped.set()
            session.close()
            with self.lock:
                self.admins.discard(session)
                self.connections.pop(conn, None)
                abandoned = [key for key, value in self.pending_commands.items() if value["admin"] is conn]
                for key in abandoned:
                    del self.pending_commands[key]
            if pc_id is not None:
                disconnected = False
                with self.lock:
                    if self.sessions.get(pc_id) is conn:
                        del self.sessions[pc_id]
                        self.devices[pc_id]["online"] = False
                        disconnected = True
                if disconnected:
                    LOG.info("Disconnected pc_id=%s", pc_id)
                    self.notify_status(pc_id)
            if watcher is not None:
                watcher.join(timeout=1)
            if session.writer is not None:
                session.writer.join(timeout=1)

    def route_command(self, admin, request):
        command_id, target = request.get("command_id"), request.get("target_id")
        if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 128:
            raise ValueError("Invalid command ID")
        if not isinstance(target, str) or not target:
            raise ValueError("Invalid target ID")
        error = None
        if not isinstance(admin, ssl.SSLSocket) or self.auth is None:
            error = "TLS_REQUIRED"
        elif request.get("action") not in ("LIST_PROCESSES", "STOP_PROCESS"):
            error = "UNSUPPORTED_ACTION"
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
            self.pending_commands = {key: value for key, value in self.pending_commands.items() if value["until"] > now}
            agent = self.sessions.get(target)
            if agent is None:
                error = error or "TARGET_OFFLINE"
            elif not isinstance(agent, ssl.SSLSocket) or self.auth is None:
                error = error or "TLS_REQUIRED"
            if command_id in self.seen_commands:
                error = error or "DUPLICATE_COMMAND"
            if len(self.seen_commands) >= 4096:
                error = error or "COMMAND_CAPACITY"
            if error is None:
                self.seen_commands[command_id] = now + 60
                self.pending_commands[command_id] = {"admin": admin, "target": target, "agent": agent,
                    "until": now + remaining, "pages": None, "received": set(), "action": request["action"]}
        if error is not None:
            self.send(admin, {"type": "COMMAND_RESULT", "pc_id": target, "command_id": command_id,
                              "ok": False, "code": error})
            return
        try:
            self.send(agent, request)
        except OSError:
            with self.lock:
                self.pending_commands.pop(command_id, None)
            self.send(admin, {"type": "COMMAND_RESULT", "pc_id": target, "command_id": command_id,
                              "ok": False, "code": "TARGET_OFFLINE"})

    def route_result(self, agent, pc_id, message):
        command_id = message.get("command_id")
        if not isinstance(command_id, str) or message.get("pc_id") != pc_id:
            raise ValueError("Command result identity mismatch")
        with self.lock:
            pending = self.pending_commands.get(command_id)
            # Late responses belong to an expired or disconnected request.
            if pending is None:
                return
            if pending["target"] != pc_id or pending["agent"] is not agent:
                raise ValueError("Command result session mismatch")
            if pending["until"] <= time.monotonic():
                del self.pending_commands[command_id]
                return
            finished = True
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
            if finished:
                del self.pending_commands[command_id]
        try:
            self.send(admin, message)
        except OSError:
            # A closed admin must not disconnect the Agent.
            with self.lock:
                self.pending_commands.pop(command_id, None)

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
        for field in ("cpu_percent", "ram_percent", "upload_mbps", "download_mbps"):
            if field not in message:
                raise ValueError("Missing metric: " + field)
            value = message[field]
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid metric: " + field)
                if field.endswith("percent") and value > 100:
                    raise ValueError("Percent must be between 0 and 100")
            clean[field] = value
        with self.lock:
            self.devices[pc_id].update(metrics=clean, last_received=utc_now())


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
    state = MonitoringServer(auth, args.heartbeat_timeout)
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
