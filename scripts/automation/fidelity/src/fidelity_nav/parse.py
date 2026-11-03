"""Pure parsing: fund page HTML -> (NAV, as-of date). No browser, no I/O.

Fund pages label the value "NAV" or, for 529 portfolios, "Unit Value". Each
is shown twice: in the "Daily Info" header (value plus an "AS OF" label) and
as a row in the "Details" card (label, date, value). Strategies, in order:

  1. header selectors (value + "AS OF" label)
  2. Details row whose title is one of NAV_LABELS
  3. text fallback: regexes over the page text near "NAV"/"Unit Value"/"AS OF"
"""

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, Tag

MAX_AGE_DAYS = 10

NAV_LABELS = ("NAV", "Unit Value")

# Candidates, most specific first. fetch.py waits for the same ones.
HEADER_VALUE_SELECTORS = [
    "mfl-daily-info-nav .value-column",
    "mfl-daily-info-nav [class*='value']",
]
HEADER_ASOF_SELECTORS = [
    'mfl-label[section="dailyInfo"][label="ASOF"]',
    '[section="dailyInfo"][label="ASOF"]',
]
ROW_SELECTOR = "mfl-card-data-row"
ROW_TITLE_SELECTORS = [".card-row-title", "mfl-label"]
ROW_VALUE_SELECTORS = [".data-text"]
ROW_DATE_SELECTORS = [".date-text"]

_DATE = r"(\d{1,2}/\d{1,2}/\d{4})"
_MONEY = r"\$?\s*(\d[\d,]*\.\d+)"
# Not preceded by a letter; may follow a digit, since the page's text runs
# elements together without spaces ("AS OF 11/02/2026Unit Value$18.90")
_LABEL = r"(?<![A-Za-z])(?:NAV|Unit Value)"
# (pattern, which group is the date, which is the value)
TEXT_PATTERNS: list[tuple[re.Pattern[str], int, int]] = [
    # Daily Info header: "AS OF 11/02/2026 Unit Value $18.90"
    (re.compile(rf"AS OF\s*{_DATE}\s*{_LABEL}\s*{_MONEY}", re.I), 1, 2),
    # Details row: "Unit Value 11/02/2026 $18.90"
    (re.compile(rf"{_LABEL}\s*{_DATE}\s*{_MONEY}", re.I), 1, 2),
    # Prose: "NAV $18.90 as of 11/02/2026"
    (re.compile(rf"{_LABEL}\s*{_MONEY}\s*as of\s*{_DATE}", re.I), 2, 1),
]


class ParseError(Exception):
    """The page didn't contain a usable NAV and date. The message says why."""


@dataclass(frozen=True)
class NavReading:
    nav: Decimal
    as_of: dt.date
    method: str  # which strategy found it, for the log


def parse_money(text: str) -> Decimal:
    """'$1,018.90' -> Decimal('1018.90')."""
    cleaned = text.strip().replace("$", "").replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as e:
        raise ParseError(f"NAV '{text.strip()}' is not a number") from e
    if not value.is_finite():
        raise ParseError(f"NAV '{text.strip()}' is not a number")
    return value


def parse_date(text: str) -> dt.date:
    """Find a date like 11/02/2026 or Nov 2, 2026 in `text`."""
    m = re.search(_DATE, text)
    if m:
        return dt.datetime.strptime(m.group(1), "%m/%d/%Y").date()
    m = re.search(r"([A-Z][a-z]{2,8})\.? (\d{1,2}), (\d{4})", text)
    if m:
        for fmt in ("%b %d %Y", "%B %d %Y"):
            try:
                return dt.datetime.strptime(" ".join(m.groups()), fmt).date()
            except ValueError:
                continue
    raise ParseError(f"no date found in '{text.strip()[:60]}'")


def _select_text(scope: Tag, selectors: list[str]) -> str | None:
    for sel in selectors:
        el = scope.select_one(sel)
        if el is not None:
            text = el.get_text(" ", strip=True)
            if text:
                return text
    return None


def _from_header(soup: BeautifulSoup) -> tuple[str, str] | None:
    value = _select_text(soup, HEADER_VALUE_SELECTORS)
    as_of = _select_text(soup, HEADER_ASOF_SELECTORS)
    return (value, as_of) if value and as_of else None


def _from_details_row(soup: BeautifulSoup) -> tuple[str, str] | None:
    for row in soup.select(ROW_SELECTOR):
        title = _select_text(row, ROW_TITLE_SELECTORS)
        if title not in NAV_LABELS:
            continue
        value = _select_text(row, ROW_VALUE_SELECTORS)
        as_of = _select_text(row, ROW_DATE_SELECTORS)
        if value and as_of:
            return value, as_of
    return None


def _from_text(soup: BeautifulSoup) -> tuple[str, str] | None:
    text = soup.get_text(" ", strip=True)
    for pattern, date_group, value_group in TEXT_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(value_group), m.group(date_group)
    return None


STRATEGIES: list[tuple[str, Callable[[BeautifulSoup], tuple[str, str] | None]]] = [
    ("header", _from_header),
    ("details row", _from_details_row),
    ("text fallback", _from_text),
]


def validate(reading: NavReading, today: dt.date, max_age_days: int = MAX_AGE_DAYS) -> None:
    """NAV must be positive; as-of date not in the future nor older than max_age_days."""
    if reading.nav <= 0:
        raise ParseError(f"NAV {reading.nav} is not positive")
    if reading.as_of > today:
        raise ParseError(f"as-of date {reading.as_of} is in the future (today is {today})")
    age = (today - reading.as_of).days
    if age > max_age_days:
        raise ParseError(
            f"as-of date {reading.as_of} is {age} days old (limit {max_age_days}); "
            "the page may be stale or the wrong date was read"
        )


def parse_nav_page(html: str, today: dt.date) -> NavReading:
    """Return the validated NAV and as-of date from a fund page's HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for method, strategy in STRATEGIES:
        found = strategy(soup)
        if found is None:
            continue
        value_text, date_text = found
        reading = NavReading(parse_money(value_text), parse_date(date_text), method)
        validate(reading, today)
        return reading
    raise ParseError(
        "NAV not found: no header, no Details row labeled "
        + "/".join(NAV_LABELS)
        + ", and no match in the page text"
    )
