"""Google Sheets with a service account: read a worksheet, append rows, replace one column or
a whole block from a given cell down, or write one cell.

The service account key is the encrypted secret file
`automation/google-service-account` (see secrets.py; import it with
tools/import_secret_file.py). Share each spreadsheet with the service
account's email as an Editor. The spreadsheet ID is private config: it is
added to the masker and never written to logs or error messages.
"""

import datetime as dt
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import gspread
from gspread.exceptions import IncorrectCellLabel
from gspread.utils import DateTimeOption, ValueInputOption, ValueRenderOption, a1_to_rowcol

from automation_common.secrets import SecretMasker, read_secret_file

log = logging.getLogger(__name__)

SERVICE_ACCOUNT_SECRET = "automation/google-service-account"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # sheets only, no Drive
BLOCK_CHUNK_ROWS = 2_000  # rows in one write, so a big block stays a reasonable request
_SHEETS_DAY_ZERO = dt.date(1899, 12, 30)
_SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class Formula:
    """A cell the sheet calculates. Only code can make one: plain text never runs as a formula."""

    text: str


Cell = str | bool | int | float | Decimal | dt.date | Formula | None


class SheetError(Exception):
    """A sheet can't be opened, read or written. The message says why."""


class Worksheet(Protocol):
    """What an automation needs from a worksheet (tests use a fake)."""

    def read_rows(self, columns: str) -> list[list[Any]]: ...

    def append_rows(self, rows: Sequence[Sequence[Any]]) -> None: ...

    def replace_from_cell(self, start_cell: str, lines: Sequence[str]) -> None: ...

    def replace_block(self, start_cell: str, rows: Sequence[Sequence[Cell]],
                      date_columns: Mapping[int, str] | None = None,
                      trim: bool = False, width: int | None = None) -> None: ...

    def write_cell(self, cell: str, value: Cell, pattern: str | None = None) -> None: ...


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


def _block_cell(value: Cell) -> dict[str, Any]:
    """Text stays literal text, so nothing in it can run as a formula. "" and None are blank."""
    if value is None or value == "":
        return {}
    if isinstance(value, Formula):
        return {"userEnteredValue": {"formulaValue": value.text}}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, dt.datetime):  # before date, which it's a kind of
        # The time as shown on this machine's clock, to the second
        local = value.replace(tzinfo=None, microsecond=0)
        days = (local.date() - _SHEETS_DAY_ZERO).days
        seconds = local.hour * 3600 + local.minute * 60 + local.second
        return {"userEnteredValue": {"numberValue": days + seconds / _SECONDS_PER_DAY}}
    if isinstance(value, dt.date):
        return {"userEnteredValue": {"numberValue": (value - _SHEETS_DAY_ZERO).days}}
    if isinstance(value, Decimal):
        return {"userEnteredValue": {"numberValue": float(value)}}
    if isinstance(value, int | float):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": value}}


