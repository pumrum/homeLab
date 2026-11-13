"""Writing to the sheet, with a fake worksheet."""

import datetime as dt

import pytest

from automation_common.run import Run
from automation_common.sheets import SheetError
from buildium_portal.config import Dashboard, Gl, GlEntity
from buildium_portal.gl_parse import CASH_VIEW_COLUMNS, MASTER_COLUMNS
from buildium_portal.gl_sheet import check_sheet, sheet_stage, stamp_stage, tabs_to_write

GL = Gl("made-up-id", "FakeMaster",
        {"entity1": GlEntity("entity1", "CODE1", "FakeOne", "B5", write_tab=True),
         "entity2": GlEntity("entity2", "CODE2", "FakeTwo", "C9", write_tab=True)}, ())
ONE_ON = Gl("made-up-id", "FakeMaster",
            {"entity1": GL.entities["entity1"],
             "entity2": GlEntity("entity2", "CODE2", "FakeTwo", "C9")}, ())
VIEWS = {"entity1": [["a", dt.date(2026, 1, 1)]], "entity2": [["b", dt.date(2026, 1, 2)]]}
MASTER = [["m"]] * 2


class FakeBook:
    def __init__(self, bad_tab=None, missing_tab=None):
        self.writes = []
        self.bad_tab = bad_tab
        self.missing_tab = missing_tab

    def open(self, spreadsheet_id, tab, masker):
        if tab == self.missing_tab:
            raise SheetError(f'no tab named "{tab}" in the spreadsheet')
        book = self

        class Tab:
            def replace_block(self, start_cell, rows, date_columns=None, trim=False):
                if tab == book.bad_tab:
                    raise SheetError("Google Sheets error 500 writing to the sheet")
                book.writes.append((tab, start_cell, rows, date_columns, trim))

            def write_cell(self, cell, value, pattern=None):
                if tab == book.bad_tab:
                    raise SheetError("Google Sheets error 500 writing to the sheet")
                book.writes.append((tab, cell, value, pattern))

        return Tab()


@pytest.fixture
def run(tmp_path):
    with Run.start("buildium", tmp_path, notifier=lambda note, masker: True) as started:
        yield started


def test_the_sheet_check_opens_the_dashboard_too():
    with_dashboard = Gl("made-up-id", "FakeMaster", ONE_ON.entities, (),
                        Dashboard("FakeDash", "C3"))
    assert tabs_to_write(with_dashboard)[-1] == ("dashboard", "FakeDash")
    assert "dashboard" not in dict(tabs_to_write(ONE_ON))


STARTED = dt.datetime(2026, 11, 13, 12, 43, 40, tzinfo=dt.timezone(dt.timedelta(hours=-5)))


def test_the_dashboard_gets_the_runs_start_time(run):
    book = FakeBook()
    stamp_stage(run, "made-up-id", Dashboard("FakeDash", "C3"), STARTED, book.open)
    assert book.writes == [("FakeDash", "C3", STARTED, "yyyy-mm-dd hh:mm:ss")]
    assert "OK        sheet: dashboard  (master updated 2026-11-13 12:43:40)" in (
        run.summary.report_text())


def test_a_failed_dashboard_write_is_reported(run):
    stamp_stage(run, "made-up-id", Dashboard("FakeDash", "C3"), STARTED,
                FakeBook(bad_tab="FakeDash").open)
    assert run.summary.exit_code == 1
    assert "the master was written, but not its update time" in run.summary.report_text()


def test_the_sheet_stage_says_whether_the_master_was_written(run):
    assert sheet_stage(run, GL, VIEWS, MASTER, FakeBook().open) is True
    assert sheet_stage(run, GL, VIEWS, MASTER, FakeBook(bad_tab="FakeMaster").open) is False
    assert sheet_stage(run, GL, VIEWS, MASTER, FakeBook(missing_tab="FakeOne").open) is False


def test_each_tab_gets_its_rows_and_only_the_master_trims(run):
    book = FakeBook()
    sheet_stage(run, GL, VIEWS, MASTER, book.open)
    view_dates = {CASH_VIEW_COLUMNS.index("EntryDate"): "yyyy-mm-dd"}
    assert book.writes == [
        ("FakeOne", "B5", VIEWS["entity1"], view_dates, False),
        ("FakeTwo", "C9", VIEWS["entity2"], view_dates, False),
        ("FakeMaster", "A2", MASTER, {MASTER_COLUMNS.index("EntryDate"): "yyyy-mm-dd",
                                      MASTER_COLUMNS.index("EFTResultDate"): "m/d/yyyy"}, True),
    ]
    assert run.summary.exit_code == 0


def test_only_entities_with_gl_write_tab_on_get_their_tab(run):
    book = FakeBook(missing_tab="FakeTwo")  # a tab that's off isn't even opened
    sheet_stage(run, ONE_ON, VIEWS, MASTER, book.open)
    assert [write[:2] for write in book.writes] == [("FakeOne", "B5"), ("FakeMaster", "A2")]
    assert run.summary.exit_code == 0
    assert "sheet: entity2" not in run.summary.report_text()


def test_a_missing_tab_means_nothing_is_written_at_all(run):
    book = FakeBook(missing_tab="FakeMaster")
    sheet_stage(run, GL, VIEWS, MASTER, book.open)
    assert book.writes == []
    assert run.summary.exit_code == 1
    assert "nothing written" in run.summary.report_text()


def test_a_failed_write_doesnt_stop_the_other_tabs_and_is_reported(run):
    book = FakeBook(bad_tab="FakeOne")
    sheet_stage(run, GL, VIEWS, MASTER, book.open)
    assert [write[0] for write in book.writes] == ["FakeTwo", "FakeMaster"]
    assert run.summary.exit_code == 1
    assert "FAILED    sheet: entity1" in run.summary.report_text()
    assert "wrote 2 rows" in run.summary.report_text()


def test_the_check_opens_each_tab_that_will_be_written_and_writes_nothing(run):
    opened = []

    def open_ws(spreadsheet_id, tab, masker):
        opened.append(tab)
        return FakeBook().open(spreadsheet_id, tab, masker)

    assert check_sheet(run, ONE_ON, open_ws) is True
    assert opened == ["FakeOne", "FakeMaster"]  # entity2's tab is off, so it isn't needed
    assert run.summary.exit_code == 0


def test_the_check_fails_on_the_first_tab_that_wont_open(run):
    assert check_sheet(run, GL, FakeBook(missing_tab="FakeTwo").open) is False
    assert run.summary.exit_code == 1
    assert "FAILED    sheet check: entity2" in run.summary.report_text()
