"""The shared signed-in session and the --capture command, without a browser."""

import pytest

from automation_common.run import Run
from innago_portal import session
from innago_portal.config import Site
from innago_portal.login import LoginError
from innago_portal.main import parse_args

SITE = Site("https://sign-in.example.invalid/login", "https://app.example.invalid/")


@pytest.fixture
def run(tmp_path):
    with Run.start("test", tmp_path, notifier=lambda s, m: False) as r:
        yield r


def test_a_missing_credential_is_a_clear_failure_before_edge_opens(run, tmp_path, monkeypatch):
    def no_browser():
        raise AssertionError("Edge must not start without a credential")
    monkeypatch.setattr(session, "sync_playwright", no_browser)
    with pytest.raises(LoginError, match="innago/login"):
        with session.signed_in_page(run, SITE, tmp_path, get_cred=lambda name, masker: None):
            pass


def test_the_profile_is_shared_by_every_command(tmp_path):
    assert session.profile_dir(tmp_path) == tmp_path / "profiles" / "edge"


def test_dump_and_capture_cannot_be_combined():
    with pytest.raises(SystemExit):
        parse_args(["--dump", "--capture"])


def test_a_job_run_with_no_arguments_never_waits_for_a_person():
    args = parse_args([])
    assert not args.capture
    assert not args.dump  # the agent runs bare commands, so --capture can't start from a job
