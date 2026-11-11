"""The parts of a run that every command in this package shares."""

import logging
from collections.abc import Callable
from typing import Protocol

from playwright.sync_api import BrowserContext

from automation_common.browser import first_page, save_page_evidence
from automation_common.run import Run, Status, Summary, pass_fail
from buildium_portal.config import Entity

log = logging.getLogger(__name__)


class HasEntities(Protocol):
    @property
    def entities(self) -> tuple[Entity, ...]: ...


def why(e: Exception) -> str:
    """One short line for the summary. The run masks private values when it's written."""
    first_line = (str(e).splitlines() or [""])[0][:200]
    return f"{type(e).__name__}: {first_line}"


def chosen_entities(run: Run, cfg: HasEntities, key: str | None) -> tuple[Entity, ...]:
    if key is None:
        return cfg.entities
    chosen = tuple(entity for entity in cfg.entities if entity.key == key)
    if not chosen:
        run.summary.record("config", key, Status.FAILED, "no entity with this key in the config")
    return chosen


def dump_observer(run: Run, ctx: BrowserContext, entity: Entity) -> Callable[[str], None]:
    """--dump: save a page's structure at each named step. Best effort, and nothing typed."""
    def observe(step: str) -> None:
        try:
            page = first_page(ctx)
            prefix = f"dump_{entity.key}_{step}"
            save_page_evidence(page, run.run_dir, prefix)
            (run.run_dir / f"{prefix}.html").write_text(page.content(), encoding="utf-8")
            log.info("dump: saved the %s page for %s", step, entity.key)
        except Exception as e:  # noqa: BLE001 - --dump's copies must never stop the run
            log.warning("dump: saving the %s page failed: %s", step, why(e))
    return observe


def final_event(summary: Summary) -> str:
    """Pass or fail, naming MFA when that's what stopped the run, since it takes a person."""
    if any(item.stage == "login" and item.status is Status.FAILED and "MFA" in item.detail
           for item in summary.items):
        return "mfa_failed"
    return pass_fail(summary)
