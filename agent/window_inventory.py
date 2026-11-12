"""Read displayed top-level window owners without titles or content."""
import ctypes
from ctypes import wintypes
import sys


def displayed_process_ids():
    """PID set, or None when window visibility cannot be established."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    for name in ("IsWindowVisible", "IsIconic"):
        function = getattr(user32, name)
        function.argtypes = [wintypes.HWND]
        function.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                         ctypes.c_void_p, wintypes.DWORD]
    dwm.DwmGetWindowAttribute.restype = ctypes.c_long
    pids, failed = set(), []

    @callback_type
    def visit(window, _):
        try:
            if not user32.IsWindowVisible(window) or user32.IsIconic(window):
                return True
            cloaked = wintypes.DWORD()
            if dwm.DwmGetWindowAttribute(window, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) != 0:
                failed.append(True)
                return True
            if not cloaked.value:
                pid = wintypes.DWORD()
                if user32.GetWindowThreadProcessId(window, ctypes.byref(pid)) and pid.value:
                    pids.add(pid.value)
                else:
                    failed.append(True)
        except (OSError, ValueError):
            failed.append(True)
        return True

    if not user32.EnumWindows(visit, 0) or failed:
        return None
    return pids
