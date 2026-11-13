from pathlib import Path

import pytest

from buildium_portal import gl_files
from buildium_portal.gl_files import SaveError, ledger_name, save_ledgers

FILES = {"entity1_Cash.csv": b"Id,Amount\n1,2\n", "entity1_Accrual.csv": b"Id,Amount\n3,4\n"}


def _names(folder):
    return sorted(p.name for p in folder.iterdir())


def test_the_name_is_the_key_and_method():
    assert ledger_name("entity1", "Cash") == "entity1_Cash.csv"


def test_saving_creates_the_folder_and_writes_each_file_exactly(tmp_path):
    folder = tmp_path / "data" / "ledgers"
    save_ledgers(FILES, folder)
    assert _names(folder) == sorted(FILES)
    for name, data in FILES.items():
        assert (folder / name).read_bytes() == data


def test_saving_replaces_the_last_runs_files_and_leaves_others(tmp_path):
    (tmp_path / "entity1_Cash.csv").write_bytes(b"old and much longer than the new one" * 100)
    (tmp_path / "retired_Cash.csv").write_bytes(b"not this run's")
    save_ledgers(FILES, tmp_path)
    assert (tmp_path / "entity1_Cash.csv").read_bytes() == FILES["entity1_Cash.csv"]
    assert (tmp_path / "retired_Cash.csv").read_bytes() == b"not this run's"
    assert not list(tmp_path.glob(".*.tmp"))


def test_an_empty_export_replaces_nothing(tmp_path):
    (tmp_path / "entity1_Cash.csv").write_bytes(b"old")
    with pytest.raises(SaveError, match="entity1_Accrual.csv is empty"):
        save_ledgers({**FILES, "entity1_Accrual.csv": b""}, tmp_path)
    assert _names(tmp_path) == ["entity1_Cash.csv"]
    assert (tmp_path / "entity1_Cash.csv").read_bytes() == b"old"


def test_a_failed_write_replaces_nothing_and_leaves_no_temporary_files(tmp_path, monkeypatch):
    (tmp_path / "entity1_Cash.csv").write_bytes(b"old")
    real = Path.write_bytes

    def fail_on_accrual(self, data):
        if "Accrual" in self.name:
            raise PermissionError("made up")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", fail_on_accrual)
    with pytest.raises(SaveError, match="none was replaced"):
        save_ledgers(FILES, tmp_path)
    assert _names(tmp_path) == ["entity1_Cash.csv"]
    assert (tmp_path / "entity1_Cash.csv").read_bytes() == b"old"


def test_a_file_that_doesnt_read_back_the_same_fails(tmp_path, monkeypatch):
    def replace_with_junk(source, target):
        Path(source).unlink()
        Path(target).write_bytes(b"something else")

    monkeypatch.setattr(gl_files.os, "replace", replace_with_junk)
    with pytest.raises(SaveError, match="doesn't read back the same"):
        save_ledgers(FILES, tmp_path)


def test_a_file_held_open_elsewhere_fails_by_name(tmp_path, monkeypatch):
    def locked(source, target):
        raise PermissionError("made up")

    monkeypatch.setattr(gl_files.os, "replace", locked)
    with pytest.raises(SaveError, match=r"couldn't replace entity1_Cash.csv \(PermissionError\)"):
        save_ledgers(FILES, tmp_path)
