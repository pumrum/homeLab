"""A browser, and a page signed in as one entity, ready for any command in this package.

    with open_browser(data_dir) as ctx:
        for entity in cfg.entities:
            with signed_in_page(run, ctx, cfg.site, entity) as page:
                ...              # the trace is saved if this entity failed

All entities share one browser profile, so the site's memory of the device carries over.
Leaving the `with` signs out. If that fails, it raises SignOutError and the caller must
stop, because it can't tell who is still signed in.
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
from buildium_portal import PROCESS
from buildium_portal.config import Entity, Site
from buildium_portal.login import LoginError, Observer, SignOutError, sign_in, sign_out

GetCredential = Callable[[str, SecretMasker], Credential | None]

log = logging.getLogger(__name__)


def profile_dir(data_dir: Path) -> Path:
    return data_dir / "profiles" / "edge"


def credential_name(entity: Entity) -> str:
    return f"{PROCESS}/{entity.key}"


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
def signed_in_page(run: Run, ctx: BrowserContext, site: Site, entity: Entity,
                   observer: Observer = _ignore,
                   get_cred: GetCredential = get_credential,
                   interactive: bool = False) -> Iterator[Page]:
    """Yield a page signed in as `entity`, or raise. Any failure saves what's on screen.

    With `interactive`, a bot check waits for a person at the screen to sign in by hand.
    """
    name = credential_name(entity)
    credential = get_cred(name, run.masker)
    if credential is None:
        raise LoginError(f"no {name} in Credential Manager")
    run.masker.add(credential.username)

    def on_mfa() -> None:
        # Only what's needed to act on it. Sending a notification never raises
        run.notify("mfa_needed", key=entity.key, abbreviation=entity.abbreviation)

    def person_needed() -> None:
        # The key is neutral, so it can be shown. The person must sign in as this entity
        log.warning("sign-in: sign in by hand as %s in the job's browser; the run continues "
                    "when the app loads", entity.key)

    page = first_page(ctx)
    try:
        sign_in(page, site, credential, on_mfa=on_mfa, observer=observer,
                wait_for_person=person_needed if interactive else None)
    except Exception:
        save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        raise
    # Only now, and stopped before the next sign-in: a trace must never see one
    failed_before = run.summary.counts()["failed"]
    start_trace(ctx)
    try:
        yield page
    finally:
        failed = run.summary.counts()["failed"] > failed_before
        stop_trace(ctx, run.run_dir / f"trace_{entity.key}.zip" if failed else None)
        try:
            sign_out(page, site, observer=observer)
        except SignOutError:
            save_page_evidence(page, run.run_dir, run.next_failure_prefix())
            raise
