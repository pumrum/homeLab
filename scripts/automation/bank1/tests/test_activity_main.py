"""bank1-activity's run decisions, with the browser steps faked: what's saved, written, and
reported for each account."""

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from automation_common.run import Run, Status
from automation_common.sheets import SheetError
from bank1_portal import activity_main
from bank1_portal.activity_main import activity_accounts, activity_stage, parse_args
from bank1_portal.config import parse_config
from bank1_portal.pages import FetchError

TODAY = dt.date(2026, 11, 11)
CSV = """Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
DEBIT,11/10/2026,EXAMPLE VENDOR,-12.50,ACH_DEBIT,100.00,
CREDIT,07/01/2026,EXAMPLE DEPOSIT,500.00,ACH_CREDIT,112.50,
"""


def _cfg():
    accounts = []
    for n, (label, kind, activity) in enumerate([
            ("entity1_checking", "checking", "sheet"),
            ("entity2_checking", "checking", "alone"),
            ("personal_card_1", "card", "sheet"),
            ("personal_invest_1", "investment", None),
            ("personal_card_2", "card", "alone")]):
        account = {"label": label, "kind": kind, "suffix": f"987{n}",
                   "statements_url": f"https://bank.example.invalid/statements/{label}"}
        if activity:
            account |= {"activity_url": f"https://bank.example.invalid/activity/{label}",
                        "activity_start": "2026-07-01"}
        if activity == "sheet":
            account["activity_sheet"] = {"sheet": "recon", "tab": f"Tab {label}",
                                         "start_cell": "B5"}
        accounts.append(account)
    return parse_config({"site": {"login_url": "https://bank.example.invalid/logon",
                                  "name_in_files": "FakeBank"},
                         "sheets": {"recon": {"spreadsheet_id": "made-up-id"}},
                         "accounts": accounts})


def _account(label):
    return next(a for a in _cfg().accounts if a.label == label)


@pytest.fixture
def run(tmp_path):
    with Run.start("test", tmp_path, notifier=lambda s, m: False) as r:
        yield r


class FakeBook:
    def __init__(self, fail=False):
        self.writes = []
        self.fail = fail

    def open(self, spreadsheet_id, tab, masker):
        book = self

        class Tab:
            def replace_block(self, start_cell, rows, date_columns=None, trim=False,
                              width=None):
                if book.fail:
                    raise SheetError("Google Sheets error 500 writing to the sheet")
                book.writes.append((spreadsheet_id, tab, start_cell, rows, width))
        return Tab()


@pytest.fixture
def site(monkeypatch):
    """The browser steps, faked: `csv` is what Download saves, `fail` a step that raises."""
    state = SimpleNamespace(csv=CSV, fail=None, steps=[])

    def step(name):
        def do(*args):
            state.steps.append(name)
            if state.fail == name:
                raise FetchError(f"{name} went wrong")
        return do

    def download(page, dest):
        state.steps.append("download")
        if state.fail == "download":
            raise FetchError("no download within 60s of clicking Download")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(state.csv, encoding="utf-8")

    for name in ("go_to_activity", "check_activity_page", "choose_date_range",
                 "check_file_type"):
        monkeypatch.setattr(activity_main, name, step(name))

    def fill(page, start, end):
        state.steps.append(("fill", start, end))
    monkeypatch.setattr(activity_main, "fill_dates", fill)
    monkeypatch.setattr(activity_main, "download", download)
    monkeypatch.setattr(activity_main, "save_page_evidence", lambda *a: [])
    return state


def _saved(tmp_path, label):
    return tmp_path / "data" / "activity" / f"{label}.csv"


def _item(run):
    return run.summary.items[-1]


def test_a_job_run_with_no_arguments_never_waits_for_a_person():
    args = parse_args([])
    assert not args.dump and not args.download_only and args.account is None


def test_dump_and_download_only_cannot_be_combined():
    with pytest.raises(SystemExit):
        parse_args(["--dump", "--download-only"])


def test_only_accounts_with_an_activity_url_are_in_a_run(run):
    assert [a.label for a in activity_accounts(run, _cfg(), None, dump=False)] == [
        "entity1_checking", "entity2_checking", "personal_card_1", "personal_card_2"]
    assert [a.label for a in activity_accounts(run, _cfg(), None, dump=True)] == [
        "entity1_checking", "personal_card_1"]


def test_naming_an_account_without_an_activity_url_fails(run):
    assert activity_accounts(run, _cfg(), "personal_invest_1", dump=False) == ()
    assert "activity_url" in _item(run).detail and run.summary.exit_code == 1


