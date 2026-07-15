#!/usr/bin/env python3

import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

try:
    import fitz  # PyMuPDF
except Exception:
    print("ERROR: This script requires PyMuPDF. Install with: pip install pymupdf")
    sys.exit(1)

load_dotenv("secrets.env")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

COMPANY_NAME = "National Fuel"
ACCOUNT_PREFIX = "NFUEL_ACCOUNT_"

# Directory containing the bills, from secrets.env
path_nfuel_bills = Path(os.environ["PATH_NFUEL_BILLS"])

# Account -> "property_unit" lookup, one per line in secrets.env as NFUEL_ACCOUNT_<account>=<property_unit>
account_lookup = {
    key[len(ACCOUNT_PREFIX):]: value
    for key, value in os.environ.items()
    if key.startswith(ACCOUNT_PREFIX)
}

# Matches "Account Number 7935088 07" / "Account Number: 7935088 07"
ACCOUNT_PATTERN = re.compile(r"Account\s*Number:?\s*(\d+)\s+(\d+)", re.IGNORECASE)

# Matches "Account Summary as of July 7, 2026"
DATE_PATTERN = re.compile(r"Account Summary as of\s+([A-Za-z]+ \d{1,2},\s*\d{4})", re.IGNORECASE)


def extract_text(pdf_path: Path) -> str:
    with fitz.open(pdf_path) as doc:
        return "".join(page.get_text() for page in doc)


for file_path in path_nfuel_bills.glob("*.pdf"):
    try:
        text = extract_text(file_path)

        account_match = ACCOUNT_PATTERN.search(text)
        if not account_match:
            logging.warning(f"Account number not found, skipping: {file_path.name}")
            continue
        account_number = account_match.group(1) + account_match.group(2)

        date_match = DATE_PATTERN.search(text)
        if not date_match:
            logging.warning(f"Invoice date not found, skipping: {file_path.name}")
            continue
        invoice_date = datetime.strptime(date_match.group(1), "%B %d, %Y")
        formatted_date = invoice_date.strftime("%Y-%m-%d")

        property_unit = account_lookup.get(account_number)
        if not property_unit:
            logging.warning(f"No property/unit found for account {account_number}, skipping: {file_path.name}")
            continue

        new_name = f"{formatted_date}_{COMPANY_NAME}_{property_unit}.pdf"
        new_path = file_path.with_name(new_name)

        file_path.rename(new_path)
        logging.info(f"Renamed {file_path.name} to {new_name}")
    except Exception as e:
        logging.error(f"Error processing {file_path.name}: {e}")
