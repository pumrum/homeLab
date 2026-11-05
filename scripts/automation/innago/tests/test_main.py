"""The run's stages with a fake invoice list: synthetic data only."""

from contextlib import contextmanager

import pytest

from automation_common.run import Run, Status
from innago_portal.config import Config, Invoices, Site
from innago_portal.login import LoginError
from innago_portal.main import browser_stage, download_stage, load_stage, main

SITE = Site("https://sign-in.example.invalid/login", "https://app.example.invalid/")
UNITS = ("1001", "1002", "1003")
CFG = Config(SITE, Invoices("https://app.example.invalid/invoices", UNITS))
TARGET = "data/invoices/2026-01/2026-01-01_{unit}_9000{unit}-1.pdf"


class FakeSource:
    """Rows are PDF bytes, or an Exception to raise when that row is downloaded."""

    def __init__(self, rows, open_error=None, labels=None):
        self.rows = rows
        self.open_error = open_error
        self.labels = labels
        self.evidence = []
        self.structure = []

    def open(self):
        if self.open_error:
            raise self.open_error
        return len(self.rows)

    def label(self, index):
        return self.labels[index] if self.labels else f"row {index + 1}"

    def download(self, index, dest):
        row = self.rows[index]
        if isinstance(row, Exception):
            raise row
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(row)

    def save_evidence(self, run_dir, prefix):
        self.evidence.append(prefix)

    def save_structure(self, run_dir, prefix):
        self.structure.append(prefix)


@pytest.fixture
def row(pdf_of):
    return lambda unit, **values: pdf_of(unit=unit, number=f"9000{unit}", **values)


@pytest.fixture
def run(tmp_path):
    with Run.start("test", tmp_path / "out", notifier=lambda s, m: False) as r:
        yield r


def _items(run):
    return {(i.stage, i.item): i for i in run.summary.items}


def _status(run, item):
    return _items(run)[("invoice", item)].status


def test_all_units_saved(run, row, tmp_path):
    source = FakeSource([row(u) for u in UNITS])
    download_stage(run, source, CFG, tmp_path, dump=False)
    assert run.summary.exit_code == 0
    for unit in UNITS:
        assert _status(run, unit) is Status.OK
        assert (tmp_path / TARGET.format(unit=unit)).exists()
    assert source.structure == []  # only --dump saves the page's structure


def test_running_again_changes_nothing(run, row, tmp_path):
    for _ in range(2):
        run.summary.items.clear()
        download_stage(run, FakeSource([row(u) for u in UNITS]), CFG, tmp_path, dump=False)
    assert [i.status for i in run.summary.items] == [Status.UNCHANGED] * 3
    assert run.summary.exit_code == 0
    assert len(list((tmp_path / "data/invoices/2026-01").iterdir())) == 3


def test_a_missing_unit_fails_the_run(run, row, tmp_path):
    download_stage(run, FakeSource([row("1001"), row("1003")]), CFG, tmp_path, dump=False)
    assert _status(run, "1001") is Status.OK
    assert _status(run, "1003") is Status.OK
    assert _status(run, "1002") is Status.FAILED
    assert "no usable invoice" in _items(run)[("invoice", "1002")].detail
    assert run.summary.exit_code == 1


def test_an_empty_list_fails_every_unit(run, tmp_path):
    download_stage(run, FakeSource([]), CFG, tmp_path, dump=False)
    assert [i.status for i in run.summary.items] == [Status.FAILED] * 3


def test_a_unit_outside_the_config_is_not_saved(run, row, tmp_path):
    download_stage(run, FakeSource([row(u) for u in UNITS] + [row("9999")]),
                   CFG, tmp_path, dump=False)
    assert _status(run, "9999") is Status.FAILED
    assert not (tmp_path / TARGET.format(unit="9999")).exists()
    assert (run.run_dir / "downloads" / "invoice_04.pdf").exists()  # kept for a look
    assert run.summary.exit_code == 1


def test_a_second_invoice_for_a_unit_is_not_saved(run, pdf_of, tmp_path):
    rows = [pdf_of(unit="1001", number="90001001"), pdf_of(unit="1001", number="90002222")]
    download_stage(run, FakeSource(rows), CFG, tmp_path, dump=False)
    saved = [p.name for p in (tmp_path / "data/invoices/2026-01").iterdir()]
    assert saved == ["2026-01-01_1001_90001001-1.pdf"]
    details = [i.detail for i in run.summary.items if i.status is Status.FAILED]
    assert any("second invoice" in d for d in details)


