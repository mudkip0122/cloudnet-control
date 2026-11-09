"""Loopback-only live monitoring server."""
import argparse
from datetime import datetime, timezone
import math
import socket
import threading
import ssl
import hmac
import time
from pathlib import Path
from common.security import loopback
from common.protocol import recv_message, send_message


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MonitoringServer:
    def __init__(self, admin_token=None, agent_token=None):
        self.tokens = {"admin": admin_token, "agent": agent_token}
        self.lock = threading.Lock()
        self.devices = {}
        self.sessions = {}
        self.send_locks = {}
        self.pending_commands = {}
        self.seen_commands = {}

    def send(self, conn, message):
        with self.lock:
            send_lock = self.send_locks.get(conn)
        if send_lock is None:
            raise OSError("Session closed")
        with send_lock:
            send_message(conn, message)

    def snapshot(self):
        with self.lock:
            return {"type": "DEVICE_LIST", "devices": [dict(d) for d in self.devices.values()]}

    def handle(self, conn):
        pc_id = None
        with self.lock:
            self.send_locks[conn] = threading.Lock()
        try:
            conn.settimeout(5)
            message = recv_message(conn)
            if message["type"] != "REGISTER":
                raise ValueError("First message must be REGISTER")
            role = message.get("role")
            if role not in ("agent", "admin"):
                raise ValueError("Role must be agent or admin")
            expected = self.tokens[role]
            supplied = message.get("token")
            if expected is not None and (not isinstance(supplied, str) or
                                        not hmac.compare_digest(supplied.encode(), expected.encode())):
                send_message(conn, {"type": "ERROR", "code": "UNAUTHORIZED"})
                return
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
            send_message(conn, response)
            conn.settimeout(40 if role == "admin" else 15)
            while True:
                message = recv_message(conn)
                if role == "admin" and message["type"] == "LIST_DEVICES":
                    self.send(conn, self.snapshot())
                elif role == "admin" and message["type"] == "COMMAND":
                    self.route_command(conn, message)
                elif role == "agent" and message["type"] == "METRICS":
                    self.update_metrics(pc_id, message)
                elif role == "agent" and message["type"] == "HEARTBEAT":
                    if message.get("pc_id") != pc_id:
                        raise ValueError("Heartbeat PC ID mismatch")
                    with self.lock:
                        self.devices[pc_id]["last_received"] = utc_now()
                    self.send(conn, {"type": "HEARTBEAT_ACK", "pc_id": pc_id})
                elif role == "agent" and message["type"] == "COMMAND_RESULT":
                    self.route_result(conn, pc_id, message)
                else:
                    raise ValueError("Message is not allowed for this role")
        except (ValueError, UnicodeError) as exc:
            try:
                send_message(conn, {"type": "ERROR", "code": "INVALID_MESSAGE", "message": str(exc)})
            except OSError:
                pass
        except (OSError, EOFError):
            pass
        finally:
            with self.lock:
                self.send_locks.pop(conn, None)
                abandoned = [key for key, value in self.pending_commands.items() if value["admin"] is conn]
                for key in abandoned:
                    del self.pending_commands[key]
            if pc_id is not None:
                with self.lock:
                    if self.sessions.get(pc_id) is conn:
                        del self.sessions[pc_id]
                        self.devices[pc_id]["online"] = False

    def route_command(self, admin, request):
        command_id, target = request.get("command_id"), request.get("target_id")
        if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 128:
            raise ValueError("Invalid command ID")
        if not isinstance(target, str) or not target:
            raise ValueError("Invalid target ID")
        error = None
        if not isinstance(admin, ssl.SSLSocket) or not self.tokens["admin"]:
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
            elif not isinstance(agent, ssl.SSLSocket) or not self.tokens["agent"]:
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
    parser.add_argument("--cert-file")
    parser.add_argument("--key-file")
    parser.add_argument("--admin-token-file")
    parser.add_argument("--agent-token-file")
    args = parser.parse_args()
    secure = all((args.cert_file, args.key_file, args.admin_token_file, args.agent_token_file))
    if any((args.cert_file, args.key_file, args.admin_token_file, args.agent_token_file)) and not secure:
        parser.error("TLS requires cert, key, admin-token-file and agent-token-file together")
    if not loopback(args.host) and not secure:
        parser.error("Remote listening requires TLS and role tokens")
    context = None
    admin_token = agent_token = None
    if secure:
        try:
            admin_token = Path(args.admin_token_file).read_text(encoding="utf-8").strip()
            agent_token = Path(args.agent_token_file).read_text(encoding="utf-8").strip()
            if not admin_token or not agent_token or admin_token == agent_token:
                parser.error("Role tokens must be nonempty and different")
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(args.cert_file, args.key_file)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    state = MonitoringServer(admin_token, agent_token)
    def serve(conn):
        with conn:
            try:
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
        print(f"Monitoring server: {args.host}:{args.port} TLS={secure}", flush=True)
        try:
            while True:
                conn, _ = listener.accept()
                threading.Thread(target=serve, args=(conn,), daemon=True).start()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
