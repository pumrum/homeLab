from pathlib import Path

import pytest

from automation_common.config import (
    ConfigError,
    data_root,
    load_toml,
    process_dir,
    reject_unknown_keys,
    require_str,
    require_tables,
)


def test_data_root_defaults_to_c_automation():
    assert data_root({}) == Path(r"C:\automation")


def test_data_root_from_env(tmp_path):
    env = {"AUTOMATION_DATA_ROOT": str(tmp_path)}
    assert data_root(env) == tmp_path
    assert process_dir("example", env) == tmp_path / "example"


def test_load_toml_missing_file_says_what_to_do(tmp_path):
    with pytest.raises(ConfigError, match="config.example.toml"):
        load_toml(tmp_path / "config.toml")


def test_load_toml_invalid(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("symbol = XXXXX\n", encoding="utf-8")  # unquoted text
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_toml(path)


def test_require_str():
    assert require_str({"a": " x "}, "a", "here") == "x"
    with pytest.raises(ConfigError, match="quoted text"):
        require_str({"a": 5}, "a", "here")
    with pytest.raises(ConfigError, match="empty"):
        require_str({"a": "  "}, "a", "here")


def test_require_tables():
    assert require_tables({"funds": [{"a": 1}]}, "funds") == [{"a": 1}]
    with pytest.raises(ConfigError, match=r"\[\[funds\]\]"):
        require_tables({}, "funds")
    with pytest.raises(ConfigError, match="table"):
        require_tables({"funds": ["x"]}, "funds")


def test_reject_unknown_keys():
    reject_unknown_keys({"a": 1}, {"a", "b"}, "here")
    with pytest.raises(ConfigError, match="unknown key"):
        reject_unknown_keys({"a": 1, "c": 2}, {"a"}, "here")
