"""Load and validate C:\\automation\\fidelity\\config.toml."""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from automation_common.config import (
    ConfigError,
    load_toml,
    reject_unknown_keys,
    require_str,
    require_tables,
)

_SYMBOL = re.compile(r"^[A-Z0-9]{3,12}$")
_PLACEHOLDERS = ("XXXXX", "...", "<", ">")


@dataclass(frozen=True)
class Fund:
    symbol: str
    url: str


@dataclass(frozen=True)
class Config:
    funds: list[Fund]


def _fund(table: dict, n: int) -> Fund:
    where = f"[[funds]] #{n}"
    reject_unknown_keys(table, {"symbol", "url"}, where)
    symbol = require_str(table, "symbol", where)
    url = require_str(table, "url", where)
    if any(p in symbol or p in url for p in _PLACEHOLDERS):
        raise ConfigError(f"{where}: still has placeholder values from config.example.toml")
    if not _SYMBOL.match(symbol):
        raise ConfigError(f'{where}: symbol "{symbol}" must be 3-12 capital letters or digits')
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigError(f'{where} ({symbol}): "url" must be a full https:// address')
    return Fund(symbol, url)


def parse_config(doc: dict) -> Config:
    """Validate an already-parsed TOML document."""
    reject_unknown_keys(doc, {"funds"}, "config")
    funds = [_fund(t, n) for n, t in enumerate(require_tables(doc, "funds"), start=1)]
    symbols = [f.symbol for f in funds]
    dupes = sorted({s for s in symbols if symbols.count(s) > 1})
    if dupes:
        raise ConfigError(f"duplicate fund symbol(s): {', '.join(dupes)}")
    return Config(funds)


def load_config(path: Path) -> Config:
    return parse_config(load_toml(path))
