"""Browser step: open the invoice list and get one invoice at a time.

Rows hold no download control. Clicking a row opens that invoice's detail page, and its
Download button opens the PDF in a new tab, which capture_pdf saves. The selectors come
from a --capture of the real site.

Download sits right beside Remind, Record Payment, Edit Invoice, and Delete, and hidden
copies of all of them are in a dropdown. So the button must be visible and its text must
be exactly "Download", and nothing here ever matches the others.
"""

import logging
import re
from pathlib import Path

from playwright.sync_api import Page, expect

from automation_common.browser import capture_pdf, find_first, save_page_evidence

log = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 45_000
DOWNLOAD_TIMEOUT_MS = 60_000

ROWS = ["tr.income-table-row"]
INVOICE_ID_CELL = "td.dv-in-id"
COUNTER = ".invoice-counter"  # like "7 of 7"
DOWNLOAD_BUTTON = ["button.btn-innago-primary:text-is('Download')", "button:text-is('Download')"]

_COUNTS = re.compile(r"(\d+)\s+of\s+(\d+)")


class FetchError(Exception):
    pass


class InvoiceList:
    """The rows of the site's default invoice view, on an already signed-in page."""

    def __init__(self, page: Page, url: str) -> None:
        self._page = page
        self._url = url
        self._rows = page.locator("__none__")
        self._on_list = False

    def _load_list(self) -> None:
        self._page.goto(self._url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        first, selector = find_first([self._page], ROWS, PAGE_TIMEOUT_MS)
        if first is None:
            raise FetchError(f"no invoice rows within {PAGE_TIMEOUT_MS // 1000}s")
        self._rows = self._page.locator(selector).filter(visible=True)
        self._on_list = True

    def _check_counter(self) -> None:
        """Wait for every row the page says it's showing, and refuse a partial list."""
        counter, _ = find_first([self._page], [COUNTER], 5_000)
        counts = _COUNTS.search(counter.inner_text()) if counter is not None else None
        if counts is None:
            log.warning("invoice list: no row counter found, so the list can't be checked")
            return
        shown, total = int(counts[1]), int(counts[2])
        if shown < total:
            raise FetchError(f"the list shows only {shown} of {total} invoices")
        expect(self._rows).to_have_count(shown, timeout=15_000)

    def open(self) -> int:
        """Load the list and return how many invoices it has."""
        self._load_list()
        self._check_counter()
        count = self._rows.count()
        log.info("invoice list: %d row(s)", count)
        return count

    def download(self, index: int, dest: Path) -> None:
        """Save one row's PDF to `dest`. The site's own file name is random, so it's ignored."""
        if not self._on_list:
            self._load_list()
        row = self._rows.nth(index)
        invoice_id = row.locator(INVOICE_ID_CELL).inner_text(timeout=PAGE_TIMEOUT_MS).strip()
        log.info("row %d: opening invoice %s", index + 1, invoice_id)
        self._on_list = False
        # The first cell, because hovering the status cell opens a pop-up with other actions
        row.locator("td").first.click()
        self._page.wait_for_url(f"**/{invoice_id}/detail/**", timeout=PAGE_TIMEOUT_MS)
        button, selector = find_first([self._page], DOWNLOAD_BUTTON, PAGE_TIMEOUT_MS)
        if button is None:
            raise FetchError(f"invoice {invoice_id} has no visible Download button")
        log.info("row %d: downloading via %s", index + 1, selector)
        capture_pdf(self._page, button.click, dest, DOWNLOAD_TIMEOUT_MS)

    def save_evidence(self, run_dir: Path, prefix: str) -> None:
        """After a failure: save the page's outline and a screenshot. Never raises."""
        save_page_evidence(self._page, run_dir, prefix)

    def save_structure(self, run_dir: Path, prefix: str) -> None:
        """For fixing selectors: save the page's outline, screenshot, and HTML."""
        self.save_evidence(run_dir, prefix)
        (run_dir / f"{prefix}.html").write_text(self._page.content(), encoding="utf-8")
