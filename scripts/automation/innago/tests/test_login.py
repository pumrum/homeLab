"""The sign-in flow in real Edge against a fake site.

Playwright answers the site's requests from here, and the PDFs come from a server on
127.0.0.1, so nothing leaves this machine. It runs on machines with Edge, and is skipped
elsewhere. The classify tests run everywhere.
"""

import datetime as dt
import http.server
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import keyring
import pytest
from playwright.sync_api import Error as PwError
from playwright.sync_api import sync_playwright

from automation_common.browser import PdfCaptureError
from automation_common.secrets import Credential
from innago_portal import capture as capture_module
from innago_portal import invoices_fetch, session
from innago_portal import login as login_module
from innago_portal.config import Site
from innago_portal.invoices_fetch import InvoiceList
from innago_portal.login import LoginError, Step, Timeouts, classify, sign_in
from innago_portal.main import main

ID = "https://identify.example.invalid"
APP = "https://app.example.invalid"
SITE = Site(f"{ID}/login", f"{APP}/")
CRED = Credential("owner@example.invalid", "made-up-password")
FAST = Timeouts(total_s=8, field=2_000, after_submit=2_000, mfa=1_500, settle=300)


@pytest.mark.parametrize(("url", "step"), [
    (f"{APP}/dashboard", Step.SIGNED_IN),
    (f"{APP}/", Step.SIGNED_IN),
    (f"{APP}/login", Step.OTHER),
    (f"{ID}/u/login/identifier?state=abc", Step.EMAIL),
    (f"{ID}/u/login/password?state=abc", Step.PASSWORD),
    (f"{ID}/u/mfa-email-challenge?state=abc", Step.MFA),
    (f"{ID}/u/passkey-enrollment?state=abc", Step.PASSKEY),
    (f"{ID}/authorize", Step.OTHER),
    ("about:blank", Step.OTHER),
])
def test_classify(url, step):
    assert classify(url, SITE.app_url) is step


