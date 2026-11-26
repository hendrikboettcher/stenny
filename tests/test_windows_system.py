import json
"""The engine's system calls on Windows (#2): priority, AC power, keeping awake, memory, the queue in Task Scheduler,
opening Claude. Most tests pretend to be on Windows with a fake kernel32, so they run on a Mac too; the ones marked
`windows` call the real Windows functions (CI's windows-latest)."""

import os
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from stenny import app_live, claude_setup, cli, i18n, jobs, resources, winsys

windows = pytest.mark.skipif(sys.platform != "win32", reason="calls Windows itself")
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class FakeKernel32:
    """kernel32 as winsys calls it: records what was asked, answers like Windows."""

    def __init__(self):
        self.priority, self.awake = [], []
        self.ac, self.load, self.avail, self.total, self.answers = 1, 40, 6 * resources.GB, 16 * resources.GB, True
        self.flag, self.battery = 128, 255  # no battery: a desktop PC

    def GetCurrentProcess(self):
        return 2 ** 64 - 1  # the pseudo handle, as c_void_p returns it

    def SetPriorityClass(self, handle, cls):
        assert handle == 2 ** 64 - 1
        self.priority.append(cls)
        return 1

    def GetSystemPowerStatus(self, ref):
        ref._obj.ACLineStatus, ref._obj.BatteryFlag, ref._obj.BatteryLifePercent = self.ac, self.flag, self.battery
        return int(self.answers)

    def GlobalMemoryStatusEx(self, ref):
        status = ref._obj
        assert status.dwLength == 64  # Windows refuses the call without it
        status.dwMemoryLoad, status.ullAvailPhys, status.ullTotalPhys = self.load, self.avail, self.total
        return int(self.answers)

    def SetThreadExecutionState(self, flags):
        self.awake.append((flags, threading.current_thread()))
        return 0x80000000


@pytest.fixture
def win(monkeypatch):
    """Pretend to be on Windows."""
    k = FakeKernel32()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(winsys, "kernel32", lambda: k)
    monkeypatch.setattr(winsys, "_awake", None)
    monkeypatch.setattr(os, "execvpe", lambda *a: pytest.fail("no re-exec on Windows"))
    monkeypatch.setattr(os, "nice", lambda n: pytest.fail("no nice on Windows"), raising=False)  # none on Windows
    return k


# ---------------------------------------------------------------- priority

def test_low_priority_is_below_normal_without_a_re_exec(win, monkeypatch):
    monkeypatch.setenv("STENNY_PRIORITY", "")
    jobs.apply_priority("low")
    assert win.priority == [winsys.BELOW_NORMAL_PRIORITY_CLASS]
    assert os.environ["STENNY_PRIORITY"] == "low"
    jobs.apply_priority("low")  # once
    assert len(win.priority) == 1


def test_background_is_idle(win, monkeypatch):
    monkeypatch.setenv("STENNY_PRIORITY", "")
    jobs.apply_priority("background")
    assert win.priority == [winsys.IDLE_PRIORITY_CLASS]


def test_normal_priority_changes_nothing(win, monkeypatch):
    monkeypatch.setenv("STENNY_PRIORITY", "")
    jobs.apply_priority("normal")
    jobs.lower_own_priority("normal")
    assert win.priority == []


def test_the_final_pass_after_a_recording_lowers_its_own_process(win):
    jobs.lower_own_priority("low")
    jobs.lower_own_priority("background")
    assert win.priority == [winsys.BELOW_NORMAL_PRIORITY_CLASS, winsys.IDLE_PRIORITY_CLASS]


# ---------------------------------------------------------------- power, sleep, memory

def test_ac_power(win):
    assert jobs.on_ac_power()
    win.ac = 0
    assert not jobs.on_ac_power()
    win.ac = 255  # unknown: counts as plugged in, as on the Mac
    assert jobs.on_ac_power()
    win.ac, win.answers = 0, False  # Windows didn't answer
    assert jobs.on_ac_power()


