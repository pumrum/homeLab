"""Load and validate C:\\automation\\bank1\\config.toml.

Everything that identifies an account lives in this private file: its statements and activity
addresses, its four-digit suffix, and any text its pages are checked for, plus the bank's name
as used in saved file names, and the Google Sheets its activity goes to. The code, logs, and
reports only ever use the neutral label.

Every command in this package reads the whole file, so a mistake anywhere in it stops them all.
"""

import datetime as dt
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
_CELL = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*$")
_ABBREVIATION = re.compile(r"^[A-Za-z0-9-]+$")  # "_" separates a saved file's name parts
_ACCOUNT_KEYS = {"label", "kind", "statements_url", "suffix", "match", "abbreviation",
                 "activity_url", "activity_start", "activity_sheet"}

NOTIFICATIONS = {
    **STANDARD_NOTIFICATIONS,
    "mfa_failed": Template("{command}", "FAIL: verification timed out"),
    "mfa_needed": Template("{command}", "Approve the sign-in on your phone"),
}


class Kind(StrEnum):
    """What kind of account it is. Each kind's pages have their own small differences."""

    CHECKING = "checking"
    CARD = "card"
    INVESTMENT = "investment"


@dataclass(frozen=True)
class Site:
    """The part of the config every command in this package shares."""

    login_url: str
    name_in_files: str  # the bank's name in saved file names, as in 2026-10-31_<name>_1234.pdf


@dataclass(frozen=True)
class Workbook:
    """A Google Sheet that activity goes to, named once under [sheets]."""

    name: str  # neutral, so it can be logged
    spreadsheet_id: str
    updated_tab: str | None = None  # optional: where the run's start time goes, once every
    updated_cell: str | None = None  # account writing to this workbook has written

    def __repr__(self) -> str:  # never show the private values, even in a traceback
        return f"Workbook({self.name!r})"


@dataclass(frozen=True)
class Destination:
    """Where an account's activity goes: a tab of a workbook named in [sheets], from a cell."""

    sheet: str  # the workbook's name under [sheets]: neutral, so it can be logged
    spreadsheet_id: str
    tab: str
    start_cell: str

    def __repr__(self) -> str:  # never show the private values, even in a traceback
        return f"Destination({self.sheet!r})"


@dataclass(frozen=True)
class Activity:
    """bank1-activity's settings for one account. Only accounts that have them are in its runs."""

    url: str  # the account's activity download page
    start: dt.date  # the first day of the range; the last is the day of the run
    destination: Destination | None  # None: download only
    abbreviation: str  # starts the saved file's name, like ABC1_1234_20261113-143342_Activity

    def __repr__(self) -> str:
        return f"Activity({self.start.isoformat()!r}, {self.destination!r})"


@dataclass(frozen=True)
class Account:
    label: str
    kind: Kind
    statements_url: str
    suffix: str  # the last four digits, checked in the statement's text
    match: str  # text the account's pages must show; the suffix unless set
    activity: Activity | None = None

    def __repr__(self) -> str:  # never show the private values, even in a traceback
        return f"Account({self.label!r}, {self.kind.value!r})"


@dataclass(frozen=True)
class Config:
    site: Site
    accounts: tuple[Account, ...]
    notifications: Mapping[str, Template] = field(default_factory=lambda: NOTIFICATIONS)
    sheets: Mapping[str, Workbook] = field(default_factory=dict)  # by name


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


def _date(table: Mapping[str, Any], key: str, where: str) -> dt.date:
    value = _text(table, key, where)
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ConfigError(f'{where}: "{key}" must be a date like "2025-07-01"') from None


def _cell(table: Mapping[str, Any], key: str, where: str) -> str:
    cell = _text(table, key, where)
    if not _CELL.match(cell):
        raise ConfigError(f'{where}: "{key}" must be a cell like "A5"')
    return cell


def _workbook(name: str, table: Mapping[str, Any], where: str) -> Workbook:
    reject_unknown_keys(table, {"spreadsheet_id", "updated_tab", "updated_cell"}, where)
    spreadsheet_id = _text(table, "spreadsheet_id", where)
    present = ["updated_tab" in table, "updated_cell" in table]
    if not any(present):
        return Workbook(name, spreadsheet_id)
    if not all(present):
        raise ConfigError(f'{where}: "updated_tab" and "updated_cell" go together')
    return Workbook(name, spreadsheet_id, _text(table, "updated_tab", where),
                    _cell(table, "updated_cell", where))


