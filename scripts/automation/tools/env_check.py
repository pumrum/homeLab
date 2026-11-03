"""
Environment check for the automations.

Confirms a machine can run them WITHOUT touching any real site. It serves
FakeBank, a small fake bank site on this machine only (127.0.0.1) that copies
the quirks found on a real bank site:
  - sign-in fields inside an iframe and inside shadow DOM
  - a "verify it's you" page that clears on its own after a few seconds
  - a drop-down with a hidden duplicate option and a click-through wrapper
  - date boxes that ignore pasted values and only accept real keystrokes
  - a Download button that does nothing unless the dates were really entered

Checks, in order:
  Python and Playwright versions          Edge launch (sandbox on)
  Edge policies (Windows registry)        Automatic sign-in (Windows registry)
  Credential Manager round trip (keyring) FakeBank: sign-in to parsed download
  --lock     lock Windows, then repeat the FakeBank download while locked

Output goes to <AUTOMATION_DATA_ROOT>/env-check/runs/<yyyyMMdd_HHmmss>/
(default C:\\automation\\env-check\\...): report.txt plus evidence on failure.

Run from scripts/automation (workspace installed with `uv sync`):
    uv run python tools/env_check.py
    uv run python tools/env_check.py --lock
Standalone, from a repo clone before `uv sync` (finds ../common/src itself):
    uv run --no-project --with playwright --with keyring python env_check.py
"""

import argparse
import csv
import ctypes
import datetime as dt
import http.server
import platform
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Error as PwError
from playwright.sync_api import Page, sync_playwright

try:
    from automation_common import browser, config
except ImportError:  # standalone: use the shared code from the repo clone
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common" / "src"))
    from automation_common import browser, config

PROCESS = "env-check"

EDGE_POLICIES = {
    "HideFirstRunExperience": 1, "AutoImportAtFirstRun": 4,
    "HideRestoreDialogEnabled": 1, "StartupBoostEnabled": 0, "BackgroundModeEnabled": 0,
    "BrowserSignin": 0, "SyncDisabled": 1, "PasswordManagerEnabled": 0,
    "AutofillAddressEnabled": 0, "AutofillCreditCardEnabled": 0,
    "DefaultBrowserSettingEnabled": 0, "DefaultBrowserSettingsCampaignEnabled": 0,
    "PromotionalTabsEnabled": 0, "ShowRecommendationsEnabled": 0,
    "EdgeShoppingAssistantEnabled": 0, "HubsSidebarEnabled": 0, "TranslateEnabled": 0,
    "UserFeedbackAllowed": 0, "PromptForDownloadLocation": 0, "QuickViewOfficeFilesEnabled": 0,
    "NewTabPageContentEnabled": 0, "NewTabPageQuickLinksEnabled": 0,
    "NewTabPageHideDefaultTopSites": 1, "NewTabPageAllowedBackgroundTypes": 3,
}


