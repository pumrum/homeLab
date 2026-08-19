#!/usr/bin/env python3

import logging
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv("secrets.env")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

COMPANY_NAME = "National Grid"
ACCOUNT_PREFIX = "NGRID_ACCOUNT_"

# Directory containing the bills, from secrets.env
path_ngrid_bills = Path(os.environ["PATH_NGRID_BILLS"])

# Account -> "property_unit" lookup, one per line in secrets.env as NGRID_ACCOUNT_<account>=<property_unit>
account_lookup = {
    key[len(ACCOUNT_PREFIX):]: value
    for key, value in os.environ.items()
    if key.startswith(ACCOUNT_PREFIX)
}

# Matches files like "NG_Bill_1234567890_2026_07_09.pdf" or "NG_Bill_1234567890_undefined.pdf".
# The date portion of the filename is not trusted; the invoice date always comes from the
# PDF contents instead.
filename_pattern = re.compile(r"NG_Bill_(\d+)_.+\.pdf", re.IGNORECASE)

# Matches "DATE BILL ISSUED\nJul 24, 2026" in the extracted PDF text
date_issued_pattern = re.compile(r"DATE BILL ISSUED\s+([A-Za-z]+ \d{1,2},\s*\d{4})", re.IGNORECASE)


def date_from_pdf_contents(pdf_path: Path) -> str | None:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logging.error("PyMuPDF is required to evaluate PDF contents. Install with: pip install pymupdf")
        return None

    with fitz.open(pdf_path) as doc:
        text = "".join(page.get_text() for page in doc)

    match = date_issued_pattern.search(text)
    if not match:
        return None

    issued_date = datetime.strptime(match.group(1), "%b %d, %Y")
    return issued_date.strftime("%Y-%m-%d")


for file_path in path_ngrid_bills.glob("*.pdf"):
    match = filename_pattern.match(file_path.name)
    if not match:
        logging.warning(f"Filename does not match expected pattern, skipping: {file_path.name}")
        continue

    account_number = match.group(1)
    formatted_date = date_from_pdf_contents(file_path)
    if not formatted_date:
        logging.warning(f"Could not determine bill date from PDF contents, skipping: {file_path.name}")
        continue

    property_unit = account_lookup.get(account_number)
    if not property_unit:
        logging.warning(f"No property/unit found for account {account_number}, skipping: {file_path.name}")
        continue

    new_name = f"{formatted_date}_{COMPANY_NAME}_{property_unit}.pdf"
    new_path = file_path.with_name(new_name)

    if new_path.exists():
        logging.warning(f"Target filename already exists, skipping: {file_path.name} -> {new_name}")
        continue

    file_path.rename(new_path)
    logging.info(f"Renamed {file_path.name} to {new_name}")
