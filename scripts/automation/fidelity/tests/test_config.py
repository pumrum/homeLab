from pathlib import Path

import pytest

from automation_common.config import ConfigError
from fidelity_nav.config import load_config, parse_config

EXAMPLE = Path(__file__).parents[1] / "config.example.toml"
GOOD = """
[[funds]]
symbol = "TEST1"
url = "https://example.invalid/fund/TEST1"

[[funds]]
symbol = "TEST2"
url = "https://example.invalid/fund/TEST2"
"""


def _write(tmp_path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_config(tmp_path):
    cfg = load_config(_write(tmp_path, GOOD))
    assert [f.symbol for f in cfg.funds] == ["TEST1", "TEST2"]
    assert cfg.funds[0].url == "https://example.invalid/fund/TEST1"


def test_sheet_is_optional(tmp_path):
    assert load_config(_write(tmp_path, GOOD)).sheet is None


def test_sheet_section(tmp_path):
    text = GOOD + (
        '\n[sheet]\nspreadsheet_id = "made-up-spreadsheet-id-0123456789"\nworksheet = "NAV"\n'
    )
    sheet = load_config(_write(tmp_path, text)).sheet
    assert sheet is not None
    assert (sheet.spreadsheet_id, sheet.worksheet) == ("made-up-spreadsheet-id-0123456789", "NAV")


FUNDS = [{"symbol": "TEST1", "url": "https://x.invalid"}]


@pytest.mark.parametrize(
    ("sheet", "message"),
    [
        ({"worksheet": "NAV"}, '"spreadsheet_id"'),
        ({"spreadsheet_id": "made-up-spreadsheet-id-0123456789"}, '"worksheet"'),
        ({"spreadsheet_id": "<spreadsheet id>", "worksheet": "NAV"}, "placeholder"),
        ({"spreadsheet_id": "https://docs.google.com/spreadsheets/d/abc/edit",
          "worksheet": "NAV"}, "not the whole address"),
        ({"spreadsheet_id": "made-up-spreadsheet-id-0123456789", "worksheet": "NAV",
          "range": "A1"}, "unknown"),
        ("NAV", "must be a table"),
    ],
)
def test_invalid_sheet_section(sheet, message):
    with pytest.raises(ConfigError, match=message):
        parse_config({"funds": FUNDS, "sheet": sheet})


def test_sheet_error_never_repeats_the_id():
    bad_id = "short-private-id"
    with pytest.raises(ConfigError) as e:
        parse_config({"funds": FUNDS, "sheet": {"spreadsheet_id": bad_id, "worksheet": "NAV"}})
    assert bad_id not in str(e.value)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "config.toml")


def test_example_is_valid_toml_but_rejected_as_placeholder():
    with pytest.raises(ConfigError, match="placeholder"):
        load_config(EXAMPLE)


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ({}, r"\[\[funds\]\]"),
        ({"funds": []}, r"\[\[funds\]\]"),
        ({"funds": [{"symbol": "TEST1"}]}, '"url"'),
        ({"funds": [{"symbol": 123, "url": "https://x.invalid"}]}, "quoted text"),
        ({"funds": [{"symbol": "test1", "url": "https://x.invalid"}]}, "capital letters"),
        ({"funds": [{"symbol": "TEST1", "url": "http://x.invalid"}]}, "https://"),
        ({"funds": [{"symbol": "TEST1", "url": "https://x.invalid", "nav": "1"}]}, "unknown"),
        ({"funds": [{"symbol": "TEST1", "url": "https://x.invalid"}], "extra": 1}, "unknown"),
        (
            {"funds": [{"symbol": "TEST1", "url": "https://a.invalid"},
                       {"symbol": "TEST1", "url": "https://b.invalid"}]},
            "duplicate",
        ),
    ],
)
def test_invalid_configs(doc, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(doc)
