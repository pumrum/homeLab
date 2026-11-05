"""Sign in to the site. Every command in this package goes through this flow.

A remembered browser profile can skip any of the steps, and MFA and the passkey prompt
come and go, so nothing assumes an order. Each pass works out where the page is and
handles that one step, until the page is signed in or the time runs out.

The email and password steps have run on the real site. The passkey prompt's address and
buttons come from a screenshot of the real page, and the job has only declined it on a
test site. The job has reached the real MFA page and waited, but no code has gone through.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PwTimeout

from automation_common.browser import find_first
from automation_common.secrets import Credential
from innago_portal.config import Site

log = logging.getLogger(__name__)

EMAIL_FIELD = ["input#username", "input[name='username']", "input[type='email']"]
PASSWORD_FIELD = ["input#password", "input[name='password']", "input[type='password']"]
# Only ways to decline. Never add one that creates a passkey, such as "Continue" alone.
# The first is the real site's, and it declines for good rather than asking again.
PASSKEY_SKIP = [
    ":is(button,a):text-matches('^not on this device$', 'i')",
    "input[type='submit'][value='Not on this device' i]",
    ":is(button,a):text-matches('^(remind me later|not now|maybe later|skip|skip for now)$', 'i')",
    ":is(button,a):text-matches('^continue without', 'i')",
]
BOT_CHECK = [
    "iframe[src*='captcha' i]",
    "iframe[title*='captcha' i]",
    "iframe[src*='challenges.cloudflare.com']",
    "[data-captcha-provider]",
    "[class*='captcha' i]",
]
_PASSKEY_TEXT = re.compile("passkey", re.IGNORECASE)


class LoginError(Exception):
    """Sign-in didn't finish. The message never holds the address's query or a secret."""


class Step(StrEnum):
    SIGNED_IN = "signed in"
    EMAIL = "email"
    PASSWORD = "password"
    PASSKEY = "passkey prompt"
    MFA = "MFA"
    OTHER = "other"


@dataclass(frozen=True)
class Timeouts:
    """Milliseconds, except where the name says otherwise."""

    total_s: float = 120  # the whole sign-in, not counting an MFA wait
    field: int = 15_000  # a field or button appearing
    after_submit: int = 30_000  # the page moving on after a submit
    mfa: int = 300_000  # someone entering a code
    settle: int = 3_000  # a passkey prompt appearing after sign-in


Observer = Callable[[Step], None]


def classify(url: str, app_url: str) -> Step:
    """Work out the sign-in step from an address. Pure, so it's easy to test."""
    parts = urlsplit(url)
    path = parts.path.lower()
    if parts.hostname == urlsplit(app_url).hostname and "login" not in path:
        return Step.SIGNED_IN
    if "/u/login/password" in path:
        return Step.PASSWORD
    if "/u/login" in path:
        return Step.EMAIL
    # Before MFA: the real offer to use a fingerprint or face is at /u/mfa-webauthn-...-enrollment
    if "/u/passkey" in path or ("webauthn" in path and "enrollment" in path):
        return Step.PASSKEY
    if "/u/mfa" in path or path.startswith("/mfa"):
        return Step.MFA
    return Step.OTHER


def _path(page: Page) -> str:
    return urlsplit(page.url).path


def _wait_for_new_address(page: Page, before: str, timeout_ms: float, problem: str) -> None:
    try:
        page.wait_for_url(lambda url: url != before, timeout=timeout_ms)
    except PwTimeout:
        raise LoginError(f"{problem} (still on {urlsplit(before).path})") from None


def _check_bot_wall(page: Page) -> None:
    """A person-check is never worked around: the run fails and says so."""
    found, selector = find_first([page], BOT_CHECK, 0)
    if found is not None:
        raise LoginError(f"the site is showing a CAPTCHA or bot check ({selector})")


def _submit(page: Page, fields: list[str], value: str, what: str, t: Timeouts) -> None:
    """Fill one field and press Enter, which can't hit the Google or Apple buttons."""
    field, selector = find_first([page], fields, t.field)
    if field is None:
        raise LoginError(f"the {what} field did not appear on {_path(page)}")
    log.info("sign-in: entering the %s (field %s)", what, selector)
    before = page.url
    field.fill(value)
    field.press("Enter")
    _wait_for_new_address(page, before, t.after_submit, f"the site did not accept the {what}")


def _passkey_prompt_showing(page: Page, t: Timeouts) -> bool:
    try:
        page.get_by_text(_PASSKEY_TEXT).first.wait_for(state="visible", timeout=t.settle)
        return True
    except PwTimeout:
        return False


def _skip_passkey(page: Page, t: Timeouts, required: bool) -> bool:
    """Decline a passkey prompt. Returns whether one was declined."""
    skip, selector = find_first([page], PASSKEY_SKIP, t.field if required else t.settle)
    if skip is None:
        if required:
            raise LoginError(f"no way to skip the passkey prompt on {_path(page)}")
        return False
    log.info("sign-in: skipping the passkey prompt (%s)", selector)
    skip.click()
    skip.wait_for(state="hidden", timeout=t.after_submit)
    return True


def _wait_for_mfa(page: Page, site: Site, t: Timeouts) -> None:
    minutes = t.mfa / 60_000
    log.warning("sign-in: the site wants an MFA code on %s; waiting up to %.0f minutes",
                _path(page), minutes)
    try:
        page.wait_for_url(lambda url: classify(url, site.app_url) is not Step.MFA, timeout=t.mfa)
    except PwTimeout:
        raise LoginError(f"MFA was not completed within {minutes:.0f} minutes") from None


def sign_in(page: Page, site: Site, credential: Credential,
            timeouts: Timeouts | None = None, observer: Observer | None = None) -> None:
    """Open the login page and finish signing in, or raise LoginError.

    The email and password are each sent at most once, so a wrong password can't lock
    the account by repeating. Never retried by this function or its callers.
    """
    t = timeouts or Timeouts()
    page.goto(site.login_url, wait_until="domcontentloaded")
    deadline = time.monotonic() + t.total_s
    sent: set[Step] = set()
    last_seen = None
    while True:
        if time.monotonic() > deadline:
            raise LoginError(f"sign-in did not finish within {t.total_s:.0f}s (on {_path(page)})")
        step = classify(page.url, site.app_url)
        if observer is not None and (step, page.url) != last_seen:
            observer(step)
        last_seen = (step, page.url)
        match step:
            case Step.SIGNED_IN:
                if _passkey_prompt_showing(page, t):
                    _skip_passkey(page, t, required=False)
                log.info("sign-in: signed in")
                return
            case Step.EMAIL | Step.PASSWORD:
                _check_bot_wall(page)
                if step in sent:
                    raise LoginError(f"the site returned to the {step} step after it was entered")
                sent.add(step)
                is_email = step is Step.EMAIL
                _submit(page, EMAIL_FIELD if is_email else PASSWORD_FIELD,
                        credential.username if is_email else credential.password, step, t)
            case Step.PASSKEY:
                _skip_passkey(page, t, required=True)
            case Step.MFA:
                _wait_for_mfa(page, site, t)
                deadline = time.monotonic() + t.total_s
            case Step.OTHER:
                _check_bot_wall(page)
                _wait_for_new_address(page, page.url, t.after_submit,
                                      "the sign-in stopped on an unknown page")
