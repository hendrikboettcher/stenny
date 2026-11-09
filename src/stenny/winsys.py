"""Windows' own calls for what macOS does with nice/taskpolicy, pmset, caffeinate and vm_stat (issue #2): the
process's priority, AC power, keeping the PC awake, free memory. Standard library only (ctypes and kernel32), so
nothing is installed for it. Only jobs.py and resources.py call these, and only on Windows; the tests replace
`kernel32()` with a fake to run them anywhere."""

import ctypes
import functools
import threading

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
IDLE_PRIORITY_CLASS = 0x00000040
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001             # no idle sleep; the display may still turn off (caffeinate -i)
CREATE_NO_WINDOW = 0x08000000               # a console program without a console window


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_uint32), ("BatteryFullLifeTime", ctypes.c_uint32)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


@functools.lru_cache(maxsize=1)
def kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = ctypes.c_void_p  # a pseudo handle (-1): 64 bits, not ctypes' default int
    k.GetCurrentProcess.argtypes = []
    k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k.SetPriorityClass.restype = ctypes.c_int
    k.GetSystemPowerStatus.argtypes = [ctypes.POINTER(SYSTEM_POWER_STATUS)]
    k.GetSystemPowerStatus.restype = ctypes.c_int
    k.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
    k.GlobalMemoryStatusEx.restype = ctypes.c_int
    k.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    k.SetThreadExecutionState.restype = ctypes.c_uint32
    return k


def set_priority(mode: str) -> None:
    """"low": below normal, like nice 10. "background": idle, like taskpolicy -b. The class carries over to the
    programs this process starts (ffmpeg, claude). Not Windows' background mode (PROCESS_MODE_BACKGROUND_BEGIN):
    it would lower disk and memory priority too, but then the programs it starts run at normal priority (Windows
    CI showed it), which is worse for a lecture's final pass than a normal disk priority."""
    k = kernel32()
    me = k.GetCurrentProcess()
    if mode == "low":
        k.SetPriorityClass(me, BELOW_NORMAL_PRIORITY_CLASS)
    elif mode == "background":
        k.SetPriorityClass(me, IDLE_PRIORITY_CLASS)


def power() -> tuple[bool, int | None]:
    """(plugged in, battery percent). A PC without a battery, or unknown (255), counts as plugged in, as on the Mac;
    the percent is None without a battery (BatteryFlag 128) or when Windows doesn't know it (255)."""
    status = SYSTEM_POWER_STATUS()
    if not kernel32().GetSystemPowerStatus(ctypes.byref(status)):
        return True, None
    no_battery = status.BatteryFlag == 128 or status.BatteryLifePercent == 255
    return status.ACLineStatus != 0, None if no_battery else status.BatteryLifePercent


def on_ac_power() -> bool:
    return power()[0]


def memory() -> MEMORYSTATUSEX | None:
    """dwMemoryLoad: percent in use; ullAvailPhys: what can be handed out without paging (free and standby, the
    cache), like free plus inactive on the Mac. None if Windows didn't answer."""
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    return status if kernel32().GlobalMemoryStatusEx(ctypes.byref(status)) else None


class Awake(threading.Thread):
    """Keeps the PC from idle sleep. SetThreadExecutionState holds only while the thread that set it lives, so it
    gets a thread of its own that lives as long as the process (a daemon: the request goes with the process, as
    `caffeinate -w <pid>` does on the Mac). Closing a laptop's lid still sleeps."""

    def __init__(self):
        super().__init__(name="stenny-keep-awake", daemon=True)
        self.ready, self.done, self.ok = threading.Event(), threading.Event(), False

    def run(self) -> None:
        k = kernel32()
        self.ok = bool(k.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
        self.ready.set()
        self.done.wait()
        k.SetThreadExecutionState(ES_CONTINUOUS)

    def terminate(self) -> None:
        """Lets the PC sleep again (like ending caffeinate)."""
        self.done.set()


_awake: Awake | None = None
_awake_lock = threading.Lock()


def keep_awake() -> Awake:
    """One request per process, however often it's asked for."""
    global _awake
    with _awake_lock:
        if _awake is None or not _awake.is_alive():
            _awake = Awake()
            _awake.start()
            _awake.ready.wait(5)
        return _awake
