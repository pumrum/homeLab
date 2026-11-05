"""Google Sheets with a service account: read a worksheet, append rows, or
replace one column from a given cell down.

The service account key is the encrypted secret file
`automation/google-service-account` (see secrets.py; import it with
tools/import_secret_file.py). Share each spreadsheet with the service
account's email as an Editor. The spreadsheet ID is private config: it is
added to the masker and never written to logs or error messages.
"""

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import gspread
from gspread.exceptions import IncorrectCellLabel
from gspread.utils import DateTimeOption, ValueInputOption, ValueRenderOption, a1_to_rowcol

from automation_common.secrets import SecretMasker, read_secret_file

log = logging.getLogger(__name__)

SERVICE_ACCOUNT_SECRET = "automation/google-service-account"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # sheets only, no Drive


class SheetError(Exception):
    """A sheet can't be opened, read or written. The message says why."""


class Worksheet(Protocol):
    """What an automation needs from a worksheet (tests use a fake)."""

    def read_rows(self, columns: str) -> list[list[Any]]: ...

    def append_rows(self, rows: Sequence[Sequence[Any]]) -> None: ...

    def replace_from_cell(self, start_cell: str, lines: Sequence[str]) -> None: ...


def replace_requests(
    sheet_id: int, row_count: int, start_cell: str, lines: Sequence[str]
) -> list[dict[str, Any]]:
    """The API requests that replace one column from `start_cell` to the tab's bottom.

    Cells above `start_cell` and in other columns aren't part of the range. Each
    line becomes a literal text value, like paste values: nothing is reinterpreted
    as a number, date, or formula. Only values change, not cell formatting. Cells in
    the range that `lines` doesn't reach are cleared. The tab grows if it's too short.
    """
    try:
        row, col = a1_to_rowcol(start_cell)
    except IncorrectCellLabel:
        raise SheetError(f'"{start_cell}" is not a cell like B5') from None
    needed = row - 1 + len(lines)
    requests: list[dict[str, Any]] = []
    if needed > row_count:
        requests.append({"appendDimension": {
            "sheetId": sheet_id, "dimension": "ROWS", "length": needed - row_count,
        }})
    requests.append({"updateCells": {
        "range": {
            "sheetId": sheet_id,
            "startRowIndex": row - 1,
            "endRowIndex": max(row_count, needed),
            "startColumnIndex": col - 1,
            "endColumnIndex": col,
        },
        "rows": [{"values": [{"userEnteredValue": {"stringValue": line}}]} for line in lines],
        "fields": "userEnteredValue",
    }})
    return requests


def load_service_account(
    masker: SecretMasker, read: Callable[[str], bytes | None] = read_secret_file
) -> dict[str, Any]:
    """Return the decrypted service account key, with its private parts masked."""
    data = read(SERVICE_ACCOUNT_SECRET)
    if data is None:
        raise SheetError(
            f"no Google service account key for this Windows user; import it with "
            f"tools\\import_secret_file.py {SERVICE_ACCOUNT_SECRET} <key.json>"
        )
    try:
        info = json.loads(data)
    except ValueError as e:
        raise SheetError("the stored Google service account key is not valid JSON") from e
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise SheetError("the stored Google key is not a service account key")
    for key in ("private_key", "private_key_id", "client_email"):
        if isinstance(info.get(key), str):
            masker.add(info[key])
    return info


class GoogleWorksheet:
    """A gspread worksheet behind the Worksheet protocol."""

    def __init__(self, ws: gspread.Worksheet) -> None:
        self._ws = ws
        self._row_count = ws.row_count  # kept current, since a replace can grow the tab

    def read_rows(self, columns: str) -> list[list[Any]]:
        """All rows in `columns` (e.g. "A:D"): numbers as numbers, dates as serial numbers."""
        try:
            return self._ws.get_all_values(
                range_name=columns,
                value_render_option=ValueRenderOption.unformatted,
                date_time_render_option=DateTimeOption.serial_number,
            )
        except gspread.exceptions.APIError as e:
            raise _api_error("reading", e) from None

    def append_rows(self, rows: Sequence[Sequence[Any]]) -> None:
        """Append after the last row of the table, as if typed (dates become dates)."""
        try:
            self._ws.append_rows(
                [list(r) for r in rows],
                value_input_option=ValueInputOption.user_entered,
                table_range="A1",
            )
        except gspread.exceptions.APIError as e:
            raise _api_error("appending to", e) from None

    def replace_from_cell(self, start_cell: str, lines: Sequence[str]) -> None:
        """Replace one column from `start_cell` down (see replace_requests).

        It's a single request, so Google applies all of it or none of it: a failure
        leaves the old values in place.
        """
        requests = replace_requests(self._ws.id, self._row_count, start_cell, lines)
        try:
            self._ws.spreadsheet.batch_update({"requests": requests})
        except gspread.exceptions.APIError as e:
            raise _api_error("writing to", e) from None
        self._row_count = requests[-1]["updateCells"]["range"]["endRowIndex"]


def _api_error(action: str, e: gspread.exceptions.APIError) -> SheetError:
    # The API's own message can include the spreadsheet ID, so keep only the code
    status = getattr(e.response, "status_code", "?")
    hint = " (is the sheet shared with the service account as an Editor?)" if status in (
        403, 404) else ""
    return SheetError(f"Google Sheets error {status} {action} the sheet{hint}")


def open_worksheet(
    spreadsheet_id: str,
    worksheet: str,
    masker: SecretMasker,
    load: Callable[[SecretMasker], dict[str, Any]] = load_service_account,
) -> GoogleWorksheet:
    """Open one tab of a spreadsheet the service account has been shared on."""
    masker.add(spreadsheet_id)
    info = load(masker)
    try:
        client = gspread.service_account_from_dict(info, scopes=SCOPES)
        return GoogleWorksheet(client.open_by_key(spreadsheet_id).worksheet(worksheet))
    except gspread.exceptions.SpreadsheetNotFound:
        raise SheetError(
            "spreadsheet not found: check spreadsheet_id and that the sheet is shared "
            "with the service account as an Editor"
        ) from None
    except gspread.exceptions.WorksheetNotFound:
        raise SheetError(f'no tab named "{worksheet}" in the spreadsheet') from None
    except gspread.exceptions.APIError as e:
        raise _api_error("opening", e) from None
