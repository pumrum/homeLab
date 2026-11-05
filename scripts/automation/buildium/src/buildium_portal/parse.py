"""Work out which statement a run expects, its file name, and whether a PDF is that statement.

The site's button gives the last full period, and every download has the same name. So the
date comes from today's date, and the PDF's own text must agree with it and with the entity
before the file is saved. All entities share one browser, so this is also what stops one
entity's statement from being saved under another's name.
"""

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

_PERIOD = re.compile(r"Statement\s+period\s+(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})")
# The header on every page. Names also appear in the transactions, so those aren't searched
_OWNER = re.compile(r"Rental\s+Owner\s+Statement\s+(.+?)\s*-\s*Generated", re.DOTALL)


class StatementError(Exception):
    """A PDF isn't the expected statement. The message never holds a private value."""


@dataclass(frozen=True)
class Statement:
    owner: str
    period_start: dt.date
    period_end: dt.date


def previous_period_end(today: dt.date) -> dt.date:
    """The last day of the calendar month before `today`."""
    return today.replace(day=1) - dt.timedelta(days=1)


def statement_folder(period_end: dt.date) -> str:
    return f"{period_end:%Y-%m}"


def statement_file_name(period_end: dt.date, system: str, abbreviation: str) -> str:
    return f"{period_end.isoformat()}_{system}_Ledger_{abbreviation}.pdf"


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(path).pages)


def _normalized(name: str) -> str:
    return " ".join(name.split()).casefold()


def _date(value: str) -> dt.date:
    try:
        return dt.datetime.strptime(value, "%m/%d/%Y").date()
    except ValueError:
        raise StatementError(f"the statement period has an invalid date: {value}") from None


def read_statement(text: str) -> Statement:
    """Read the header. Raises StatementError if it's missing or its pages disagree."""
    periods = set(_PERIOD.findall(text))
    owners = {_normalized(owner) for owner in _OWNER.findall(text)}
    if len(periods) != 1:
        raise StatementError(f"expected one statement period, found {len(periods)}")
    if len(owners) != 1:
        raise StatementError(f"expected one owner in the header, found {len(owners)}")
    start, end = periods.pop()
    return Statement(owners.pop(), _date(start), _date(end))


def check_statement(statement: Statement, statement_name: str, period_end: dt.date) -> None:
    """Raise StatementError unless this is that entity's statement for that full month."""
    if statement.owner != _normalized(statement_name):
        raise StatementError("the statement is for a different owner than this entity")
    if statement.period_end != period_end or statement.period_start != period_end.replace(day=1):
        raise StatementError(f"the statement covers {statement.period_start} to "
                             f"{statement.period_end}, not the month ending {period_end}")
