"""Sign in and out of the site. Every command in this package goes through this flow.

All entities share one browser profile, so the site's memory of the device carries over,
but only one can be signed in at a time. A run may also find a session left by a run that
was stopped. So signing in always starts by signing out whoever is there.

The sign-in form can be on a separate sign-in server. On the way there, and back after signing
in, the browser passes through app addresses with nobody signed in, so the app counts only once
its Account menu shows, never from the address alone.

The MFA page's structure isn't known, so it's anything that isn't the sign-in form or the
app once the page has settled.

The page loads reCAPTCHA all the time, with a badge, so only a visible challenge counts as
a person-check. It's never worked around.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from playwright.sync_api import Error as PwError
from playwright.sync_api import Page

from automation_common.browser import click_until, find_first
from automation_common.secrets import Credential
from buildium_portal.config import Site

log = logging.getLogger(__name__)

APP_PATH = "/manager/app"
LOGIN_PATH = "/authentication/login"

EMAIL_FIELD = ["input#userName", "input#emailAddressInput", "input[type='email']"]
PASSWORD_FIELD = ["input#password", "input#passwordInput", "input[type='password']"]
# Exact text, because "Sign in with Google" sits right below it, and has had the same class
SIGN_IN_BUTTON = [
    "button.login__button:has(.button-text:text-is('Sign in'))",
    "button.login__button:text-is('Sign in')",
    "button.login__sign-in-button:text-is('Sign in')",
    "button.login__sign-in-button:has(:text-is('Sign in'))",
    "button[type='submit']:text-is('Sign in')",
]
ACCOUNT_MENU = [
    "nav :text-is('Account')",
    "button:has(:text-is('Account'))",
    "a:has(:text-is('Account'))",
    ":text-is('Account')",
]
SIGN_OUT_ITEM = [
    "[role='menuitem']:text-is('Sign out')",
    "a:text-is('Sign out')",
    "button:text-is('Sign out')",
    ":text-is('Sign out')",
]
BOT_CHECK = [
    "iframe[title*='recaptcha challenge' i]",
    "iframe[src*='recaptcha/api2/bframe']",
    "iframe[src*='hcaptcha.com']",
    "iframe[src*='challenges.cloudflare.com']",
]
POLL_MS = 100


class LoginError(Exception):
    """Sign-in didn't finish. The message never holds the address's query or a secret."""


class SignOutError(Exception):
    """Sign-out didn't finish, so whoever signs in next can't be trusted to be alone."""


class Step(StrEnum):
    SIGNED_IN = "signed in"
    LOGIN = "login"
    OTHER = "other"


@dataclass(frozen=True)
class Timeouts:
    """Milliseconds, except where the name says otherwise."""

    field: int = 15_000  # a field or button appearing
    after_submit: int = 30_000  # the page moving on after the password
    mfa_settle_s: float = 8  # the form gone and no app yet, before that counts as MFA
    mfa: int = 300_000  # someone opening the emailed link; leave room in the job's time limit
    menu: int = 3_000  # a menu opening after a click
    sign_out: int = 20_000  # leaving the app after Sign out


Observer = Callable[[str], None]
OnMfa = Callable[[], None]


def _ignore(_: str) -> None:
    pass


def classify(url: str, login_url: str) -> Step:
    """Work out the sign-in step from an address. Pure, so it's easy to test."""
    parts = urlsplit(url)
    if parts.hostname != urlsplit(login_url).hostname:
        return Step.OTHER
    path = parts.path.lower()
    if path == APP_PATH or path.startswith(f"{APP_PATH}/"):
        return Step.SIGNED_IN
    if LOGIN_PATH in path:
        return Step.LOGIN
    return Step.OTHER


def _path(page: Page) -> str:
    return urlsplit(page.url).path


def _in_app(page: Page, site: Site) -> bool:
    """Signed in: an app address with the Account menu showing (see the module's notes)."""
    return (classify(page.url, site.login_url) is Step.SIGNED_IN
            and find_first([page], ACCOUNT_MENU, 0)[0] is not None)


def _check_bot_wall(page: Page) -> None:
    found, selector = find_first([page], BOT_CHECK, 0)
    if found is not None:
        raise LoginError(f"the site is showing a CAPTCHA or bot check ({selector})")


