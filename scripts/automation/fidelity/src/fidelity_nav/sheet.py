"""Pure planning for the Google Sheet copy of nav.csv. No network, no I/O.

The sheet mirrors nav.csv's history, one row per CSV row:

    as_of_date | symbol | nav | retrieved_at

Each run compares the CSV with what's already in the sheet and appends only
the missing rows, in CSV order. A run whose sheet write failed is caught up
by the next run, and re-running never duplicates rows.

Cells are written as typed: as_of_date becomes a date and nav a number, so
sheet formulas and sorting work. retrieved_at is written as text. Columns
right of D are left alone, so formulas can live there.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

HEADER = ["as_of_date", "symbol", "nav", "retrieved_at"]
COLUMNS = "A:D"

# Google Sheets serial dates count days from 1899-12-30
_SHEETS_EPOCH = dt.date(1899, 12, 30)

Key = tuple[dt.date, str, Decimal, str]


class SheetLayoutError(Exception):
    """The worksheet isn't laid out as expected. The message says how to fix it."""


@dataclass(frozen=True)
class SheetPlan:
    rows: list[list[Any]]  # what to append, header first if the sheet is empty
    added: int  # data rows in `rows`


def cell_date(value: Any) -> dt.date:
    """A date cell read unformatted: a serial number, or ISO text."""
    if isinstance(value, bool):
        raise ValueError(f"not a date: {value!r}")
    if isinstance(value, int | float):
        return _SHEETS_EPOCH + dt.timedelta(days=int(value))
    return dt.date.fromisoformat(str(value).strip())


def cell_decimal(value: Any) -> Decimal:
    """A number cell read unformatted (18.9, 19) or as text ("18.90")."""
    try:
        return Decimal(str(value).strip()).normalize()
    except InvalidOperation as e:
        raise ValueError(f"not a number: {value!r}") from e


def _is_blank(row: Sequence[Any]) -> bool:
    return all(str(c).strip() == "" for c in row)


def _sheet_key(row: Sequence[Any], n: int) -> Key:
    cells = list(row) + [""] * (len(HEADER) - len(row))
    try:
        return (cell_date(cells[0]), str(cells[1]).strip(), cell_decimal(cells[2]),
                str(cells[3]).strip())
    except ValueError as e:
        raise SheetLayoutError(f"sheet row {n} can't be read ({e}); fix or delete it") from e


def _csv_key(row: dict[str, str]) -> Key:
    return (dt.date.fromisoformat(row["as_of_date"]), row["symbol"],
            Decimal(row["nav"]).normalize(), row["retrieved_at"])


def to_sheet_row(row: dict[str, str]) -> list[Any]:
    """One CSV row as sheet cells: ISO date text (becomes a date), NAV as a number,
    retrieved_at forced to text with a leading apostrophe."""
    return [row["as_of_date"], row["symbol"], float(Decimal(row["nav"])),
            "'" + row["retrieved_at"]]


def plan_append(
    csv_rows: Iterable[dict[str, str]], sheet_rows: Sequence[Sequence[Any]]
) -> SheetPlan:
    """Rows to append so the sheet holds every CSV row once."""
    data_rows = [r for r in sheet_rows if not _is_blank(r)]
    out: list[list[Any]] = []
    if not data_rows:
        out.append(list(HEADER))
    else:
        header = [str(c).strip() for c in data_rows[0][: len(HEADER)]]
        if header != HEADER:
            raise SheetLayoutError(
                f"row 1 of the sheet must be {', '.join(HEADER)} (columns A to D); "
                f"found {', '.join(header) or 'nothing'}"
            )
    have = {_sheet_key(r, n) for n, r in enumerate(data_rows[1:], start=2)}
    added = 0
    for row in csv_rows:
        key = _csv_key(row)
        if key in have:
            continue
        have.add(key)
        out.append(to_sheet_row(row))
        added += 1
    if not added:
        return SheetPlan([], 0)  # nothing new: don't write a header on its own
    return SheetPlan(out, added)
