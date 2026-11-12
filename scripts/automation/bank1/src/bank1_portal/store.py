"""Move a checked statement to its final place, replacing any file already there.

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