def sign_out(page: Page, site: Site, timeouts: Timeouts | None = None,
             observer: Observer = _ignore) -> None:
    """Account, then Sign out, then wait to leave the app. Raises SignOutError otherwise.

    A failed download can leave the page somewhere with no menu, such as a browser error
    page. The session is still open there, so this goes back to the site first.
    """
    t = timeouts or Timeouts()
    if classify(page.url, site.login_url) is not Step.SIGNED_IN:
        try:
            if _open_login_form(page, site, t) is Step.LOGIN:
                return
        except (LoginError, PwError) as e:
            raise SignOutError(f"couldn't get back to the site ({type(e).__name__})") from None
    account, selector = find_first([page], ACCOUNT_MENU, t.field)
    if account is None:
        raise SignOutError(f"no Account menu on {_path(page)}")
    log.info("sign-out: opening the account menu (%s)", selector)
    opened = click_until(account, lambda: find_first([page], SIGN_OUT_ITEM, t.menu)[0] is not None,
                         "account menu")
    if opened is None:
        raise SignOutError(f"the Account menu didn't show Sign out on {_path(page)}")
    observer("account_menu")
    item, _ = find_first([page], SIGN_OUT_ITEM, t.menu)
    if item is None:
        raise SignOutError(f"Sign out disappeared from the menu on {_path(page)}")

    def left_app() -> bool:
        try:
            page.wait_for_url(lambda url: classify(url, site.login_url) is not Step.SIGNED_IN,
                              timeout=t.sign_out)
        except PwError:  # includes a timeout
            return False
        return True

    # The Account button's tooltip can cover the item while the mouse is still on the button
    try:
        item.hover(force=True, timeout=t.menu)
    except PwError as e:
        log.debug("sign-out: couldn't move the mouse to Sign out (%s)", type(e).__name__)
    how = click_until(item, left_app, "Sign out")
    if how is None:
        raise SignOutError("still in the app after Sign out")
    log.info("sign-out: signed out (%s)", how)


def _open_login_form(page: Page, site: Site, t: Timeouts) -> Step:
    """Load the sign-in page and wait until it shows the form or the app's menu."""
    page.goto(site.login_url, wait_until="domcontentloaded")
    deadline = time.monotonic() + t.field / 1000
    while True:
        if find_first([page], EMAIL_FIELD, 0)[0] is not None:
            return Step.LOGIN
        if _in_app(page, site):
            return Step.SIGNED_IN
        if time.monotonic() > deadline:
            raise LoginError(f"neither the sign-in form nor the app appeared on {_path(page)}")
        page.wait_for_timeout(POLL_MS)


def _to_a_clean_login_form(page: Page, site: Site, t: Timeouts) -> None:
    if _open_login_form(page, site, t) is Step.LOGIN:
        return
    log.warning("sign-in: a session was still open, so signing it out first")
    sign_out(page, site, t)
    if _open_login_form(page, site, t) is not Step.LOGIN:
        raise SignOutError("still signed in after signing out")


def _required(page: Page, candidates: list[str], what: str, t: Timeouts):
    found, selector = find_first([page], candidates, t.field)
    if found is None:
        raise LoginError(f"the {what} did not appear on {_path(page)}")
    log.info("sign-in: found the %s (%s)", what, selector)
    return found


def _wait_after_submit(page: Page, site: Site, t: Timeouts) -> Step:
    """Return SIGNED_IN or OTHER (MFA), or raise if the form never goes away.

    The form also vanishes for a moment on the way into the app, so it must stay gone for
    a while before this counts as MFA, or every normal sign-in would send a notification.
    An app address is on the way in, not MFA, until the app has had `after_submit` to load.
    """
    deadline = time.monotonic() + t.after_submit / 1000
    form_gone_since: float | None = None
    while True:
        if _in_app(page, site):
            return Step.SIGNED_IN
        _check_bot_wall(page)
        now = time.monotonic()
        if classify(page.url, site.login_url) is Step.SIGNED_IN:
            form_gone_since = None
            if now > deadline:
                raise LoginError(f"the app didn't finish loading on {_path(page)}")
        elif find_first([page], EMAIL_FIELD + PASSWORD_FIELD, 0)[0] is not None:
            form_gone_since = None
            if now > deadline:
                raise LoginError("the site did not accept the email and password")
        else:
            form_gone_since = form_gone_since or now
            if now - form_gone_since >= t.mfa_settle_s:
                return Step.OTHER
        page.wait_for_timeout(POLL_MS)


def _wait_for_mfa(page: Page, site: Site, t: Timeouts, on_mfa: OnMfa) -> None:
    seconds = t.mfa // 1000
    log.warning("sign-in: the site wants MFA on %s; waiting up to %d seconds",
                _path(page), seconds)
    on_mfa()
    deadline = time.monotonic() + t.mfa / 1000
    while not _in_app(page, site):
        if time.monotonic() > deadline:
            raise LoginError(f"MFA was not completed within {seconds} seconds")
        page.wait_for_timeout(POLL_MS)


def sign_in(page: Page, site: Site, credential: Credential, timeouts: Timeouts | None = None,
            on_mfa: OnMfa | None = None, observer: Observer = _ignore) -> None:
    """Sign out anyone still there, then sign this credential in, or raise.

    The email and password are sent at most once, so a wrong password can't lock the
    account by repeating. Never retried by this function or its callers.
    """
    t = timeouts or Timeouts()
    _to_a_clean_login_form(page, site, t)
    _check_bot_wall(page)
    observer("login")  # before typing, so a saved copy holds nothing typed
    _required(page, EMAIL_FIELD, "email field", t).fill(credential.username)
    _required(page, PASSWORD_FIELD, "password field", t).fill(credential.password)
    _required(page, SIGN_IN_BUTTON, "Sign in button", t).click()
    if _wait_after_submit(page, site, t) is not Step.SIGNED_IN:
        observer("mfa")
        _wait_for_mfa(page, site, t, on_mfa or (lambda: None))
    log.info("sign-in: signed in")
