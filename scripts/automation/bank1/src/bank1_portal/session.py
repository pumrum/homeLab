"""A browser, and a signed-in page, ready for any command in this package.

    with open_browser(data_dir) as ctx:
        with signed_in_page(run, ctx, cfg.site) as page:
            ...              # the trace is saved if anything failed in here

One login reaches every account, so a run signs in once. The profile keeps the site's memory
of the device, so after one approved verification, later runs usually aren't asked. Never
copy a profile between machines: each one verifies once.

Leaving the `with` signs out. A failed sign-out is only a warning, since everything is done
by then and the site ends an idle session on its own.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from automation_common.browser import (
    first_page,
    launch_edge,
    save_page_evidence,
    start_trace,
    stop_trace,
)
from automation_common.run import Run
from automation_common.secrets import Credential, SecretMasker, get_credential
from bank1_portal import PROCESS
from bank1_portal.config import Site
from bank1_portal.login import LoginError, Observer, SignOutError, sign_in, sign_out

GetCredential = Callable[[str, SecretMasker], Credential | None]
CREDENTIAL = f"{PROCESS}/login"

log = logging.getLogger(__name__)


def profile_dir(data_dir: Path) -> Path:
    return data_dir / "profiles" / "edge"


@contextmanager
def open_browser(data_dir: Path) -> Iterator[BrowserContext]:
    with sync_playwright() as pw:
        ctx = launch_edge(pw, profile_dir(data_dir))
        try:
            yield ctx
        finally:
            ctx.close()


def _ignore(_: str) -> None:
    pass


@contextmanager
def signed_in_page(run: Run, ctx: BrowserContext, site: Site, observer: Observer = _ignore,
                   get_cred: GetCredential = get_credential) -> Iterator[Page]:
    """Yield a signed-in page, or raise. A failed sign-in saves what's on screen."""
    credential = get_cred(CREDENTIAL, run.masker)
    if credential is None:
        raise LoginError(f"no {CREDENTIAL} in Credential Manager")
    run.masker.add(credential.username)

    def on_verify() -> None:
        run.notify("mfa_needed")  # never raises

    page = first_page(ctx)
    try:
        sign_in(page, site, credential, on_verify=on_verify, observer=observer)
    except Exception:
        save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        raise
    # Only now: a trace must never see the sign-in
    failed_before = run.summary.counts()["failed"]
    start_trace(ctx)
    try:
        yield page
    finally:
        failed = run.summary.counts()["failed"] > failed_before
        stop_trace(ctx, run.run_dir / "trace.zip" if failed else None)
        try:
            sign_out(page)
        except Exception as e:  # noqa: BLE001 - only a warning, by design
            reason = str(e) if isinstance(e, SignOutError) else type(e).__name__
            log.warning("sign-out failed, which doesn't fail the run: %s", reason)
