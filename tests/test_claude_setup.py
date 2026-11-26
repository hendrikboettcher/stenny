import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from stenny import app_server, claude_setup, keychain

posix = pytest.mark.skipif(sys.platform == "win32", reason="the fake `claude` is a shell script")


def cfg(backend):
    return SimpleNamespace(llm_backend=backend)


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A `claude` that answers `auth status` from a state file and records `mcp add`."""
    state = tmp_path / "state"
    state.write_text("in", encoding="utf-8")
    exe = tmp_path / "claude"
    exe.write_text(f"""#!/bin/sh
case "$1 $2" in
  "auth status") if [ "$(cat {state})" = in ]; then
      echo '{{"loggedIn": true, "email": "student@example.edu", "subscriptionType": "pro"}}'
    else echo '{{"loggedIn": false}}'; fi ;;
  "auth login") echo "Opening browser to sign in…"; echo login > {tmp_path}/login; if [ -n "$SLOW" ]; then sleep 30; fi ;;
  "mcp add") echo "$@" > {tmp_path}/added; echo '{{"mcpServers": {{"stenny": {{}}}}}}' > "$HOME/.claude.json" ;;
  "mcp remove") echo "$@" > {tmp_path}/removed; echo '{{"mcpServers": {{}}}}' > "$HOME/.claude.json" ;;
