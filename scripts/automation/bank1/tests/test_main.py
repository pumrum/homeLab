"""The command line, which accounts a run works on, and its final notification."""

import pytest

from automation_common.run import Run, Status
from bank1_portal import session
from bank1_portal.config import parse_config
from bank1_portal.login import LoginError, asks_to_verify, on_dashboard
from bank1_portal.main import chosen_accounts, final_event, parse_args


def _cfg():
    accounts = [
        {"label": label, "kind": kind, "suffix": "9871",
         "statements_url": f"https://bank.example.invalid/statements/{label}"}
        for label, kind in [("entity1_checking", "checking"), ("entity2_checking", "checking"),
                            ("personal_card_1", "card"), ("personal_invest_1", "investment"),
                            ("personal_card_2", "card")]
    ]
    return parse_config({"site": {"login_url": "https://bank.example.invalid/logon"},
                         "accounts": accounts})


@pytest.fixture
def run(tmp_path):
    with Run.start("test", tmp_path, notifier=lambda s, m: False) as r:
        yield r


def test_a_job_run_with_no_arguments_never_waits_for_a_person():
    args = parse_args([])
    assert not args.capture and not args.dump and args.account is None


def test_dump_and_capture_cannot_be_combined():
    with pytest.raises(SystemExit):
        parse_args(["--dump", "--capture"])


def test_a_normal_run_works_on_every_account_in_order(run):
    labels = [a.label for a in chosen_accounts(run, _cfg(), None, dump=False)]
    assert labels == ["entity1_checking", "entity2_checking", "personal_card_1",
                      "personal_invest_1", "personal_card_2"]


def test_dump_takes_the_first_account_of_each_kind(run):
    labels = [a.label for a in chosen_accounts(run, _cfg(), None, dump=True)]
    assert labels == ["entity1_checking", "personal_card_1", "personal_invest_1"]


def test_a_named_account_wins_even_for_dump(run):
    assert [a.label for a in chosen_accounts(run, _cfg(), "personal_card_2", dump=True)] == [
        "personal_card_2"]


def test_an_unknown_label_is_recorded_as_a_failure(run):
    assert chosen_accounts(run, _cfg(), "nope", dump=False) == ()
    assert run.summary.exit_code == 1


def test_the_final_event_names_the_verification_only_when_it_timed_out(run):
    run.summary.record("login", "site", Status.OK)
    assert final_event(run.summary) == "pass"
    run.summary.record("statement", "x", Status.FAILED, "verification timed out, but elsewhere")
    assert final_event(run.summary) == "fail"
    run.summary.record("login", "site", Status.FAILED,
                       "VerificationTimeout: verification timed out after 300 seconds")
    assert final_event(run.summary) == "mfa_failed"


def test_a_missing_credential_fails_before_the_page_is_touched(run):
    with pytest.raises(LoginError, match="bank1/login"):
        with session.signed_in_page(run, None, _cfg().site, get_cred=lambda name, masker: None):
            pass


def test_one_profile_per_machine(tmp_path):
    assert session.profile_dir(tmp_path) == tmp_path / "profiles" / "edge"


@pytest.mark.parametrize(("text", "asks"), [
    ("Let’s make sure it’s you", True),
    ("We don't recognize this device", True),
    ("Confirm your identity", True),
    ("Accounts Overview", False),
])
def test_the_verification_words(text, asks):
    assert asks_to_verify(text) is asks


def test_the_dashboard_is_known_by_its_address():
    assert on_dashboard("https://bank.example.invalid/web/auth/dashboard#/overview")
    assert not on_dashboard("https://bank.example.invalid/logon")
