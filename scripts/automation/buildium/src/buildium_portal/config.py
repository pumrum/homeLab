"""Load and validate C:\\automation\\buildium\\config.toml.

Everything that identifies an entity lives in this private file. The code only ever
works with what's here. Logs and reports use only the neutral key, while an MFA push
goes to a person's phone, so its words can use the abbreviation too.
"""

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
class GlConfig:
    site: Site
    entities: tuple[Entity, ...]
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


def _entity(table: Mapping[str, Any], number: int) -> Entity:
    where = f"[[entities]] number {number}"
    reject_unknown_keys(table, {"key", "abbreviation", "statement_name"}, where)
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
    reject_unknown_keys(doc, {"site", "entities", "statements", "notifications"}, "config")
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


def parse_gl_config(doc: Mapping[str, Any]) -> GlConfig:
    """Validate an already-parsed TOML document for buildium-gl."""
    return GlConfig(*_shared(doc))


def load_gl_config(path: Path) -> GlConfig:
    return parse_gl_config(load_toml(path))
