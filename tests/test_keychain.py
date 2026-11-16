import sys

from stenny import keychain


def test_a_mac_secret_goes_to_security_on_stdin_in_its_own_session(monkeypatch):
    """With a terminal attached (install.sh's `stenny invite --stdin`), security asked the terminal for the secret
    instead of reading stdin, and timed out: in its own session it has no terminal to ask. Never on the command line."""
    calls = []

    class Done:
        returncode = 0
        stderr = ""

    def run(args, **kw):
        calls.append((args, kw))
        return Done()

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keychain.subprocess, "run", run)
    keychain.save_secret("invite", "42-abcdefghijk2m3p4", "Stenny: invite")
    args, kw = calls[0]
    assert args[:2] == [keychain.SECURITY, "add-generic-password"] and args[-1] == "-w"
    assert "42-abcdefghijk2m3p4" not in " ".join(args)
    assert kw["input"] == "42-abcdefghijk2m3p4\n42-abcdefghijk2m3p4\n"
    assert kw["start_new_session"] is True