IDENTIFIER_PAGE = """<h1>Welcome</h1>
<form method="post"><input type="hidden" name="state" value="made-up">
<button type="submit" name="action" value="default" style="position:absolute;left:-9999px">
Continue</button>
<input id="username" name="username" type="email">
<input class="hide" type="password" style="display:none">
<button type="submit" name="action" value="default">Continue</button></form>
<form method="post" action="/google"><button type="submit">Continue with Google</button></form>"""
PASSWORD_PAGE = """<form method="post"><input id="password" name="password" type="password">
<button type="submit">Continue</button></form>"""
PASSKEY_PAGE = """<h1>Create a passkey for faster sign-in</h1>
<form method="post" action="/create"><button type="submit">Continue</button></form>
<a href="{after}">Remind me later</a>"""
STUCK_PASSKEY_PAGE = """<h1>Create a passkey</h1>
<form method="post" action="/create"><button type="submit">Continue</button></form>"""
MFA_PAGE = "<h1>Enter the code we sent you</h1><input name='code'>"
# Mirrors the real list and detail pages' structure, with made-up values
LIST_PAGE = """<div class="filter-group"><div class="simple-fake-select"
    onclick="document.getElementById('group-menu').style.display = 'block'">
  <p id="group-label">__GROUP_LABEL__</p></div>
  <ul id="group-menu" class="simple-fake-select-opt" style="display:none">
    <li><a href="#" onclick="chooseNotGrouped(); return false">Not Grouped</a></li>
    <li><a href="#">Grouped by Property</a></li></ul></div>
<in-icon id="filter-icon-tenant-list-mobile" style="display:inline-block"
  onclick="document.getElementById('filter').style.display = 'block'">filter</in-icon>
<div id="filter" style="display:none">
  <button class="btn btn-link" onclick="clearFilters()">Clear Filters</button>
  <div class="mb-3"><label>Date Range</label>
    <select id="range"><option>Week</option><option>Month</option></select></div>
  <div class="mb-3"><label>Select Month</label>
    <select id="month"><option>October</option><option>November</option></select></div>
  <div class="mb-3"><label>Select Year</label>
    <select id="year"><option>2025</option><option>2026</option></select></div>
  <div class="styled-checkbox"><input id="senton" type="checkbox" checked>
    <label for="senton">Sent On</label></div>
  <div class="styled-checkbox"><input id="dueon" type="checkbox">
    <label for="dueon">Due On</label></div>
  <div class="styled-checkbox"><input id="paidon" type="checkbox" checked>
    <label for="paidon">Paid On</label></div>
  <div class="styled-checkbox"><input id="disbursedon" type="checkbox">
    <label for="disbursedon">Disbursed On</label></div>
  <button class="btn btn-primary" onclick="applyFilters()">Apply Filters</button>
</div>
<span class="invoice-counter" id="counter"></span>
<table><tbody id="rows">__ROWS__</tbody></table>
<script>
  let grouped = __GROUPED__, filteredOut = __FILTERED_OUT__;
  const fixedCounter = "__COUNTER__";
  function refresh() {
    const rows = [...document.querySelectorAll('tr.income-table-row')];
    rows.forEach((row, i) => {
      row.style.display = grouped || (filteredOut && i > 0) ? 'none' : '';
    });
    const shown = filteredOut ? 1 : rows.length;
    document.getElementById('counter').textContent = fixedCounter || `${shown} of ${shown}`;
  }
  function chooseNotGrouped() {
    // Like the real site: the table reloads, reading "0 of 0" with no rows until it's back
    document.getElementById('group-label').textContent = 'Not Grouped';
    document.getElementById('group-menu').style.display = 'none';
    if (!fixedCounter) { document.getElementById('counter').textContent = '0 of 0'; }
    setTimeout(() => { grouped = false; refresh(); }, 400);
  }
  function clearFilters() {
    // The real panel's behavior here is unknown, so this takes the harder path: it closes
    document.getElementById('filter').style.display = 'none';
    window.filtersCleared = true;
  }
  function applyFilters() {
    const value = id => document.getElementById(id).value;
    const on = id => document.getElementById(id).checked;
    if (window.filtersCleared && value('range') === 'Month' && value('month') === 'November'
        && value('year') === '2026' && on('dueon') && !on('senton') && !on('paidon')
        && !on('disbursedon')) {
      filteredOut = false;
      window.filtersApplied = true;
    }
    document.getElementById('filter').style.display = 'none';
    refresh();
  }
  refresh();
</script>"""
LIST_ROW = """<tr class="income-table-row">
  <td class="dv-in-id"><p>90000{n}</p></td><td class="dv-in-unit"><p>100{n}</p></td></tr>"""
DETAIL_PAGE = """<div style="display:none">
  <button class="dropdown-item" onclick="location='/hidden-download'">Download</button>
  <button class="dropdown-item" onclick="location='/delete'">Delete</button></div>
<div id="pop" style="display:{pop_display};position:fixed;left:150px;top:150px;width:300px;
    height:200px;background:white;z-index:9"><a href="/edit">Edit Invoice</a></div>
<script>
  // A status badge's hover pop-up: open while the mouse stays parked, gone once it moves off
  document.addEventListener('mousemove', e => {{
    if (e.clientX < 150 || e.clientY < 150) {{
      document.getElementById('pop').style.display = 'none';
      window.popupClosed = true;
    }}
  }});
</script>
<div class="detail-sticky-header"><h3>Invoice: {heading}</h3></div>
<button class="btn btn-innago-primary" style="position:fixed;left:200px;top:200px"
  onclick="window.open('{base}/invoice-{n}.pdf')">Download</button>
<button onclick="location='/remind'">Remind</button>
<button onclick="location='/record-payment'">Record Payment</button>
<button onclick="location='/edit'">Edit Invoice</button>
<button onclick="location='/delete'">Delete</button>"""
OTHER_ACTIONS = ("/hidden-download", "/delete", "/remind", "/record-payment", "/edit")
SESSION_COOKIE = {"name": "session", "value": "made-up"}


