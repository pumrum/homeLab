"""Config validation: synthetic data only."""

import tomllib
from copy import deepcopy
from pathlib import Path

import pytest

from automation_common.config import ConfigError
from innago_portal.config import load_config, parse_config

GOOD = {
    "site": {
        "login_url": "https://sign-in.example.invalid/login",
        "app_url": "https://app.example.invalid/",
    },
    "invoices": {
        "page_url": "https://app.example.invalid/invoices",
        "units": ["1001", "10a"],
    },
}


def _with(section, key, value):
    doc = deepcopy(GOOD)
    doc[section][key] = value
    return doc


def test_good_config_uppercases_units():
    cfg = parse_config(GOOD)
    assert cfg.invoices.units == ("1001", "10A")
    assert cfg.site.login_url == GOOD["site"]["login_url"]


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "config.toml")


def test_example_config_is_refused_until_filled_in():
    example = Path(__file__).parents[1] / "config.example.toml"
    with pytest.raises(ConfigError, match="placeholder"):
        parse_config(tomllib.loads(example.read_text(encoding="utf-8")))


@pytest.mark.parametrize("url", ["http://app.example.invalid", "app.example.invalid", "https://"])
def test_urls_must_be_full_https_addresses(url):
    with pytest.raises(ConfigError, match="https://") as caught:
        parse_config(_with("site", "login_url", url))
    assert "app.example" not in str(caught.value)  # private, so it's never repeated


@pytest.mark.parametrize(("units", "message"), [
    ([], "at least one"),
    ("1001", "at least one"),
    ([1001], "quoted"),
    (["10 01"], "letters, digits"),
    (["<unit>"], "placeholder"),
    (["10a", "10A"], "duplicate"),
])
def test_bad_units(units, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(_with("invoices", "units", units))


def test_unknown_keys_are_refused():
    with pytest.raises(ConfigError, match="unknown"):
        parse_config(_with("invoices", "unit", "1001"))


def test_missing_section():
    with pytest.raises(ConfigError, match=r"\[invoices\]"):
        parse_config({"site": GOOD["site"]})
