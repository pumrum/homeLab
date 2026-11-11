"""Pure checks: which statement is newest, its file name, and whether a file is that statement.

The newest statement is the row with the latest date, wherever it sits in the list, so a
list in either order works. The saved PDF must then start with %PDF, be a sensible size,
name the account's four-digit suffix, and show that closing date, before it replaces
anything.
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


@dataclass(frozen=True)
class Newest:
    index: int  # which row, in the order given
    closing: dt.date
    same_month: int  # other rows that closed in the same month


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


def newest_statement(rows: list[str]) -> Newest:
    """The row whose latest date is the latest of all. Each row's latest date is its closing
    date, since a period's start comes first. Raises if it can't tell."""
    closings = [max(dates, default=None) for dates in map(dates_in, rows)]
    dated = [(d, i) for i, d in enumerate(closings) if d is not None]
    if not dated:
        raise StatementError(f"no statement date in any of the {len(rows)} row(s)")
    closing, index = max(dated)
    if sum(1 for d, _ in dated if d == closing) > 1:
        raise StatementError(f"two statements close on the same day ({closing})")
    same_month = sum(1 for d, i in dated
                     if i != index and (d.year, d.month) == (closing.year, closing.month))
    return Newest(index, closing, same_month)


def statement_file_name(label: str, closing: dt.date) -> str:
    return f"{label}_{closing:%Y-%m}.pdf"


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
