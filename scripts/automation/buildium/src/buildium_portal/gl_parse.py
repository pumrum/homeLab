"""Reading the general ledger export. Pure functions only."""

import csv
import io

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


def decode(raw: bytes) -> tuple[str, str]:
    """The export's text, and the encoding that read it."""
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def read_records(text: str) -> list[list[str]]:
    """Every record in the export, with the header first. A field can hold a line break."""
    return list(csv.reader(io.StringIO(text, newline="")))
