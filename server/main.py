"""Loopback-only live monitoring server."""
import argparse
from datetime import datetime, timezone
import math
import socket
import threading
from common.protocol import recv_message, send_message


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MonitoringServer:
    def __init__(self):
        self.lock = threading.Lock()
        self.devices = {}
        self.sessions = {}

    def snapshot(self):
        with self.lock:
            return {"type": "DEVICE_LIST", "devices": [dict(d) for d in self.devices.values()]}

    def handle(self, conn):
        pc_id = None
        try:
            conn.settimeout(5)
            message = recv_message(conn)
            if message["type"] != "REGISTER":
                raise ValueError("First message must be REGISTER")
            role = message.get("role")
            if role not in ("agent", "admin"):
                raise ValueError("Role must be agent or admin")
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
            conn.settimeout(15)
            while True:
                message = recv_message(conn)
                if role == "admin" and message["type"] == "LIST_DEVICES":
                    send_message(conn, self.snapshot())
                elif role == "agent" and message["type"] == "METRICS":
                    self.update_metrics(pc_id, message)
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
            if pc_id is not None:
                with self.lock:
                    if self.sessions.get(pc_id) is conn:
                        del self.sessions[pc_id]
                        self.devices[pc_id]["online"] = False

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
    args = parser.parse_args()
    state = MonitoringServer()
    def serve(conn):
        with conn:
            state.handle(conn)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", args.port))
        listener.listen(16)
        print(f"Local monitoring server: 127.0.0.1:{args.port}", flush=True)
        try:
            while True:
                conn, _ = listener.accept()
                threading.Thread(target=serve, args=(conn,), daemon=True).start()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
