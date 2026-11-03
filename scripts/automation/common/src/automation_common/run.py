"""One run of an automation: run folder, logging, summary, and exit code.

    with Run.start(PROCESS, process_dir(PROCESS)) as run:
        ...                      # record each item with run.summary.record(...)
    return run.exit_code         # 0 all OK, 1 any item failed

Leaving the `with` block (normally or by an exception) always writes
`summary.json` and `report.txt`, sends the summary to the webhook if one is
configured, and closes the log. An unexpected exception is recorded as a
failed item, not re-raised, so the exit code still reflects it.
"""

import datetime as dt
import json
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from automation_common.notify import notify_summary
from automation_common.secrets import SecretMasker

log = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class Status(StrEnum):
    OK = "ok"
    UNCHANGED = "unchanged"
    FAILED = "failed"


@dataclass(frozen=True)
class ItemResult:
    stage: str
    item: str
    status: Status
    detail: str = ""


@dataclass
class Summary:
    process: str
    run_id: str
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    items: list[ItemResult] = field(default_factory=list)

    def record(self, stage: str, item: str, status: Status, detail: str = "") -> None:
        self.items.append(ItemResult(stage, item, status, detail))
        level = logging.ERROR if status is Status.FAILED else logging.INFO
        log.log(level, "%s %s: %s%s", stage, item, status, f" ({detail})" if detail else "")

    def counts(self) -> dict[str, int]:
        found = Counter(i.status.value for i in self.items)
        return {s.value: found.get(s.value, 0) for s in Status}

    @property
    def exit_code(self) -> int:
        """0 when every item is OK or unchanged; 1 if any failed or nothing ran."""
        if not self.items:
            return 1
        return 1 if any(i.status is Status.FAILED for i in self.items) else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "process": self.process,
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": self.finished_at.isoformat(timespec="seconds")
            if self.finished_at
            else None,
            "result": "ok" if self.exit_code == 0 else "failed",
            "counts": self.counts(),
            "items": [asdict(i) | {"status": i.status.value} for i in self.items],
        }

    def report_text(self) -> str:
        lines = [f"{self.process} run {self.run_id}", ""]
        for i in self.items:
            lines.append(
                f"{i.status.value.upper():<9} {i.stage}: {i.item}"
                + (f"  ({i.detail})" if i.detail else "")
            )
        counts = ", ".join(f"{n} {s}" for s, n in self.counts().items())
        result = "OK" if self.exit_code == 0 else "FAILED"
        lines += ["", f"Result: {result}  ({counts})"]
        return "\n".join(lines) + "\n"


class _MaskingFormatter(logging.Formatter):
    # 2026-11-03 12:47:49.275, not Python's default comma before the milliseconds
    default_msec_format = "%s.%03d"

    def __init__(self, masker: SecretMasker) -> None:
        super().__init__(LOG_FORMAT)
        self._masker = masker

    def format(self, record: logging.LogRecord) -> str:
        return self._masker.mask(super().format(record))


def make_run_dir(runs_dir: Path, now: dt.datetime) -> Path:
    """Create runs/<yyyyMMdd_HHmmss>/, adding _2, _3... if that second is taken."""
    stamp = now.strftime("%Y%m%d_%H%M%S")
    for n in range(1, 100):
        candidate = runs_dir / (stamp if n == 1 else f"{stamp}_{n}")
        try:
            candidate.mkdir(parents=True)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"could not create a run folder under {runs_dir}")


Notifier = Callable[[dict[str, Any], SecretMasker], bool]


class Run:
    """A single run. Create with Run.start(); use as a context manager."""

    def __init__(
        self, process: str, run_dir: Path, now: dt.datetime, notifier: Notifier
    ) -> None:
        self.process = process
        self.run_dir = run_dir
        self.masker = SecretMasker()
        self.summary = Summary(process, run_dir.name, now)
        self.exit_code = 1
        self._notifier = notifier
        self._failures = 0
        self._handlers: list[logging.Handler] = []

    @classmethod
    def start(
        cls,
        process: str,
        data_dir: Path,
        now: dt.datetime | None = None,
        notifier: Notifier = notify_summary,
    ) -> Self:
        now = now or dt.datetime.now().astimezone()
        run = cls(process, make_run_dir(data_dir / "runs", now), now, notifier)
        run._setup_logging()
        log.info("%s run %s started; run folder %s", process, run.run_dir.name, run.run_dir)
        return run

    def next_failure_prefix(self) -> str:
        """Name prefix for the next failure's evidence: fail_01, fail_02, ..."""
        self._failures += 1
        return f"fail_{self._failures:02d}"

    def _setup_logging(self) -> None:
        fmt = _MaskingFormatter(self.masker)
        file_handler = logging.FileHandler(self.run_dir / "run.log", encoding="utf-8")
        console = logging.StreamHandler()
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        for h in (file_handler, console):
            h.setFormatter(fmt)
            root.addHandler(h)
            self._handlers.append(h)

    def _close_logging(self) -> None:
        root = logging.getLogger()
        for h in self._handlers:
            root.removeHandler(h)
            h.close()
        self._handlers.clear()

    def finish(self) -> int:
        """Write the summary and report, notify, close the log. Returns the exit code."""
        try:
            self.summary.finished_at = dt.datetime.now().astimezone()
            data = self.summary.to_dict()
            (self.run_dir / "summary.json").write_text(
                self.masker.mask(json.dumps(data, indent=2)), encoding="utf-8"
            )
            (self.run_dir / "report.txt").write_text(
                self.masker.mask(self.summary.report_text()), encoding="utf-8"
            )
            self._notifier(data, self.masker)
            self.exit_code = self.summary.exit_code
            log.info("run finished: %s, exit code %s", data["result"], self.exit_code)
        except Exception:  # noqa: BLE001 - the summary step itself must never crash
            log.exception("writing the run summary failed")
            self.exit_code = 1
        finally:
            self._close_logging()
        return self.exit_code

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        suppress = False
        if exc is not None and isinstance(exc, Exception):
            log.error("unexpected error", exc_info=(exc_type, exc, tb))
            self.summary.record("run", self.process, Status.FAILED, f"{type(exc).__name__}: {exc}")
            suppress = True
        self.finish()
        return suppress
