"""
Import a secret file (e.g. a Google service account key) for this Windows user.

Encrypts the file with Windows DPAPI so only the current Windows user can
read it, and saves it as %USERPROFILE%\\.<prefix>\\<name>.dpapi. Run it signed
in as the user that runs the automation (e.g. robot). Then delete the
original download.

Run from scripts/automation:
    uv run python tools\\import_secret_file.py automation/google-service-account C:\\Users\\<you>\\Downloads\\<key>.json
    uv run python tools\\import_secret_file.py automation/google-service-account --check
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from automation_common import secrets
except ImportError:  # standalone: use the shared code from the repo clone
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common" / "src"))
    from automation_common import secrets

GOOGLE_SECRET = "automation/google-service-account"


def describe(name: str, data: bytes) -> str:
    """What to tell the person about the file, without showing any secret part."""
    if name != GOOGLE_SECRET:
        return f"{len(data)} bytes"
    try:
        info = json.loads(data)
    except ValueError as e:
        raise SystemExit("That file is not JSON; download the key again as JSON.") from e
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise SystemExit("That JSON is not a Google service account key.")
    return (f"service account key for {info.get('client_email', '?')}\n"
            "Share each Google Sheet with that email as an Editor.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="secret name, e.g. automation/google-service-account")
    ap.add_argument("file", nargs="?", type=Path, help="the file to import")
    ap.add_argument("--check", action="store_true",
                    help="only check that this user can read the stored secret")
    args = ap.parse_args()

    if args.check:
        data = secrets.read_secret_file(args.name)
        if data is None:
            print(f"missing: no {args.name} for this Windows user")
            return 1
        print(f"found: {describe(args.name, data)}")
        return 0

    if args.file is None or not args.file.is_file():
        ap.error("give the path of the file to import (or --check)")
    data = args.file.read_bytes()
    about = describe(args.name, data)
    path = secrets.write_secret_file(args.name, data)
    if secrets.read_secret_file(args.name) != data:
        print("Saved, but reading it back failed; nothing is usable yet.")
        return 1
    print(f"Saved {path}\n{about}")
    print(f"Now delete the original: {args.file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
