"""Write one account's checked activity to its Google Sheet tab.

Every write is a full snapshot of the range, so it replaces everything from the start cell
down, across exactly the columns the activity fills: a shorter download can't leave old rows
below it, and columns beside the block are never touched. No header row is written.

Each value goes in as pasting the values by hand would store it, but only where that can't
change it: plain numbers become numbers, and MM/DD/YYYY dates become dates shown the same way.
Anything else stays text exactly as exported, such as 007, which a paste would turn into 7, or
a description starting with =, which a paste would run as a formula. The cells keep their other
formatting, as a paste of values does.

A workbook can also have an update cell. Once every account this run writes to it has written,
that cell gets the run's start time, so it always says when the whole workbook was refreshed.
"""

import datetime as dt
import logging
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from decimal import Decimal

from automation_common.secrets import SecretMasker
from automation_common.sheets import Cell, Worksheet
from bank1_portal.activity_parse import DATE_FORMAT, Activity
from bank1_portal.config import Account, Destination, Workbook

OpenWorksheet = Callable[[str, str, SecretMasker], Worksheet]

DATE_PATTERN = "mm/dd/yyyy"  # as the export shows its dates
UPDATED_PATTERN = "yyyy-mm-dd hh:mm:ss"  # a workbook's update time, as buildium shows it
_PLAIN_NUMBER = re.compile(r"-?(?:0|[1-9]\d{0,14})(?:\.\d{1,6})?")
_SLASHED_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")

log = logging.getLogger(__name__)


def sheet_cell(value: str) -> Cell:
    """What a paste would store, but only where the value can't change."""
    if _PLAIN_NUMBER.fullmatch(value):
        return Decimal(value) if "." in value else int(value)
    if _SLASHED_DATE.fullmatch(value):
        try:
            return dt.datetime.strptime(value, DATE_FORMAT).date()
        except ValueError:
            return value
    return value


def sheet_block(rows: Sequence[Sequence[str]]) -> tuple[list[list[Cell]], dict[int, str]]:
    """The rows as sheet values, and the date pattern for each column holding a date."""
    cells = [[sheet_cell(value) for value in row] for row in rows]
    dates = {position for row in cells for position, value in enumerate(row)
             if isinstance(value, dt.date)}
    return cells, dict.fromkeys(sorted(dates), DATE_PATTERN)


def write_activity(destination: Destination, activity: Activity, masker: SecretMasker,
                   open_ws: OpenWorksheet) -> None:
    """Replace the account's block. Raises SheetError, whose message holds no private value."""
    cells, date_columns = sheet_block(activity.rows)
    ws = open_ws(destination.spreadsheet_id, destination.tab, masker)
    ws.replace_block(destination.start_cell, cells, date_columns, width=activity.width)
    log.info("activity: wrote %d row(s) to the %s workbook", len(cells), destination.sheet)


def workbooks_to_stamp(sheets: Mapping[str, Workbook], accounts: Sequence[Account],
                       ok: Collection[str]) -> list[tuple[Workbook, bool]]:
    """Each workbook with an update cell that this run's accounts write to, and whether every
    one of those accounts wrote (its label is in `ok`). Only then does its time change."""
    found = []
    for workbook in sheets.values():
        if workbook.updated_tab is None:
            continue
        writers = [account.label for account in accounts
                   if account.activity and account.activity.destination
                   and account.activity.destination.sheet == workbook.name]
        if writers:
            found.append((workbook, all(label in ok for label in writers)))
    return found


def stamp_workbook(workbook: Workbook, started: dt.datetime, masker: SecretMasker,
                   open_ws: OpenWorksheet) -> None:
    """Write the run's start time to the workbook's update cell, as a date and time the sheet
    can compare. It's the time in the run folder's name, so the two can be matched up."""
    assert workbook.updated_tab is not None and workbook.updated_cell is not None
    ws = open_ws(workbook.spreadsheet_id, workbook.updated_tab, masker)
    ws.write_cell(workbook.updated_cell, started, UPDATED_PATTERN)
