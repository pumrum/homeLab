import datetime as dt
import json
import logging
import re

from automation_common.notify import STANDARD_NOTIFICATIONS, Template
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
    assert sent == [{"title": "test", "message": "PASS"}]
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


def test_notification_defaults_to_process_and_pass(tmp_path):
    sent = []
    with _run(tmp_path, sent) as run:
        run.summary.record("fetch", "AAA", Status.OK)
    # Only what the notification shows goes to the webhook
    assert sent == [{"title": "test", "message": "PASS"}]
    # The full summary stays in the run folder
    saved = json.loads((run.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert (saved["message"], saved["result"], saved["items"][0]["item"]) == ("PASS", "ok", "AAA")


def test_notification_says_fail_when_an_item_failed(tmp_path):
    sent = []
    with _run(tmp_path, sent) as run:
        run.summary.record("fetch", "AAA", Status.FAILED, "boom")
    assert sent[0]["message"] == "FAIL"


def test_the_title_names_the_command_when_there_is_one(tmp_path):
    sent = []
    with Run.start("test", tmp_path, now=NOW, command="test-thing",
                   notifier=lambda s, m: sent.append(s) or False) as run:
        run.summary.record("fetch", "AAA", Status.OK)
    assert sent == [{"title": "test-thing", "message": "PASS"}]


def test_templates_from_the_config_fill_in_the_runs_values(tmp_path):
    sent = []
    with _run(tmp_path, sent) as run:
        run.notifications = {"pass": Template("{process}/{command} {run_id}",
                                              "{ok} ok, {unchanged} same, {failed} bad, {total}")}
        run.summary.record("fetch", "AAA", Status.OK)
        run.summary.record("fetch", "BBB", Status.UNCHANGED)
    assert sent == [{"title": "test/test 20261103_080000", "message": "1 ok, 1 same, 0 bad, 2"}]
    saved = json.loads((run.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert saved["message"] == "1 ok, 1 same, 0 bad, 2"


def test_a_process_can_choose_its_own_final_event(tmp_path):
    sent = []
    with Run.start("test", tmp_path, now=NOW, notifier=lambda s, m: sent.append(s) or False,
                   notifications={**STANDARD_NOTIFICATIONS, "odd": Template("t", "odd one")},
                   event=lambda summary: "odd") as run:
        run.summary.record("fetch", "AAA", Status.OK)
    assert sent == [{"title": "t", "message": "odd one"}]


def test_a_push_during_the_run_takes_its_own_values(tmp_path):
    sent = []
    with _run(tmp_path, sent) as run:
        run.notifications = {**STANDARD_NOTIFICATIONS, "wait": Template("{command}", "{who} waits",
                                                                        frozenset({"who"}))}
        assert run.notify("wait", who="someone") is False  # what this notifier returned
        run.summary.record("fetch", "AAA", Status.OK)
    assert sent[0] == {"title": "test", "message": "someone waits"}
    assert sent[1]["message"] == "PASS"


def test_a_broken_event_function_falls_back_to_pass_or_fail(tmp_path):
    sent = []

    def broken(summary):
        raise KeyError("nope")

    with Run.start("test", tmp_path, now=NOW, notifier=lambda s, m: sent.append(s) or False,
                   event=broken) as run:
        run.summary.record("fetch", "AAA", Status.OK)
    assert sent[0]["message"] == "PASS"
    assert run.exit_code == 0


def test_log_timestamps_use_a_period_before_milliseconds(tmp_path):
    with _run(tmp_path, []) as run:
        logging.getLogger("x").info("hello")
        run.summary.record("x", "y", Status.OK)
    first = (run.run_dir / "run.log").read_text(encoding="utf-8").splitlines()[0]
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} ", first), first


def test_failure_prefixes_count_up(tmp_path):
    with _run(tmp_path, []) as run:
        assert run.next_failure_prefix() == "fail_01"
        assert run.next_failure_prefix() == "fail_02"
        run.summary.record("x", "y", Status.OK)
