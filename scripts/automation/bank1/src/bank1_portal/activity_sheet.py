"""Write one account's checked activity to its Google Sheet tab.

Every write is a full snapshot of the range, so it replaces everything from the start cell
down, across exactly the columns the activity fills: a shorter download can't leave old rows
below it, and columns beside the block are never touched. No header row is written.

Each value goes in as pasting the values by hand would store it, but only where that can't
change it: plain numbers become numbers, and MM/DD/YYYY dates become dates shown the same way.
Anything else stays text exactly as exported, such as 007, which a paste would turn into 7, or
a description starting with =, which a paste would run as a formula. The cells keep their other
formatting, as a paste of values does.
"""

import datetime as dt
import logging
import re
from collections.abc import Callable, Sequence
from decimal import Decimal

from automation_common.secrets import SecretMasker
from automation_common.sheets import Cell, Worksheet
from bank1_portal.activity_parse import DATE_FORMAT, Activity
from bank1_portal.config import Destination

OpenWorksheet = Callable[[str, str, SecretMasker], Worksheet]

DATE_PATTERN = "mm/dd/yyyy"  # as the export shows its dates
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
