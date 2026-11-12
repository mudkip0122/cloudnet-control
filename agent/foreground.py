"""Foreground process observation; never reads window titles or browser content."""
import ctypes
from ctypes import wintypes
import sys

import psutil


def foreground_process():
    """Return a stable foreground process identity, or None for a sampling gap."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    window = user32.GetForegroundWindow()
    if not window:
        return None
    pid = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(window, ctypes.byref(pid)) or not pid.value:
        return None
    process = psutil.Process(pid.value)
    with process.oneshot():
        name, created = process.name(), process.create_time()
    # A switch during lookup is a gap, not evidence of either application.
    check_pid = wintypes.DWORD()
    if (user32.GetForegroundWindow() != window
            or not user32.GetWindowThreadProcessId(window, ctypes.byref(check_pid))
            or check_pid.value != pid.value):
        return None
    return {"pid": pid.value, "name": name[:128], "create_time": created}


class ForegroundMonitor:
    def __init__(self):
        self.previous = None
        self.status = None

    def scan(self, event):
        try:
            process = foreground_process()
        except (psutil.Error, OSError):
            process = None
        status = "ok" if process else "unavailable"
        events = []
        if status != self.status:
            events.append(event("FOREGROUND_MONITOR_STATUS", status=status))
            self.status = status
        if process is None:
            self.previous = None
            return events
        previous, self.previous = self.previous, process
        if previous is not None:
            was_chrome = previous["name"].casefold() == "chrome.exe"
            is_chrome = process["name"].casefold() == "chrome.exe"
            if was_chrome != is_chrome:
                events.append(event("EXAM_WINDOW_CHANGED",
                                    transition="returned" if is_chrome else "left",
                                    previous_process=previous, process=process))
        return events
