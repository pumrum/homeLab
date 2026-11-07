"""Config validation."""

import copy
import datetime as dt

import pytest

from automation_common.config import ConfigError
from buildium_portal.config import load_config, parse_config, parse_gl_config

VALID = {
    "site": {"login_url": "https://site.example.invalid/login"},
    "entities": [
        {"key": "entity1", "abbreviation": "ABCLLC", "statement_name": "FakeEntity One, LLC"},
        {"key": "entity2", "abbreviation": "DEF-2", "statement_name": "FakeEntity Two, LLC"},
    ],
    "statements": {"system": "FakeSystem"},
}


def _changed(change):
    doc = copy.deepcopy(VALID)
    change(doc)
    return doc


def test_mfa_words_default_to_the_abbreviation_and_can_change():
    note = parse_config(VALID).notifications["mfa_needed"].render(abbreviation="ABC", key="k")
    assert note["message"] == "ABC: MFA link needed"
    doc = VALID | {"notifications": {"mfa_needed": {"message": "{key} and {abbreviation}"}}}
    note = parse_config(doc).notifications["mfa_needed"].render(abbreviation="ABC", key="k")
    assert note["message"] == "k and ABC"


def test_only_the_mfa_push_can_name_an_entity():
    doc = VALID | {"notifications": {"fail": {"message": "{abbreviation}"}}}
    with pytest.raises(ConfigError, match="abbreviation"):
        parse_config(doc)


def test_a_valid_config_loads():
    cfg = parse_config(VALID)
    assert cfg.site.login_url == VALID["site"]["login_url"]
    assert [entity.key for entity in cfg.entities] == ["entity1", "entity2"]
    assert cfg.entities[0].abbreviation == "ABCLLC"
    assert cfg.entities[1].statement_name == "FakeEntity Two, LLC"
    assert cfg.statements.system == "FakeSystem"


