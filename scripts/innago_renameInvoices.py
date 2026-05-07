#!/usr/bin/env python3

from pathlib import Path
from datetime import datetime
import re
import sys

try:
    import fitz  # PyMuPDF
except Exception:
    print("ERROR: This script requires PyMuPDF. Install with: pip install pymupdf")
    sys.exit(1)

# ---------- Patterns ----------
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


# ---------- Helpers ----------
def extract_text(pdf_path: Path) -> str:
    parts = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            parts.append(page.get_text())
    return "".join(parts)


def find_first(patterns, text: str):
    for p in patterns:
        m = p.search(text)
        if m:
            return m.group(1).strip()
    return None


def normalize_date(date_str: str) -> str | None:
    date_str = re.sub(r"([A-Za-z]{3,}\s+\d{1,2},\s*\d{4}).*$", r"\1", date_str)
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def sanitize_token(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9\-_]", "", token).upper()


def unique_target(path: Path, current_file: Path) -> Path:
    """
    Return a unique path for renaming.
    If current_file already matches the desired path, return current_file unchanged.
    """
    if path == current_file:
        return current_file
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def build_new_name(text: str) -> str | None:
    raw_date = find_first(DATE_PATTERNS, text)
    if not raw_date:
        return None
    ymd = normalize_date(raw_date)
    if not ymd:
        return None

    unit = find_first(UNIT_PATTERNS, text)
    inv = find_first(INVOICE_NO_PATTERNS, text)
    if not unit or not inv:
        return None

    unit_s = sanitize_token(unit)
    inv_s = sanitize_token(inv)
    return f"{ymd}_{unit_s}_{inv_s}-1.pdf"


def process_pdf(pdf_path: Path) -> bool:
    try:
        text = extract_text(pdf_path)
        new_name = build_new_name(text)
        if not new_name:
            print(f"SKIP: {pdf_path.name} — missing required fields (date/unit/invoice).")
            return False

        target = unique_target(pdf_path.with_name(new_name), pdf_path)

        # If file is already named correctly, do nothing
        if target == pdf_path:
            print(f"OK: {pdf_path.name} already has correct name, no change.")
            return True

        pdf_path.rename(target)
        print(f"RENAMED: {pdf_path.name}  ->  {target.name}")
        return True
    except Exception as e:
        print(f"ERROR: {pdf_path.name} — {e}")
        return False


# ---------- Main ----------
def main():
    cwd = Path.cwd()
    pdfs = [p for p in cwd.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]

    if not pdfs:
        print("No PDF files found in the current folder.")
        return

    renamed = 0
    skipped = 0
    for pdf in sorted(pdfs, key=lambda x: x.name.lower()):
        if process_pdf(pdf):
            renamed += 1
        else:
            skipped += 1

    print(f"\nDone. Renamed: {renamed}  |  Skipped: {skipped}")

if __name__ == "__main__":
    main()
