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
