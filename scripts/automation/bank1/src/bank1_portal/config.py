"""Load and validate C:\\automation\\bank1\\config.toml.

Everything that identifies an account lives in this private file: its statements address,
its four-digit suffix, and any text its page is checked for, plus the bank's name as used in
saved file names. The code, logs, and reports only ever use the neutral label.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
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
from automation_common.notify import STANDARD_NOTIFICATIONS, Template, parse_notifications

_LABEL = re.compile(r"^[a-z0-9_]+$")  # goes in logs, reports, and run file names
_SUFFIX = re.compile(r"^[0-9]{4}$")
_PLACEHOLDERS = ("<", ">")  # not "...": a page may show an account as ...1234
_FILE_NAME_PART = re.compile(r'^[^\\/:*?"<>|_]+$')  # "_" separates the parts of a file name

NOTIFICATIONS = {
    **STANDARD_NOTIFICATIONS,
    "mfa_failed": Template("{command}", "FAIL: verification timed out"),
    "mfa_needed": Template("{command}", "Approve the sign-in on your phone"),
}


class Kind(StrEnum):
    """What kind of statements page an account has. Each has its own small extra step."""

    CHECKING = "checking"
    CARD = "card"
    INVESTMENT = "investment"


@dataclass(frozen=True)
class Site:
    """The part of the config every command in this package shares."""

    login_url: str
    name_in_files: str  # the bank's name in saved file names, as in 2026-10-31_<name>_1234.pdf


@dataclass(frozen=True)
class Account:
    label: str
    kind: Kind
    statements_url: str
    suffix: str  # the last four digits, checked in the statement's text
    match: str  # text the heading above its statements must show; the suffix unless set

    def __repr__(self) -> str:  # never show the private values, even in a traceback
        return f"Account({self.label!r}, {self.kind.value!r})"


@dataclass(frozen=True)
class Config:
    site: Site
    accounts: tuple[Account, ...]
    notifications: Mapping[str, Template] = field(default_factory=lambda: NOTIFICATIONS)


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


def _account(table: Mapping[str, Any], number: int) -> Account:
    where = f"[[accounts]] number {number}"
    reject_unknown_keys(table, {"label", "kind", "statements_url", "suffix", "match"}, where)
    label = _text(table, "label", where)
    if not _LABEL.match(label):
        raise ConfigError(f'{where}: "label" can only use lowercase letters, digits, or _')
    where = f"[[accounts]] {label}"
    kind = _text(table, "kind", where)
    if kind not in set(Kind):
        raise ConfigError(f'{where}: "kind" must be one of {", ".join(Kind)}')
    suffix = _text(table, "suffix", where)
    if not _SUFFIX.match(suffix):
        # The value is private, so the message never repeats it
        raise ConfigError(f'{where}: "suffix" must be exactly four digits, in quotes')
    match = _text(table, "match", where) if "match" in table else suffix
    return Account(label, Kind(kind), _url(table, "statements_url", where), suffix, match)


def _reject_repeats(accounts: list[Account]) -> None:
    labels = [account.label for account in accounts]
    repeats = sorted({label for label in labels if labels.count(label) > 1})
    if repeats:
        raise ConfigError(f"[[accounts]]: duplicate label(s): {', '.join(repeats)}")
    # The same address twice would save one account's statement under two labels
    urls = [account.statements_url for account in accounts]
    if len(set(urls)) != len(urls):
        raise ConfigError('[[accounts]]: two accounts have the same "statements_url"')
    # Every account's files share one folder, told apart by the suffix in their names
    suffixes = [account.suffix for account in accounts]
    if len(set(suffixes)) != len(suffixes):
        raise ConfigError('[[accounts]]: two accounts have the same "suffix", so their files '
                          "would overwrite each other")


def parse_config(doc: Mapping[str, Any]) -> Config:
    """Validate an already-parsed TOML document."""
    reject_unknown_keys(doc, {"site", "accounts", "notifications"}, "config")
    site = doc.get("site")
    if not isinstance(site, dict):
        raise ConfigError("a [site] section is required")
    reject_unknown_keys(site, {"login_url", "name_in_files"}, "[site]")
    name = _text(site, "name_in_files", "[site]")
    if not _FILE_NAME_PART.match(name):
        # The value is private, so the message never repeats it
        raise ConfigError('[site]: "name_in_files" can\'t use _ \\ / : * ? " < > |')
    accounts = [_account(table, number)
                for number, table in enumerate(require_tables(doc, "accounts"), start=1)]
    _reject_repeats(accounts)
    return Config(Site(_url(site, "login_url", "[site]"), name), tuple(accounts),
                  parse_notifications(doc, NOTIFICATIONS))


def load_config(path: Path) -> Config:
    return parse_config(load_toml(path))
