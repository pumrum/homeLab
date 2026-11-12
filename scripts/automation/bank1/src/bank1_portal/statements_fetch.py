"""Browser step: from a signed-in page, open one account's statements page and save the newest.

Each account's statements page is reached by its own direct address from the private config.
The run first makes sure it landed on that account's statements page, by the account heading
above the list, and fails with "statements link no longer valid" otherwise, rather than
downloading the wrong account's files. It doesn't fall back to the site's menus. The heading
is the only place to check: the page's side menu lists every account.

The list is a table filtered by year, newest year first. Each row has a date cell, a type
cell, and its controls. Rows that share a date sit in a group, and only the group's first
row shows the date. The kind of account sets which type of row is its statement:

    checking     "Statement"
    card         "Statement"
    investment   "Single", this account alone, not "Multi", which combines several accounts

Early in a year, the newest year may hold no statement yet, so the run then views the year
before, once.

A row is downloaded one of two ways, whichever it has:

    a download menu    open it, then choose the standard PDF, never the accessible one
    a download link    click it

Each row also has a control that opens the document for viewing. The run never uses it.
If the site changes, update the selectors here from a --dump or --capture run.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Locator, Page

from automation_common.browser import capture_pdf, click_until, find_first
from bank1_portal.config import Account, Kind
from bank1_portal.login import PASSWORD_FIELD, USERNAME_FIELD, on_dashboard
from bank1_portal.pages import FetchError, element_text
from bank1_portal.parse import Newest, Row, newest_of_type, of_type, page_shows

log = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 45_000
STEP_TIMEOUT_MS = 10_000
DOWNLOAD_TIMEOUT_MS = 60_000
POLL_MS = 500

ROW_TYPE = {
    Kind.CHECKING: "Statement",
    Kind.CARD: "Statement",
    Kind.INVESTMENT: "Single",
}

# The heading above the list names the account whose statements these are
ACCOUNT_HEADING = ["#pageTitle", ".document-list-header h2"]
# The statements section is there, whether or not the year shown has any rows
STATEMENTS_SECTION = ["#accountsAccordian-STATEMENTS", "#accountsTable-STATEMENTS"]
# One per statement, letter, or notice in the statements section
STATEMENT_ROW = ["tr[id^='accountsTable-STATEMENTS-row']", "#accountsTable-STATEMENTS tbody tr"]
DATE_CELL = ["td[id$='-cell0']", "td:nth-of-type(1)"]
TYPE_CELL = ["td[id$='-cell1']", "td:nth-of-type(2)"]

# Inside a row. The menu's header opens it; the standard PDF is the first choice in it
DOWNLOAD_MENU = ["a[id^='header-'][id$='-downloadDocumentDropdown']",
                 "[id$='-downloadDocumentDropdown'] a.header"]
STANDARD_PDF = ["a[id$='-downloadPDFOption']"]
DOWNLOAD_LINK = ["a[id$='-requestThisDocumentAnchor-download']"]

# The year filter above the list
YEAR_FILTER = ["#header-filterstyledselect-0", "input[id^='header-filterstyledselect']"]
YEAR_FILTER_OPEN = ["#iconButton-filterstyledselect-0",
                    "button[id^='iconButton-filterstyledselect']", "#header-filterstyledselect-0"]


def _year_option(year: int) -> list[str]:
    return [f"[role='option']:has(.primary:text-is('{year}'))",
            f"[role='option']:has-text('{year}')"]


@dataclass(frozen=True)
class Pick:
    row: Locator
    newest: Newest
    count: int  # rows of the wanted type in the year shown


class LinkError(FetchError):
    """The account's address no longer leads to its statements page."""


def _ignore(_: str) -> None:
    pass


def _visible(page: Page, candidates: list[str]) -> list[Locator]:
    found, selector = find_first([page], candidates, 0)
    if found is None:
        return []
    matches = page.locator(selector)
    return [matches.nth(i) for i in range(min(matches.count(), 100)) if matches.nth(i).is_visible()]


def _landed_wrong(page: Page, account: Account) -> str | None:
    if find_first([page], USERNAME_FIELD + PASSWORD_FIELD, 0)[0] is not None:
        return "landed on a sign-in page"
    if on_dashboard(page.url) and not on_dashboard(account.statements_url):
        return "landed on the dashboard"
    return None


def go_to_statements(page: Page, account: Account) -> None:
    """Load the account's statements page: its one page load this run."""
    log.info("statement: opening %s's statements page", account.label)
    page.goto(account.statements_url, wait_until="domcontentloaded")


