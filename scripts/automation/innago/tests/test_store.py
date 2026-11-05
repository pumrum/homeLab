"""Saving invoices without repeats: synthetic data only."""

from innago_portal.parse import parse_invoice, pdf_text
from innago_portal.store import Outcome, save_invoice


def _download(tmp_path, bytes_):
    path = tmp_path / "download.pdf"
    path.write_bytes(bytes_)
    return path, pdf_text(path)


def _save(tmp_path, bytes_):
    path, text = _download(tmp_path, bytes_)
    return save_invoice(path, parse_invoice(text), text, tmp_path / "invoices",
                        tmp_path / "replaced")


TARGET = "invoices/2026-01/2026-01-01_1001_90000001-1.pdf"


def test_a_new_invoice_is_moved_into_its_month_folder(tmp_path, pdf_of):
    assert _save(tmp_path, pdf_of()) is Outcome.SAVED
    assert (tmp_path / TARGET).read_bytes() == pdf_of()
    assert not (tmp_path / "download.pdf").exists()


def test_same_text_is_unchanged_even_when_the_bytes_differ(tmp_path, pdf_of):
    _save(tmp_path, pdf_of())
    # The site stamps each download, so a later copy of the same invoice has other bytes
    later_copy = pdf_of() + b"\n% regenerated"
    assert _save(tmp_path, later_copy) is Outcome.UNCHANGED
    assert (tmp_path / TARGET).read_bytes() == pdf_of()
    assert not (tmp_path / "download.pdf").exists()
    assert not (tmp_path / "replaced").exists()


def test_changed_text_replaces_it_and_keeps_the_old_copy(tmp_path, pdf_of):
    _save(tmp_path, pdf_of())
    paid = pdf_of(paid="$100.00")
    assert _save(tmp_path, paid) is Outcome.UPDATED
    assert (tmp_path / TARGET).read_bytes() == paid
    assert (tmp_path / "replaced/2026-01-01_1001_90000001-1.pdf").read_bytes() == pdf_of()


def test_an_unreadable_old_file_is_replaced(tmp_path, pdf_of):
    target = tmp_path / TARGET
    target.parent.mkdir(parents=True)
    target.write_bytes(b"not a pdf")
    assert _save(tmp_path, pdf_of()) is Outcome.UPDATED
    assert target.read_bytes() == pdf_of()