def test_a_good_download_is_saved_then_written_oldest_first_as_text(run, tmp_path, site):
    book = FakeBook()
    activity_stage(run, None, _account("entity1_checking"), tmp_path, TODAY, False, book.open)
    assert ("fill", "07/01/2026", "11/11/2026") in site.steps
    assert _saved(tmp_path, "entity1_checking").read_text(encoding="utf-8") == CSV
    [(spreadsheet_id, tab, cell, rows, width)] = book.writes
    assert (spreadsheet_id, tab, cell, width) == ("made-up-id", "Tab entity1_checking", "B5", 7)
    assert rows == [["CREDIT", "07/01/2026", "EXAMPLE DEPOSIT", "500.00", "ACH_CREDIT",
                     "112.50", ""],
                    ["DEBIT", "11/10/2026", "EXAMPLE VENDOR", "-12.50", "ACH_DEBIT",
                     "100.00", ""]]
    item = _item(run)
    assert item.status is Status.OK
    assert item.detail == "2 row(s) 07/01/2026 to 11/10/2026; saved and written to recon"


def test_every_run_replaces_the_file_and_the_block(run, tmp_path, site):
    book = FakeBook()
    account = _account("entity1_checking")
    activity_stage(run, None, account, tmp_path, TODAY, False, book.open)
    site.csv = CSV.replace("DEBIT,11/10/2026", "DEBIT,11/11/2026")
    activity_stage(run, None, account, tmp_path, TODAY, False, book.open)
    assert "11/11/2026" in _saved(tmp_path, "entity1_checking").read_text(encoding="utf-8")
    assert len(book.writes) == 2 and book.writes[1][3][-1][1] == "11/11/2026"


def test_an_account_without_a_sheet_is_only_saved(run, tmp_path, site):
    book = FakeBook()
    activity_stage(run, None, _account("entity2_checking"), tmp_path, TODAY, False, book.open)
    assert _saved(tmp_path, "entity2_checking").exists() and book.writes == []
    assert _item(run).detail.endswith("saved, no sheet")


def test_download_only_saves_nothing_and_writes_nothing(run, tmp_path, site):
    book = FakeBook()
    activity_stage(run, None, _account("entity1_checking"), tmp_path, TODAY, True, book.open)
    assert not _saved(tmp_path, "entity1_checking").exists() and book.writes == []
    assert (run.run_dir / "downloads" / "entity1_checking.csv").exists()
    assert _item(run).status is Status.OK and "download only" in _item(run).detail


@pytest.mark.parametrize("step", ["go_to_activity", "check_activity_page", "choose_date_range",
                                  "check_file_type",
                                  "download"])
def test_a_failed_browser_step_touches_neither_file_nor_sheet(run, tmp_path, site, step):
    target = _saved(tmp_path, "entity1_checking")
    target.parent.mkdir(parents=True)
    target.write_text("the last good copy", encoding="utf-8")
    site.fail = step
    book = FakeBook()
    activity_stage(run, None, _account("entity1_checking"), tmp_path, TODAY, False, book.open)
    assert target.read_text(encoding="utf-8") == "the last good copy" and book.writes == []
    assert _item(run).status is Status.FAILED


def test_a_file_that_fails_its_checks_is_kept_only_in_the_run_folder(run, tmp_path, site):
    site.csv = CSV.replace("Balance", "Running Balance")
    book = FakeBook()
    activity_stage(run, None, _account("entity1_checking"), tmp_path, TODAY, False, book.open)
    assert not _saved(tmp_path, "entity1_checking").exists() and book.writes == []
    assert (run.run_dir / "downloads" / "entity1_checking.csv").exists()
    assert _item(run).detail.startswith("check: ActivityError: the checking columns changed")


def test_a_failed_write_keeps_the_saved_file_and_fails_the_account(run, tmp_path, site):
    activity_stage(run, None, _account("entity1_checking"), tmp_path, TODAY, False,
                   FakeBook(fail=True).open)
    assert _saved(tmp_path, "entity1_checking").exists()
    assert _item(run).status is Status.FAILED
    assert _item(run).detail.startswith("write:")


def test_a_start_date_after_today_fails_before_opening_anything(run, tmp_path, site):
    activity_stage(run, None, _account("entity1_checking"), tmp_path, dt.date(2026, 6, 30),
                   False, FakeBook().open)
    assert site.steps == [] and "after today" in _item(run).detail


def test_the_report_never_shows_a_tab_name_or_spreadsheet_id(run, tmp_path, site):
    activity_stage(run, None, _account("entity1_checking"), tmp_path, TODAY, False,
                   FakeBook().open)
    activity_stage(run, None, _account("entity1_checking"), tmp_path, TODAY, False,
                   FakeBook(fail=True).open)
    shown = json.dumps([item.detail for item in run.summary.items])
    assert "Tab " not in shown and "made-up-id" not in shown
