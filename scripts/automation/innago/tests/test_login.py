"""The sign-in flow in real Edge against a fake site.

Playwright answers every request from here, so nothing reaches a network. It runs on
machines with Edge, and is skipped elsewhere. The classify tests run everywhere.
"""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import keyring
import pytest
from playwright.sync_api import Error as PwError
from playwright.sync_api import sync_playwright

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
LIST_PAGE = """<table><tbody>
<tr><td>Unit 1001</td><td><button onclick="location='/download/1'">Download</button></td></tr>
<tr><td>Unit 1002</td><td><a href="/download/2">Download</a></td></tr>
<tr style="display:none"><td>hidden copy</td><td><button>Download</button></td></tr>
<tr><td>A note without a download button</td></tr>
</tbody></table><button onclick="location='/pay'">Make Payment</button>"""


class FakeSite:
    """A hosted sign-in and an app, answered by Playwright. Records what it's sent."""

    def __init__(self, signed_in=False, after_password=None, password_ok=True,
                 home="<h1>Dashboard</h1>", pdfs=()):
        self.signed_in = signed_in
        self.after_password = after_password  # the path shown between password and app
        self.password_ok = password_ok
        self.home = home
        self.pdfs = list(pdfs)
        self.posts = []
        self.get_paths = []

    def _html(self, route, body):
        route.fulfill(status=200, content_type="text/html", body=f"<!doctype html>{body}")

    def _redirect(self, route, url):
        # Not a 302: Edge would follow a faked one with a real DNS lookup, bypassing the route
        self._html(route, f"<script>location.replace('{url}')</script>")

    def handle(self, route):
        request = route.request
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
        if path == "/invoices":
            self._html(route, LIST_PAGE)
        elif path.startswith("/download/"):
            route.fulfill(
                status=200, content_type="application/pdf", body=self.pdfs[int(path[-1]) - 1],
                headers={"content-disposition": 'attachment; filename="0a1b2c3d-random.pdf"'})
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
def open_site(browser):
    contexts = []

    def opened(site):
        ctx = browser.new_context(accept_downloads=True)
        contexts.append(ctx)
        ctx.route("**/*", site.handle)
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


def test_the_invoice_list_downloads_rows_and_ignores_the_site_file_name(
        open_site, pdf_of, tmp_path):
    pdfs = [pdf_of(unit="1001"), pdf_of(unit="1002")]
    site = FakeSite(signed_in=True, pdfs=pdfs)
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    source = InvoiceList(page, f"{APP}/invoices")
    assert source.open() == 2  # not the hidden copy, the note, or the payment button
    for index, expected in enumerate(pdfs):
        dest = tmp_path / f"invoice_{index}.pdf"
        source.download(index, dest)
        assert dest.read_bytes() == expected
    assert "/pay" not in site.get_paths


# ---------------------------------------------- whole runs, with a real browser profile

CONFIG = f"""[site]
login_url = "{SITE.login_url}"
app_url = "{SITE.app_url}"
[invoices]
page_url = "{APP}/invoices"
units = ["1001", "1002"]
"""


@pytest.fixture
def whole_run(needs_edge, tmp_path, monkeypatch):
    """Everything real but the network, the window, and the stored credential."""
    def launch(site):
        def launch_edge(pw, profile):
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), channel="msedge", headless=True, accept_downloads=True)
            ctx.route("**/*", site.handle)
            ctx.set_default_timeout(3_000)
            return ctx
        return launch_edge

    def start(site, *args):
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
    pdfs[1] = pdf_of(unit="1001", number="90002222")  # a second one for 1001, none for 1002
    assert whole_run.start(FakeSite(signed_in=True, pdfs=pdfs)) == 1
    run_dir = _run_dirs(whole_run.data)[0]
    report = (run_dir / "report.txt").read_text(encoding="utf-8")
    assert "second invoice" in report
    assert "no usable invoice" in report
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