def test_power_and_battery(win):
    assert jobs.power() == jobs.Power(True, None)                     # a desktop PC: no battery
    win.ac, win.flag, win.battery = 0, 0, 82
    assert jobs.power() == jobs.Power(False, 82)                      # a laptop on battery
    win.ac, win.battery = 1, 15
    assert jobs.power() == jobs.Power(True, 15)
    win.battery = 255                                                 # Windows doesn't know the charge
    assert jobs.power() == jobs.Power(True, None)
    win.answers = False
    assert jobs.power() == jobs.Power(True, None)


def test_keep_awake_holds_from_a_thread_of_its_own(win):
    awake = jobs.keep_awake()
    assert awake.ok and awake.is_alive() and awake.daemon
    [(flags, thread)] = win.awake
    assert flags == winsys.ES_CONTINUOUS | winsys.ES_SYSTEM_REQUIRED
    assert thread is awake and thread is not threading.main_thread()
    assert jobs.keep_awake() is awake and len(win.awake) == 1  # one request per process
    awake.terminate()
    awake.join(5)
    assert win.awake[-1] == (winsys.ES_CONTINUOUS, awake)  # released by the same thread


def test_memory_load_stands_in_for_macos_pressure_levels(win):
    for load, level in [(40, 1), (89, 1), (90, 2), (96, 2), (97, 4), (100, 4)]:
        win.load = load
        assert resources.memory_pressure() == level, load
    assert resources.available_memory() == 6 * resources.GB
    win.load = 50
    assert resources.enough_memory_for_final_pass()
    win.avail = 2 * resources.GB
    assert not resources.enough_memory_for_final_pass()
    win.answers = False  # Windows didn't answer: don't hold the work up
    assert resources.memory_pressure() == resources.PRESSURE_NORMAL
    assert resources.available_memory() == resources.MEMORY_FOR_FINAL_PASS


# ---------------------------------------------------------------- the queue in Task Scheduler

def _task(xml: str) -> ET.Element:
    return ET.fromstring(xml.encode("utf-16"))


