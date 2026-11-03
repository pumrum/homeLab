import datetime as dt
from decimal import Decimal

import pytest

from fidelity_nav.sheet import HEADER, SheetLayoutError, cell_date, cell_decimal, plan_append

ROW1 = {"as_of_date": "2026-10-30", "symbol": "TEST1", "nav": "12.30",
        "retrieved_at": "2026-10-30T19:30:04-05:00", "source_url": "https://example.invalid/1"}
ROW2 = {"as_of_date": "2026-11-02", "symbol": "TEST1", "nav": "12.34",
        "retrieved_at": "2026-11-03T19:30:05-05:00", "source_url": "https://example.invalid/1"}

# How Sheets returns those rows read unformatted: dates as serial numbers, NAV as numbers
SHEET_ROW1 = [46325, "TEST1", 12.3, "2026-10-30T19:30:04-05:00"]
SHEET_ROW2 = [46328, "TEST1", 12.34, "2026-11-03T19:30:05-05:00"]


def test_empty_sheet_gets_header_and_every_row():
    plan = plan_append([ROW1, ROW2], [])
    assert plan.added == 2
    assert plan.rows == [
        HEADER,
        ["2026-10-30", "TEST1", 12.3, "'2026-10-30T19:30:04-05:00"],
        ["2026-11-02", "TEST1", 12.34, "'2026-11-03T19:30:05-05:00"],
    ]


def test_only_missing_rows_are_appended():
    plan = plan_append([ROW1, ROW2], [HEADER, SHEET_ROW1])
    assert plan.added == 1
    assert plan.rows == [["2026-11-02", "TEST1", 12.34, "'2026-11-03T19:30:05-05:00"]]


def test_up_to_date_sheet_appends_nothing():
    plan = plan_append([ROW1, ROW2], [HEADER, SHEET_ROW1, SHEET_ROW2])
    assert (plan.added, plan.rows) == (0, [])


def test_no_csv_rows_writes_nothing_not_even_a_header():
    assert plan_append([], []).rows == []


def test_revised_nav_for_the_same_date_is_its_own_row():
    revised = ROW2 | {"nav": "12.35", "retrieved_at": "2026-11-03T21:00:00-05:00"}
    plan = plan_append([ROW1, ROW2, revised], [HEADER, SHEET_ROW1, SHEET_ROW2])
    assert plan.added == 1
    assert plan.rows[0][2] == 12.35


def test_text_cells_blank_rows_and_extra_columns_are_tolerated():
    sheet = [
        HEADER + ["my formula"],
        ["2026-10-30", "TEST1", "12.30", "2026-10-30T19:30:04-05:00", "=C2*2"],
        ["", "", "", ""],
    ]
    assert plan_append([ROW1], sheet).added == 0


def test_wrong_header_is_refused():
    with pytest.raises(SheetLayoutError, match="row 1"):
        plan_append([ROW1], [["date", "fund", "price"]])


def test_unreadable_row_names_its_row_number():
    with pytest.raises(SheetLayoutError, match="row 2"):
        plan_append([ROW1], [HEADER, ["soon", "TEST1", "n/a", ""]])


def test_cell_conversions():
    assert cell_date(46328) == dt.date(2026, 11, 2)
    assert cell_date(46328.75) == dt.date(2026, 11, 2)
    assert cell_date("2026-11-02") == dt.date(2026, 11, 2)
    assert cell_decimal(19) == cell_decimal("19.00") == Decimal("19")
    assert cell_decimal(12.3) == Decimal("12.30").normalize()
