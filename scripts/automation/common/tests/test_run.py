import datetime as dt
import json
import logging

from automation_common.run import Run, Status, make_run_dir

NOW = dt.datetime(2026, 11, 3, 8, 0, 0, tzinfo=dt.UTC)


def _run(tmp_path, sent):
    return Run.start("test", tmp_path, now=NOW, notifier=lambda s, m: sent.append(s) or False)


def test_make_run_dir_adds_suffix_when_taken(tmp_path):
    first = make_run_dir(tmp_path, NOW)
    second = make_run_dir(tmp_path, NOW)
    assert first.name == "20261103_080000"
    assert second.name == "20261103_080000_2"


def test_all_ok_exits_0_and_writes_summary(tmp_path):
    sent = []
    with _run(tmp_path, sent) as run:
        run.summary.record("fetch", "AAA", Status.OK)
        run.summary.record("fetch", "BBB", Status.UNCHANGED)
    assert run.exit_code == 0
    data = json.loads((run.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert data["result"] == "ok"
    assert data["counts"] == {"ok": 1, "unchanged": 1, "failed": 0}
    assert sent and sent[0]["run_id"] == run.run_dir.name
    assert "Result: OK" in (run.run_dir / "report.txt").read_text(encoding="utf-8")
    assert (run.run_dir / "run.log").exists()


def test_one_failure_exits_1(tmp_path):
    with _run(tmp_path, []) as run:
        run.summary.record("fetch", "AAA", Status.OK)
        run.summary.record("fetch", "BBB", Status.FAILED, "boom")
    assert run.exit_code == 1


def test_nothing_recorded_exits_1(tmp_path):
    with _run(tmp_path, []) as run:
        pass
    assert run.exit_code == 1


def test_unexpected_exception_is_recorded_not_raised(tmp_path):
    with _run(tmp_path, []) as run:
        raise ValueError("surprise")
    assert run.exit_code == 1
    assert run.summary.items[-1].status is Status.FAILED
    assert "surprise" in run.summary.items[-1].detail


def test_secrets_are_masked_in_log_and_summary(tmp_path):
    with _run(tmp_path, []) as run:
        run.masker.add("s3cr3t-value")
        logging.getLogger("x").info("token is s3cr3t-value")
        run.summary.record("fetch", "AAA", Status.FAILED, "bad s3cr3t-value")
    for name in ("run.log", "summary.json", "report.txt"):
        text = (run.run_dir / name).read_text(encoding="utf-8")
        assert "s3cr3t-value" not in text
        assert "***" in text


def test_failure_prefixes_count_up(tmp_path):
    with _run(tmp_path, []) as run:
        assert run.next_failure_prefix() == "fail_01"
        assert run.next_failure_prefix() == "fail_02"
        run.summary.record("x", "y", Status.OK)
