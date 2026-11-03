import datetime as dt
from decimal import Decimal

import pytest

from automation_common.run import Run, Status
from automation_common.sheets import SheetError
from fidelity_nav.config import Config, Fund, Sheet
from fidelity_nav.main import sheet_stage
from fidelity_nav.sheet import HEADER
from fidelity_nav.store import NavRow, append_nav

SHEET_ID = "made-up-spreadsheet-id-0123456789"
CFG = Config([Fund("TEST1", "https://example.invalid/1")], Sheet(SHEET_ID, "NAV"))
TZ = dt.timezone(dt.timedelta(hours=-5))


class FakeWorksheet:
    def __init__(self, rows=None, fail_on=None):
        self.rows = rows or []
        self.appended = []
        self.fail_on = fail_on

    def read_rows(self, columns):
        if self.fail_on == "read":
            raise SheetError("Google Sheets error 500 reading the sheet")
        return self.rows

    def append_rows(self, rows):
        if self.fail_on == "append":
            raise SheetError("Google Sheets error 500 appending to the sheet")
        self.appended.extend(rows)


@pytest.fixture
def data_dir(tmp_path):
    append_nav(tmp_path / "data" / "nav.csv",
               NavRow(dt.date(2026, 11, 2), "TEST1", Decimal("12.34"),
                      dt.datetime(2026, 11, 3, 19, 30, 5, tzinfo=TZ), "https://example.invalid/1"))
    return tmp_path


def _stage(data_dir, ws, cfg=CFG):
    opened = []
    with Run.start("test", data_dir, notifier=lambda s, m: False) as run:
        sheet_stage(run, cfg, data_dir, open_ws=lambda i, t, m: opened.append((i, t)) or ws)
    return run, opened


def test_appends_new_rows(data_dir):
    ws = FakeWorksheet()
    run, opened = _stage(data_dir, ws)
    assert opened == [(SHEET_ID, "NAV")]
    assert ws.appended[0] == HEADER and len(ws.appended) == 2
    item = run.summary.items[-1]
    assert (item.stage, item.status, item.detail) == ("sheet", Status.OK, "appended 1 row(s)")


def test_up_to_date_is_unchanged(data_dir):
    ws = FakeWorksheet([HEADER, [46328, "TEST1", 12.34, "2026-11-03T19:30:05-05:00"]])
    run, _ = _stage(data_dir, ws)
    assert ws.appended == []
    assert run.summary.items[-1].status is Status.UNCHANGED
    assert run.exit_code == 0


@pytest.mark.parametrize("fail_on", ["read", "append"])
def test_sheet_failure_fails_the_run_without_raising(data_dir, fail_on):
    run, _ = _stage(data_dir, FakeWorksheet(fail_on=fail_on))
    item = run.summary.items[-1]
    assert (item.stage, item.status) == ("sheet", Status.FAILED)
    assert run.exit_code == 1


def test_no_sheet_section_skips_the_stage(data_dir):
    run, opened = _stage(data_dir, FakeWorksheet(), Config(CFG.funds))
    assert opened == [] and run.summary.items == []


def test_spreadsheet_id_never_reaches_the_log(data_dir):
    def leaky(i, t, m):
        m.add(i)  # what open_worksheet does
        raise SheetError(f"failed for {i}")

    with Run.start("test", data_dir, notifier=lambda s, m: False) as run:
        sheet_stage(run, CFG, data_dir, open_ws=leaky)
    for name in ("run.log", "report.txt", "summary.json"):
        assert SHEET_ID not in (run.run_dir / name).read_text(encoding="utf-8")
