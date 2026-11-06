"""Load and validate C:\\automation\\innago\\config.toml."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from automation_common.config import ConfigError, load_toml, reject_unknown_keys, require_str
from automation_common.notify import STANDARD_NOTIFICATIONS, Template, parse_notifications

_UNIT = re.compile(r"^[A-Za-z0-9_-]+$")
_PLACEHOLDERS = ("...", "<", ">")

NOTIFICATIONS = {
    **STANDARD_NOTIFICATIONS,
    "mfa_failed": Template("{command}", "FAIL: MFA needed"),
}


@dataclass(frozen=True)
class Site:
    """The part of the config every command in this package shares."""

    login_url: str
    app_url: str


@dataclass(frozen=True)
class Invoices:
    page_url: str
    units: tuple[str, ...]  # uppercase, as they appear in saved file names


@dataclass(frozen=True)
class Config:
    site: Site
    invoices: Invoices
    notifications: Mapping[str, Template] = field(default_factory=lambda: NOTIFICATIONS)


def _table(doc: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    table = doc.get(key)
    if not isinstance(table, dict):
        raise ConfigError(f"a [{key}] section is required")
    return table


def _url(table: Mapping[str, Any], key: str, where: str) -> str:
    value = require_str(table, key, where)
    if any(p in value for p in _PLACEHOLDERS):
        raise ConfigError(f"{where}: still has placeholder values from config.example.toml")
    parts = urlsplit(value)
    # The value is private, so the message never repeats it
    if parts.scheme != "https" or not parts.hostname:
        raise ConfigError(f'{where}: "{key}" must be a full https:// address')
    return value


def _units(table: Mapping[str, Any], where: str) -> tuple[str, ...]:
    values = table.get("units")
    if not isinstance(values, list) or not values:
        raise ConfigError(f'{where}: "units" must list at least one unit')
    if not all(isinstance(v, str) for v in values):
        raise ConfigError(f'{where}: every value in "units" must be quoted text')
    if any(p in v for v in values for p in _PLACEHOLDERS):
        raise ConfigError(f"{where}: still has placeholder values from config.example.toml")
    bad = sorted(v for v in values if not _UNIT.match(v))
    if bad:
        raise ConfigError(f'{where}: "units" can only use letters, digits, _ or -: {bad}')
    units = [v.upper() for v in values]
    repeats = sorted({u for u in units if units.count(u) > 1})
    if repeats:
        raise ConfigError(f"{where}: duplicate unit(s): {', '.join(repeats)}")
    return tuple(units)


def parse_config(doc: Mapping[str, Any]) -> Config:
    """Validate an already-parsed TOML document."""
    reject_unknown_keys(doc, {"site", "invoices", "notifications"}, "config")
    site, invoices = _table(doc, "site"), _table(doc, "invoices")
    reject_unknown_keys(site, {"login_url", "app_url"}, "[site]")
    reject_unknown_keys(invoices, {"page_url", "units"}, "[invoices]")
    return Config(
        Site(_url(site, "login_url", "[site]"), _url(site, "app_url", "[site]")),
        Invoices(_url(invoices, "page_url", "[invoices]"), _units(invoices, "[invoices]")),
        parse_notifications(doc, NOTIFICATIONS),
    )


def load_config(path: Path) -> Config:
    return parse_config(load_toml(path))