esac
""", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(claude_setup, "find_claude", lambda: str(exe))
    return SimpleNamespace(exe=exe, state=state, dir=tmp_path)


def test_off():
    assert claude_setup.ai_status(cfg("none")) == {"backend": "none", "state": "off"}


def test_claude_code_missing(monkeypatch):
    monkeypatch.setattr(claude_setup, "find_claude", lambda: None)
    assert claude_setup.ai_status(cfg("claude-code"))["state"] == "missing"


@posix
def test_claude_code_signed_in_and_out(fake_claude):
    status = claude_setup.ai_status(cfg("claude-code"))
    assert status == {"backend": "claude-code", "state": "ready", "account": "student@example.edu", "plan": "pro"}
    fake_claude.state.write_text("out", encoding="utf-8")
    assert claude_setup.ai_status(cfg("claude-code"))["state"] == "signed_out"


@posix
def test_claude_code_that_says_nothing_useful_is_unknown(fake_claude):
    fake_claude.exe.write_text("#!/bin/sh\necho not json\n", encoding="utf-8")
    assert claude_setup.ai_status(cfg("claude-code"))["state"] == "unknown"


def test_api_without_a_key(monkeypatch):
    monkeypatch.setattr(keychain, "api_key", lambda: None)
    assert claude_setup.ai_status(cfg("api")) == {"backend": "api", "state": "no_key"}


def test_api_key_states_never_show_the_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(keychain, "saved_key", lambda: "sk-ant-api03-secretsecret1234")
    monkeypatch.setattr(claude_setup, "check_key", lambda key: ("bad_key", None))
    status = claude_setup.ai_status(cfg("api"))
    assert status == {"backend": "api", "state": "bad_key", "source": "keychain", "hint": "…1234"}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-fromtheenv9876")
    monkeypatch.setattr(claude_setup, "check_key", lambda key: ("ready", None))
    status = claude_setup.ai_status(cfg("api"))
    assert status["source"] == "environment" and status["hint"] == "…9876" and status["state"] == "ready"
    assert "secret" not in json.dumps(status)


def test_a_key_is_saved_only_once_anthropic_accepts_it(monkeypatch):
    saved = []
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(keychain, "save_key", saved.append)
    monkeypatch.setattr(keychain, "saved_key", lambda: saved[-1] if saved else None)
    with pytest.raises(claude_setup.SetupError, match="sk-ant-"):
        claude_setup.set_api_key(cfg("api"), "hello")
    with pytest.raises(claude_setup.SetupError, match="Paste"):
        claude_setup.set_api_key(cfg("api"), "  ")
    monkeypatch.setattr(claude_setup, "check_key", lambda key: ("bad_key", None))
    with pytest.raises(claude_setup.SetupError, match="didn't accept"):
        claude_setup.set_api_key(cfg("api"), "sk-ant-api03-wrong")
    assert saved == []
    monkeypatch.setattr(claude_setup, "check_key", lambda key: ("ready", None))
    status = claude_setup.set_api_key(cfg("api"), " sk-ant-api03-\nright ")  # pasted with a line break
    assert saved == ["sk-ant-api03-right"] and status["state"] == "ready"


def test_the_environment_wins_over_the_keychain(monkeypatch):
    monkeypatch.setattr(keychain, "saved_key", lambda: "sk-ant-saved")
    monkeypatch.setenv("ANTHROPIC_API_KEY", " sk-ant-env ")
    assert keychain.api_key() == "sk-ant-env"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert keychain.api_key() == "sk-ant-saved"


def extension(data, folder, name, enabled=None):
    (data / "Claude Extensions" / folder).mkdir(parents=True)
    (data / "Claude Extensions" / folder / "manifest.json").write_text(json.dumps({"name": name, "version": "0.4.0"}), encoding="utf-8")
    if enabled is not None:
        (data / "Claude Extensions Settings").mkdir(exist_ok=True)
        (data / "Claude Extensions Settings" / f"{folder}.json").write_text(json.dumps({"isEnabled": enabled}), encoding="utf-8")


def test_finds_the_installed_extension(tmp_path):
    assert claude_setup.installed_extension(tmp_path) is None
    extension(tmp_path, "ant.dir.filesystem", "filesystem", True)
    assert claude_setup.installed_extension(tmp_path) is None
    extension(tmp_path, "local.mcpb.stenny", "stenny", False)
    assert claude_setup.installed_extension(tmp_path) == {"id": "local.mcpb.stenny", "version": "0.4.0", "enabled": False}


def test_an_extension_without_settings_is_on(tmp_path):
    extension(tmp_path, "local.mcpb.stenny", "stenny")
    assert claude_setup.installed_extension(tmp_path)["enabled"] is True


@posix
def test_adds_the_connector_to_claude_code_once(fake_claude, monkeypatch):
    monkeypatch.setenv("HOME", str(fake_claude.dir))
    monkeypatch.setattr(claude_setup, "claude_data_dir", lambda: fake_claude.dir / "Claude")
    assert claude_setup.connector_status(None)["code"] == {"available": True, "added": False}
    status = claude_setup.add_to_claude_code(None)
    assert status["code"]["added"] is True
    assert (fake_claude.dir / "added").read_text(encoding="utf-8").startswith("mcp add --scope user stenny -- ")
    assert (fake_claude.dir / "added").read_text(encoding="utf-8").rstrip().endswith(" mcp")
    (fake_claude.dir / "added").unlink()
    claude_setup.add_to_claude_code(None)
    assert not (fake_claude.dir / "added").exists()  # already there: not added twice


def test_installing_needs_the_file_and_claude(monkeypatch):
    monkeypatch.setattr(claude_setup, "connector_file", lambda: None)
    with pytest.raises(claude_setup.SetupError, match="Stenny.mcpb"):
        claude_setup.install_connector(None)


def test_app_server_reports_setup_errors_as_messages(monkeypatch, tmp_path):
    monkeypatch.setattr(keychain, "save_key", lambda key: None)
    reply = app_server.handle(SimpleNamespace(llm_backend="api"),
                              {"id": 7, "method": "set_api_key", "params": {"key": "nope"}})
    assert reply["id"] == 7 and "sk-ant-" in reply["error"]
    for method in ("ai_status", "set_api_key", "remove_api_key", "claude_sign_in", "connector_status",
                   "install_connector", "add_to_claude_code", "remove_from_claude_desktop"):
        assert method in app_server.METHODS


@posix
def test_signs_in_without_a_terminal_window(fake_claude, monkeypatch, capfd):
    """`claude auth login` runs in the background, its output kept off the app server's stdout (the replies)."""
    claude_setup.claude_sign_in(None)
    assert claude_setup._sign_in.wait(timeout=10) == 0
    assert (fake_claude.dir / "login").exists()
    assert "Opening browser" not in capfd.readouterr().out
    monkeypatch.setenv("SLOW", "1")           # a second click while the first still waits for the browser
    claude_setup.claude_sign_in(None)
    first = claude_setup._sign_in
    claude_setup.claude_sign_in(None)
    assert first.wait(timeout=10) != 0        # ended, so only one waits for the browser
    claude_setup._sign_in.kill()


