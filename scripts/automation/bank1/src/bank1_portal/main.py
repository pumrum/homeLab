"""bank1-statements: sign in once, then save each account's newest PDF statement.

    uv run bank1-statements                   normal run: every account, saved under data
    uv run bank1-statements --account LABEL   only that account
    uv run bank1-statements --dump            sign in, then save the structure of one statements
                                              page of each kind; downloads nothing
    uv run bank1-statements --capture         by hand only: you sign in and click around while
                                              the page structure is saved to the run folder
"""

import argparse
import logging
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from automation_common.browser import first_page, save_page_evidence
from automation_common.config import ConfigError, process_dir
from automation_common.notify import send_notification
from automation_common.run import Notifier, Run, Status, Summary, pass_fail
from bank1_portal import PROCESS
from bank1_portal.capture import capture
from bank1_portal.config import NOTIFICATIONS, Account, Config, load_config
from bank1_portal.login import LoginError
from bank1_portal.parse import check_pdf_file, check_statement, pdf_text, statement_file_name
from bank1_portal.session import open_browser, profile_dir, signed_in_page
from bank1_portal.statements_fetch import (
    check_statements_page,
    fetch_statement,
    find_newest,
    go_to_statements,
)
from bank1_portal.store import save_statement, statement_path

COMMAND = "bank1-statements"
TIMED_OUT = "verification timed out"

log = logging.getLogger(__name__)

Observer = Callable[[str], None]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=COMMAND, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dump", action="store_true",
                      help="save one statements page of each kind; download nothing")
    mode.add_argument("--capture", action="store_true",
                      help="by hand only: save page structure while you sign in and click around")
    ap.add_argument("--account", metavar="LABEL", help="only this account's label from the config")
    return ap.parse_args(argv)


def why(e: Exception) -> str:
    """One short line for the summary. The run masks private values when it's written."""
    first_line = (str(e).splitlines() or [""])[0][:200]
    return f"{type(e).__name__}: {first_line}"


def final_event(summary: Summary) -> str:
    """Pass or fail, naming the verification when that's what stopped the run."""
    if any(item.stage == "login" and item.status is Status.FAILED and TIMED_OUT in item.detail
           for item in summary.items):
        return "mfa_failed"
    return pass_fail(summary)


def load_stage(run: Run, data_dir: Path) -> Config | None:
    try:
        cfg = load_config(data_dir / "config.toml")
    except ConfigError as e:
        run.summary.record("config", "config.toml", Status.FAILED, str(e))
        return None
    run.notifications = cfg.notifications
    # Private: never in logs. The labels stay readable, since the report needs them. A bare
    # suffix isn't masked: four digits would also blank out dates and times in every log.
    # It's never logged, and no message repeats it
    run.masker.add(cfg.site.login_url)
    for account in cfg.accounts:
        run.masker.add(account.statements_url)
        if account.match != account.suffix:
            run.masker.add(account.match)
    log.info("config OK: %d account(s): %s", len(cfg.accounts),
             ", ".join(account.label for account in cfg.accounts))
    return cfg


def chosen_accounts(run: Run, cfg: Config, label: str | None,
                    dump: bool) -> tuple[Account, ...]:
    """The accounts this run works on: one by label, the first of each kind, or all."""
    if label is not None:
        chosen = tuple(account for account in cfg.accounts if account.label == label)
        if not chosen:
            run.summary.record("config", label, Status.FAILED,
                               "no account with this label in the config")
        return chosen
    if dump:
        firsts: dict[str, Account] = {}
        for account in cfg.accounts:
            firsts.setdefault(account.kind, account)
        return tuple(firsts.values())
    return cfg.accounts


