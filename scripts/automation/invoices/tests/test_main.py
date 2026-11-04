"""The run's stages with a fake server and a fake sheet: synthetic data only."""

from contextlib import contextmanager

import pytest

from automation_common.run import Run, Status
from automation_common.sheets import SheetError
from invoices_sync import main as main_module
from invoices_sync.config import Config, Entity, Sftp, Sheet
from invoices_sync.fetch import FetchError
from invoices_sync.main import KEY_SECRET, fetch_stage, sheet_stage

HOST = "sftp.example.invalid"
SHEET_ID = "made-up-spreadsheet-id-0123456789"
CFG = Config(
    Sftp(HOST, 22, "made-up-user", "/made/up/folder"),
    Sheet(SHEET_ID, "Invoices"),
    [
        Entity("entity_a", True, "inv_entity_a.txt", "B5"),
        Entity("entity_b", True, "inv_entity_b.txt", "J5"),
        Entity("entity_c", False, "inv_entity_c.txt", "R5"),
        Entity("entity_d", True, "inv_entity_d.txt", "Z5"),
    ],
)
GOOD_FILES = {
    "inv_entity_a.txt": b"2026-01-15_Example One\n2026-01-31_Example Two\n",
    "inv_entity_b.txt": b"2026-01-15_Example Three\n",
    "inv_entity_d.txt": b"2026-01-31_Example Four\n",
}


class FakeSource:
    def __init__(self, files):
        self.files = files
        self.read_names = []

    def read(self, name):
        self.read_names.append(name)
        if name not in self.files:
            raise FileNotFoundError(2, "No such file")
        return self.files[name]


class FakeWorksheet:
    def __init__(self, fail_cells=()):
        self.writes = []
        self.fail_cells = fail_cells

    def replace_from_cell(self, cell, lines):
        if cell in self.fail_cells:
            raise SheetError("Google Sheets error 500 writing to the sheet")
        self.writes.append((cell, list(lines)))


def _opener(source):
    @contextmanager
    def open_src(cfg, key, known_hosts):
        yield source
    return open_src


def _status(run, stage, item):
    return next(i.status for i in run.summary.items if (i.stage, i.item) == (stage, item))


def _run(tmp_path):
    return Run.start("test", tmp_path, notifier=lambda s, m: False)


def _fetch(run, tmp_path, files=GOOD_FILES):
    return fetch_stage(run, CFG, tmp_path, get_key=lambda n, m: "made-up-key",
                       open_src=_opener(FakeSource(files)))


def test_full_run_writes_each_enabled_file_once(tmp_path):
    ws = FakeWorksheet()
    with _run(tmp_path) as run:
        fetched = _fetch(run, tmp_path)
        sheet_stage(run, CFG, fetched, open_ws=lambda i, t, m: ws)
    assert ws.writes == [
        ("B5", ["2026-01-15_Example One", "2026-01-31_Example Two"]),
        ("J5", ["2026-01-15_Example Three"]),
        ("Z5", ["2026-01-31_Example Four"]),
    ]
    assert run.exit_code == 0


def test_disabled_entity_is_not_fetched_or_written(tmp_path):
    source = FakeSource(GOOD_FILES)
    with _run(tmp_path) as run:
        fetched = fetch_stage(run, CFG, tmp_path, get_key=lambda n, m: "made-up-key",
                              open_src=_opener(source))
    assert "inv_entity_c.txt" not in source.read_names
    assert "entity_c" not in fetched
    assert not any(i.item == "entity_c" for i in run.summary.items)


def test_key_comes_from_the_process_entry_and_known_hosts_from_its_folder(tmp_path):
    seen = {}

    @contextmanager
    def open_src(cfg, key, known_hosts):
        seen.update(cfg=cfg, key=key, known_hosts=known_hosts)
        yield FakeSource(GOOD_FILES)

    with _run(tmp_path) as run:
        fetch_stage(run, CFG, tmp_path, get_key=lambda n, m: f"key for {n}", open_src=open_src)
    assert seen == {"cfg": CFG.sftp, "key": f"key for {KEY_SECRET}",
                    "known_hosts": tmp_path / "known_hosts"}


