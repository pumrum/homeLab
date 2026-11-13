"""Move a checked file to its final place, replacing any file already there.

Running again means wanting the freshest copy, so nothing is compared or kept. Activity CSVs
are named with the run's start time, so each run adds new ones beside the old.
"""

import datetime as dt
import os
from pathlib import Path


def statement_path(data_dir: Path, file_name: str) -> Path:
    """Every account's files sit together: each name holds its account's suffix."""
    return data_dir / "data" / "statements" / file_name


def save_statement(download: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(download, target)


def activity_path(data_dir: Path, abbreviation: str, suffix: str, when: dt.datetime) -> Path:
    """Named as the old process named them, stamped with the run's start time, so each run
    adds a file per account: <abbreviation>_<suffix>_<yyyymmdd-HHmmss>_Activity.csv."""
    name = f"{abbreviation}_{suffix}_{when:%Y%m%d-%H%M%S}_Activity.csv"
    return data_dir / "data" / "activity" / name


def save_activity(download: Path, target: Path) -> None:
    """Copy, not move: the run folder keeps the raw download too."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_bytes(download.read_bytes())
    os.replace(temp, target)
