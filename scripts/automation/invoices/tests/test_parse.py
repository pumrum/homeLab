from pathlib import Path

import pytest

from invoices_sync.parse import ParseError, parse_lines

FIXTURE = Path(__file__).parent / "fixtures" / "inv_example.txt"
EXPECTED = [
    "2026-01-15_Example Vendor Company",
    "2026-01-31_FakeBank_0000",
    "2026-01-31_Sample_Ledger_TEST",
]


def test_fixture_mirrors_a_real_file():
    assert parse_lines(FIXTURE.read_bytes()) == EXPECTED


@pytest.mark.parametrize("raw", [
    b"one\ntwo\n",  # Linux
    b"one\r\ntwo\r\n",  # Windows
    b"one\ntwo",  # no final line ending
    b"\xef\xbb\xbfone\ntwo\n",  # byte-order mark
    b"one\ntwo\n\n  \n",  # blank lines at the end
])
def test_line_endings_and_the_end_of_the_file(raw):
    assert parse_lines(raw) == ["one", "two"]


def test_lines_are_kept_exactly_as_they_are():
    raw = "  spaced  \n\nCafé_=SUM(A1)|x\n".encode()
    assert parse_lines(raw) == ["  spaced  ", "", "Café_=SUM(A1)|x"]


@pytest.mark.parametrize("raw", [b"", b"\n", b"  \r\n\r\n", b"\xef\xbb\xbf"])
def test_empty_file_is_refused(raw):
    with pytest.raises(ParseError, match="empty"):
        parse_lines(raw)


def test_a_file_that_is_not_utf8_is_refused_without_repeating_its_contents():
    with pytest.raises(ParseError, match="not valid UTF-8") as e:
        parse_lines(b"private\xff value\n")
    assert "private" not in str(e.value)
