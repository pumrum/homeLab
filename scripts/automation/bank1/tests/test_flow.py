"""Sign-in, verification, statements pages, and whole runs, in real Edge against FakeBank.

FakeBank mirrors what's known of the real site: a sign-in form in an iframe whose fields sit
in shadow roots, hidden copies of controls, a device-verification page, a dashboard, and one
statements page per account in three kinds. Playwright answers its requests from here, and
the PDFs come from a server on 127.0.0.1, so nothing leaves this machine. These run on
machines with Edge, and are skipped elsewhere. Headless Edge treats PDFs differently, so
also run them in visible windows:

    $env:AUTOMATION_TEST_HEADED = "1"; uv run pytest bank1/tests/test_flow.py
"""

import http.server
import json
import os
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

import keyring
import pytest
from playwright.sync_api import Error as PwError
from playwright.sync_api import sync_playwright

from automation_common.secrets import Credential
from bank1_portal import capture as capture_module
from bank1_portal import login as login_module
from bank1_portal import session, statements_fetch
from bank1_portal.config import Site
from bank1_portal.login import LoginError, Timeouts, VerificationTimeout, sign_in, sign_out
from bank1_portal.main import main

HOST = "https://bank.example.invalid"
LOGIN = "/logon"
DASHBOARD = "/web/dashboard"
VERIFY = "/web/verify"
SITE = Site(f"{HOST}{LOGIN}")
USER = "fake-user"
PASSWORD = "made-up-password"
CRED = Credential(USER, PASSWORD)
HEADED = bool(os.environ.get("AUTOMATION_TEST_HEADED"))
FAST = Timeouts(field=2_000, after_submit=3_000, choice=1_500, verify=2_000, dashboard=1_000,
                sign_out=1_500, poll=100)

# id: (kind, page name, suffix, rows as (date shown, file on the PDF server))
ACCOUNTS = {
    "chk1": ("checking", "FakeChecking", "9871",
             [("Sep 30, 2026", "chk1-sep"), ("Oct 31, 2026", "chk1-oct"),
              ("Aug 31, 2026", "chk1-aug")]),
    "card1": ("card", "FakeCard", "8972",
              [("Oct 1, 2026 - Oct 27, 2026", "card1-oct"),
               ("Sep 1, 2026 - Sep 27, 2026", "card1-sep")]),
    "inv1": ("investment", "FakeInvest", "7983",
             [("10/31/2026", "inv1-oct"), ("09/30/2026", "inv1-sep")]),
}

COMPONENTS = """<script>
customElements.define('mds-text-input', class extends HTMLElement {
  connectedCallback() {
    const input = document.createElement('input');
    input.id = this.id + '-input-field';
    input.type = this.getAttribute('type') || 'text';
    this.attachShadow({mode: 'open'}).appendChild(input);
  }
});
customElements.define('mds-account-header', class extends HTMLElement {
  connectedCallback() {
    this.attachShadow({mode: 'open'}).innerHTML = `<h2>${this.getAttribute('name')}</h2>`;
  }
});
customElements.define('mds-list-item', class extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: 'open'}).innerHTML = '<li><slot></slot></li>'; }
});
</script>"""

LOGIN_PAGE = """<h1>Welcome</h1><p>Get our mobile app</p>
<iframe id="logonbox" src="/logon-frame" style="width:500px;height:300px"></iframe>"""

LOGON_FRAME = COMPONENTS + """
<input type="password" style="display:none">
<mds-text-input id="userId"></mds-text-input>
<mds-text-input id="password" type="password"></mds-text-input>
<button id="signin-button" onclick="go()">Sign in</button>
__CHALLENGE__
<script>
  const value = (id) => document.getElementById(id).shadowRoot.querySelector('input').value;
  async function go() {
    const r = await fetch('/api/login', {method: 'POST',
      body: JSON.stringify({username: value('userId'), password: value('password')})});
    const answer = await r.json();
    if (answer.go) top.location = answer.go;
  }
</script>"""

CHALLENGE = "<iframe title='recaptcha challenge expires in two minutes' src='about:blank'></iframe>"

SIGN_OUT = "<button onclick=\"__SIGN_OUT__\">Sign out</button>"