def dump_observer(run: Run, ctx: BrowserContext, name: str) -> Observer:
    """--dump: save a page's outline and screenshot at each named step. Best effort."""
    def observe(step: str) -> None:
        try:
            save_page_evidence(first_page(ctx), run.run_dir, f"dump_{name}_{step}")
            log.info("dump: saved the %s page for %s", step, name)
        except Exception as e:  # noqa: BLE001 - --dump's copies must never stop the run
            log.warning("dump: saving the %s page failed: %s", step, why(e))
    return observe


def _ignore(_: str) -> None:
    pass


def dump_stage(run: Run, page: Page, account: Account, observer: Observer) -> None:
    """Open the page and report what a run would pick. Clicks nothing, downloads nothing."""
    try:
        go_to_statements(page, account)
        try:
            rows = check_statements_page(page, account)
        finally:
            observer("statements")  # also when the list wasn't recognized
        newest = find_newest(rows)
    except Exception as e:  # noqa: BLE001 - one account's failure must not stop the others
        save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        run.summary.record("dump", account.label, Status.FAILED, why(e))
        return
    name = statement_file_name(account.label, newest.closing)
    run.summary.record("dump", account.label, Status.OK,
                       f"{len(rows)} statement(s), would save {name}")


def statement_stage(run: Run, page: Page, account: Account, data_dir: Path,
                    observer: Observer) -> None:
    """Download, check, and save one account's statement. Records the result; never raises."""
    download = run.run_dir / "downloads" / f"{account.label}.pdf"
    step = "download"
    try:
        newest = fetch_statement(page, account, download, observer)
        step = "check"
        check_pdf_file(download)
        check_statement(pdf_text(download), account.suffix, newest.closing)
        step = "save"
        name = statement_file_name(account.label, newest.closing)
        save_statement(download, statement_path(data_dir, account.label, name))
    except Exception as e:  # noqa: BLE001 - one account's failure must not stop the others
        save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        # A file that failed its checks stays in the run folder's downloads for a look
        run.summary.record("statement", account.label, Status.FAILED, f"{step}: {why(e)}")
        return
    note = f"; {newest.same_month} other(s) closed that month" if newest.same_month else ""
    run.summary.record("statement", account.label, Status.OK, f"saved {name}{note}")


def browser_stage(run: Run, cfg: Config, accounts: tuple[Account, ...], data_dir: Path,
                  dump: bool) -> None:
    """One browser and one sign-in for every account, one at a time."""
    remaining = list(accounts)
    stage, reason = "browser", ""
    try:
        with open_browser(data_dir) as ctx:
            observer = dump_observer(run, ctx, "site") if dump else _ignore
            with signed_in_page(run, ctx, cfg.site, observer=observer) as page:
                run.summary.record("login", "site", Status.OK)
                while remaining:
                    account = remaining.pop(0)
                    if dump:
                        dump_stage(run, page, account, dump_observer(run, ctx, account.label))
                    else:
                        statement_stage(run, page, account, data_dir, _ignore)
        return
    except LoginError as e:
        stage, reason = "login", why(e)
    except Exception as e:  # noqa: BLE001 - record and let the summary report it
        reason = why(e)
    run.summary.record(stage, "site", Status.FAILED, reason)
    # Nothing past a failed sign-in is tried, so no statement file is touched
    for account in remaining:
        run.summary.record("dump" if dump else "statement", account.label, Status.FAILED,
                           f"not attempted: {stage} failed")


def main(argv: list[str] | None = None, *, notifier: Notifier = send_notification) -> int:
    args = parse_args(argv)
    data_dir = process_dir(PROCESS)
    with Run.start(PROCESS, data_dir, notifier=notifier, command=COMMAND,
                   notifications=NOTIFICATIONS, event=final_event) as run:
        cfg = load_stage(run, data_dir)
        if cfg is not None:
            if args.capture:
                capture(run, cfg.site, profile_dir(data_dir))
            else:
                accounts = chosen_accounts(run, cfg, args.account, args.dump)
                if accounts:
                    browser_stage(run, cfg, accounts, data_dir, args.dump)
    return run.exit_code