class PdfServer:
    """Serves PDFs on 127.0.0.1 to a signed-in session only, as the real site's address does.

    A local server, not a Playwright route, so the PDFs come over a real connection.
    """

    def __init__(self):
        self.files = {}
        self.attachment = False
        self.single_use = False
        self.fetches = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.fetches.append(self.path)
                if SESSION_COOKIE["name"] not in self.headers.get("Cookie", ""):
                    return self._reply(401, b"sign in first")
                if self.path not in owner.files:
                    return self._reply(404, b"not found")
                if owner.single_use and owner.fetches.count(self.path) > 1:
                    return self._reply(403, b"link already used")
                return self._reply(200, owner.files[self.path])

            def _reply(self, status, body):
                kind = "application/pdf" if status == 200 else "text/plain"
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                if owner.attachment:
                    self.send_header("Content-Disposition", 'attachment; filename="0a1b.pdf"')
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
    """A hosted sign-in and an app, answered by Playwright. Records what it's sent."""

    def __init__(self, signed_in=False, after_password=None, password_ok=True,
                 home="<h1>Dashboard</h1>", pdfs=(), counter=None, grouped=True,
                 filtered_out=False, heading=None, hover_popup=False):
        self.signed_in = signed_in
        self.after_password = after_password  # the path shown between password and app
        self.password_ok = password_ok
        self.home = home
        self.pdfs = list(pdfs)
        self.counter = counter
        self.grouped = grouped
        self.filtered_out = filtered_out
        self.heading = heading
        self.hover_popup = hover_popup
        self.posts = []
        self.get_paths = []
        self.server = None

    def use(self, server):
        self.server = server
        server.files = {f"/invoice-{n}.pdf": pdf for n, pdf in enumerate(self.pdfs, start=1)}

    def _html(self, route, body):
        route.fulfill(status=200, content_type="text/html", body=f"<!doctype html>{body}")

    def _redirect(self, route, url):
        # Not a 302: Edge would follow a faked one with a real DNS lookup, bypassing the route
        self._html(route, f"<script>location.replace('{url}')</script>")

    def handle(self, route):
        request = route.request
        if self.server and request.url.startswith(self.server.base):
            return route.continue_()
        path = urlsplit(request.url).path
        self.get_paths.append(path)
        if request.method == "POST":
            self.posts.append((path, parse_qs(request.post_data or "")))
        if request.url.startswith(APP):
            return self._app(route, path)
        match (path, request.method):
            case ("/login", _):
                start = f"{ID}/u/login/identifier?state=a"
                self._redirect(route, f"{APP}/dashboard" if self.signed_in else start)
            case ("/u/login/identifier", "GET"):
                self._html(route, IDENTIFIER_PAGE)
            case ("/u/login/identifier", "POST"):
                self._redirect(route, f"{ID}/u/login/password?state=b")
            case ("/u/login/password", "GET"):
                self._html(route, PASSWORD_PAGE)
            case ("/u/login/password", "POST"):
                if not self.password_ok:
                    self._html(route, f"<p>Wrong email or password</p>{PASSWORD_PAGE}")
                else:
                    self._redirect(route, self.after_password or f"{APP}/dashboard")
            case ("/u/passkey-enrollment", _):
                self._html(route, PASSKEY_PAGE.format(after=f"{APP}/dashboard"))
            case ("/u/passkey-stuck", _):
                self._html(route, STUCK_PASSKEY_PAGE)
            case ("/u/mfa-email-challenge", _):
                self._html(route, MFA_PAGE)
            case _:
                self._html(route, "<h1>Other</h1>")

    def _app(self, route, path):
        count = len(self.pdfs)
        if path == "/invoices":
            # Highest unit first, so a list that wasn't sorted gets the wrong PDF in each test
            rows = "".join(LIST_ROW.format(n=n) for n in range(count, 0, -1))
            label = "Grouped by Property" if self.grouped else "Not Grouped"
            page = (LIST_PAGE.replace("__ROWS__", rows).replace("__GROUP_LABEL__", label)
                    .replace("__GROUPED__", "true" if self.grouped else "false")
                    .replace("__FILTERED_OUT__", "true" if self.filtered_out else "false")
                    .replace("__COUNTER__", self.counter or ""))
            self._html(route, page)
        elif "/detail/" in path:
            invoice_id = path.split("/detail/")[1].split("/")[0]
            self._html(route, DETAIL_PAGE.format(
                base=self.server.base, n=invoice_id[-1], heading=self.heading or invoice_id,
                pop_display="block" if self.hover_popup else "none"))
        else:
            self._html(route, self.home)


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
def browser(needs_edge):
    # Per test: Playwright can't be running here when a whole-run test starts its own
    with sync_playwright() as pw:
        edge = pw.chromium.launch(channel="msedge", headless=True)
        yield edge
        edge.close()


