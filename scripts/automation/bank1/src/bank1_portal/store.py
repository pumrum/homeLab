"""Move a checked file to its final place, replacing any file already there.

Running again means wanting the freshest copy, so nothing is compared or kept.
"""

import os
from pathlib import Path


def statement_path(data_dir: Path, file_name: str) -> Path:
    """Every account's files sit together: each name holds its account's suffix."""
    return data_dir / "data" / "statements" / file_name


def save_statement(download: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(download, target)


def activity_path(data_dir: Path, label: str) -> Path:
    """One file per account, replaced by every run: the label holds nothing private."""
    return data_dir / "data" / "activity" / f"{label}.csv"


def save_activity(download: Path, target: Path) -> None:
    """Copy, not move: the run folder keeps the raw download too."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_bytes(download.read_bytes())
    os.replace(temp, target)
