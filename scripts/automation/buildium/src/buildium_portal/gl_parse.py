"""Reading and cleaning the general ledger export. Pure functions only.

Error messages name columns and record numbers, never a value from the export.
"""

import csv
import datetime as dt
import io
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from automation_common.sheets import Cell
from buildium_portal.config import UnitFix
from buildium_portal.gl_rules import Row

EXPECTED_COLUMNS = (
    "Id", "PostingId", "JournalCodeId", "EntryDate", "JournalMemo", "CheckNumber",
    "PostingMemo", "Amount", "IsCashPosting", "GlAccountId", "ReferenceNumber", "BuildingId",
    "GLAccountName", "ParentGLAccountName", "IsParent", "GLSubTypeId", "ParentGLAccountId",
    "PayeeName", "PayeeNameRaw", "PayeeUserId", "PayeeNames", "SortedPayeeNames",
    "UserTypeId", "LeaseId", "UnitNumber", "UnitId", "BuildingName", "VendorId", "FileId",
    "FileIds", "IsBankAccount", "GLAccountTypeId", "GLAccountTypeName",
    "JournalCodeDescription", "AccountingBookId", "IsEFT", "TraceNumber", "EFTResultDate",
    "EFTResultCode", "AttributeId", "ReversedJournalId", "BankAccountName",
    "BuildingStatusId", "BuildingTypeId", "DepositJournalId", "IsActive", "ParentIsActive",
    "GLAccountNameRaw", "IsPendingEFT", "IsApplicationJournal", "FileIdString",
    "runTimeCreatedRow", "skipMaxRowLimit",
)
MASTER_COLUMNS = ("Entity", "Method", *EXPECTED_COLUMNS, "EntryDate_YM")
CASH_VIEW_COLUMNS = (
    "Id", "PostingId", "EntryDate", "JournalMemo", "PostingMemo", "Amount", "GLAccountName",
    "PayeeName", "PayeeNameRaw", "PayeeNames", "UnitNumber", "BuildingName",
    "GLAccountTypeName", "JournalCodeDescription", "GLAccountNameRaw",
)
_MAX_NAMED_COLUMNS = 5
_COLUMN_NAME = re.compile(r"\w{1,40}")
_BOOLEANS = {"True": True, "False": False}
# No leading zeros, and few enough digits that a sheet's number holds them exactly
_PLAIN_NUMBER = re.compile(r"-?(?:0|[1-9]\d{0,14})(?:\.\d{1,6})?")


class LedgerError(Exception):
    """An export can't be trusted. The message says why and holds no value from it."""


@dataclass(frozen=True)
class Ledger:
    """One entity's cleaned export for one method, in sheet order."""

    rows: list[Row]
    read: int
    blank_id: int
    unit_fixed: int


def decode(raw: bytes) -> tuple[str, str]:
    """The export's text, and the encoding that read it."""
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def read_records(text: str) -> list[list[str]]:
    """Every record in the export, with the header first. A field can hold a line break."""
    return list(csv.reader(io.StringIO(text, newline="")))


def _named(columns: Sequence[str]) -> str:
    # Something that isn't a CSV could have anything in its first line, so only show real names
    names = [c if _COLUMN_NAME.fullmatch(c) else "(not a column name)" for c in columns]
    shown = ", ".join(names[:_MAX_NAMED_COLUMNS])
    more = len(names) - _MAX_NAMED_COLUMNS
    return f"{shown}, and {more} more" if more > 0 else shown


def check_columns(header: Sequence[str]) -> None:
    """The header must be the expected columns in order, ignoring case. Raises otherwise."""
    found = [column.strip().casefold() for column in header]
    expected = [column.casefold() for column in EXPECTED_COLUMNS]
    if found == expected:
        return
    missing = [column for column in EXPECTED_COLUMNS if column.casefold() not in found]
    added = [column for column in header if column.strip().casefold() not in expected]
    changes = [f"missing {_named(missing)}" if missing else "",
               f"added {_named(added)}" if added else ""]
    raise LedgerError("the export's columns changed: "
                      + ("; ".join(filter(None, changes)) or "they're in a different order"))


def parse_amount(text: str, record: int) -> Decimal:
    try:
        amount = Decimal(text.strip().replace(",", ""))
    except InvalidOperation:
        raise LedgerError(f"record {record}: Amount isn't a number") from None
    if not amount.is_finite():
        raise LedgerError(f"record {record}: Amount isn't a number")
    return amount


