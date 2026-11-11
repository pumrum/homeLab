"""Sign in to and out of the site. Every command in this package goes through this flow.

One login reaches every account. The sign-in page comes in more than one form: the fields
may sit in an iframe, and their selectors differ between visits, so each is a list of
candidates searched in the iframe and then the page.

After the password, the site may ask to verify the device with a push to its phone app.
That's only taken as the prompt when its words are on screen on two checks in a row with
the sign-in form gone, and never once the dashboard has loaded. Words alone gave a false
alarm before. The site may first offer a choice of how to confirm: the run always picks
the push to the phone app, never signing in to the app. It then sends its own notification
and waits for the dashboard, up to a limit.

Nothing here is ever retried: a username and password are sent at most once per run, so
a wrong password can't lock the login.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from playwright.sync_api import Error as PwError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PwTimeout

from automation_common.browser import Scope, click_until, find_first
from automation_common.secrets import Credential
from bank1_portal.config import Site
from bank1_portal.pages import visible_text

log = logging.getLogger(__name__)

LOGON_FRAME = ["#logonbox"]
USERNAME_FIELD = ["#userId-input-field", "#userId input", "input[name='username']",
                  "input[autocomplete='username']"]
PASSWORD_FIELD = ["#password-input-field", "#password input", "input[type='password']"]
SIGN_IN_BUTTON = ["#signin-button", "#signin-button button", "button[type='submit']"]
SIGN_OUT = ["a:text-is('Sign out')", "button:text-is('Sign out')",
            "mds-button:has-text('Sign out')", "[aria-label='Sign out']", ":text-is('Sign out')"]
BOT_CHECK = [
    "iframe[title*='recaptcha challenge' i]",
    "iframe[src*='recaptcha/api2/bframe']",
    "iframe[src*='hcaptcha.com']",
    "iframe[src*='challenges.cloudflare.com']",
]
# Lowercase. Both apostrophes, since the site uses the curly one in places
VERIFY_TEXT = ["confirm your identity", "verify your identity", "we don't recognize",
               "we don’t recognize", "let's make sure it's you", "let’s make sure it’s you",
               "get a code", "mobile app"]
# The verification chooser's push option. Never the "Sign into our mobile app" one
PUSH_OPTION = [
    "button:has-text('Confirm using our mobile app')",
    "a:has-text('Confirm using our mobile app')",
    "[role='button']:has-text('Confirm using our mobile app')",
    "[role='link']:has-text('Confirm using our mobile app')",
    "mds-list-item:has-text('Confirm using our mobile app')",
    ":text('Confirm using our mobile app')",
]
# Lowercase. Words that show the chooser is up, so a missing push option is an error
CHOOSER_TEXT = ["choose a confirmation method", "confirmation method"]
DASHBOARD_MARK = "dashboard"  # in the address once signed in


class LoginError(Exception):
    """Sign-in didn't finish. The message never holds an address or a secret."""


class VerificationTimeout(LoginError):
    """The device verification wasn't approved in time. The whole run stops."""


class SignOutError(Exception):
    """Sign-out didn't finish. Only a warning: the site ends the session on its own."""


@dataclass(frozen=True)
class Timeouts:
    """Milliseconds."""

    field: int = 20_000  # the sign-in form appearing
    after_submit: int = 45_000  # the page moving on after the password
    choice: int = 10_000  # the verification chooser's push option, and it going away
    verify: int = 300_000  # approving on the phone; leave room in the job's time limit
    dashboard: int = 30_000  # the dashboard finishing its load
    sign_out: int = 20_000
    poll: int = 1_000  # between checks; a bank's pages are slow, so no need to rush


Observer = Callable[[str], None]
OnVerify = Callable[[], None]


def _ignore(_: str) -> None:
    pass


def on_dashboard(url: str) -> bool:
    return DASHBOARD_MARK in url.lower()


def _scopes(page: Page) -> list[Scope]:
    return [page.frame_locator(s) for s in LOGON_FRAME] + [page]


def _form_showing(page: Page) -> bool:
    return find_first(_scopes(page), USERNAME_FIELD + PASSWORD_FIELD, 0)[0] is not None


def asks_to_verify(text: str) -> bool:
    lowered = text.lower()
    return any(words in lowered for words in VERIFY_TEXT)


def _check_bot_wall(page: Page) -> None:
    found, selector = find_first(_scopes(page), BOT_CHECK, 0)
    if found is not None:
        raise LoginError(f"the site is showing a CAPTCHA or bot check ({selector})")


def _required(page: Page, candidates: list[str], what: str, timeout_ms: int):
    found, selector = find_first(_scopes(page), candidates, timeout_ms)
    if found is None:
        raise LoginError(f"the {what} did not appear")
    log.info("sign-in: found the %s (%s)", what, selector)
    return found


