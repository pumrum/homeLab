"""Browser step: from a signed-in page, get the statement for the last period.

Rental owner, then Rental owner ledger, then Statement, then one exact menu item. Send funds
sits on the same page, and the Statement menu has other periods right beside this one, so
every label here is matched exactly and nothing falls back to something similar.

These steps have run on the real site. If the site changes, update the selectors from a
--dump run.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Page

from automation_common.browser import capture_pdf
from buildium_portal.page_steps import open_menu, required

log = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 45_000
MENU_TIMEOUT_MS = 3_000
DOWNLOAD_TIMEOUT_MS = 60_000

RENTAL_OWNER_MENU = ["nav :text-is('Rental owner')", ":text-is('Rental owner')"]
LEDGER_ITEM = [":text-is('Rental owner ledger')"]
STATEMENT_MENU = ["button:text-is('Statement')", "a:text-is('Statement')",
                  ":text-is('Statement')"]
LAST_PERIOD_ITEM = [":text-is('Last period ending the last day')"]


def _ignore(_: str) -> None:
    pass


def fetch_statement(page: Page, dest: Path,
                    observer: Callable[[str], None] = _ignore) -> None:
    """Save the last period's statement to `dest`. The site's own file name is ignored."""
    owner_menu = required(page, RENTAL_OWNER_MENU, "Rental owner menu", PAGE_TIMEOUT_MS)
    open_menu(page, owner_menu, LEDGER_ITEM, "Rental owner menu", MENU_TIMEOUT_MS)
    required(page, LEDGER_ITEM, "Rental owner ledger item", MENU_TIMEOUT_MS).click()
    statement_menu = required(page, STATEMENT_MENU, "Statement menu", PAGE_TIMEOUT_MS)
    observer("ledger")
    open_menu(page, statement_menu, LAST_PERIOD_ITEM, "Statement menu", MENU_TIMEOUT_MS)
    observer("statement_menu")
    item = required(page, LAST_PERIOD_ITEM, "last period item", MENU_TIMEOUT_MS)
    log.info("statement: downloading")
    capture_pdf(page, item.click, dest, DOWNLOAD_TIMEOUT_MS)
