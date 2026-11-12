"""The pure checks: dates, the newest statement, file names, and the PDF checks."""

import datetime as dt

import pytest

from bank1_portal.parse import (
    Row,
    StatementError,
    check_pdf_file,
    check_statement,
    dates_in,
    page_shows,
    pdf_text,
    plan_items,
    slug,
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


STATEMENT = {"Statement": ""}
INVESTMENT = {"Single": "single", "Multi": "multi"}


def _plain(dates):
    return [Row(d, "Statement") for d in dates]


@pytest.mark.parametrize("dates", [
    ["Oct 31, 2026", "Sep 30, 2026", "Aug 31, 2026"],
    ["Aug 31, 2026", "Sep 30, 2026", "Oct 31, 2026"],
    ["Sep 30, 2026", "Oct 31, 2026", "Aug 31, 2026"],
])
def test_the_newest_statement_is_the_latest_date_in_any_order(dates):
    [item] = plan_items(_plain(dates), STATEMENT)
    assert dates[item.index] == "Oct 31, 2026"
    assert (item.closing, item.part, item.letter, item.newest) == (OCT31, "", False, True)


def test_a_row_with_a_period_closes_on_its_last_date():
    [item] = plan_items(_plain(["Sep 1, 2026 - Sep 30, 2026", "Oct 1, 2026 - Oct 31, 2026"]),
                        STATEMENT)
    assert (item.index, item.closing) == (1, OCT31)


def test_two_statements_on_the_newest_day_are_refused():
    with pytest.raises(StatementError, match="two 'Statement' statements close on the same day"):
        plan_items(_plain(["Oct 31, 2026", "10/31/2026"]), STATEMENT)


def test_a_list_with_no_statement_is_refused():
    with pytest.raises(StatementError, match="no 'Statement' statement in the 2 row"):
        plan_items([Row("Oct 31, 2026", "Notice"), Row("", "Download")], STATEMENT)


# An investment account's list, as on the real page: a group per date, whose first row
# alone shows the date, with a Multi row and a Single row, each followed by any letters
INVESTMENT_ROWS = [
    Row("Oct 31, 2026", "Multi"),
    Row("", "Single"),
    Row("Sep 30, 2026", "Multi"),
    Row("", "2026 Annual Disclosure Cover Letter"),
    Row("", "Single"),
    Row("", "2026 Annual Disclosure Cover Letter"),
    Row("Aug 31, 2026", "Multi"),
    Row("", "Single"),
]
SEP30 = dt.date(2026, 9, 30)


def test_an_investment_saves_single_and_multi_and_files_each_letter_under_its_statement():
    items = plan_items(INVESTMENT_ROWS, INVESTMENT)
    assert [(i.index, i.closing, i.part, i.letter, i.newest) for i in items] == [
        (1, OCT31, "single", False, True),
        (0, OCT31, "multi", False, True),
        (3, SEP30, "multi-2026-annual-disclosure-cover-letter", True, False),
        (5, SEP30, "single-2026-annual-disclosure-cover-letter", True, False),
    ]


def test_a_letter_in_the_newest_group_is_marked_newest():
    rows = [Row("Oct 31, 2026", "Statement"), Row("", "Privacy Notice"),
            Row("Sep 30, 2026", "Statement")]
    assert [(i.part, i.letter, i.newest) for i in plan_items(rows, STATEMENT)] == [
        ("", False, True), ("privacy-notice", True, True)]


def test_a_missing_statement_type_on_the_newest_day_is_refused():
    rows = [Row("Oct 31, 2026", "Single"), Row("Sep 30, 2026", "Multi"), Row("", "Single")]
    with pytest.raises(StatementError, match="no 'Multi' statement on 2026-10-31"):
        plan_items(rows, INVESTMENT)


def test_type_names_ignore_case_and_spacing():
    [item] = plan_items([Row("Oct 31, 2026", "  statement ")], STATEMENT)
    assert item.index == 0


@pytest.mark.parametrize(("title", "expected"), [
    ("2026 Annual Disclosure Cover Letter", "2026-annual-disclosure-cover-letter"),
    ("Privacy Notice (Updated)", "privacy-notice-updated"),
    ("  ", ""),
])
def test_a_letters_title_becomes_a_safe_file_name_part(title, expected):
    assert slug(title) == expected


def test_file_names_follow_the_existing_scheme():
    assert statement_file_name("FakeBank", "9871", OCT31) == "2026-10-31_FakeBank_9871.pdf"
    assert (statement_file_name("FakeBank", "7983", OCT31, "single")
            == "2026-10-31_FakeBank_7983-single.pdf")


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
