#!/usr/bin/env python3

import logging
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

try:
    import fitz  # PyMuPDF
except Exception:
    print("ERROR: This script requires PyMuPDF. Install with: pip install pymupdf")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Matches files like "2026-07-01_8904_10138105-1.pdf" -> prefix="2026-07-01_8904_10138105", part="1"
FILENAME_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}_.+)-(\d+)\.pdf$", re.IGNORECASE)


def merge_pdfs(parts: list[Path], target: Path) -> None:
    merged = fitz.open()
    for part in parts:
        with fitz.open(part) as src:
            merged.insert_pdf(src)
    merged.save(target)
    merged.close()


def main():
    cwd = Path.cwd()

    groups = defaultdict(dict)  # prefix -> {part_number: Path}
    for file_path in cwd.glob("*.pdf"):
        match = FILENAME_PATTERN.match(file_path.name)
        if not match:
            continue
        prefix, part = match.groups()
        groups[prefix][int(part)] = file_path

    if not groups:
        print("No matching invoice files found in the current folder.")
        return

    for prefix, parts_by_number in groups.items():
        deposit_path = cwd / f"{prefix}_Deposit.pdf"
        if deposit_path.exists():
            logging.warning(f"SKIP: {deposit_path.name} already exists, skipping group: {prefix}")
            continue

        ordered_parts = [parts_by_number[n] for n in sorted(parts_by_number)]

        if len(ordered_parts) == 1:
            try:
                shutil.copy2(ordered_parts[0], deposit_path)
                logging.info(f"DUPLICATED: {ordered_parts[0].name} -> {deposit_path.name}")
            except Exception as e:
                logging.error(f"ERROR duplicating {prefix}: {e}")
                continue
        else:
            try:
                merge_pdfs(ordered_parts, deposit_path)
                logging.info(f"COMBINED: {[p.name for p in ordered_parts]} -> {deposit_path.name}")
            except Exception as e:
                logging.error(f"ERROR combining {prefix}: {e}")
                continue

        base_part = parts_by_number.get(1)
        if not base_part:
            logging.warning(f"No '-1' part found for {prefix}, skipping rename.")
            continue

        base_path = cwd / f"{prefix}.pdf"
        base_part.rename(base_path)
        logging.info(f"RENAMED: {base_part.name} -> {base_path.name}")


if __name__ == "__main__":
    main()
