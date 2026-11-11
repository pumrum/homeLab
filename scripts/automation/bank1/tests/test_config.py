"""Config validation: every way a hand-edited file can go wrong fails clearly."""

import tomllib
from pathlib import Path

import pytest

from automation_common.config import ConfigError
from bank1_portal.config import Kind, load_config, parse_config

EXAMPLE = Path(__file__).parents[1] / "config.example.toml"


def doc(**account):
    entry = {"label": "entity1_checking", "kind": "checking",
             "statements_url": "https://bank.example.invalid/statements/a1", "suffix": "1111"}
    entry |= account
    return {"site": {"login_url": "https://bank.example.invalid/logon"}, "accounts": [entry]}


def test_a_good_config_loads_and_match_defaults_to_the_suffix():
    cfg = parse_config(doc())
    account = cfg.accounts[0]
    assert account.kind is Kind.CHECKING
    assert account.match == "1111"
    assert set(cfg.notifications) == {"pass", "fail", "mfa_needed", "mfa_failed"}


def test_match_can_be_set_on_its_own():
    assert parse_config(doc(match="FakeChecking ...1111")).accounts[0].match == (
        "FakeChecking ...1111")


def test_an_accounts_repr_never_shows_private_values():
    shown = repr(parse_config(doc()).accounts[0])
    assert "1111" not in shown and "example.invalid" not in shown
    assert "entity1_checking" in shown


@pytest.mark.parametrize(("change", "message"), [
    ({"kind": "savings"}, "kind"),
    ({"suffix": "123"}, "four digits"),
    ({"suffix": "12345"}, "four digits"),
    ({"suffix": "12a4"}, "four digits"),
    ({"label": "Entity 1"}, "label"),
    ({"statements_url": "http://bank.example.invalid/x"}, "https"),
    ({"statements_url": "https://<statements page>"}, "placeholder"),
    ({"nickname": "x"}, "unknown"),
])
def test_a_bad_account_value_fails_clearly(change, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(doc(**change))


def test_a_bad_suffix_or_address_is_never_repeated_in_the_message():
    for change in ({"suffix": "98765"}, {"statements_url": "http://secret.example.invalid/9"}):
        with pytest.raises(ConfigError) as e:
            parse_config(doc(**change))
        assert next(iter(change.values())) not in str(e.value)


def test_a_suffix_without_quotes_is_refused():
    with pytest.raises(ConfigError, match="quoted"):
        parse_config(doc(suffix=1111))


def test_labels_and_addresses_must_each_be_used_once():
    two = doc()
    two["accounts"].append(dict(two["accounts"][0]))
    with pytest.raises(ConfigError, match="duplicate label"):
        parse_config(two)
    two["accounts"][1]["label"] = "entity2_checking"
    with pytest.raises(ConfigError, match="same \"statements_url\""):
        parse_config(two)


def test_a_misspelled_notification_variable_fails():
    bad = doc() | {"notifications": {"fail": {"message": "{label} failed"}}}
    with pytest.raises(ConfigError, match="unknown variable"):
        parse_config(bad)


def test_the_example_lists_every_kind_and_every_event_but_wont_run_as_is():
    example = tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert {a["kind"] for a in example["accounts"]} == set(Kind)
    assert set(example["notifications"]) == {"pass", "fail", "mfa_needed", "mfa_failed"}
    with pytest.raises(ConfigError, match="placeholder"):
        load_config(EXAMPLE)