@posix
def test_removes_the_connector_from_claude_code(fake_claude, monkeypatch):
    monkeypatch.setenv("HOME", str(fake_claude.dir))
    monkeypatch.setattr(claude_setup, "claude_data_dir", lambda: fake_claude.dir / "Claude")
    claude_setup.add_to_claude_code(None)
    status = claude_setup.remove_from_claude_code(None)
    assert status["code"]["added"] is False
    assert (fake_claude.dir / "removed").read_text(encoding="utf-8").strip() == "mcp remove --scope user stenny"


def test_the_connector_bundled_with_the_app_comes_first(tmp_path, monkeypatch):
    bundled = tmp_path / "Stenny.mcpb"
    bundled.write_bytes(b"zip")
    monkeypatch.setenv("STENNY_CONNECTOR", str(bundled))
    assert claude_setup.connector_file() == bundled
    monkeypatch.setenv("STENNY_CONNECTOR", str(tmp_path / "missing.mcpb"))
    assert claude_setup.connector_file() != tmp_path / "missing.mcpb"


def test_windows_knows_a_0_5_7_entry_and_takes_it_out(tmp_path, monkeypatch):
    """Stenny 0.5.7 wrote itself into claude_desktop_config.json on Windows: that entry is recognised, and taken out
    on request, everything else in the file as it was."""
    roaming = tmp_path / "Roaming/Claude"
    roaming.mkdir(parents=True)
    entry = {"command": "C:/Users/s/.local/bin/stenny.exe", "args": ["mcp"]}
    (roaming / "claude_desktop_config.json").write_text(json.dumps(
        {"preferences": {"quickEntry": True}, "mcpServers": {"other": {"command": "x"}, "stenny": entry}}), encoding="utf-8")
    file = tmp_path / "Stenny.mcpb"
    file.write_bytes(b"zip")
    monkeypatch.setattr(claude_setup, "_claude_config_dirs", lambda: [roaming])
    monkeypatch.setattr(claude_setup, "claude_data_dir", lambda: roaming)
    monkeypatch.setattr(claude_setup, "claude_desktop_installed", lambda: True)
    monkeypatch.setattr(claude_setup, "find_claude", lambda: None)
    monkeypatch.setattr(claude_setup, "connector_file", lambda: file)
    monkeypatch.setattr(claude_setup.sys, "platform", "win32")
    before = claude_setup.connector_status(None)
    assert before["installed"] is True and before["via"] == "config" and before["file"] == str(file)

    claude_setup.remove_from_desktop_config(None)
    kept = json.loads((roaming / "claude_desktop_config.json").read_text(encoding="utf-8"))
    assert kept == {"preferences": {"quickEntry": True}, "mcpServers": {"other": {"command": "x"}}}
    after = claude_setup.connector_status(None)
    assert after["installed"] is False and after["file"] == str(file)   # the file Install hands to Claude