def _parse_date(text: str, record: int) -> dt.date:
    try:
        return dt.datetime.strptime(text.strip(), "%m/%d/%Y").date()
    except ValueError:
        raise LedgerError(f"record {record}: EntryDate isn't a date like 3/15/2026") from None


def _clean_row(fields: Mapping[str, str], code: str, method: str, record: int) -> Row:
    for column in ("Id", "PostingId"):
        if not fields[column].strip().isdecimal():
            raise LedgerError(f"record {record}: {column} isn't a whole number")
    entry_date = _parse_date(fields["EntryDate"], record)
    return {**fields, "Entity": code, "Method": method, "EntryDate": entry_date,
            "Amount": parse_amount(fields["Amount"], record),
            "EntryDate_YM": entry_date.strftime("%Y-%m")}


def apply_unit_fixes(rows: Sequence[Row], fixes: Iterable[UnitFix]) -> int:
    """Set the unit on a lease's rows before each fix's date. Returns how many rows matched."""
    changed = 0
    for fix in fixes:
        for row in rows:
            if row["LeaseId"].strip() == fix.lease_id and row["EntryDate"] < fix.before:
                row["UnitNumber"] = fix.unit_number
                changed += 1
    return changed


def sort_rows(rows: Iterable[Row]) -> list[Row]:
    """EntryDate, Id, PostingId, then GLAccountNameRaw descending, then UnitNumber."""
    by_unit = sorted(rows, key=lambda row: row["UnitNumber"].strip().casefold())
    by_account = sorted(by_unit, key=lambda row: row["GLAccountNameRaw"].strip().casefold(),
                        reverse=True)
    return sorted(by_account, key=lambda row: (row["EntryDate"], int(row["Id"]),
                                               int(row["PostingId"])))


def clean_export(raw: bytes, code: str, method: str,
                 fixes: Iterable[UnitFix] = ()) -> Ledger:
    """Check one export and return its rows, or raise LedgerError.

    A row with no Id is a report line, not an entry. They're dropped, but only if they're zero:
    with the whole history exported, a non-zero one means this isn't the data we think it is,
    and dropping it would change the totals.
    """
    records = read_records(decode(raw)[0])
    if not records:
        raise LedgerError("the export is empty")
    check_columns(records[0])
    rows: list[Row] = []
    read = blank_id = 0
    for number, record in enumerate(records[1:], start=2):
        if not record:
            continue
        read += 1
        if len(record) != len(EXPECTED_COLUMNS):
            raise LedgerError(f"record {number} has {len(record)} fields, "
                              f"not {len(EXPECTED_COLUMNS)}")
        fields = dict(zip(EXPECTED_COLUMNS, record, strict=True))
        if fields["Id"].strip():
            rows.append(_clean_row(fields, code, method, number))
            continue
        blank_id += 1
        if fields["Amount"].strip() and parse_amount(fields["Amount"], number) != 0:
            raise LedgerError(f"record {number} has no Id but isn't zero")
    if not rows:
        raise LedgerError("the export has no entries")
    unit_fixed = apply_unit_fixes(rows, fixes)
    return Ledger(sort_rows(rows), read, blank_id, unit_fixed)


def repeated_pairs(rows: Iterable[Row]) -> int:
    """How many Id and PostingId pairs appear more than once. Reported, never dropped."""
    counts = Counter((row["Id"].strip(), row["PostingId"].strip()) for row in rows)
    return sum(1 for count in counts.values() if count > 1)


def shared_pairs(a: Iterable[Row], b: Iterable[Row]) -> int:
    """How many Id and PostingId pairs two exports have in common."""
    def pairs(rows: Iterable[Row]) -> set[tuple[str, str]]:
        return {(row["Id"].strip(), row["PostingId"].strip()) for row in rows}
    return len(pairs(a) & pairs(b))


def _cell(column: str, value: Any) -> Cell:
    """What a paste would store, but only where the value can't change.

    True and False, plain numbers, and EFTResultDate's dates become real values. Anything else
    stays text exactly as exported, such as 007, which a paste would turn into 7.
    """
    if not isinstance(value, str):
        return value
    if value in _BOOLEANS:
        return _BOOLEANS[value]
    if _PLAIN_NUMBER.fullmatch(value):
        return Decimal(value) if "." in value else int(value)
    if column == "EFTResultDate":
        try:
            return dt.datetime.strptime(value, "%m/%d/%Y").date()
        except ValueError:
            return value
    return value


def sheet_rows(rows: Iterable[Row], columns: Sequence[str]) -> list[list[Cell]]:
    return [[_cell(column, row[column]) for column in columns] for row in rows]
