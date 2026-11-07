"""Cleaning an export, and the Cash view's rules. Every value is made up."""

import datetime as dt
from decimal import Decimal

import pytest

from buildium_portal.config import UnitFix
from buildium_portal.gl_parse import (
    CASH_VIEW_COLUMNS,
    EXPECTED_COLUMNS,
    MASTER_COLUMNS,
    LedgerError,
    check_columns,
    clean_export,
    decode,
    repeated_pairs,
    shared_pairs,
    sheet_rows,
    sort_rows,
)
from buildium_portal.gl_rules import cash_view


def _row(**values):
    row = {"Id": "1", "PostingId": "1", "EntryDate": "3/15/2026", "Amount": "10.00",
           "GLAccountName": "Rent", "GLAccountNameRaw": "Rent", "GLAccountTypeName": "Income",
           "JournalCodeDescription": "Charge", "PayeeName": "FakeTenant", "UnitNumber": "1",
           "LeaseId": "77"}
    return row | values


def _clean(gl_csv_of, rows, fixes=()):
    return clean_export(gl_csv_of(rows=rows), "FakeCode", "Cash", fixes)


def _view_row(gl_csv_of, **values):
    return _clean(gl_csv_of, [_row(**values)]).rows


# ------------------------------------------------ cleaning

def test_a_clean_row_gets_its_entity_method_and_types(gl_csv_of):
    ledger = _clean(gl_csv_of, [_row(Amount="-1,250.50", EntryDate="03/05/2026")])
    row = ledger.rows[0]
    assert row["Entity"] == "FakeCode"
    assert row["Method"] == "Cash"
    assert row["EntryDate"] == dt.date(2026, 3, 5)
    assert row["EntryDate_YM"] == "2026-03"
    assert row["Amount"] == Decimal("-1250.50")
    assert (ledger.read, ledger.blank_id, ledger.unit_fixed) == (1, 0, 0)


def test_rows_with_no_id_are_dropped_when_theyre_zero(gl_csv_of):
    blank = _row(Id="", PostingId="", Amount="0.00")
    ledger = _clean(gl_csv_of, [blank, _row(), _row(Id="", PostingId="", Amount="0")])
    assert (ledger.read, ledger.blank_id, len(ledger.rows)) == (3, 2, 1)


def test_a_row_with_no_id_that_isnt_zero_stops_the_entity(gl_csv_of):
    with pytest.raises(LedgerError, match="record 3 has no Id but isn't zero"):
        _clean(gl_csv_of, [_row(), _row(Id="", PostingId="", Amount="5.00")])


@pytest.mark.parametrize(("change", "message"), [
    ({"Id": "ABC"}, "record 2: Id isn't a whole number"),
    ({"PostingId": ""}, "record 2: PostingId isn't a whole number"),
    ({"Amount": "FakeSecret"}, "record 2: Amount isn't a number"),
    ({"Amount": "NaN"}, "record 2: Amount isn't a number"),
    ({"EntryDate": "2026-03-15"}, "record 2: EntryDate isn't a date"),
])
def test_a_bad_value_stops_the_entity_without_repeating_it(gl_csv_of, change, message):
    with pytest.raises(LedgerError, match=message) as caught:
        _clean(gl_csv_of, [_row(**change)])
    assert "FakeSecret" not in str(caught.value)


def test_an_export_with_no_entries_stops_the_entity(gl_csv_of):
    with pytest.raises(LedgerError, match="no entries"):
        _clean(gl_csv_of, [_row(Id="", PostingId="", Amount="0.00")])
    with pytest.raises(LedgerError, match="empty"):
        clean_export(b"", "FakeCode", "Cash")


def test_a_row_with_the_wrong_number_of_fields_stops_the_entity(gl_csv_of):
    with pytest.raises(LedgerError, match="record 2 has 2 fields, not 53"):
        clean_export(gl_csv_of(rows=[]) + b"1,2\r\n", "FakeCode", "Cash")


