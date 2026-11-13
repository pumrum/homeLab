"""buildium-gl: export each entity's general ledger, clean it, and replace it in the sheet.

    uv run buildium-gl                       normal run: every entity, then save each export
                                             to data/ledgers and write the sheet
    uv run buildium-gl --entity KEY          only that entity, for testing; writes nothing
    uv run buildium-gl --dump                every entity, and also save page structure and a
                                             profile of each export; writes nothing
    uv run buildium-gl --interactive         for a run watched on screen: if the site's bot
                                             check stops a sign-in, wait up to 10 minutes for
                                             you to sign in by hand, then carry on. Never for
                                             a job. Can be added to the others

The sheet needs every entity, so the first failure stops the run, and nothing is saved to
data/ledgers or written to the sheet.
"""

import argparse
import datetime as dt
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from automation_common.browser import save_page_evidence
from automation_common.config import ConfigError, process_dir
from automation_common.notify import send_notification
from automation_common.run import Notifier, Run, Status
from automation_common.sheets import open_worksheet
from buildium_portal import PROCESS
from buildium_portal.config import NOTIFICATIONS, Entity, GlConfig, load_gl_config
from buildium_portal.gl_fetch import METHODS, export_method, open_report
from buildium_portal.gl_files import LEDGER_FOLDER, ledger_name, save_ledgers
from buildium_portal.gl_parse import (
    CASH_VIEW_COLUMNS,
    MASTER_COLUMNS,
    Ledger,
    clean_export,
    repeated_pairs,
    shared_pairs,
    sheet_rows,
)
from buildium_portal.gl_profile import profile
from buildium_portal.gl_rules import CashView, cash_view
from buildium_portal.gl_sheet import OpenWorksheet, check_sheet, sheet_stage
from buildium_portal.login import SignOutError
from buildium_portal.session import open_browser, signed_in_page
from buildium_portal.stages import chosen_entities, dump_observer, final_event, why

COMMAND = "buildium-gl"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntityResult:
    key: str
    exports: dict[str, bytes]
    ledgers: dict[str, Ledger]
    view: CashView


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=COMMAND, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", action="store_true",
                    help="also save page structure and a profile of each export; write nothing")
    ap.add_argument("--entity", metavar="KEY",
                    help="only this entity's key from the config; write nothing")
    ap.add_argument("--interactive", action="store_true",
                    help="if the bot check stops a sign-in, wait for you to sign in by hand")
    return ap.parse_args(argv)


def load_stage(run: Run, data_dir: Path) -> GlConfig | None:
    try:
        cfg = load_gl_config(data_dir / "config.toml")
    except ConfigError as e:
        run.summary.record("config", "config.toml", Status.FAILED, str(e))
        return None
    run.notifications = cfg.notifications
    # Private: never in logs. The neutral keys stay readable, since the report needs them
    run.masker.add(cfg.site.login_url)
    run.masker.add(cfg.gl.spreadsheet_id)
    run.masker.add(cfg.gl.master_tab)
    for entity in cfg.entities:
        run.masker.add(entity.abbreviation)
        run.masker.add(entity.statement_name)
    for target in cfg.gl.entities.values():
        run.masker.add(target.code)
        run.masker.add(target.tab)
    for fix in cfg.gl.unit_fixes:
        run.masker.add(fix.lease_id)
        run.masker.add(fix.unit_number)
    log.info("config OK: %d entity(ies): %s", len(cfg.entities),
             ", ".join(entity.key for entity in cfg.entities))
    return cfg


def _ignore(_: str) -> None:
    pass


def _export_one(run: Run, page: Page, entity: Entity, method: str,
                observer: Callable[[str], None], dump: bool) -> bytes:
    # Saved straight to the run folder, and kept: it's what any later question gets checked with
    dest = run.run_dir / "downloads" / ledger_name(entity.abbreviation, method)
    started = time.monotonic()
    export_method(page, method, dest, observer)
    seconds = time.monotonic() - started
    raw = dest.read_bytes()
    if dump:
        (run.run_dir / f"gl_profile_{entity.key}_{method}.txt").write_text(
            run.masker.mask(profile(raw)), encoding="utf-8")
    run.summary.record("export", f"{entity.key} {method}", Status.OK,
                       f"{len(raw)} bytes in {seconds:.0f}s")
    return raw


