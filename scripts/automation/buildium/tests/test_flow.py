"""Sign-in, sign-out, the statement menus, and whole runs, in real Edge against a fake site.

Playwright answers the site's requests from here, and the PDFs come from a server on
127.0.0.1, so nothing leaves this machine. These run on machines with Edge, and are skipped
elsewhere. The classify tests run everywhere.
"""

import datetime as dt
import http.server
import json
import threading
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlsplit

import keyring
import pytest
from playwright.sync_api import Error as PwError
from playwright.sync_api import sync_playwright

from automation_common.secrets import Credential
from automation_common.sheets import SheetError
from buildium_portal import capture as capture_module
from buildium_portal import gl_fetch, gl_main, session, statements_fetch
from buildium_portal import login as login_module
from buildium_portal.config import Site
from buildium_portal.gl_fetch import date_forms, export_method, open_report, report_url
from buildium_portal.gl_parse import EXPECTED_COLUMNS
from buildium_portal.login import (
    LoginError,
    SignOutError,
    Step,
    Timeouts,
    classify,
    sign_in,
    sign_out,
)
from buildium_portal.main import main
from buildium_portal.statements_fetch import fetch_statement

HOST = "https://site.example.invalid"
LOGIN = "/manager/public/authentication/login"
HOME = "/manager/app/home"
LEDGER = "/manager/app/ledger"
GL_REPORT = "/manager/app/reports/Financials/GeneralLedger"
MFA ="/manager/public/authentication/verify"
# The newer flow: the sign-in address goes through an app page with no menu to a separate
# sign-in server, and after signing in comes back through another one
AUTH = "https://auth.example.invalid"
AUTH_LOGIN = "/authentication/login"
APP_LOGIN = "/manager/app/login"
APP_RETURN = "/manager/app/login/redirect"
SITE = Site(f"{HOST}{LOGIN}")
CRED = Credential("one@example.invalid", "made-up-password")
FAST = Timeouts(field=2_000, after_submit=2_000, mfa_settle_s=0.3, mfa=1_500, menu=500,
                sign_out=1_500)
TODAY = dt.date(2026, 11, 5)
ONE = "FakeEntity One, LLC"
TWO = "FakeEntity Two, LLC"
USERS = {"one@example.invalid": ONE, "two@example.invalid": TWO}


@pytest.mark.parametrize(("url", "step"), [
    (f"{HOST}{HOME}", Step.SIGNED_IN),
    (f"{HOST}/manager/app", Step.SIGNED_IN),
    (f"{HOST}/manager/app/accounting/ledger?x=1", Step.SIGNED_IN),
    (f"{HOST}/manager/application", Step.OTHER),
    (f"{HOST}{LOGIN}", Step.LOGIN),
    (f"{HOST}{MFA}", Step.OTHER),
    (f"https://elsewhere.example.invalid{HOME}", Step.OTHER),
    ("about:blank", Step.OTHER),
])
def test_classify(url, step):
    assert classify(url, SITE.login_url) is step


# Mirrors the real sign-in page's structure: no form element, a shared button class, and a
# reCAPTCHA badge that's always there. The fields are read and posted by script
LOGIN_PAGE = """<h1>Sign in</h1>
<input id="emailAddressInput" type="text">
<input id="passwordInput" type="password">
<input id="rememberEmailCheckbox" type="checkbox">
<button class="btn btn--primary save login__sign-in-button" type="submit" onclick="send()">
  <span>Sign in</span></button>
<button class="btn login__sign-in-button" type="submit"
  onclick="fetch('/google', {method: 'POST'})">Sign in with Google</button>
<div class="grecaptcha-badge" style="width:250px;height:60px">
  <iframe title="reCAPTCHA" src="about:blank" width="250" height="60"></iframe></div>
__CHALLENGE__
<script>
  async function send() {
    const body = JSON.stringify({
      email: document.getElementById('emailAddressInput').value,
      password: document.getElementById('passwordInput').value});
    const reply = await (await fetch('/api/login', {method: 'POST', body})).json();
    if (reply.go) location = reply.go;
  }
</script>"""
# Mirrors the sign-in server's page: a real form, and the Google button now has the class the
# old Sign in button had
AUTH_LOGIN_PAGE = """<h1 class="login__sign-in-text">Sign in</h1>
<form onsubmit="send(); return false">
  <label class="form__label">Email Address</label>
  <input id="userName" class="form-element__input" type="text">
  <label class="form__label">Password</label>
  <input id="password" class="form-element__input" type="password">
  <input id="rememberMe" type="checkbox">
  <button class="btn btn-primary login__button" type="submit">
    <span class="spinner d-none"></span><span class="button-text">Sign in</span></button>
</form>
<form onsubmit="fetch('/google', {method: 'POST'}); return false">
  <button class="btn login__sign-in-button" type="submit">
    <span class="login__sign-in-svg-icon"></span><span>Sign in with Google</span></button>
</form>
<div class="grecaptcha-badge" style="width:250px;height:60px">
  <iframe title="reCAPTCHA" src="about:blank" width="250" height="60"></iframe></div>
__CHALLENGE__
<script>
  async function send() {
    const body = JSON.stringify({
      email: document.getElementById('userName').value,
      password: document.getElementById('password').value});
    const reply = await (await fetch('/api/login', {method: 'POST', body})).json();
    if (reply.go) location = reply.go;
  }
</script>"""
# Long enough that a check of the address alone would see it
PASSING_THROUGH_MS = 400
CHALLENGE = """<iframe title="recaptcha challenge expires in two minutes" src="about:blank"
  width="300" height="300"></iframe>"""
