"""Read process identities from a Windows Tool Help snapshot."""
import ctypes
from ctypes import wintypes
import sys


SECURE_SYSTEM_NAME = "secure system"
TH32CS_SNAPPROCESS = 0x00000002
ERROR_NO_MORE_FILES = 18
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def secure_system_pid():
    """Return the uniquely identified Secure System child of System, if visible."""
    if sys.platform != "win32":
        return None

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for read in (kernel.Process32FirstW, kernel.Process32NextW):
        read.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        read.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, INVALID_HANDLE_VALUE):
        return None
    try:
        entry = ProcessEntry(dwSize=ctypes.sizeof(ProcessEntry))
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        processes = {}
        while True:
            processes[entry.th32ProcessID] = (entry.szExeFile.casefold(), entry.th32ParentProcessID)
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() != ERROR_NO_MORE_FILES:
                    return None
                break
    finally:
        kernel.CloseHandle(snapshot)

    candidates = [pid for pid, (name, parent) in processes.items()
                  if pid > 0 and parent > 0 and name == SECURE_SYSTEM_NAME
                  and processes.get(parent) == ("system", 0)]
    return candidates[0] if len(candidates) == 1 else None
