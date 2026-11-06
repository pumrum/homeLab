"""Load and validate C:\\automation\\invoices\\config.toml."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from automation_common.config import (
    ConfigError,
    load_toml,
    reject_unknown_keys,
    require_str,
    require_tables,
)
from automation_common.notify import STANDARD_NOTIFICATIONS, Template, parse_notifications

_CODE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FILE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")  # a bare name: no folders, no leading dot
_CELL = re.compile(r"^([A-Z]{1,3})[1-9][0-9]*$")
_SPREADSHEET_ID = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_PLACEHOLDERS = ("...", "<", ">")
DEFAULT_PORT = 22


@dataclass(frozen=True)
class Sftp:
    host: str
    port: int
    user: str
    folder: str  # absolute, as the SFTP login sees it


@dataclass(frozen=True)
class Sheet:
    spreadsheet_id: str
    worksheet: str


@dataclass(frozen=True)
class Entity:
    code: str
    enabled: bool
    file: str
    start_cell: str


@dataclass(frozen=True)
class Config:
    sftp: Sftp
    sheet: Sheet
    entities: list[Entity]
    notifications: Mapping[str, Template] = field(default_factory=lambda: STANDARD_NOTIFICATIONS)

    @property
    def enabled_entities(self) -> list[Entity]:
        return [e for e in self.entities if e.enabled]


def _text(table: Mapping[str, Any], key: str, where: str) -> str:
    """A required text value that's no longer a placeholder from config.example.toml."""
    value = require_str(table, key, where)
    if any(p in value for p in _PLACEHOLDERS):
        raise ConfigError(f"{where}: still has placeholder values from config.example.toml")
    return value


def _table(doc: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    table = doc.get(key)
    if not isinstance(table, dict):
        raise ConfigError(f"a [{key}] section is required")
    return table


def _sftp(table: Mapping[str, Any]) -> Sftp:
    where = "[sftp]"
    reject_unknown_keys(table, {"host", "port", "user", "folder"}, where)
    port = table.get("port", DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigError(f'{where}: "port" must be a number from 1 to 65535')
    folder = _text(table, "folder", where)
    if not folder.startswith("/"):
        raise ConfigError(f'{where}: "folder" must start with / (as the SFTP login sees it)')
    return Sftp(_text(table, "host", where), port, _text(table, "user", where), folder)


def _sheet(table: Mapping[str, Any]) -> Sheet:
    where = "[sheet]"
    reject_unknown_keys(table, {"spreadsheet_id", "worksheet"}, where)
    spreadsheet_id = _text(table, "spreadsheet_id", where)
    # The ID is private, so the message never repeats it
    if not _SPREADSHEET_ID.match(spreadsheet_id):
        raise ConfigError(
            f'{where}: "spreadsheet_id" must be the ID from the sheet\'s address '
            "(the part between /d/ and /edit), not the whole address"
        )
    return Sheet(spreadsheet_id, _text(table, "worksheet", where))


def _entity(table: Mapping[str, Any], n: int) -> Entity:
    where = f"[[entities]] #{n}"
    reject_unknown_keys(table, {"code", "enabled", "file", "start_cell"}, where)
    code = _text(table, "code", where)
    if not _CODE.match(code):
        raise ConfigError(f'{where}: code "{code}" must be lowercase letters, digits, _ or -')
    enabled = table.get("enabled")
    if not isinstance(enabled, bool):
        raise ConfigError(f'{where} ({code}): "enabled" must be true or false, without quotes')
    file = _text(table, "file", where)
    if not _FILE.match(file):
        raise ConfigError(
            f'{where} ({code}): "file" must be a file name only, like inv_example.txt'
        )
    start_cell = _text(table, "start_cell", where)
    if not _CELL.match(start_cell):
        raise ConfigError(f'{where} ({code}): "start_cell" must be a cell like B5')
    return Entity(code, enabled, file, start_cell)


def _reject_repeats(values: list[str], what: str) -> None:
    dupes = sorted({v for v in values if values.count(v) > 1})
    if dupes:
        raise ConfigError(f"duplicate {what}: {', '.join(dupes)}")


def _column(cell: str) -> str:
    match = _CELL.match(cell)
    assert match is not None  # checked by _entity
    return match.group(1)


def parse_config(doc: Mapping[str, Any]) -> Config:
    """Validate an already-parsed TOML document."""
    reject_unknown_keys(doc, {"sftp", "sheet", "entities", "notifications"}, "config")
    entities = [_entity(t, n) for n, t in enumerate(require_tables(doc, "entities"), start=1)]
    _reject_repeats([e.code for e in entities], "entity code(s)")
    _reject_repeats([e.file for e in entities], "file name(s)")
    # A replace clears the column to the bottom, so two blocks can't share a column
    _reject_repeats([_column(e.start_cell) for e in entities], "start cell column(s)")
    cfg = Config(_sftp(_table(doc, "sftp")), _sheet(_table(doc, "sheet")), entities,
                 parse_notifications(doc, STANDARD_NOTIFICATIONS))
    if not cfg.enabled_entities:
        raise ConfigError("no entity has enabled = true, so there is nothing to do")
    return cfg


def load_config(path: Path) -> Config:
    return parse_config(load_toml(path))