def test_a_field_with_a_line_break_stays_one_field(gl_csv_of):
    assert _clean(gl_csv_of, [_row(JournalMemo="two\nlines")]).rows[0]["JournalMemo"] == (
        "two\nlines")


def test_an_older_encoding_still_reads():
    text, encoding = decode("caf\xe9".encode("cp1252"))
    assert (text, encoding) == ("caf\xe9", "cp1252")


def test_column_names_are_matched_ignoring_case():
    check_columns([column.upper() for column in EXPECTED_COLUMNS])


def test_a_column_change_names_whats_missing_and_added():
    columns = ["BrandNew" if c == "Amount" else c for c in EXPECTED_COLUMNS]
    with pytest.raises(LedgerError, match="missing Amount; added BrandNew"):
        check_columns(columns)


def test_columns_in_another_order_are_refused():
    with pytest.raises(LedgerError, match="different order"):
        check_columns(tuple(reversed(EXPECTED_COLUMNS)))


def test_a_column_change_doesnt_repeat_what_isnt_a_column_name():
    with pytest.raises(LedgerError) as caught:
        check_columns(["<html> FakeSecret page"])
    assert "FakeSecret" not in str(caught.value)
    assert "not a column name" in str(caught.value)


def test_a_long_column_change_is_cut_short():
    with pytest.raises(LedgerError, match="and 48 more"):
        check_columns([])


def test_the_sheet_layouts():
    assert len(MASTER_COLUMNS) == 56
    assert MASTER_COLUMNS[:2] == ("Entity", "Method")
    assert MASTER_COLUMNS[-1] == "EntryDate_YM"
    assert len(CASH_VIEW_COLUMNS) == 15


# ------------------------------------------------ the unit fix

FIX = UnitFix("entity1", "77", dt.date(2030, 1, 31), "Upper")


def test_the_unit_fix_changes_only_that_lease_before_that_date(gl_csv_of):
    rows = [_row(Id="1", EntryDate="1/30/2030"),
            _row(Id="2", EntryDate="1/31/2030"),
            _row(Id="3", EntryDate="1/30/2030", LeaseId="78")]
    ledger = _clean(gl_csv_of, rows, [FIX])
    assert [row["UnitNumber"] for row in ledger.rows] == ["Upper", "1", "1"]
    assert ledger.unit_fixed == 1


# ------------------------------------------------ the order

def test_rows_sort_by_date_id_posting_then_account_descending_then_unit(gl_csv_of):
    rows = [_row(Id="9", PostingId="1", EntryDate="3/16/2026"),
            _row(Id="10", PostingId="2", EntryDate="3/15/2026"),
            _row(Id="10", PostingId="1", GLAccountNameRaw="Alpha", EntryDate="3/15/2026"),
            _row(Id="10", PostingId="1", GLAccountNameRaw="Zed", UnitNumber="B",
                 EntryDate="3/15/2026"),
            _row(Id="10", PostingId="1", GLAccountNameRaw="Zed", UnitNumber="A",
                 EntryDate="3/15/2026"),
            _row(Id="9", PostingId="1", EntryDate="3/15/2026")]
    ordered = _clean(gl_csv_of, rows).rows
    assert [(r["Id"], r["PostingId"], r["GLAccountNameRaw"], r["UnitNumber"]) for r in ordered] == [
        ("9", "1", "Rent", "1"),  # 9 before 10: Ids sort as numbers, not text
        ("10", "1", "Zed", "A"),
        ("10", "1", "Zed", "B"),
        ("10", "1", "Alpha", "1"),
        ("10", "2", "Rent", "1"),
        ("9", "1", "Rent", "1"),  # the later date
    ]


def test_sorting_ignores_case_and_outer_spaces(gl_csv_of):
    rows = _clean(gl_csv_of, [_row(UnitNumber="b"), _row(UnitNumber=" A")]).rows
    assert [row["UnitNumber"] for row in sort_rows(rows)] == [" A", "b"]


