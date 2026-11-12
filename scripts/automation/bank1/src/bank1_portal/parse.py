"""Pure checks: which rows to save, their file names, and whether a file is that statement.

The statements list is a table. Each row has a date cell and a type cell: "Statement", or
for investments "Multi", "Single", or a letter's name. Rows that share a date sit in a group,
and only the group's first row shows the date. The newest statement is the row of the wanted
type with the latest date, wherever it sits in the list, so a list in either order works.

Each account saves the statements of its kind that closed on the newest date: "Statement",
or for investments both "Single" and "Multi". Any other row is a letter, like a disclosure,
filed under the statement row above it. Letters in the newest group are saved every run;
older ones only once, since they don't change.

File names follow the scheme already used for this bank's statements, with the full closing
date, the bank's name from the private config, and the account's suffix:

    2026-10-31_<name>_1234.pdf                      a statement
    2026-10-31_<name>_1234-single.pdf               an investment's Single or Multi statement
    2026-09-30_<name>_1234-single-<letter's title>.pdf

The saved PDF must then start with %PDF, be a sensible size, name the account's four-digit
suffix, and show that closing date, before it replaces anything.
"""

import calendar
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

MIN_PDF_BYTES = 5_000  # a real statement is far larger; an error page or stub is smaller

_MONTHS = {name.lower(): n for n, name in enumerate(calendar.month_name) if name}
_MONTHS |= {name.lower(): n for n, name in enumerate(calendar.month_abbr) if name}
_MONTHS["sept"] = 9
_MONTH_WORDS = "|".join(sorted(_MONTHS, key=len, reverse=True))
_NAMED = re.compile(rf"\b({_MONTH_WORDS})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.IGNORECASE)
_SLASHED = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


class StatementError(Exception):
    """A page or file isn't the expected statement. The message holds no private value."""


def _date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def dates_in(text: str) -> list[dt.date]:
    """Every whole date in `text`: Oct 31, 2026; October 31 2026; 10/31/2026; 10/31/26;
    2026-10-31. A date with no year, like the start of "Oct 1 - Oct 31, 2026", isn't one."""
    found: list[dt.date | None] = []
    for month, day, year in _NAMED.findall(text):
        found.append(_date(int(year), _MONTHS[month.lower()], int(day)))
    for month, day, year in _SLASHED.findall(text):
        full = int(year) + 2000 if len(year) == 2 else int(year)
        found.append(_date(full, int(month), int(day)))
    for year, month, day in _ISO.findall(text):
        found.append(_date(int(year), int(month), int(day)))
    return [d for d in found if d is not None]


@dataclass(frozen=True)
class Row:
    date: str  # the date cell; empty on a group's later rows
    type: str  # the type cell


def _squash_type(text: str) -> str:
    return " ".join(text.split()).casefold()


def of_type(rows: list[Row], wanted: str) -> list[int]:
    """Which rows have exactly the wanted type, ignoring case and spacing."""
    return [i for i, row in enumerate(rows) if _squash_type(row.type) == _squash_type(wanted)]


@dataclass(frozen=True)
class Item:
    """One row to save."""

    index: int  # which row, in the order given
    closing: dt.date  # its group's date
    part: str  # what follows the suffix in its file name: "", "single", or a letter's
    letter: bool
    newest: bool  # in the newest statement's group


def slug(text: str, limit: int = 60) -> str:
    """A file-name-safe version of a row's title: lowercase words joined by hyphens."""
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return "-".join(words)[:limit].strip("-")


def plan_items(rows: list[Row], types: dict[str, str]) -> list[Item]:
    """The rows to save: each wanted statement type on the newest date, then every letter.

    `types` maps each wanted row type to its part of the file name, like {"Single": "single"}.
    Raises if a wanted type is missing on the newest date, or appears twice that day.
    """
    dates: list[dt.date | None] = []
    for row in rows:
        found = dates_in(row.date)
        dates.append(max(found) if found else (dates[-1] if dates else None))
    wanted = {_squash_type(t): part for t, part in types.items()}
    statements = [i for i, row in enumerate(rows) if _squash_type(row.type) in wanted]
    dated = [dates[i] for i in statements if dates[i] is not None]
    if not dated:
        names = " or ".join(f"'{t}'" for t in types)
        raise StatementError(f"no {names} statement in the {len(rows)} row(s)")
    closing = max(dated)
    items: list[Item] = []
    for type_name in types:
        on_day = [i for i in statements if dates[i] == closing
                  and _squash_type(rows[i].type) == _squash_type(type_name)]
        if not on_day:
            raise StatementError(f"no '{type_name}' statement on {closing}")
        if len(on_day) > 1:
            raise StatementError(f"two '{type_name}' statements close on the same day ({closing})")
        items.append(Item(on_day[0], closing, types[type_name], False, True))
    parent = ""
    for i, row in enumerate(rows):
        kind = _squash_type(row.type)
        if kind in wanted:
            parent = wanted[kind]
        elif dates[i] is not None and slug(row.type):
            part = "-".join(p for p in (parent, slug(row.type)) if p)
            items.append(Item(i, dates[i], part, True, dates[i] == closing))
    return items


def statement_file_name(name: str, suffix: str, closing: dt.date, part: str = "") -> str:
    """The scheme already used for this bank's statements: date, bank name, suffix."""
    tail = f"-{part}" if part else ""
    return f"{closing:%Y-%m-%d}_{name}_{suffix}{tail}.pdf"


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def page_shows(page_text: str, match: str) -> bool:
    """Whether a page's text holds `match`, ignoring case and spacing. A number must stand
    alone, so suffix 1234 doesn't match 91234 or 12345."""
    wanted = _squash(match)
    left = r"(?<!\d)" if wanted[:1].isdigit() else ""
    right = r"(?!\d)" if wanted[-1:].isdigit() else ""
    return re.search(left + re.escape(wanted) + right, _squash(page_text)) is not None


def check_pdf_file(path: Path, min_bytes: int = MIN_PDF_BYTES) -> None:
    """A real PDF, not an error page or an empty file."""
    with path.open("rb") as f:
        head = f.read(5)
    if head != b"%PDF-":
        raise StatementError("the file isn't a PDF")
    size = path.stat().st_size
    if size < min_bytes:
        raise StatementError(f"the PDF is only {size} bytes, under {min_bytes}")


def pdf_text(path: Path) -> str:
    try:
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    except Exception as e:  # noqa: BLE001 - pypdf raises many kinds for a damaged file
        raise StatementError(f"the PDF can't be read ({type(e).__name__})") from None


def check_statement(text: str, suffix: str, closing: dt.date) -> None:
    """The PDF must name this account's suffix and hold the closing date the list showed."""
    if not page_shows(text, suffix):
        raise StatementError("the PDF doesn't show this account's suffix")
    if closing not in dates_in(text):
        raise StatementError(f"the PDF doesn't show the closing date {closing}")
