"""Read an invoice PDF's text and work out the file name it should be saved under.

The name is {due date}_{unit}_{invoice number}-1.pdf. The site gives each download a
random name, so the PDF's text is the only source for these. The merge step for these
files expects this exact format, so don't change it on its own.
"""

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

DATE_PATTERNS = [
    re.compile(r"DUE\s*DATE\s*[:\-]?\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE),
    re.compile(r"DUE\s*DATE\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    re.compile(r"DUE\s*DATE\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE),
    re.compile(r"(?:Invoice\s*Date|Date)\s*[:\-]?\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE),
    re.compile(r"(?:Invoice\s*Date|Date)\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    re.compile(r"(?:Invoice\s*Date|Date)\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE),
    re.compile(r"SUBJECT.*?due\s+on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE | re.DOTALL),
    re.compile(r"RECEIVED\s+ON\s+([A-Za-z]{3,}\s+\d{1,2},\s*\d{4})", re.IGNORECASE),
]
UNIT_PATTERNS = [
    re.compile(r"\bUnit\s*(?:No\.?|Number|#)?\s*[:\-]?\s*([A-Za-z0-9\-_\/]+)", re.IGNORECASE),
]
INVOICE_NO_PATTERNS = [
    re.compile(r"\bInvoice\s*(?:No\.?|Number|#)\s*[:\-]?\s*([A-Za-z0-9\-_\/]+)", re.IGNORECASE),
    re.compile(r"\bInvoice\s*[:\-]?\s*#?\s*([A-Za-z0-9\-_\/]+)", re.IGNORECASE),
]
DATE_FORMATS = ["%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"]


class ParseError(Exception):
    """A value the file name needs is missing from the invoice's text."""


@dataclass(frozen=True)
class Invoice:
    due: dt.date
    unit: str
    number: str

    @property
    def file_name(self) -> str:
        return f"{self.due.isoformat()}_{self.unit}_{self.number}-1.pdf"

    @property
    def folder(self) -> str:
        return f"{self.due:%Y-%m}"


def pdf_text(path: Path) -> str:
    return "".join(page.extract_text() for page in PdfReader(path).pages)


def _first(patterns: Sequence[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def _first_with_digit(patterns: Sequence[re.Pattern[str]], text: str) -> str | None:
    """The first match with a digit, so labels like "Invoice Date" or "Unit Price" don't count."""
    for pattern in patterns:
        for match in pattern.finditer(text):
            if any(ch.isdigit() for ch in match.group(1)):
                return match.group(1).strip()
    return None


def _due_date(text: str) -> dt.date:
    raw = _first(DATE_PATTERNS, text)
    if raw is None:
        raise ParseError("no due date found")
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ParseError(f"can't read the date '{raw}'")


def _token(value: str | None, what: str) -> str:
    """Only characters that are safe in a file name, in uppercase."""
    token = re.sub(r"[^A-Za-z0-9\-_]", "", value or "").upper()
    if not token:
        raise ParseError(f"no {what} found")
    return token


def parse_invoice(text: str) -> Invoice:
    """Read the due date, unit, and invoice number. Raises ParseError if one is missing."""
    return Invoice(
        _due_date(text),
        _token(_first_with_digit(UNIT_PATTERNS, text), "unit"),
        _token(_first_with_digit(INVOICE_NO_PATTERNS, text), "invoice number"),
    )