@pytest.fixture
def pdf_server():
    server = PdfServer()
    yield server
    server.close()


@pytest.fixture
def open_site(browser, pdf_server):
    contexts = []

    def opened(site, session_cookie=True):
        ctx = browser.new_context(accept_downloads=True)
        contexts.append(ctx)
        site.use(pdf_server)
        ctx.route("**/*", site.handle)
        if session_cookie:
            ctx.add_cookies([SESSION_COOKIE | {"url": pdf_server.base}])
        return ctx.new_page()
    yield opened
    for ctx in contexts:
        ctx.close()


def _sent_fields(site):
    return [fields for _, fields in site.posts]


def test_email_then_password_reaches_the_app(open_site):
    site = FakeSite()
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    assert page.url == f"{APP}/dashboard"
    assert _sent_fields(site)[0]["username"] == [CRED.username]
    assert _sent_fields(site)[1]["password"] == [CRED.password]
    assert "/google" not in site.get_paths


def test_a_remembered_profile_skips_the_forms(open_site):
    site = FakeSite(signed_in=True)
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    assert page.url == f"{APP}/dashboard"
    assert site.posts == []


def test_a_wrong_password_is_sent_only_once(open_site):
    site = FakeSite(password_ok=False)
    page = open_site(site)
    with pytest.raises(LoginError, match="password"):
        sign_in(page, SITE, CRED, FAST)
    assert sum("password" in fields for fields in _sent_fields(site)) == 1


def test_the_error_message_holds_no_query_or_secret(open_site):
    site = FakeSite(password_ok=False)
    with pytest.raises(LoginError) as caught:
        sign_in(open_site(site), SITE, CRED, FAST)
    assert "state=" not in str(caught.value)
    assert CRED.password not in str(caught.value)


def test_a_hosted_passkey_prompt_is_skipped_and_never_accepted(open_site):
    site = FakeSite(after_password=f"{ID}/u/passkey-enrollment?state=c")
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    assert page.url == f"{APP}/dashboard"
    assert "/create" not in site.get_paths


def test_a_passkey_prompt_with_no_way_to_skip_fails(open_site):
    site = FakeSite(after_password=f"{ID}/u/passkey-stuck?state=c")
    with pytest.raises(LoginError, match="skip the passkey"):
        sign_in(open_site(site), SITE, CRED, FAST)
    assert "/create" not in site.get_paths


def test_a_passkey_prompt_inside_the_app_is_skipped(open_site):
    prompt = """<div id="prompt"><h2>Add a passkey</h2>
    <button onclick="document.getElementById('prompt').remove()">Not now</button>
    <button onclick="location='/create'">Add passkey</button></div><h1>Dashboard</h1>"""
    site = FakeSite(home=prompt)
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    assert page.locator("#prompt").count() == 0
    assert "/create" not in site.get_paths


def test_a_page_mentioning_passkeys_with_no_prompt_still_signs_in(open_site):
    page = open_site(FakeSite(home="<a href='/settings'>Manage your passkeys</a>"))
    sign_in(page, SITE, CRED, FAST)
    assert page.url == f"{APP}/dashboard"


def test_mfa_waits_and_then_times_out(open_site):
    site = FakeSite(after_password=f"{ID}/u/mfa-email-challenge?state=c")
    with pytest.raises(LoginError, match="MFA was not completed"):
        sign_in(open_site(site), SITE, CRED, FAST)


def test_mfa_continues_once_someone_finishes_it(open_site):
    site = FakeSite(after_password=f"{ID}/u/mfa-email-challenge?state=c")
    page = open_site(site)
    page.add_init_script(f"""if (location.pathname.includes('mfa'))
        setTimeout(() => location.href = '{APP}/dashboard', 300);""")
    sign_in(page, SITE, CRED, FAST)
    assert page.url == f"{APP}/dashboard"


