"""Synthetic invoices: made-up values in the same layout as the site's real PDFs."""

import pytest

INVOICE_TEMPLATE = """Total Due:
$100.00
Convenience Fee:
$0.00
Total Paid:
{paid}
Remaining Balance:
$100.00
Example Condo Association, Inc.
owner@example.invalid
(555) 555-0100
PO Box 0000,
EXAMPLE CITY, Florida 00000-
INVOICE#
{number}
DUE DATE
{due}
SUBJECT
Association Fee due on {due}
SENT TO
Example Owner
ADDRESS
Example Condo Association
Unit {unit}
1000-1012 Example Road, Example City,
Florida 00000
Late
ITEM
DESCRIPTION
QUANTITY
RATE
AMOUNT
Association Fee
Association Fee due on
{due}
1.00
$100.00
$100.00
Powered by"""


def invoice_text(unit="1001", number="90000001", due="January 1, 2026", paid="$0.00"):
    return INVOICE_TEMPLATE.format(unit=unit, number=number, due=due, paid=paid)


def make_pdf(text: str) -> bytes:
    """A one-page PDF holding `text`, one line per text line. No library needed."""
    lines = []
    for n, line in enumerate(text.splitlines()):
        safe = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        lines.append(f"BT /F1 10 Tf 40 {780 - n * 14} Td ({safe}) Tj ET")
    stream = "\n".join(lines).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        pdf += b"%010d 00000 n \n" % offset
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    return bytes(pdf)


@pytest.fixture
def text_of():
    return invoice_text


@pytest.fixture
def pdf_of():
    """Make a synthetic invoice PDF: pdf_of(unit="1001", number=..., due=..., paid=...)."""
    return lambda **values: make_pdf(invoice_text(**values))
