"""The shared session and the command line, without a browser."""

import pytest

from automation_common.run import Run, Status
from buildium_portal import session
from buildium_portal.config import Entity, Site
from buildium_portal.login import LoginError
from buildium_portal.main import final_event, parse_args

SITE = Site("https://site.example.invalid/login")
ENTITY = Entity("entity1", "ABCLLC", "FakeEntity One, LLC")


@pytest.fixture
def run(tmp_path):
    with Run.start("test", tmp_path, notifier=lambda s, m: False) as r:
        yield r


def test_a_missing_credential_is_a_clear_failure_before_the_page_is_touched(run):
    with pytest.raises(LoginError, match="buildium/entity1"):
        with session.signed_in_page(run, None, SITE, ENTITY, get_cred=lambda name, masker: None):
            pass


def test_the_credential_name_uses_only_the_neutral_key():
    assert session.credential_name(ENTITY) == "buildium/entity1"


def test_every_entity_shares_one_profile(tmp_path):
    assert session.profile_dir(tmp_path) == tmp_path / "profiles" / "edge"


def test_dump_and_capture_cannot_be_combined():
    with pytest.raises(SystemExit):
        parse_args(["--dump", "--capture"])


def test_a_job_run_with_no_arguments_never_waits_for_a_person():
    args = parse_args([])
    assert not args.capture
    assert not args.dump  # the agent runs bare commands, so --capture can't start from a job
    assert args.entity is None


def test_the_final_event_names_mfa_only_when_it_stopped_a_sign_in(run):
    run.summary.record("login", "entity1", Status.OK)
    assert final_event(run.summary) == "pass"
    run.summary.record("statement", "entity1", Status.FAILED, "mentions MFA, but isn't a sign-in")
    assert final_event(run.summary) == "fail"
    run.summary.record("login", "entity2", Status.FAILED, "MFA was not completed")
    assert final_event(run.summary) == "mfa_failed"
