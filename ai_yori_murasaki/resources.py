"""Resource measurement (SPEC A-6). Real platform APIs only; never fabricate 0."""
from __future__ import annotations

import platform
import sys
from typing import Any, Dict, Optional


def _unix_rss() -> Dict[str, Optional[int]]:
    rss = peak = None
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss: KiB on Linux, bytes on macOS
        if sys.platform == "darwin":
            peak = int(usage.ru_maxrss)
        else:
            peak = int(usage.ru_maxrss) * 1024
    except Exception:
        pass
    try:
        with open("/proc/self/status", "r", encoding="ascii", errors="ignore") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                    break
    except Exception:
        pass
    return {"rss_bytes": rss, "peak_rss_bytes": peak}


def _windows_rss() -> Dict[str, Optional[int]]:
    rss = peak = None
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        psapi = ctypes.WinDLL("psapi.dll")
        kernel32 = ctypes.WinDLL("kernel32.dll")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        handle = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb)
        if ok:
            rss = int(counters.WorkingSetSize)
            peak = int(counters.PeakWorkingSetSize)
    except Exception:
        pass
    return {"rss_bytes": rss, "peak_rss_bytes": peak}


def measure_resources() -> Dict[str, Any]:
    """Return {rss_bytes, peak_rss_bytes, platform, source}. Unavailable -> None."""
    plat = platform.system()
    if plat == "Windows":
        vals = _windows_rss()
        source = "psapi.GetProcessMemoryInfo"
    else:
        vals = _unix_rss()
        source = "resource.getrusage+/proc/self/status"
    return {
        "rss_bytes": vals["rss_bytes"],
        "peak_rss_bytes": vals["peak_rss_bytes"],
        "platform": plat,
        "source": source,
    }
