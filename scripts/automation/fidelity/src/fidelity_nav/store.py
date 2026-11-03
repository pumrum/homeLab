"""Append NAV rows to the CSV, one row per (symbol, as_of_date).

The file is append-only: rows are never rewritten, and the header is written
only when the file is created. If the same date is read again with a
different NAV, a new row is appended and the latest row wins when read.
"""

import csv
import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

HEADER = ["as_of_date", "symbol", "nav", "retrieved_at", "source_url"]


class Outcome(StrEnum):
    ADDED = "added"
    UNCHANGED = "unchanged"
    REVISED = "revised"  # same date, different NAV: appended, latest wins


@dataclass(frozen=True)
class NavRow:
    as_of_date: dt.date
    symbol: str
    nav: Decimal
    retrieved_at: dt.datetime
    source_url: str

    def to_csv(self) -> list[str]:
        return [
            self.as_of_date.isoformat(),
            self.symbol,
            format(self.nav, "f"),
            self.retrieved_at.isoformat(timespec="seconds"),
            self.source_url,
        ]


def latest_nav(rows: Iterable[dict[str, str]], symbol: str, as_of: dt.date) -> Decimal | None:
    """The NAV of the last row for (symbol, as_of), or None. Pure."""
    found = None
    key = as_of.isoformat()
    for r in rows:
        if r.get("symbol") == symbol and r.get("as_of_date") == key:
            found = Decimal(r["nav"])
    return found


def decide(existing: Decimal | None, new: Decimal) -> Outcome:
    """What to do with a new reading given the latest stored one. Pure."""
    if existing is None:
        return Outcome.ADDED
    return Outcome.UNCHANGED if existing == new else Outcome.REVISED


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != HEADER:
            raise ValueError(f"{path.name} has header {reader.fieldnames}, expected {HEADER}")
        return list(reader)


def append_nav(path: Path, row: NavRow) -> Outcome:
    """Append `row` unless the same NAV is already stored for that date."""
    outcome = decide(latest_nav(read_rows(path), row.symbol, row.as_of_date), row.nav)
    if outcome is Outcome.UNCHANGED:
        return outcome
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        if new_file:
            writer.writerow(HEADER)
        writer.writerow(row.to_csv())
    return outcome