def test_a_missing_file_says_what_to_do(tmp_path):
    with pytest.raises(ConfigError, match="Copy config.example.toml"):
        load_config(tmp_path / "config.toml")


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d.pop("site"), r"\[site\] section"),
    (lambda d: d.pop("statements"), r"\[statements\] section"),
    (lambda d: d.pop("entities"), r"\[\[entities\]\]"),
    (lambda d: d.update(extra=1), "unknown"),
    (lambda d: d["site"].update(extra="x"), "unknown"),
    (lambda d: d["entities"][0].update(name="x"), "unknown"),
    (lambda d: d["entities"][0].pop("statement_name"), "statement_name"),
    (lambda d: d["site"].update(login_url="http://site.example.invalid"), "https"),
    (lambda d: d["site"].update(login_url="https://<address>"), "placeholder"),
    (lambda d: d["entities"][0].update(abbreviation="<ABBREVIATION>"), "placeholder"),
    (lambda d: d["entities"][0].update(statement_name="<name>"), "placeholder"),
    (lambda d: d["statements"].update(system="<system name>"), "placeholder"),
    (lambda d: d["entities"][0].update(key="Entity 1"), "lowercase"),
    (lambda d: d["entities"][0].update(key="../up"), "lowercase"),
    (lambda d: d["entities"][0].update(abbreviation="A/B"), "letters, digits"),
    (lambda d: d["entities"][0].update(abbreviation="A_B"), "letters, digits"),
    (lambda d: d["statements"].update(system="Fake System"), "letters, digits"),
    (lambda d: d["entities"][1].update(key="entity1"), "duplicate key"),
    (lambda d: d["entities"][1].update(abbreviation="abcllc"), "same"),
])
def test_a_bad_config_fails_clearly(change, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(_changed(change))


GL = {
    "spreadsheet_id": "made-up-id",
    "master_tab": "FakeMaster",
    "entities": [
        {"key": "entity1", "code": "CODE1", "tab": "FakeOne", "start_cell": "B5"},
        {"key": "entity2", "code": "CODE2", "tab": "FakeTwo", "start_cell": "AB12"},
    ],
    "unit_fixes": [
        {"key": "entity1", "lease_id": "77", "before": "2030-01-31", "unit_number": "Upper"},
    ],
}


def _gl_changed(change):
    doc = _changed(lambda d: d.update(gl=copy.deepcopy(GL)))
    change(doc)
    return doc


def test_a_valid_gl_section_loads():
    gl = parse_gl_config(_gl_changed(lambda d: None)).gl
    assert (gl.spreadsheet_id, gl.master_tab) == ("made-up-id", "FakeMaster")
    assert gl.entities["entity2"].code == "CODE2"
    assert gl.entities["entity2"].start_cell == "AB12"
    fix = gl.unit_fixes[0]
    assert (fix.key, fix.lease_id, fix.before, fix.unit_number) == (
        "entity1", "77", dt.date(2030, 1, 31), "Upper")


def test_unit_fixes_are_optional():
    assert parse_gl_config(_gl_changed(lambda d: d["gl"].pop("unit_fixes"))).gl.unit_fixes == ()


def test_buildium_gl_doesnt_depend_on_the_statements_section():
    doc = _gl_changed(lambda d: d.pop("statements"))
    assert [entity.key for entity in parse_gl_config(doc).entities] == ["entity1", "entity2"]
    doc = _gl_changed(lambda d: d["statements"].update(system="<system name>"))
    assert parse_gl_config(doc).site.login_url == VALID["site"]["login_url"]


def test_buildium_statements_doesnt_depend_on_the_gl_section():
    assert parse_config(VALID).statements.system == "FakeSystem"
    doc = _gl_changed(lambda d: d["gl"].update(master_tab="<tab>"))
    assert parse_config(doc).statements.system == "FakeSystem"


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d.pop("site"), r"\[site\] section"),
    (lambda d: d.pop("entities"), r"\[\[entities\]\]"),
    (lambda d: d.pop("gl"), r"\[gl\] section"),
    (lambda d: d.update(extra=1), "unknown"),
    (lambda d: d["gl"].update(extra="x"), "unknown"),
    (lambda d: d["gl"].pop("spreadsheet_id"), "spreadsheet_id"),
    (lambda d: d["gl"].update(master_tab="<tab>"), "placeholder"),
    (lambda d: d["gl"]["entities"].pop(), "one block for each"),
    (lambda d: d["gl"]["entities"][1].update(key="entity3"), "one block for each"),
    (lambda d: d["gl"]["entities"][1].update(key="entity1"), "one block for each"),
    (lambda d: d["gl"]["entities"][0].update(extra="x"), "unknown"),
    (lambda d: d["gl"]["entities"][0].pop("code"), "code"),
    (lambda d: d["gl"]["entities"][0].update(start_cell="b5"), "a cell like B5"),
    (lambda d: d["gl"]["entities"][0].update(start_cell="A0"), "a cell like B5"),
    (lambda d: d["gl"]["entities"][1].update(tab="FakeOne"), "their own tab"),
    (lambda d: d["gl"].update(master_tab="FakeOne"), "their own tab"),
    (lambda d: d["gl"].update(entities="x"), "must be"),
    (lambda d: d["gl"]["unit_fixes"][0].update(before="1/31/2030"), "a date like"),
    (lambda d: d["gl"]["unit_fixes"][0].update(key="entity3"), "unknown key"),
    (lambda d: d["gl"]["unit_fixes"][0].update(extra="x"), "unknown"),
    (lambda d: d["gl"]["unit_fixes"][0].pop("lease_id"), "lease_id"),
])
def test_a_bad_gl_config_fails_clearly(change, message):
    with pytest.raises(ConfigError, match=message):
        parse_gl_config(_gl_changed(change))


def test_a_gl_message_never_repeats_a_private_value():
    with pytest.raises(ConfigError) as caught:
        parse_gl_config(_gl_changed(lambda d: d["gl"]["entities"][0].update(start_cell="SECRET")))
    assert "SECRET" not in str(caught.value)


def test_a_message_never_repeats_a_private_value():
    url = "http://private.example.invalid"
    with pytest.raises(ConfigError) as caught:
        parse_config(_changed(lambda d: d["site"].update(login_url=url)))
    assert url not in str(caught.value)
    with pytest.raises(ConfigError) as caught:
        parse_config(_changed(lambda d: d["entities"][0].update(abbreviation="SECRET/1")))
    assert "SECRET" not in str(caught.value)


def test_the_example_config_is_refused_until_filled_in():
    from pathlib import Path
    example = Path(__file__).parents[1] / "config.example.toml"
    with pytest.raises(ConfigError, match="placeholder"):
        load_config(example)
