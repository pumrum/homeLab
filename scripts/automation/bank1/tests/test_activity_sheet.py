"""How activity values go in the sheet: as a paste of values would store them, where that
can't change them."""

import datetime as dt
from decimal import Decimal

import pytest

from bank1_portal.activity_sheet import (
    DATE_PATTERN,
    sheet_block,
    sheet_cell,
    workbooks_to_stamp,
)
from bank1_portal.config import parse_config


@pytest.mark.parametrize(("value", "stored"), [
    ("-12.50", Decimal("-12.50")),
    ("500.00", Decimal("500.00")),
    ("0", 0),
    ("1001", 1001),
    ("11/10/2026", dt.date(2026, 11, 10)),
    ("07/01/2026", dt.date(2026, 7, 1)),
])
def test_numbers_and_dates_become_real_values(value, stored):
    assert sheet_cell(value) == stored
    assert type(sheet_cell(value)) is type(stored)


@pytest.mark.parametrize("value", [
    "",                       # stays blank
    "EXAMPLE VENDOR, INC",
    "ACH_DEBIT",
    "007",                    # a paste would turn this into 7
    "1,234.50",               # never in the export; left alone rather than guessed at
    "=HYPERLINK(\"x\")",      # a paste would run this as a formula
    "+1 555 0100",
    "13/45/2026",             # not a real date
    "1/2/2026",               # not the export's MM/DD/YYYY
    "2026-11-10",
])
def test_anything_else_stays_text_exactly_as_exported(value):
    assert sheet_cell(value) == value


def test_a_block_formats_only_the_columns_holding_dates():
    rows = [["DEBIT", "11/10/2026", "EXAMPLE", "-12.50", "ACH_DEBIT", "100.00", ""],
            ["CHECK", "10/15/2026", "CHECK 1001", "-50.00", "CHECK_PAID", "112.50", "1001"]]
    cells, date_columns = sheet_block(rows)
    assert cells[1] == ["CHECK", dt.date(2026, 10, 15), "CHECK 1001", Decimal("-50.00"),
                        "CHECK_PAID", Decimal("112.50"), 1001]
    assert date_columns == {1: DATE_PATTERN}


def test_a_card_block_formats_both_date_columns():
    _, date_columns = sheet_block([["11/09/2026", "11/10/2026", "EXAMPLE STORE", "Shopping",
                                    "Sale", "-20.00", ""]])
    assert date_columns == {0: DATE_PATTERN, 1: DATE_PATTERN}


def _stamp_cfg():
    def account(n, label, sheet=None):
        entry = {"label": label, "kind": "checking", "suffix": f"111{n}",
                 "statements_url": f"https://bank.example.invalid/s/{n}",
                 "activity_url": f"https://bank.example.invalid/a/{n}",
                 "activity_start": "2026-07-01", "abbreviation": f"A{n}"}
        if sheet:
            entry["activity_sheet"] = {"sheet": sheet, "tab": f"T{n}", "start_cell": "A5"}
        return entry
    stamped = {"updated_tab": "Dashboard", "updated_cell": "B2"}
    return parse_config({
        "site": {"login_url": "https://bank.example.invalid/logon", "name_in_files": "FakeBank"},
        "sheets": {"one": {"spreadsheet_id": "id-1"} | stamped,
                   "two": {"spreadsheet_id": "id-2"} | stamped,
                   "plain": {"spreadsheet_id": "id-3"},
                   "unused": {"spreadsheet_id": "id-4"} | stamped},
        "accounts": [account(1, "a1", "one"), account(2, "a2", "one"), account(3, "a3", "two"),
                     account(4, "a4", "plain"), account(5, "a5")]})


def _decisions(ok, labels=None):
    cfg = _stamp_cfg()
    accounts = [a for a in cfg.accounts if labels is None or a.label in labels]
    found = workbooks_to_stamp(cfg.sheets, accounts, ok)
    return {workbook.name: stamp for workbook, stamp in found}


def test_a_workbook_is_stamped_only_when_every_account_writing_to_it_did():
    assert _decisions({"a1", "a2", "a3", "a4", "a5"}) == {"one": True, "two": True}
    assert _decisions({"a1", "a3"}) == {"one": False, "two": True}
    assert _decisions(set()) == {"one": False, "two": False}


def test_only_the_accounts_in_this_run_count():
    assert _decisions({"a1"}, labels={"a1"}) == {"one": True}
    assert _decisions({"a5"}, labels={"a5"}) == {}