def export_stage(run: Run, page: Page, cfg: GlConfig, entity: Entity, today: dt.date,
                 observer: Callable[[str], None], dump: bool) -> dict[str, bytes] | None:
    """Export every method for one entity. Records the result; never raises."""
    step = "report"
    exports: dict[str, bytes] = {}
    try:
        open_report(page, cfg.site, today, observer)
        for method in METHODS:
            step = method
            exports[method] = _export_one(run, page, entity, method, observer, dump)
    except Exception as e:  # noqa: BLE001 - recorded here, and the run stops after sign-out
        save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        run.summary.record("export", entity.key, Status.FAILED, f"{step}: {why(e)}")
        return None
    return exports


def process_entity(run: Run, ctx: BrowserContext, cfg: GlConfig, entity: Entity,
                   today: dt.date, dump: bool,
                   interactive: bool = False) -> dict[str, bytes] | None:
    """Sign in, export, and sign out. None if that failed. Only a failed sign-out raises."""
    observer = dump_observer(run, ctx, entity) if dump else _ignore
    reached = False
    try:
        with signed_in_page(run, ctx, cfg.site, entity, observer=observer,
                            interactive=interactive) as page:
            reached = True
            run.summary.record("login", entity.key, Status.OK)
            return export_stage(run, page, cfg, entity, today, observer, dump)
    except SignOutError:
        raise
    except Exception as e:  # noqa: BLE001 - recorded here, and the run stops after this
        run.summary.record("browser" if reached else "login", entity.key, Status.FAILED, why(e))
        return None


def _view_detail(view: CashView) -> str:
    removed = ", ".join(f"{name} {count}" for name, count in view.removed.items()) or "none"
    return (f"kept {len(view.rows)}; removed: {removed}; "
            f"Deposit Transfer rows set to Equity: {view.deposit_transfers}")


def transform_stage(run: Run, cfg: GlConfig, entity: Entity,
                    exports: dict[str, bytes]) -> EntityResult | None:
    """Clean one entity's exports and build its Cash view. Records the result; never raises."""
    fixes = tuple(fix for fix in cfg.gl.unit_fixes if fix.key == entity.key)
    code = cfg.gl.entities[entity.key].code
    ledgers: dict[str, Ledger] = {}
    for method, raw in exports.items():
        try:
            ledger = clean_export(raw, code, method, fixes)
        except Exception as e:  # noqa: BLE001 - recorded here, and the run stops after this
            run.summary.record("transform", f"{entity.key} {method}", Status.FAILED, why(e))
            return None
        ledgers[method] = ledger
        run.summary.record(
            "transform", f"{entity.key} {method}", Status.OK,
            f"read {ledger.read}, kept {len(ledger.rows)}, no Id {ledger.blank_id}, "
            f"unit fixes {ledger.unit_fixed}, repeated pairs {repeated_pairs(ledger.rows)}")
    if len(set(exports.values())) < len(exports):
        log.warning("%s: the exports are identical, so the method may not have applied",
                    entity.key)
    view = cash_view(ledgers["Cash"].rows)
    if not view.rows:
        run.summary.record("cash view", entity.key, Status.FAILED, "the rules removed every row")
        return None
    run.summary.record("cash view", entity.key, Status.OK, _view_detail(view))
    return EntityResult(entity.key, exports, ledgers, view)


