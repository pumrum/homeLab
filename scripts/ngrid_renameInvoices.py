#!/usr/bin/env python3

import logging
import re
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

# Matches files like "NG_Bill_1234567890_2026_07_09.pdf"
filename_pattern = re.compile(r"NG_Bill_(\d+)_(\d{4})_(\d{2})_(\d{2})\.pdf", re.IGNORECASE)

for file_path in path_ngrid_bills.glob("*.pdf"):
    match = filename_pattern.match(file_path.name)
    if not match:
        logging.warning(f"Filename does not match expected pattern, skipping: {file_path.name}")
        continue

    account_number, year, month, day = match.groups()

    property_unit = account_lookup.get(account_number)
    if not property_unit:
        logging.warning(f"No property/unit found for account {account_number}, skipping: {file_path.name}")
        continue

    formatted_date = f"{year}-{month}-{day}"
    new_name = f"{formatted_date}_{COMPANY_NAME}_{property_unit}.pdf"
    new_path = file_path.with_name(new_name)

    file_path.rename(new_path)
    logging.info(f"Renamed {file_path.name} to {new_name}")
