"""The activity CSV checks, on synthetic exports shaped like the real ones."""

import datetime as dt

import pytest

from bank1_portal.activity_parse import ActivityError, header_problem, parse_activity, site_date
from bank1_portal.config import Kind

START, END = dt.date(2026, 7, 1), dt.date(2026, 11, 11)

CHECKING = """Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
DEBIT,11/10/2026,"EXAMPLE VENDOR, INC",-12.50,ACH_DEBIT,100.00,
CHECK,10/15/2026,CHECK 1001,-50.00,CHECK_PAID,112.50,1001
CREDIT,07/01/2026,EXAMPLE DEPOSIT,500.00,ACH_CREDIT,162.50,
"""

CARD = """Transaction Date,Post Date,Description,Category,Type,Amount,Memo
11/09/2026,11/10/2026,EXAMPLE STORE,Shopping,Sale,-20.00,
06/30/2026,07/01/2026,EXAMPLE CAFE,Food & Drink,Sale,-4.50,
"""


def test_checking_rows_come_back_oldest_first_as_exported_text():
    activity = parse_activity(CHECKING, Kind.CHECKING, START, END)
    assert [row[2] for row in activity.rows] == [
        "EXAMPLE DEPOSIT", "CHECK 1001", "EXAMPLE VENDOR, INC"]
    assert activity.rows[0] == ["CREDIT", "07/01/2026", "EXAMPLE DEPOSIT", "500.00",
                                "ACH_CREDIT", "162.50", ""]
    assert activity.width == 7
    assert (activity.first, activity.last) == (START, dt.date(2026, 11, 10))


def test_a_card_row_counts_as_in_range_if_either_date_is():
    activity = parse_activity(CARD, Kind.CARD, START, END)
    assert activity.rows[0][0] == "06/30/2026"
    assert activity.first == START


def test_a_byte_order_mark_and_trailing_commas_are_tolerated():
    text = "﻿" + CHECKING.replace("Slip #\n", "Slip #,\n").replace("ACH_DEBIT,100.00,\n",
                                                                         "ACH_DEBIT,100.00,,\n")
    assert len(parse_activity(text, Kind.CHECKING, START, END).rows) == 3


def test_a_short_row_is_padded_to_the_header():
    text = CHECKING.replace("ACH_CREDIT,162.50,\n", "ACH_CREDIT,162.50\n")
    assert parse_activity(text, Kind.CHECKING, START, END).rows[0][-1] == ""


def test_a_row_with_more_values_than_the_header_fails():
    text = CHECKING.replace("ACH_CREDIT,162.50,\n", "ACH_CREDIT,162.50,,surprise\n")
    with pytest.raises(ActivityError, match="row 3 has 8 fields"):
        parse_activity(text, Kind.CHECKING, START, END)


def test_a_new_leading_column_is_named():
    text = "\n".join("Card," + line for line in CHECKING.splitlines())
    with pytest.raises(ActivityError, match="added Card$"):
        parse_activity(text, Kind.CHECKING, START, END)


def test_header_problems_name_added_missing_and_reordered_columns():
    assert header_problem(Kind.CARD, ["Transaction Date", "Post Date", "Description",
                                      "Category", "Type", "Amount", "Memo"]) is None
    assert header_problem(Kind.CARD, ["Transaction Date", "Posted On", "Description",
                                      "Category", "Type", "Amount", "Memo"]) == (
        "the card columns changed: added Posted On; missing Post Date")
    assert "different order" in header_problem(
        Kind.CARD, ["Post Date", "Transaction Date", "Description", "Category", "Type",
                    "Amount", "Memo"])


def test_a_row_outside_the_range_fails_without_showing_its_values():
    text = CHECKING.replace("07/01/2026,EXAMPLE DEPOSIT", "06/30/2026,EXAMPLE DEPOSIT")
    with pytest.raises(ActivityError, match="row 3 is dated outside 07/01/2026 to 11/11/2026") \
            as e:
        parse_activity(text, Kind.CHECKING, START, END)
    assert "EXAMPLE" not in str(e.value)


def test_a_bad_date_fails():
    with pytest.raises(ActivityError, match="row 1 has a date that isn't MM/DD/YYYY"):
        parse_activity(CHECKING.replace("11/10/2026", "2026-11-10"), Kind.CHECKING, START, END)


@pytest.mark.parametrize(("text", "message"), [
    ("", "the file is empty"),
    ("Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n",
     "no transactions"),
    ("<html><body>Something went wrong</body></html>", "columns changed"),
])
def test_an_empty_or_wrong_file_is_never_saved(text, message):
    with pytest.raises(ActivityError, match=message):
        parse_activity(text, Kind.CHECKING, START, END)


def test_an_investment_has_no_known_columns_yet():
    with pytest.raises(ActivityError, match="no known activity columns for a investment"):
        parse_activity(CHECKING, Kind.INVESTMENT, START, END)


def test_dates_are_typed_as_the_site_shows_them():
    assert site_date(dt.date(2026, 7, 1)) == "07/01/2026"
