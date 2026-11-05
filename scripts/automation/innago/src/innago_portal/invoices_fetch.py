"""Browser step: read the invoice list, then get each invoice from its own page.

The list opens grouped by property, with its invoice rows hidden. So it's switched to
"Not Grouped", and filtered to this month if it doesn't show one invoice per unit. Each
row's unit and invoice number are read from there. Every invoice has a fixed address built
from its number, and its Download button opens the PDF in a new tab, which capture_pdf
saves. The selectors come from runs against the real site.

Download sits right beside Remind, Record Payment, Edit Invoice, and Delete, and hidden
copies of all of them are in a dropdown. So the button must be visible and its text must
be exactly "Download", and nothing here ever matches the others.
"""

import datetime as dt
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, expect
from playwright.sync_api import TimeoutError as PwTimeout

from automation_common.browser import capture_pdf, find_first, save_page_evidence

log = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 45_000
DOWNLOAD_TIMEOUT_MS = 60_000
SETTLE_TIMEOUT_MS = 15_000

ROWS = "tr.income-table-row"
ROW_INVOICE_ID = "td.dv-in-id"
ROW_UNIT = "td.dv-in-unit"
COUNTER = ".invoice-counter"  # like "7 of 7"
GROUPING = ".filter-group .simple-fake-select"
NOT_GROUPED_OPTION = ".simple-fake-select-opt a:text-is('Not Grouped')"
DETAIL_HEADING = ".detail-sticky-header h3"
DOWNLOAD_BUTTON = ["button.btn-innago-primary:text-is('Download')", "button:text-is('Download')"]

FILTER_BUTTON = "#filter-icon-tenant-list-mobile"
CLEAR_FILTERS = ["button:has-text('Clear Filters')", "button.btn-link:has(em.innago-icon-close)"]
APPLY_FILTERS = "button:text-is('Apply Filters')"
# Only the due date counts: an invoice sent or paid in another month belongs to its due month
RANGE_CHECKBOXES = {"senton": False, "dueon": True, "paidon": False, "disbursedon": False}

_COUNTS = re.compile(r"(\d+)\s+of\s+(\d+)")
_INVOICE_ID = re.compile(r"^\w+$")  # it goes in an address


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class ListedInvoice:
    unit: str
    invoice_id: str


def detail_url(list_url: str, invoice_id: str) -> str:
    return f"{list_url.rstrip('/')}/{invoice_id}/detail/{invoice_id}/false/false"


def _labeled_select(label: str) -> str:
    return f"div.mb-3:has(> label:text-is('{label}')) select"


