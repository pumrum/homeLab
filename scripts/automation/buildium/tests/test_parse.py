"""The date logic, the file name, and the check that a PDF is the expected statement."""

import datetime as dt

import pytest

from buildium_portal.parse import (
    Statement,
    StatementError,
    check_statement,
    pdf_text,
    previous_period_end,
    read_statement,
    statement_file_name,
    statement_folder,
)

OCT_31 = dt.date(2026, 10, 31)
NAME = "FakeEntity One, LLC"


@pytest.mark.parametrize(("today", "end"), [
    (dt.date(2026, 11, 5), OCT_31),
    (dt.date(2026, 11, 1), OCT_31),  # the first of a month
    (dt.date(2026, 11, 30), OCT_31),  # the last of a month
    (dt.date(2027, 1, 1), dt.date(2026, 12, 31)),  # across a year
    (dt.date(2028, 3, 15), dt.date(2028, 2, 29)),  # a leap year
    (dt.date(2027, 3, 15), dt.date(2027, 2, 28)),
])
def test_the_period_ends_on_the_last_day_of_the_previous_month(today, end):
    assert previous_period_end(today) == end


def test_the_file_name_and_folder():
    assert statement_file_name(OCT_31, "FakeSystem", "ABCLLC") == (
        "2026-10-31_FakeSystem_Ledger_ABCLLC.pdf")
    assert statement_folder(OCT_31) == "2026-10"


def test_the_header_is_read_from_a_pdf(pdf_of, tmp_path):
    path = tmp_path / "statement.pdf"
    path.write_bytes(pdf_of())
    assert read_statement(pdf_text(path)) == Statement(
        "fakeentity one, llc", dt.date(2026, 10, 1), OCT_31)


def test_every_page_of_a_statement_counts_as_one_statement(text_of):
    statement = read_statement("\n".join([text_of()] * 3))
    assert statement.period_end == OCT_31


def test_pages_for_two_owners_are_refused(text_of):
    with pytest.raises(StatementError, match="one owner"):
        read_statement(text_of() + "\n" + text_of(owner="FakeEntity Two, LLC"))


def test_pages_for_two_periods_are_refused(text_of):
    with pytest.raises(StatementError, match="one statement period"):
        read_statement(text_of() + "\n" + text_of(period="9/1/2026 - 9/30/2026"))


@pytest.mark.parametrize("text", ["", "a page that isn't a statement"])
def test_something_that_isnt_a_statement_is_refused(text):
    with pytest.raises(StatementError):
        read_statement(text)


def test_an_impossible_date_is_refused(text_of):
    with pytest.raises(StatementError, match="invalid date"):
        read_statement(text_of(period="13/1/2026 - 13/31/2026"))


@pytest.mark.parametrize("name", [NAME, "fakeentity  one,  LLC"])
def test_the_right_statement_passes(text_of, name):
    check_statement(read_statement(text_of()), name, OCT_31)


def test_another_entitys_statement_fails_without_naming_either(text_of):
    with pytest.raises(StatementError, match="different owner") as caught:
        check_statement(read_statement(text_of(owner="FakeEntity Two, LLC")), NAME, OCT_31)
    assert "fakeentity" not in str(caught.value).casefold()


def test_a_name_in_the_transactions_doesnt_count_as_the_owner(text_of):
    text = text_of(owner="FakeEntity Two, LLC") + f"\nOwner Draw\n{NAME}\n"
    with pytest.raises(StatementError, match="different owner"):
        check_statement(read_statement(text), NAME, OCT_31)


@pytest.mark.parametrize("period", [
    "9/1/2026 - 9/30/2026",  # the previous month
    "10/1/2026 - 11/5/2026",  # the current period
    "1/1/2026 - 10/31/2026",  # year to date
    "10/15/2026 - 10/31/2026",  # part of the month
])
def test_any_other_period_fails(text_of, period):
    with pytest.raises(StatementError, match="not the month ending 2026-10-31"):
        check_statement(read_statement(text_of(period=period)), NAME, OCT_31)
