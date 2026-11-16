"""`stenny uninstall` (uninstall.py): removes what can be made again, lists settings and recordings for the Trash,
never touches the Stenny folder."""
from stenny import calendars, claude_setup, jobs, keychain, paths, uninstall


def _setup(places, monkeypatch, folder=None, data_dir=None):
    folder = folder or places.home / "Documents" / "Stenny"
    lines = [f'folder = "{folder.as_posix()}"']
    if data_dir:
        lines.append(f'data_dir = "{data_dir.as_posix()}"')
    places.config_dir.mkdir(parents=True)
    places.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "Course").mkdir(parents=True)
    (folder / "Course" / "Lecture.md").write_text("mine", encoding="utf-8")
    for place in (places.data_dir / "Recordings", places.logs_dir, places.cache_dir / "Shared"):
        place.mkdir(parents=True, exist_ok=True)
        (place / "file").write_text("x", encoding="utf-8")
    deleted = []
    monkeypatch.setattr(keychain, "delete_secret", deleted.append)
    monkeypatch.setattr(calendars, "_load", lambda: [{"id": "abc"}])
    monkeypatch.setattr(jobs, "uninstall_agent", lambda: False)  # never the test computer's own job
    monkeypatch.setattr(uninstall, "find_claude", lambda: None)
    monkeypatch.setattr(claude_setup, "installed_extension", lambda: None)
    monkeypatch.setattr(uninstall, "whisper_model_dirs", lambda model: [])
    return folder, deleted


def test_without_yes_nothing_changes(places, monkeypatch):
    folder, deleted = _setup(places, monkeypatch)
    result = uninstall.run(act=False)
    assert str(places.logs_dir) in result["removed"]
    assert places.logs_dir.exists() and places.cache_dir.exists()
    assert deleted == []


def test_removes_what_can_be_made_again_and_lists_settings_and_recordings(places, monkeypatch):
    folder, deleted = _setup(places, monkeypatch)
    result = uninstall.run(act=True)
    assert not places.logs_dir.exists() and not places.cache_dir.exists()
    assert set(result["trash"]) == {str(places.config_dir), str(places.data_dir)}
    assert (places.data_dir / "Recordings" / "file").exists()  # the script trashes it, not the engine
    assert result["folder"] == str(folder)
    assert (folder / "Course" / "Lecture.md").read_text(encoding="utf-8") == "mine"
    assert deleted == [keychain.ACCOUNT, "calendar-link-abc"]
    assert result["problems"] == []


def test_a_place_holding_the_stenny_folder_stays(places, monkeypatch):
    # an odd setup: the Stenny folder inside the data place
    folder, _ = _setup(places, monkeypatch, folder=places.data_dir / "Stenny")
    result = uninstall.run(act=True)
    assert str(places.data_dir) not in result["trash"]
    assert (folder / "Course" / "Lecture.md").exists()
    assert any("holds the Stenny folder" in p for p in result["problems"])


def test_mac_model_is_only_that_models_folder_in_the_shared_cache(monkeypatch):
    monkeypatch.setattr(paths, "models_dir", lambda: None)
    dirs = uninstall.whisper_model_dirs("mlx-community/whisper-large-v3-turbo")
    assert [d.name for d in dirs] == ["models--mlx-community--whisper-large-v3-turbo"]
    assert uninstall.whisper_model_dirs("large-v3-turbo") == []