def test_raw_files_are_kept_in_the_run_folder_as_they_came(tmp_path):
    with _run(tmp_path) as run:
        _fetch(run, tmp_path)
    for name, data in GOOD_FILES.items():
        assert (run.run_dir / "files" / name).read_bytes() == data


@pytest.mark.parametrize(("bad", "detail"), [
    ({"inv_entity_b.txt": b""}, "empty"),
    ({"inv_entity_b.txt": b"\n  \n"}, "empty"),
    ({"inv_entity_b.txt": b"bad\xff\n"}, "UTF-8"),
])
def test_a_bad_file_fails_that_entity_and_leaves_its_block_alone(tmp_path, bad, detail):
    ws = FakeWorksheet()
    with _run(tmp_path) as run:
        fetched = _fetch(run, tmp_path, GOOD_FILES | bad)
        sheet_stage(run, CFG, fetched, open_ws=lambda i, t, m: ws)
    assert [cell for cell, _ in ws.writes] == ["B5", "Z5"]
    failed = [i for i in run.summary.items if i.status is Status.FAILED]
    assert [i.item for i in failed] == ["entity_b"] and detail in failed[0].detail
    assert (run.run_dir / "files" / "inv_entity_b.txt").read_bytes() == bad["inv_entity_b.txt"]
    assert run.exit_code == 1


def test_a_missing_file_fails_that_entity_and_the_others_still_run(tmp_path):
    ws = FakeWorksheet()
    files = {k: v for k, v in GOOD_FILES.items() if k != "inv_entity_a.txt"}
    with _run(tmp_path) as run:
        fetched = _fetch(run, tmp_path, files)
        sheet_stage(run, CFG, fetched, open_ws=lambda i, t, m: ws)
    assert [cell for cell, _ in ws.writes] == ["J5", "Z5"]
    assert _status(run, "fetch", "entity_a") is Status.FAILED
    assert run.exit_code == 1


def test_no_key_fails_every_enabled_entity_and_does_not_connect(tmp_path):
    def open_src(cfg, key, known_hosts):
        raise AssertionError("must not connect")

    with _run(tmp_path) as run:
        fetched = fetch_stage(run, CFG, tmp_path, get_key=lambda n, m: None, open_src=open_src)
    assert fetched == {}
    assert [i.item for i in run.summary.items] == ["entity_a", "entity_b", "entity_d"]
    assert "invoices/ssh-key" in run.summary.items[0].detail
    assert run.exit_code == 1


def test_connection_failure_writes_nothing(tmp_path):
    @contextmanager
    def open_src(cfg, key, known_hosts):
        raise FetchError("made up")
        yield

    opened = []
    with _run(tmp_path) as run:
        fetched = fetch_stage(run, CFG, tmp_path, get_key=lambda n, m: "k", open_src=open_src)
        sheet_stage(run, CFG, fetched, open_ws=lambda i, t, m: opened.append(i))
    assert opened == []
    assert _status(run, "sftp", "session") is Status.FAILED
    assert _status(run, "fetch", "entity_d") is Status.FAILED
    assert run.exit_code == 1


def test_session_failing_at_the_end_keeps_what_was_fetched(tmp_path):
    @contextmanager
    def open_src(cfg, key, known_hosts):
        yield FakeSource(GOOD_FILES)
        raise EOFError("made up")

    with _run(tmp_path) as run:
        fetched = fetch_stage(run, CFG, tmp_path, get_key=lambda n, m: "k", open_src=open_src)
    assert set(fetched) == {"entity_a", "entity_b", "entity_d"}
    assert _status(run, "sftp", "session") is Status.FAILED
    assert run.exit_code == 1


def test_one_write_failing_does_not_stop_the_others(tmp_path):
    ws = FakeWorksheet(fail_cells=("J5",))
    with _run(tmp_path) as run:
        fetched = _fetch(run, tmp_path)
        sheet_stage(run, CFG, fetched, open_ws=lambda i, t, m: ws)
    assert [cell for cell, _ in ws.writes] == ["B5", "Z5"]
    assert _status(run, "sheet", "entity_b") is Status.FAILED
    assert _status(run, "sheet", "entity_d") is Status.OK
    assert run.exit_code == 1


