"""Move a downloaded invoice to its final place, without creating repeats.

A file's identity is its name: due date, unit, and invoice number. The site builds a
new PDF for every download, so the bytes differ even for the same invoice. That's why
an existing file is compared by its text. The text does change when an invoice is
paid or goes late, and then the newest version wins.
"""

import logging
import os
from enum import StrEnum
from pathlib import Path

from innago_portal.parse import Invoice, pdf_text

log = logging.getLogger(__name__)


class Outcome(StrEnum):
    SAVED = "saved"
    UNCHANGED = "unchanged"
    UPDATED = "updated"


def _words(text: str) -> list[str]:
    return text.split()


def _same_text(path: Path, text: str) -> bool:
    try:
        return _words(pdf_text(path)) == _words(text)
    except Exception as e:  # noqa: BLE001 - an unreadable old file is simply replaced
        log.warning("can't read the existing %s: %s", path.name, type(e).__name__)
        return False


def save_invoice(download: Path, invoice: Invoice, text: str,
                 invoices_dir: Path, replaced_dir: Path) -> Outcome:
    """Move `download` into `invoices_dir`. A version it replaces goes to `replaced_dir`."""
    target = invoices_dir / invoice.folder / invoice.file_name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        os.replace(download, target)
        return Outcome.SAVED
    if _same_text(target, text):
        download.unlink()
        return Outcome.UNCHANGED
    replaced_dir.mkdir(parents=True, exist_ok=True)
    os.replace(target, replaced_dir / target.name)
    os.replace(download, target)
    return Outcome.UPDATED
