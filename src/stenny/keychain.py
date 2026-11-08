"""The Anthropic API key, kept in the system's own password store: the macOS Keychain or the Windows Credential
Manager, never in config.toml. $ANTHROPIC_API_KEY still wins when it's set (older setups, scripts).

On macOS this goes through /usr/bin/security rather than a Python library: a Keychain item belongs to the program
that created it, and the program here is always `security`, so a background job never stops at a Keychain prompt
after Python was updated. The key reaches `security` on stdin, never on its command line. On Windows the `keyring`
package talks to the Credential Manager."""

import os
import subprocess
import sys

SERVICE = "Stenny"
ACCOUNT = "anthropic-api-key"
SECURITY = "/usr/bin/security"


class KeychainError(Exception):
    pass


def from_environment() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def api_key() -> str | None:
    """The key to use: $ANTHROPIC_API_KEY, else the saved one, else None."""
    if from_environment():
        return os.environ["ANTHROPIC_API_KEY"].strip()
    return saved_key()


def saved_key() -> str | None:
    try:
        if sys.platform == "darwin":
            p = subprocess.run([SECURITY, "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
                               capture_output=True, text=True, timeout=10)
            return (p.stdout.strip() or None) if p.returncode == 0 else None
        import keyring
        return keyring.get_password(SERVICE, ACCOUNT) or None
    except Exception:  # noqa: BLE001 — no store, locked, no backend: there's simply no key
        return None


def save_key(key: str) -> None:
    try:
        if sys.platform == "darwin":
            # -w as the last option reads the password from stdin, asked twice; -U replaces an existing one
            p = subprocess.run([SECURITY, "add-generic-password", "-U", "-s", SERVICE, "-a", ACCOUNT,
                                "-l", "Stenny: Anthropic API key", "-w"],
                               input=f"{key}\n{key}\n", capture_output=True, text=True, timeout=10)
            if p.returncode != 0:
                raise KeychainError(p.stderr.strip() or f"security exited with {p.returncode}")
            return
        import keyring
        keyring.set_password(SERVICE, ACCOUNT, key)
    except KeychainError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeychainError(str(e)) from None


def delete_key() -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run([SECURITY, "delete-generic-password", "-s", SERVICE, "-a", ACCOUNT],
                           capture_output=True, timeout=10)
            return
        import keyring
        from keyring.errors import PasswordDeleteError
        try:
            keyring.delete_password(SERVICE, ACCOUNT)
        except PasswordDeleteError:
            pass
    except Exception as e:  # noqa: BLE001
        raise KeychainError(str(e)) from None
