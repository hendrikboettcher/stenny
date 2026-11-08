import json
import sys
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
    state.write_text("in")
    exe = tmp_path / "claude"
    exe.write_text(f"""#!/bin/sh
case "$1 $2" in
  "auth status") if [ "$(cat {state})" = in ]; then
      echo '{{"loggedIn": true, "email": "student@example.edu", "subscriptionType": "pro"}}'
    else echo '{{"loggedIn": false}}'; fi ;;
  "mcp add") echo "$@" > {tmp_path}/added; echo '{{"mcpServers": {{"stenny": {{}}}}}}' > "$HOME/.claude.json" ;;
  "mcp remove") echo "$@" > {tmp_path}/removed; echo '{{"mcpServers": {{}}}}' > "$HOME/.claude.json" ;;
esac
""")
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
    fake_claude.state.write_text("out")
    assert claude_setup.ai_status(cfg("claude-code"))["state"] == "signed_out"


@posix
def test_claude_code_that_says_nothing_useful_is_unknown(fake_claude):
    fake_claude.exe.write_text("#!/bin/sh\necho not json\n")
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
    (data / "Claude Extensions" / folder / "manifest.json").write_text(json.dumps({"name": name, "version": "0.4.0"}))
    if enabled is not None:
        (data / "Claude Extensions Settings").mkdir(exist_ok=True)
        (data / "Claude Extensions Settings" / f"{folder}.json").write_text(json.dumps({"isEnabled": enabled}))


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
    assert (fake_claude.dir / "added").read_text().startswith("mcp add --scope user stenny -- ")
    assert (fake_claude.dir / "added").read_text().rstrip().endswith(" mcp")
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
                   "install_connector", "add_to_claude_code"):
        assert method in app_server.METHODS


@posix
def test_removes_the_connector_from_claude_code(fake_claude, monkeypatch):
    monkeypatch.setenv("HOME", str(fake_claude.dir))
    monkeypatch.setattr(claude_setup, "claude_data_dir", lambda: fake_claude.dir / "Claude")
    claude_setup.add_to_claude_code(None)
    status = claude_setup.remove_from_claude_code(None)
    assert status["code"]["added"] is False
    assert (fake_claude.dir / "removed").read_text().strip() == "mcp remove --scope user stenny"
