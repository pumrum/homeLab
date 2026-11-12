"""The pure checks: dates, the newest statement, file names, and the PDF checks."""

import datetime as dt

import pytest

from bank1_portal.parse import (
    Row,
    StatementError,
    check_pdf_file,
    check_statement,
    dates_in,
    newest_of_type,
    newest_statement,
    page_shows,
    pdf_text,
    statement_file_name,
)

OCT31 = dt.date(2026, 10, 31)


@pytest.mark.parametrize("text", [
    "Oct 31, 2026", "October 31, 2026", "Oct. 31 2026", "OCTOBER 31, 2026",
    "10/31/2026", "10/31/26", "2026-10-31", "Statement closing 10/31/2026 (PDF)",
])
def test_each_date_form_is_read(text):
    assert dates_in(text) == [OCT31]


def test_a_date_with_no_year_or_an_impossible_one_isnt_a_date():
    assert dates_in("Oct 1 - Feb 30, 2026 - 13/01/2026 - account 1031") == []


def test_a_period_gives_both_ends():
    assert dates_in("Oct 1, 2026 - Oct 31, 2026") == [dt.date(2026, 10, 1), OCT31]


@pytest.mark.parametrize("rows", [
    ["Oct 31, 2026 Download", "Sep 30, 2026 Download", "Aug 31, 2026 Download"],
    ["Aug 31, 2026 Download", "Sep 30, 2026 Download", "Oct 31, 2026 Download"],
    ["Sep 30, 2026", "Oct 31, 2026", "Year-end summary", "Aug 31, 2026"],
])
def test_the_newest_is_the_latest_date_in_any_order(rows):
    newest = newest_statement(rows)
    assert rows[newest.index].startswith("Oct 31")
    assert newest.closing == OCT31
    assert newest.same_month == 0


def test_a_row_with_a_period_closes_on_its_last_date():
    newest = newest_statement(["Sep 1, 2026 - Sep 30, 2026", "Oct 1, 2026 - Oct 31, 2026"])
    assert (newest.index, newest.closing) == (1, OCT31)


def test_another_statement_in_the_same_month_is_counted():
    newest = newest_statement(["Oct 31, 2026", "Oct 2, 2026", "Sep 30, 2026"])
    assert (newest.closing, newest.same_month) == (OCT31, 1)


def test_two_statements_on_the_newest_day_are_refused():
    with pytest.raises(StatementError, match="same day"):
        newest_statement(["Oct 31, 2026", "10/31/2026"])


def test_a_list_with_no_dates_is_refused():
    with pytest.raises(StatementError, match="no statement date in any of the 2"):
        newest_statement(["Statement", "Download"])


# An investment account's list, as on the real page: a group per date, whose first row
# alone shows the date, with a Multi row, a Single row, and sometimes letters
INVESTMENT_ROWS = [
    Row("Oct 31, 2026", "Multi"),
    Row("", "Single"),
    Row("Sep 30, 2026", "Multi"),
    Row("", "2026 Annual Disclosure Cover Letter"),
    Row("", "Single"),
]


def test_a_row_without_a_date_takes_its_groups_date():
    newest = newest_of_type(INVESTMENT_ROWS, "Single")
    assert (newest.index, newest.closing, newest.same_month) == (1, OCT31, 0)


def test_only_rows_of_the_wanted_type_count():
    assert newest_of_type(INVESTMENT_ROWS, "multi").index == 0  # case doesn't matter
    rows = [Row("Nov 2, 2026", "Notice"), Row("Oct 31, 2026", "Statement")]
    assert newest_of_type(rows, "Statement").index == 1


def test_no_row_of_the_wanted_type_is_refused():
    with pytest.raises(StatementError, match="no 'Statement' statement in the 5 row"):
        newest_of_type(INVESTMENT_ROWS, "Statement")


def test_two_rows_of_the_wanted_type_on_one_day_are_refused():
    rows = [Row("Oct 31, 2026", "Single"), Row("", "Single")]
    with pytest.raises(StatementError, match="same day"):
        newest_of_type(rows, "Single")


def test_the_file_name_uses_the_closing_month():
    assert statement_file_name("entity1_checking", OCT31) == "entity1_checking_2026-10.pdf"


@pytest.mark.parametrize(("text", "match", "found"), [
    ("FakeChecking (...1111)", "1111", True),
    ("FakeChecking ending in 1111.", "1111", True),
    ("FakeChecking ...91111", "1111", False),
    ("FakeChecking ...11112", "1111", False),
    ("fakechecking   ...1111", "FakeChecking ...1111", True),
    ("FakeSavings ...1111", "FakeChecking ...1111", False),
])
def test_a_page_shows_its_match_only_as_a_whole_number(text, match, found):
    assert page_shows(text, match) is found


def test_a_good_pdf_passes_every_check(tmp_path, pdf_of):
    path = tmp_path / "s.pdf"
    path.write_bytes(pdf_of())
    check_pdf_file(path)
    check_statement(pdf_text(path), "1111", OCT31)


@pytest.mark.parametrize("body", [b"<html>Session expired</html>", b"", b"%PDX-1.4"])
def test_a_file_thats_not_a_pdf_is_refused(tmp_path, body):
    path = tmp_path / "s.pdf"
    path.write_bytes(body + b" " * 10_000)
    with pytest.raises(StatementError, match="isn't a PDF"):
        check_pdf_file(path)


def test_a_tiny_pdf_is_refused(tmp_path, pdf_of):
    path = tmp_path / "s.pdf"
    path.write_bytes(pdf_of(pad=0))
    with pytest.raises(StatementError, match="bytes"):
        check_pdf_file(path)


def test_a_damaged_pdf_is_a_clear_failure(tmp_path):
    path = tmp_path / "s.pdf"
    path.write_bytes(b"%PDF-1.4\n" + b"garbage" * 2_000)
    with pytest.raises(StatementError):
        check_statement(pdf_text(path), "1111", OCT31)


def test_another_accounts_statement_is_refused_without_naming_the_suffix(text_of):
    with pytest.raises(StatementError, match="suffix") as e:
        check_statement(text_of(suffix="2222"), "1111", OCT31)
    assert "1111" not in str(e.value)


def test_a_statement_for_another_period_is_refused(text_of):
    with pytest.raises(StatementError, match="2026-10-31"):
        check_statement(text_of(closing="September 30, 2026"), "1111", OCT31)
