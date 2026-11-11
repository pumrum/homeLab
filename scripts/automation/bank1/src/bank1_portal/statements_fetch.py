"""Browser step: from a signed-in page, open one account's statements page and save the newest.

Each account's statements page is reached by its own direct address from the private config.
The run first makes sure it landed on a statements page for that account, and fails with
"statements link no longer valid" otherwise, rather than downloading the wrong account's
files. It doesn't fall back to the site's menus.

The pages are the same for every account, apart from one small step that depends on its kind:

    checking     after picking the statement, choose the standard PDF (not the accessible one)
    card         no extra step
    investment   before picking the statement, choose a single statement (not multiple)

An expected choice that's missing fails the account. If the site changes, update the
selectors here from a --dump or --capture run.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PwError
from playwright.sync_api import Locator, Page

from automation_common.browser import capture_pdf, click_until, find_first
from bank1_portal.config import Account, Kind
from bank1_portal.login import PASSWORD_FIELD, USERNAME_FIELD, on_dashboard
from bank1_portal.pages import FetchError, element_text, required, visible_text
from bank1_portal.parse import Newest, newest_statement, page_shows

log = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 45_000
STEP_TIMEOUT_MS = 10_000
DOWNLOAD_TIMEOUT_MS = 60_000
POLL_MS = 500

# One per statement. The first candidate with a visible match is used for every row
STATEMENT_ROW = [
    "[data-testid*='statement' i] [role='row']:has(button, a)",
    "mds-list-item:has(button, a)",
    "[role='row']:has(button, a)",
    "tbody tr:has(button, a)",
    "li:has(a[href*='pdf' i], button:has-text('Download'))",
]
# Inside a row: what starts the download. The row's only link or button comes last
ROW_DOWNLOAD = [
    "[aria-label*='download' i]",
    "button:has-text('Download')",
    "a:has-text('Download')",
    "a[href*='pdf' i]",
    "a:has-text('PDF')",
    "a",
    "button",
]
STANDARD_PDF = [
    "[role='radio']:has-text('Standard')",
    "mds-radio:has-text('Standard')",
    "input[type='radio'][value*='standard' i]",
    "label:has-text('Standard') input",
    "label:has-text('Standard')",
]
SINGLE_STATEMENT = [
    "[role='radio']:has-text('Single')",
    "[role='tab']:has-text('Single')",
    "mds-radio:has-text('Single')",
    "input[type='radio'][value*='single' i]",
    "label:has-text('Single') input",
    "label:has-text('Single')",
]
# The last click, once a choice has been made
CONFIRM_DOWNLOAD = [
    "[role='dialog'] mds-button:has-text('Download') button",
    "[role='dialog'] button:has-text('Download')",
    "mds-button#download button",
    "button:text-is('Download')",
]

# Whether a radio, tab, or toggle is now the chosen one, wherever its state is kept
_CHOSEN_JS = r"""
(el) => {
  const yes = (e) => e && (e.checked === true || e.getAttribute('aria-checked') === 'true'
    || e.getAttribute('aria-selected') === 'true' || e.getAttribute('aria-pressed') === 'true'
    || (e.hasAttribute('checked') && e.tagName !== 'INPUT'));
  if (yes(el)) return true;
  const roots = [el, el.shadowRoot, el.closest('label')].filter(Boolean);
  return roots.some((r) => [...r.querySelectorAll('input, [role=radio], [role=tab]')].some(yes));
}
"""


@dataclass(frozen=True)
class Choice:
    what: str
    candidates: list[str]


@dataclass(frozen=True)
class KindSteps:
    before_row: Choice | None = None  # chosen on the page, before picking the statement
    after_row: Choice | None = None  # chosen after the statement's link, then Download


KIND_STEPS = {
    Kind.CHECKING: KindSteps(after_row=Choice("standard PDF choice", STANDARD_PDF)),
    Kind.CARD: KindSteps(),
    Kind.INVESTMENT: KindSteps(before_row=Choice("single statement choice", SINGLE_STATEMENT)),
}


class LinkError(FetchError):
    """The account's address no longer leads to its statements page."""


def _ignore(_: str) -> None:
    pass


def _visible_rows(page: Page) -> list[Locator]:
    found, selector = find_first([page], STATEMENT_ROW, 0)
    if found is None:
        return []
    matches = page.locator(selector)
    return [matches.nth(i) for i in range(min(matches.count(), 60)) if matches.nth(i).is_visible()]


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


def check_statements_page(page: Page, account: Account) -> list[Locator]:
    """Wait for the statements list, check it's this account's, and return its rows."""
    deadline = time.monotonic() + PAGE_TIMEOUT_MS / 1000
    while not (rows := _visible_rows(page)):
        wrong = _landed_wrong(page, account)
        if wrong is None and time.monotonic() > deadline:
            wrong = f"no statements list after {PAGE_TIMEOUT_MS // 1000}s"
        if wrong is not None:
            raise LinkError(f"statements link no longer valid: {wrong}")
        page.wait_for_timeout(POLL_MS)
    if not page_shows(visible_text(page), account.match):
        raise LinkError("statements link no longer valid: the page doesn't show this account")
    log.info("statement: on %s's statements page, %d row(s)", account.label, len(rows))
    return rows


def find_newest(rows: list[Locator]) -> Newest:
    newest = newest_statement([element_text(row) for row in rows])
    if newest.same_month:
        log.warning("statement: %d other statement(s) also closed in %s", newest.same_month,
                    f"{newest.closing:%Y-%m}")
    return newest


def _chosen(loc: Locator) -> bool:
    try:
        return bool(loc.evaluate(_CHOSEN_JS))
    except PwError:
        return False


def choose(page: Page, choice: Choice) -> None:
    try:
        option = required([page], choice.candidates, choice.what, STEP_TIMEOUT_MS)
    except FetchError:
        raise FetchError(f"expected a {choice.what}, but the page has none") from None
    if not _chosen(option) and click_until(option, lambda: _chosen(option), choice.what) is None:
        raise FetchError(f"the {choice.what} couldn't be selected")


def _row_download(row: Locator) -> Locator:
    found, selector = find_first([row], ROW_DOWNLOAD, STEP_TIMEOUT_MS)
    if found is None:
        raise FetchError("the newest statement has no download link")
    log.info("statement: found the download link (%s)", selector)
    return found


def fetch_statement(page: Page, account: Account, dest: Path,
                    observer: Callable[[str], None] = _ignore) -> Newest:
    """Save the account's newest statement to `dest`, and return which one it was."""
    steps = KIND_STEPS[account.kind]
    go_to_statements(page, account)
    rows = check_statements_page(page, account)
    observer("statements")
    if steps.before_row:
        choose(page, steps.before_row)
        rows = _visible_rows(page) or rows
    newest = find_newest(rows)
    link = _row_download(rows[newest.index])
    log.info("statement: downloading the one closing %s", newest.closing)
    if steps.after_row is None:
        capture_pdf(page, link.click, dest, DOWNLOAD_TIMEOUT_MS)
        return newest
    choice = steps.after_row

    def choice_showing() -> bool:
        return find_first([page], choice.candidates, STEP_TIMEOUT_MS)[0] is not None

    if click_until(link, choice_showing, "statement link") is None:
        raise FetchError(f"expected a {choice.what}, but the page has none")
    observer("choice")
    choose(page, choice)
    confirm = required([page], CONFIRM_DOWNLOAD, "Download button", STEP_TIMEOUT_MS)
    capture_pdf(page, confirm.click, dest, DOWNLOAD_TIMEOUT_MS)
    return newest
