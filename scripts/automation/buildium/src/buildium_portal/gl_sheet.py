"""Writing the finished general ledger to the sheet."""

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from automation_common.run import Run, Status
from automation_common.secrets import SecretMasker
from automation_common.sheets import Cell, Worksheet
from buildium_portal.config import Dashboard, Gl
from buildium_portal.gl_parse import CASH_VIEW_COLUMNS, MASTER_COLUMNS
from buildium_portal.stages import why

OpenWorksheet = Callable[[str, str, SecretMasker], Worksheet]

MASTER_START_CELL = "A2"  # row 1 holds the maintainer's headers
ENTRY_DATE_PATTERN = "yyyy-mm-dd"
EXPORTED_DATE_PATTERN = "m/d/yyyy"  # how the export shows a date we don't reformat
UPDATED_PATTERN = "yyyy-mm-dd hh:mm:ss"  # the master's update time on the dashboard
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


def tabs_to_write(gl: Gl) -> list[tuple[str, str]]:
    """The name and tab of everything a run writes: each entity that's on, the master, then
    the dashboard if there is one."""
    dashboard = [("dashboard", gl.dashboard.tab)] if gl.dashboard else []
    return ([(key, entity.tab) for key, entity in gl.entities.items() if entity.write_tab]
            + [("master", gl.master_tab)] + dashboard)


def check_sheet(run: Run, gl: Gl, open_ws: OpenWorksheet) -> bool:
    """Open every tab a run will write, before anything slow starts. False if one won't open.

    Opening only reads, so it catches a wrong or deleted spreadsheet, a missing tab, or a sheet
    that isn't shared. A sheet shared as Viewer opens, and fails only when it's written.
    """
    tabs = tabs_to_write(gl)
    for name, tab in tabs:
        try:
            open_ws(gl.spreadsheet_id, tab, run.masker)
        except Exception as e:  # noqa: BLE001 - recorded, and the run stops before the site
            run.summary.record("sheet check", name, Status.FAILED,
                               f"not started, so nothing was exported: {why(e)}")
            return False
    run.summary.record("sheet check", "all tabs", Status.OK, f"opened {len(tabs)} tab(s)")
    return True


def sheet_stage(run: Run, gl: Gl, views: dict[str, SheetRows], master: SheetRows,
                open_ws: OpenWorksheet) -> bool:
    """Replace the master, and the rows of each entity whose gl_write_tab is on.

    Every tab is opened first, so a wrong tab name changes nothing. After that a failed write
    doesn't stop the others: each tab is a full snapshot, and the report names what failed.
    Returns whether the master was written.
    """
    on = {key: rows for key, rows in views.items() if gl.entities[key].write_tab}
    targets = _targets(gl, on, master)
    opened: list[tuple[_Target, Worksheet]] = []
    for target in targets:
        try:
            opened.append((target, open_ws(gl.spreadsheet_id, target.tab, run.masker)))
        except Exception as e:  # noqa: BLE001 - recorded, and nothing has been written yet
            run.summary.record("sheet", target.name, Status.FAILED, f"nothing written: {why(e)}")
            return False
    master_written = False
    for target, ws in opened:
        try:
            ws.replace_block(target.start_cell, target.rows, target.date_columns, target.trim)
        except Exception as e:  # noqa: BLE001 - one tab's failure must not stop the others
            run.summary.record("sheet", target.name, Status.FAILED, why(e))
        else:
            run.summary.record("sheet", target.name, Status.OK, f"wrote {len(target.rows)} rows")
            master_written = master_written or target.name == "master"
    return master_written


def stamp_stage(run: Run, spreadsheet_id: str, dashboard: Dashboard, started: dt.datetime,
                open_ws: OpenWorksheet) -> None:
    """Write the run's start time to the dashboard, as a date and time the sheet can compare.

    It's the time in the run folder's name, not the time of the write, so the sheet and the
    run folder can be matched up.
    """
    shown = started.strftime("%Y-%m-%d %H:%M:%S")
    try:
        ws = open_ws(spreadsheet_id, dashboard.tab, run.masker)
        ws.write_cell(dashboard.updated_cell, started, UPDATED_PATTERN)
    except Exception as e:  # noqa: BLE001 - recorded; the master is already written
        run.summary.record("sheet", "dashboard", Status.FAILED,
                           f"the master was written, but not its update time: {why(e)}")
    else:
        run.summary.record("sheet", "dashboard", Status.OK, f"master updated {shown}")
