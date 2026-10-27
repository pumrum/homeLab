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

# Matches "ACCOUNT NUMBER\n 12345-67890" in the extracted PDF text
account_number_pattern = re.compile(r"ACCOUNT NUMBER\s+(\d{5}-\d{5})", re.IGNORECASE)

# Matches "DATE BILL ISSUED\nJul 24, 2026" in the extracted PDF text
date_issued_pattern = re.compile(r"DATE BILL ISSUED\s+([A-Za-z]+ \d{1,2},\s*\d{4})", re.IGNORECASE)

# Matches the service address line following "SERVICE FOR\n<name>\n", used in warnings
service_address_pattern = re.compile(r"SERVICE FOR\s*\n[^\n]+\n([^\n]+)", re.IGNORECASE)


def pdf_text(pdf_path: Path) -> str:
    import fitz  # PyMuPDF

    with fitz.open(pdf_path) as doc:
        return "".join(page.get_text() for page in doc)


def account_from_text(text: str) -> str | None:
    match = account_number_pattern.search(text)
    return match.group(1).replace("-", "") if match else None


def date_from_text(text: str) -> str | None:
    match = date_issued_pattern.search(text)
    if not match:
        return None

    issued_date = datetime.strptime(match.group(1), "%b %d, %Y")
    return issued_date.strftime("%Y-%m-%d")


try:
    import fitz  # noqa: F401  PyMuPDF
except ImportError:
    logging.error("PyMuPDF is required to evaluate PDF contents. Install with: pip install pymupdf")
    raise SystemExit(1)

for file_path in path_ngrid_bills.glob("*.pdf"):
    text = pdf_text(file_path)

    account_number = account_from_text(text)
    if not account_number:
        logging.warning(f"Could not determine account number from PDF contents, skipping: {file_path.name}")
        continue

    formatted_date = date_from_text(text)
    if not formatted_date:
        logging.warning(f"Could not determine bill date from PDF contents, skipping: {file_path.name}")
        continue

    property_unit = account_lookup.get(account_number)
    if not property_unit:
        address_match = service_address_pattern.search(text)
        address = address_match.group(1).strip() if address_match else "unknown address"
        logging.warning(
            f"No property/unit found for account {account_number} ({address}); "
            f"add {ACCOUNT_PREFIX}{account_number}=<property_unit> to secrets.env. Skipping: {file_path.name}"
        )
        continue

    new_name = f"{formatted_date}_{COMPANY_NAME}_{property_unit}.pdf"
    new_path = file_path.with_name(new_name)

    if new_path == file_path:
        # Already named correctly
        continue

    if new_path.exists():
        logging.warning(f"Target filename already exists, skipping: {file_path.name} -> {new_name}")
        continue

    file_path.rename(new_path)
    logging.info(f"Renamed {file_path.name} to {new_name}")
