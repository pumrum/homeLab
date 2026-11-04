"""Hand an update to the helper task, and read back how it went.

The agent can't update itself: Windows locks its files while it runs. So it asks
Task Scheduler to start the automation-agent-update task, which runs outside this
process, stops the agent, updates the code, and starts the agent again. The helper
leaves its result in a file, for the restarted agent to report.
"""

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from automation_agent.topics import FAIL, PASS

log = logging.getLogger(__name__)

UPDATE_TASK = "automation-agent-update"
RESULT_FILE = "update_result.txt"
UNKNOWN_VERSION = "unknown"
TIMEOUT_S = 30


def start_update_task(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> bool:
    """Ask Task Scheduler to start the helper. False if it couldn't. Never raises."""
    try:
        done = run(["schtasks", "/Run", "/TN", UPDATE_TASK], capture_output=True,
                   timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.error("can't start the %s task: %s", UPDATE_TASK, type(e).__name__)
        return False
    if done.returncode != 0:
        log.error("can't start the %s task: is it registered? (exit code %s)",
                  UPDATE_TASK, done.returncode)
        return False
    return True


def take_update_result(data_dir: Path) -> str | None:
    """The helper's result (PASS or FAIL) if it left one, removing it so it's told once."""
    path = data_dir / RESULT_FILE
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        log.error("can't read %s: %s", path, type(e).__name__)
        text = FAIL
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        log.error("can't remove %s: %s", path, type(e).__name__)
    return PASS if text == PASS else FAIL


def current_version(
    cwd: Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> str:
    """The running commit, for the version sensor. Never raises."""
    try:
        done = run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, capture_output=True,
                   text=True, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("can't read the version: %s", type(e).__name__)
        return UNKNOWN_VERSION
    version = done.stdout.strip()
    if done.returncode != 0 or not version:
        log.warning("can't read the version: git exit code %s", done.returncode)
        return UNKNOWN_VERSION
    return version
