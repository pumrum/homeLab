"""Synthetic statements and ledger exports: made-up values in the real files' layouts."""

import csv
import io

import pytest

from buildium_portal.gl_parse import EXPECTED_COLUMNS

STATEMENT_PAGE = """{owner}
100 Example Street
Example City, ST 00000
Summary by property
200 Example Avenue
All properties
Beginning cash balance
$1,000.00
Ending cash balance
$1,500.00
Owner Draw
{owner}
Owner Draw
100.00
Prepared By: Example Management LLC
300 Example Road
Example City, ST 00000
Statement period
{period}
Statement date
11/15/2026
Rental Owner Statement
{owner} -
Generated 11/15/2026 14:18:38
Page
1"""


def statement_text(owner="FakeEntity One, LLC", period="10/1/2026 - 10/31/2026"):
    return STATEMENT_PAGE.format(owner=owner, period=period)


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


GL_ROWS = [
    {"Id": "1002", "PostingId": "5002", "EntryDate": "03/15/2026", "Amount": "-1,250.50",
     "JournalMemo": "FakeMemo Rent", "PayeeName": "FakeTenant Person", "UnitNumber": "Upper",
     "GLAccountName": "Rent Income", "GLAccountTypeName": "Income",
     "JournalCodeDescription": "Charge", "GLAccountNameRaw": "Rent Income"},
    {"Id": "1003", "PostingId": "5003", "EntryDate": "03/16/2026", "Amount": "1250.50",
     "JournalMemo": "FakeMemo two\nlines", "GLAccountName": "Operating Cash",
     "GLAccountTypeName": "Asset", "JournalCodeDescription": "Check",
     "GLAccountNameRaw": "Operating Cash"},
    {"Id": "", "PostingId": "", "JournalMemo": "FakeSubtotal line", "Amount": "0.00"},
]


def gl_csv(rows=None, bom=True, columns=EXPECTED_COLUMNS) -> bytes:
    """A synthetic general ledger export, with Windows line endings."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(columns)
    for row in GL_ROWS if rows is None else rows:
        writer.writerow([row.get(column, "") for column in columns])
    return (b"\xef\xbb\xbf" if bom else b"") + out.getvalue().encode("utf-8")


@pytest.fixture
def gl_csv_of():
    """Make a synthetic export: gl_csv_of(rows=[{...}], bom=..., columns=...)."""
    return gl_csv


@pytest.fixture
def text_of():
    return statement_text


@pytest.fixture
def pdf_of():
    """Make a synthetic statement PDF: pdf_of(owner=..., period=...)."""
    return lambda **values: make_pdf(statement_text(**values))
