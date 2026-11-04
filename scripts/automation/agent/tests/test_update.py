import subprocess
from pathlib import Path

from automation_agent.update import current_version, start_update_task, take_update_result

SCRIPT = Path(__file__).parents[1] / "update_agent.ps1"


def _done(returncode=0, stdout=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout)


def _raises(error):
    def run(*args, **kwargs):
        raise error

    return run


def test_start_runs_only_the_named_task():
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return _done()

    assert start_update_task(run) is True
    assert calls == [["schtasks", "/Run", "/TN", "automation-agent-update"]]


def test_start_reports_a_missing_task_without_raising():
    assert start_update_task(lambda *a, **k: _done(1)) is False
    assert start_update_task(_raises(FileNotFoundError())) is False
    assert start_update_task(_raises(subprocess.TimeoutExpired("schtasks", 30))) is False


def test_result_is_read_once(tmp_path):
    path = tmp_path / "update_result.txt"
    path.write_text("PASS\r\n", encoding="ascii")
    assert take_update_result(tmp_path) == "PASS"
    assert not path.exists()
    assert take_update_result(tmp_path) is None


def test_anything_but_pass_is_a_failure(tmp_path):
    for content in (b"FAIL\r\n", b"", b"maybe", b"\xff\xfe"):
        (tmp_path / "update_result.txt").write_bytes(content)
        assert take_update_result(tmp_path) == "FAIL"
    assert not (tmp_path / "update_result.txt").exists()


def test_no_result_file_means_no_update(tmp_path):
    assert take_update_result(tmp_path) is None


def test_version_is_the_short_commit(tmp_path):
    assert current_version(tmp_path, lambda *a, **k: _done(stdout="abc1234\n")) == "abc1234"


def test_version_never_raises(tmp_path):
    assert current_version(tmp_path, lambda *a, **k: _done(128)) == "unknown"
    assert current_version(tmp_path, lambda *a, **k: _done(stdout="\n")) == "unknown"
    assert current_version(tmp_path, _raises(FileNotFoundError())) == "unknown"


def test_helper_script_takes_no_input_and_stays_ascii():
    # Windows PowerShell 5.1 misreads non-ASCII in a file without a byte-order mark
    text = SCRIPT.read_bytes().decode("ascii")
    assert "param(" not in text.lower()
    assert "$args" not in text.lower()
