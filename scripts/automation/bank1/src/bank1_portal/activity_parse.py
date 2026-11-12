"""Check an activity CSV and turn it into the rows a sheet gets. Pure functions only.

The bank exports one row per transaction, newest first, with a header row. A run checks the
header against the columns expected for the account's kind, so a change on the site fails
loudly instead of shifting every column in the sheet. It checks every row's dates fall in the
range asked for, then reverses the rows so the oldest comes first, as the sheets expect.

Every value stays text, exactly as exported: the sheet gets it as literal text, the way the
old process pasted it.
"""

import csv
import datetime as dt
import io
from dataclasses import dataclass

from bank1_portal.config import Kind

# Each kind's header, in order, and the columns holding a transaction's dates. A row is in
# range if any of its dates is, since the range may filter on either one
COLUMNS: dict[Kind, tuple[str, ...]] = {
    Kind.CHECKING: ("Details", "Posting Date", "Description", "Amount", "Type", "Balance",
                    "Check or Slip #"),
    Kind.CARD: ("Transaction Date", "Post Date", "Description", "Category", "Type", "Amount",
                "Memo"),
}
DATE_COLUMNS: dict[Kind, tuple[str, ...]] = {
    Kind.CHECKING: ("Posting Date",),
    Kind.CARD: ("Transaction Date", "Post Date"),
}
DATE_FORMAT = "%m/%d/%Y"


class ActivityError(Exception):
    """The CSV isn't what a run can save. The message never holds a value from it."""


@dataclass(frozen=True)
class Activity:
    header: tuple[str, ...]
    rows: list[list[str]]  # oldest first, each as wide as the header
    first: dt.date
    last: dt.date

    @property
    def width(self) -> int:
        return len(self.header)


def site_date(day: dt.date) -> str:
    """A date as the site's date boxes take it."""
    return day.strftime(DATE_FORMAT)


def header_problem(kind: Kind, header: list[str]) -> str | None:
    """What's wrong with a header, naming the columns, or None if it's the expected one."""
    expected = list(COLUMNS[kind])
    if header == expected:
        return None
    added = [name for name in header if name not in expected]
    missing = [name for name in expected if name not in header]
    parts = []
    if added:
        parts.append(f"added {', '.join(added)}")
    if missing:
        parts.append(f"missing {', '.join(missing)}")
    if not parts:
        parts.append(f"in a different order: {', '.join(header)}")
    return f"the {kind} columns changed: " + "; ".join(parts)


def _fit(row: list[str], width: int, number: int) -> list[str]:
    """A row as wide as the header. Exports can end a row with empty fields, or leave them out."""
    extra = row[width:]
    if any(value.strip() for value in extra):
        raise ActivityError(f"row {number} has {len(row)} fields; the header has {width}")
    row = row[:width]
    return row + [""] * (width - len(row))


def _row_dates(row: list[str], positions: list[int], number: int) -> list[dt.date]:
    dates = []
    for position in positions:
        value = row[position].strip()
        if not value:
            continue
        try:
            dates.append(dt.datetime.strptime(value, DATE_FORMAT).date())
        except ValueError:
            raise ActivityError(f"row {number} has a date that isn't MM/DD/YYYY") from None
    if not dates:
        raise ActivityError(f"row {number} has no date")
    return dates


def parse_activity(text: str, kind: Kind, start: dt.date, end: dt.date) -> Activity:
    """Check the CSV for this kind and range, and return its rows oldest first."""
    if kind not in COLUMNS:
        raise ActivityError(f"no known activity columns for a {kind} account yet")
    lines = [row for row in csv.reader(io.StringIO(text.lstrip("﻿")))
             if any(value.strip() for value in row)]
    if not lines:
        raise ActivityError("the file is empty")
    header = [name.strip() for name in lines[0]]
    while header and not header[-1]:  # a trailing comma on the header
        header.pop()
    problem = header_problem(kind, header)
    if problem:
        raise ActivityError(problem)
    if len(lines) == 1:
        raise ActivityError("no transactions in the range")
    positions = [header.index(name) for name in DATE_COLUMNS[kind]]
    rows, seen = [], []
    for number, raw in enumerate(lines[1:], start=1):
        row = _fit(raw, len(header), number)
        dates = _row_dates(row, positions, number)
        if not any(start <= day <= end for day in dates):
            raise ActivityError(f"row {number} is dated outside {site_date(start)} to "
                                f"{site_date(end)}")
        seen.extend(day for day in dates if start <= day <= end)
        rows.append(row)
    rows.reverse()  # exported newest first; the sheet wants the oldest first
    return Activity(tuple(header), rows, min(seen), max(seen))
