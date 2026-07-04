#!/usr/bin/env python3

import logging
import re
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv("secrets.env")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Directory containing the statements and the bank name, from secrets.env
path_bank_statements = Path(os.environ["PATH_BANK_STATEMENTS"])
bank_name = os.environ["BANK_NAME"]

# Matches files like "20240131-statements-1234-.pdf"
filename_pattern = re.compile(r"(\d{8})-statements-(\d{4})-\.pdf")

for file_path in path_bank_statements.glob("*.pdf"):
    match = filename_pattern.match(file_path.name)
    if not match:
        continue

    date_part, account_part = match.groups()
    formatted_date = f"{date_part[0:4]}-{date_part[4:6]}-{date_part[6:8]}"
    new_name = f"{formatted_date}_{bank_name}_{account_part}.pdf"
    new_path = file_path.with_name(new_name)

    file_path.rename(new_path)
    logging.info(f"Renamed {file_path.name} to {new_name}")
