"""All writes into the Obsidian vault go through here.

Two guarantees:
- nothing is written outside the configured lectures root;
- folder rule: every folder holds either only folders or only files, never both.
Hidden entries (".DS_Store", our ".tmp" files) are ignored for the rule.
"""

import os
import re
import shutil
from pathlib import Path

GLOSSARY_DIR = "_Glossaries"
COURSE_FILES_DIR = "_Course Files"
TOPICS_DIR = "_Topics"


class VaultError(Exception):
    pass


def _visible(d: Path) -> list[Path]:
    return [p for p in d.iterdir() if not p.name.startswith(".")] if d.is_dir() else []


def safe_name(name: str) -> str:
    """Folder/file name usable in Obsidian links (no / \\ : # ^ [ ] |)."""
    return re.sub(r'[/\\:#^\[\]|*?"<>]', "-", name).strip(" .") or "Untitled"


def folder_key(name: str) -> tuple[str, int]:
    """Sort key for lecture folders: '2026-10-20' < '2026-10-20 (2)' < '2026-10-20 (10)'."""
    m = re.fullmatch(r"(.*?) \((\d+)\)", name)
    return (m.group(1), int(m.group(2))) if m else (name, 1)


class LectureFiles:
    """File names of one lecture, unique across the vault ("Calculus 2026-10-20.md", "… (raw).md",
    "… (summary).md", "… (slides).pdf"), so they stay distinguishable in Obsidian links and searches and
    when Claude reads them. Lectures made before used fixed names (Transcript.md …); they keep them."""

    LEGACY = {"transcript": "Transcript.md", "raw": "Raw Transcript.md", "summary": "Summary.md", "slides": "Slides.pdf"}

    def __init__(self, folder: Path, course: str):
        self.folder = folder
        self.stem = f"{safe_name(course)} {folder.name}"

    def new(self, kind: str) -> Path:
        suffix = {"transcript": ".md", "raw": " (raw).md", "summary": " (summary).md", "slides": " (slides).pdf"}[kind]
        return self.folder / f"{self.stem}{suffix}"

    def existing(self, kind: str) -> Path | None:
        for path in (self.new(kind), self.folder / self.LEGACY[kind]):
            if path.exists():
                return path
        return None

    def target(self, kind: str) -> Path:
        """Where to write: the file already there (old name in an old lecture), else the new name."""
        existing = self.existing(kind)
        if existing:
            return existing
        legacy = (self.folder / self.LEGACY["transcript"]).exists()
        return self.folder / self.LEGACY[kind] if legacy else self.new(kind)

    def slides(self) -> Path | None:
        """The lecture's slides: under its own name, else the only PDF in the folder (dropped in by hand)."""
        if found := self.existing("slides"):
            return found
        pdfs = [p for p in _visible(self.folder) if p.suffix.lower() == ".pdf"]
        return pdfs[0] if len(pdfs) == 1 else None

    def names(self) -> dict[str, str]:
        """Names for links between the files (see render.py)."""
        slides = self.slides()
        return {"raw": self.target("raw").name, "transcript": self.target("transcript").name,
                "summary": self.target("summary").name,
                "slides": slides.name if slides else self.target("slides").name}


