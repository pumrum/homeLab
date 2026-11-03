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
_SPREADSHEET_ID = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_PLACEHOLDERS = ("XXXXX", "...", "<", ">")


@dataclass(frozen=True)
class Fund:
    symbol: str
    url: str


@dataclass(frozen=True)
class Sheet:
    spreadsheet_id: str
    worksheet: str


@dataclass(frozen=True)
class Config:
    funds: list[Fund]
    sheet: Sheet | None = None  # no [sheet] section: the sheet stage is skipped


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


def _sheet(table: object) -> Sheet:
    where = "[sheet]"
    if not isinstance(table, dict):
        raise ConfigError(f"{where} must be a table")
    reject_unknown_keys(table, {"spreadsheet_id", "worksheet"}, where)
    spreadsheet_id = require_str(table, "spreadsheet_id", where)
    worksheet = require_str(table, "worksheet", where)
    if any(p in spreadsheet_id or p in worksheet for p in _PLACEHOLDERS):
        raise ConfigError(f"{where}: still has placeholder values from config.example.toml")
    # The ID is private, so the message never repeats it
    if not _SPREADSHEET_ID.match(spreadsheet_id):
        raise ConfigError(
            f'{where}: "spreadsheet_id" must be the ID from the sheet\'s address '
            "(the part between /d/ and /edit), not the whole address"
        )
    return Sheet(spreadsheet_id, worksheet)


def parse_config(doc: dict) -> Config:
    """Validate an already-parsed TOML document."""
    reject_unknown_keys(doc, {"funds", "sheet"}, "config")
    funds = [_fund(t, n) for n, t in enumerate(require_tables(doc, "funds"), start=1)]
    symbols = [f.symbol for f in funds]
    dupes = sorted({s for s in symbols if symbols.count(s) > 1})
    if dupes:
        raise ConfigError(f"duplicate fund symbol(s): {', '.join(dupes)}")
    sheet = _sheet(doc["sheet"]) if "sheet" in doc else None
    return Config(funds, sheet)


def load_config(path: Path) -> Config:
    return parse_config(load_toml(path))
