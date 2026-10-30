"""Register or stream real metrics to a configurable development server."""
import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import select
import socket
import ssl
import threading
import time

from agent.identity import DEFAULT_ID_FILE, resolve_id
from common.protocol import recv_message, send_message
from common.security import client_settings, connect

LOG = logging.getLogger("cloudnet.agent")


class RegistrationError(ValueError):
    pass


def configure_logging(path):
    LOG.setLevel(logging.INFO)
    for handler in LOG.handlers[:]:
        LOG.removeHandler(handler)
        handler.close()
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
    message = {"type": "REGISTER", "role": "agent", "pc_id": args.pc_id, "pc_name": args.pc_name}
    if getattr(args, "token", None):
        message["token"] = args.token
    send_message(conn, message)
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
                # TLS may already hold decrypted bytes even when the OS socket
                # is not readable. Do not leave buffered responses unread.
                pending = isinstance(conn, ssl.SSLSocket) and conn.pending() > 0
                if not pending and not select.select([conn], [], [], 0.2)[0]:
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


def run(args, context=None):
    failures = 0
    while True:
        started = time.monotonic()
        try:
            LOG.info("Connecting to %s:%s", args.host, args.port)
            with connect(args.host, args.port, context) as conn:
                register(conn, args)
                if not args.live:
                    return 0
                stream(conn, args)
        except ssl.SSLCertVerificationError:
            LOG.error("TLS certificate verification failed; retry stopped. Check CA, server name and certificate validity")
            return 2
        except (ssl.SSLEOFError, ssl.SSLZeroReturnError):
            # An established TLS connection ending is a recoverable disconnect.
            if not args.live:
                LOG.error("TLS connection closed")
                return 1
        except ssl.SSLError:
            LOG.error("TLS protocol error; retry stopped. Check server TLS configuration")
            return 2
        except (RegistrationError, ValueError, UnicodeError):
            LOG.error("Protocol or registration error; automatic retry stopped. Check ID and server settings")
            return 2
        except (OSError, EOFError) as exc:
            if not args.live:
                LOG.error("Connection failed (%s)", type(exc).__name__)
                return 1
            LOG.warning("Connection lost (%s)", type(exc).__name__)
        # A stable session resets the backoff; rapid failures increase it.
        if time.monotonic() - started >= 10:
            failures = 0
        delay = retry_delay(failures)
        failures += 1
        LOG.warning("Retrying connection in %s seconds", delay)
        time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pc-id", help="Override the persistent ID, e.g. for multiple test agents")
    parser.add_argument("--id-file", type=Path, default=DEFAULT_ID_FILE)
    parser.add_argument("--pc-name", default=socket.gethostname())
    parser.add_argument("--host", default="127.0.0.1", help="Server hostname or IP")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--ca-file", type=Path, help="Trusted server certificate/CA PEM")
    parser.add_argument("--token-file", type=Path, help="File containing this Agent's secret token")
    parser.add_argument("--live", action="store_true", help="Stream real metrics and reconnect")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_ID_FILE.parent / "logs" / "agent.log")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not args.host.strip() or not args.pc_name.strip() or len(args.pc_name) > 128:
        parser.error("Host and PC name must be nonempty; PC name must be at most 128 characters")
    try:
        configure_logging(args.log_file)
        try:
            context, args.token = client_settings(args)
        except (OSError, ValueError):
            LOG.error("Invalid TLS/token settings. Supply readable --ca-file and --token-file together for remote connections")
            return 2
        args.pc_id = resolve_id(args.pc_id, args.id_file)
        return run(args, context)
    except KeyboardInterrupt:
        LOG.info("Agent stopped by user")
        return 0
    except (OSError, ValueError) as exc:
        LOG.error("Cannot initialize Agent (%s); check ID file and log path", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