def test_a_captcha_fails_without_sending_anything(open_site, monkeypatch):
    site = FakeSite()
    original = site._html

    def with_captcha(route, body):
        original(route, f"{body}<iframe src='/recaptcha' width=300 height=80></iframe>")
    monkeypatch.setattr(site, "_html", with_captcha)
    with pytest.raises(LoginError, match="CAPTCHA"):
        sign_in(open_site(site), SITE, CRED, FAST)
    assert site.posts == []


def test_an_unknown_page_fails_instead_of_hanging(open_site):
    site = FakeSite(after_password=f"{ID}/u/something-new")
    with pytest.raises(LoginError, match="unknown page"):
        sign_in(open_site(site), SITE, CRED, FAST)


def test_the_observer_sees_each_step(open_site):
    steps = []
    sign_in(open_site(FakeSite()), SITE, CRED, FAST, steps.append)
    assert steps == [Step.EMAIL, Step.PASSWORD, Step.SIGNED_IN]


def _opened_list(open_site, pdfs, session_cookie=True, **site_options):
    site = FakeSite(signed_in=True, pdfs=pdfs, **site_options)
    page = open_site(site, session_cookie=session_cookie)
    sign_in(page, SITE, CRED, FAST)
    return site, page, InvoiceList(page, f"{APP}/invoices", len(pdfs))


def test_the_list_reads_every_invoice_by_unit_without_filtering(open_site, pdf_of):
    # The table reloads after Not Grouped, and that mustn't look like a short list
    _, page, source = _opened_list(open_site, [pdf_of(), pdf_of(), pdf_of()])
    assert source.open() == 3
    assert page.locator("#group-label").inner_text() == "Not Grouped"
    assert page.evaluate("window.filtersCleared") is None
    assert [source.label(index) for index in range(3)] == ["1001", "1002", "1003"]


def test_each_invoice_opens_at_its_own_address(open_site, pdf_of, tmp_path):
    _, page, source = _opened_list(open_site, [pdf_of(), pdf_of()])
    source.open()
    source.download(1, tmp_path / "invoice.pdf")
    assert page.url == f"{APP}/invoices/900002/detail/900002/false/false"


def test_the_address_is_built_with_or_without_a_trailing_slash():
    wanted = f"{APP}/list/42/detail/42/false/false"
    assert invoices_fetch.detail_url(f"{APP}/list", "42") == wanted
    assert invoices_fetch.detail_url(f"{APP}/list/", "42") == wanted


def test_a_mouse_left_over_a_status_badge_cannot_block_download(open_site, pdf_of, tmp_path):
    # This failed a real run: a pop-up opened on hover and covered the Download button
    pdfs = [pdf_of(), pdf_of()]
    _, page, source = _opened_list(open_site, pdfs, hover_popup=True)
    source.open()
    source.download(0, tmp_path / "invoice.pdf")
    assert (tmp_path / "invoice.pdf").read_bytes() == pdfs[0]
    assert page.evaluate("window.popupClosed") is True


def test_a_list_already_not_grouped_is_left_as_it_is(open_site, pdf_of):
    _, _, source = _opened_list(open_site, [pdf_of(), pdf_of()], grouped=False)
    assert source.open() == 2


def test_a_short_list_is_filtered_to_this_month_by_due_date(open_site, pdf_of):
    _, page, source = _opened_list(open_site, [pdf_of(), pdf_of()], filtered_out=True)
    assert source.open(today=dt.date(2026, 11, 5)) == 2
    assert page.evaluate("window.filtersApplied") is True  # only with every value right


def test_a_list_still_short_after_filtering_reports_what_it_has(open_site, pdf_of):
    # The wrong month for the fake site, so its filter doesn't bring the other row back
    _, _, source = _opened_list(open_site, [pdf_of(), pdf_of()], filtered_out=True)
    assert source.open(today=dt.date(2026, 10, 5)) == 1


def test_a_download_never_runs_until_the_heading_shows_the_invoice(
        open_site, pdf_server, pdf_of, tmp_path, monkeypatch):
    monkeypatch.setattr(invoices_fetch, "PAGE_TIMEOUT_MS", 1_000)
    _, _, source = _opened_list(open_site, [pdf_of(), pdf_of()], heading="a different invoice")
    source.open()
    with pytest.raises(AssertionError):
        source.download(0, tmp_path / "invoice.pdf")
    assert not [path for path in pdf_server.fetches if path.endswith(".pdf")]


