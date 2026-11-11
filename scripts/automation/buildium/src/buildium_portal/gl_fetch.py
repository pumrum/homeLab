"""Browser step: from a signed-in page, export the general ledger as CSV for one method.

The report's address comes from the sign-in page's host. The date range runs from a fixed
early date to today, because both outputs are full snapshots of the ledger. The export arrives
as a download, which is saved straight into the run folder.
"""

import datetime as dt
import logging
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PwTimeout

from automation_common.browser import click_until, type_date
from buildium_portal.config import Site
from buildium_portal.page_steps import FetchError, open_menu, required

log = logging.getLogger(__name__)

METHODS = ("Cash", "Accrual")
REPORT_PATH = "/manager/app/reports/Financials/GeneralLedger"
FIRST_DATE = dt.date(1999, 1, 1)

PAGE_TIMEOUT_MS = 45_000
MENU_TIMEOUT_MS = 3_000
DOWNLOAD_TIMEOUT_MS = 300_000  # a full history is slow to build, and it grows every year

DATE_RANGE_MENU = ["div#daterange-dropdown", "#daterange-dropdown"]
CUSTOM_DATE_ITEM = ["[id^='daterange-dropdown-menu-item']:has-text('Custom')",
                    "#daterange-dropdown-menu-item-10 span"]
FROM_DATE = ["input[name='dateInput_fromDate']"]
TO_DATE = ["input[name='dateInput_toDate']"]
EXPORT_BUTTON = ["a#exportReportButton", "#exportReportButton"]
CSV_ITEM = [":text-is('Comma-separated (.csv)')"]


def report_url(login_url: str) -> str:
    parts = urlsplit(login_url)
    return urlunsplit((parts.scheme, parts.netloc, REPORT_PATH, "", ""))


def date_forms(day: dt.date) -> tuple[str, str]:
    """The two ways the date box might show a day: 1/5/2026 or 01/05/2026."""
    return f"{day.month}/{day.day}/{day.year}", f"{day.month:02d}/{day.day:02d}/{day.year}"


def _set_date(box: Locator, day: dt.date, what: str) -> None:
    for form in date_forms(day):
        try:
            type_date(box, form)
            return
        except RuntimeError:
            continue  # the box shows the date another way, so try that one
    raise FetchError(f"the {what} box didn't take the date")


def _ignore(_: str) -> None:
    pass


def open_report(page: Page, site: Site, today: dt.date,
                observer: Callable[[str], None] = _ignore) -> None:
    """Open the report and set its date range, which stays set for each method's export."""
    page.goto(report_url(site.login_url), wait_until="domcontentloaded")
    menu = required(page, DATE_RANGE_MENU, "date range menu", PAGE_TIMEOUT_MS)
    observer("report")
    open_menu(page, menu, CUSTOM_DATE_ITEM, "date range menu", MENU_TIMEOUT_MS)
    required(page, CUSTOM_DATE_ITEM, "custom date item", MENU_TIMEOUT_MS).click()
    _set_date(required(page, FROM_DATE, "from date", PAGE_TIMEOUT_MS), FIRST_DATE, "from date")
    _set_date(required(page, TO_DATE, "to date", PAGE_TIMEOUT_MS), today, "to date")
    observer("dates")


def _method_label(method: str) -> list[str]:
    return [f"form[name='ReportFiltersForm'] label:text-is('{method}')",
            f"#ReportFiltersForm label:text-is('{method}')",
            f"label:text-is('{method}')"]


def _choose_method(page: Page, method: str) -> None:
    label = required(page, _method_label(method), f"{method} method", PAGE_TIMEOUT_MS)

    def chosen() -> bool:
        boxes = label.locator("input")
        return boxes.count() == 0 or boxes.first.is_checked()
    if click_until(label, chosen, f"{method} method") is None:
        raise FetchError(f"the {method} method wasn't chosen")


def export_method(page: Page, method: str, dest: Path,
                  observer: Callable[[str], None] = _ignore) -> None:
    """Choose a method and save its CSV to `dest`. The site's own file name is ignored."""
    _choose_method(page, method)
    button = required(page, EXPORT_BUTTON, "Export button", PAGE_TIMEOUT_MS)
    open_menu(page, button, CSV_ITEM, "Export menu", MENU_TIMEOUT_MS)
    observer(f"export_menu_{method.lower()}")
    item = required(page, CSV_ITEM, "CSV item", MENU_TIMEOUT_MS)
    log.info("general ledger: exporting %s", method)
    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as started:
            item.click()
    except PwTimeout:
        raise FetchError(
            f"no {method} export within {DOWNLOAD_TIMEOUT_MS // 1000}s") from None
    download = started.value
    if download.failure() is not None:
        raise FetchError(f"the {method} download failed")
    dest.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(dest)