class Report:
    """Step results, printed as they happen and written to report.txt."""

    def __init__(self, run_dir: Path, title: str) -> None:
        self.run_dir = run_dir
        self.title = title
        self.rows: list[tuple[str, str, str]] = []
        self.t0 = time.monotonic()
        self.failures = 0

    def add(self, step: str, status: str, note: str = "") -> None:
        secs = time.monotonic() - self.t0
        self.rows.append((step, status, note))
        print(f"[{secs:6.1f}s] {status:<5} {step}" + (f"  ({note})" if note else ""), flush=True)

    def write(self) -> None:
        lines = [self.title + "  " + dt.datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
        lines += [f"{s:<5} {step}" + (f"  ({n})" if n else "") for step, s, n in self.rows]
        fails = sum(1 for r in self.rows if r[1] == "FAIL")
        warns = sum(1 for r in self.rows if r[1] == "WARN")
        # INFO rows are observations; WARN rows need attention but don't block
        lines += ["", f"Result: {'VIABLE' if not fails else 'NEEDS WORK'}"
                  f"  ({fails} failed, {warns} warnings)"]
        (self.run_dir / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n" + "\n".join(lines))


def step(rep: Report, page: Page, name: str, action: Callable[[], str | None]) -> bool:
    """Run one step; on failure save screenshot + page outline and record FAIL."""
    try:
        note = action()
        rep.add(name, "PASS", note or "")
        return True
    except Exception as e:  # noqa: BLE001 - environment check: record anything
        rep.failures += 1
        browser.save_page_evidence(page, rep.run_dir, f"fail_{rep.failures:02d}")
        rep.add(name, "FAIL", f"{type(e).__name__}: {str(e).splitlines()[0][:150]}")
        return False


# ---------------------------------------------------------------- FakeBank
_HOST = """<script>function host(el,html){el.attachShadow({mode:'open'}).innerHTML=html;return el;}</script>"""
PAGES = {
    "/": """<html><body><h1>FakeBank (test only)</h1>
<iframe id="logonbox" src="/logon" width="600" height="300"></iframe></body></html>""",
    "/logon": _HOST + """<body>
<mds-text-input id="userId"></mds-text-input>
<mds-text-input-secure id="password"></mds-text-input-secure>
<mds-button id="signin-button"></mds-button>
<script>
host(document.getElementById('userId'),'<input id="userId-input-field" autocomplete="username">');
host(document.getElementById('password'),'<input id="password-input-field" type="password">');
host(document.getElementById('signin-button'),'<button type="submit">Sign in</button>');
document.getElementById('signin-button').shadowRoot.querySelector('button').onclick=()=>{
  const u=document.getElementById('userId').shadowRoot.querySelector('input').value;
  const p=document.getElementById('password').shadowRoot.querySelector('input').value;
  if(u==='testuser'&&p==='testpass') top.location='/verify';
};</script></body>""",
    "/verify": """<html><body><p>Let's make sure it's you. Check the FakeBank mobile app.</p>
<script>setTimeout(()=>location='/dashboard',4000)</script></body></html>""",
    "/dashboard": "<html><body><h1>Dashboard</h1></body></html>",
    "/download-page": _HOST + """<body><h2>Direct Download</h2>
<mds-select id="downloadActivityOptionId"></mds-select>
<div style="display:none"><mds-select-option>Choose a date range</mds-select-option></div>
<div id="opts"></div>
<div id="dates" style="display:none">
  <mds-datepicker id="accountActivityFromDate"></mds-datepicker>
  <mds-datepicker id="accountActivityToDate"></mds-datepicker></div>
<mds-button id="download"></mds-button>
<script>
const typed={from:'',to:''};
host(document.getElementById('downloadActivityOptionId'),'<button>Activity</button>');
document.getElementById('downloadActivityOptionId').shadowRoot.querySelector('button').onclick=()=>{
  const o=document.getElementById('opts');
  if(!o.shadowRoot) host(o,'<mds-select-option role="option" style="pointer-events:none"></mds-select-option>');
  const so=o.shadowRoot.querySelector('mds-select-option');
  if(!so.shadowRoot){ host(so,'<div style="pointer-events:auto">Choose a date range</div>');
    so.onclick=()=>{document.getElementById('dates').style.display='block'}; }
};
for (const [k,id] of [['from','accountActivityFromDate'],['to','accountActivityToDate']]) {
  const dp=host(document.getElementById(id),'<mds-text-input id="'+id+'-input"></mds-text-input>');
  const ti=host(dp.shadowRoot.querySelector('mds-text-input'),'<input id="'+id+'-input-input" placeholder="mm/dd/yyyy">');
  const box=ti.shadowRoot.querySelector('input');
  box.addEventListener('keydown',e=>{            // only real keystrokes count
    if(e.key.length===1) typed[k]+=e.key; else if(e.key==='Backspace') typed[k]=''; });
}
host(document.getElementById('download'),'<button>Download</button>');
document.getElementById('download').shadowRoot.querySelector('button').onclick=()=>{
  if(/^\\d\\d\\/\\d\\d\\/\\d{4}$/.test(typed.from)&&/^\\d\\d\\/\\d\\d\\/\\d{4}$/.test(typed.to))
    location='/activity.csv';                     // silently ignores pasted dates
};</script></body>""",
}
CSV = ("Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
       'DEBIT,10/30/2026,"TEST VENDOR, INC",-12.50,ACH_DEBIT,100.00,\n'
       "CREDIT,10/02/2026,TEST DEPOSIT,500.00,ACH_CREDIT,112.50,\n")


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/activity.csv":
            body, ctype = CSV.encode(), "text/csv"
            extra = {"Content-Disposition": 'attachment; filename="FakeBank_Activity.CSV"'}
        elif self.path in PAGES:
            body, ctype, extra = PAGES[self.path].encode(), "text/html", {}
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        pass


def start_fakebank() -> tuple[http.server.ThreadingHTTPServer, str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ---------------------------------------------------------------- selectors
SEL = {
    "username": ["#userId-input-field", "#userId input", "input[autocomplete='username']"],
    "password": ["#password-input-field", "#password input", "input[type='password']"],
    "signin": ["#signin-button button", "#signin-button", "button[type='submit']"],
    "activity_select": ["#downloadActivityOptionId button", "#downloadActivityOptionId"],
    "date_range_option": ["[role='option']:has-text('Choose a date range')",
                          "mds-select-option:has-text('Choose a date range')"],
    "from_date": ["#accountActivityFromDate-input-input", "#accountActivityFromDate-input input"],
    "to_date": ["#accountActivityToDate-input-input", "#accountActivityToDate-input input"],
    "download_btn": ["mds-button#download button", "#download button"],
}


def fakebank_flow(rep: Report, page: Page, base: str, label: str) -> None:
    """Sign in, verify, pick a date range, type dates, download, parse."""
    find_first = browser.find_first

    def sign_in() -> str:
        page.goto(base + "/", wait_until="domcontentloaded")
        scopes = [page.frame_locator("#logonbox"), page]
        u, us = find_first(scopes, SEL["username"], 10000)
        p, _ = find_first(scopes, SEL["password"], 5000)
        b, _ = find_first(scopes, SEL["signin"], 5000)
        if not (u and p and b):
            raise RuntimeError("sign-in fields not found")
        u.fill("testuser")
        p.fill("testpass")
        b.click()
        page.wait_for_url("**/verify", timeout=10000)
        page.wait_for_url("**/dashboard", timeout=30000)
        return f"verification page cleared; username via {us}"
    if not step(rep, page, f"{label}: sign in and pass verification", sign_in):
        return

    def choose_range() -> str:
        page.goto(base + "/download-page", wait_until="domcontentloaded")
        sel, _ = find_first([page], SEL["activity_select"], 10000)
        opened = lambda: find_first([page], SEL["date_range_option"], 3000)[0] is not None  # noqa: E731
        dates = lambda: find_first([page], SEL["from_date"], 3000)[0] is not None  # noqa: E731
        if not (sel and browser.click_until(sel, opened, "drop-down")):
            raise RuntimeError("drop-down did not open")
        opt, _ = find_first([page], SEL["date_range_option"], 5000)
        how = browser.click_until(opt, dates, "option") if opt else None
        if not how:
            raise RuntimeError("date range not selected")
        return how
    if not step(rep, page, f"{label}: pick 'date range' (hidden duplicate + wrapper)", choose_range):
        return

    start = (dt.date.today() - dt.timedelta(days=30)).strftime("%m/%d/%Y")
    today = dt.date.today().strftime("%m/%d/%Y")

    def dates() -> str:
        f, _ = find_first([page], SEL["from_date"], 5000)
        t, _ = find_first([page], SEL["to_date"], 5000)
        if not (f and t):
            raise RuntimeError("date boxes not found")
        return f"{browser.type_date(f, start)} to {browser.type_date(t, today)}"
    if not step(rep, page, f"{label}: type dates (keystrokes only)", dates):
        return

    saved: dict[str, Path] = {}

    def download() -> str:
        b, _ = find_first([page], SEL["download_btn"], 5000)
        if not b:
            raise RuntimeError("Download button not found")
        with page.expect_download(timeout=30000) as d:
            b.click()
        target = rep.run_dir / f"fakebank_{label.lower().replace(' ', '_')}.csv"
        d.value.save_as(str(target))
        saved["p"] = target
        return d.value.suggested_filename
    if not step(rep, page, f"{label}: download captured", download):
        return

    def parse() -> str:
        with saved["p"].open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if len(rows) != 2:
            raise RuntimeError(f"expected 2 rows, got {len(rows)}")
        return f"{len(rows)} rows, {len(rows[0])} columns"
    step(rep, page, f"{label}: parse CSV", parse)


def check_registry(rep: Report) -> None:
    if platform.system() != "Windows":
        rep.add("Edge policies", "INFO", "not Windows; skipped")
        rep.add("Automatic sign-in", "INFO", "not Windows; skipped")
        return
    import winreg
    missing, wrong = [], []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Edge")
        for name, want in EDGE_POLICIES.items():
            try:
                val, _ = winreg.QueryValueEx(key, name)
                if val != want:
                    wrong.append(f"{name}={val}")
            except FileNotFoundError:
                missing.append(name)
    except FileNotFoundError:
        missing = list(EDGE_POLICIES)
    if not missing and not wrong:
        rep.add("Edge policies", "PASS", f"all {len(EDGE_POLICIES)} set")
    else:
        rep.add("Edge policies", "WARN",
                f"{len(missing)} missing, {len(wrong)} different: " + ", ".join((missing + wrong)[:6])
                + ("..." if len(missing + wrong) > 6 else ""))
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon")
        on = str(winreg.QueryValueEx(key, "AutoAdminLogon")[0]) == "1"
        user = winreg.QueryValueEx(key, "DefaultUserName")[0] if on else ""
        rep.add("Automatic sign-in", "INFO",
                f"on for '{user}'" if on else "off (expected on the robot only)")
    except FileNotFoundError:
        rep.add("Automatic sign-in", "INFO", "off (expected on the robot only)")


def check_keyring(rep: Report) -> None:
    try:
        import keyring
    except ImportError:
        rep.add("Credential Manager round trip", "WARN",
                "keyring not installed: run with uv run --with playwright --with keyring")
        return
    # A throwaway test entry, deleted again; never a real secret
    name, user, val = "automation/env-check", "test", f"ok-{time.time():.0f}"
    try:
        keyring.set_password(name, user, val)
        got = keyring.get_password(name, user)
        keyring.delete_password(name, user)
        if got != val:
            raise RuntimeError("value read back did not match")
        rep.add("Credential Manager round trip", "PASS",
                f"write, read, delete via {type(keyring.get_keyring()).__name__}")
    except Exception as e:  # noqa: BLE001
        rep.add("Credential Manager round trip", "FAIL", type(e).__name__)


def wait_until_locked(timeout_s: float = 15) -> bool:
    """Wait for the lock to take effect: the input desktop can't be opened while locked."""
    user32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        desk = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not desk:
            return True
        user32.CloseDesktop(desk)
        time.sleep(0.2)  # polling a condition, not waiting a fixed time
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lock", action="store_true",
                    help="lock Windows, then repeat the download while locked")
    args = ap.parse_args()

    data_dir = config.process_dir(PROCESS)
    run_dir = data_dir / "runs" / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    rep = Report(run_dir, "Environment check")
    from importlib.metadata import version
    rep.add("Python and Playwright", "INFO",
            f"Python {platform.python_version()}, Playwright {version('playwright')}, "
            f"{platform.platform()}")
    check_registry(rep)
    check_keyring(rep)

    srv, base = start_fakebank()
    try:
        with sync_playwright() as pw:
            try:
                ctx = browser.launch_edge(pw, data_dir / "profiles" / "edge")
            except PwError as e:
                rep.add("Launch Edge (sandbox on)", "FAIL", str(e).splitlines()[0][:150])
                rep.write()
                return 1
            rep.add("Launch Edge (sandbox on)", "PASS", ctx.browser.version if ctx.browser else "")
            page = browser.first_page(ctx)
            try:
                fakebank_flow(rep, page, base, "FakeBank")
                if args.lock:
                    if platform.system() != "Windows":
                        rep.add("Lock Windows", "INFO", "not Windows; skipped")
                    elif ctypes.windll.user32.LockWorkStation() and wait_until_locked():
                        rep.add("Lock Windows", "PASS", "locked; repeating download unattended")
                        fakebank_flow(rep, page, base, "FakeBank locked")
                    else:
                        rep.add("Lock Windows", "FAIL", "screen did not lock")
            finally:
                ctx.close()
    finally:
        srv.shutdown()

    rep.write()
    print(f"\nFiles: {run_dir}")
    return 0 if all(r[1] in ("PASS", "INFO") for r in rep.rows) else 1


if __name__ == "__main__":
    sys.exit(main())
