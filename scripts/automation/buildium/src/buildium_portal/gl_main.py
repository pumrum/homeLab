"""buildium-gl: for each entity, sign in, export the general ledger by method, and sign out.

    uv run buildium-gl --dump                save page structure, the exports, and a profile of
                                             each export to the run folder
    uv run buildium-gl --dump --entity KEY   only that entity

The run stops at the first entity that fails, because the sheet needs all of them. --dump is
required for now: nothing writes to a sheet yet.
"""

import argparse
import datetime as dt
import logging
import time
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from automation_common.browser import save_page_evidence
from automation_common.config import ConfigError, process_dir
from automation_common.notify import send_notification
from automation_common.run import Notifier, Run, Status
from buildium_portal import PROCESS
from buildium_portal.config import NOTIFICATIONS, Entity, GlConfig, load_gl_config
from buildium_portal.gl_fetch import METHODS, export_method, open_report
from buildium_portal.gl_profile import profile
from buildium_portal.login import SignOutError
from buildium_portal.session import open_browser, signed_in_page
from buildium_portal.stages import chosen_entities, dump_observer, final_event, why

COMMAND = "buildium-gl"

log = logging.getLogger(__name__)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=COMMAND, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", action="store_true", required=True,
                    help="save page structure, the exports, and a profile of each export")
    ap.add_argument("--entity", metavar="KEY", help="only this entity's key from the config")
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
    for entity in cfg.entities:
        run.masker.add(entity.abbreviation)
        run.masker.add(entity.statement_name)
    log.info("config OK: %d entity(ies): %s", len(cfg.entities),
             ", ".join(entity.key for entity in cfg.entities))
    return cfg


def _export_one(run: Run, page: Page, entity: Entity, method: str,
                observer: Callable[[str], None]) -> bytes:
    dest = run.run_dir / "downloads" / f"{entity.key}_{method}.csv"
    started = time.monotonic()
    export_method(page, method, dest, observer)
    seconds = time.monotonic() - started
    raw = dest.read_bytes()
    (run.run_dir / f"gl_profile_{entity.key}_{method}.txt").write_text(
        run.masker.mask(profile(raw)), encoding="utf-8")
    run.summary.record("export", f"{entity.key} {method}", Status.OK,
                       f"{len(raw)} bytes in {seconds:.0f}s")
    return raw


def export_stage(run: Run, page: Page, cfg: GlConfig, entity: Entity, today: dt.date,
                 observer: Callable[[str], None]) -> bool:
    """Export every method for one entity. Records the result; never raises."""
    step = "report"
    try:
        open_report(page, cfg.site, today, observer)
        exports = []
        for method in METHODS:
            step = method
            exports.append(_export_one(run, page, entity, method, observer))
    except Exception as e:  # noqa: BLE001 - recorded here, and the run stops after sign-out
        save_page_evidence(page, run.run_dir, run.next_failure_prefix())
        run.summary.record("export", entity.key, Status.FAILED, f"{step}: {why(e)}")
        return False
    if len(set(exports)) < len(exports):
        log.warning("%s: the exports are identical, so the method may not have applied",
                    entity.key)
    return True


def process_entity(run: Run, ctx: BrowserContext, cfg: GlConfig, entity: Entity,
                   today: dt.date) -> bool:
    """Sign in, export, and sign out. True if it all worked. Only a failed sign-out raises."""
    observer = dump_observer(run, ctx, entity)
    reached = False
    try:
        with signed_in_page(run, ctx, cfg.site, entity, observer=observer) as page:
            reached = True
            run.summary.record("login", entity.key, Status.OK)
            return export_stage(run, page, cfg, entity, today, observer)
    except SignOutError:
        raise
    except Exception as e:  # noqa: BLE001 - recorded here, and the run stops after this
        run.summary.record("browser" if reached else "login", entity.key, Status.FAILED, why(e))
        return False


def browser_stage(run: Run, cfg: GlConfig, entities: tuple[Entity, ...], today: dt.date,
                  data_dir: Path) -> None:
    """One browser for all entities, one at a time, stopping at the first failure."""
    remaining = list(entities)
    current = "site"
    try:
        with open_browser(data_dir) as ctx:
            while remaining:
                entity = remaining.pop(0)
                current = entity.key
                if not process_entity(run, ctx, cfg, entity, today):
                    break
    except SignOutError as e:
        run.summary.record("sign-out", current, Status.FAILED, why(e))
    except Exception as e:  # noqa: BLE001 - record and let the summary report it
        run.summary.record("browser", current, Status.FAILED, why(e))
    for entity in remaining:
        run.summary.record("export", entity.key, Status.FAILED,
                           f"not processed: {current} failed")


def main(argv: list[str] | None = None, *, notifier: Notifier = send_notification,
         today: dt.date | None = None) -> int:
    args = parse_args(argv)
    data_dir = process_dir(PROCESS)
    with Run.start(PROCESS, data_dir, notifier=notifier, command=COMMAND,
                   notifications=NOTIFICATIONS, event=final_event) as run:
        cfg = load_stage(run, data_dir)
        if cfg is not None:
            entities = chosen_entities(run, cfg, args.entity)
            if entities:
                browser_stage(run, cfg, entities, today or dt.date.today(), data_dir)
    return run.exit_code
