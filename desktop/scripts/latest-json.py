"""Adds one system's update to latest.json, the file Stenny's updater reads from the newest GitHub release
(tauri.conf.json, `plugins.updater.endpoints`; src-tauri/src/updater.rs). app/release.sh writes it with the Mac's
update (darwin-aarch64: Stenny.app.tar.gz); CI's installer job adds Windows' (windows-x86_64: the setup program)
once it has built it. A file for another version is started over, never mixed with this one.

    python3 desktop/scripts/latest-json.py <latest.json> --version 0.5.12 --platform darwin-aarch64 \\
        --url https://github.com/…/releases/download/v0.5.12/Stenny.app.tar.gz --signature-file Stenny.app.tar.gz.sig
"""

from __future__ import annotations  # macOS's own python3 is 3.9

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PLATFORMS = ("darwin-aarch64", "windows-x86_64")


def add(manifest: dict, version: str, platform: str, url: str, signature: str, notes: str | None = None,
        now: datetime | None = None) -> dict:
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r} (one of {', '.join(PLATFORMS)})")
    if not url.startswith("https://"):
        raise ValueError(f"the download must be https: {url}")
    if not signature.strip():
        raise ValueError("empty signature")
    if manifest.get("version") != version:
        manifest = {"version": version, "platforms": {}}
    manifest.setdefault("pub_date", (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if notes is not None:
        manifest["notes"] = notes
    manifest.setdefault("platforms", {})[platform] = {"signature": signature.strip(), "url": url}
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", type=Path)
    ap.add_argument("--version", required=True)
    ap.add_argument("--platform", required=True, choices=PLATFORMS)
    ap.add_argument("--url", required=True)
    ap.add_argument("--signature-file", required=True, type=Path)
    ap.add_argument("--notes")
    args = ap.parse_args(argv)
    try:
        manifest = json.loads(args.file.read_text(encoding="utf-8")) if args.file.is_file() else {}
    except json.JSONDecodeError:
        manifest = {}
    try:
        manifest = add(manifest, args.version.lstrip("v"), args.platform, args.url,
                       args.signature_file.read_text(encoding="utf-8"), args.notes)
    except (ValueError, OSError) as e:
        print(f"latest-json: {e}", file=sys.stderr)
        return 1
    args.file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"latest.json: {manifest['version']} for {', '.join(sorted(manifest['platforms']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
