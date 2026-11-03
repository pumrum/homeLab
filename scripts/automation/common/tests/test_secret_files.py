import sys
from pathlib import Path

import pytest

from automation_common.secrets import read_secret_file, secret_file_path, write_secret_file

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


def test_secret_file_path(tmp_path):
    assert secret_file_path("automation/google-service-account", tmp_path) == (
        tmp_path / ".automation" / "google-service-account.dpapi")
    assert secret_file_path("example/key", tmp_path) == tmp_path / ".example" / "key.dpapi"


@pytest.mark.parametrize("bad", ["nokey", "/x", "x/", "a/b/c", "a/../b", r"a\b"])
def test_secret_file_path_rejects_bad_names(bad, tmp_path):
    with pytest.raises(ValueError):
        secret_file_path(bad, tmp_path)


def test_missing_secret_file_is_none(tmp_path):
    assert read_secret_file("example/missing", tmp_path) is None


@windows_only
def test_round_trip_and_file_is_encrypted(tmp_path):
    data = b'{"made": "up", "value": "not-a-real-secret"}'
    path = write_secret_file("example/key", data, tmp_path)
    assert b"not-a-real-secret" not in path.read_bytes()
    assert read_secret_file("example/key", tmp_path) == data


@windows_only
def test_file_copied_to_another_name_does_not_decrypt(tmp_path):
    src = write_secret_file("example/one", b"made-up", tmp_path)
    other = secret_file_path("example/two", tmp_path)
    Path(other).write_bytes(src.read_bytes())
    with pytest.raises(RuntimeError, match="can't be decrypted"):
        read_secret_file("example/two", tmp_path)