STATEMENTS_PAGE = COMPONENTS + """__SIGN_OUT_BUTTON__
<mds-account-header name="__NAME__ (...__SUFFIX__)"></mds-account-header>
__TABS__
<mds-list-item style="display:none">Hidden copy Dec 31, 2026 <button>Download</button>
</mds-list-item>
__ROWS__
<div id="dialog"></div>
<script>
  const KIND = '__KIND__', BASE = '__BASE__';
  function choose(el, group, attr) {
    document.querySelectorAll(`[data-group=${group}]`).forEach(o => o.setAttribute(attr, 'false'));
    el.setAttribute(attr, 'true');
  }
  function single() {
    const tab = document.querySelector('[data-group=mode][aria-selected=true]');
    return tab ? tab.dataset.value === 'single' : true;
  }
  function download(file) {
    if (KIND === 'investment') {
      location = single() ? `${BASE}/${file}.pdf` : `${BASE}/${file}-all.zip`;
    } else if (KIND === 'checking') {
      document.getElementById('dialog').innerHTML = `<div role="dialog">__CHOICES__
        <button onclick="get('${file}')">Download</button></div>`;
    } else {
      location = `${BASE}/${file}.pdf`;
    }
  }
  function get(file) {
    const picked = document.querySelector('[data-group=format][aria-checked=true]');
    const format = picked ? picked.dataset.value : 'none';
    location = `${BASE}/${file}-${format}.pdf`;
  }
</script>"""

TABS = """<div role="tablist">
<div role="tab" data-group="mode" data-value="single" aria-selected="false"
     onclick="choose(this, 'mode', 'aria-selected')">Single statement</div>
<div role="tab" data-group="mode" data-value="multiple" aria-selected="true"
     onclick="choose(this, 'mode', 'aria-selected')">Multiple statements</div></div>"""

FORMAT_CHOICES = """
<div role="radio" data-group="format" data-value="standard" aria-checked="false"
     onclick="choose(this, 'format', 'aria-checked')">Standard PDF</div>
<div role="radio" data-group="format" data-value="accessible" aria-checked="true"
     onclick="choose(this, 'format', 'aria-checked')">Accessible PDF</div>"""


class PdfServer:
    """Serves files over a real connection on 127.0.0.1, as attachments."""

    def __init__(self):
        self.files = {}
        self.fetches = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlsplit(self.path).path
                owner.fetches.append(path)
                body = owner.files.get(path, b"not found")
                self.send_response(200 if path in owner.files else 404)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", 'attachment; filename="statement.pdf"')
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


# After the push option, as on the real page: Next and Cancel are mds-buttons whose shadow root
# holds the button, its label, and a hidden copy of the label. A hidden copy of each sits in a
# mobile-only footer. Next sends the push
NEXT_BUTTON = """
<mds-button id="next-content" data-label="Next" data-go="/web/verify/sent"></mds-button>
<div style="display:none"><mds-button id="next-button" data-label="Next" data-go="/web/verify/sent">
</mds-button></div>"""
NEXT_PAGE = """
<h1>We'll send a notification to your device</h1>
<p>Select Next to get a push notification on your phone.</p>
__NEXT__
<mds-button id="cancel-content" data-label="Cancel" data-go="/web/verify"></mds-button>
<script>
customElements.define("mds-button", class extends HTMLElement {
  connectedCallback() {
    const label = this.dataset.label;
    const root = this.attachShadow({mode: "open"});
    root.innerHTML = `<button class="button"><span class="button__label">${label}</span>` +
      `<span class="accessible-text" style="display:none">${label}</span></button>`;
    root.querySelector("button").onclick = () => { location = this.dataset.go; };
  }
});
</script>"""


