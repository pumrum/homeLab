"""innago-invoices: sign in, download each unit's invoice, name it from its text, save it.

    uv run innago-invoices             normal run: save invoices under data\\invoices
    uv run innago-invoices --dump      save page structure, download to the run folder,
                                       and report the names; saves nothing under data
    uv run innago-invoices --capture   by hand only: you sign in and click around while
                                       the page structure is saved to the run folder
"""

import argparse
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from playwright.sync_api import Page

from automation_common.browser import save_page_evidence
from automation_common.config import ConfigError, process_dir
from automation_common.notify import send_notification
from automation_common.run import Notifier, Run, Status
from innago_portal import PROCESS
from innago_portal.capture import capture
from innago_portal.config import Config, load_config
from innago_portal.invoices_fetch import InvoiceList
from innago_portal.login import Observer, Step
from innago_portal.parse import parse_invoice, pdf_text
from innago_portal.session import profile_dir, signed_in_page
from innago_portal.store import Outcome, save_invoice

COMMAND = "innago-invoices"

log = logging.getLogger(__name__)


class InvoiceSource(Protocol):
    def open(self) -> int: ...
    def download(self, index: int, dest: Path) -> None: ...
    def save_evidence(self, run_dir: Path, prefix: str) -> None: ...
    def save_structure(self, run_dir: Path, prefix: str) -> None: ...


OpenSession = Callable[..., AbstractContextManager[Page]]
OpenSource = Callable[[Page, str, int], InvoiceSource]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=COMMAND, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dump", action="store_true",
                      help="save page structure and report names; save nothing under data")
    mode.add_argument("--capture", action="store_true",
                      help="by hand only: save page structure while you sign in and click around")
    return ap.parse_args(argv)


def _why(e: Exception) -> str:
    """One short line for the summary. The run masks private values when it's written."""
    first_line = (str(e).splitlines() or [""])[0][:200]
    return f"{type(e).__name__}: {first_line}"


def load_stage(run: Run, data_dir: Path) -> Config | None:
    try:
        cfg = load_config(data_dir / "config.toml")
    except ConfigError as e:
        run.summary.record("config", "config.toml", Status.FAILED, str(e))
        return None
    # Private: never in logs. The unit numbers stay readable, since the report needs them
    for url in (cfg.site.login_url, cfg.site.app_url, cfg.invoices.page_url):
        run.masker.add(url)
    log.info("config OK: %d unit(s): %s", len(cfg.invoices.units), ", ".join(cfg.invoices.units))
    return cfg


def _fail_units(run: Run, units: tuple[str, ...], detail: str) -> None:
    for unit in units:
        run.summary.record("invoice", unit, Status.FAILED, detail)


def process_row(run: Run, source: InvoiceSource, index: int, cfg: Config, data_dir: Path,
                seen: set[str], dump: bool) -> None:
    """Download, name, and save one invoice. Records the result; never raises."""
    label = f"row {index + 1}"
    step = "download"
    download = run.run_dir / "downloads" / f"invoice_{index + 1:02d}.pdf"
    try:
        source.download(index, download)
        step = "read"
        text = pdf_text(download)
        invoice = parse_invoice(text)
        label = invoice.unit
        if invoice.unit not in cfg.invoices.units:
            raise ValueError(f"unit {invoice.unit} isn't in the config's list of units")
        if invoice.unit in seen:
            raise ValueError(f"a second invoice for this unit ({invoice.number}); not saved")
        seen.add(invoice.unit)
        if dump:
            run.summary.record("invoice", label, Status.OK, f"would save {invoice.file_name}")
            return
        step = "save"
        outcome = save_invoice(download, invoice, text, data_dir / "data" / "invoices",
                               run.run_dir / "replaced")
    except Exception as e:  # noqa: BLE001 - one invoice's failure must not stop the others
        if step == "download":
            source.save_evidence(run.run_dir, run.next_failure_prefix())
        # Anything not saved stays in the run folder's downloads for a look
        run.summary.record("invoice", label, Status.FAILED, f"{step}: {_why(e)}")
        return
    status = Status.UNCHANGED if outcome is Outcome.UNCHANGED else Status.OK
    run.summary.record("invoice", label, status, f"{outcome} {invoice.file_name}")


def download_stage(run: Run, source: InvoiceSource, cfg: Config, data_dir: Path,
                   dump: bool) -> None:
    """Every row in the site's default view, then a check that every unit was covered."""
    units = cfg.invoices.units
    try:
        count = source.open()
    except Exception as e:  # noqa: BLE001 - recorded; the units below all fail with it
        source.save_evidence(run.run_dir, run.next_failure_prefix())
        _fail_units(run, units, f"invoice list: {_why(e)}")
        if dump:
            _save_structure(run, source)
        return
    if dump:
        _save_structure(run, source)
    seen: set[str] = set()
    for index in range(count):
        process_row(run, source, index, cfg, data_dir, seen, dump)
    for unit in units:
        if unit not in seen:
            run.summary.record("invoice", unit, Status.FAILED,
                               "no usable invoice in the site's default view")


def _save_structure(run: Run, source: InvoiceSource) -> None:
    try:
        source.save_structure(run.run_dir, "dump_invoices")
    except Exception as e:  # noqa: BLE001 - --dump's copies are best effort
        log.warning("saving the page structure failed: %s", _why(e))


def _dump_observer(run: Run) -> Callable[[Page], Observer]:
    """--dump: save each sign-in page's structure as it's reached. Saves nothing typed."""
    def make(page: Page) -> Observer:
        count = [0]

        def observe(step: Step) -> None:
            count[0] += 1
            save_page_evidence(page, run.run_dir, f"dump_login_{count[0]:02d}")
            log.info("dump: sign-in page %d is step '%s'", count[0], step)
        return observe
    return make


def browser_stage(run: Run, cfg: Config, data_dir: Path, dump: bool,
                  open_session: OpenSession = signed_in_page,
                  open_source: OpenSource = InvoiceList) -> None:
    """One signed-in session for all invoices. A failed sign-in fails every unit."""
    reached = False
    try:
        with open_session(run, cfg.site, data_dir,
                          make_observer=_dump_observer(run) if dump else None) as page:
            run.summary.record("login", "site", Status.OK)
            reached = True
            source = open_source(page, cfg.invoices.page_url, len(cfg.invoices.units))
            download_stage(run, source, cfg, data_dir, dump)
    except Exception as e:  # noqa: BLE001 - record and let the summary report it
        run.summary.record("browser" if reached else "login", "site", Status.FAILED, _why(e))
        if not reached:
            _fail_units(run, cfg.invoices.units, "not processed: sign-in failed")


def main(argv: list[str] | None = None, *, notifier: Notifier = send_notification) -> int:
    args = parse_args(argv)
    data_dir = process_dir(PROCESS)
    with Run.start(PROCESS, data_dir, notifier=notifier, title=COMMAND) as run:
        cfg = load_stage(run, data_dir)
        if cfg is not None:
            if args.capture:
                capture(run, cfg.site, profile_dir(data_dir))
            else:
                browser_stage(run, cfg, data_dir, args.dump)
    return run.exit_code