def test_a_broken_claude_settings_file_is_left_alone(tmp_path, monkeypatch):
    (tmp_path / "claude_desktop_config.json").write_text('{"mcpServers": {', encoding="utf-8")
    monkeypatch.setattr(claude_setup, "_claude_config_dirs", lambda: [tmp_path])
    with pytest.raises(claude_setup.SetupError, match="JSON"):
        claude_setup.remove_from_desktop_config(None)
    assert (tmp_path / "claude_desktop_config.json").read_text(encoding="utf-8") == '{"mcpServers": {'
    assert claude_setup.in_desktop_config() is False


def test_stenny_is_started_as_stenny_exe_when_argv_leaves_out_the_exe(tmp_path, monkeypatch):
    (tmp_path / "stenny.exe").write_bytes(b"")
    monkeypatch.setattr(claude_setup.sys, "argv", [str(tmp_path / "stenny"), "app-server"])
    assert claude_setup.stenny_command() == [str((tmp_path / "stenny.exe").resolve())]


class FakeInstaller:
    """An installer that runs until it's told to end: it writes what Anthropic's would and exits with `code`."""
    def __init__(self, args, stdout=None, **kw):
        self.args, self.kw, self.code, self.out = args, kw, None, stdout
        FakeInstaller.started.append(self)

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9

    def end(self, code, text):
        Path(self.out.name).write_text(text, encoding="utf-8")
        self.code = code


@pytest.fixture
def installer(monkeypatch, tmp_path):
    FakeInstaller.started = []
    monkeypatch.setattr(claude_setup.subprocess, "Popen", FakeInstaller)
    monkeypatch.setattr(claude_setup, "_install", None)
    monkeypatch.setattr(claude_setup.paths, "LOGS_DIR", tmp_path / "Logs")
    monkeypatch.setattr(claude_setup, "find_claude", lambda: None)
    return FakeInstaller


def test_installs_claude_code_with_anthropics_installer_in_the_background(installer, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert claude_setup.claude_code_status() == {"state": "missing"}
    assert app_server.install_claude_code(None, {}) == {"state": "missing", "install": "running"}
    [run] = installer.started
    assert run.args == ["/bin/bash", "-c", "set -o pipefail; /usr/bin/curl -fsSL https://claude.ai/install.sh | /bin/bash"]
    assert run.kw["stdin"] is subprocess.DEVNULL and "/usr/bin" in run.kw["env"]["PATH"].split(":")
    # asked again while it runs: still running, nothing started twice
    assert claude_setup.install_claude_code(None) == {"state": "missing", "install": "running"}
    assert claude_setup.claude_code_status() == {"state": "missing", "install": "running"}
    assert len(installer.started) == 1


def test_an_install_that_ends_without_claude_code_says_why(installer, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    claude_setup.install_claude_code(None)
    installer.started[0].end(1, f"Downloading…\ncurl: (6) Could not resolve host: claude.ai\n{Path.home()}/.local/bin\n")
    status = claude_setup.claude_code_status()
    assert status["state"] == "missing" and status["install"] == "failed"
    assert "Could not resolve host" in status["detail"] and str(Path.home()) not in status["detail"]
    # a second click starts it again
    assert claude_setup.install_claude_code(None)["install"] == "running" and len(installer.started) == 2


def test_installs_claude_code_on_windows_with_powershell_and_no_window(installer, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    claude_setup.install_claude_code(None)
    [run] = installer.started
    assert run.args[0].replace("\\", "/").endswith("System32/WindowsPowerShell/v1.0/powershell.exe")
    assert run.args[1:] == ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                            "irm https://claude.ai/install.ps1 | iex"]
    assert run.kw["creationflags"] == 0x08000000


def test_installing_when_claude_code_is_there_starts_nothing(installer, monkeypatch, fake_claude):
    monkeypatch.setattr(claude_setup, "find_claude", lambda: str(fake_claude.exe))
    monkeypatch.setattr(claude_setup, "claude_code_status", lambda: {"state": "signed_out"})
    assert claude_setup.install_claude_code(None) == {"state": "signed_out"}
    assert installer.started == []
