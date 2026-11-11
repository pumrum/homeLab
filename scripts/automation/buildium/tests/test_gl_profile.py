"""The export's profile: enough to build the parser from, and nothing private."""

import pytest

from buildium_portal.gl_parse import EXPECTED_COLUMNS, decode, read_records
from buildium_portal.gl_profile import profile, shape


def test_there_are_53_expected_columns_with_no_repeats():
    assert len(EXPECTED_COLUMNS) == 53
    assert len({name.casefold() for name in EXPECTED_COLUMNS}) == 53


@pytest.mark.parametrize(("value", "expected"), [
    ("-1,234.50", "-9,999.99"),
    ("03/15/2026", "99/99/9999"),
    ("José Doe", "aaaa aaa"),
    ("two\nlines", "aaa\\naaaaa"),
    ("x" * 50, "a" * 40 + "..."),
])
def test_shape_keeps_the_form_and_removes_the_content(value, expected):
    assert shape(value) == expected


def test_a_utf8_export_loses_its_byte_order_mark():
    assert decode(b"\xef\xbb\xbfId") == ("Id", "utf-8")


def test_an_export_that_isnt_utf8_still_reads():
    assert decode(b"caf\xe9") == ("café", "cp1252")


def test_a_field_can_hold_a_line_break():
    assert read_records('Id,Memo\r\n1,"two\nlines"\r\n') == [["Id", "Memo"], ["1", "two\nlines"]]


def test_the_profile_reports_the_files_structure(gl_csv_of):
    text = profile(gl_csv_of())
    for line in ("byte-order mark: yes", "decoded as: utf-8", "records, with the header: 4",
                 "columns: 53 (expected 53)",
                 "same names in the same order, ignoring case: yes", "missing: none",
                 "rows: 3", "rows with a different number of fields: 0",
                 "rows with a blank Id: 1", "Id and PostingId pairs seen more than once: 0",
                 "GLAccountTypeName | Asset | 1 | 1"):
        assert line in text
    assert "line endings: CRLF 4, LF 1, CR 0" in text  # the extra LF is inside a memo
    assert "[-9,999.99] x1" in text


def test_the_profile_never_repeats_a_value(gl_csv_of):
    text = profile(gl_csv_of())
    for private in ("FakeMemo", "FakeTenant", "Person", "Upper", "1250", "1002", "5003",
                    "FakeSubtotal", "2026", "Rent Income", "Operating Cash"):
        assert private not in text


def test_the_profile_names_columns_that_changed_without_repeating_new_names(gl_csv_of):
    columns = ["SecretColumn" if name == "Amount" else name.upper() if name == "Id" else name
               for name in EXPECTED_COLUMNS]
    text = profile(gl_csv_of(rows=[{"ID": "1"}], columns=columns))
    assert "missing: Amount" in text
    assert "not expected, as shapes: aaaaaaaaaaaa" in text
    assert "different case from expected: ID" in text
    assert "SecretColumn" not in text
    assert "column 8" in text


def test_the_profile_counts_labels_in_any_case_and_repeated_pairs(gl_csv_of):
    rows = [{"Id": "1", "PostingId": "2", "GLAccountTypeName": "ASSET "},
            {"Id": "1", "PostingId": "2", "GLAccountTypeName": "Asset"}]
    text = profile(gl_csv_of(rows=rows))
    assert "GLAccountTypeName | Asset | 1 | 2" in text
    assert "Id and PostingId pairs seen more than once: 1" in text


@pytest.mark.parametrize("raw", [b"", b"<html><body>Sign in</body></html>", b"\xff\xfe\x00"])
def test_a_file_that_isnt_an_export_still_gets_a_profile(raw):
    assert "bytes:" in profile(raw)
    assert "Sign in" not in profile(raw)


def test_a_header_only_export_gets_a_profile(gl_csv_of):
    assert "rows: 0" in profile(gl_csv_of(rows=[]))