class Vault:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()

    def _inside(self, path: Path) -> Path:
        path = path.resolve()
        if path != self.root and self.root not in path.parents:
            raise VaultError(f"Refusing to write outside the lectures folder: {path}")
        return path

    def _check_can_hold(self, folder: Path, kind: str) -> None:
        """kind = 'file' or 'dir': what we are about to create inside `folder`."""
        entries = _visible(folder)
        if kind == "file" and any(p.is_dir() for p in entries):
            raise VaultError(f"Folder rule: {folder} contains folders, so it must not contain files.")
        if kind == "dir" and any(p.is_file() for p in entries):
            raise VaultError(f"Folder rule: {folder} contains files, so it must not contain folders.")

    def ensure_dir(self, path: Path) -> Path:
        path = self._inside(path)
        missing = []
        p = path
        while not p.exists():
            missing.append(p)
            p = p.parent
        for d in reversed(missing):
            if d != self.root and self.root not in d.parents:
                raise VaultError(f"{d} does not exist; create the vault folders above the lectures root yourself.")
            self._check_can_hold(d.parent, "dir")
            d.mkdir()
        return path

    def _prepare(self, path: Path) -> tuple[Path, Path]:
        path = self._inside(path)
        self.ensure_dir(path.parent)
        if not path.exists():
            self._check_can_hold(path.parent, "file")
        return path, path.with_name(f".{path.name}.tmp")

    def write(self, path: Path, text: str) -> Path:
        """Atomic write: readers (Obsidian) never see a half-written file."""
        path, tmp = self._prepare(path)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return path

    def copy(self, src: Path, path: Path) -> Path:
        """Atomic copy of a file from outside (e.g. slides) into the vault."""
        if src.resolve() == path.resolve():
            return path
        path, tmp = self._prepare(path)
        shutil.copyfile(src, tmp)
        os.replace(tmp, path)
        return path

    def move_out(self, path: Path, dest: Path) -> Path:
        """Move one of our files out of the lectures folder (e.g. the raw output, by `stenny migrate`)."""
        path = self._inside(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(path, dest)
        return dest

    def rename(self, path: Path, new: Path) -> Path:
        """Rename a file within the same folder of the lectures folder."""
        path, new = self._inside(path), self._inside(new)
        if new.parent != path.parent or new.exists():
            raise VaultError(f"Can't rename {path.name} to {new}")
        os.replace(path, new)
        return new

    def course_dir(self, course: str) -> Path:
        return self.root / safe_name(course)

    def lecture_dir(self, course: str, date: str, source: str | None = None) -> Path:
        """<root>/<Course>/<YYYY-MM-DD>, or '<date> (2)' … for a second lecture that day.
        Re-processing the same source file reuses its existing folder."""
        base = self.course_dir(course)
        n = 1
        while True:
            d = base / (date if n == 1 else f"{date} ({n})")
            transcript = LectureFiles(d, course).existing("transcript")
            if not d.exists() or not _visible(d):
                return d
            if source and transcript and f'source: "{source}"' in transcript.read_text(encoding="utf-8"):
                return d
            n += 1

    def files(self, course: str, folder: Path) -> LectureFiles:
        return LectureFiles(folder, course)

    def lectures(self, course: str) -> list[Path]:
        """The course's lecture folders that hold a transcript, oldest first."""
        base = self.course_dir(course)
        folders = [d for d in _visible(base) if d.is_dir() and LectureFiles(d, course).existing("transcript")]
        return sorted(folders, key=lambda d: folder_key(d.name))

    def lecture_number(self, course: str, folder: Path) -> int:
        """1 for the course's first lecture, …; counts this folder even before its first file exists."""
        names = {d.name for d in self.lectures(course)} | {folder.name}
        return sorted(names, key=folder_key).index(folder.name) + 1

    def glossary_path(self, course: str) -> Path:
        """`_Glossaries/<Course> (glossary).md`, named so it isn't mistaken for the course in Obsidian's graph.
        Before `stenny migrate` it was `<Course>.md`; that one is used while it's still there."""
        new = self.root / GLOSSARY_DIR / f"{safe_name(course)} (glossary).md"
        old = self.root / GLOSSARY_DIR / f"{safe_name(course)}.md"
        return old if old.exists() and not new.exists() else new

    def course_file(self, course: str, kind: str) -> Path:
        """Generated per-course files: kind "overview", "all lectures" or "summaries"."""
        return self.root / COURSE_FILES_DIR / f"{safe_name(course)} ({kind}).md"

    def topic_path(self, topic: str) -> Path:
        return self.root / TOPICS_DIR / f"{safe_name(topic)}.md"

    def topic_names(self) -> list[str]:
        """Names of the existing topic notes."""
        return sorted(p.stem for p in _visible(self.root / TOPICS_DIR) if p.suffix == ".md")

    def course_dirs(self) -> list[Path]:
        """Every course folder (the root's folders except the generated "_…" ones)."""
        return sorted(d for d in _visible(self.root) if d.is_dir() and not d.name.startswith("_"))

    def violations(self) -> list[Path]:
        """Folders under the root that break the folder rule (for checks and tests)."""
        bad = []
        for d in [self.root, *[p for p in self.root.rglob("*") if p.is_dir()]]:
            entries = _visible(d)
            if any(p.is_dir() for p in entries) and any(p.is_file() for p in entries):
                bad.append(d)
        return bad
