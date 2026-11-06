"""Move a checked statement to its final place, replacing any file already there.

Running again means wanting the freshest report, so nothing is compared or kept.
"""

import os
from pathlib import Path


def save_statement(download: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(download, target)
