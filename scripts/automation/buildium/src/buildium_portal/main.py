"""buildium-statements: for each entity, sign in, get last period's statement, and sign out.

    uv run buildium-statements                  normal run: save statements under data\\statements
    uv run buildium-statements --entity KEY     only that entity, for testing
    uv run buildium-statements --dump           save page structure, download to the run folder,
                                                and report the names; saves nothing under data
    uv run buildium-statements --capture        by hand only: you sign in and click around while
                                                the page structure is saved to the run folder
"""

import argparse
import datetime as dt
import logging
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from automation_common.browser import save_page_evidence
from automation_common.config import ConfigError, process_dir
from automation_common.notify import send_notification
from automation_common.run import Notifier, Run, Status
from buildium_portal import PROCESS
from buildium_portal.capture import capture
from buildium_portal.config import NOTIFICATIONS, Config, Entity, load_config
from buildium_portal.login import SignOutError
from buildium_portal.parse import (
    check_statement,
    pdf_text,
    previous_period_end,
    read_statement,
    statement_file_name,
    statement_folder,
)
from buildium_portal.session import open_browser, profile_dir, signed_in_page
from buildium_portal.stages import chosen_entities, dump_observer, final_event, why
from buildium_portal.statements_fetch import fetch_statement
from buildium_portal.store import save_statement

COMMAND = "buildium-statements"

log = logging.getLogger(__name__)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=COMMAND, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dump", action="store_true",
                      help="save page structure and report names; save nothing under data")
    mode.add_argument("--capture", action="store_true",
                      help="by hand only: save page structure while you sign in and click around")
    ap.add_argument("--entity", metavar="KEY", help="only this entity's key from the config")
    return ap.parse_args(argv)


def load_stage(run: Run, data_dir: Path) -> Config | None:
    try:
        cfg = load_config(data_dir / "config.toml")
    except ConfigError as e:
        run.summary.record("config", "config.toml", Status.FAILED, str(e))
        return None
    run.notifications = cfg.notifications
    # Private: never in logs. The neutral keys stay readable, since the report needs them
    run.masker.add(cfg.site.login_url)
    run.masker.add(cfg.statements.system)
    for entity in cfg.entities:
        run.masker.add(entity.abbreviation)
        run.masker.add(entity.statement_name)
    log.info("config OK: %d entity(ies): %s", len(cfg.entities),
             ", ".join(entity.key for entity in cfg.entities))
    return cfg


def _ignore(_: str) -> None:
    pass


def statement_stage(run: Run, page: Page, cfg: Config, entity: Entity, period_end: dt.date,
                    data_dir: Path, dump: bool, observer: Callable[[str], None]) -> None:
    """Download, check, and save one entity's statement. Records the result; never raises."""
    download = run.run_dir / "downloads" / f"{entity.key}.pdf"
    file_name = statement_file_name(period_end, cfg.statements.system, entity.abbreviation)
    step = "download"
    try:
        fetch_statement(page, download, observer)
        step = "check"
        statement = read_statement(pdf_text(download))
        check_statement(statement, entity.statement_name, period_end)
        if dump:
            run.summary.record("statement", entity.key, Status.OK, f"would save {file_name}")
            return
        step = "save"
        target = data_dir / "data" / "statements" / statement_folder(period_end) / file_name
        save_statement(download, target)
    except Exception as e:  # noqa: BLE001 - one entity's failure must not stop the others
        save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        # Anything not saved stays in the run folder's downloads for a look
        run.summary.record("statement", entity.key, Status.FAILED, f"{step}: {why(e)}")
        return
    run.summary.record("statement", entity.key, Status.OK, f"saved {file_name}")


def process_entity(run: Run, ctx: BrowserContext, cfg: Config, entity: Entity,
                   period_end: dt.date, data_dir: Path, dump: bool) -> None:
    """Sign in, get the statement, and sign out. Only a failed sign-out raises."""
    observer = dump_observer(run, ctx, entity) if dump else _ignore
    reached = False
    try:
        with signed_in_page(run, ctx, cfg.site, entity, observer=observer) as page:
            reached = True
            run.summary.record("login", entity.key, Status.OK)
            statement_stage(run, page, cfg, entity, period_end, data_dir, dump, observer)
    except SignOutError:
        raise
    except Exception as e:  # noqa: BLE001 - a sign-in failure stops only this entity
        run.summary.record("browser" if reached else "login", entity.key, Status.FAILED, why(e))
        if not reached:
            run.summary.record("statement", entity.key, Status.FAILED,
                               "not processed: sign-in failed")


def browser_stage(run: Run, cfg: Config, entities: tuple[Entity, ...], period_end: dt.date,
                  data_dir: Path, dump: bool) -> None:
    """One browser for all entities, one at a time. A failed sign-out stops the rest."""
    remaining = list(entities)
    current = "site"
    try:
        with open_browser(data_dir) as ctx:
            while remaining:
                entity = remaining.pop(0)
                current = entity.key
                process_entity(run, ctx, cfg, entity, period_end, data_dir, dump)
    except SignOutError as e:
        stage = "sign-out"
        run.summary.record(stage, current, Status.FAILED, why(e))
    except Exception as e:  # noqa: BLE001 - record and let the summary report it
        stage = "browser"
        run.summary.record(stage, current, Status.FAILED, why(e))
    else:
        return
    for entity in remaining:
        run.summary.record("statement", entity.key, Status.FAILED,
                           f"not processed: {stage} failed")


def main(argv: list[str] | None = None, *, notifier: Notifier = send_notification,
         today: dt.date | None = None) -> int:
    args = parse_args(argv)
    data_dir = process_dir(PROCESS)
    with Run.start(PROCESS, data_dir, notifier=notifier, command=COMMAND,
                   notifications=NOTIFICATIONS, event=final_event) as run:
        cfg = load_stage(run, data_dir)
        if cfg is not None:
            if args.capture:
                capture(run, cfg.site, profile_dir(data_dir))
            else:
                entities = chosen_entities(run, cfg, args.entity)
                period_end = previous_period_end(today or dt.date.today())
                log.info("expecting statements for the period ending %s", period_end)
                if entities:
                    browser_stage(run, cfg, entities, period_end, data_dir, args.dump)
    return run.exit_code
