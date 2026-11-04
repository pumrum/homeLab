import subprocess

from automation_agent.config import Job
from automation_agent.runner import JobResult, Runner, run_job

JOB = Job("example", "example-job", timeout_minutes=1)
OTHER = Job("other", "other-job")


class FakeProc:
    pid = 123

    def __init__(self, code=0, hang=False):
        self.code = code
        self.hang = hang

    def wait(self, timeout=None):
        if self.hang and timeout is not None:
            raise subprocess.TimeoutExpired("x", timeout)
        return self.code


def _scripts(tmp_path):
    (tmp_path / "example-job.exe").write_bytes(b"")
    return tmp_path


def test_exit_0_passes_and_the_job_reported_itself(tmp_path):
    calls = []

    def popen(args, cwd):
        calls.append((args, cwd))
        return FakeProc(0)

    result = run_job(JOB, _scripts(tmp_path), tmp_path, popen=popen)
    assert result == JobResult(True, "exit code 0", True)
    assert calls == [([str(tmp_path / "example-job.exe")], tmp_path)]  # no shell, no arguments


def test_nonzero_exit_fails(tmp_path):
    result = run_job(JOB, _scripts(tmp_path), tmp_path, popen=lambda a, cwd: FakeProc(1))
    assert (result.ok, result.reported_by_job) == (False, True)


def test_missing_script_fails_and_agent_must_report(tmp_path):
    result = run_job(JOB, tmp_path, tmp_path)
    assert (result.ok, result.reported_by_job) == (False, False)
    assert "uv sync" in result.detail


def test_start_failure_never_raises(tmp_path):
    def popen(args, cwd):
        raise PermissionError("no")

    result = run_job(JOB, _scripts(tmp_path), tmp_path, popen=popen)
    assert (result.ok, result.reported_by_job) == (False, False)


def test_hung_job_is_killed_with_its_children(tmp_path):
    killed = []
    result = run_job(JOB, _scripts(tmp_path), tmp_path,
                     popen=lambda a, cwd: FakeProc(hang=True), kill=killed.append)
    assert killed == [123]
    assert (result.ok, result.reported_by_job) == (False, False)
    assert "1 minute" in result.detail


def _runner(run=None):
    events = []
    runner = Runner(
        run or (lambda job: JobResult(True, "fine", True)),
        lambda job: events.append(("start", job.name)),
        lambda job, result: events.append(("finish", job.name, result.ok)),
    )
    return runner, events


def test_duplicate_request_is_ignored_while_waiting():
    runner, _ = _runner()
    assert runner.request(JOB) is True
    assert runner.request(JOB) is False
    assert runner.request(OTHER) is True


def test_request_is_ignored_while_running():
    seen = []
    runner, _ = _runner()

    def run(job):
        seen.append((runner.request(JOB), runner.is_active("example")))
        return JobResult(True, "fine", True)

    runner._run = run
    runner.request(JOB)
    runner.process_one(runner._queue.get_nowait())
    assert seen == [(False, True)]
    assert runner.is_active("example") is False
    assert runner.request(JOB) is True  # can run again once finished


def test_events_in_order():
    runner, events = _runner()
    runner.request(JOB)
    runner.process_one(runner._queue.get_nowait())
    assert events == [("start", "example"), ("finish", "example", True)]


def test_worker_survives_a_crashing_job():
    def boom(job):
        raise RuntimeError("bad")

    runner, events = _runner(boom)
    runner.process_one(JOB)
    assert events[-1] == ("finish", "example", False)


def test_worker_survives_a_failure_while_reporting():
    def bad_finish(job, result):
        raise RuntimeError("broker gone")

    runner = Runner(lambda job: JobResult(True, "fine", True), lambda job: None, bad_finish)
    runner.process_one(JOB)
    assert runner.is_active("example") is False
