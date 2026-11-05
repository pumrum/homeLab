"""A signed-in browser page, ready for any command in this package.

    with signed_in_page(run, cfg.site, data_dir) as page:
        ...                      # do the work; the trace is saved if anything failed

All commands share one browser profile and one credential, so a remembered device
carries over. The agent runs one job at a time, so they never fight over the profile.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from automation_common.browser import (
    first_page,
    launch_edge,
    save_page_evidence,
    start_trace,
    stop_trace,
)
from automation_common.run import Run
from automation_common.secrets import Credential, SecretMasker, get_credential
from innago_portal import PROCESS
from innago_portal.config import Site
from innago_portal.login import LoginError, Observer, sign_in

LOGIN_SECRET = f"{PROCESS}/login"

GetCredential = Callable[[str, SecretMasker], Credential | None]

log = logging.getLogger(__name__)


def profile_dir(data_dir: Path) -> Path:
    return data_dir / "profiles" / "edge"


@contextmanager
def signed_in_page(run: Run, site: Site, data_dir: Path,
                   get_cred: GetCredential = get_credential,
                   make_observer: Callable[[Page], Observer] | None = None) -> Iterator[Page]:
    """Yield a signed-in page, or raise. A failed sign-in saves evidence in the run folder."""
    credential = get_cred(LOGIN_SECRET, run.masker)
    if credential is None:
        raise LoginError(f"no {LOGIN_SECRET} in Credential Manager")
    run.masker.add(credential.username)
    with sync_playwright() as pw:
        ctx = launch_edge(pw, profile_dir(data_dir))
        try:
            page = first_page(ctx)
            try:
                sign_in(page, site, credential,
                        observer=make_observer(page) if make_observer else None)
            except Exception:
                save_page_evidence(page, run.run_dir, run.next_failure_prefix())
                raise
            # Only now: a trace must never see the sign-in
            start_trace(ctx)
            try:
                yield page
            finally:
                failed = run.summary.counts()["failed"] > 0
                stop_trace(ctx, run.run_dir / "trace.zip" if failed else None)
        finally:
            ctx.close()