def block_requests(
    sheet_id: int, row_count: int, col_count: int, start_cell: str,
    rows: Sequence[Sequence[Cell]], date_columns: Mapping[int, str] | None = None,
    trim: bool = False, chunk_rows: int = BLOCK_CHUNK_ROWS, width: int | None = None,
) -> tuple[list[list[dict[str, Any]]], int, int]:
    """The writes that replace everything from `start_cell` right to the tab's edge and down.

    Returns the writes, each a list of requests sent together, then the tab's new row and
    column counts. A write overwrites its own rows in place, and only the last one clears what
    the new rows don't reach, so the tab is never left empty on the way. `date_columns` maps a
    position within a row to the pattern its dates show in, like yyyy-mm-dd. Other cell
    formatting stays. `trim` deletes the rows below the block, so the tab shrinks with its data.
    `width` limits the block to that many columns from `start_cell`, so columns to its right
    are never written or cleared. No row may be wider.
    """
    try:
        row, col = a1_to_rowcol(start_cell)
    except IncorrectCellLabel:
        raise SheetError(f'"{start_cell}" is not a cell like B5') from None
    first_row, first_col = row - 1, col - 1
    end_row = first_row + len(rows)
    widest = max((len(r) for r in rows), default=0)
    if width is not None and (width < 1 or widest > width):
        raise SheetError(f"a block {width} column(s) wide can't hold a row of {widest}")
    total_rows = max(row_count, end_row)
    total_cols = max(col_count, first_col + (widest if width is None else width))
    end_col = total_cols if width is None else first_col + width

    def block_range(start: int, end: int) -> dict[str, int]:
        return {"sheetId": sheet_id, "startRowIndex": start, "endRowIndex": end,
                "startColumnIndex": first_col, "endColumnIndex": end_col}

    writes: list[list[dict[str, Any]]] = []
    for start in range(0, len(rows), chunk_rows):
        chunk = rows[start:start + chunk_rows]
        writes.append([{"updateCells": {
            "range": block_range(first_row + start, first_row + start + len(chunk)),
            "rows": [{"values": [_block_cell(value) for value in r]} for r in chunk],
            "fields": "userEnteredValue",
        }}])
    if not writes:
        writes.append([])
    if total_rows > row_count:
        writes[0].insert(0, {"appendDimension": {
            "sheetId": sheet_id, "dimension": "ROWS", "length": total_rows - row_count}})
    if total_cols > col_count:
        writes[0].insert(0, {"appendDimension": {
            "sheetId": sheet_id, "dimension": "COLUMNS", "length": total_cols - col_count}})
    if rows:
        for column, pattern in (date_columns or {}).items():
            writes[-1].append({"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": first_row, "endRowIndex": end_row,
                          "startColumnIndex": first_col + column,
                          "endColumnIndex": first_col + column + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {
                    "type": "DATE", "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat",
            }})
    new_row_count = total_rows
    if end_row < total_rows:
        if trim and rows:  # a tab can't lose all its rows, so an empty block only clears
            writes[-1].append({"deleteDimension": {"range": {
                "sheetId": sheet_id, "dimension": "ROWS",
                "startIndex": end_row, "endIndex": total_rows}}})
            new_row_count = end_row
        else:
            writes[-1].append({"updateCells": {
                "range": block_range(end_row, total_rows), "fields": "userEnteredValue"}})
    return writes, new_row_count, total_cols


def cell_request(sheet_id: int, cell: str, value: Cell,
                 pattern: str | None = None) -> dict[str, Any]:
    """The request that writes one cell, typed like a block's cells. Nothing else changes.

    `pattern` sets how a date or time shows, like yyyy-mm-dd hh:mm:ss. Other formatting stays.
    """
    try:
        row, col = a1_to_rowcol(cell)
    except IncorrectCellLabel:
        raise SheetError(f'"{cell}" is not a cell like B5') from None
    data = _block_cell(value)
    fields = "userEnteredValue"
    if pattern is not None:
        data["userEnteredFormat"] = {"numberFormat": {"type": "DATE_TIME", "pattern": pattern}}
        fields += ",userEnteredFormat.numberFormat"
    return {"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": row - 1, "endRowIndex": row,
                  "startColumnIndex": col - 1, "endColumnIndex": col},
        "rows": [{"values": [data]}],
        "fields": fields,
    }}


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
        self._col_count = ws.col_count

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

    def replace_block(self, start_cell: str, rows: Sequence[Sequence[Cell]],
                      date_columns: Mapping[int, str] | None = None,
                      trim: bool = False, width: int | None = None) -> None:
        """Replace everything from `start_cell` right and down (see block_requests).

        A block that fits in one write is all or nothing. A bigger one that fails partway
        holds new rows above old ones, so the caller must report that it failed.
        """
        writes, row_count, col_count = block_requests(
            self._ws.id, self._row_count, self._col_count, start_cell, rows, date_columns, trim,
            width=width)
        for requests in writes:
            if not requests:
                continue
            try:
                self._ws.spreadsheet.batch_update({"requests": requests})
            except gspread.exceptions.APIError as e:
                raise _api_error("writing to", e) from None
        self._row_count, self._col_count = row_count, col_count

    def write_cell(self, cell: str, value: Cell, pattern: str | None = None) -> None:
        """Write one cell (see cell_request). It's one request, so all or nothing.

        The cell must already exist: this never grows the tab.
        """
        request = cell_request(self._ws.id, cell, value, pattern)
        cell_range = request["updateCells"]["range"]
        if (cell_range["endRowIndex"] > self._row_count
                or cell_range["endColumnIndex"] > self._col_count):
            raise SheetError(f"{cell} is outside the tab")
        try:
            self._ws.spreadsheet.batch_update({"requests": [request]})
        except gspread.exceptions.APIError as e:
            raise _api_error("writing to", e) from None


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
