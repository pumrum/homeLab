"""invoices-sync: config, run folder, per-entity fetch over SFTP, sheet, summary.

    uv run invoices-sync            normal run: paste each entity's file into its
                                    block on the sheet, replacing what's there
    uv run invoices-sync --dump     download the files to the run folder and report
                                    what was read; writes nothing to the sheet
"""

import argparse
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from automation_common.config import ConfigError, process_dir
from automation_common.notify import send_notification
from automation_common.run import Notifier, Run, Status
from automation_common.secrets import SecretMasker, get_secret
from automation_common.sheets import Worksheet, open_worksheet
from invoices_sync import PROCESS
from invoices_sync.config import Config, Entity, Sftp, load_config
from invoices_sync.fetch import FileSource, open_source
from invoices_sync.parse import parse_lines
from invoices_sync.store import save_raw

KEY_SECRET = f"{PROCESS}/ssh-key"

GetSecret = Callable[[str, SecretMasker], str | None]
OpenSource = Callable[[Sftp, str, Path], AbstractContextManager[FileSource]]
OpenWorksheet = Callable[[str, str, SecretMasker], Worksheet]

log = logging.getLogger(__name__)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="invoices-sync", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", action="store_true",
                    help="download the files to the run folder; write nothing to the sheet")
    return ap.parse_args(argv)


def _why(e: Exception) -> str:
    """One short line for the summary. The run masks private values when it's written."""
    first_line = (str(e).splitlines() or [""])[0][:200]
    return f"{type(e).__name__}: {first_line}"


def _fail_all(run: Run, stage: str, entities: list[Entity], detail: str) -> None:
    for entity in entities:
        run.summary.record(stage, entity.code, Status.FAILED, detail)


def load_stage(run: Run, data_dir: Path) -> Config | None:
    try:
        cfg = load_config(data_dir / "config.toml")
    except ConfigError as e:
        run.summary.record("config", "config.toml", Status.FAILED, str(e))
        return None
    # Private: never in logs. The file names and tab name aren't secret, so they stay readable
    for value in (cfg.sftp.host, cfg.sftp.user, cfg.sftp.folder, cfg.sheet.spreadsheet_id):
        run.masker.add(value)
    log.info("config OK: %d of %d entities enabled: %s; sheet tab \"%s\"",
             len(cfg.enabled_entities), len(cfg.entities),
             ", ".join(e.code for e in cfg.enabled_entities), cfg.sheet.worksheet)
    return cfg


def fetch_entity(run: Run, source: FileSource, entity: Entity) -> list[str] | None:
    """Download, keep, and parse one file. Records the result; never raises."""
    step = "download"
    try:
        raw = source.read(entity.file)
        step = "parse"
        lines = parse_lines(raw)
    except Exception as e:  # noqa: BLE001 - one entity's failure must not stop the others
        if step == "parse":
            _keep_raw(run, entity, raw)
        run.summary.record("fetch", entity.code, Status.FAILED, f"{step}: {_why(e)}")
        return None
    _keep_raw(run, entity, raw)
    run.summary.record("fetch", entity.code, Status.OK, f"{len(lines)} line(s), {len(raw)} bytes")
    return lines


def _keep_raw(run: Run, entity: Entity, raw: bytes) -> None:
    """Saving the debugging copy can fail the run, but doesn't stop the sheet write."""
    try:
        save_raw(run.run_dir, entity.file, raw)
    except OSError as e:
        run.summary.record("files", entity.code, Status.FAILED, f"saving a copy: {_why(e)}")


def fetch_stage(run: Run, cfg: Config, data_dir: Path,
                get_key: GetSecret = get_secret,
                open_src: OpenSource = open_source) -> dict[str, list[str]]:
    """One SFTP session for all entities. Returns the lines of each file that's usable."""
    entities = cfg.enabled_entities
    key = get_key(KEY_SECRET, run.masker)
    if key is None:
        _fail_all(run, "fetch", entities, f"no {KEY_SECRET} in Credential Manager")
        return {}
    fetched: dict[str, list[str]] = {}
    done: set[str] = set()
    try:
        with open_src(cfg.sftp, key, data_dir / "known_hosts") as source:
            for entity in entities:
                lines = fetch_entity(run, source, entity)
                done.add(entity.code)
                if lines is not None:
                    fetched[entity.code] = lines
    except Exception as e:  # noqa: BLE001 - record and let the summary report it
        run.summary.record("sftp", "session", Status.FAILED, _why(e))
        _fail_all(run, "fetch", [x for x in entities if x.code not in done],
                  "not processed: SFTP session failed")
    return fetched


def sheet_stage(run: Run, cfg: Config, fetched: dict[str, list[str]],
                open_ws: OpenWorksheet = open_worksheet) -> None:
    """Replace each fetched entity's block, one request per entity. Never raises."""
    entities = [e for e in cfg.enabled_entities if e.code in fetched]
    if not entities:
        return
    try:
        ws = open_ws(cfg.sheet.spreadsheet_id, cfg.sheet.worksheet, run.masker)
    except Exception as e:  # noqa: BLE001 - recorded; running again fixes it
        _fail_all(run, "sheet", entities, f"opening the sheet: {_why(e)}")
        return
    for entity in entities:
        lines = fetched[entity.code]
        try:
            ws.replace_from_cell(entity.start_cell, lines)
        except Exception as e:  # noqa: BLE001 - one block's failure must not stop the others
            run.summary.record("sheet", entity.code, Status.FAILED, _why(e))
            continue
        run.summary.record("sheet", entity.code, Status.OK,
                           f"wrote {len(lines)} line(s) from {entity.start_cell}")


def main(argv: list[str] | None = None, *,
         notifier: Notifier = send_notification,
         get_key: GetSecret = get_secret,
         open_src: OpenSource = open_source,
         open_ws: OpenWorksheet = open_worksheet) -> int:
    args = parse_args(argv)
    data_dir = process_dir(PROCESS)
    with Run.start(PROCESS, data_dir, notifier=notifier) as run:
        cfg = load_stage(run, data_dir)
        if cfg is not None:
            fetched = fetch_stage(run, cfg, data_dir, get_key, open_src)
            if args.dump:
                log.info("--dump: copies are in %s; nothing written to the sheet",
                         run.run_dir / "files")
            else:
                sheet_stage(run, cfg, fetched, open_ws)
    return run.exit_code
