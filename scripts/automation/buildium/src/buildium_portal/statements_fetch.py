"""Browser step: from a signed-in page, get the statement for the last period.

Rental owner, then Rental owner ledger, then Statement, then one exact menu item. Send funds
sits on the same page, and the Statement menu has other periods right beside this one, so
every label here is matched exactly and nothing falls back to something similar.

The labels come from screenshots of the real site. The structure around them hasn't been
seen, so the selectors are candidates to update from a --dump run.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Locator, Page

from automation_common.browser import capture_pdf, click_until, find_first

log = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 45_000
MENU_TIMEOUT_MS = 3_000
DOWNLOAD_TIMEOUT_MS = 60_000

RENTAL_OWNER_MENU = ["nav :text-is('Rental owner')", ":text-is('Rental owner')"]
LEDGER_ITEM = [":text-is('Rental owner ledger')"]
STATEMENT_MENU = ["button:text-is('Statement')", "a:text-is('Statement')",
                  ":text-is('Statement')"]
LAST_PERIOD_ITEM = [":text-is('Last period ending the last day')"]


class FetchError(Exception):
    pass


def _ignore(_: str) -> None:
    pass


def _required(page: Page, candidates: list[str], what: str, timeout_ms: int) -> Locator:
    found, selector = find_first([page], candidates, timeout_ms)
    if found is None:
        raise FetchError(f"no {what} within {timeout_ms // 1000}s")
    log.info("statement: found %s (%s)", what, selector)
    return found


def _open_menu(page: Page, menu: Locator, item: list[str], what: str) -> None:
    def item_showing() -> bool:
        return find_first([page], item, MENU_TIMEOUT_MS)[0] is not None
    if click_until(menu, item_showing, what) is None:
        raise FetchError(f"the {what} did not open")


def fetch_statement(page: Page, dest: Path,
                    observer: Callable[[str], None] = _ignore) -> None:
    """Save the last period's statement to `dest`. The site's own file name is ignored."""
    owner_menu = _required(page, RENTAL_OWNER_MENU, "Rental owner menu", PAGE_TIMEOUT_MS)
    _open_menu(page, owner_menu, LEDGER_ITEM, "Rental owner menu")
    _required(page, LEDGER_ITEM, "Rental owner ledger item", MENU_TIMEOUT_MS).click()
    statement_menu = _required(page, STATEMENT_MENU, "Statement menu", PAGE_TIMEOUT_MS)
    observer("ledger")
    _open_menu(page, statement_menu, LAST_PERIOD_ITEM, "Statement menu")
    observer("statement_menu")
    item = _required(page, LAST_PERIOD_ITEM, "last period item", MENU_TIMEOUT_MS)
    log.info("statement: downloading")
    capture_pdf(page, item.click, dest, DOWNLOAD_TIMEOUT_MS)
