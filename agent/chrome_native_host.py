"""Chrome Native Messaging entry point and per-user host registration on Windows."""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import struct
import sys
from uuid import uuid4

from agent.chrome_tabs import CAMERA_STATES, DEFAULT_DB, TRANSITIONS, TabStore

NAME = "com.cloudnet_control.exam_tabs"
MAX_MESSAGE = 1024
ORIGIN = re.compile(r"chrome-extension://[a-p]{32}/")


def read_message(source):
    header = source.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("Incomplete Chrome message header")
    size, = struct.unpack("=I", header)
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError("Invalid Chrome message size")
    body = source.read(size)
    if len(body) != size:
        raise ValueError("Incomplete Chrome message")
    request = json.loads(body.decode("utf-8"))
    if not isinstance(request, dict):
        raise ValueError("Chrome message must be an object")
    return request


def write_status(target, status):
    body = json.dumps({"status": status}).encode("utf-8")
    target.write(struct.pack("=I", len(body)) + body)
    target.flush()


def serve(source, target, store=None):
    store = store or TabStore()
    host = uuid4().hex
    session = None
    try:
        while (request := read_message(source)) is not None:
            kind = request.get("kind")
            transition = request.get("transition")
            wait_for_exam = request.keys() == {"kind", "wait"} and request.get("wait") is True
            if kind == "attach" and session is None and (request.keys() == {"kind"} or wait_for_exam):
                status, session = store.attach(host)
                if status == "exam_off" and wait_for_exam:
                    status = "waiting_for_exam"
            elif kind == "attach" and session is not None and wait_for_exam:
                status = store.report(session, host)
            elif kind == "heartbeat" and session is not None and request.keys() == {"kind"}:
                status = store.report(session, host)
            elif (kind == "camera" and session is not None and request.get("state") in CAMERA_STATES
                  and request.keys() == {"kind", "state"}):
                status = store.report(session, host, f"camera_{request['state']}")
            elif (kind == "transition" and session is not None and transition in TRANSITIONS
                  and request.keys() == {"kind", "transition"}):
                status = store.report(session, host, transition)
                if transition == "closed" and status == "monitoring":
                    status = "tab_closed"
                    store.detach(session, host)
                    session = None
            else:
                status = "invalid_message"
            write_status(target, status)
            if status not in ("monitoring", "waiting_for_exam"):
                return
    except (OSError, sqlite3.Error, ValueError):
        return
    finally:
        if session is not None:
            try:
                store.detach(session, host)
            except (OSError, sqlite3.Error):
                pass  # The monitoring lease expires even after an abrupt process exit.


def install(extension_id):
    import winreg

    root = DEFAULT_DB.parent.parent
    manifest = DEFAULT_DB.with_name(NAME + ".json")
    launcher = root / "agent" / "chrome_native_host.cmd"
    origin = f"chrome-extension://{extension_id}/"
    if not ORIGIN.fullmatch(origin):
        raise ValueError("Expected the 32-letter ID from chrome://extensions")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"name": NAME, "description": "CloudNet Control Chrome exam tab monitor",
                                    "path": str(launcher), "type": "stdio", "allowed_origins": [origin]},
                                   indent=2), encoding="utf-8")
    key = rf"Software\Google\Chrome\NativeMessagingHosts\{NAME}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as registration:
        winreg.SetValueEx(registration, "", 0, winreg.REG_SZ, str(manifest))


def main():
    if sys.platform != "win32":
        return 2
    if len(sys.argv) > 1 and ORIGIN.fullmatch(sys.argv[1]):
        import msvcrt
        import os

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        serve(sys.stdin.buffer, sys.stdout.buffer)
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", metavar="EXTENSION_ID", required=True)
    args = parser.parse_args()
    try:
        install(args.install)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print("Registered CloudNet Control Chrome native host for this Windows user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