@pytest.mark.parametrize("attachment", [False, True], ids=["opens in a new tab", "attachment"])
def test_the_invoice_list_saves_each_pdf_however_it_opens(
        open_site, pdf_server, pdf_of, tmp_path, attachment):
    pdf_server.attachment = attachment
    pdfs = [pdf_of(unit="1001"), pdf_of(unit="1002")]
    site, page, source = _opened_list(open_site, pdfs)
    assert source.open() == 2
    for index, expected in enumerate(pdfs):
        dest = tmp_path / f"invoice_{index}.pdf"
        source.download(index, dest)
        assert dest.read_bytes() == expected
        if not attachment:  # with one, Edge may open its downloads tab a moment later
            assert page.context.pages == [page]
    assert not set(OTHER_ACTIONS) & set(site.get_paths)  # only Download was ever clicked


def test_a_partial_list_is_refused(open_site, pdf_of):
    _, _, source = _opened_list(open_site, [pdf_of()], counter="1 of 7")
    with pytest.raises(invoices_fetch.FetchError, match="only 1 of 7"):
        source.open()


def test_a_row_count_that_never_matches_the_counter_fails(open_site, pdf_of, monkeypatch):
    monkeypatch.setattr(invoices_fetch, "SETTLE_TIMEOUT_MS", 1_000)
    _, _, source = _opened_list(open_site, [pdf_of()], counter="2 of 2")
    with pytest.raises(AssertionError):
        source.open()


def test_a_single_use_address_still_gives_the_pdf(open_site, pdf_server, pdf_of, tmp_path):
    pdf_server.single_use = True
    pdfs = [pdf_of(unit="1001"), pdf_of(unit="1002")]
    _, _, source = _opened_list(open_site, pdfs)
    source.open()
    source.download(0, tmp_path / "invoice.pdf")
    assert (tmp_path / "invoice.pdf").read_bytes() == pdfs[0]
    assert pdf_server.fetches.count("/invoice-1.pdf") == 1


@pytest.mark.parametrize("setup", [{"session_cookie": False}, {"not_a_pdf": True}],
                         ids=["no session", "not a PDF"])
def test_no_pdf_fails_clearly_and_keeps_its_address_private(
        open_site, pdf_server, pdf_of, tmp_path, monkeypatch, setup):
    monkeypatch.setattr(invoices_fetch, "DOWNLOAD_TIMEOUT_MS", 1_500)
    pdfs = [b"<html>a sign-in page</html>" if setup.get("not_a_pdf") else pdf_of(), pdf_of()]
    options = {"session_cookie": setup.get("session_cookie", True)}
    _, page, source = _opened_list(open_site, pdfs, **options)
    source.open()
    dest = tmp_path / "invoice.pdf"
    with pytest.raises(PdfCaptureError, match="no PDF arrived") as caught:
        source.download(0, dest)
    assert pdf_server.base not in str(caught.value)
    assert not dest.exists()
    assert page.context.pages == [page]


# ---------------------------------------------- whole runs, with a real browser profile

CONFIG = f"""[site]
login_url = "{SITE.login_url}"
app_url = "{SITE.app_url}"
[invoices]
page_url = "{APP}/invoices"
units = ["1001", "1002"]
"""


@pytest.fixture
def whole_run(needs_edge, pdf_server, tmp_path, monkeypatch):
    """Everything real but the network, the window, and the stored credential."""
    def launch(site):
        def launch_edge(pw, profile):
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), channel="msedge", headless=True, accept_downloads=True)
            ctx.route("**/*", site.handle)
            ctx.add_cookies([SESSION_COOKIE | {"url": pdf_server.base}])
            ctx.set_default_timeout(3_000)
            return ctx
        return launch_edge

    def start(site, *args):
        site.use(pdf_server)
        monkeypatch.setattr(session, "launch_edge", launch(site))
        monkeypatch.setattr(capture_module, "launch_edge", launch(site))
        monkeypatch.setattr(login_module, "Timeouts", lambda: FAST)
        monkeypatch.setattr(invoices_fetch, "PAGE_TIMEOUT_MS", 2_000)
        return main(list(args), notifier=lambda note, masker: True)

    monkeypatch.setenv("AUTOMATION_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(keyring, "get_credential", lambda name, user: SimpleNamespace(
        username=CRED.username, password=CRED.password))
    data = tmp_path / "innago"
    data.mkdir()
    (data / "config.toml").write_text(CONFIG, encoding="utf-8")
    return SimpleNamespace(start=start, data=data)