# ------------------------------------------------ the checks

def test_repeated_pairs_are_counted_and_kept(gl_csv_of):
    ledger = _clean(gl_csv_of, [_row(), _row(), _row(Id="2")])
    assert len(ledger.rows) == 3
    assert repeated_pairs(ledger.rows) == 1


def test_shared_pairs_counts_entries_in_both_exports(gl_csv_of):
    a = _clean(gl_csv_of, [_row(Id="1"), _row(Id="2")]).rows
    b = _clean(gl_csv_of, [_row(Id="2"), _row(Id="3")]).rows
    assert shared_pairs(a, b) == 1


# ------------------------------------------------ the sheet's values

def test_sheet_rows_type_each_column(gl_csv_of):
    rows = _clean(gl_csv_of, [_row(Id="12", UnitNumber="9", JournalMemo="=1+1",
                                   CheckNumber="99-1", IsCashPosting="1", EFTResultDate="3/1/2026",
                                   FileIdString="55", VendorId="")]).rows
    cell = dict(zip(MASTER_COLUMNS, sheet_rows(rows, MASTER_COLUMNS)[0], strict=True))
    assert cell["Id"] == 12
    assert cell["PostingId"] == 1
    assert cell["LeaseId"] == 77
    assert cell["VendorId"] == ""  # blank, not zero
    assert cell["EntryDate"] == dt.date(2026, 3, 15)
    assert cell["Amount"] == Decimal("10.00")
    assert cell["EntryDate_YM"] == "2026-03"
    assert cell["UnitNumber"] == 9
    assert cell["IsCashPosting"] == 1
    assert cell["FileIdString"] == 55
    assert cell["EFTResultDate"] == dt.date(2026, 3, 1)
    assert cell["JournalMemo"] == "=1+1"  # stays text, so it can't run
    assert cell["CheckNumber"] == "99-1"


def test_true_and_false_become_real_values(gl_csv_of):
    rows = _clean(gl_csv_of, [_row(IsActive="True", IsEFT="False")]).rows
    cell = dict(zip(MASTER_COLUMNS, sheet_rows(rows, MASTER_COLUMNS)[0], strict=True))
    assert cell["IsActive"] is True
    assert cell["IsEFT"] is False


@pytest.mark.parametrize("text", [
    "007",  # a number would lose its zeros
    "1234567890123456",  # too long for a sheet's number to hold exactly
    "1/2",  # a unit, not a date
    "1,250",
    "5A",
    "true",
    "True ",
    "",
])
def test_a_value_that_could_change_stays_as_exported(gl_csv_of, text):
    rows = _clean(gl_csv_of, [_row(UnitNumber=text)]).rows
    cell = dict(zip(MASTER_COLUMNS, sheet_rows(rows, MASTER_COLUMNS)[0], strict=True))
    assert cell["UnitNumber"] == text


def test_a_date_in_another_column_or_an_odd_one_stays_as_exported(gl_csv_of):
    rows = _clean(gl_csv_of, [_row(JournalMemo="3/1/2026", EFTResultDate="soon")]).rows
    cell = dict(zip(MASTER_COLUMNS, sheet_rows(rows, MASTER_COLUMNS)[0], strict=True))
    assert cell["JournalMemo"] == "3/1/2026"
    assert cell["EFTResultDate"] == "soon"


def test_trailing_spaces_stay_in_what_goes_to_the_sheet(gl_csv_of):
    rows = _clean(gl_csv_of, [_row(JournalCodeDescription="Check ")]).rows
    cell = dict(zip(MASTER_COLUMNS, sheet_rows(rows, MASTER_COLUMNS)[0], strict=True))
    assert cell["JournalCodeDescription"] == "Check "


# ------------------------------------------------ the Cash view's rules