def _open_sign_in(page: Page, site: Site, t: Timeouts) -> bool:
    """Load the sign-in page. True if the profile is still signed in."""
    page.goto(site.login_url, wait_until="domcontentloaded")
    deadline = time.monotonic() + t.field / 1000
    while True:
        if on_dashboard(page.url):
            return True
        if find_first(_scopes(page), USERNAME_FIELD, 0)[0] is not None:
            return False
        if time.monotonic() > deadline:
            raise LoginError("the sign-in form did not appear")
        page.wait_for_timeout(t.poll)


def _wait_after_submit(page: Page, t: Timeouts) -> bool:
    """True when the dashboard loads, False when the site asks to verify the device."""
    deadline = time.monotonic() + t.after_submit / 1000
    seen = 0
    while True:
        if on_dashboard(page.url):
            return True
        _check_bot_wall(page)
        form = _form_showing(page)
        seen = seen + 1 if not form and asks_to_verify(visible_text(page)) else 0
        if seen >= 2:
            return False
        if time.monotonic() > deadline:
            if form:
                raise LoginError("the site did not accept the username and password")
            raise LoginError("neither the dashboard nor a verification prompt appeared")
        page.wait_for_timeout(t.poll)


def _choose_push(page: Page, t: Timeouts) -> None:
    """On the chooser, pick the push to the phone app. A page without one already sent it."""
    option, selector = find_first(_scopes(page), PUSH_OPTION, t.choice)
    if option is None:
        text = visible_text(page).lower()
        if any(words in text for words in CHOOSER_TEXT):
            raise LoginError("the verification page has no 'Confirm using our mobile app' option")
        log.info("sign-in: no verification chooser; the site sent the push itself")
        return
    log.info("sign-in: choosing the push to the phone app (%s)", selector)

    def chosen() -> bool:
        try:
            option.wait_for(state="hidden", timeout=t.choice)
        except PwError:  # includes a timeout
            return on_dashboard(page.url)
        return True

    if click_until(option, chosen, "Confirm using our mobile app") is None:
        raise LoginError("the verification page didn't move on after choosing the push")


def _wait_for_verify(page: Page, t: Timeouts, on_verify: OnVerify) -> None:
    seconds = t.verify // 1000
    log.warning("sign-in: the site wants to verify this device; waiting up to %d seconds",
                seconds)
    on_verify()
    try:
        page.wait_for_url(on_dashboard, timeout=t.verify)
    except PwTimeout:
        raise VerificationTimeout(f"verification timed out after {seconds} seconds") from None
    log.info("sign-in: verification approved")


def _settle(page: Page, t: Timeouts) -> None:
    """Some statements links only work once the dashboard has finished loading."""
    try:
        page.wait_for_load_state("load", timeout=t.dashboard)
    except PwTimeout:
        log.warning("sign-in: the dashboard was still loading after %d seconds; going on",
                    t.dashboard // 1000)


def sign_in(page: Page, site: Site, credential: Credential, timeouts: Timeouts | None = None,
            on_verify: OnVerify | None = None, observer: Observer = _ignore) -> None:
    """Sign in, verifying the device if asked, and wait for the dashboard. Raises otherwise."""
    t = timeouts or Timeouts()
    if _open_sign_in(page, site, t):
        log.info("sign-in: the profile is still signed in")
    else:
        _check_bot_wall(page)
        observer("login")  # before typing, so a saved copy holds nothing typed
        _required(page, USERNAME_FIELD, "username field", t.field).fill(credential.username)
        _required(page, PASSWORD_FIELD, "password field", t.field).fill(credential.password)
        _required(page, SIGN_IN_BUTTON, "Sign in button", t.field).click()
        if not _wait_after_submit(page, t):
            observer("verify")  # the chooser, if the site shows one
            _choose_push(page, t)
            observer("push")
            _wait_for_verify(page, t, on_verify or (lambda: None))
    _settle(page, t)
    observer("dashboard")
    log.info("sign-in: signed in")


def sign_out(page: Page, timeouts: Timeouts | None = None) -> None:
    """Click Sign out and wait for the page to move on. Raises SignOutError otherwise."""
    t = timeouts or Timeouts()
    button, selector = find_first([page], SIGN_OUT, t.field)
    if button is None:
        raise SignOutError("no Sign out control on the page")
    log.info("sign-out: found Sign out (%s)", selector)
    before = page.url

    def moved_on() -> bool:
        try:
            page.wait_for_url(lambda url: url != before and not on_dashboard(url),
                              timeout=t.sign_out)
        except PwError:  # includes a timeout
            return False
        return True

    how = click_until(button, moved_on, "Sign out")
    if how is None:
        raise SignOutError("the page didn't move on after Sign out")
    log.info("sign-out: signed out (%s)", how)