def test_sheet_that_will_not_open_fails_each_fetched_entity(tmp_path):
    def cannot_open(i, t, m):
        raise SheetError("spreadsheet not found")

    with _run(tmp_path) as run:
        fetched = _fetch(run, tmp_path)
        sheet_stage(run, CFG, fetched, open_ws=cannot_open)
    failed = [i for i in run.summary.items if i.status is Status.FAILED]
    assert [(i.stage, i.item) for i in failed] == [
        ("sheet", "entity_a"), ("sheet", "entity_b"), ("sheet", "entity_d")]
    assert run.exit_code == 1


def test_a_second_run_writes_the_same_thing(tmp_path):
    ws = FakeWorksheet()
    for _ in range(2):
        with _run(tmp_path) as run:
            sheet_stage(run, CFG, _fetch(run, tmp_path), open_ws=lambda i, t, m: ws)
    assert ws.writes[:3] == ws.writes[3:]


def _main(argv, ws, open_src=None):
    return main_module.main(
        argv,
        notifier=lambda s, m: False,
        get_key=lambda n, m: "made-up-key",
        open_src=open_src or _opener(FakeSource(GOOD_FILES)),
        open_ws=lambda i, t, m: ws,
    )


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A data folder with a real config file."""
    monkeypatch.setenv("AUTOMATION_DATA_ROOT", str(tmp_path))
    data_dir = tmp_path / "invoices"
    data_dir.mkdir()
    (data_dir / "config.toml").write_text(
        f"""
[sftp]
host = "{HOST}"
user = "made-up-user"
folder = "/made/up/folder"
[sheet]
spreadsheet_id = "{SHEET_ID}"
worksheet = "Invoices"
[[entities]]
code = "entity_a"
enabled = true
file = "inv_entity_a.txt"
start_cell = "B5"
""", encoding="utf-8")
    return data_dir


def test_normal_run_writes_to_the_sheet(data_dir):
    ws = FakeWorksheet()
    assert _main([], ws) == 0
    assert [cell for cell, _ in ws.writes] == ["B5"]


def test_dump_saves_the_files_and_writes_nothing_to_the_sheet(data_dir):
    ws = FakeWorksheet()
    assert _main(["--dump"], ws) == 0
    assert ws.writes == []
    run_dir = next((data_dir / "runs").iterdir())
    assert (run_dir / "files" / "inv_entity_a.txt").is_file()


def test_private_values_never_reach_the_run_folder(data_dir):
    @contextmanager
    def leaky(cfg, key, known_hosts):
        raise FetchError(f"{cfg.user}@{cfg.host}:{cfg.folder} refused {SHEET_ID} {key}")
        yield

    sent = []
    code = main_module.main(
        [],
        notifier=lambda s, m: sent.append(m.mask(str(s))) or False,
        get_key=lambda n, m: m.add("made-up-key") or "made-up-key",
        open_src=leaky,
    )
    assert code == 1
    run_dir = next((data_dir / "runs").iterdir())
    texts = [(run_dir / n).read_text(encoding="utf-8")
             for n in ("run.log", "report.txt", "summary.json")] + sent
    for text in texts:
        for private in (HOST, "made-up-user", "/made/up/folder", SHEET_ID, "made-up-key"):
            assert private not in text


def test_the_notification_is_only_pass_or_fail(data_dir):
    sent = []
    code = main_module.main(
        [],
        notifier=lambda s, m: sent.append(s) or False,
        get_key=lambda n, m: "made-up-key",
        open_src=_opener(FakeSource({})),
        open_ws=lambda i, t, m: FakeWorksheet(),
    )
    assert code == 1
    assert sent == [{"title": "invoices", "message": "FAIL"}]


def test_bad_config_fails_the_run_without_connecting(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATION_DATA_ROOT", str(tmp_path))

    def must_not_connect(cfg, key, known_hosts):
        raise AssertionError("must not connect")

    assert _main([], FakeWorksheet(), must_not_connect) == 1
    run_dir = next((tmp_path / "invoices" / "runs").iterdir())
    assert "config file not found" in (run_dir / "report.txt").read_text(encoding="utf-8")
