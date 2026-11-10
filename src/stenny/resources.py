"""Is there room for this work? Disk and memory, so a full Mac costs a live draft or a wait, never a lecture."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import winsys

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
# Windows has no verdict like macOS's; the share of memory in use stands in for it. From about 90 % Windows trims
# and pages out what apps keep in memory. A first guess, not yet tuned on a Windows PC (#2).
WINDOWS_LOAD_WARNING = 90
WINDOWS_LOAD_CRITICAL = 97


def free_disk(path: Path) -> int:
    return shutil.disk_usage(path).free


def memory_pressure() -> int:
    """macOS's own verdict: 1 normal, 2 warning, 4 critical. On Windows the same levels from the memory load."""
    if sys.platform == "win32":
        status = winsys.memory()
        load = status.dwMemoryLoad if status else 0
        return PRESSURE_CRITICAL if load >= WINDOWS_LOAD_CRITICAL else \
            PRESSURE_WARNING if load >= WINDOWS_LOAD_WARNING else PRESSURE_NORMAL
    p = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return int(p.stdout)
    except ValueError:
        return PRESSURE_NORMAL


def available_memory() -> int:
    """Memory macOS can hand out without swapping: free, plus what it only keeps as a cache (Windows: available)."""
    if sys.platform == "win32":
        status = winsys.memory()
        return status.ullAvailPhys if status else MEMORY_FOR_FINAL_PASS
    out = subprocess.run(["vm_stat"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    page = re.search(r"page size of (\d+) bytes", out)
    pages = sum(int(m.group(1)) for m in re.finditer(r"Pages (?:free|inactive|speculative|purgeable):\s+(\d+)", out))
    return pages * int(page.group(1)) if page else MEMORY_FOR_FINAL_PASS


# The live draft's Whisper model (large-v3-turbo through mlx) takes ~2.5 GB while loaded (measured on battery, #13).
# On a 16 GB Mac with a second account logged in, loading it alone turns macOS's verdict to "warning" with 40-60 %
# still free; pausing on that unloaded the model, the verdict went back to normal, the draft came back two minutes
# later and paused again. So "warning" pauses the draft only when memory really runs short, and it comes back only
# once the model fits again with room to spare.
DRAFT_MEMORY_LOW = 1 * GB      # less available than this, with the verdict at "warning": the draft pauses
DRAFT_MODEL_MEMORY = 3 * GB    # what the model needs back (2.5 GB measured, plus some)


def draft_memory_low(paused: bool) -> bool:
    """Whether the live draft has to (stay) paused for memory. "critical" always pauses it. Running, it pauses at
    "warning" only with less than DRAFT_MEMORY_LOW available; paused (the model unloaded), it stays paused until
    the model fits again: DRAFT_MEMORY_LOW plus DRAFT_MODEL_MEMORY available."""
    pressure = memory_pressure()
    if pressure >= PRESSURE_CRITICAL:
        return True
    if pressure == PRESSURE_NORMAL and not paused:
        return False
    need = DRAFT_MEMORY_LOW + (DRAFT_MODEL_MEMORY if paused else 0)
    return available_memory() < need


def enough_memory_for_final_pass() -> bool:
    return memory_pressure() == PRESSURE_NORMAL and available_memory() >= MEMORY_FOR_FINAL_PASS