def browser_stage(run: Run, cfg: GlConfig, entities: tuple[Entity, ...], today: dt.date,
                  data_dir: Path, dump: bool, interactive: bool = False) -> list[EntityResult]:
    """One browser for all entities, one at a time, stopping at the first failure."""
    remaining = list(entities)
    current = "site"
    results: list[EntityResult] = []
    try:
        with open_browser(data_dir) as ctx:
            while remaining:
                entity = remaining.pop(0)
                current = entity.key
                exports = process_entity(run, ctx, cfg, entity, today, dump, interactive)
                result = None if exports is None else transform_stage(run, cfg, entity, exports)
                if result is None:
                    break
                results.append(result)
    except SignOutError as e:
        run.summary.record("sign-out", current, Status.FAILED, why(e))
    except Exception as e:  # noqa: BLE001 - record and let the summary report it
        run.summary.record("browser", current, Status.FAILED, why(e))
    for entity in remaining:
        run.summary.record("export", entity.key, Status.FAILED,
                           f"not processed: {current} failed")
    return results


def check_entities_differ(run: Run, results: list[EntityResult]) -> None:
    """All entities share one browser, so catch one being exported under another's name."""
    for a, b in combinations(results, 2):
        for method in METHODS:
            shared = shared_pairs(a.ledgers[method].rows, b.ledgers[method].rows)
            if shared:
                run.summary.record("check", f"{a.key} and {b.key}", Status.FAILED,
                                   f"{shared} {method} entries are in both exports")


def files_stage(run: Run, cfg: GlConfig, results: list[EntityResult], data_dir: Path) -> None:
    """Replace every export's file in the ledger folder. A failure doesn't stop the sheet.

    Files are named by abbreviation, which is masked, so the report shows a file as ***_Cash.csv.
    """
    abbreviations = {entity.key: entity.abbreviation for entity in cfg.entities}
    files = {ledger_name(abbreviations[result.key], method): result.exports[method]
             for result in results for method in METHODS}
    try:
        save_ledgers(files, data_dir / LEDGER_FOLDER)
    except Exception as e:  # noqa: BLE001 - recorded, and the sheet is still written
        run.summary.record("ledger files", str(LEDGER_FOLDER), Status.FAILED, why(e))
    else:
        run.summary.record("ledger files", str(LEDGER_FOLDER), Status.OK,
                           f"replaced {len(files)} files")


def write_stage(run: Run, cfg: GlConfig, results: list[EntityResult], write: bool,
                open_ws: OpenWorksheet, data_dir: Path) -> None:
    views = {result.key: sheet_rows(result.view.rows, CASH_VIEW_COLUMNS) for result in results}
    master = [row for result in results for method in METHODS
              for row in sheet_rows(result.ledgers[method].rows, MASTER_COLUMNS)]
    if run.summary.exit_code != 0:
        log.error("something failed, so nothing is saved to the ledger folder or the sheet")
    elif write:
        files_stage(run, cfg, results, data_dir)
        sheet_stage(run, cfg.gl, views, master, open_ws)
    else:
        run.summary.record("ledger files", str(LEDGER_FOLDER), Status.UNCHANGED,
                           f"not saved; would replace {len(results) * len(METHODS)} files")
        run.summary.record("sheet", "all tabs", Status.UNCHANGED,
                           f"not written; would write {len(master)} rows to the master")


def main(argv: list[str] | None = None, *, notifier: Notifier = send_notification,
         today: dt.date | None = None, open_ws: OpenWorksheet = open_worksheet) -> int:
    args = parse_args(argv)
    data_dir = process_dir(PROCESS)
    with Run.start(PROCESS, data_dir, notifier=notifier, command=COMMAND,
                   notifications=NOTIFICATIONS, event=final_event) as run:
        cfg = load_stage(run, data_dir)
        if cfg is not None:
            entities = chosen_entities(run, cfg, args.entity)
            # Only a run that writes needs the sheet, and it's checked before the slow part
            write = not (args.dump or args.entity)
            if entities and (not write or check_sheet(run, cfg.gl, open_ws)):
                results = browser_stage(run, cfg, entities, today or dt.date.today(), data_dir,
                                        args.dump, args.interactive)
                check_entities_differ(run, results)
                write_stage(run, cfg, results, write, open_ws, data_dir)
    return run.exit_code
