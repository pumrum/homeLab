import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from fidelity_nav.parse import ParseError, parse_date, parse_money, parse_nav_page

FIXTURE = (Path(__file__).parent / "fixtures" / "fund_page.html").read_text(encoding="utf-8")
TODAY = dt.date(2026, 11, 3)


def _without(html: str, *selectors: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for sel in selectors:
        for el in soup.select(sel):
            el.decompose()
    return str(soup)


def test_reads_header():
    r = parse_nav_page(FIXTURE, TODAY)
    assert (r.nav, r.as_of, r.method) == (Decimal("12.34"), dt.date(2026, 11, 2), "header")


def test_falls_back_to_details_row():
    html = _without(FIXTURE, "mfl-daily-info-nav")
    r = parse_nav_page(html, TODAY)
    assert (r.nav, r.as_of, r.method) == (Decimal("12.34"), dt.date(2026, 11, 2), "details row")


def test_details_row_accepts_nav_label():
    html = _without(FIXTURE, "mfl-daily-info-nav").replace(
        "<span>Unit Value</span><span></span>", "<span>NAV</span><span></span>"
    )
    assert parse_nav_page(html, TODAY).method == "details row"


def test_ignores_other_asof_labels():
    # The 10/31 "Average Annual Returns" date must never be taken for the NAV date
    html = _without(FIXTURE, '[section="dailyInfo"][label="ASOF"]', "mfl-card-data-row")
    with pytest.raises(ParseError, match="NAV not found"):
        parse_nav_page(html, TODAY)


def test_text_fallback_when_structure_changes():
    html = """<html><body><div>Daily Info</div><p>AS OF 11/02/2026</p>
              <p>Unit Value</p><p>$12.34</p></body></html>"""
    r = parse_nav_page(html, TODAY)
    assert (r.nav, r.as_of, r.method) == (Decimal("12.34"), dt.date(2026, 11, 2), "text fallback")


def test_text_fallback_without_spaces_between_elements():
    html = "<div><b>AS OF 11/02/2026</b><b>Unit Value</b><b>$12.34</b><b>Change</b></div>"
    soup_text_unspaced = BeautifulSoup(html, "html.parser").get_text("", strip=True)
    assert "2026Unit Value$12.34" in soup_text_unspaced  # what the page's own text looks like
    r = parse_nav_page(f"<p>{soup_text_unspaced}</p>", TODAY)
    assert (r.nav, r.as_of, r.method) == (Decimal("12.34"), dt.date(2026, 11, 2), "text fallback")


def test_text_fallback_prose_form():
    html = "<p>The NAV $1,012.50 as of 10/30/2026 was published.</p>"
    r = parse_nav_page(html, TODAY)
    assert (r.nav, r.as_of) == (Decimal("1012.50"), dt.date(2026, 10, 30))


def test_not_found_is_clear():
    with pytest.raises(ParseError, match="NAV not found"):
        parse_nav_page("<html><body>Maintenance</body></html>", TODAY)


def test_future_date_rejected():
    with pytest.raises(ParseError, match="future"):
        parse_nav_page(FIXTURE, dt.date(2026, 11, 1))


def test_stale_date_rejected():
    with pytest.raises(ParseError, match="days old"):
        parse_nav_page(FIXTURE, dt.date(2026, 11, 13))


def test_ten_days_is_still_ok():
    assert parse_nav_page(FIXTURE, dt.date(2026, 11, 12)).as_of == dt.date(2026, 11, 2)


def test_zero_nav_rejected():
    html = FIXTURE.replace("$12.34", "$0.00")
    with pytest.raises(ParseError, match="not positive"):
        parse_nav_page(html, TODAY)


def test_parse_money():
    assert parse_money(" $1,018.90 ") == Decimal("1018.90")
    with pytest.raises(ParseError):
        parse_money("--")


def test_parse_date_formats():
    assert parse_date("AS OF 11/02/2026") == dt.date(2026, 11, 2)
    assert parse_date("as of Nov 2, 2026") == dt.date(2026, 11, 2)
    with pytest.raises(ParseError):
        parse_date("AS OF")
