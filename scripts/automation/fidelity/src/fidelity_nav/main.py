"""fidelity-nav: config, run folder, one browser, per-fund loop, sheet, summary.

    uv run fidelity-nav            normal run: append NAVs to data\\nav.csv, then
                                   copy new rows to the Google Sheet if configured
    uv run fidelity-nav --dump     save each page's outline, screenshot,
                                   and HTML to the run folder; writes no data
"""

import argparse
import datetime as dt
import logging
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from automation_common.browser import (
    first_page,
    launch_edge,
    save_page_evidence,
    start_trace,
    stop_trace,
)
from automation_common.config import ConfigError, process_dir
from automation_common.run import Run, Status
from automation_common.secrets import SecretMasker
from automation_common.sheets import Worksheet, open_worksheet
from fidelity_nav import PROCESS
from fidelity_nav.config import Config, Fund, load_config
from fidelity_nav.fetch import dump_fund_page, fetch_page_html
from fidelity_nav.parse import ParseError, parse_nav_page
from fidelity_nav.sheet import COLUMNS, plan_append
from fidelity_nav.store import NavRow, Outcome, append_nav, read_rows

OpenWorksheet = Callable[[str, str, SecretMasker], Worksheet]

log = logging.getLogger(__name__)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="fidelity-nav", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", action="store_true",
                    help="save page outline, screenshot, and HTML per fund; write no data")
    return ap.parse_args(argv)


def load_stage(run: Run, data_dir: Path) -> Config | None:
    path = data_dir / "config.toml"
    try:
        cfg = load_config(path)
    except ConfigError as e:
        run.summary.record("config", "config.toml", Status.FAILED, str(e))
        return None
    if cfg.sheet is not None:
        run.masker.add(cfg.sheet.spreadsheet_id)  # private: never in logs
    log.info("config OK: %d fund(s): %s; sheet %s", len(cfg.funds),
             ", ".join(f.symbol for f in cfg.funds),
             f'tab "{cfg.sheet.worksheet}"' if cfg.sheet else "not configured")
    return cfg


def process_fund(run: Run, page: Page, fund: Fund, csv_path: Path) -> bool:
    """Fetch, parse, and store one fund. Records the result; never raises."""
    step = "fetch"
    try:
        html = fetch_page_html(page, fund.url)
        step = "parse"
        reading = parse_nav_page(html, dt.date.today())
        step = "store"
        row = NavRow(reading.as_of, fund.symbol, reading.nav,
                     dt.datetime.now().astimezone(), fund.url)
        outcome = append_nav(csv_path, row)
    except Exception as e:  # noqa: BLE001 - one fund's failure must not stop the others
        prefix = run.next_failure_prefix()
        if step != "store":
            save_page_evidence(page, run.run_dir, prefix)
        run.summary.record("fund", fund.symbol, Status.FAILED,
                           f"{step}: {type(e).__name__}: {str(e).splitlines()[0][:200]}")
        return False
    if outcome is Outcome.REVISED:
        log.warning("%s: NAV for %s changed to %s since the last run; appended a new row",
                    fund.symbol, reading.as_of, reading.nav)
    status = Status.UNCHANGED if outcome is Outcome.UNCHANGED else Status.OK
    run.summary.record("fund", fund.symbol, status,
                       f"NAV {reading.nav} as of {reading.as_of}, {outcome} (via {reading.method})")
    return True


def dump_fund(run: Run, page: Page, fund: Fund) -> bool:
    """--dump: save evidence and report what the parser would read. Never raises."""
    try:
        html = dump_fund_page(page, fund.url, run.run_dir, f"dump_{fund.symbol}")
    except Exception as e:  # noqa: BLE001
        run.summary.record("dump", fund.symbol, Status.FAILED, f"{type(e).__name__}: {e}")
        return False
    try:
        r = parse_nav_page(html, dt.date.today())
        detail = f"saved; parser reads NAV {r.nav} as of {r.as_of} (via {r.method})"
    except ParseError as e:
        detail = f"saved; parser says: {e}"
    run.summary.record("dump", fund.symbol, Status.OK, detail)
    return True


def browser_stage(run: Run, cfg: Config, data_dir: Path, dump: bool) -> None:
    """One Edge session for all funds, one fund at a time."""
    csv_path = data_dir / "data" / "nav.csv"
    done: set[str] = set()
    try:
        with sync_playwright() as pw:
            ctx = launch_edge(pw, data_dir / "profiles" / "edge")
            # Public pages, no sign-in, so tracing can start right away
            start_trace(ctx)
            all_ok = True
            try:
                page = first_page(ctx)
                for fund in cfg.funds:
                    ok = dump_fund(run, page, fund) if dump else process_fund(
                        run, page, fund, csv_path)
                    done.add(fund.symbol)
                    all_ok &= ok
            finally:
                stop_trace(ctx, None if all_ok else run.run_dir / "trace.zip")
                ctx.close()
    except Exception as e:  # noqa: BLE001 - record and let the summary report it
        run.summary.record("browser", "edge", Status.FAILED,
                           f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
        for fund in cfg.funds:
            if fund.symbol not in done:
                run.summary.record("fund", fund.symbol, Status.FAILED,
                                   "not processed: browser stage failed")


def sheet_stage(run: Run, cfg: Config, data_dir: Path,
                open_ws: OpenWorksheet = open_worksheet) -> None:
    """Append the CSV rows the Google Sheet doesn't have yet. Records the result."""
    if cfg.sheet is None:
        log.info("no [sheet] in config; skipping the sheet")
        return
    tab = cfg.sheet.worksheet
    try:
        csv_rows = read_rows(data_dir / "data" / "nav.csv")
        ws = open_ws(cfg.sheet.spreadsheet_id, tab, run.masker)
        plan = plan_append(csv_rows, ws.read_rows(COLUMNS))
        if plan.added:
            ws.append_rows(plan.rows)
    except Exception as e:  # noqa: BLE001 - recorded; the next run catches up
        run.summary.record("sheet", tab, Status.FAILED,
                           f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
        return
    if plan.added:
        run.summary.record("sheet", tab, Status.OK, f"appended {plan.added} row(s)")
    else:
        run.summary.record("sheet", tab, Status.UNCHANGED, "already up to date")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = process_dir(PROCESS)
    with Run.start(PROCESS, data_dir) as run:
        cfg = load_stage(run, data_dir)
        if cfg is not None:
            browser_stage(run, cfg, data_dir, args.dump)
            if not args.dump:
                sheet_stage(run, cfg, data_dir)
    return run.exit_code
