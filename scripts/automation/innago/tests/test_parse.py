"""Naming an invoice from its text: synthetic data only."""

import datetime as dt

import pytest

from innago_portal.parse import Invoice, ParseError, parse_invoice, pdf_text


def test_reads_a_full_invoice(text_of):
    invoice = parse_invoice(text_of(unit="1003", number="90000042", due="March 1, 2026"))
    assert invoice == Invoice(dt.date(2026, 3, 1), "1003", "90000042")
    assert invoice.file_name == "2026-03-01_1003_90000042-1.pdf"
    assert invoice.folder == "2026-03"


def test_the_name_fits_the_merge_step_format(text_of):
    # The merge script splits names on: date_prefix-<part number>.pdf
    assert parse_invoice(text_of()).file_name.endswith("-1.pdf")


def test_reads_the_same_values_through_a_real_pdf(pdf_of, tmp_path):
    path = tmp_path / "ca01.pdf"
    path.write_bytes(pdf_of(unit="1005", number="90000005", due="November 1, 2026"))
    invoice = parse_invoice(pdf_text(path))
    assert invoice.file_name == "2026-11-01_1005_90000005-1.pdf"


@pytest.mark.parametrize(("due", "expected"), [
    ("May 1, 2026", dt.date(2026, 5, 1)),
    ("Sep 15, 2026", dt.date(2026, 9, 15)),
    ("2026-07-04", dt.date(2026, 7, 4)),
    ("12/31/2026", dt.date(2026, 12, 31)),
])
def test_date_formats(due, expected):
    assert parse_invoice(f"INVOICE# 90000001 Unit 1001 DUE DATE {due}").due == expected


def test_labels_are_not_taken_for_values():
    # These all matched the old rename script's patterns and gave wrong names
    text = "Invoice Date: May 1, 2026\nInvoice Notes\nInvoice # 90000123\nUnit Price 5\nUnit 1001"
    invoice = parse_invoice(text)
    assert invoice.number == "90000123"
    assert invoice.unit == "1001"


def test_a_label_alone_is_not_an_invoice_number():
    with pytest.raises(ParseError, match="invoice number"):
        parse_invoice("Invoice Date: May 1, 2026\nUnit 1001")


@pytest.mark.parametrize(("text", "missing"), [
    ("INVOICE# 90000001\nUnit 1001", "due date"),
    ("DUE DATE May 1, 2026\nINVOICE# 90000001", "unit"),
    ("DUE DATE May 1, 2026\nUnit 1001", "invoice number"),
])
def test_missing_values_are_errors(text, missing):
    with pytest.raises(ParseError, match=missing):
        parse_invoice(text)


def test_an_unreadable_date_is_an_error():
    with pytest.raises(ParseError, match="can't read the date"):
        parse_invoice("INVOICE# 90000001 Unit 1001 DUE DATE 2026-13-45")


def test_values_are_made_safe_for_a_file_name():
    invoice = parse_invoice("INVOICE# 900/001\nUnit 10a/b\nDUE DATE May 1, 2026")
    assert invoice.file_name == "2026-05-01_10AB_900001-1.pdf"