NAV = """<nav>
  <a id="owner" href="#" onclick="show('owner-menu'); return false">Rental owner</a>
  <div id="owner-menu" style="display:none">
    <a href="/manager/app/financials">Financials</a>
    <a href="/manager/app/ledger">Rental owner ledger</a>
    <a href="/manager/app/documents">Documents</a></div>
  <a id="account" href="#" onclick="show('account-menu'); return false"><span>Account</span></a>
  <div id="account-menu" style="display:none; position:relative">
    <a href="/manager/app/settings">My settings</a>
    <a href="__SIGN_OUT__">Sign out</a>__COVER__</div>
</nav>
<script>function show(id) { document.getElementById(id).style.display = 'block'; }</script>"""
# The real Account button's tooltip names the account, and can sit on top of Sign out
COVER = """<div class="cover" style="position:absolute; inset:0; background:#333">A name</div>"""
TOOLTIP = """<style>.cover {display:none} #account:hover ~ #account-menu .cover {display:block}
</style>""" + COVER
LEDGER_PAGE = """<button onclick="location = '/send-funds'">Send funds</button>
<a href="#" onclick="show('statement-menu'); return false">Statement</a>
<ul id="statement-menu" style="display:none">
  <li><a href="#" onclick="get('/current.pdf'); return false">Current period</a></li>
  <li><a href="#" onclick="get('__PDF__'); return false">Last period ending
    the last day</a></li>
  <li><a href="#" onclick="get('/year.pdf'); return false">Year to date</a></li>
  <li><a href="#" onclick="get('/custom.pdf'); return false">Custom statement</a></li>
</ul>
<script>function get(path) { location = '__BASE__' + path; }</script>"""


# Built from the ids and names the old process used on the real report, which this code hasn't
# run against yet. A PDF item sits beside the CSV one, as the old process's menu implies
GL_PAGE = """<form id="ReportFiltersForm" name="ReportFiltersForm">
  <div id="daterange-dropdown" onclick="show('daterange-menu')">Last month</div>
  <ul id="daterange-menu" style="display:none">
    <li id="daterange-dropdown-menu-item-1"><span>Last month</span></li>
    <li id="daterange-dropdown-menu-item-10" onclick="custom()"><span>Custom</span></li>
  </ul>
  <div id="dates" style="display:none">
    <input name="dateInput_fromDate" onblur="tidy(this)">
    <input name="dateInput_toDate" onblur="tidy(this)">
  </div>
  <label>
                 Cash
             <input type="radio" name="method" value="Cash" checked></label>
  <label>
                 Accrual
             <input type="radio" name="method" value="Accrual"></label>
</form>
<a id="exportReportButton" href="#" onclick="show('export-menu'); return false">Export</a>
<ul id="export-menu" style="display:none">
  <li><a href="#" onclick="fetch('/pdf-export'); return false">PDF (.pdf)</a></li>
  <li><a href="#" onclick="get(); return false"><span>Comma-separated (.csv)</span></a></li>
</ul>
<script>
  function custom() { document.getElementById('dates').style.display = 'block'; }
  function tidy(box) {
    if (!__PAD_DATES__) return;
    const [m, d, y] = box.value.split('/');
    box.value = `${m.padStart(2, '0')}/${d.padStart(2, '0')}/${y}`;
  }
  function get() {
    const method = document.querySelector('input[name=method]:checked').value;
    const from = document.querySelector('input[name=dateInput_fromDate]').value;
    const to = document.querySelector('input[name=dateInput_toDate]').value;
    location = `__BASE__/__USER___${method}.csv?from=${from}&to=${to}`;
  }
</script>"""


class PdfServer:
    """Serves files over a real connection on 127.0.0.1, as attachments like the real site's."""

    def __init__(self):
        self.files = {}
        self.fetches = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.fetches.append(self.path)
                path = urlsplit(self.path).path
                body = owner.files.get(path, b"not found")
                is_csv = path.endswith(".csv")
                name = "General_Ledger.csv" if is_csv else "Rental_owner_statement.pdf"
                self.send_response(200 if path in owner.files else 404)
                self.send_header("Content-Type", "text/csv" if is_csv else "application/pdf")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


