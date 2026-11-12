"""Synthetic statements: made-up values in a statement's general shape."""

import pytest

STATEMENT = """FakeBank
Statement of account
Account number: XXXX XXXX XXXX {suffix}
Opening date {opening}
Closing date {closing}
Beginning balance $1,000.00
Ending balance $1,500.00
Page 1 of 1"""


def statement_text(suffix="1111", opening="October 1, 2026", closing="October 31, 2026"):
    return STATEMENT.format(suffix=suffix, opening=opening, closing=closing)


def make_pdf(text: str, pad: int = 6_000) -> bytes:
    """A one-page PDF holding `text`, padded with a comment to a real statement's size."""
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
    pdf = bytearray(b"%PDF-1.4\n%" + b"x" * pad + b"\n")
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
def pdf_of():
    """Make a synthetic statement PDF: pdf_of(suffix=..., closing=...)."""
    return lambda pad=6_000, **values: make_pdf(statement_text(**values), pad)


@pytest.fixture
def text_of():
    return statement_text


@pytest.fixture
def letter_pdf():
    """A synthetic letter: a short PDF with no account details."""
    return make_pdf("Important information about your account", pad=1_500)
