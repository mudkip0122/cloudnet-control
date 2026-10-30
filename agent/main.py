"""Register or stream real metrics to a configurable development server."""
import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import select
import socket
import threading
import time

from agent.identity import DEFAULT_ID_FILE, resolve_id
from common.protocol import recv_message, send_message

LOG = logging.getLogger("cloudnet.agent")


class RegistrationError(ValueError):
    pass


def configure_logging(path):
    LOG.setLevel(logging.INFO)
    LOG.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    LOG.addHandler(console)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(formatter)
    LOG.addHandler(handler)


def retry_delay(failures):
    return min(3 * 2 ** min(failures, 4), 30)


def register(conn, args):
    send_message(conn, {"type": "REGISTER", "role": "agent", "pc_id": args.pc_id, "pc_name": args.pc_name})
    response = recv_message(conn)
    if response.get("type") != "REGISTER_RESULT" or response.get("ok") is not True or response.get("pc_id") != args.pc_id:
        # Do not log arbitrary server payloads, which may contain sensitive data.
        raise RegistrationError("Registration rejected or invalid response; check PC ID and server configuration")
    LOG.info("Registered PC %s at %s:%s", args.pc_id, args.host, args.port)


def stream(conn, args):
    from agent.collect import samples
    failed = threading.Event()
    errors = []

    def receive():
        try:
            while not failed.is_set():
                if not select.select([conn], [], [], 0.2)[0]:
                    continue
                response = recv_message(conn)
                if response.get("type") == "ERROR":
                    raise RegistrationError("Server rejected this session; check message configuration")
                raise RegistrationError("Unexpected server message")
        except (OSError, EOFError, ValueError) as exc:
            errors.append(exc)
        finally:
            failed.set()

    reader = threading.Thread(target=receive, name="agent-receiver", daemon=True)
    reader.start()
    count = 0
    try:
        for message in samples(args.pc_id):
            if failed.is_set():
                if errors:
                    raise errors[0]
                raise ConnectionError("Server closed the session")
            send_message(conn, message)
            count += 1
            if count == 1:
                LOG.info("Metric transmission started")
            elif count % 30 == 0:
                LOG.info("Metric transmission active: %s samples in this session", count)
        return count
    finally:
        failed.set()
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        reader.join(timeout=6)


def run(args):
    failures = 0
    while True:
        started = time.monotonic()
        try:
            LOG.info("Connecting to %s:%s", args.host, args.port)
            with socket.create_connection((args.host, args.port), timeout=5) as conn:
                register(conn, args)
                if not args.live:
                    return 0
                stream(conn, args)
        except (RegistrationError, ValueError, UnicodeError):
            LOG.error("Protocol or registration error; automatic retry stopped. Check ID and server settings")
            return 2
        except (OSError, EOFError) as exc:
            if not args.live:
                LOG.error("Connection failed (%s)", type(exc).__name__)
                return 1
            # A stable session resets the backoff; rapid failures increase it.
            if time.monotonic() - started >= 10:
                failures = 0
            delay = retry_delay(failures)
            failures += 1
            LOG.warning("Connection lost (%s); retry in %s seconds", type(exc).__name__, delay)
            time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pc-id", help="Override the persistent ID, e.g. for multiple test agents")
    parser.add_argument("--id-file", type=Path, default=DEFAULT_ID_FILE)
    parser.add_argument("--pc-name", default=socket.gethostname())
    parser.add_argument("--host", default="127.0.0.1", help="Server hostname or IP")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--live", action="store_true", help="Stream real metrics and reconnect")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_ID_FILE.parent / "logs" / "agent.log")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not args.host.strip() or not args.pc_name.strip() or len(args.pc_name) > 128:
        parser.error("Host and PC name must be nonempty; PC name must be at most 128 characters")
    try:
        configure_logging(args.log_file)
        args.pc_id = resolve_id(args.pc_id, args.id_file)
        return run(args)
    except KeyboardInterrupt:
        LOG.info("Agent stopped by user")
        return 0
    except (OSError, ValueError) as exc:
        LOG.error("Cannot initialize Agent (%s); check ID file and log path", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
