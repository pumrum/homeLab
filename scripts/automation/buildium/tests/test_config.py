"""Config validation."""

import copy

import pytest

from automation_common.config import ConfigError
from buildium_portal.config import load_config, parse_config

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
