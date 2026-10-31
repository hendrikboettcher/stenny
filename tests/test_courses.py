import tomllib

import pytest

from stenny import config as config_mod
from stenny import courses
from stenny.config import ConfigError, course_for_title

CONFIG = '''# my comment
vault = "/tmp/vault"

[courses."Calculus"]  # keep me
language = "en"
aliases = ["calc"]

[courses.Economics]
language = "de"
'''


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(CONFIG, encoding="utf-8")
    return p


def test_add_keeps_comments_and_loads(path):
    courses.add(config_mod.load(path), "Müller: Recht".replace(":", ""), "mixed", ["de", "en"], "recht")
    text = path.read_text(encoding="utf-8")
    assert "# my comment" in text and "# keep me" in text
    course = config_mod.load(path).course("recht")
    assert course.name == "Müller Recht" and course.language == "mixed" and course.languages == ["de", "en"]


@pytest.mark.parametrize("name,language,alias,match", [
    ("Calculus", "en", None, "already"), ("calc", "en", None, "already"), ("New", "en", "calc", "short name"),
    ("A/B", "en", None, "can't contain"), ("  ", "en", None, "needs a name"), ("New", "English", None, "Unknown language"),
])
def test_add_refuses_and_leaves_the_file_alone(path, name, language, alias, match):
    with pytest.raises(ConfigError, match=match):
        courses.add(config_mod.load(path), name, language, alias=alias)
    assert path.read_text(encoding="utf-8") == CONFIG


def test_a_name_with_quotes_in_toml_stays_valid(path):
    courses.add(config_mod.load(path), "Ethik & Recht – 2", "de")
    assert "Ethik & Recht – 2" in tomllib.loads(path.read_text(encoding="utf-8"))["courses"]


def test_hide_keeps_settings_and_leaves_calendar_matching(path):
    courses.hide(config_mod.load(path), "calc")
    cfg = config_mod.load(path)
    assert cfg.course("Calculus").hidden and cfg.course("Calculus").aliases == ["calc"]  # queued jobs still find it
    assert "calc" not in [c.name for c in cfg.visible_courses.values()]
    assert course_for_title(cfg.visible_courses, "Calculus VL") is None
    assert course_for_title(cfg.visible_courses, "Economics VL").name == "Economics"


def test_hide_finds_a_table_written_without_quotes(path):
    courses.hide(config_mod.load(path), "Economics")
    assert config_mod.load(path).course("Economics").hidden


def test_adding_a_hidden_course_brings_it_back_unchanged(path):
    courses.hide(config_mod.load(path), "Calculus")
    assert "back" in courses.add(config_mod.load(path), "Calculus", "de")
    assert path.read_text(encoding="utf-8") == CONFIG
