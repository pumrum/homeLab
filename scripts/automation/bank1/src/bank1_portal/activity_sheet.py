"""Write one account's checked activity to its Google Sheet tab.

Every write is a full snapshot of the range, so it replaces everything from the start cell
down, across exactly the columns the activity fills: a shorter download can't leave old rows
below it, and columns beside the block are never touched. No header row is written, and every
value goes in as literal text, as the old process pasted it.
"""

import logging
from collections.abc import Callable

from automation_common.secrets import SecretMasker
from automation_common.sheets import Worksheet
from bank1_portal.activity_parse import Activity
from bank1_portal.config import Destination

OpenWorksheet = Callable[[str, str, SecretMasker], Worksheet]

log = logging.getLogger(__name__)


def write_activity(destination: Destination, activity: Activity, masker: SecretMasker,
                   open_ws: OpenWorksheet) -> None:
    """Replace the account's block. Raises SheetError, whose message holds no private value."""
    ws = open_ws(destination.spreadsheet_id, destination.tab, masker)
    ws.replace_block(destination.start_cell, activity.rows, width=activity.width)
    log.info("activity: wrote %d row(s) to the %s workbook", len(activity.rows), destination.sheet)