def _sheets(doc: Mapping[str, Any]) -> dict[str, Workbook]:
    """[sheets.<name>] blocks: each workbook once, with its private spreadsheet ID."""
    sheets = doc.get("sheets", {})
    if not isinstance(sheets, dict):
        raise ConfigError("[sheets] must hold [sheets.<name>] blocks")
    found = {}
    for name, table in sheets.items():
        where = f"[sheets.{name}]"
        if not _LABEL.match(name):
            raise ConfigError(f"{where}: the name can only use lowercase letters, digits, or _")
        if not isinstance(table, dict):
            raise ConfigError(f"{where} must be a table")
        found[name] = _workbook(name, table, where)
    return found


def _destination(table: Any, sheets: Mapping[str, Workbook], where: str) -> Destination:
    where = f"{where} activity_sheet"
    if not isinstance(table, dict):
        raise ConfigError(f"{where} must be a table")
    reject_unknown_keys(table, {"sheet", "tab", "start_cell"}, where)
    sheet = _text(table, "sheet", where)
    if sheet not in sheets:
        raise ConfigError(f'{where}: "sheet" must name a [sheets.<name>] block')
    cell = _cell(table, "start_cell", where)
    return Destination(sheet, sheets[sheet].spreadsheet_id, _text(table, "tab", where), cell)


def _activity(table: Mapping[str, Any], sheets: Mapping[str, Workbook],
              where: str) -> Activity | None:
    if "activity_url" not in table:
        for key in ("activity_start", "activity_sheet", "abbreviation"):
            if key in table:
                raise ConfigError(f'{where}: "{key}" needs an "activity_url"')
        return None
    destination = (_destination(table["activity_sheet"], sheets, where)
                   if "activity_sheet" in table else None)
    abbreviation = _text(table, "abbreviation", where)
    if not _ABBREVIATION.match(abbreviation):
        # The value is private, so the message never repeats it
        raise ConfigError(f'{where}: "abbreviation" can only use letters, digits, or -')
    return Activity(_url(table, "activity_url", where), _date(table, "activity_start", where),
                    destination, abbreviation)


def _account(table: Mapping[str, Any], number: int,
             sheets: Mapping[str, Workbook]) -> Account:
    where = f"[[accounts]] number {number}"
    reject_unknown_keys(table, _ACCOUNT_KEYS, where)
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
    return Account(label, Kind(kind), _url(table, "statements_url", where), suffix, match,
                   _activity(table, sheets, where))


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
    activities = [account.activity for account in accounts if account.activity]
    urls = [activity.url for activity in activities]
    if len(set(urls)) != len(urls):
        raise ConfigError('[[accounts]]: two accounts have the same "activity_url"')
    # Each write replaces everything below its start cell, so one would wipe out the other
    places = [(d.sheet, d.tab, d.start_cell) for a in activities if (d := a.destination)]
    if len(set(places)) != len(places):
        raise ConfigError("[[accounts]]: two accounts write their activity to the same tab "
                          "and start cell")


def parse_config(doc: Mapping[str, Any]) -> Config:
    """Validate an already-parsed TOML document."""
    reject_unknown_keys(doc, {"site", "accounts", "sheets", "notifications"}, "config")
    site = doc.get("site")
    if not isinstance(site, dict):
        raise ConfigError("a [site] section is required")
    reject_unknown_keys(site, {"login_url", "name_in_files"}, "[site]")
    name = _text(site, "name_in_files", "[site]")
    if not _FILE_NAME_PART.match(name):
        # The value is private, so the message never repeats it
        raise ConfigError('[site]: "name_in_files" can\'t use _ \\ / : * ? " < > |')
    sheets = _sheets(doc)
    accounts = [_account(table, number, sheets)
                for number, table in enumerate(require_tables(doc, "accounts"), start=1)]
    _reject_repeats(accounts)
    return Config(Site(_url(site, "login_url", "[site]"), name), tuple(accounts),
                  parse_notifications(doc, NOTIFICATIONS), sheets)


def load_config(path: Path) -> Config:
    return parse_config(load_toml(path))
