"""Data-root resolution and TOML config helpers.

Each automation keeps its private data in `<AUTOMATION_DATA_ROOT>/<process>/`
(default `C:\\automation\\<process>\\`), outside the repo. The real
`config.toml` lives there; the repo only holds `config.example.toml`.
"""

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DATA_ROOT_ENV = "AUTOMATION_DATA_ROOT"
DEFAULT_DATA_ROOT = r"C:\automation"


class ConfigError(Exception):
    """The config file is missing or invalid. The message says how to fix it."""


def data_root(env: Mapping[str, str] | None = None) -> Path:
    """Return the root of all automation data folders."""
    env = os.environ if env is None else env
    return Path(env.get(DATA_ROOT_ENV) or DEFAULT_DATA_ROOT)


def process_dir(process: str, env: Mapping[str, str] | None = None) -> Path:
    """Return the private data folder for one automation, e.g. C:\\automation\\<process>."""
    return data_root(env) / process


def load_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file, turning every failure into a clear ConfigError."""
    if not path.is_file():
        raise ConfigError(
            f"config file not found: {path}. Copy config.example.toml there and fill it in."
        )
    try:
        # utf-8-sig also accepts the byte-order mark some Windows tools add
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path} is not valid UTF-8. Save it as UTF-8.") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from e


def require_str(table: Mapping[str, Any], key: str, where: str) -> str:
    """Return a non-empty string value, or raise ConfigError naming its location."""
    value = table.get(key)
    if not isinstance(value, str):
        raise ConfigError(f'{where}: "{key}" must be a quoted text value')
    if not value.strip():
        raise ConfigError(f'{where}: "{key}" is empty')
    return value.strip()


def require_tables(doc: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    """Return a non-empty array of tables, e.g. [[funds]]."""
    value = doc.get(key)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"at least one [[{key}]] entry is required")
    if not all(isinstance(item, dict) for item in value):
        raise ConfigError(f"every {key} entry must be a [[{key}]] table")
    return value


def reject_unknown_keys(table: Mapping[str, Any], allowed: set[str], where: str) -> None:
    """Fail on misspelled or unexpected keys instead of silently ignoring them."""
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")