def _run_dirs(data):
    return sorted((data / "runs").iterdir())


def _everything_written(data):
    """Every readable run file, as text, to search for things that must not be there."""
    found = ""
    for path in (data / "runs").rglob("*"):
        if path.suffix in (".log", ".txt", ".json", ".html"):
            found += path.read_text(encoding="utf-8")
    return found


def _two_units(pdf_of, **values):
    return [pdf_of(unit=unit, number=f"9000{unit}", **values) for unit in ("1001", "1002")]


def test_a_normal_run_saves_and_a_second_run_finds_no_change(whole_run, pdf_of):
    assert whole_run.start(FakeSite(pdfs=_two_units(pdf_of))) == 0
    saved = whole_run.data / "data" / "invoices" / "2026-01"
    assert sorted(p.name for p in saved.iterdir()) == [
        "2026-01-01_1001_90001001-1.pdf", "2026-01-01_1002_90001002-1.pdf"]
    assert not (_run_dirs(whole_run.data)[0] / "trace.zip").exists()

    assert whole_run.start(FakeSite(signed_in=True, pdfs=_two_units(pdf_of))) == 0
    assert "UNCHANGED" in (_run_dirs(whole_run.data)[-1] / "report.txt").read_text(encoding="utf-8")
    assert len(list(saved.iterdir())) == 2


def test_a_wrong_password_saves_evidence_and_no_secret(whole_run, pdf_of):
    assert whole_run.start(FakeSite(password_ok=False)) == 1
    run_dir = _run_dirs(whole_run.data)[0]
    assert (run_dir / "fail_01.png").exists()
    assert (run_dir / "fail_01_page.txt").exists()
    assert not (run_dir / "trace.zip").exists()  # a trace must never see the sign-in
    written = _everything_written(whole_run.data)
    assert CRED.password not in written
    assert SITE.login_url not in written
    assert "not processed: sign-in failed" in written


def test_a_missing_invoice_fails_and_keeps_a_trace(whole_run, pdf_of):
    pdfs = _two_units(pdf_of)
    pdfs[1] = pdf_of(unit="1001", number="90002222")  # the row for 1002 gets 1001's PDF
    assert whole_run.start(FakeSite(signed_in=True, pdfs=pdfs)) == 1
    run_dir = _run_dirs(whole_run.data)[0]
    report = (run_dir / "report.txt").read_text(encoding="utf-8")
    assert "the list says unit 1002, but the PDF says 1001" in report
    assert (run_dir / "trace.zip").exists()


def test_dump_saves_structure_and_nothing_under_data(whole_run, pdf_of):
    assert whole_run.start(FakeSite(pdfs=_two_units(pdf_of)), "--dump") == 0
    run_dir = _run_dirs(whole_run.data)[0]
    assert (run_dir / "dump_login_01_page.txt").exists()
    assert (run_dir / "dump_invoices.html").exists()
    assert not (whole_run.data / "data").exists()
    report = (run_dir / "report.txt").read_text(encoding="utf-8")
    assert "would save 2026-01-01_1001_90001001-1.pdf" in report
    assert CRED.password not in _everything_written(whole_run.data)


def test_capture_saves_snapshots_and_types_nothing(whole_run, monkeypatch):
    monkeypatch.setattr(capture_module, "CAPTURE_MINUTES", 0.05)
    monkeypatch.setattr(capture_module, "POLL_MS", 100)
    site = FakeSite()
    assert whole_run.start(site, "--capture") == 0
    run_dir = _run_dirs(whole_run.data)[0]
    assert (run_dir / "capture_001_page.txt").exists()
    assert site.posts == []
    assert not (run_dir / "trace.zip").exists()
