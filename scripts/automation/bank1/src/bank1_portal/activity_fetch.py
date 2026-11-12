"""Browser step: from a signed-in page, download one account's activity as a CSV.

Each account's activity download page is reached by its own direct address from the private
config. The run first makes sure it landed on that page, by its "Direct Download" form, and
that the form shows this account, and fails with "activity link no longer valid" otherwise. It
doesn't fall back to the site's menus. Only the form is checked for the account: the rest of
the page can list every account.

Then, on the form:

    Activity      open the drop-down and choose "Choose a date range", by its words. Its place
                  in the list differs between kinds of account
    From, To      the account's start date and today, typed key by key, as MM/DD/YYYY: the boxes
                  show a pasted date but don't register it, and Download then does nothing
    Download      the file type is left as the site sets it; the CSV's header is checked after

The controls are web components, so the selectors reach into shadow roots, and pages hold
hidden copies of controls, so only visible matches count. If the site changes, update the
selectors here from a --dump run.
"""

import logging
import time
from pathlib import Path

from playwright.sync_api import Locator, Page

from automation_common.browser import click_until, find_first, type_date
from bank1_portal.config import Account
from bank1_portal.login import PASSWORD_FIELD, USERNAME_FIELD, on_dashboard
from bank1_portal.pages import FetchError, element_text, required
from bank1_portal.parse import page_shows

log = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 45_000
STEP_TIMEOUT_MS = 10_000
DOWNLOAD_TIMEOUT_MS = 60_000
POLL_MS = 500

DATE_RANGE = "Choose a date range"

# The download form; its heading reads "Direct Download"
FORM = ["#downloadAccountActivity"]
FORM_HEADING = ["#downloadAccountActivity h2:has-text('Direct Download')",
                "h2:has-text('Direct Download')"]
# Where the form names the account it downloads; the whole form as a last resort
ACCOUNT_SHOWN = ["#downloadAccountActivity #account-selector",
                 "#downloadAccountActivity [id^='accountSelector']",
                 "#downloadAccountActivity mds-select#account",
                 "#downloadAccountActivity"]
ACTIVITY_SELECT = ["#downloadActivityOptionId button", "#downloadActivityOptionId"]
RANGE_OPTION = [f"#downloadActivityOptionId mds-select-option:has-text('{DATE_RANGE}')",
                f"#downloadActivityOptionId [role='option']:has-text('{DATE_RANGE}')",
                f"mds-select-option:has-text('{DATE_RANGE}')"]
FROM_DATE = ["#accountActivityFromDate-input-input", "#accountActivityFromDate input"]
TO_DATE = ["#accountActivityToDate-input-input", "#accountActivityToDate input"]
DOWNLOAD = ["#download button", "mds-button#download"]


class LinkError(FetchError):
    """The account's address no longer leads to its activity download page."""


def _landed_wrong(page: Page, account: Account) -> str | None:
    if find_first([page], USERNAME_FIELD + PASSWORD_FIELD, 0)[0] is not None:
        return "landed on a sign-in page"
    if account.activity and on_dashboard(page.url) and not on_dashboard(account.activity.url):
        return "landed on the dashboard"
    return None


def go_to_activity(page: Page, account: Account) -> None:
    """Load the account's activity download page: its one page load this run."""
    if account.activity is None:
        raise FetchError("the account has no activity_url")
    log.info("activity: opening %s's download page", account.label)
    page.goto(account.activity.url, wait_until="domcontentloaded")


def check_activity_page(page: Page, account: Account) -> None:
    """Wait for the download form, and check it names this account."""
    deadline = time.monotonic() + PAGE_TIMEOUT_MS / 1000
    while find_first([page], FORM_HEADING, 0)[0] is None:
        wrong = _landed_wrong(page, account)
        if wrong is None and time.monotonic() > deadline:
            wrong = f"no download form after {PAGE_TIMEOUT_MS // 1000}s"
        if wrong is not None:
            raise LinkError(f"activity link no longer valid: {wrong}")
        page.wait_for_timeout(POLL_MS)
    shown, selector = find_first([page], ACCOUNT_SHOWN, STEP_TIMEOUT_MS)
    if shown is None or not page_shows(element_text(shown), account.match):
        raise LinkError("activity link no longer valid: the form is for another account")
    log.info("activity: on %s's download page (%s)", account.label, selector)


def _visible(page: Page, candidates: list[str]) -> Locator | None:
    return find_first([page], candidates, 0)[0]


def choose_date_range(page: Page) -> None:
    """Open the Activity drop-down and choose the date range, by its words."""
    select = required([page], ACTIVITY_SELECT, "Activity drop-down", STEP_TIMEOUT_MS)
    option: Locator | None = None

    def options_showing() -> bool:
        nonlocal option
        option = find_first([page], RANGE_OPTION, STEP_TIMEOUT_MS)[0]
        return option is not None

    if click_until(select, options_showing, "Activity drop-down") is None or option is None:
        raise FetchError(f'the Activity drop-down has no "{DATE_RANGE}" choice')

    def boxes_showing() -> bool:
        deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000
        while _visible(page, FROM_DATE) is None:
            if time.monotonic() > deadline:
                return False
            page.wait_for_timeout(POLL_MS)
        return True

    how = click_until(option, boxes_showing, "date range choice")
    if how is None:
        raise FetchError(f'choosing "{DATE_RANGE}" showed no date boxes')
    log.info("activity: chose the date range (%s)", how)


def fill_dates(page: Page, start: str, end: str) -> None:
    """Type both dates key by key; type_date reads each back."""
    for candidates, value, what in ((FROM_DATE, start, "From date"), (TO_DATE, end, "To date")):
        box = required([page], candidates, f"{what} box", STEP_TIMEOUT_MS)
        try:
            type_date(box, value)
        except RuntimeError:
            raise FetchError(f"the {what} box didn't take the date") from None


def download(page: Page, dest: Path) -> None:
    """Click Download and save the file the site sends straight to `dest`."""
    button = required([page], DOWNLOAD, "Download button", STEP_TIMEOUT_MS)
    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as info:
            button.click()
    except Exception as e:
        if "Timeout" in type(e).__name__ or "timeout" in str(e).lower():
            raise FetchError(f"no download within {DOWNLOAD_TIMEOUT_MS // 1000}s of "
                             "clicking Download") from None
        raise
    dest.parent.mkdir(parents=True, exist_ok=True)
    info.value.save_as(dest)
    log.info("activity: downloaded %d bytes", dest.stat().st_size)
