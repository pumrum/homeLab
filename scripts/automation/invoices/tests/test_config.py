import copy
import tomllib
from pathlib import Path

import pytest

from automation_common.config import ConfigError
from invoices_sync.config import parse_config

EXAMPLE = Path(__file__).parents[1] / "config.example.toml"

GOOD = {
    "sftp": {"host": "sftp.example.invalid", "port": 2222, "user": "made-up",
             "folder": "/made/up"},
    "sheet": {"spreadsheet_id": "made-up-spreadsheet-id-0123456789", "worksheet": "Invoices"},
    "entities": [
        {"code": "entity_a", "enabled": True, "file": "inv_entity_a.txt", "start_cell": "B5"},
        {"code": "entity_b", "enabled": False, "file": "inv_entity_b.txt", "start_cell": "J5"},
    ],
}


def _changed(section, key, value, index=None):
    doc = copy.deepcopy(GOOD)
    table = doc[section] if index is None else doc[section][index]
    table[key] = value
    return doc


def test_good_config():
    cfg = parse_config(GOOD)
    assert cfg.sftp.port == 2222
    assert [e.code for e in cfg.entities] == ["entity_a", "entity_b"]
    assert [e.code for e in cfg.enabled_entities] == ["entity_a"]


def test_the_entity_list_can_grow():
    doc = copy.deepcopy(GOOD)
    doc["entities"] += [
        {"code": f"entity_{n}", "enabled": True, "file": f"inv_{n}.txt", "start_cell": f"{c}5"}
        for n, c in enumerate(["R", "Z", "AH", "AP", "AX"])
    ]
    assert len(parse_config(doc).enabled_entities) == 6


def test_port_defaults_to_22():
    doc = copy.deepcopy(GOOD)
    del doc["sftp"]["port"]
    assert parse_config(doc).sftp.port == 22


def test_example_file_is_refused_until_filled_in():
    with pytest.raises(ConfigError, match="placeholder"):
        parse_config(tomllib.loads(EXAMPLE.read_text(encoding="utf-8")))


@pytest.mark.parametrize(("doc", "message"), [
    (_changed("sftp", "port", 0), "port"),
    (_changed("sftp", "port", True), "port"),
    (_changed("sftp", "port", "22"), "port"),
    (_changed("sftp", "folder", "made/up"), "must start with /"),
    (_changed("sftp", "extra", "x"), "unknown key"),
    (_changed("sheet", "spreadsheet_id", "https://example.invalid/d/x/edit"), "ID from the"),
    (_changed("entities", "code", "Entity A", 0), "lowercase"),
    (_changed("entities", "enabled", "true", 0), "without quotes"),
    (_changed("entities", "file", "../inv_a.txt", 0), "file name only"),
    (_changed("entities", "file", "sub/inv_a.txt", 0), "file name only"),
    (_changed("entities", "file", "..", 0), "file name only"),
    (_changed("entities", "start_cell", "b5", 0), "cell like B5"),
    (_changed("entities", "start_cell", "B", 0), "cell like B5"),
    (_changed("entities", "start_cell", "B0", 0), "cell like B5"),
    (_changed("entities", "code", "entity_b", 0), "duplicate entity code"),
    (_changed("entities", "file", "inv_entity_b.txt", 0), "duplicate file name"),
    (_changed("entities", "start_cell", "J9", 0), "duplicate start cell column"),
    (_changed("entities", "enabled", False, 0), "nothing to do"),
])
def test_bad_values_are_refused(doc, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(doc)


@pytest.mark.parametrize("section", ["sftp", "sheet", "entities"])
def test_missing_sections_are_refused(section):
    doc = copy.deepcopy(GOOD)
    del doc[section]
    with pytest.raises(ConfigError, match=section):
        parse_config(doc)


def test_a_bad_spreadsheet_id_is_not_repeated_in_the_message():
    with pytest.raises(ConfigError) as e:
        parse_config(_changed("sheet", "spreadsheet_id", "short-private-value"))
    assert "short-private-value" not in str(e.value)