def test_the_task_runs_every_10_minutes_as_the_user_without_windows_power_defaults():
    task = _task(jobs.task_xml(r"C:\Users\Ana & Bo\Stenny\Scripts\pythonw.exe", "-m stenny queue run --scheduled",
                               user="PC\\Ana"))
    text = lambda path: task.find(path, NS).text  # noqa: E731
    assert text("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval") == "PT10M"
    assert text("t:Principals/t:Principal/t:UserId") == "PC\\Ana"
    assert text("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"  # only while logged on, like launchd
    assert text("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"  # no admin rights
    # power and night hours are decided in Python from the config, so Settings needs no new task
    assert text("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert text("t:Settings/t:RunOnlyIfIdle") == "false"
    assert text("t:Settings/t:ExecutionTimeLimit") == "PT0S"  # a long lecture's pass isn't killed after 72 h
    assert text("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text("t:Actions/t:Exec/t:Command") == r"C:\Users\Ana & Bo\Stenny\Scripts\pythonw.exe"
    assert text("t:Actions/t:Exec/t:Arguments") == "-m stenny queue run --scheduled"
    assert task.find("t:Principals/t:Principal/t:UserId", NS) is not None
    assert _task(jobs.task_xml("stenny.exe", "queue run")).find("t:Principals/t:Principal/t:UserId", NS) is None


def test_the_task_starts_pythonw_so_no_console_window_opens(tmp_path, monkeypatch):
    python = tmp_path / "Scripts/python.exe"
    python.parent.mkdir()
    python.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr("shutil.which", lambda name: str(tmp_path / "Scripts/stenny.exe"))
    assert jobs.task_action() == (str(tmp_path / "Scripts/stenny.exe"), "queue run --scheduled")
    (tmp_path / "Scripts/pythonw.exe").write_bytes(b"")
    assert jobs.task_action() == (str(tmp_path / "Scripts/pythonw.exe"), "-m stenny queue run --scheduled")
    assert jobs.task_action(exe="D:/stenny.exe") == ("D:/stenny.exe", "queue run --scheduled")


@pytest.fixture
def schtasks(win, places, monkeypatch):
    """A fake schtasks: the commands it got, and the task XML it was given."""
    calls = SimpleNamespace(args=[], xml=None, tasks=set(), fail=None)
    monkeypatch.setattr(jobs, "LOGS_DIR", places.logs_dir)
    monkeypatch.setenv("USERDOMAIN", "LAPTOP")
    monkeypatch.setenv("USERNAME", "ana")
    monkeypatch.setattr("shutil.which", lambda name: r"C:\Stenny\Scripts\stenny.exe")  # Windows' own can't run here

    def run(*args):
        calls.args.append(args)
        if calls.fail:
            return subprocess.CompletedProcess(args, 1, "", calls.fail)
        if args[0] == "/Create":
            calls.xml = Path(args[args.index("/XML") + 1]).read_text(encoding="utf-16")
            calls.tasks.add(args[2])
        elif args[0] == "/Delete":
            calls.tasks.discard(args[2])
        return subprocess.CompletedProcess(args, 0 if args[0] != "/Query" or args[2] in calls.tasks else 1, "", "")
    monkeypatch.setattr(jobs, "_schtasks", run)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail(f"not on Windows: {a}"))
    return calls


def test_schedule_install_and_uninstall_use_task_scheduler(schtasks, places):
    assert jobs.install_agent() == "Stenny Queue"
    assert schtasks.args[0][:3] == ("/Create", "/TN", "Stenny Queue") and schtasks.args[0][-1] == "/F"
    assert "<UserId>LAPTOP\\ana</UserId>" in schtasks.xml and "queue run --scheduled" in schtasks.xml
    assert not list(places.cache_dir.glob("*.xml"))  # the XML was only for schtasks
    assert not jobs.AGENT_PATH.exists()  # no launchd agent
    assert jobs.uninstall_agent() is True
    assert schtasks.args[-1] == ("/Delete", "/TN", "Stenny Queue", "/F")
    assert jobs.uninstall_agent() is False


def test_schedule_command_on_windows(schtasks, monkeypatch, capsys):
    cfg = SimpleNamespace(require_ac_for_queue=True, night_only=False)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    cli.cmd_schedule(SimpleNamespace(action="install"))
    out = capsys.readouterr().out
    assert out.startswith('Added "Stenny Queue" to Task Scheduler: checks the queue every 10 min (runs only on AC '
                          'power). Log: ')
    cfg.night_only = True
    cli.cmd_schedule(SimpleNamespace(action="install"))
    assert "(runs only on AC power and at night)" in capsys.readouterr().out
    cli.cmd_schedule(SimpleNamespace(action="uninstall"))
    assert capsys.readouterr().out == "Removed.\n"
    schtasks.fail = "ERROR: Access is denied."
    with pytest.raises(SystemExit, match="Task Scheduler: ERROR: Access is denied."):
        cli.cmd_schedule(SimpleNamespace(action="install"))


def test_a_run_from_pythonw_goes_on_as_a_console_program_without_a_window(win, places, monkeypatch):
    monkeypatch.setattr(jobs, "LOGS_DIR", places.logs_dir)
    monkeypatch.setattr(sys, "executable", r"C:\Stenny\Scripts\pythonw.exe")
    monkeypatch.setattr(sys, "argv", ["-m", "queue", "run", "--scheduled"])
    started = []

    def run(cmd, **kw):
        started.append((cmd, kw))
        kw["stdout"].write(b"[01:00:00] done\n")
        return subprocess.CompletedProcess(cmd, 3)
    monkeypatch.setattr(subprocess, "run", run)
    jobs.run_without_console()  # a console: nothing to do
    assert started == []
    monkeypatch.setattr(sys, "stdout", None)  # pythonw
    with pytest.raises(SystemExit) as exit_:
        jobs.run_without_console()
    assert exit_.value.code == 3
    [(cmd, kw)] = started
    assert cmd == [str(Path(r"C:\Stenny\Scripts\pythonw.exe").with_name("python.exe")), "-m", "stenny", "queue",
                   "run", "--scheduled"]
    assert kw["creationflags"] == winsys.CREATE_NO_WINDOW and kw["env"]["PYTHONIOENCODING"] == "utf-8"
    assert (places.logs_dir / "queue.log").read_bytes() == b"[01:00:00] done\n"


def test_on_a_mac_a_run_never_starts_again(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "stdout", None)
    assert jobs.run_without_console() is None


# ---------------------------------------------------------------- words, Claude, feedback

def test_windows_messages_say_pc(monkeypatch):
    from stenny.render import draft_status
    monkeypatch.setattr(sys, "platform", "win32")
    assert draft_status("queued", "battery") == \
        "The recording is finished; its transcript is made once the PC is plugged in."
    i18n.set_language("de")
    try:
        assert i18n.tr(draft_status("queued", "battery")).endswith("sobald dein PC am Netzteil ist.")
        assert i18n.tr("final pass queued (on battery)") == "Transkript folgt, sobald der PC am Netzteil ist"
        monkeypatch.setattr(sys, "platform", "darwin")
        assert i18n.tr("final pass queued (on battery)") == "Transkript folgt, sobald der Mac am Netzteil ist"
        assert i18n.tr(draft_status("queued", "battery")).endswith("sobald dein Mac am Netzteil ist.")
    finally:
        i18n.set_language("en")


def test_windows_german_variants_replace_existing_messages():
    assert set(i18n.DE_WINDOWS) <= set(i18n.DE)
    assert all("Mac" not in de for de in i18n.DE_WINDOWS.values())


@pytest.fixture
def claude_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    opened = []
    monkeypatch.setattr(os, "startfile", lambda target: opened.append(str(target)), raising=False)
    return SimpleNamespace(local=tmp_path / "Local", roaming=tmp_path / "Roaming", opened=opened)


def test_claude_desktop_from_its_installer(claude_windows):
    assert not claude_setup.claude_desktop_installed()
    exe = claude_windows.local / "AnthropicClaude/claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert claude_setup.claude_desktop_installed() and claude_setup.claude_desktop_exe() == exe
    assert claude_setup.claude_data_dir() == claude_windows.roaming / "Claude"
    claude_setup.open_claude(None)
    assert claude_windows.opened == [str(exe)]


def test_claude_desktop_as_an_app_package(claude_windows, monkeypatch):
    package = claude_windows.local / "Packages/Claude_pzs8sxrjxfjjc"
    (package / "LocalCache/Roaming/Claude").mkdir(parents=True)
    assert claude_setup.claude_desktop_installed() and claude_setup.claude_desktop_exe() is None
    assert claude_setup.claude_data_dir() == package / "LocalCache/Roaming/Claude"
    claude_setup.open_claude(None)
    assert claude_windows.opened == ["claude://"]
    (claude_windows.roaming / "Claude").mkdir(parents=True)  # a real folder there wins
    assert claude_setup.claude_data_dir() == claude_windows.roaming / "Claude"

    def refuse(target):
        raise OSError("No application is associated with the specified file")
    monkeypatch.setattr(os, "startfile", refuse, raising=False)
    with pytest.raises(claude_setup.SetupError, match="Couldn't open Claude: No application"):
        claude_setup.open_claude(None)


def test_install_starts_claude_with_the_file_once_it_isnt_running(claude_windows, monkeypatch, tmp_path):
    """A running Claude drops a file it's handed (only a cold start reads it): while it runs, Install answers
    "waiting" and starts nothing; once it's gone, Claude is started with Stenny.mcpb, as a double-click would."""
    exe = claude_windows.local / "AnthropicClaude/claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    file = tmp_path / "Stenny.mcpb"
    file.write_bytes(b"zip")
    monkeypatch.setattr(claude_setup, "connector_file", lambda: file)
    started = []
    monkeypatch.setattr(claude_setup.subprocess, "Popen", lambda args, **kw: started.append(args))
    running = [str(claude_windows.local / "AnthropicClaude/app-1.2.3/claude.exe")]
    monkeypatch.setattr(claude_setup, "_windows_process_paths", lambda image: list(running))

    assert claude_setup.install_connector(None) == {"waiting": True}
    assert started == []
    running[:] = [str(Path.home() / ".local/bin/claude.exe")]   # Claude Code isn't the desktop app
    assert claude_setup.install_connector(None) == {"opened": True}
    assert started == [[str(exe), str(file)]]


def test_install_on_the_store_app_asks_for_the_path(claude_windows, monkeypatch, tmp_path):
    (claude_windows.local / "Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude").mkdir(parents=True)
    file = tmp_path / "Stenny.mcpb"
    file.write_bytes(b"zip")
    monkeypatch.setattr(claude_setup, "connector_file", lambda: file)
    monkeypatch.setattr(claude_setup, "_windows_process_paths", lambda image: [])
    assert claude_setup.install_connector(None) == {"manual": True}
    # the app knows before Connect is clicked (connector_status' "store"): it shows how to paste the path right away
    assert claude_setup.claude_desktop_store()
    exe = claude_windows.local / "AnthropicClaude/claude.exe"   # the installer's Claude too: that one takes the file
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert not claude_setup.claude_desktop_store()


def test_which_claude_exe_is_the_desktop_app(claude_windows):
    local = claude_windows.local
    assert claude_setup._is_claude_desktop(str(local / "AnthropicClaude/app-1.0.0/claude.exe"))
    assert claude_setup._is_claude_desktop(str(local / "Programs/Claude/Claude.exe"))
    assert claude_setup._is_claude_desktop(r"C:\Program Files\WindowsApps\Claude_1.0.0.0_x64__pzs8sxrjxfjjc\app\claude.exe")
    assert not claude_setup._is_claude_desktop(r"C:\Users\s\.local\bin\claude.exe")
    assert not claude_setup._is_claude_desktop(str(local / "AnthropicClaudeOld/claude.exe"))


@windows
def test_running_programs_are_found_by_name():
    paths = claude_setup._windows_process_paths("python.exe")
    assert any(Path(p).name.lower() == "python.exe" and Path(p).is_file() for p in paths)
    assert claude_setup._windows_process_paths("no-such-program.exe") == []


def test_feedback_names_the_pc(win, monkeypatch):
    import platform
    import types
    bios = {"SystemManufacturer": "LENOVO ", "SystemProductName": "21HD"}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    winreg = types.SimpleNamespace(HKEY_LOCAL_MACHINE=0, OpenKey=lambda root, path: Key(),
                                   QueryValueEx=lambda key, name: (bios[name], 1))
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(platform, "win32_ver", lambda: ("11", "10.0.22631", "SP0", "Multiprocessor Free"))
    assert app_live.system_details() == "LENOVO 21HD, 16 GB, Windows 11 (10.0.22631)"


# ---------------------------------------------------------------- the real thing (Windows CI)

def _priority_of_child(mode: str) -> str:
    """A fresh Python lowers itself and starts another one; both report their priority class."""
    code = ("import ctypes, subprocess, sys; from stenny import winsys; winsys.set_priority(sys.argv[1]); "
            "k = ctypes.windll.kernel32; k.GetCurrentProcess.restype = ctypes.c_void_p; "
            "own = k.GetPriorityClass(ctypes.c_void_p(k.GetCurrentProcess())); "
            "child = subprocess.run([sys.executable, '-c', 'import ctypes; k = ctypes.windll.kernel32; "
            "k.GetCurrentProcess.restype = ctypes.c_void_p; "
            "print(k.GetPriorityClass(ctypes.c_void_p(k.GetCurrentProcess())))'], capture_output=True, text=True); "
            "print(own, child.stdout.strip())")
    return subprocess.run([sys.executable, "-c", code, mode], capture_output=True, text=True, check=True).stdout.split()


@windows
def test_real_priority_classes_and_children_inherit_them():
    assert _priority_of_child("low") == [str(winsys.BELOW_NORMAL_PRIORITY_CLASS)] * 2
    assert _priority_of_child("background") == [str(winsys.IDLE_PRIORITY_CLASS)] * 2


@windows
def test_real_power_memory_and_keep_awake():
    assert isinstance(winsys.on_ac_power(), bool)
    status = winsys.memory()
    assert status and 0 < status.dwMemoryLoad <= 100 and 0 < status.ullAvailPhys <= status.ullTotalPhys
    awake = winsys.keep_awake()
    assert awake.ok and awake.is_alive()
    awake.terminate()
    awake.join(5)
    assert not awake.is_alive()


def test_the_extension_is_found_in_the_app_packages_folder_too(claude_windows, monkeypatch):
    """Claude from the Store keeps its extensions in its package's folder; a %APPDATA%\\Claude folder can be there
    as well (an older Claude, Stenny 0.5.7's config). Stenny counts as connected wherever Claude put it."""
    (claude_windows.roaming / "Claude").mkdir(parents=True)
    package = claude_windows.local / "Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude"
    ext = package / "Claude Extensions/local.mcpb.hendrik-boettcher.stenny"
    ext.mkdir(parents=True)
    assert claude_setup.installed_extension() is None
    (ext / "manifest.json").write_text('{"name": "stenny", "version": "0.5.14"}', encoding="utf-8")
    assert claude_setup.installed_extension() == {"id": ext.name, "version": "0.5.14", "enabled": True}
    monkeypatch.setattr(claude_setup, "find_claude", lambda: None)
    assert claude_setup.connector_status(None)["installed"] is True


def test_claude_from_the_store_used_the_connector(claude_windows, monkeypatch):
    """The Store app runs `stenny mcp` inside its package: the note it writes lands in the package's own copy of
    AppData, which the app only finds by looking there."""
    data = claude_windows.local / "Stenny"
    data.mkdir(parents=True)
    monkeypatch.setattr(claude_setup.paths, "DATA_DIR", data)
    package = claude_windows.local / "Packages/Claude_pzs8sxrjxfjjc"
    (package / "LocalCache/Roaming/Claude").mkdir(parents=True)
    assert claude_setup.connector_used() is None

    private = package / "LocalCache/Local/Stenny" / claude_setup.CONNECTOR_USED
    private.parent.mkdir(parents=True)
    private.write_text('{"at": "2026-11-22T10:00:00"}', encoding="utf-8")
    assert claude_setup.connector_used() == "2026-11-22T10:00:00"
    # the newest of both counts
    (data / claude_setup.CONNECTOR_USED).write_text('{"at": "2026-11-22T09:00:00"}', encoding="utf-8")
    assert claude_setup.connector_used() == "2026-11-22T10:00:00"
    (data / claude_setup.CONNECTOR_USED).write_text('{"at": "2026-11-22T11:00:00"}', encoding="utf-8")
    assert claude_setup.connector_used() == "2026-11-22T11:00:00"


def _stenny_extension(folder, version="0.5.21"):
    ext = folder / "Claude Extensions/local.mcpb.hendrik-bttcher.stenny"
    ext.mkdir(parents=True)
    (ext / "manifest.json").write_text(f'{{"name": "stenny", "version": "{version}"}}', encoding="utf-8")
    return ext


def test_a_newer_claude_keeps_its_data_outside_its_package(claude_windows, monkeypatch):
    """Claude as an app package moves its data to %LOCALAPPDATA%\\Claude-Data\\<package name> (6.28899.6 does; seen
    on a tester's PC, where Stenny was in Claude but the checklist never ticked). The old private copy stays behind."""
    (claude_windows.local / "Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude").mkdir(parents=True)
    moved = claude_windows.local / "Claude-Data/claude_pzs8sxrjxfjjc"
    (claude_windows.local / "Claude-Data/~copying.claude_pzs8sxrjxfjjc").mkdir(parents=True)  # not Claude's folder yet
    moved.mkdir(parents=True)
    assert claude_setup.claude_data_dir() == moved
    assert claude_setup.installed_extension() is None
    ext = _stenny_extension(moved)
    assert claude_setup.installed_extension() == {"id": ext.name, "version": "0.5.21", "enabled": True}
    monkeypatch.setattr(claude_setup, "find_claude", lambda: None)
    assert claude_setup.connector_status(None)["installed"] is True


def test_an_older_claude_is_still_found_where_it_always_was(claude_windows):
    """A Claude that hasn't moved its data (not updated yet, or moved back) keeps it in the package's private copy or
    in %APPDATA%\\Claude: the extension counts there even when a Claude-Data folder is there as well."""
    (claude_windows.local / "Claude-Data/claude_pzs8sxrjxfjjc").mkdir(parents=True)
    package = claude_windows.local / "Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude"
    ext = _stenny_extension(package, "0.5.20")
    assert claude_setup.installed_extension()["version"] == "0.5.20"
    ext.rename(ext.parent / "elsewhere")
    _stenny_extension(claude_windows.roaming / "Claude", "0.5.19")
    assert claude_setup.installed_extension()["version"] == "0.5.19"


def test_claude_from_the_microsoft_store(claude_windows, monkeypatch):
    """The Store's Claude is the package AnthropicPBC.Claude_…, not Claude_…: installed, the paste-the-path way, and
    its running program counts as the desktop app."""
    assert not claude_setup.claude_desktop_installed()
    (claude_windows.local / "Packages/AnthropicPBC.Claude_fnn82j28hfe8t").mkdir(parents=True)
    assert claude_setup.claude_desktop_installed() and claude_setup.claude_desktop_store()
    exe = r"C:\Program Files\WindowsApps\AnthropicPBC.Claude_6.28899.6.0_x64__fnn82j28hfe8t\app\claude.exe"
    assert claude_setup._is_claude_desktop(exe)
    monkeypatch.setattr(claude_setup, "_windows_process_paths", lambda image: [exe])
    assert claude_setup.claude_desktop_version() == "store 6.28899.6.0"
    monkeypatch.setattr(claude_setup, "_windows_process_paths",
                        lambda image: [r"C:\Program Files\WindowsApps\Claude_6.28899.6.0_x64__pzs8sxrjxfjjc\app\claude.exe"])
    assert claude_setup.claude_desktop_version() == "store 6.28899.6.0"


def test_the_report_names_claudes_folders_and_reads_its_newest_log(claude_windows, monkeypatch, tmp_path):
    old = claude_windows.local / "Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude"
    moved = claude_windows.local / "Claude-Data/claude_pzs8sxrjxfjjc"
    for folder, line in ((old, "old: Handling DXT/MCPB file"), (moved, "new: Handling DXT/MCPB file")):
        (folder / "logs").mkdir(parents=True)
        (folder / "logs/main.log").write_text(line + "\n", encoding="utf-8")
    os.utime(old / "logs/main.log", (1_700_000_000, 1_700_000_000))
    _stenny_extension(moved)
    file = tmp_path / "Stenny.mcpb"
    file.write_bytes(b"zip")
    monkeypatch.setattr(claude_setup, "connector_file", lambda: file)
    monkeypatch.setattr(claude_setup, "_windows_process_paths", lambda image: [])
    report = claude_setup.connector_report("store")
    assert "Claude has Stenny: 0.5.21" in report
    assert f"Claude's folders: {moved}, {old}" in report
    assert "new: Handling DXT/MCPB file" in report and "old:" not in report