@pytest.mark.parametrize(("rule", "values"), [
    ("Asset", {"GLAccountTypeName": "Asset"}),
    ("Convenience fee", {"GLAccountNameRaw": "Convenience Fee"}),
    ("Applied deposit", {"JournalCodeDescription": "Applied Deposit"}),
    ("Deposit refund", {"JournalCodeDescription": "Refund", "GLAccountTypeName": "Liability"}),
    ("Owner draw by check", {"GLAccountName": "Owner Draw", "JournalCodeDescription": "Check"}),
    ("Opening balance equity", {"GLAccountName": "Opening Balance Equity"}),
    ("Opening security credits", {"GLAccountTypeName": "Liability",
                                  "JournalCodeDescription": "Credit"}),
])
def test_each_rule_removes_its_rows(gl_csv_of, rule, values):
    view = cash_view(_view_row(gl_csv_of, **values))
    assert view.rows == []
    assert view.removed == {rule: 1}


@pytest.mark.parametrize("values", [
    {"JournalCodeDescription": "Refund", "GLAccountTypeName": "Income"},
    {"GLAccountName": "Owner Draw", "JournalCodeDescription": "Charge"},
    {"GLAccountName": "Rent", "JournalCodeDescription": "Check"},
    {"GLAccountTypeName": "Liability", "JournalCodeDescription": "Charge"},
    {"GLAccountTypeName": "Equity", "JournalCodeDescription": "Credit"},
])
def test_rows_only_partly_matching_a_rule_stay(gl_csv_of, values):
    view = cash_view(_view_row(gl_csv_of, **values))
    assert len(view.rows) == 1
    assert not view.removed


def test_rules_ignore_case_and_outer_spaces_like_the_real_export(gl_csv_of):
    rows = _view_row(gl_csv_of, GLAccountName="Owner Draw", JournalCodeDescription="Check ")
    assert cash_view(rows).removed == {"Owner draw by check": 1}
    rows = _view_row(gl_csv_of, GLAccountTypeName=" asset")
    assert cash_view(rows).removed == {"Asset": 1}
    rows = _view_row(gl_csv_of, JournalCodeDescription="Refund ", GLAccountTypeName="Liability")
    assert cash_view(rows).removed == {"Deposit refund": 1}


def test_a_deposit_transfer_with_a_payee_becomes_equity_before_the_asset_rule(gl_csv_of):
    rows = _view_row(gl_csv_of, GLAccountNameRaw="Deposit Transfer", GLAccountTypeName="Asset")
    view = cash_view(rows)
    assert [row["GLAccountTypeName"] for row in view.rows] == ["Equity"]
    assert view.deposit_transfers == 1


def test_a_deposit_transfer_with_no_payee_stays_as_it_was(gl_csv_of):
    rows = _view_row(gl_csv_of, GLAccountNameRaw="Deposit Transfer", GLAccountTypeName="Asset",
                     PayeeName="  ")
    view = cash_view(rows)
    assert view.removed == {"Asset": 1}
    assert view.deposit_transfers == 0


def test_every_amount_flips_as_a_number(gl_csv_of):
    rows = _clean(gl_csv_of, [_row(Id="1", Amount="-10.50"), _row(Id="2", Amount="1,000.25"),
                              _row(Id="3", Amount="0.00")]).rows
    assert [row["Amount"] for row in cash_view(rows).rows] == [
        Decimal("10.50"), Decimal("-1000.25"), Decimal("0.00")]


def test_the_view_leaves_the_ledger_alone(gl_csv_of):
    rows = _view_row(gl_csv_of, Amount="10.00", GLAccountNameRaw="Deposit Transfer")
    cash_view(rows)
    assert rows[0]["Amount"] == Decimal("10.00")
    assert rows[0]["GLAccountTypeName"] == "Income"


def test_the_view_keeps_the_ledgers_order(gl_csv_of):
    rows = _clean(gl_csv_of, [_row(Id="2"), _row(Id="1"), _row(Id="3")]).rows
    assert [row["Id"] for row in cash_view(rows).rows] == ["1", "2", "3"]
