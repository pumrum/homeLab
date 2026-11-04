"""Run jobs one at a time on a single worker thread.

One at a time because a job may drive Edge and use its whole machine. A request
for a job that's already running or waiting is ignored, so a double press or a
schedule landing on a manual run can't stack up repeat runs.
"""

import logging
import queue
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from automation_agent.config import Job

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobResult:
    ok: bool
    detail: str
    reported_by_job: bool  # the job ran to its end, so it sent its own notification


def kill_tree(pid: int) -> None:
    """Stop a process and its children (Edge), which plain kill would leave behind."""
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)


def run_job(
    job: Job,
    scripts_dir: Path,
    cwd: Path,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    kill: Callable[[int], None] = kill_tree,
) -> JobResult:
    """Run a job's console script from this environment and wait for it. Never raises."""
    exe = scripts_dir / f"{job.command}.exe"
    if not exe.is_file():
        return JobResult(False, f"{exe.name} not found; run uv sync", False)
    try:
        proc = popen([str(exe)], cwd=cwd)
    except OSError as e:
        return JobResult(False, f"could not start: {type(e).__name__}", False)
    try:
        code = proc.wait(timeout=job.timeout_minutes * 60)
    except subprocess.TimeoutExpired:
        kill(proc.pid)
        proc.wait()
        return JobResult(False, f"stopped after {job.timeout_minutes} minute limit", False)
    return JobResult(code == 0, f"exit code {code}", True)


class Runner:
    """Takes run requests from any thread and works through them in order."""

    def __init__(
        self,
        run: Callable[[Job], JobResult],
        on_start: Callable[[Job], None],
        on_finish: Callable[[Job, JobResult], None],
    ) -> None:
        self._run = run
        self._on_start = on_start
        self._on_finish = on_finish
        self._queue: queue.Queue[Job] = queue.Queue()
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._running: str | None = None

    def request(self, job: Job) -> bool:
        """Queue a job. False if it's already running or waiting."""
        with self._lock:
            if job.name in self._pending or job.name == self._running:
                return False
            self._pending.add(job.name)
        self._queue.put(job)
        return True

    def is_active(self, name: str) -> bool:
        with self._lock:
            return name == self._running

    def is_idle(self) -> bool:
        """True if no job is running or waiting."""
        with self._lock:
            return self._running is None and not self._pending

    def start(self) -> None:
        threading.Thread(target=self._work, daemon=True, name="job-runner").start()

    def _work(self) -> None:
        while True:
            self.process_one(self._queue.get())

    def process_one(self, job: Job) -> None:
        """Run one job. A failure in any step is logged; the worker keeps going."""
        with self._lock:
            self._pending.discard(job.name)
            self._running = job.name
        try:
            self._on_start(job)
            result = self._run(job)
        except Exception as e:  # noqa: BLE001 - the worker must outlive any job
            log.exception("job %s failed unexpectedly", job.name)
            result = JobResult(False, type(e).__name__, False)
        with self._lock:
            self._running = None
        try:
            self._on_finish(job, result)
        except Exception:  # noqa: BLE001
            log.exception("reporting job %s failed", job.name)
