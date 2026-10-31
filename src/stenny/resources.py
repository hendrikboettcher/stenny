"""Is there room for this work? Disk and memory, so a full Mac costs a live draft or a wait, never a lecture."""

import re
import shutil
import subprocess
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


def free_disk(path: Path) -> int:
    return shutil.disk_usage(path).free


def memory_pressure() -> int:
    """macOS's own verdict: 1 normal, 2 warning, 4 critical."""
    p = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True)
    try:
        return int(p.stdout)
    except ValueError:
        return PRESSURE_NORMAL


def available_memory() -> int:
    """Memory macOS can hand out without swapping: free, plus what it only keeps as a cache."""
    out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    page = re.search(r"page size of (\d+) bytes", out)
    pages = sum(int(m.group(1)) for m in re.finditer(r"Pages (?:free|inactive|speculative|purgeable):\s+(\d+)", out))
    return pages * int(page.group(1)) if page else MEMORY_FOR_FINAL_PASS


def enough_memory_for_final_pass() -> bool:
    return memory_pressure() == PRESSURE_NORMAL and available_memory() >= MEMORY_FOR_FINAL_PASS