class FakeSite:
    """One sign-in page and an app, answered by Playwright. One user at a time, like the site."""

    def __init__(self, signed_in_as=None, mfa=False, mfa_clears=False, reject=False,
                 challenge=False, sign_out_works=True, pdf_for=None, pad_dates=False,
                 sign_out_cover="", auth_server=False):
        self.user = signed_in_as
        self.auth_server = auth_server
        self.mfa = mfa  # True, or the set of users who get asked
        self.mfa_clears = mfa_clears  # someone opens the emailed link
        self.reject = reject
        self.challenge = challenge
        self.sign_out_works = sign_out_works
        self.sign_out_cover = sign_out_cover
        self.pdf_for = pdf_for or (lambda user: f"/{user.split('@')[0]}.pdf")
        self.pad_dates = pad_dates
        self.logins = []
        self.paths = []
        self.server = None

    def _html(self, route, body):
        route.fulfill(status=200, content_type="text/html", body=f"<!doctype html>{body}")

    def _go(self, route, path, host=HOST):
        self._html(route, f"<script>location.replace('{host}{path}')</script>")

    def _pass_through(self, route, url):
        self._html(route, "<p>Loading</p><script>setTimeout(() => location.replace("
                          f"'{url}'), {PASSING_THROUGH_MS})</script>")

    def _auth(self, route, path):
        if path == AUTH_LOGIN:
            page = AUTH_LOGIN_PAGE.replace("__CHALLENGE__", CHALLENGE if self.challenge else "")
            return self._html(route, page)
        if path == APP_LOGIN:
            if self.user:
                return self._go(route, HOME)
            return self._pass_through(route, f"{AUTH}{AUTH_LOGIN}")
        return self._pass_through(route, f"{HOST}{HOME}")  # APP_RETURN

    def _wants_mfa(self, user):
        return self.mfa is True or (self.mfa and user in self.mfa)

    def _app(self, route, path):
        nav = (NAV.replace("__SIGN_OUT__", "/sign-out" if self.sign_out_works else "#")
               .replace("__COVER__", self.sign_out_cover))
        if path == LEDGER:
            ledger = (LEDGER_PAGE.replace("__PDF__", self.pdf_for(self.user))
                      .replace("__BASE__", self.server.base))
            return self._html(route, nav + ledger)
        if path == GL_REPORT:
            report = (GL_PAGE.replace("__PAD_DATES__", "true" if self.pad_dates else "false")
                      .replace("__BASE__", self.server.base)
                      .replace("__USER__", self.user.split("@")[0]))
            return self._html(route, nav + report)
        return self._html(route, f"{nav}<h1>Home</h1>")

    def handle(self, route):
        request = route.request
        if self.server and request.url.startswith(self.server.base):
            return route.continue_()
        if request.url == "about:blank":
            return route.continue_()
        path = urlsplit(request.url).path
        self.paths.append(path)
        if self.auth_server and (path in (APP_LOGIN, APP_RETURN)
                                 or urlsplit(request.url).hostname == "auth.example.invalid"
                                 and path != "/api/login"):
            return self._auth(route, path)
        if path == "/api/login":
            return self._api_login(route, json.loads(request.post_data))
        if path == "/sign-out":
            self.user = None
            return self._go(route, LOGIN)
        if path == LOGIN:
            if self.auth_server:
                return self._go(route, APP_LOGIN)
            if self.user:
                return self._go(route, HOME)
            page = LOGIN_PAGE.replace("__CHALLENGE__", CHALLENGE if self.challenge else "")
            return self._html(route, page)
        if path == MFA:
            clears = f"<script>setTimeout(() => location = '{HOME}', 200)</script>"
            return self._html(route, "<h1>Check your email for a link</h1>"
                              + (clears if self.mfa_clears else ""))
        if path.startswith("/manager/app"):
            if self.user is None:
                return self._go(route, APP_LOGIN if self.auth_server else LOGIN)
            return self._app(route, path)
        return self._html(route, "<h1>Other</h1>")

    def _api_login(self, route, fields):
        self.logins.append(fields)
        email = fields["email"]
        if self.reject or email not in USERS:
            return route.fulfill(status=200, content_type="application/json", body="{}")
        self.user = email
        home = f"{HOST}{APP_RETURN}" if self.auth_server else HOME
        go = MFA if self._wants_mfa(email) else home
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps({"go": go}))

    def use(self, server, pdfs):
        self.server = server
        server.files = pdfs


@pytest.fixture(scope="session")
def edge_installed():
    with sync_playwright() as pw:
        try:
            pw.chromium.launch(channel="msedge", headless=True).close()
            return True
        except PwError:
            return False


@pytest.fixture
def needs_edge(edge_installed):
    if not edge_installed:
        pytest.skip("Edge isn't installed here")


@pytest.fixture
def pdf_server():
    server = PdfServer()
    yield server
    server.close()


@pytest.fixture
def open_site(needs_edge, pdf_server):
    with sync_playwright() as pw:
        edge = pw.chromium.launch(channel="msedge", headless=True)

        def opened(site, pdfs=None):
            ctx = edge.new_context(accept_downloads=True)
            site.use(pdf_server, pdfs or {})
            ctx.route("**/*", site.handle)
            ctx.set_default_timeout(3_000)
            return ctx.new_page()
        yield opened
        edge.close()


def test_a_sign_in_sends_each_value_once_and_never_uses_google(open_site):
    site = FakeSite()
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    assert urlsplit(page.url).path == HOME
    assert site.logins == [{"email": CRED.username, "password": CRED.password}]
    assert "/google" not in site.paths


def test_a_sign_in_through_a_sign_in_server_isnt_taken_for_a_session_left_open(open_site):
    # The app address it passes through, with nobody signed in, once stopped every run
    site = FakeSite(auth_server=True)
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    assert urlsplit(page.url).path == HOME
    assert site.logins == [{"email": CRED.username, "password": CRED.password}]
    assert "/sign-out" not in site.paths
    assert "/google" not in site.paths


def test_a_session_left_open_is_signed_out_first_with_a_sign_in_server(open_site):
    site = FakeSite(signed_in_as="two@example.invalid", auth_server=True)
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    assert "/sign-out" in site.paths
    assert site.user == CRED.username


def test_sign_out_reaches_the_sign_in_server(open_site):
    site = FakeSite(auth_server=True)
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    sign_out(page, SITE, FAST)
    assert site.user is None
    page.wait_for_url(f"{AUTH}{AUTH_LOGIN}")


def test_an_app_that_never_shows_its_menu_isnt_signed_in(open_site):
    site = FakeSite(auth_server=True)
    site._pass_through = lambda route, url: site._html(route, "<p>Loading</p>")
    with pytest.raises(LoginError, match="neither the sign-in form nor the app"):
        sign_in(open_site(site), SITE, CRED, FAST)
    assert site.logins == []


def test_the_always_there_recaptcha_badge_doesnt_stop_a_sign_in(open_site):
    # This is why the check differs from a site with no badge: the real page has one
    page = open_site(FakeSite())
    sign_in(page, SITE, CRED, FAST)
    assert urlsplit(page.url).path == HOME


def test_a_visible_challenge_fails_without_sending_anything(open_site):
    site = FakeSite(challenge=True)
    with pytest.raises(LoginError, match="CAPTCHA"):
        sign_in(open_site(site), SITE, CRED, FAST)
    assert site.logins == []


def test_a_rejected_password_is_sent_only_once_and_stays_out_of_the_message(open_site):
    site = FakeSite(reject=True)
    with pytest.raises(LoginError, match="did not accept") as caught:
        sign_in(open_site(site), SITE, CRED, FAST)
    assert len(site.logins) == 1
    assert CRED.password not in str(caught.value)


