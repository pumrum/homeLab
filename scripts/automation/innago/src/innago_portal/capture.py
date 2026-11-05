"""--capture: a person signs in and clicks around while the page structure is saved.

For learning the real site and setting up the browser profile, and never run by a job.
It types nothing and starts no trace. It saves an outline and a screenshot each time
the page settles into something new, until the window closes or the time runs out.
"""

import hashlib
import logging
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Download, Page, sync_playwright
from playwright.sync_api import Error as PwError

from automation_common.browser import DOM_OUTLINE_JS, first_page, launch_edge, save_page_evidence
from automation_common.run import Run, Status
from innago_portal.config import Site

log = logging.getLogger(__name__)

CAPTURE_MINUTES = 20
POLL_MS = 1500
MAX_SNAPSHOTS = 150


def _fingerprint(page: Page) -> str:
    outline = page.evaluate(DOM_OUTLINE_JS)
    return hashlib.sha256(f"{page.url}\n{outline}".encode()).hexdigest()


def _on_download(download: Download) -> None:
    # Answers how the PDF arrives. The name and address aren't logged, and it isn't kept
    log.info("capture: the site started a file download (not kept)")


def _watch(ctx: BrowserContext, run_dir: Path, deadline: float) -> int:
    """Poll the open tabs. A page counts once it looks the same on two polls in a row."""
    last: dict[Page, str] = {}
    saved: dict[Page, str] = {}
    watched: set[Page] = set()
    count = 0
    while ctx.pages and time.monotonic() < deadline and count < MAX_SNAPSHOTS:
        for page in list(ctx.pages):
            if page not in watched:
                page.on("download", _on_download)
                watched.add(page)
            try:
                mark = _fingerprint(page)
                if mark == last.get(page) and mark != saved.get(page):
                    count += 1
                    save_page_evidence(page, run_dir, f"capture_{count:03d}")
                    saved[page] = mark
                    log.info("capture: saved snapshot %d", count)
                last[page] = mark
            except PwError:
                continue  # the page is navigating or was closed; look again next time
        try:
            ctx.pages[0].wait_for_timeout(POLL_MS)
        except (PwError, IndexError):
            continue
    return count


def capture(run: Run, site: Site, profile: Path) -> None:
    """Open the login page in the shared profile and watch. Records one result."""
    log.warning("capture: sign in by hand and open the invoices. Close the window when done. "
                "The time limit is %d minutes.", CAPTURE_MINUTES)
    with sync_playwright() as pw:
        ctx = launch_edge(pw, profile)
        try:
            first_page(ctx).goto(site.login_url, wait_until="domcontentloaded")
            count = _watch(ctx, run.run_dir, time.monotonic() + CAPTURE_MINUTES * 60)
        finally:
            try:
                ctx.close()
            except PwError:
                pass  # already closed by the person
    run.summary.record("capture", "pages", Status.OK, f"{count} snapshot(s) saved")