class FakeBank:
    """One login, answered by Playwright."""

    def __init__(self, signed_in=False, verify=False, approve=False, reject=False,
                 challenge=False, sign_out_works=True, expired=(), show=None,
                 no_format_choice=False, chooser="push",
                 next_button=True):
        self.signed_in = signed_in
        self.verify = verify  # the site asks to verify the device
        self.approve = approve  # the person approves it on the phone
        self.reject = reject
        self.challenge = challenge
        self.sign_out_works = sign_out_works
        self.expired = set(expired)  # account ids whose links now go to the dashboard
        self.show = show or {}  # account id: the name its page shows instead
        self.no_format_choice = no_format_choice
        # The verification page: "push" shows the real chooser, "other" a chooser without the
        # push option, None skips the chooser because the site sent the push itself
        self.chooser = chooser
        self.next_button = next_button  # the page after the push option has a Next button
        self.pushes = 0
        self.app_sign_ins = 0
        self.logins = []
        self.paths = []
        self.server = None

    def _html(self, route, body):
        route.fulfill(status=200, content_type="text/html", body=f"<!doctype html>{body}")

    def _go(self, route, path):
        self._html(route, f"<script>location.replace('{HOST}{path}')</script>")

    def _sign_out_button(self):
        return SIGN_OUT.replace("__SIGN_OUT__",
                                "location='/logoff'" if self.sign_out_works else "")

    def _statements(self, route, account_id):
        kind, name, suffix, rows = ACCOUNTS[account_id]
        items = "\n".join(f"<mds-list-item><span>Statement {date}</span> "
                          f"<button onclick=\"download('{file}')\">Download</button>"
                          f"</mds-list-item>" for date, file in rows)
        page = (STATEMENTS_PAGE.replace("__SIGN_OUT_BUTTON__", self._sign_out_button())
                .replace("__NAME__", self.show.get(account_id, name))
                .replace("__SUFFIX__", suffix).replace("__KIND__", kind)
                .replace("__TABS__", TABS if kind == "investment" else "")
                .replace("__ROWS__", items).replace("__BASE__", self.server.base)
                .replace("__CHOICES__", "" if self.no_format_choice else FORMAT_CHOICES))
        return self._html(route, page)

    def handle(self, route):
        request = route.request
        if self.server and request.url.startswith(self.server.base):
            return route.continue_()
        if request.url == "about:blank":
            return route.continue_()
        path = urlsplit(request.url).path
        self.paths.append(path)
        if path == "/api/login":
            return self._api_login(route, json.loads(request.post_data))
        if path == "/logoff":
            self.signed_in = False
            return self._html(route, "<h1>You're signed out</h1>")
        if path == LOGIN:
            return self._go(route, DASHBOARD) if self.signed_in else self._html(route, LOGIN_PAGE)
        if path == "/logon-frame":
            return self._html(route, LOGON_FRAME.replace("__CHALLENGE__",
                                                         CHALLENGE if self.challenge else ""))
        if path == VERIFY:
            return self._html(route, self._chooser() if self.chooser else self._sent())
        if path == VERIFY + "/push":
            return self._html(route, NEXT_PAGE.replace(
                "__NEXT__", NEXT_BUTTON if self.next_button else ""))
        if path == VERIFY + "/sent":
            self.pushes += 1
            return self._html(route, self._sent())
        if path == VERIFY + "/app":
            self.app_sign_ins += 1
            return self._html(route, "<h1>Open our mobile app</h1>")
        if path.startswith("/web/"):
            if not self.signed_in:
                return self._go(route, LOGIN)
            account_id = path.rsplit("/", 1)[-1]
            if path.startswith("/web/statements/") and account_id not in self.expired:
                return self._statements(route, account_id)
            if path != DASHBOARD:
                return self._go(route, DASHBOARD)
            return self._html(route, f"{self._sign_out_button()}<h1>Accounts</h1>")
        return self._html(route, "<h1>Other</h1>")

    def _sent(self):
        approve = f"<script>setTimeout(() => location = '{DASHBOARD}', 700)</script>"
        if self.approve:
            self.signed_in = True
        return ("<h1>Let's make sure it's you</h1><p>We sent a notification to your mobile "
                "app.</p>" + (approve if self.approve else ""))

    def _chooser(self):
        push = ("Confirm using our mobile app" if self.chooser == "push"
                else "Get a text message")
        # As on the real page: each option is a list item in mds-list's shadow root, its text
        # in a label that does nothing when clicked, beside a link holding only an arrow icon,
        # named by its aria-label. A hidden mds-list-item holds the same words
        options = "".join(
            f"<li><span class='accessible-text' style='position:absolute;clip:rect(0 0 0 0)'>"
            f", {name}, {about}</span><label>{name}</label><span>{about}</span>"
            f"<a href='{VERIFY}/{to}' aria-label='{name}. {about}'><span>&gt;</span></a></li>"
            for name, about, to in [
                (push, "We will send a push notification to your device.", "push"),
                ("Sign into our mobile app", "Open our mobile app on your device.", "app"),
            ])
        return ("<h1>Let's make sure it's you</h1><p>For your security, we need to confirm "
                "your identity. Choose a confirmation method to continue.</p>"
                "<mds-list id='optionsList'></mds-list>"
                f"<mds-list-item id='inAppSend' hidden>{push}</mds-list-item>"
                "<a href='#'>Need help? Call us</a>"
                "<script>document.getElementById('optionsList').attachShadow({mode: 'open'})"
                f".innerHTML = \"<ul>{options}</ul>\"</script>")

    def _api_login(self, route, fields):
        self.logins.append(fields)
        answer = {}
        if not self.reject and fields == {"username": USER, "password": PASSWORD}:
            answer = {"go": VERIFY if self.verify else DASHBOARD}
            self.signed_in = not self.verify
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(answer))

    def use(self, server, pdfs):
        self.server = server
        server.files = pdfs


