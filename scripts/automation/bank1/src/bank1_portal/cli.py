"""What every command in this package does around its own work: load the config and hide its
private values, choose the accounts, save --dump copies, and pick the final notification."""

import logging
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext

from automation_common.browser import first_page, save_page_evidence
from automation_common.config import ConfigError
from automation_common.run import Run, Status, Summary, pass_fail
from bank1_portal.config import Account, Config, load_config

TIMED_OUT = "verification timed out"

log = logging.getLogger(__name__)

Observer = Callable[[str], None]


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


def _mask(run: Run, cfg: Config) -> None:
    # Private: never in logs. The labels stay readable, since the report needs them. A bare
    # suffix isn't masked: four digits would also blank out dates and times in every log.
    # It's never logged, and no message repeats it
    run.masker.add(cfg.site.login_url)
    run.masker.add(cfg.site.name_in_files)
    for spreadsheet_id in cfg.sheets.values():
        run.masker.add(spreadsheet_id)
    for account in cfg.accounts:
        run.masker.add(account.statements_url)
        if account.match != account.suffix:
            run.masker.add(account.match)
        if account.activity is not None:
            run.masker.add(account.activity.url)
            if account.activity.destination is not None:
                run.masker.add(account.activity.destination.tab)


def load_stage(run: Run, data_dir: Path) -> Config | None:
    try:
        cfg = load_config(data_dir / "config.toml")
    except ConfigError as e:
        run.summary.record("config", "config.toml", Status.FAILED, str(e))
        return None
    run.notifications = cfg.notifications
    _mask(run, cfg)
    log.info("config OK: %d account(s): %s", len(cfg.accounts),
             ", ".join(account.label for account in cfg.accounts))
    return cfg


def choose(run: Run, accounts: tuple[Account, ...], label: str | None, dump: bool,
           missing: str = "no account with this label in the config") -> tuple[Account, ...]:
    """The accounts a run works on: one by label, the first of each kind, or all."""
    if label is not None:
        chosen = tuple(account for account in accounts if account.label == label)
        if not chosen:
            run.summary.record("config", label, Status.FAILED, missing)
        return chosen
    if dump:
        firsts: dict[str, Account] = {}
        for account in accounts:
            firsts.setdefault(account.kind, account)
        return tuple(firsts.values())
    return accounts


def dump_observer(run: Run, ctx: BrowserContext, name: str) -> Observer:
    """--dump: save a page's outline and screenshot at each named step. Best effort."""
    def observe(step: str) -> None:
        try:
            save_page_evidence(first_page(ctx), run.run_dir, f"dump_{name}_{step}")
            log.info("dump: saved the %s page for %s", step, name)
        except Exception as e:  # noqa: BLE001 - --dump's copies must never stop the run
            log.warning("dump: saving the %s page failed: %s", step, why(e))
    return observe


def ignore(_: str) -> None:
    pass
