"""Register or stream real metrics to a configurable development server."""
import argparse
import logging
import math
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
import select
import socket
import ssl
import threading
import time

from agent.identity import DEFAULT_ID_FILE, resolve_id
from agent.exam import ExamMonitor
from agent.exam_commands import EXAM_ACTIONS, ExamCommands
from agent.process_commands import ProcessCommands
from agent.rtt import RttTracker, validate_rtt_settings
from common.protocol import recv_message, send_message
from common.security import client_settings, connect

LOG = logging.getLogger("cloudnet.agent")


class RegistrationError(ValueError):
    pass


def heartbeat_interval(value):
    """Zero keeps compatibility with servers that only accept METRICS."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Heartbeat interval must be a finite number")
    if value != 0 and not 1 <= value <= 60:
        raise ValueError("Heartbeat interval must be 0 or between 1 and 60 seconds")
    return value


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
    # Recompute for each session; a reconnect must not reuse an old server's setting.
    args.session_heartbeat_interval = heartbeat_interval(
        response.get("heartbeat_interval", getattr(args, "heartbeat_interval", 0)))
    args.session_rtt_enabled = (response.get("rtt_v1") is True
                                and getattr(args, "rtt_interval", 5) != 0)
    if (getattr(args, "exam_mode", False) or getattr(args, "allow_remote_exam_control", False)) and response.get("exam_events_v1") is not True:
        LOG.error("Exam mode requires server exam_events_v1 support; exam monitoring has not started")
        raise RegistrationError("Exam mode requires server exam_events_v1 support")
    if getattr(args, "allow_remote_exam_control", False) and response.get("exam_control_v1") is not True:
        LOG.error("Remote exam control requires server exam_control_v1 support")
        raise RegistrationError("Remote exam control is unsupported by the server")
    LOG.info("Registered PC %s at %s:%s", args.pc_id, args.host, args.port)


def stream(conn, args):
    from agent.collect import samples
    failed = threading.Event()
    errors = []
    send_lock = threading.Lock()
    rtt = (RttTracker(args.pc_id, getattr(args, "rtt_interval", 5), getattr(args, "rtt_timeout", 3))
           if getattr(args, "session_rtt_enabled", False) else None)
    exam_commands = getattr(args, "exam_commands", None) or ExamCommands(args.pc_id)
    initial_monitor = (ExamMonitor(args.pc_id, args.exam_id, args.forbid_process, args.exam_poll_interval)
                       if getattr(args, "exam_mode", False) else None)
    exam_commands.start_session(initial_monitor)

    def fail(exc):
        errors.append(exc)
        failed.set()
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def send(message, *, probe=False):
        # A complete header + payload is sent under one connection-local lock.
        with send_lock:
            if failed.is_set():
                raise errors[0] if errors else ConnectionError("Session ended")
            if probe:
                message = rtt.begin()
            send_message(conn, message)

    def measure_rtt():
        try:
            while not failed.is_set():
                send(None, probe=True)
                if failed.wait(rtt.timeout):
                    return
                rtt.snapshot()  # Clear unanswered probes, independently of metric collection.
                if failed.wait(rtt.interval - rtt.timeout):
                    return
        except (OSError, EOFError, ValueError) as exc:
            fail(exc)

    def heartbeat():
        while not failed.wait(interval):
            try:
                send({"type": "HEARTBEAT", "pc_id": args.pc_id,
                      "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})
            except (OSError, EOFError, ValueError) as exc:
                fail(exc)
                return

    def receive():
        try:
            while not failed.is_set():
                # TLS may already hold decrypted bytes even when the OS socket
                # is not readable. Do not leave buffered responses unread.
                pending = isinstance(conn, ssl.SSLSocket) and conn.pending() > 0
                if not pending and not select.select([conn], [], [], 0.2)[0]:
                    continue
                response = recv_message(conn)
                if rtt is not None and response.get("type") == "RTT_PONG":
                    rtt.accept(response)
                    continue
                if response.get("type") == "COMMAND":
                    if response.get("action") in EXAM_ACTIONS:
                        with exam_commands.lock:
                            for result in exam_commands.handle(response):
                                send(result)
                        continue
                    commands = getattr(args, "process_commands", None)
                    if commands is None:
                        commands = ProcessCommands(args.pc_id)
                    for result in commands.handle(response):
                        send(result)
                    continue
                if (interval and response.get("type") == "HEARTBEAT_ACK"
                        and response.get("pc_id") == args.pc_id):
                    continue
                if response.get("type") == "ERROR":
                    raise RegistrationError("Server rejected this session; check message configuration")
                raise RegistrationError("Unexpected server message")
        except (OSError, EOFError, ValueError) as exc:
            fail(exc)
        finally:
            failed.set()

    def watch_exam():
        try:
            while not failed.is_set():
                exam_commands.poll(send)
                if failed.wait(.1):
                    return
        except (OSError, EOFError, ValueError) as exc:
            fail(exc)

    interval = heartbeat_interval(getattr(args, "session_heartbeat_interval", 0))
    reader = threading.Thread(target=receive, name="agent-receiver", daemon=True)
    reader.start()
    pulse = None
    exam_worker = None
    rtt_worker = None
    if interval:
        LOG.info("Heartbeat enabled: every %s seconds", interval)
        pulse = threading.Thread(target=heartbeat, name="agent-heartbeat", daemon=True)
        pulse.start()
    if getattr(args, "exam_mode", False) or exam_commands.enabled:
        LOG.info("Exam monitor ready; startup mode=%s remote control=%s",
                 bool(initial_monitor), exam_commands.enabled)
        exam_worker = threading.Thread(target=watch_exam, name="agent-exam", daemon=True)
        exam_worker.start()
    if rtt is not None:
        LOG.info("RTT measurement enabled: interval=%s timeout=%s seconds", rtt.interval, rtt.timeout)
        rtt_worker = threading.Thread(target=measure_rtt, name="agent-rtt", daemon=True)
        rtt_worker.start()
    count = 0
    try:
        for message in samples(args.pc_id):
            if failed.is_set():
                if errors:
                    raise errors[0]
                raise ConnectionError("Server closed the session")
            send(message | rtt.snapshot() if rtt is not None else message)
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
        if pulse is not None:
            pulse.join(timeout=6)
        if exam_worker is not None:
            exam_worker.join(timeout=6)
        if rtt_worker is not None:
            rtt_worker.join(timeout=6)


def run(args, context=None):
    args.exam_commands = ExamCommands(args.pc_id, enabled=bool(
        getattr(args, "allow_remote_exam_control", False) and context is not None
        and getattr(args, "token", None) and args.live))
    args.process_commands = ProcessCommands(args.pc_id, enabled=bool(
        getattr(args, "enable_process_control", False) and context is not None and getattr(args, "token", None)),
        protected_names=getattr(args, "protect_process", ()))
    failures = 0
    while True:
        started = time.monotonic()
        try:
            LOG.info("Connecting to %s:%s", args.host, args.port)
            with connect(args.host, args.port, context, getattr(args, "server_name", None)) as conn:
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
    parser.add_argument("--server-name", help="Expected TLS certificate identity; defaults to --host")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--ca-file", type=Path, help="Trusted server certificate/CA PEM")
    parser.add_argument("--token-file", type=Path, help="File containing this Agent's secret token")
    parser.add_argument("--live", action="store_true", help="Stream real metrics and reconnect")
    parser.add_argument("--enable-process-control", action="store_true",
                        help="Accept process inventory/stop commands over authenticated TLS")
    parser.add_argument("--exam-mode", action="store_true", help="Observe forbidden program names during an exam")
    parser.add_argument("--allow-remote-exam-control", action="store_true",
                        help="Allow authenticated GUI exam on/off and status commands; starts off unless --exam-mode")
    parser.add_argument("--exam-id", help="Exam session identifier (1-64 ASCII letters/digits/_.-)")
    parser.add_argument("--forbid-process", action="append", default=None, metavar="NAME.exe",
                        help="Replace default exam detection list with exact executable names; repeat for multiple names")
    parser.add_argument("--exam-poll-interval", type=float, default=1,
                        help="Exam scan interval in seconds (0.5-60; default 1)")
    parser.add_argument("--protect-process", action="append", default=[], metavar="NAME.exe",
                        help="Add a protected process name; repeat to protect several applications")
    parser.add_argument("--heartbeat-interval", type=float, default=0,
                        help="Heartbeat seconds (1-60); 0 for legacy servers. Server setting takes precedence")
    parser.add_argument("--rtt-interval", type=float, default=5,
                        help="RTT probe interval in seconds (1-60); 0 disables; requires server rtt_v1 support")
    parser.add_argument("--rtt-timeout", type=float, default=3,
                        help="RTT response timeout (0.1-60 seconds, no greater than interval)")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_ID_FILE.parent / "logs" / "agent.log")
    args = parser.parse_args()
    try:
        heartbeat_interval(args.heartbeat_interval)
        validate_rtt_settings(args.rtt_interval, args.rtt_timeout)
        if args.exam_mode:
            ExamMonitor("validation", args.exam_id, args.forbid_process, args.exam_poll_interval)
        elif args.exam_id or args.forbid_process or args.exam_poll_interval != 1:
            raise ValueError("Exam settings require --exam-mode")
    except ValueError as exc:
        parser.error(str(exc))
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
        if (args.exam_mode or args.allow_remote_exam_control) and (context is None or not args.token or not args.live):
            LOG.error("Exam mode requires TLS/token settings and --live")
            return 2
        if args.enable_process_control and (context is None or not args.live):
            LOG.error("Process control requires TLS/token settings and --live")
            return 2
        return run(args, context)
    except KeyboardInterrupt:
        LOG.info("Agent stopped by user")
        return 0
    except (OSError, ValueError) as exc:
        LOG.error("Cannot initialize Agent (%s); check ID file and log path", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
