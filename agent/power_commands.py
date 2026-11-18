"""Execute explicitly enabled, fixed local power actions on Windows."""
from contextlib import closing
import ctypes
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import subprocess

POWER_ACTIONS = ("SHUTDOWN_PC", "REBOOT_PC")
COMMAND_WINDOW = 60
COMMAND_CAPACITY = 4096


def windows_system_directory():
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError("Cannot locate Windows system directory")
    return Path(buffer.value)


def read_boot_id():
    if os.name != "nt":
        return None
    query = ("$boot = (Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime; "
             "([DateTimeOffset]$boot).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ', "
             "[Globalization.CultureInfo]::InvariantCulture)")
    try:
        powershell = windows_system_directory() / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", query],
                                check=True, capture_output=True, text=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        value = result.stdout.strip()
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def request_windows_power(action):
    option = {"SHUTDOWN_PC": "/s", "REBOOT_PC": "/r"}[action]
    program = windows_system_directory() / "shutdown.exe"
    subprocess.run([str(program), option, "/t", "0"], check=True, timeout=4,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=subprocess.CREATE_NO_WINDOW)


class PowerCommands:
    def __init__(self, pc_id, mode="off", boot_id=None, db_path=None):
        self.pc_id = pc_id
        self.mode = mode
        self.boot_id = boot_id
        self.db_path = db_path

    def reserve(self, command_id, now):
        # An explicit write transaction also serializes reservations by two processes.
        with closing(sqlite3.connect(self.db_path, timeout=2)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS power_commands (
                pc_id TEXT NOT NULL, command_id TEXT NOT NULL, retain_until REAL NOT NULL,
                PRIMARY KEY (pc_id, command_id))""")
            db.execute("DELETE FROM power_commands WHERE retain_until <= ?", (now,))
            if db.execute("SELECT 1 FROM power_commands WHERE pc_id = ? AND command_id = ?",
                          (self.pc_id, command_id)).fetchone():
                return "DUPLICATE_COMMAND"
            if db.execute("SELECT COUNT(*) FROM power_commands").fetchone()[0] >= COMMAND_CAPACITY:
                return "COMMAND_CAPACITY"
            db.execute("INSERT INTO power_commands VALUES (?, ?, ?)",
                       (self.pc_id, command_id, now + COMMAND_WINDOW))
        return None

    def handle(self, request, send, *, server_supported):
        command_id = request.get("command_id")
        valid_id = isinstance(command_id, str) and bool(command_id.strip()) and len(command_id) <= 128
        base = {"type": "COMMAND_RESULT", "pc_id": self.pc_id,
                "command_id": command_id if valid_id else None}

        def result(code, *, ok=False, terminal=True):
            send(base | {"ok": ok, "code": code, "terminal": terminal})

        if self.mode == "off" or not server_supported:
            return result("POWER_CONTROL_DISABLED")
        action = request.get("action")
        if action not in POWER_ACTIONS:
            return result("UNSUPPORTED_ACTION")
        if not valid_id:
            return result("INVALID_COMMAND_ID")
        if request.get("target_id") != self.pc_id:
            return result("WRONG_TARGET")
        # A request composed for a simulated session must never run on a real one.
        if request.get("power_control_mode") != self.mode:
            return result("POWER_MODE_MISMATCH")
        if request.get("target_boot_id") != self.boot_id or "target_boot_id" not in request:
            return result("BOOT_ID_MISMATCH")
        if self.mode == "execute" and (os.name != "nt" or self.boot_id is None):
            return result("POWER_UNAVAILABLE")
        try:
            expiry = datetime.fromisoformat(request["expires_at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return result("INVALID_EXPIRY")
        if expiry.tzinfo is None:
            return result("INVALID_EXPIRY")
        now = datetime.now(timezone.utc)
        remaining = (expiry - now).total_seconds()
        if not 0 < remaining <= COMMAND_WINDOW:
            return result("EXPIRED_OR_TOO_FAR")
        try:
            rejection = self.reserve(command_id, now.timestamp())
        except (sqlite3.Error, UnicodeError, OSError):
            return result("POWER_STATE_UNAVAILABLE")
        if rejection:
            return result(rejection)
        result("POWER_ACCEPTED", ok=True, terminal=False)
        if datetime.now(timezone.utc) >= expiry:
            return result("EXPIRED_OR_TOO_FAR")
        if self.mode == "simulate":
            return result("POWER_SIMULATED", ok=True)
        try:
            request_windows_power(action)
        except subprocess.TimeoutExpired:
            return result("POWER_EXECUTION_UNKNOWN")
        except (OSError, subprocess.CalledProcessError):
            return result("POWER_EXECUTION_FAILED")
        return result("POWER_EXECUTION_STARTED", ok=True)