def test_one_failed_download_does_not_stop_the_others(run, row, tmp_path):
    source = FakeSource([row("1001"), RuntimeError("button went away"), row("1003")])
    download_stage(run, source, CFG, tmp_path, dump=False)
    assert _status(run, "1001") is Status.OK
    assert _status(run, "1003") is Status.OK
    assert _status(run, "row 2") is Status.FAILED
    assert _status(run, "1002") is Status.FAILED  # the check for missing units still runs
    assert source.evidence == ["fail_01"]


def test_a_failed_unit_from_the_list_is_reported_once_by_its_unit(run, row, tmp_path):
    source = FakeSource([row("1001"), RuntimeError("button went away"), row("1003")],
                        labels=list(UNITS))
    download_stage(run, source, CFG, tmp_path, dump=False)
    failures = [i for i in run.summary.items if i.status is Status.FAILED]
    assert [i.item for i in failures] == ["1002"]
    assert "button went away" in failures[0].detail


def test_a_pdf_for_a_different_unit_than_its_row_is_not_saved(run, row, tmp_path):
    source = FakeSource([row("1001"), row("1003"), row("1003")], labels=list(UNITS))
    download_stage(run, source, CFG, tmp_path, dump=False)
    detail = _items(run)[("invoice", "1002")].detail
    assert "the list says unit 1002, but the PDF says 1003" in detail
    assert _status(run, "1003") is Status.OK


def test_a_pdf_that_cannot_be_named_is_kept(run, row, tmp_path):
    source = FakeSource([row("1001"), b"not a pdf", row("1003")])
    download_stage(run, source, CFG, tmp_path, dump=False)
    assert _status(run, "row 2") is Status.FAILED
    assert (run.run_dir / "downloads" / "invoice_02.pdf").read_bytes() == b"not a pdf"
    assert source.evidence == []  # the page is fine, so there's nothing to see there


def test_a_list_that_will_not_open_fails_every_unit(run, tmp_path):
    source = FakeSource([], open_error=RuntimeError("page never loaded"))
    download_stage(run, source, CFG, tmp_path, dump=False)
    assert all(_status(run, unit) is Status.FAILED for unit in UNITS)
    assert source.evidence == ["fail_01"]


def test_dump_reports_names_and_saves_nothing_under_data(run, row, tmp_path):
    source = FakeSource([row(u) for u in UNITS])
    download_stage(run, source, CFG, tmp_path, dump=True)
    assert "would save 2026-01-01_1001_90001001-1.pdf" in _items(run)[("invoice", "1001")].detail
    assert not (tmp_path / "data").exists()
    assert source.structure == ["dump_invoices"]


def test_failed_sign_in_fails_every_unit(run, tmp_path):
    @contextmanager
    def no_session(*args, **kwargs):
        raise LoginError("the site did not accept the password")
        yield

    browser_stage(run, CFG, tmp_path, False, no_session)
    assert _items(run)[("login", "site")].status is Status.FAILED
    assert "did not accept the password" in _items(run)[("login", "site")].detail
    assert all(_status(run, unit) is Status.FAILED for unit in UNITS)


def test_signed_in_session_runs_the_downloads(run, row, tmp_path):
    @contextmanager
    def session(*args, **kwargs):
        yield "page"

    browser_stage(run, CFG, tmp_path, False, session,
                  lambda page, url, expected: FakeSource([row(u) for u in UNITS]))
    assert _items(run)[("login", "site")].status is Status.OK
    assert run.summary.exit_code == 0


def test_missing_config_fails_with_a_clear_message(run, tmp_path):
    assert load_stage(run, tmp_path) is None
    assert "not found" in _items(run)[("config", "config.toml")].detail


def test_load_stage_masks_private_addresses(run, tmp_path):
    (tmp_path / "config.toml").write_text(
        '[site]\nlogin_url = "https://sign-in.example.invalid/login"\n'
        'app_url = "https://app.example.invalid/"\n'
        '[invoices]\npage_url = "https://app.example.invalid/invoices"\nunits = ["1001"]\n',
        encoding="utf-8")
    assert load_stage(run, tmp_path) is not None
    assert "https://sign-in.example.invalid/login" not in run.masker.mask(
        "https://sign-in.example.invalid/login")


def test_main_without_config_exits_1_and_notifies_under_its_own_name(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATION_DATA_ROOT", str(tmp_path))
    sent = []
    assert main([], notifier=lambda note, masker: sent.append(note) or True) == 1
    assert sent == [{"title": "innago-invoices", "message": "FAIL"}]
