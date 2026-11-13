"""bank1-activity: sign in once, then download each account's activity and write it to its sheet.

    uv run bank1-activity                          normal run: every account with an activity_url
    uv run bank1-activity --account LABEL          only that account
    uv run bank1-activity --download-only          download and check into the run folder only:
                                                   nothing in data, no sheet written
    uv run bank1-activity --dump                   sign in, then fill in one download form of each
                                                   kind and save its structure; downloads nothing

Each account's range runs from its activity_start to today. The site's download is current to
the minute, so every run replaces the account's whole block in the sheet: a later run the same
day just picks up what has posted since. Nothing is appended to the sheet or compared. Each run
also saves a new CSV per account, stamped with the run's start time.
"""

import argparse
import datetime as dt
import logging
from pathlib import Path

from playwright.sync_api import Page

from automation_common.browser import save_page_evidence
from automation_common.config import process_dir
from automation_common.notify import send_notification
from automation_common.run import Notifier, Run, Status
from automation_common.sheets import open_worksheet
from bank1_portal import PROCESS
from bank1_portal.activity_fetch import (
    check_activity_page,
    check_file_type,
    choose_date_range,
    download,
    fill_dates,
    go_to_activity,
)
from bank1_portal.activity_parse import Activity, parse_activity, site_date
from bank1_portal.activity_sheet import (
    OpenWorksheet,
    stamp_workbook,
    workbooks_to_stamp,
    write_activity,
)
from bank1_portal.cli import Observer, choose, dump_observer, final_event, ignore, load_stage, why
from bank1_portal.config import NOTIFICATIONS, Account, Config
from bank1_portal.login import LoginError
from bank1_portal.session import open_browser, signed_in_page
from bank1_portal.store import activity_path, save_activity

COMMAND = "bank1-activity"

log = logging.getLogger(__name__)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=COMMAND, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dump", action="store_true",
                      help="fill in one download form of each kind; download nothing")
    mode.add_argument("--download-only", action="store_true",
                      help="download and check into the run folder only; save and write nothing")
    ap.add_argument("--account", metavar="LABEL", help="only this account's label from the config")
    return ap.parse_args(argv)


def activity_accounts(run: Run, cfg: Config, label: str | None,
                      dump: bool) -> tuple[Account, ...]:
    """The accounts with an activity_url: one by label, the first of each kind, or all."""
    accounts = tuple(account for account in cfg.accounts if account.activity is not None)
    return choose(run, accounts, label, dump,
                  missing="no account with this label and an activity_url in the config")


def _summary(activity: Activity) -> str:
    return (f"{len(activity.rows)} row(s) {site_date(activity.first)} to "
            f"{site_date(activity.last)}")


def _open_form(page: Page, account: Account, today: dt.date, observer: Observer) -> None:
    assert account.activity is not None
    if account.activity.start > today:
        raise ValueError("activity_start is after today")
    go_to_activity(page, account)
    try:
        check_activity_page(page, account)
    finally:
        observer("activity")  # also when the form wasn't recognized
    choose_date_range(page)
    fill_dates(page, site_date(account.activity.start), site_date(today))
    check_file_type(page)


def dump_stage(run: Run, page: Page, account: Account, today: dt.date,
               observer: Observer) -> None:
    """Fill in the form and save its structure. Never clicks Download."""
    try:
        _open_form(page, account, today, observer)
        observer("form")
    except Exception as e:  # noqa: BLE001 - one account's failure must not stop the others
        save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        run.summary.record("dump", account.label, Status.FAILED, why(e))
        return
    run.summary.record("dump", account.label, Status.OK,
                       f"form filled for {site_date(account.activity.start)} to "
                       f"{site_date(today)}; nothing downloaded")


