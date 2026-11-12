"""Process inventory and conservative, identity-checked termination."""
import argparse
import ctypes
from ctypes import wintypes
import json
import math
import ntpath
import os
import time
from datetime import datetime, timezone

import psutil

MAX_BATCH_TARGETS = 100
BATCH_SECONDS = 5


def validate_batch_targets(targets):
    """Validate the entire explicit snapshot before terminating any PID."""
    if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_BATCH_TARGETS:
        raise ValueError("Expected 1-100 explicit process identities")
    seen = set()
    for row in targets:
        if not isinstance(row, dict):
            raise ValueError("Invalid process identity")
        pid, created = row.get("pid"), row.get("create_time")
        if (type(pid) is not int or pid <= 0 or pid in seen
                or type(created) not in (int, float) or not math.isfinite(created) or created <= 0):
            raise ValueError("Invalid or duplicate process identity")
        seen.add(pid)

PROTECTED_NAMES = frozenset({
    "system", "system idle process", "registry", "secure system", "memory compression",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "lsaiso.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe",
    "explorer.exe", "sihost.exe", "taskhostw.exe", "runtimebroker.exe",
    "securityhealthservice.exe", "securityhealthsystray.exe", "msmpeng.exe",
    "mssense.exe", "nissrv.exe", "spoolsv.exe", "audiodg.exe",
})


def windows_critical(pid):
    """Unknown critical status is protected, including access denied."""
    if os.name != "nt":
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.IsProcessCritical.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessCritical.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        critical = wintypes.BOOL()
        if not kernel.IsProcessCritical(handle, ctypes.byref(critical)):
            return None
        return bool(critical.value)
    finally:
        kernel.CloseHandle(handle)


def under_windows(path):
    if os.name != "nt":
        return False
    root = os.environ.get("SystemRoot", r"C:\Windows")
    try:
        root = ntpath.normcase(ntpath.realpath(root))
        path = ntpath.normcase(ntpath.realpath(path))
        return ntpath.commonpath([root, path]) == root
    except (OSError, ValueError):
        return True


class ProcessManager:
    def __init__(self, protected_names=()):
        self.protected_names = PROTECTED_NAMES | {name.casefold() for name in protected_names}

    def identity(self):
        me = psutil.Process()
        return me.pid, {p.pid for p in me.parents()}, me.username().casefold()

    def protection(self, process, identity=None):
        """Always recompute policy from the live process, never trust GUI flags."""
        try:
            own_pid, ancestors, own_user = identity if identity is not None else self.identity()
            if process.pid <= 4 or process.pid == own_pid or process.pid in ancestors:
                return "AGENT_OR_SYSTEM_PID"
            if process.name().casefold() in self.protected_names:
                return "PROTECTED_NAME"
            owner = process.username()
            if not owner or owner.casefold() != own_user:
                return "OTHER_USER"
            executable = process.exe()
            if not executable or under_windows(executable):
                return "SYSTEM_OR_UNKNOWN_EXECUTABLE"
            critical = windows_critical(process.pid)
            if critical is not False:
                return "CRITICAL_OR_UNKNOWN"
            return None
        except (psutil.Error, OSError):
            return "ACCESS_DENIED_OR_EXITED"

    def inventory(self):
        rows = []
        try:
            identity = self.identity()
        except (psutil.Error, OSError):
            identity = None
        # PID-only iteration still shows processes whose details are inaccessible.
        for process in psutil.process_iter():
            row = {"pid": process.pid}
            for key in ("name", "username", "create_time", "memory_info", "status"):
                try:
                    value = getattr(process, key)()
                    if key == "memory_info":
                        row["memory_bytes"] = value.rss
                    else:
                        row[key] = value[:128] if isinstance(value, str) else value
                except (psutil.Error, OSError):
                    row["memory_bytes" if key == "memory_info" else key] = None
            reason = self.protection(process, identity) if identity is not None else "UNKNOWN_AGENT_IDENTITY"
            if row["create_time"] is None:
                reason = reason or "UNKNOWN_IDENTITY"
            row.update(can_stop=reason is None, protected_reason=reason)
            rows.append(row)
        return sorted(rows, key=lambda row: row["pid"])

    def stop(self, pid, create_time, wait_timeout=2):
        if (type(pid) is not int or pid <= 0 or isinstance(create_time, bool)
                or not isinstance(create_time, (int, float))
                or not math.isfinite(create_time) or create_time <= 0):
            return {"ok": False, "code": "INVALID_IDENTITY"}
        try:
            process = psutil.Process(pid)
            if process.create_time() != create_time:
                return {"ok": False, "code": "PID_REUSED"}
            reason = self.protection(process)
            if reason:
                return {"ok": False, "code": "PROTECTED", "reason": reason}
            # psutil.terminate also checks process identity before signalling.
            process.terminate()
            try:
                process.wait(timeout=wait_timeout)
                return {"ok": True, "code": "TERMINATED"}
            except psutil.TimeoutExpired:
                return {"ok": True, "code": "TERMINATION_REQUESTED"}
        except psutil.NoSuchProcess:
            return {"ok": False, "code": "NOT_FOUND"}
        except (psutil.AccessDenied, PermissionError):
            return {"ok": False, "code": "ACCESS_DENIED"}
        except (psutil.Error, OSError):
            return {"ok": False, "code": "PROCESS_ERROR"}

    def stop_many(self, targets, expires_at):
        validate_batch_targets(targets)
        deadline = time.monotonic() + BATCH_SECONDS
        outcomes = []
        for row in targets:
            pid, created = row["pid"], row["create_time"]
            remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
            budget = deadline - time.monotonic()
            if remaining <= 0:
                outcome = {"ok": False, "code": "EXPIRED_DURING_BATCH"}
            elif budget <= 0:
                outcome = {"ok": False, "code": "BATCH_TIME_LIMIT"}
            else:
                # Every target goes through the same live identity/protection
                # check as single stop; never enumerate extra same-name PIDs.
                outcome = self.stop(pid, created, wait_timeout=min(.1, remaining, budget))
            outcomes.append({"pid": pid, "create_time": created} | outcome)
        return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    listing = sub.add_parser("list")
    stop = sub.add_parser("stop")
    for command in (listing, stop):
        command.add_argument("--protect-process", action="append", default=[])
    stop.add_argument("--pid", type=int, required=True)
    stop.add_argument("--create-time", type=float, required=True)
    args = parser.parse_args()
    manager = ProcessManager(args.protect_process)
    result = manager.inventory() if args.action == "list" else manager.stop(args.pid, args.create_time)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    return 0 if args.action == "list" or result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
