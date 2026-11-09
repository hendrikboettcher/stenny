"""Every test runs in a home folder of its own: nothing reads or writes the real ~/Lectures Data, the real Stenny
folder or Stenny's app-data places (paths.py computes them when it's imported, so this runs first)."""

import os
import tempfile
from pathlib import Path

_home = Path(tempfile.mkdtemp(prefix="stenny-tests-"))
os.environ["HOME"] = os.environ["USERPROFILE"] = str(_home)
os.environ["STENNY_HOME"] = str(_home / "Stenny")
for name in ("STENNY_CONFIG", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
    os.environ.pop(name, None)


import pytest  # noqa: E402


@pytest.fixture
def places(tmp_path, monkeypatch):
    """Stenny's own places (paths.py) and the older layouts' folders, all under tmp_path: <tmp>/home is the home
    folder, <tmp>/app the app-data places, <tmp>/home/Lectures Data the layout until 0.4."""
    from types import SimpleNamespace

    from stenny import config, jobs, paths
    home, app = tmp_path / "home", tmp_path / "app"
    values = {
        "CONFIG_DIR": app / "config", "DATA_DIR": app / "data", "LOGS_DIR": app / "logs", "CACHE_DIR": app / "cache",
        "CONFIG_PATH": app / "config" / "config.toml", "CONSENT_PATH": app / "config" / "consent.json",
        "QUEUE_DIR": app / "data" / "Queue", "SOCKET": paths.control_address(app / "data"),
        "SHARED_DIR": app / "cache" / "Shared",
        "LEGACY_HOME": home / "Lectures Data", "LEGACY_SYSTEM_DIR": home / "Lectures Data" / "_System",
        "LEGACY_CONFIG_DIR": home / ".config/lecture", "LEGACY_STATE_DIR": home / ".local/share/lecture",
        "LEGACY_AUDIO_DIR": home / "Lectures Audio",
    }
    for name, value in values.items():
        monkeypatch.setattr(paths, name, value)
    monkeypatch.setattr(config, "DEFAULT_PATH", values["CONFIG_PATH"])
    monkeypatch.setattr(jobs, "QUEUE_DIR", values["QUEUE_DIR"])
    monkeypatch.setattr(jobs, "LOGS_DIR", values["LOGS_DIR"])
    monkeypatch.setattr(jobs, "AGENT_PATH", home / "Library/LaunchAgents/local.stenny.queue.plist")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(paths, "_windows_documents", lambda: str(home / "Documents"))  # not the runner's own
    monkeypatch.setenv("HOME", str(home))  # "~" in config.toml
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("STENNY_HOME", raising=False)
    monkeypatch.delenv("STENNY_CONFIG", raising=False)
    home.mkdir()
    return SimpleNamespace(home=home, app=app, **{k.lower(): v for k, v in values.items()})
