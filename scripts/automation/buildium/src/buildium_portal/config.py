"""Load and validate C:\\automation\\buildium\\config.toml.

Everything that identifies an entity lives in this private file. The code only ever
works with what's here. Logs and reports use only the neutral key, while an MFA push
goes to a person's phone, so its words can use the abbreviation too.
"""

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from automation_common.config import (
    ConfigError,
    load_toml,
    reject_unknown_keys,
    require_str,
    require_tables,
)
from automation_common.notify import (
    RUN_VARIABLES,
    STANDARD_NOTIFICATIONS,
    Template,
    parse_notifications,
)

_KEY = re.compile(r"^[a-z0-9-]+$")  # goes in a credential's name and a run folder's file names
_FILE_NAME_PART = re.compile(r"^[A-Za-z0-9-]+$")  # "_" separates a saved file's name parts
_CELL = re.compile(r"^[A-Z]+[1-9][0-9]*$")
_PLACEHOLDERS = ("...", "<", ">")

NOTIFICATIONS = {
    **STANDARD_NOTIFICATIONS,
    "mfa_failed": Template("{command}", "FAIL: MFA needed"),
    "mfa_needed": Template("{command}", "{abbreviation}: MFA link needed",
                           RUN_VARIABLES | {"key", "abbreviation"}),
}


@dataclass(frozen=True)
class Site:
    """The part of the config every command in this package shares."""

    login_url: str


@dataclass(frozen=True)
class Entity:
    """One login. Every command in this package shares the list of these."""

    key: str
    abbreviation: str
    statement_name: str


@dataclass(frozen=True)
class Statements:
    system: str


@dataclass(frozen=True)
class Config:
    site: Site
    entities: tuple[Entity, ...]
    statements: Statements
    notifications: Mapping[str, Template] = field(default_factory=lambda: NOTIFICATIONS)


@dataclass(frozen=True)
class GlEntity:
    """Where one entity goes in the sheet."""

    key: str
    code: str  # the master tab's Entity column
    tab: str
    start_cell: str
    write_tab: bool = False  # off: the sheet builds this entity's rows from the master


@dataclass(frozen=True)
class UnitFix:
    """For one entity: this lease's rows dated before `before` get this unit number."""

    key: str
    lease_id: str
    before: dt.date
    unit_number: str


@dataclass(frozen=True)
class Gl:
    spreadsheet_id: str
    master_tab: str
    entities: Mapping[str, GlEntity]
    unit_fixes: tuple[UnitFix, ...]


@dataclass(frozen=True)
class GlConfig:
    site: Site
    entities: tuple[Entity, ...]
    gl: Gl
    notifications: Mapping[str, Template] = field(default_factory=lambda: NOTIFICATIONS)


