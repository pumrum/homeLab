"""Writing the finished general ledger to the sheet."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from automation_common.run import Run, Status
from automation_common.secrets import SecretMasker
from automation_common.sheets import Cell, Worksheet
from buildium_portal.config import Gl
from buildium_portal.gl_parse import CASH_VIEW_COLUMNS, MASTER_COLUMNS
from buildium_portal.stages import why

OpenWorksheet = Callable[[str, str, SecretMasker], Worksheet]

MASTER_START_CELL = "A2"  # row 1 holds the maintainer's headers
ENTRY_DATE_PATTERN = "yyyy-mm-dd"
EXPORTED_DATE_PATTERN = "m/d/yyyy"  # how the export shows a date we don't reformat
VIEW_DATES = {CASH_VIEW_COLUMNS.index("EntryDate"): ENTRY_DATE_PATTERN}
MASTER_DATES = {MASTER_COLUMNS.index("EntryDate"): ENTRY_DATE_PATTERN,
                MASTER_COLUMNS.index("EFTResultDate"): EXPORTED_DATE_PATTERN}
SheetRows = Sequence[Sequence[Cell]]


@dataclass(frozen=True)
class _Target:
    name: str
    tab: str
    start_cell: str
    rows: SheetRows
    date_columns: Mapping[int, str]
    trim: bool


def _targets(gl: Gl, views: dict[str, SheetRows], master: SheetRows) -> list[_Target]:
    targets = [_Target(key, gl.entities[key].tab, gl.entities[key].start_cell, rows, VIEW_DATES,
                       trim=False)
               for key, rows in views.items()]
    # Only the master shrinks with its data. An entity's tab holds other things below its rows
    targets.append(_Target("master", gl.master_tab, MASTER_START_CELL, master, MASTER_DATES,
                           trim=True))
    return targets


def sheet_stage(run: Run, gl: Gl, views: dict[str, SheetRows], master: SheetRows,
                open_ws: OpenWorksheet) -> None:
    """Replace each entity's rows and the master.

    Every tab is opened first, so a wrong tab name changes nothing. After that a failed write
    doesn't stop the others: each tab is a full snapshot, and the report names what failed.
    """
    targets = _targets(gl, views, master)
    opened: list[tuple[_Target, Worksheet]] = []
    for target in targets:
        try:
            opened.append((target, open_ws(gl.spreadsheet_id, target.tab, run.masker)))
        except Exception as e:  # noqa: BLE001 - recorded, and nothing has been written yet
            run.summary.record("sheet", target.name, Status.FAILED, f"nothing written: {why(e)}")
            return
    for target, ws in opened:
        try:
            ws.replace_block(target.start_cell, target.rows, target.date_columns, target.trim)
        except Exception as e:  # noqa: BLE001 - one tab's failure must not stop the others
            run.summary.record("sheet", target.name, Status.FAILED, why(e))
        else:
            run.summary.record("sheet", target.name, Status.OK, f"wrote {len(target.rows)} rows")
