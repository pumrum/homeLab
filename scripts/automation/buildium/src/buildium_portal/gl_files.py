"""Save every export to the ledger folder, replacing the files of the last run.

Each file is the export exactly as downloaded, and the run folder keeps its own copy. Every
file is written beside its target first, so a failure while writing changes nothing. Then each
replaces its target and is read back. Files in the folder that this run doesn't write are left
alone.
"""

import os
from collections.abc import Mapping
from pathlib import Path

LEDGER_FOLDER = Path("data") / "ledgers"


class SaveError(Exception):
    """A ledger file couldn't be saved or didn't read back the same. The message says which."""


def ledger_name(key: str, method: str) -> str:
    return f"{key}_{method}.csv"


def _temporary(target: Path) -> Path:
    return target.with_name(f".{target.name}.tmp")


def _write_temporaries(files: Mapping[str, bytes], folder: Path) -> None:
    """Write every file beside its target, or remove them all and raise."""
    written: list[Path] = []
    try:
        for name, data in files.items():
            temporary = _temporary(folder / name)
            written.append(temporary)
            temporary.write_bytes(data)
            if temporary.stat().st_size != len(data):
                raise SaveError(f"{name} came out a different size, so no ledger file was "
                                "replaced")
    except (OSError, SaveError) as e:
        for temporary in written:
            temporary.unlink(missing_ok=True)
        if isinstance(e, SaveError):
            raise
        raise SaveError(f"couldn't write a ledger file, so none was replaced "
                        f"({type(e).__name__})") from None


def save_ledgers(files: Mapping[str, bytes], folder: Path) -> None:
    """Write each file name's bytes to `folder`, replacing what's there, or raise SaveError."""
    for name, data in files.items():
        if not data:
            raise SaveError(f"{name} is empty, so no ledger file was replaced")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SaveError(f"couldn't create the ledger folder ({type(e).__name__})") from None
    _write_temporaries(files, folder)
    for name, data in files.items():
        target = folder / name
        try:
            os.replace(_temporary(target), target)
            matches = target.read_bytes() == data
        except OSError as e:
            raise SaveError(f"couldn't replace {name} ({type(e).__name__})") from None
        if not matches:
            raise SaveError(f"{name} doesn't read back the same as the export")