@pytest.fixture(scope="session")
def edge_installed():
    with sync_playwright() as pw:
        try:
            pw.chromium.launch(channel="msedge", headless=not HEADED).close()
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
        edge = pw.chromium.launch(channel="msedge", headless=not HEADED)

        def opened(site):
            ctx = edge.new_context(accept_downloads=True)
            site.use(pdf_server, {})
            ctx.route("**/*", site.handle)
            ctx.set_default_timeout(3_000)
            return ctx.new_page()
        yield opened
        edge.close()


# ---------------------------------------------- sign-in

def test_a_sign_in_reaches_into_the_frame_and_shadow_roots_and_sends_once(open_site):
    site = FakeBank()
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    assert urlsplit(page.url).path == DASHBOARD
    assert site.logins == [{"username": USER, "password": PASSWORD}]


def test_mobile_app_words_on_the_sign_in_page_never_count_as_verification(open_site):
    asked = []
    sign_in(open_site(FakeBank()), SITE, CRED, FAST, on_verify=lambda: asked.append(1))
    assert asked == []


def test_a_profile_still_signed_in_types_nothing(open_site):
    site = FakeBank(signed_in=True)
    sign_in(open_site(site), SITE, CRED, FAST)
    assert site.logins == []


def test_a_rejected_password_is_sent_once_and_stays_out_of_the_message(open_site):
    site = FakeBank(reject=True)
    with pytest.raises(LoginError, match="did not accept") as e:
        sign_in(open_site(site), SITE, CRED, FAST)
    assert len(site.logins) == 1
    assert PASSWORD not in str(e.value)


def test_a_visible_challenge_fails_without_sending_anything(open_site):
    site = FakeBank(challenge=True)
    with pytest.raises(LoginError, match="CAPTCHA"):
        sign_in(open_site(site), SITE, CRED, FAST)
    assert site.logins == []


def test_verification_notifies_once_and_continues_when_approved(open_site):
    asked = []
    site = FakeBank(verify=True, approve=True)
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST, on_verify=lambda: asked.append(1))
    assert asked == [1]
    assert (site.pushes, site.app_sign_ins) == (1, 0)
    assert urlsplit(page.url).path == DASHBOARD


def test_no_next_button_after_the_push_option_fails_without_waiting(open_site):
    site = FakeBank(verify=True, approve=True, next_button=False)
    asked = []
    with pytest.raises(LoginError, match="no Next button"):
        sign_in(open_site(site), SITE, CRED, FAST, on_verify=lambda: asked.append(1))
    assert asked == []
    assert (site.pushes, site.app_sign_ins) == (0, 0)


def test_verification_without_a_chooser_just_waits(open_site):
    asked = []
    page = open_site(FakeBank(verify=True, approve=True, chooser=None))
    sign_in(page, SITE, CRED, FAST, on_verify=lambda: asked.append(1))
    assert asked == [1]
    assert urlsplit(page.url).path == DASHBOARD


def test_a_chooser_without_the_push_option_fails_without_choosing(open_site):
    site = FakeBank(verify=True, approve=True, chooser="other")
    asked = []
    with pytest.raises(LoginError, match="no 'Confirm using our mobile app' option"):
        sign_in(open_site(site), SITE, CRED, FAST, on_verify=lambda: asked.append(1))
    assert asked == []
    assert (site.pushes, site.app_sign_ins) == (0, 0)


