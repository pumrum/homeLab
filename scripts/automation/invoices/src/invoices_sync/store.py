"""Keep each downloaded file in the run folder, as it came, for debugging."""

from pathlib import Path


def save_raw(run_dir: Path, file_name: str, data: bytes) -> Path:
    """Save `data` as <run folder>\\files\\<file name> and return the path."""
    path = run_dir / "files" / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
