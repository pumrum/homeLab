"""Saving a statement: it always replaces what's there."""

from buildium_portal.store import save_statement


def test_a_new_statement_creates_its_folder(tmp_path):
    download = tmp_path / "downloads" / "entity1.pdf"
    download.parent.mkdir()
    download.write_bytes(b"new")
    target = tmp_path / "data" / "2026-10" / "statement.pdf"
    save_statement(download, target)
    assert target.read_bytes() == b"new"
    assert not download.exists()


def test_a_second_run_replaces_the_first(tmp_path):
    target = tmp_path / "statement.pdf"
    target.write_bytes(b"old")
    download = tmp_path / "entity1.pdf"
    download.write_bytes(b"new")
    save_statement(download, target)
    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["statement.pdf"]