def test_verification_times_out_after_notifying(open_site):
    asked = []
    with pytest.raises(VerificationTimeout, match="verification timed out"):
        sign_in(open_site(FakeBank(verify=True)), SITE, CRED, FAST,
                on_verify=lambda: asked.append(1))
    assert asked == [1]


def test_sign_out_clicks_sign_out_and_waits_for_the_page_to_move(open_site):
    site = FakeBank()
    page = open_site(site)
    sign_in(page, SITE, CRED, FAST)
    sign_out(page, FAST)
    assert site.signed_in is False


# ---------------------------------------------- whole runs

CONFIG = f"""
[site]
login_url = "{HOST}{LOGIN}"

[[accounts]]
label = "entity1_checking"
kind = "checking"
statements_url = "{HOST}/web/statements/chk1"
suffix = "9871"

[[accounts]]
label = "personal_card_1"
kind = "card"
statements_url = "{HOST}/web/statements/card1"
suffix = "8972"
match = "FakeCard (...8972)"

[[accounts]]
label = "personal_invest_1"
kind = "investment"
statements_url = "{HOST}/web/statements/inv1"
suffix = "7983"
"""

CHK = "entity1_checking"
CARD = "personal_card_1"
INV = "personal_invest_1"


@pytest.fixture
def whole_run(needs_edge, pdf_server, tmp_path, monkeypatch, pdf_of):
    """Everything real but the network, the window, and the stored credential."""
    def launch(site):
        def launch_edge(pw, profile):
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), channel="msedge", headless=not HEADED,
                accept_downloads=True)
            ctx.route("**/*", site.handle)
            ctx.set_default_timeout(3_000)
            return ctx
        return launch_edge

    notes = []
    good = {
        "/chk1-oct-standard.pdf": pdf_of(suffix="9871", closing="10/31/2026"),
        "/chk1-oct-accessible.pdf": pdf_of(suffix="9871", closing="10/31/2026"),
        "/card1-oct.pdf": pdf_of(suffix="8972", opening="Oct 1, 2026", closing="Oct 27, 2026"),
        "/inv1-oct.pdf": pdf_of(suffix="7983", closing="October 31, 2026"),
    }

    def start(site, *args, files=None):
        site.use(pdf_server, good if files is None else good | files)
        monkeypatch.setattr(session, "launch_edge", launch(site))
        monkeypatch.setattr(capture_module, "launch_edge", launch(site))
        monkeypatch.setattr(login_module, "Timeouts", lambda: FAST)
        monkeypatch.setattr(statements_fetch, "PAGE_TIMEOUT_MS", 2_000)
        monkeypatch.setattr(statements_fetch, "STEP_TIMEOUT_MS", 700)
        monkeypatch.setattr(statements_fetch, "DOWNLOAD_TIMEOUT_MS", 1_500)
        return main(list(args), notifier=lambda note, masker: notes.append(note) or True)

    def credentials(name, user):
        if name != "bank1/login":
            return None
        return SimpleNamespace(username=USER, password=PASSWORD)

    monkeypatch.setenv("AUTOMATION_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(keyring, "get_credential", credentials)
    data = tmp_path / "bank1"
    data.mkdir()
    (data / "config.toml").write_text(CONFIG, encoding="utf-8")
    return SimpleNamespace(start=start, data=data, notes=notes, server=pdf_server)


def _run_dir(data):
    return sorted((data / "runs").iterdir())[-1]


def _report(data):
    return (_run_dir(data) / "report.txt").read_text(encoding="utf-8")


def _written(data):
    found = ""
    for path in (data / "runs").rglob("*"):
        if path.suffix in (".log", ".txt", ".json"):
            found += path.read_text(encoding="utf-8")
    return found


def _saved(data):
    root = data / "data" / "statements"
    if not root.exists():
        return []
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.pdf"))


EVERY_FILE = [f"{CHK}/{CHK}_2026-10.pdf", f"{CARD}/{CARD}_2026-10.pdf",
              f"{INV}/{INV}_2026-10.pdf"]


def test_a_normal_run_saves_each_accounts_newest_statement_and_signs_out(whole_run):
    site = FakeBank()
    assert whole_run.start(site) == 0
    assert _saved(whole_run.data) == EVERY_FILE
    assert site.logins == [{"username": USER, "password": PASSWORD}]
    assert site.signed_in is False
    assert whole_run.notes == [{"title": "bank1-statements", "message": "PASS"}]


def test_each_kind_takes_its_own_step(whole_run):
    assert whole_run.start(FakeBank()) == 0
    fetched = whole_run.server.fetches
    assert "/chk1-oct-standard.pdf" in fetched  # the standard PDF, not the accessible one
    assert "/chk1-oct-accessible.pdf" not in fetched
    assert "/inv1-oct.pdf" in fetched  # a single statement, not all of them
    assert not any(path.endswith(".zip") for path in fetched)
    assert "/card1-oct.pdf" in fetched


def test_private_values_stay_out_of_the_run_folder(whole_run):
    # The suffixes are made-up values that can't appear in a date or time
    assert whole_run.start(FakeBank()) == 0
    written = _written(whole_run.data)
    for private in (SITE.login_url, "/web/statements/", "9871", "8972", "7983", "FakeCard",
                    USER, PASSWORD):
        assert private not in written
    assert CHK in written


def test_a_second_run_overwrites_the_first(whole_run, pdf_of):
    assert whole_run.start(FakeBank()) == 0
    target = whole_run.data / "data" / "statements" / CARD / f"{CARD}_2026-10.pdf"
    newer = pdf_of(suffix="8972", closing="Oct 27, 2026", pad=7_000)
    assert whole_run.start(FakeBank(), files={"/card1-oct.pdf": newer}) == 0
    assert target.read_bytes() == newer


def test_verification_sends_a_push_and_the_run_continues_once_approved(whole_run):
    site = FakeBank(verify=True, approve=True)
    assert whole_run.start(site) == 0
    assert (site.pushes, site.app_sign_ins) == (1, 0)
    assert whole_run.notes[0] == {"title": "bank1-statements",
                                  "message": "Approve the sign-in on your phone"}
    assert _saved(whole_run.data) == EVERY_FILE


def test_a_verification_timeout_stops_everything_and_keeps_the_evidence(whole_run, pdf_of):
    target = whole_run.data / "data" / "statements" / CHK / f"{CHK}_2026-10.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"the good file from last month's run")
    site = FakeBank(verify=True)
    assert whole_run.start(site) == 1
    assert target.read_bytes() == b"the good file from last month's run"
    assert _saved(whole_run.data) == [f"{CHK}/{CHK}_2026-10.pdf"]
    assert not any(p.startswith("/web/statements/") for p in site.paths)
    assert whole_run.notes[-1] == {"title": "bank1-statements",
                                   "message": "FAIL: verification timed out"}
    report = _report(whole_run.data)
    assert "verification timed out" in report
    assert report.count("not attempted: login failed") == 3
    run_dir = _run_dir(whole_run.data)
    assert (run_dir / "fail_01.png").exists()
    assert "make sure it" in (run_dir / "fail_01_page.txt").read_text(encoding="utf-8")
    assert not (run_dir / "trace.zip").exists()  # never a trace near a sign-in


def test_a_wrong_password_fails_the_run_without_a_trace(whole_run):
    site = FakeBank(reject=True)
    assert whole_run.start(site) == 1
    assert len(site.logins) == 1
    assert "did not accept" in _report(whole_run.data)
    assert whole_run.notes[-1]["message"] == "FAIL"
    assert not (_run_dir(whole_run.data) / "trace.zip").exists()


def test_a_missing_credential_fails_before_typing(whole_run, monkeypatch):
    monkeypatch.setattr(keyring, "get_credential", lambda name, user: None)
    site = FakeBank()
    assert whole_run.start(site) == 1
    assert "bank1/login" in _report(whole_run.data)
    assert site.logins == []


def test_an_expired_link_fails_only_that_account_and_says_so(whole_run):
    assert whole_run.start(FakeBank(expired={"card1"})) == 1
    report = _report(whole_run.data)
    assert "statements link no longer valid: landed on the dashboard" in report
    assert _saved(whole_run.data) == [EVERY_FILE[0], EVERY_FILE[2]]
    assert (_run_dir(whole_run.data) / "trace.zip").exists()


def test_a_page_for_another_account_is_never_downloaded(whole_run):
    site = FakeBank(show={"card1": "FakeSavings"})  # the card's match names FakeCard
    assert whole_run.start(site) == 1
    assert "the page doesn't show this account" in _report(whole_run.data)
    assert "/card1-oct.pdf" not in whole_run.server.fetches


def test_a_pdf_for_another_account_never_replaces_a_good_file(whole_run, pdf_of):
    assert whole_run.start(FakeBank()) == 0
    target = whole_run.data / "data" / "statements" / INV / f"{INV}_2026-10.pdf"
    good = target.read_bytes()
    wrong = {"/inv1-oct.pdf": pdf_of(suffix="9999", closing="October 31, 2026")}
    assert whole_run.start(FakeBank(), files=wrong) == 1
    assert target.read_bytes() == good
    assert "check: StatementError: the PDF doesn't show this account's suffix" in _report(
        whole_run.data)
    assert (_run_dir(whole_run.data) / "downloads" / f"{INV}.pdf").exists()


def test_an_error_page_instead_of_a_pdf_fails_only_that_account(whole_run):
    assert whole_run.start(FakeBank(), files={"/card1-oct.pdf": b"<html>Try later</html>"}) == 1
    assert "download: PdfCaptureError" in _report(whole_run.data)
    assert _saved(whole_run.data) == [EVERY_FILE[0], EVERY_FILE[2]]


def test_a_missing_format_choice_fails_clearly(whole_run):
    assert whole_run.start(FakeBank(no_format_choice=True)) == 1
    assert "expected a standard PDF choice" in _report(whole_run.data)
    assert _saved(whole_run.data) == EVERY_FILE[1:]


def test_a_failed_sign_out_is_only_a_warning(whole_run):
    assert whole_run.start(FakeBank(sign_out_works=False)) == 0
    log = (_run_dir(whole_run.data) / "run.log").read_text(encoding="utf-8")
    assert "sign-out failed, which doesn't fail the run" in log
    assert whole_run.notes[-1]["message"] == "PASS"


def test_account_runs_only_that_one(whole_run):
    assert whole_run.start(FakeBank(), "--account", CARD) == 0
    assert _saved(whole_run.data) == [EVERY_FILE[1]]


def test_an_unknown_account_fails_without_opening_a_browser(whole_run):
    site = FakeBank()
    assert whole_run.start(site, "--account", "nope") == 1
    assert site.paths == []


def test_dump_saves_one_page_of_each_kind_and_downloads_nothing(whole_run):
    assert whole_run.start(FakeBank()) == 0  # a file already saved must be left alone
    before = _saved(whole_run.data)
    whole_run.server.fetches.clear()
    assert whole_run.start(FakeBank(), "--dump") == 0
    run_dir = _run_dir(whole_run.data)
    for name in ("site_login", "site_dashboard", f"{CHK}_statements", f"{CARD}_statements",
                 f"{INV}_statements"):
        assert (run_dir / f"dump_{name}_page.txt").exists()
        assert (run_dir / f"dump_{name}.png").exists()
    assert whole_run.server.fetches == []
    assert _saved(whole_run.data) == before
    assert f"would save {CHK}_2026-10.pdf" in _report(whole_run.data)
    assert PASSWORD not in _written(whole_run.data)


def test_dump_still_saves_a_page_it_cant_read(whole_run):
    assert whole_run.start(FakeBank(expired={"inv1"}), "--dump", "--account", INV) == 1
    assert (_run_dir(whole_run.data) / f"dump_{INV}_statements_page.txt").exists()


def test_capture_saves_snapshots_and_types_nothing(whole_run, monkeypatch):
    monkeypatch.setattr(capture_module, "CAPTURE_MINUTES", 0.05)
    monkeypatch.setattr(capture_module, "POLL_MS", 100)
    site = FakeBank()
    assert whole_run.start(site, "--capture") == 0
    run_dir = _run_dir(whole_run.data)
    assert (run_dir / "capture_001_page.txt").exists()
    assert site.logins == []
    assert not list(run_dir.glob("trace*"))
