import pytest

from stenny import config as config_mod
from stenny import settings
from stenny.config import ConfigError

CONFIG = '''# my comment
vault = "/tmp/vault"
keep_audio = true                            # false = don't archive audio

[llm]
backend = "claude-code"      # claude-code | api | none
summary = true

[processing]
priority = "low"

[courses."Calculus"]  # keep me
language = "en"

[courses.Economics]
language = "mixed"
languages = ["fr", "en"]
'''


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(CONFIG, encoding="utf-8")
    return p


def change(path, key, value):
    return settings.change(config_mod.load(path), key, value)


def test_replaces_a_line_and_keeps_comments(path):
    got = change(path, "llm.backend", "none")
    text = path.read_text(encoding="utf-8")
    assert 'backend = "none"      # claude-code | api | none' in text
    assert "# my comment" in text and "# keep me" in text
    assert got["llm"]["backend"] == "none" and config_mod.load(path).llm_backend == "none"


def test_top_level_key_stays_above_the_tables(path):
    change(path, "keep_audio", False)
    assert config_mod.load(path).keep_audio is False
    assert path.read_text(encoding="utf-8").index("keep_audio = false") < path.read_text(encoding="utf-8").index("[llm]")


def test_adds_a_missing_key_and_a_missing_table(path):
    change(path, "processing.night_only", True)
    change(path, "app.pause_shortcut", "ctrl+opt+space")
    cfg = config_mod.load(path)
    assert cfg.night_only and cfg.priority == "low" and cfg.pause_shortcut == "ctrl+opt+space"
    assert cfg.course("Calculus").language == "en"  # the new key went into [processing], not into a course


def test_the_setup_card_goes_for_good(path):
    assert config_mod.load(path).setup_checklist is True
    got = change(path, "app.setup_checklist", False)
    assert got["app"]["setup_checklist"] is False and config_mod.load(path).setup_checklist is False


@pytest.mark.parametrize("key,value", [
    ("llm.backend", "gpt"), ("processing.night_start", "25:00"), ("app.pause_shortcut", "shift+p"),
    ("keep_audio", "yes"), ("vault", "/elsewhere"), ("recordings.languages", ["English"]),
    ("recordings.languages", ["en", "es"]), ("app.setup_checklist", "no"),
])
def test_refuses_and_leaves_the_file_alone(path, key, value):
    with pytest.raises(ConfigError):
        change(path, key, value)
    assert path.read_text(encoding="utf-8") == CONFIG


def test_course_language(path):
    settings.course_language(config_mod.load(path), "Economics", "de", None)
    c = config_mod.load(path).course("Economics")
    assert c.language == "de" and "languages" not in path.read_text(encoding="utf-8").split("[courses.Economics]")[1]
    settings.course_language(config_mod.load(path), "Calculus", "mixed", ["en", "de"])
    c = config_mod.load(path).course("Calculus")
    assert c.language == "mixed" and c.languages == ["en", "de"]
    assert "# keep me" in path.read_text(encoding="utf-8")


def test_course_language_only_supported(path):
    with pytest.raises(ConfigError):
        settings.course_language(config_mod.load(path), "Calculus", "es", None)
    with pytest.raises(ConfigError):
        settings.course_language(config_mod.load(path), "Calculus", "mixed", ["en", "ja"])
    assert path.read_text(encoding="utf-8") == CONFIG


def test_get_lists_visible_courses_and_paths(path):
    got = settings.get(config_mod.load(path))
    assert [c["name"] for c in got["courses"]] == ["Calculus", "Economics"]
    assert got["paths"]["lectures"].endswith("Lectures")