def test_a_session_left_open_is_signed_out_first(open_site):
    # Otherwise the next entity's statement would really be the last run's entity's
    site = FakeSite(signed_in_as="two@example.invalid")
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    assert "/sign-out" in site.paths
    assert site.user == CRED.username


def test_a_session_that_wont_sign_out_is_not_used(open_site):
    site = FakeSite(signed_in_as="two@example.invalid", sign_out_works=False)
    with pytest.raises(SignOutError):
        sign_in(open_site(site), SITE, CRED, FAST)
    assert site.logins == []


def test_mfa_notifies_once_and_continues_when_the_link_is_opened(open_site):
    site = FakeSite(mfa=True, mfa_clears=True)
    page = open_site(site)
    calls = []
    # A short settle, so the page is noticed before the link moves it on
    sign_in(page, SITE, CRED, Timeouts(field=2_000, after_submit=2_000, mfa_settle_s=0.05,
                                       mfa=3_000, menu=500, sign_out=1_500),
            on_mfa=lambda: calls.append("notified"))
    assert urlsplit(page.url).path == HOME
    assert calls == ["notified"]


def test_mfa_times_out_after_notifying(open_site):
    calls = []
    with pytest.raises(LoginError, match="MFA was not completed"):
        sign_in(open_site(FakeSite(mfa=True)), SITE, CRED, FAST,
                on_mfa=lambda: calls.append("notified"))
    assert calls == ["notified"]


def test_a_normal_sign_in_never_notifies(open_site):
    calls = []
    sign_in(open_site(FakeSite()), SITE, CRED, FAST, on_mfa=lambda: calls.append("notified"))
    assert calls == []


def test_sign_out_uses_the_account_menu(open_site):
    site = FakeSite()
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    sign_out(page, SITE, FAST)
    assert site.user is None
    assert classify(page.url, SITE.login_url) is Step.LOGIN


@pytest.mark.parametrize("cover", [TOOLTIP, COVER], ids=["while hovering", "always"])
def test_sign_out_works_with_something_on_top_of_it(open_site, cover):
    site = FakeSite(sign_out_cover=cover)
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    sign_out(page, SITE, FAST)
    assert site.user is None
    assert classify(page.url, SITE.login_url) is Step.LOGIN


def test_sign_out_that_stays_in_the_app_fails(open_site):
    site = FakeSite(sign_out_works=False)
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    with pytest.raises(SignOutError, match="still in the app"):
        sign_out(page, SITE, FAST)


def test_sign_out_works_from_a_page_outside_the_app(open_site):
    # A failed download can leave the page on an error page, with the session still open
    site = FakeSite()
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    page.goto("about:blank")
    sign_out(page, SITE, FAST)
    assert site.user is None


def test_sign_out_with_no_session_has_nothing_to_do(open_site):
    site = FakeSite()
    page = open_site(site)
    page.goto("about:blank")
    sign_out(page, SITE, FAST)
    assert "/sign-out" not in site.paths


def test_the_statement_comes_from_only_the_exact_menu_item(open_site, pdf_server, pdf_of,
                                                           tmp_path, monkeypatch):
    monkeypatch.setattr(statements_fetch, "PAGE_TIMEOUT_MS", 2_000)
    pdf = pdf_of()
    site = FakeSite()
    page = open_site(site, {"/one.pdf": pdf})
    sign_in(page, SITE, CRED, FAST)
    dest = tmp_path / "statement.pdf"
    fetch_statement(page, dest)
    assert dest.read_bytes() == pdf
    assert pdf_server.fetches == ["/one.pdf"]  # never another period
    assert "/send-funds" not in site.paths


# ---------------------------------------------- whole runs, with a real browser profile

CONFIG = f"""[site]
login_url = "{SITE.login_url}"
[[entities]]
key = "entity1"
abbreviation = "ONELLC"
statement_name = "{ONE}"
gl_code = "FAKECODE1"
gl_tab = "FakeTabOne"
gl_start_cell = "B5"
[[entities]]
key = "entity2"
abbreviation = "TWOLLC"
statement_name = "{TWO}"
gl_code = "FAKECODE2"
gl_tab = "FakeTabTwo"
gl_start_cell = "C9"
gl_write_tab = true
[gl]
spreadsheet_id = "made-up-spreadsheet-id"
master_tab = "FakeMasterTab"
[statements]
system = "FakeSystem"
"""


