"""Browser step: open the invoice list and download one invoice at a time.

The selectors are candidates until they're checked against a --capture or --dump of
the real page. Only controls labeled as downloads are ever clicked, so this can't send
or pay anything.
"""

import logging
from pathlib import Path

from playwright.sync_api import Locator, Page

from automation_common.browser import find_first, save_page_evidence

log = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 45_000
DOWNLOAD_TIMEOUT_MS = 60_000

ROWS = [
    "table tbody tr",
    "[role='row']:has([role='cell'])",
    "mat-row",
    "[class*='invoice' i][class*='row' i]",
]
DOWNLOAD_BUTTON = [
    "button:has-text('Download')",
    "a:has-text('Download')",
    "[aria-label*='Download' i]",
    "[title*='Download' i]",
]


class FetchError(Exception):
    pass


class InvoiceList:
    """The rows of the site's default invoice view, on an already signed-in page."""

    def __init__(self, page: Page, url: str) -> None:
        self._page = page
        self._url = url
        self._rows: list[Locator] = []

    def open(self) -> int:
        """Load the page once and return how many invoices can be downloaded."""
        self._page.goto(self._url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        # Any download control counts, so a mix of buttons and links can't drop a row
        any_download = ", ".join(DOWNLOAD_BUTTON)
        wanted = [f"{row}:has({any_download})" for row in ROWS]
        first, selector = find_first([self._page], wanted, PAGE_TIMEOUT_MS)
        if first is None:
            raise FetchError(
                f"no invoice row with a download button within {PAGE_TIMEOUT_MS // 1000}s"
            )
        matches = self._page.locator(selector)
        self._rows = [matches.nth(i) for i in range(matches.count()) if matches.nth(i).is_visible()]
        log.info("invoice list: %d row(s) via %s", len(self._rows), selector)
        return len(self._rows)

    def download(self, index: int, dest: Path) -> None:
        """Download one row's PDF to `dest`. The site's own file name is random, so it's ignored."""
        button, selector = find_first([self._rows[index]], DOWNLOAD_BUTTON, PAGE_TIMEOUT_MS)
        if button is None:
            raise FetchError(f"row {index + 1} has no download button")
        log.info("downloading row %d via %s", index + 1, selector)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download:
            button.click()
        download.value.save_as(dest)

    def save_evidence(self, run_dir: Path, prefix: str) -> None:
        """After a failure: save the page's outline and a screenshot. Never raises."""
        save_page_evidence(self._page, run_dir, prefix)

    def save_structure(self, run_dir: Path, prefix: str) -> None:
        """For fixing selectors: save the page's outline, screenshot, and HTML."""
        self.save_evidence(run_dir, prefix)
        (run_dir / f"{prefix}.html").write_text(self._page.content(), encoding="utf-8")
