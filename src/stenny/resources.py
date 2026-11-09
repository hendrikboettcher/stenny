"""Is there room for this work? Disk and memory, so a full computer costs a live draft or a wait, never a lecture.
macOS reports its memory pressure itself (sysctl, vm_stat); Windows' memory status (GlobalMemoryStatusEx) is read
into the same terms."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

GB = 1024 ** 3
MB = 1024 ** 2
DISK_TO_START = int(1.5 * GB)  # a 4-hour lecture with a backup microphone is about 1 GB, plus its final pass
DISK_LOW = 500 * MB            # the backup microphone stops, so the main recording lasts longer
DISK_FULL = 100 * MB           # stop cleanly while what was recorded can still be saved
# Measured on a 137-minute lecture (16 GB M4): 4.9 GB peak footprint. Less than that free can still work, as
# macOS compresses and swaps, but below this it waits instead of slowing the Mac down.
MEMORY_FOR_FINAL_PASS = 4 * GB
PRESSURE_NORMAL = 1
PRESSURE_WARNING = 2
PRESSURE_CRITICAL = 4
# Windows has no pressure level of its own to read; its memory load (% of physical memory in use) stands in for it.
WINDOWS_LOAD_WARNING = 90
WINDOWS_LOAD_CRITICAL = 95


def free_disk(path: Path) -> int:
    return shutil.disk_usage(path).free


def memory_pressure() -> int:
    """The system's verdict: 1 normal, 2 warning, 4 critical."""
    if sys.platform == "win32":
        status = _windows_memory_status()
        if status is None:
            return PRESSURE_NORMAL
        load = status.dwMemoryLoad
        return PRESSURE_CRITICAL if load >= WINDOWS_LOAD_CRITICAL else \
            PRESSURE_WARNING if load >= WINDOWS_LOAD_WARNING else PRESSURE_NORMAL
    if sys.platform != "darwin":
        return PRESSURE_NORMAL
    p = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return int(p.stdout)
    except ValueError:
        return PRESSURE_NORMAL


def available_memory() -> int:
    """Memory the system can hand out without swapping: free, plus what it only keeps as a cache."""
    if sys.platform == "win32":
        status = _windows_memory_status()
        return status.ullAvailPhys if status is not None else MEMORY_FOR_FINAL_PASS  # free + standby (cache)
    if sys.platform != "darwin":
        return MEMORY_FOR_FINAL_PASS
    out = subprocess.run(["vm_stat"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    page = re.search(r"page size of (\d+) bytes", out)
    pages = sum(int(m.group(1)) for m in re.finditer(r"Pages (?:free|inactive|speculative|purgeable):\s+(\d+)", out))
    return pages * int(page.group(1)) if page else MEMORY_FOR_FINAL_PASS


def _windows_memory_status():
    """Windows' MEMORYSTATUSEX, or None if it can't be read."""
    import ctypes
    from ctypes import wintypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status


def enough_memory_for_final_pass() -> bool:
    return memory_pressure() == PRESSURE_NORMAL and available_memory() >= MEMORY_FOR_FINAL_PASS