def activity_stage(run: Run, page: Page, account: Account, data_dir: Path, today: dt.date,
                   download_only: bool, open_ws: OpenWorksheet) -> None:
    """Download, check, save, and write one account. Records the result; never raises."""
    assert account.activity is not None
    # The raw download stays in the run folder, whether or not it passes
    dest = run.run_dir / "downloads" / f"{account.label}.csv"
    step = "open"
    try:
        _open_form(page, account, today, ignore)
        step = "download"
        download(page, dest)
        step = "check"
        activity = parse_activity(dest.read_text(encoding="utf-8-sig"), account.kind,
                                  account.activity.start, today)
    except Exception as e:  # noqa: BLE001 - one account's failure must not stop the others
        if step != "check":
            save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        run.summary.record("activity", account.label, Status.FAILED, f"{step}: {why(e)}")
        return
    if download_only:
        run.summary.record("activity", account.label, Status.OK,
                           f"{_summary(activity)}; download only, nothing saved or written")
        return
    try:
        step = "save"
        save_activity(dest, activity_path(data_dir, account.activity.abbreviation,
                                          account.suffix, run.summary.started_at))
        destination = account.activity.destination
        if destination is None:
            run.summary.record("activity", account.label, Status.OK,
                               f"{_summary(activity)}; saved, no sheet")
            return
        step = "write"
        write_activity(destination, activity, run.masker, open_ws)
    except Exception as e:  # noqa: BLE001 - one account's failure must not stop the others
        run.summary.record("activity", account.label, Status.FAILED, f"{step}: {why(e)}")
        return
    run.summary.record("activity", account.label, Status.OK,
                       f"{_summary(activity)}; saved and written to {destination.sheet}")


def browser_stage(run: Run, cfg: Config, accounts: tuple[Account, ...], data_dir: Path,
                  today: dt.date, args: argparse.Namespace, open_ws: OpenWorksheet) -> None:
    """One browser and one sign-in for every account, one at a time."""
    remaining = list(accounts)
    stage, reason = "browser", ""
    try:
        with open_browser(data_dir) as ctx:
            observer = dump_observer(run, ctx, "site") if args.dump else ignore
            with signed_in_page(run, ctx, cfg.site, observer=observer) as page:
                run.summary.record("login", "site", Status.OK)
                while remaining:
                    account = remaining.pop(0)
                    if args.dump:
                        dump_stage(run, page, account, today,
                                   dump_observer(run, ctx, account.label))
                    else:
                        activity_stage(run, page, account, data_dir, today,
                                       args.download_only, open_ws)
        return
    except LoginError as e:
        stage, reason = "login", why(e)
    except Exception as e:  # noqa: BLE001 - record and let the summary report it
        reason = why(e)
    run.summary.record(stage, "site", Status.FAILED, reason)
    # Nothing past a failed sign-in is tried, so no file or sheet is touched
    for account in remaining:
        run.summary.record("dump" if args.dump else "activity", account.label, Status.FAILED,
                           f"not attempted: {stage} failed")


def stamp_stage(run: Run, cfg: Config, accounts: tuple[Account, ...],
                open_ws: OpenWorksheet) -> None:
    """Stamp each workbook whose accounts all wrote this run. Records each; never raises."""
    ok = {item.item for item in run.summary.items
          if item.stage == "activity" and item.status is Status.OK}
    started = run.summary.started_at
    shown = started.strftime("%Y-%m-%d %H:%M:%S")
    for workbook, all_written in workbooks_to_stamp(cfg.sheets, accounts, ok):
        if not all_written:
            run.summary.record("stamp", workbook.name, Status.UNCHANGED,
                               "update time left alone: an account writing to it failed")
            continue
        try:
            stamp_workbook(workbook, started, run.masker, open_ws)
        except Exception as e:  # noqa: BLE001 - recorded; the rows are already written
            run.summary.record("stamp", workbook.name, Status.FAILED,
                               f"the rows were written, but not the update time: {why(e)}")
        else:
            run.summary.record("stamp", workbook.name, Status.OK, f"updated {shown}")


def main(argv: list[str] | None = None, *, notifier: Notifier = send_notification,
         today: dt.date | None = None, open_ws: OpenWorksheet = open_worksheet) -> int:
    args = parse_args(argv)
    data_dir = process_dir(PROCESS)
    with Run.start(PROCESS, data_dir, notifier=notifier, command=COMMAND,
                   notifications=NOTIFICATIONS, event=final_event) as run:
        cfg = load_stage(run, data_dir)
        if cfg is not None:
            accounts = activity_accounts(run, cfg, args.account, args.dump)
            if not accounts and args.account is None:
                run.summary.record("config", "accounts", Status.FAILED,
                                   "no account has an activity_url")
            if accounts:
                browser_stage(run, cfg, accounts, data_dir, today or dt.date.today(), args,
                              open_ws)
                if not args.dump and not args.download_only:
                    stamp_stage(run, cfg, accounts, open_ws)
    return run.exit_code