def _table(doc: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    table = doc.get(key)
    if not isinstance(table, dict):
        raise ConfigError(f"a [{key}] section is required")
    return table


def _text(table: Mapping[str, Any], key: str, where: str) -> str:
    value = require_str(table, key, where)
    if any(p in value for p in _PLACEHOLDERS):
        raise ConfigError(f"{where}: still has placeholder values from config.example.toml")
    return value


def _url(table: Mapping[str, Any], key: str, where: str) -> str:
    value = _text(table, key, where)
    parts = urlsplit(value)
    # The value is private, so the message never repeats it
    if parts.scheme != "https" or not parts.hostname:
        raise ConfigError(f'{where}: "{key}" must be a full https:// address')
    return value


def _file_name_part(table: Mapping[str, Any], key: str, where: str) -> str:
    value = _text(table, key, where)
    # The value is private, so the message never repeats it
    if not _FILE_NAME_PART.match(value):
        raise ConfigError(f'{where}: "{key}" can only use letters, digits, or -')
    return value


def _entity_where(number: int) -> str:
    return f"[[entities]] number {number}"


def _entity(table: Mapping[str, Any], number: int) -> Entity:
    where = _entity_where(number)
    # The gl_ values are only checked by buildium-gl
    reject_unknown_keys(table, {"key", "abbreviation", "statement_name", "gl_code", "gl_tab",
                                "gl_start_cell", "gl_write_tab", "gl_unit_fixes"}, where)
    key = _text(table, "key", where)
    if not _KEY.match(key):
        raise ConfigError(f'{where}: "key" can only use lowercase letters, digits, or -')
    return Entity(key, _file_name_part(table, "abbreviation", where),
                  _text(table, "statement_name", where))


def _reject_repeats(entities: list[Entity]) -> None:
    keys = [entity.key for entity in entities]
    repeats = sorted({key for key in keys if keys.count(key) > 1})
    if repeats:
        raise ConfigError(f"[[entities]]: duplicate key(s): {', '.join(repeats)}")
    # Two entities with one abbreviation would overwrite each other's files
    abbreviations = [entity.abbreviation.upper() for entity in entities]
    if len(set(abbreviations)) != len(abbreviations):
        raise ConfigError('[[entities]]: two entities have the same "abbreviation"')


def _shared(doc: Mapping[str, Any]) -> tuple[Site, tuple[Entity, ...], Mapping[str, Template]]:
    """The sections every command needs. Each command checks only its own section besides."""
    reject_unknown_keys(doc, {"site", "entities", "statements", "gl", "notifications"}, "config")
    site = _table(doc, "site")
    reject_unknown_keys(site, {"login_url"}, "[site]")
    entities = [_entity(table, number)
                for number, table in enumerate(require_tables(doc, "entities"), start=1)]
    _reject_repeats(entities)
    return (Site(_url(site, "login_url", "[site]")), tuple(entities),
            parse_notifications(doc, NOTIFICATIONS))


def parse_config(doc: Mapping[str, Any]) -> Config:
    """Validate an already-parsed TOML document for buildium-statements."""
    site, entities, notifications = _shared(doc)
    statements = _table(doc, "statements")
    reject_unknown_keys(statements, {"system"}, "[statements]")
    return Config(site, entities, Statements(_file_name_part(statements, "system", "[statements]")),
                  notifications)


def load_config(path: Path) -> Config:
    return parse_config(load_toml(path))


def _flag(table: Mapping[str, Any], name: str, where: str) -> bool:
    """An optional true or false, off when it's left out."""
    value = table.get(name, False)
    if not isinstance(value, bool):
        raise ConfigError(f'{where}: "{name}" must be true or false, without quotes')
    return value


def _gl_entity(table: Mapping[str, Any], key: str, where: str) -> GlEntity:
    start_cell = _text(table, "gl_start_cell", where)
    # The value is private, so the message never repeats it
    if not _CELL.match(start_cell):
        raise ConfigError(f'{where}: "gl_start_cell" must be a cell like B5')
    return GlEntity(key, _text(table, "gl_code", where), _text(table, "gl_tab", where), start_cell,
                    _flag(table, "gl_write_tab", where))


def _unit_fix(table: Mapping[str, Any], key: str, where: str) -> UnitFix:
    reject_unknown_keys(table, {"lease_id", "before", "unit_number"}, where)
    try:
        before = dt.date.fromisoformat(_text(table, "before", where))
    except ValueError:
        raise ConfigError(f'{where}: "before" must be a date like 2030-01-31') from None
    return UnitFix(key, _text(table, "lease_id", where), before,
                   _text(table, "unit_number", where))


def _unit_fixes(table: Mapping[str, Any], key: str, where: str) -> list[UnitFix]:
    value = table.get("gl_unit_fixes", [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ConfigError(f'{where}: "gl_unit_fixes" must be a list of {{ ... }} entries')
    return [_unit_fix(item, key, f"{where}, gl_unit_fixes number {number}")
            for number, item in enumerate(value, start=1)]


def _parse_gl(doc: Mapping[str, Any], entities: tuple[Entity, ...]) -> Gl:
    gl = _table(doc, "gl")
    reject_unknown_keys(gl, {"spreadsheet_id", "master_tab"}, "[gl]")
    targets: dict[str, GlEntity] = {}
    fixes: list[UnitFix] = []
    tables = require_tables(doc, "entities")
    for number, (entity, table) in enumerate(zip(entities, tables, strict=True), start=1):
        where = _entity_where(number)
        targets[entity.key] = _gl_entity(table, entity.key, where)
        fixes += _unit_fixes(table, entity.key, where)
    master_tab = _text(gl, "master_tab", "[gl]")
    tabs = [target.tab for target in targets.values()] + [master_tab]
    if len(set(tabs)) != len(tabs):
        raise ConfigError("[gl]: every entity and the master need their own tab")
    return Gl(_text(gl, "spreadsheet_id", "[gl]"), master_tab, targets, tuple(fixes))


def parse_gl_config(doc: Mapping[str, Any]) -> GlConfig:
    """Validate an already-parsed TOML document for buildium-gl."""
    site, entities, notifications = _shared(doc)
    return GlConfig(site, entities, _parse_gl(doc, entities), notifications)


def load_gl_config(path: Path) -> GlConfig:
    return parse_gl_config(load_toml(path))
