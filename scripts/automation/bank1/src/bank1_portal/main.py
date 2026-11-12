"""bank1-statements: sign in once, then save each account's newest PDF statements and letters.

    uv run bank1-statements                   normal run: every account, saved under data
    uv run bank1-statements --account LABEL   only that account
    uv run bank1-statements --dump            sign in, then save the structure of one statements
                                              page of each kind; downloads nothing
    uv run bank1-statements --capture         by hand only: you sign in and click around while
                                              the page structure is saved to the run folder
"""

import argparse
import logging
from pathlib import Path

from playwright.sync_api import Page

from automation_common.browser import save_page_evidence
from automation_common.config import process_dir
from automation_common.notify import send_notification
from automation_common.run import Notifier, Run, Status
from bank1_portal import PROCESS
from bank1_portal.capture import capture
from bank1_portal.cli import (
    Observer,
    choose,
    dump_observer,
    final_event,
    load_stage,
    why,
)
from bank1_portal.cli import ignore as _ignore
from bank1_portal.config import NOTIFICATIONS, Account, Config
from bank1_portal.login import LoginError
from bank1_portal.pages import FetchError
from bank1_portal.parse import (
    Item,
    check_pdf_file,
    check_statement,
    pdf_text,
    statement_file_name,
)
from bank1_portal.session import open_browser, profile_dir, signed_in_page
from bank1_portal.statements_fetch import (
    Plan,
    check_statements_page,
    download,
    go_to_statements,
    plan,
    still_on_page,
)
from bank1_portal.store import save_statement, statement_path

COMMAND = "bank1-statements"
LETTER_MIN_BYTES = 1_000  # a letter can be a short page; a statement is checked at 5,000

log = logging.getLogger(__name__)


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


def chosen_accounts(run: Run, cfg: Config, label: str | None,
                    dump: bool) -> tuple[Account, ...]:
    """The accounts this run works on: one by label, the first of each kind, or all."""
    return choose(run, cfg.accounts, label, dump)


def _open(page: Page, account: Account, observer: Observer) -> Plan:
    go_to_statements(page, account)
    try:
        check_statements_page(page, account)
    finally:
        observer("statements")  # also when the list wasn't recognized
    return plan(page, account)


def _key(item: Item) -> tuple:
    return item.closing, item.part, item.letter


def _names(account: Account, items: list[Item]) -> list[str]:
    """Each row's name in the report. A letter's title stays out: it may name the bank."""
    names, letters = [], 0
    for item in items:
        if item.letter:
            letters += 1
            names.append(f"{account.label} letter {letters}")
        else:
            names.append(f"{account.label} {item.part}".strip())
    return names


def _target(cfg: Config, account: Account, data_dir: Path, item: Item) -> Path:
    name = statement_file_name(cfg.site.name_in_files, account.suffix, item.closing, item.part)
    return statement_path(data_dir, name)


def _skip(item: Item, target: Path) -> bool:
    """An older letter already saved doesn't change, so it isn't downloaded again."""
    return item.letter and not item.newest and target.exists()


def dump_stage(run: Run, page: Page, cfg: Config, account: Account, data_dir: Path,
               observer: Observer) -> None:
    """Open the page and report what a run would save. Downloads nothing, and clicks nothing
    but the year filter, when the newest year has no statement yet."""
    try:
        found = _open(page, account, observer)
    except Exception as e:  # noqa: BLE001 - one account's failure must not stop the others
        save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        run.summary.record("dump", account.label, Status.FAILED, why(e))
        return
    statements = [item for item in found.items if not item.letter]
    letters = [item for item in found.items if item.letter]
    new = sum(not _skip(item, _target(cfg, account, data_dir, item)) for item in letters)
    kinds = ", ".join(item.part or "statement" for item in statements)
    run.summary.record("dump", account.label, Status.OK,
                       f"would save {kinds} closing {statements[0].closing} and "
                       f"{new} of {len(letters)} letter(s)")


def statement_stage(run: Run, page: Page, cfg: Config, account: Account, data_dir: Path,
                    observer: Observer) -> None:
    """Save the account's newest statements and its letters. Records each; never raises."""
    try:
        found = _open(page, account, observer)
    except Exception as e:  # noqa: BLE001 - one account's failure must not stop the others
        save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        run.summary.record("statement", account.label, Status.FAILED, f"open: {why(e)}")
        return
    rows = {_key(item): found.rows[item.index] for item in found.items}
    for number, (item, name) in enumerate(zip(found.items, _names(account, found.items),
                                              strict=True), start=1):
        target = _target(cfg, account, data_dir, item)
        if _skip(item, target):
            run.summary.record("statement", name, Status.UNCHANGED, "already saved")
            continue
        # A file that fails its checks stays in the run folder's downloads for a look
        dest = run.run_dir / "downloads" / f"{account.label}_{number}.pdf"
        step = "download"
        try:
            if _key(item) not in rows:
                raise FetchError("the row was gone after the page reopened")
            download(page, rows[_key(item)], dest)
            step = "check"
            if item.letter:
                check_pdf_file(dest, LETTER_MIN_BYTES)
            else:
                check_pdf_file(dest)
                check_statement(pdf_text(dest), account.suffix, item.closing)
            step = "save"
            save_statement(dest, target)
            run.summary.record("statement", name, Status.OK, f"saved {item.closing}")
        except Exception as e:  # noqa: BLE001 - one row's failure must not stop the others
            save_page_evidence(page, run.run_dir, run.next_failure_prefix())
            run.summary.record("statement", name, Status.FAILED, f"{step}: {why(e)}")
        # A PDF shown in the page has the list reloaded afterward, which resets its year
        # filter, so the rows are found again after every download
        try:
            still_on_page(page, account)
            again = plan(page, account)
            rows = {_key(i): again.rows[i.index] for i in again.items}
        except Exception as e:  # noqa: BLE001 - the rest of this account can't be reached
            run.summary.record("statement", account.label, Status.FAILED, f"reopen: {why(e)}")
            return


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
                        dump_stage(run, page, cfg, account, data_dir,
                                   dump_observer(run, ctx, account.label))
                    else:
                        statement_stage(run, page, cfg, account, data_dir, _ignore)
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