@pytest.fixture
def whole_run(needs_edge, pdf_server, tmp_path, monkeypatch, pdf_of):
    """Everything real but the network, the window, and the stored credentials."""
    def launch(site):
        def launch_edge(pw, profile):
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), channel="msedge", headless=True, accept_downloads=True)
            ctx.route("**/*", site.handle)
            ctx.set_default_timeout(3_000)
            return ctx
        return launch_edge

    notes = []
    pdfs = {"/one.pdf": pdf_of(owner=ONE), "/two.pdf": pdf_of(owner=TWO)}

    def start(site, *args, files=None, command=main):
        site.use(pdf_server, pdfs if files is None else files)
        monkeypatch.setattr(session, "launch_edge", launch(site))
        monkeypatch.setattr(capture_module, "launch_edge", launch(site))
        monkeypatch.setattr(login_module, "Timeouts", lambda: FAST)
        monkeypatch.setattr(statements_fetch, "PAGE_TIMEOUT_MS", 2_000)
        monkeypatch.setattr(statements_fetch, "DOWNLOAD_TIMEOUT_MS", 1_500)
        monkeypatch.setattr(gl_fetch, "PAGE_TIMEOUT_MS", 2_000)
        monkeypatch.setattr(gl_fetch, "DOWNLOAD_TIMEOUT_MS", 1_500)
        return command(list(args), notifier=lambda note, masker: notes.append(note) or True,
                       today=TODAY)

    def credentials(name, user):
        email = {"buildium/entity1": "one@example.invalid",
                 "buildium/entity2": "two@example.invalid"}.get(name)
        return SimpleNamespace(username=email, password=f"password-{email}") if email else None

    monkeypatch.setenv("AUTOMATION_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(keyring, "get_credential", credentials)
    data = tmp_path / "buildium"
    data.mkdir()
    (data / "config.toml").write_text(CONFIG, encoding="utf-8")
    return SimpleNamespace(start=start, data=data, notes=notes)


def _run_dir(data):
    return sorted((data / "runs").iterdir())[-1]


def _report(data):
    return (_run_dir(data) / "report.txt").read_text(encoding="utf-8")


def _written(data):
    found = ""
    for path in (data / "runs").rglob("*"):
        if path.suffix in (".log", ".txt", ".json", ".html"):
            found += path.read_text(encoding="utf-8")
    return found


def _saved(data):
    folder = data / "data" / "statements" / "2026-10"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


ONE_FILE = "2026-10-31_FakeSystem_Ledger_ONELLC.pdf"
TWO_FILE = "2026-10-31_FakeSystem_Ledger_TWOLLC.pdf"


def test_a_normal_run_saves_each_entitys_statement_and_signs_out(whole_run):
    site = FakeSite()
    assert whole_run.start(site) == 0
    assert _saved(whole_run.data) == [ONE_FILE, TWO_FILE]
    assert [login["email"] for login in site.logins] == list(USERS)
    assert site.paths.count("/sign-out") == 2
    assert site.user is None
    assert whole_run.notes == [{"title": "buildium-statements", "message": "PASS"}]


def test_private_values_stay_out_of_the_run_folder(whole_run):
    assert whole_run.start(FakeSite()) == 0
    written = _written(whole_run.data)
    for private in (SITE.login_url, ONE, "ONELLC", "FakeSystem", "one@example.invalid",
                    "password-one@example.invalid"):
        assert private not in written
    assert "entity1" in written


def test_a_second_run_overwrites_the_first(whole_run, pdf_of):
    assert whole_run.start(FakeSite()) == 0
    target = whole_run.data / "data" / "statements" / "2026-10" / ONE_FILE
    target.write_bytes(b"an older copy")
    assert whole_run.start(FakeSite()) == 0
    assert target.read_bytes().startswith(b"%PDF")
    assert _saved(whole_run.data) == [ONE_FILE, TWO_FILE]


def test_mfa_sends_a_push_naming_the_abbreviation_and_saves_a_screenshot(whole_run):
    site = FakeSite(mfa={"one@example.invalid"})
    assert whole_run.start(site) == 1
    assert whole_run.notes[0] == {"title": "buildium-statements",
                                  "message": "ONELLC: MFA link needed"}
    assert whole_run.notes[-1]["message"] == "FAIL: MFA needed"
    run_dir = _run_dir(whole_run.data)
    assert (run_dir / "fail_01.png").exists()
    assert MFA in (run_dir / "fail_01_page.txt").read_text(encoding="utf-8")
    # The push reaches a person's phone, but the abbreviation stays out of the run folder
    assert "ONELLC" not in _written(whole_run.data)


def test_the_config_sets_the_words_of_every_push(whole_run):
    with (whole_run.data / "config.toml").open("a", encoding="utf-8") as config:
        config.write('''
[notifications.mfa_needed]
title = "{abbreviation} needs you"
message = "Open the link for {key}"
[notifications.mfa_failed]
message = "{failed} of {total} failed"
''')
    assert whole_run.start(FakeSite(mfa={"one@example.invalid"})) == 1
    assert whole_run.notes[0] == {"title": "ONELLC needs you",
                                  "message": "Open the link for entity1"}
    assert whole_run.notes[-1]["title"] == "buildium-statements"
    assert whole_run.notes[-1]["message"].endswith("failed")


def test_an_mfa_failure_doesnt_stop_the_next_entity(whole_run):
    site = FakeSite(mfa={"one@example.invalid"})
    assert whole_run.start(site) == 1
    assert _saved(whole_run.data) == [TWO_FILE]
    assert "not processed: sign-in failed" in _report(whole_run.data)


def test_a_wrong_password_saves_a_screenshot_and_no_trace(whole_run):
    assert whole_run.start(FakeSite(reject=True)) == 1
    run_dir = _run_dir(whole_run.data)
    assert (run_dir / "fail_01.png").exists()
    assert (run_dir / "fail_02.png").exists()  # each entity's own
    assert not list(run_dir.glob("trace*"))  # a trace must never see the sign-in


def test_another_entitys_statement_is_never_saved_under_this_ones_name(whole_run):
    # The risk of sharing a browser: the site gives entity2 what entity1 should have
    site = FakeSite(pdf_for=lambda user: "/two.pdf")
    assert whole_run.start(site) == 1
    assert _saved(whole_run.data) == [TWO_FILE]
    report = _report(whole_run.data)
    assert "check: StatementError: the statement is for a different owner" in report
    run_dir = _run_dir(whole_run.data)
    assert (run_dir / "fail_01.png").exists()
    assert (run_dir / "downloads" / "entity1.pdf").exists()  # kept for a look
    assert (run_dir / "trace_entity1.zip").exists()


def test_a_bad_statement_doesnt_replace_a_good_one(whole_run, pdf_of):
    assert whole_run.start(FakeSite()) == 0
    target = whole_run.data / "data" / "statements" / "2026-10" / ONE_FILE
    good = target.read_bytes()
    files = {"/one.pdf": pdf_of(owner=ONE, period="9/1/2026 - 9/30/2026"),
             "/two.pdf": pdf_of(owner=TWO)}
    assert whole_run.start(FakeSite(), files=files) == 1
    assert target.read_bytes() == good
    assert "not the month ending 2026-10-31" in _report(whole_run.data)


def test_a_pdf_that_never_arrives_saves_a_screenshot_and_continues(whole_run, pdf_of):
    assert whole_run.start(FakeSite(), files={"/two.pdf": pdf_of(owner=TWO)}) == 1
    assert _saved(whole_run.data) == [TWO_FILE]
    assert (_run_dir(whole_run.data) / "fail_01.png").exists()
    assert "no PDF arrived" in _report(whole_run.data)


def test_a_failed_sign_out_stops_the_rest_and_saves_a_screenshot(whole_run):
    # It can't tell who's still signed in, so nothing more is done in this browser
    site = FakeSite(sign_out_works=False)
    assert whole_run.start(site) == 1
    assert _saved(whole_run.data) == [ONE_FILE]
    assert len(site.logins) == 1
    report = _report(whole_run.data)
    assert "sign-out: entity1" in report
    assert "not processed: sign-out failed" in report
    assert (_run_dir(whole_run.data) / "fail_01.png").exists()


def test_a_missing_credential_fails_only_that_entity(whole_run, monkeypatch):
    real = keyring.get_credential
    monkeypatch.setattr(keyring, "get_credential", lambda name, user: (
        None if name == "buildium/entity1" else real(name, user)))
    assert whole_run.start(FakeSite()) == 1
    assert _saved(whole_run.data) == [TWO_FILE]
    assert "no buildium/entity1 in Credential Manager" in _report(whole_run.data)


def test_entity_runs_only_that_one(whole_run):
    site = FakeSite()
    assert whole_run.start(site, "--entity", "entity2") == 0
    assert _saved(whole_run.data) == [TWO_FILE]
    assert [login["email"] for login in site.logins] == ["two@example.invalid"]


def test_an_unknown_entity_fails_without_opening_a_browser(whole_run):
    site = FakeSite()
    assert whole_run.start(site, "--entity", "nobody") == 1
    assert site.paths == []
    assert "no entity with this key" in _report(whole_run.data)


def test_dump_saves_structure_and_nothing_under_data(whole_run):
    assert whole_run.start(FakeSite(), "--dump") == 0
    run_dir = _run_dir(whole_run.data)
    for step in ("login", "ledger", "statement_menu", "account_menu"):
        assert (run_dir / f"dump_entity1_{step}_page.txt").exists()
        assert (run_dir / f"dump_entity1_{step}.html").exists()
    assert not (whole_run.data / "data").exists()
    assert "would save" in _report(whole_run.data)
    assert "password-one@example.invalid" not in _written(whole_run.data)


def test_capture_saves_snapshots_and_types_nothing(whole_run, monkeypatch):
    monkeypatch.setattr(capture_module, "CAPTURE_MINUTES", 0.05)
    monkeypatch.setattr(capture_module, "POLL_MS", 100)
    site = FakeSite()
    assert whole_run.start(site, "--capture") == 0
    run_dir = _run_dir(whole_run.data)
    assert (run_dir / "capture_001_page.txt").exists()
    assert site.logins == []
    assert not list(run_dir.glob("trace*"))


# ---------------------------------------------- buildium-gl

def _gl_row(number, memo):
    return {"Id": str(number), "PostingId": str(number + 100), "EntryDate": "03/15/2026",
            "Amount": "-10.50", "JournalMemo": memo, "GLAccountName": "Rent",
            "GLAccountNameRaw": "Rent", "GLAccountTypeName": "Income",
            "JournalCodeDescription": "Charge"}


def _gl_files(gl_csv_of):
    return {f"/{user}_{method}.csv": gl_csv_of(rows=[_gl_row(number, f"{user}{method}")])
            for number, user in enumerate(("one", "two"), start=1)
            for method in ("Cash", "Accrual")}


def _gl_run(whole_run, site, gl_csv_of, *args, files=None):
    files = _gl_files(gl_csv_of) if files is None else files
    return whole_run.start(site, "--dump", *args, files=files, command=gl_main.main)


class FakeBook:
    """Stands in for the spreadsheet, and keeps every write by tab."""

    def __init__(self, missing_tab=None):
        self.writes = {}
        self.missing_tab = missing_tab

    def open(self, spreadsheet_id, tab, masker):
        masker.add(spreadsheet_id)  # what open_worksheet does
        if tab == self.missing_tab:
            raise SheetError("spreadsheet not found")
        book = self

        class Tab:
            def replace_block(self, start_cell, rows, date_columns=(), trim=False):
                book.writes[tab] = SimpleNamespace(start_cell=start_cell, rows=rows,
                                                   date_columns=date_columns, trim=trim)

        return Tab()


def _gl_write_run(whole_run, site, gl_csv_of, *args, files=None, book=None):
    book = book or FakeBook()
    files = _gl_files(gl_csv_of) if files is None else files

    def command(argv, **options):
        return gl_main.main(argv, open_ws=book.open, **options)

    return whole_run.start(site, *args, files=files, command=command), book


def test_a_sheet_that_wont_open_stops_the_run_before_the_site(whole_run, gl_csv_of):
    site = FakeSite()
    code, book = _gl_write_run(whole_run, site, gl_csv_of, book=FakeBook("FakeMasterTab"))
    assert code == 1
    assert site.logins == []  # nothing was signed in to or exported
    assert book.writes == {}
    report = _report(whole_run.data)
    assert "FAILED    sheet check: master" in report
    assert "not started, so nothing was exported" in report


def test_a_run_that_doesnt_write_skips_the_sheet_check(whole_run, gl_csv_of):
    site = FakeSite()
    code, _ = _gl_write_run(whole_run, site, gl_csv_of, "--entity", "entity1",
                            book=FakeBook("FakeMasterTab"))
    assert code == 0
    assert "sheet check" not in _report(whole_run.data)


def test_the_report_address_comes_from_the_sign_in_host():
    assert report_url(f"{SITE.login_url}?x=1") == f"{HOST}{GL_REPORT}"


def test_date_forms_cover_the_ways_a_box_can_show_a_date():
    assert date_forms(dt.date(1999, 1, 5)) == ("1/5/1999", "01/05/1999")


def _gl_page(open_site, gl_csv_of, monkeypatch, site=None):
    monkeypatch.setattr(gl_fetch, "PAGE_TIMEOUT_MS", 2_000)
    monkeypatch.setattr(gl_fetch, "DOWNLOAD_TIMEOUT_MS", 1_500)
    page = open_site(site or FakeSite(), _gl_files(gl_csv_of))
    sign_in(page, SITE, CRED, FAST)
    return page


def test_each_method_saves_its_own_export_for_the_whole_date_range(
        open_site, pdf_server, gl_csv_of, tmp_path, monkeypatch):
    page = _gl_page(open_site, gl_csv_of, monkeypatch)
    open_report(page, SITE, TODAY)
    for method in ("Cash", "Accrual"):
        export_method(page, method, tmp_path / f"{method}.csv")
    assert b"oneCash" in (tmp_path / "Cash.csv").read_bytes()
    assert b"oneAccrual" in (tmp_path / "Accrual.csv").read_bytes()
    assert pdf_server.fetches == ["/one_Cash.csv?from=1/1/1999&to=11/5/2026",
                                  "/one_Accrual.csv?from=1/1/1999&to=11/5/2026"]


def test_a_date_box_that_rewrites_the_date_still_works(open_site, pdf_server, gl_csv_of, tmp_path,
                                                       monkeypatch):
    page = _gl_page(open_site, gl_csv_of, monkeypatch, FakeSite(pad_dates=True))
    open_report(page, SITE, TODAY)
    export_method(page, "Cash", tmp_path / "Cash.csv")
    assert pdf_server.fetches == ["/one_Cash.csv?from=01/01/1999&to=11/05/2026"]


def test_a_dump_saves_every_export_with_a_profile_and_signs_out(whole_run, gl_csv_of):
    site = FakeSite()
    assert _gl_run(whole_run, site, gl_csv_of) == 0
    run_dir = _run_dir(whole_run.data)
    for key in ("entity1", "entity2"):
        for method in ("Cash", "Accrual"):
            assert (run_dir / "downloads" / f"{key}_{method}.csv").stat().st_size > 0
            assert "columns: 53" in (run_dir / f"gl_profile_{key}_{method}.txt").read_text(
                encoding="utf-8")
    for step in ("report", "dates", "export_menu_cash", "export_menu_accrual"):
        assert (run_dir / f"dump_entity1_{step}_page.txt").exists()
    assert site.paths.count("/sign-out") == 2
    assert whole_run.notes == [{"title": "buildium-gl", "message": "PASS"}]
    assert not (whole_run.data / "data").exists()


def test_the_log_report_and_profiles_hold_no_private_values(whole_run, gl_csv_of):
    # Unlike these, a dump's page copies and the raw exports do hold real values
    assert _gl_run(whole_run, FakeSite(), gl_csv_of) == 0
    run_dir = _run_dir(whole_run.data)
    safe = [run_dir / "run.log", run_dir / "report.txt", run_dir / "summary.json",
            *run_dir.glob("gl_profile_*.txt")]
    written = "".join(path.read_text(encoding="utf-8") for path in safe)
    for private in (SITE.login_url, ONE, "ONELLC", "one@example.invalid", "oneCash",
                    "made-up-spreadsheet-id", "FakeMasterTab", "FakeTabOne", "FAKECODE1"):
        assert private not in written
    assert "entity1 Cash" in written


def test_it_doesnt_need_the_statements_section(whole_run, gl_csv_of):
    config = whole_run.data / "config.toml"
    config.write_text(config.read_text(encoding="utf-8").split("[statements]")[0],
                      encoding="utf-8")
    assert _gl_run(whole_run, FakeSite(), gl_csv_of) == 0


def test_a_missing_export_stops_the_run_and_saves_evidence(whole_run, gl_csv_of):
    # The sheet needs every entity, so there's no point in signing the next one in
    site = FakeSite()
    files = _gl_files(gl_csv_of)
    del files["/one_Accrual.csv"]
    assert _gl_run(whole_run, site, gl_csv_of, files=files) == 1
    assert len(site.logins) == 1
    assert site.user is None
    report = _report(whole_run.data)
    assert "Accrual: FetchError: no Accrual export" in report
    assert "export: entity2  (not processed: entity1 failed)" in report
    run_dir = _run_dir(whole_run.data)
    assert (run_dir / "fail_01.png").exists()
    assert (run_dir / "trace_entity1.zip").exists()
    assert whole_run.notes[-1] == {"title": "buildium-gl", "message": "FAIL"}


def test_a_failed_sign_in_stops_the_run(whole_run, gl_csv_of):
    site = FakeSite(reject=True)
    assert _gl_run(whole_run, site, gl_csv_of) == 1
    assert len(site.logins) == 1
    report = _report(whole_run.data)
    assert "login: entity1" in report
    assert "export: entity2  (not processed: entity1 failed)" in report


def test_a_report_that_wont_open_stops_the_run_and_saves_evidence(whole_run, gl_csv_of,
                                                                  monkeypatch):
    monkeypatch.setattr(gl_fetch, "REPORT_PATH", "/manager/app/moved")
    site = FakeSite()
    assert _gl_run(whole_run, site, gl_csv_of) == 1
    assert len(site.logins) == 1
    assert site.user is None  # still signed out
    run_dir = _run_dir(whole_run.data)
    assert (run_dir / "fail_01.png").exists()
    assert (run_dir / "trace_entity1.zip").exists()
    assert "report: FetchError: no date range menu" in _report(whole_run.data)


def test_mfa_failing_says_so(whole_run, gl_csv_of):
    assert _gl_run(whole_run, FakeSite(mfa=True), gl_csv_of) == 1
    assert whole_run.notes[0]["message"] == "ONELLC: MFA link needed"
    assert whole_run.notes[-1] == {"title": "buildium-gl", "message": "FAIL: MFA needed"}


def test_entity_exports_only_that_one(whole_run, gl_csv_of):
    site = FakeSite()
    assert _gl_run(whole_run, site, gl_csv_of, "--entity", "entity2") == 0
    assert [login["email"] for login in site.logins] == ["two@example.invalid"]


def test_a_normal_run_replaces_the_master_after_every_entity_worked(whole_run, gl_csv_of):
    site = FakeSite()
    code, book = _gl_write_run(whole_run, site, gl_csv_of)
    assert code == 0
    assert site.paths.count("/sign-out") == 2
    assert set(book.writes) == {"FakeTabTwo", "FakeMasterTab"}  # only entity2 turns its tab on

    two = book.writes["FakeTabTwo"]
    assert (two.start_cell, two.trim) == ("C9", False)
    assert two.rows[0][3] == "twoCash"
    assert two.rows[0][5] == Decimal("10.50")  # the view's sign is flipped

    master = book.writes["FakeMasterTab"]
    assert (master.start_cell, master.trim) == ("A2", True)
    assert [row[:2] for row in master.rows] == [["FAKECODE1", "Cash"], ["FAKECODE1", "Accrual"],
                                                ["FAKECODE2", "Cash"], ["FAKECODE2", "Accrual"]]
    assert all(len(row) == 56 for row in master.rows)
    assert master.rows[0][-1].text == "=LEFT(INDEX(F:F, ROW()), 7)"
    assert Decimal("-10.50") in master.rows[0]  # the master keeps the export's sign

    assert whole_run.notes == [{"title": "buildium-gl", "message": "PASS"}]
    report = _report(whole_run.data)
    assert "read 1, kept 1, no Id 0, unit fixes 0, repeated pairs 0" in report
    assert "kept 1; removed: none" in report
    assert "wrote 4 rows" in report
    # Raw exports stay, and only a --dump adds page copies and profiles
    run_dir = _run_dir(whole_run.data)
    assert (run_dir / "downloads" / "entity2_Accrual.csv").exists()
    assert not list(run_dir.glob("gl_profile_*"))
    assert not list(run_dir.glob("dump_*"))


def test_one_entitys_failed_export_means_nothing_is_written(whole_run, gl_csv_of):
    files = _gl_files(gl_csv_of)
    del files["/two_Accrual.csv"]
    code, book = _gl_write_run(whole_run, FakeSite(), gl_csv_of, files=files)
    assert code == 1
    assert book.writes == {}
    assert whole_run.notes[-1] == {"title": "buildium-gl", "message": "FAIL"}


def test_a_changed_column_fails_by_name_and_writes_nothing(whole_run, gl_csv_of):
    files = _gl_files(gl_csv_of)
    columns = ["BrandNew" if c == "Amount" else c for c in EXPECTED_COLUMNS]
    files["/two_Cash.csv"] = gl_csv_of(rows=[], columns=columns)
    code, book = _gl_write_run(whole_run, FakeSite(), gl_csv_of, files=files)
    assert code == 1
    assert book.writes == {}
    assert "missing Amount; added BrandNew" in _report(whole_run.data)


def test_a_bad_first_entity_stops_before_the_next_one_signs_in(whole_run, gl_csv_of):
    files = _gl_files(gl_csv_of)
    files["/one_Cash.csv"] = gl_csv_of(rows=[])
    site = FakeSite()
    code, book = _gl_write_run(whole_run, site, gl_csv_of, files=files)
    assert code == 1
    assert len(site.logins) == 1
    assert site.user is None
    assert book.writes == {}
    assert "the export has no entries" in _report(whole_run.data)
    assert "export: entity2  (not processed: entity1 failed)" in _report(whole_run.data)


def test_a_failed_sign_out_means_nothing_is_written(whole_run, gl_csv_of):
    code, book = _gl_write_run(whole_run, FakeSite(sign_out_works=False), gl_csv_of)
    assert code == 1
    assert book.writes == {}
    assert "sign-out: entity1" in _report(whole_run.data)


def test_entries_in_two_entities_exports_mean_nothing_is_written(whole_run, gl_csv_of):
    files = _gl_files(gl_csv_of)
    files["/two_Cash.csv"] = files["/one_Cash.csv"]
    code, book = _gl_write_run(whole_run, FakeSite(), gl_csv_of, files=files)
    assert code == 1
    assert book.writes == {}
    assert "1 Cash entries are in both exports" in _report(whole_run.data)


def test_one_entity_never_writes(whole_run, gl_csv_of):
    site = FakeSite()
    code, book = _gl_write_run(whole_run, site, gl_csv_of, "--entity", "entity2")
    assert code == 0
    assert [login["email"] for login in site.logins] == ["two@example.invalid"]
    assert book.writes == {}
    assert "not written" in _report(whole_run.data)


def test_a_dump_never_writes(whole_run, gl_csv_of):
    code, book = _gl_write_run(whole_run, FakeSite(), gl_csv_of, "--dump")
    assert code == 0
    assert book.writes == {}
    assert "would write 4 rows" in _report(whole_run.data)


def test_a_config_with_no_gl_section_fails_clearly(whole_run, gl_csv_of):
    config = whole_run.data / "config.toml"
    text = config.read_text(encoding="utf-8")
    config.write_text(text.split("[gl]")[0], encoding="utf-8")
    code, book = _gl_write_run(whole_run, FakeSite(), gl_csv_of)
    assert code == 1
    assert book.writes == {}
    assert "a [gl] section is required" in _report(whole_run.data)
