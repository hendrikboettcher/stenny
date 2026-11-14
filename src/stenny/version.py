"""Stenny's version: the one in pyproject.toml next to the code that runs.

Stenny's engine is an editable install (`uv tool install -e`), whose metadata keeps the version it was installed
with: after a `git pull` (or anything else that changes the code without reinstalling) that number is old while the
code is new, and the update check said "You have 0.4.0" on a 0.5.9 engine. So the version comes from the folder the
code runs from, read once per process (the code a process runs doesn't change under it), and from the metadata only
when there's no pyproject.toml there (an install from a wheel).
"""

import tomllib
from functools import cache
from importlib.metadata import version as package_version
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@cache
def version() -> str:
    try:
        project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8")).get("project", {})
    except (OSError, tomllib.TOMLDecodeError):
        project = {}
    if project.get("name") == "stenny" and project.get("version"):
        return project["version"]
    return package_version("stenny")
