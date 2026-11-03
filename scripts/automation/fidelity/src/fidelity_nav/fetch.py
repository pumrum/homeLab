"""Browser step: open a fund page and return its HTML once the NAV is shown.

One page load per fund, one fund at a time. Parsing happens in parse.py.
"""

import logging
import re
from pathlib import Path

from playwright.sync_api import Error as PwError
from playwright.sync_api import Page, expect

from automation_common.browser import find_first, save_page_evidence
from fidelity_nav.parse import HEADER_ASOF_SELECTORS, HEADER_VALUE_SELECTORS, NAV_LABELS

log = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 45_000

# The header value, else the Details row value, for either label
WAIT_SELECTORS = HEADER_VALUE_SELECTORS + [
    f"mfl-card-data-row:has(.card-row-title:text-is('{label}')) .data-text" for label in NAV_LABELS
]


class FetchError(Exception):
    pass


def open_fund_page(page: Page, url: str) -> str | None:
    """Load `url` and wait for the NAV to show. Returns the matched selector or None."""
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    loc, sel = find_first([page], WAIT_SELECTORS, PAGE_TIMEOUT_MS)
    if loc is None:
        return None
    # The element can appear before its number is filled in
    expect(loc).to_contain_text(re.compile(r"\d"), timeout=15_000)
    # The as-of label renders with the value; the parser decides if it's missing
    find_first([page], HEADER_ASOF_SELECTORS, 5_000)
    return sel


def fetch_page_html(page: Page, url: str) -> str:
    """Open the fund page and return its rendered HTML."""
    sel = open_fund_page(page, url)
    if sel is None:
        raise FetchError(
            f"NAV element did not appear within {PAGE_TIMEOUT_MS // 1000}s "
            f"(tried {len(WAIT_SELECTORS)} selectors)"
        )
    log.info("NAV element found via %s", sel)
    return page.content()


def dump_fund_page(page: Page, url: str, run_dir: Path, prefix: str) -> str:
    """For fixing selectors: load the page and save outline, screenshot, and HTML.

    Saves even when the NAV element never appears. Returns the HTML.
    """
    try:
        sel = open_fund_page(page, url)
        log.info("dump: NAV element %s", f"found via {sel}" if sel else "NOT found")
    except (PwError, AssertionError) as e:
        log.warning("dump: page did not settle: %s", str(e).splitlines()[0])
    save_page_evidence(page, run_dir, prefix)
    html = page.content()
    (run_dir / f"{prefix}.html").write_text(html, encoding="utf-8")
    return html