class InvoiceList:
    """The site's invoices for the period, on an already signed-in page."""

    def __init__(self, page: Page, url: str, expected: int) -> None:
        self._page = page
        self._url = url
        self._expected = expected
        self._rows = page.locator(ROWS).filter(visible=True)
        self._invoices: list[ListedInvoice] = []

    def _show_not_grouped(self) -> None:
        grouping, _ = find_first([self._page], [GROUPING], PAGE_TIMEOUT_MS)
        if grouping is None:
            raise FetchError(f"no grouping dropdown within {PAGE_TIMEOUT_MS // 1000}s")
        if "Not Grouped" in grouping.inner_text():
            return
        log.info("invoice list: switching to Not Grouped")
        grouping.click()
        self._page.locator(NOT_GROUPED_OPTION).click()
        expect(grouping).to_contain_text("Not Grouped", timeout=SETTLE_TIMEOUT_MS)

    def _wait_for_some_rows(self) -> None:
        """The table reloads after each change, with no rows until it's back."""
        try:
            expect(self._rows).not_to_have_count(0, timeout=SETTLE_TIMEOUT_MS)
        except AssertionError:
            log.warning("invoice list: no rows after %ds", SETTLE_TIMEOUT_MS // 1000)

    def _settle(self) -> tuple[int, int] | None:
        """Wait for every row the page says it's showing. Returns its shown and total counts."""
        counter, _ = find_first([self._page], [COUNTER], SETTLE_TIMEOUT_MS)
        counts = _COUNTS.search(counter.inner_text()) if counter is not None else None
        if counts is None:
            log.warning("invoice list: no row counter found, so the list can't be checked")
            return None
        shown, total = int(counts[1]), int(counts[2])
        expect(self._rows).to_have_count(shown, timeout=SETTLE_TIMEOUT_MS)
        return shown, total

    def _filter_to_this_month(self, today: dt.date) -> None:
        """The panel's Clear Filters, then this month by due date only, then Apply Filters."""
        page = self._page
        log.warning("invoice list: %d row(s), expected %d; filtering to this month",
                    self._rows.count(), self._expected)
        page.locator(FILTER_BUTTON).click()
        clear, _ = find_first([page], CLEAR_FILTERS, SETTLE_TIMEOUT_MS)
        if clear is None:
            raise FetchError("the filter panel has no Clear Filters button")
        clear.click()
        try:
            page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
        except PwTimeout:
            log.info("invoice list: the page stayed busy after clearing filters; carrying on")
        apply_button = page.locator(APPLY_FILTERS)
        if not apply_button.is_visible():  # clearing can close the panel
            page.locator(FILTER_BUTTON).click()
        page.locator(_labeled_select("Date Range")).select_option(label="Month")
        page.locator(_labeled_select("Select Month")).select_option(label=f"{today:%B}")
        page.locator(_labeled_select("Select Year")).select_option(label=str(today.year))
        for box_id, wanted in RANGE_CHECKBOXES.items():
            if page.locator(f"input#{box_id}").is_checked() != wanted:
                page.locator(f".styled-checkbox:has(input#{box_id}) label").click()
        apply_button.click()
        try:
            expect(self._rows).to_have_count(self._expected, timeout=SETTLE_TIMEOUT_MS)
        except AssertionError:
            log.warning("invoice list: still %d row(s) after filtering", self._rows.count())

    def _read_rows(self) -> list[ListedInvoice]:
        invoices = []
        for index in range(self._rows.count()):
            row = self._rows.nth(index)
            invoice_id = row.locator(ROW_INVOICE_ID).inner_text().strip()
            if not _INVOICE_ID.match(invoice_id):
                raise FetchError(f"row {index + 1} has no usable invoice number")
            invoices.append(ListedInvoice(row.locator(ROW_UNIT).inner_text().strip().upper(),
                                          invoice_id))
        return sorted(invoices, key=lambda invoice: invoice.unit)

    def open(self, today: dt.date | None = None) -> int:
        """Load the list, read every invoice showing, and return how many there are."""
        self._page.goto(self._url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        self._show_not_grouped()
        self._wait_for_some_rows()
        counts = self._settle()
        if self._rows.count() != self._expected:
            self._filter_to_this_month(today or dt.date.today())
            self._wait_for_some_rows()
            counts = self._settle()
        if counts is not None and counts[0] < counts[1]:
            raise FetchError(f"the list shows only {counts[0]} of {counts[1]} invoices")
        self._invoices = self._read_rows()
        log.info("invoice list: %d invoice(s)", len(self._invoices))
        return len(self._invoices)

    def label(self, index: int) -> str:
        """The unit the list gives for this row, for reporting and to check against its PDF."""
        return self._invoices[index].unit or f"row {index + 1}"

    def download(self, index: int, dest: Path) -> None:
        """Save one invoice's PDF to `dest`. The site's own file name is random, so it's ignored."""
        invoice_id = self._invoices[index].invoice_id
        self._page.goto(detail_url(self._url, invoice_id), wait_until="domcontentloaded",
                        timeout=PAGE_TIMEOUT_MS)
        heading = self._page.locator(DETAIL_HEADING).filter(visible=True).first
        expect(heading).to_contain_text(invoice_id, timeout=PAGE_TIMEOUT_MS)
        # A mouse left over a status badge opens a pop-up that can cover the button
        self._page.mouse.move(0, 0)
        button, selector = find_first([self._page], DOWNLOAD_BUTTON, PAGE_TIMEOUT_MS)
        if button is None:
            raise FetchError(f"invoice {invoice_id} has no visible Download button")
        log.info("%s: downloading invoice %s via %s", self.label(index), invoice_id, selector)
        capture_pdf(self._page, button.click, dest, DOWNLOAD_TIMEOUT_MS)

    def save_evidence(self, run_dir: Path, prefix: str) -> None:
        """After a failure: save the page's outline and a screenshot. Never raises."""
        save_page_evidence(self._page, run_dir, prefix)

    def save_structure(self, run_dir: Path, prefix: str) -> None:
        """For fixing selectors: save the page's outline, screenshot, and HTML."""
        self.save_evidence(run_dir, prefix)
        (run_dir / f"{prefix}.html").write_text(self._page.content(), encoding="utf-8")