def check_statements_page(page: Page, account: Account) -> None:
    """Wait for the statements section, and check its heading names this account."""
    deadline = time.monotonic() + PAGE_TIMEOUT_MS / 1000
    while find_first([page], STATEMENTS_SECTION, 0)[0] is None:
        wrong = _landed_wrong(page, account)
        if wrong is None and time.monotonic() > deadline:
            wrong = f"no statements list after {PAGE_TIMEOUT_MS // 1000}s"
        if wrong is not None:
            raise LinkError(f"statements link no longer valid: {wrong}")
        page.wait_for_timeout(POLL_MS)
    heading = find_first([page], ACCOUNT_HEADING, STEP_TIMEOUT_MS)[0]
    if heading is None:
        raise LinkError("statements link no longer valid: no account heading above the list")
    if not page_shows(element_text(heading), account.match):
        raise LinkError("statements link no longer valid: the page is for another account")
    log.info("statement: on %s's statements page", account.label)


def _cell(row: Locator, candidates: list[str]) -> str:
    found = find_first([row], candidates, 0)[0]
    return element_text(found) if found is not None else ""


def _read_rows(page: Page) -> tuple[list[Locator], list[Row]]:
    rows = _visible(page, STATEMENT_ROW)
    return rows, [Row(_cell(r, DATE_CELL), _cell(r, TYPE_CELL)) for r in rows]


def _year_shown(page: Page) -> int | None:
    box = find_first([page], YEAR_FILTER, 0)[0]
    if box is None:
        return None
    for text in (box.input_value(), box.get_attribute("aria-label") or ""):
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) == 4:
            return int(digits)
    return None


def _view_year_before(page: Page, wanted: str) -> None:
    """Switch the list to the year before the one shown, and wait for its rows."""
    year = _year_shown(page)
    if year is None:
        raise FetchError(f"no '{wanted}' statement this year, and no year filter to look back")
    opener = find_first([page], YEAR_FILTER_OPEN, STEP_TIMEOUT_MS)[0]
    if opener is None:
        raise FetchError(f"no '{wanted}' statement in {year}, and the year filter is missing")
    option = None

    def options_showing() -> bool:
        nonlocal option
        option = find_first([page], _year_option(year - 1), STEP_TIMEOUT_MS)[0]
        return option is not None

    if click_until(opener, options_showing, "year filter") is None or option is None:
        raise FetchError(f"no '{wanted}' statement in {year}, and {year - 1} can't be chosen")
    log.info("statement: none in %d yet; viewing %d", year, year - 1)
    option.click()
    deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000
    while _year_shown(page) != year - 1 or not of_type(_read_rows(page)[1], wanted):
        if time.monotonic() > deadline:
            raise FetchError(f"no '{wanted}' statement in {year} or {year - 1}")
        page.wait_for_timeout(POLL_MS)


def find_newest(page: Page, account: Account) -> Pick:
    """The newest statement of the account's kind, looking back one year if needed."""
    wanted = ROW_TYPE[account.kind]
    # The table can fill in after the page appears, so wait a little before looking back
    deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000
    rows, cells = _read_rows(page)
    while not of_type(cells, wanted) and time.monotonic() < deadline:
        page.wait_for_timeout(POLL_MS)
        rows, cells = _read_rows(page)
    if not of_type(cells, wanted):
        _view_year_before(page, wanted)
        rows, cells = _read_rows(page)
    newest = newest_of_type(cells, wanted)
    count = len(of_type(cells, wanted))
    log.info("statement: %d '%s' row(s) of %d; the newest closed %s", count, wanted, len(rows),
             newest.closing)
    if newest.same_month:
        log.warning("statement: %d other statement(s) also closed in %s", newest.same_month,
                    f"{newest.closing:%Y-%m}")
    return Pick(rows[newest.index], newest, count)


def _download(page: Page, row: Locator, dest: Path, observer: Callable[[str], None]) -> None:
    """The row's download menu and its standard PDF, or else its download link."""
    menu, selector = find_first([row], DOWNLOAD_MENU, 0)
    if menu is not None:
        log.info("statement: opening the download menu (%s)", selector)
        option = None

        def option_showing() -> bool:
            nonlocal option
            option = find_first([row], STANDARD_PDF, STEP_TIMEOUT_MS)[0]
            return option is not None

        if click_until(menu, option_showing, "download menu") is None or option is None:
            raise FetchError("the download menu has no standard PDF choice")
        observer("menu")
        capture_pdf(page, option.click, dest, DOWNLOAD_TIMEOUT_MS)
        return
    link, selector = find_first([row], DOWNLOAD_LINK, STEP_TIMEOUT_MS)
    if link is None:
        raise FetchError("the newest statement has no download menu or link")
    log.info("statement: clicking the download link (%s)", selector)
    capture_pdf(page, link.click, dest, DOWNLOAD_TIMEOUT_MS)


def fetch_statement(page: Page, account: Account, dest: Path,
                    observer: Callable[[str], None] = _ignore) -> Newest:
    """Save the account's newest statement to `dest`, and return which one it was."""
    go_to_statements(page, account)
    check_statements_page(page, account)
    observer("statements")
    pick = find_newest(page, account)
    log.info("statement: downloading the one closing %s", pick.newest.closing)
    _download(page, pick.row, dest, observer)
    return pick.newest
