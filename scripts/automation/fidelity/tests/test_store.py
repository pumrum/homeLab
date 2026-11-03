import datetime as dt
from decimal import Decimal

import pytest

from fidelity_nav.store import HEADER, NavRow, Outcome, append_nav, decide, read_rows

TZ = dt.timezone(dt.timedelta(hours=-5))
URL = "https://example.invalid/fund/TEST1"


def _row(nav: str, day: int = 2, symbol: str = "TEST1") -> NavRow:
    return NavRow(dt.date(2026, 11, day), symbol, Decimal(nav),
                  dt.datetime(2026, 11, 3, 8, 0, 5, tzinfo=TZ), URL)


def test_creates_file_with_header(tmp_path):
    path = tmp_path / "data" / "nav.csv"
    assert append_nav(path, _row("12.34")) is Outcome.ADDED
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == [
        ",".join(HEADER),
        f"2026-11-02,TEST1,12.34,2026-11-03T08:00:05-05:00,{URL}",
    ]


def test_same_date_same_nav_is_unchanged(tmp_path):
    path = tmp_path / "nav.csv"
    append_nav(path, _row("12.34"))
    before = path.read_bytes()
    assert append_nav(path, _row("12.340")) is Outcome.UNCHANGED  # Decimal equality
    assert path.read_bytes() == before


def test_same_date_new_nav_appends_and_latest_wins(tmp_path):
    path = tmp_path / "nav.csv"
    append_nav(path, _row("12.34"))
    assert append_nav(path, _row("12.35")) is Outcome.REVISED
    assert append_nav(path, _row("12.35")) is Outcome.UNCHANGED  # compares to the latest
    rows = read_rows(path)
    assert [r["nav"] for r in rows] == ["12.34", "12.35"]


def test_header_written_once_and_file_only_grows(tmp_path):
    path = tmp_path / "nav.csv"
    append_nav(path, _row("12.34", day=2))
    first = path.read_bytes()
    append_nav(path, _row("12.40", day=3))
    append_nav(path, _row("50.00", day=3, symbol="TEST2"))
    text = path.read_text(encoding="utf-8")
    assert path.read_bytes().startswith(first)
    assert text.count("as_of_date") == 1
    assert len(read_rows(path)) == 3


def test_wrong_header_is_refused(tmp_path):
    path = tmp_path / "nav.csv"
    path.write_text("date,value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header"):
        append_nav(path, _row("12.34"))
    assert path.read_text(encoding="utf-8") == "date,value\n"


def test_decide():
    assert decide(None, Decimal("1")) is Outcome.ADDED
    assert decide(Decimal("1.0"), Decimal("1")) is Outcome.UNCHANGED
    assert decide(Decimal("1"), Decimal("2")) is Outcome.REVISED
