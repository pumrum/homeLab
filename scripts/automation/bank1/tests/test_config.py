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
    return {"site": {"login_url": "https://bank.example.invalid/logon",
                     "name_in_files": "FakeBank"}, "accounts": [entry]}


@pytest.mark.parametrize("name", ["Fake_Bank", "Fake/Bank", "Fake:Bank", "<bank name>"])
def test_the_name_in_file_names_must_be_safe_and_filled_in(name):
    bad = doc()
    bad["site"]["name_in_files"] = name
    with pytest.raises(ConfigError, match=r"\[site\]") as e:
        parse_config(bad)
    assert "Fake" not in str(e.value)  # a private value


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


def test_labels_addresses_and_suffixes_must_each_be_used_once():
    two = doc()
    two["accounts"].append(dict(two["accounts"][0]))
    with pytest.raises(ConfigError, match="duplicate label"):
        parse_config(two)
    two["accounts"][1]["label"] = "entity2_checking"
    with pytest.raises(ConfigError, match="same \"statements_url\""):
        parse_config(two)
    two["accounts"][1]["statements_url"] = "https://bank.example.invalid/statements/a2"
    with pytest.raises(ConfigError, match="same \"suffix\"") as e:
        parse_config(two)
    assert "1111" not in str(e.value)  # a private value


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


# ---------------------------------------------- bank1-activity's settings

SHEETS = {"recon": {"spreadsheet_id": "made-up-spreadsheet-id"}}
SHEET = {"sheet": "recon", "tab": "Recon 1", "start_cell": "A5"}
ACTIVITY = {"activity_url": "https://bank.example.invalid/activity/a1",
            "activity_start": "2026-07-01", "abbreviation": "ABC-1"}


def with_sheets(config):
    return config | {"sheets": SHEETS}


def test_an_account_without_an_activity_url_has_no_activity():
    assert parse_config(doc()).accounts[0].activity is None


def test_activity_settings_load_with_and_without_a_sheet():
    import datetime as dt
    alone = parse_config(doc(**ACTIVITY)).accounts[0].activity
    assert alone.start == dt.date(2026, 7, 1) and alone.destination is None
    assert alone.abbreviation == "ABC-1"
    cfg = parse_config(with_sheets(doc(**ACTIVITY, activity_sheet=SHEET)))
    destination = cfg.accounts[0].activity.destination
    assert (destination.sheet, destination.spreadsheet_id, destination.tab,
            destination.start_cell) == ("recon", "made-up-spreadsheet-id", "Recon 1", "A5")
    workbook = cfg.sheets["recon"]
    assert (workbook.name, workbook.spreadsheet_id) == ("recon", "made-up-spreadsheet-id")
    assert workbook.updated_tab is None and workbook.updated_cell is None


def test_activity_reprs_never_show_private_values():
    activity = parse_config(with_sheets(doc(**ACTIVITY, activity_sheet=SHEET))).accounts[0].activity
    shown = repr(activity)
    assert "example.invalid" not in shown and "made-up" not in shown and "Recon 1" not in shown


@pytest.mark.parametrize(("change", "message"), [
    ({"activity_start": "2026-07-01"}, "needs an \"activity_url\""),
    ({"activity_sheet": SHEET}, "needs an \"activity_url\""),
    ({"abbreviation": "ABC"}, "needs an \"activity_url\""),
    ({**ACTIVITY, "abbreviation": "ABC_1"}, "letters, digits, or -"),
    ({**ACTIVITY, "abbreviation": "ABC 1"}, "letters, digits, or -"),
    ({"activity_url": "https://bank.example.invalid/activity/a1",
      "activity_start": "2026-07-01"}, "abbreviation"),
    ({**ACTIVITY, "activity_start": "07/01/2026"}, "a date like"),
    ({"activity_url": "https://bank.example.invalid/activity/a1", "abbreviation": "A"},
     "activity_start"),
    ({**ACTIVITY, "activity_url": "http://bank.example.invalid/a"}, "https"),
    ({**ACTIVITY, "activity_sheet": SHEET | {"sheet": "other"}}, r"\[sheets.<name>\]"),
    ({**ACTIVITY, "activity_sheet": SHEET | {"start_cell": "a5"}}, "a cell like"),
    ({**ACTIVITY, "activity_sheet": SHEET | {"start_cell": "A0"}}, "a cell like"),
    ({**ACTIVITY, "activity_sheet": SHEET | {"tab": "<tab name>"}}, "placeholder"),
    ({**ACTIVITY, "activity_sheet": SHEET | {"cell": "A5"}}, "unknown"),
])
def test_a_bad_activity_value_fails_clearly(change, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(with_sheets(doc(**change)))


@pytest.mark.parametrize("sheets", [
    {"Recon": {"spreadsheet_id": "x"}},
    {"recon": {"spreadsheet_id": "<spreadsheet id>"}},
    {"recon": {"id": "x"}},
])
def test_a_bad_workbook_fails_clearly(sheets):
    with pytest.raises(ConfigError, match=r"\[sheets"):
        parse_config(doc() | {"sheets": sheets})


def test_two_accounts_cant_share_an_activity_address_or_a_block():
    two = with_sheets(doc(**ACTIVITY, activity_sheet=SHEET))
    second = dict(two["accounts"][0], label="entity2_checking", suffix="2222",
                  statements_url="https://bank.example.invalid/statements/a2")
    two["accounts"].append(second)
    with pytest.raises(ConfigError, match="same \"activity_url\""):
        parse_config(two)
    second["activity_url"] = "https://bank.example.invalid/activity/a2"
    with pytest.raises(ConfigError, match="same tab and start cell"):
        parse_config(two)
    second["activity_sheet"] = SHEET | {"start_cell": "J5"}
    assert len(parse_config(two).accounts) == 2


def test_the_example_shows_activity_for_some_accounts_and_its_workbooks():
    example = tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert example["sheets"]
    with_activity = [a for a in example["accounts"] if "activity_url" in a]
    assert {a["kind"] for a in with_activity} == {"checking", "card"}
    assert any("activity_sheet" not in a for a in with_activity)


def test_a_workbook_can_have_an_update_cell():
    sheets = {"recon": SHEETS["recon"] | {"updated_tab": "Dashboard", "updated_cell": "B2"}}
    workbook = parse_config(doc() | {"sheets": sheets}).sheets["recon"]
    assert (workbook.updated_tab, workbook.updated_cell) == ("Dashboard", "B2")
    assert "Dashboard" not in repr(workbook) and "made-up" not in repr(workbook)


@pytest.mark.parametrize(("extra", "message"), [
    ({"updated_tab": "Dashboard"}, "go together"),
    ({"updated_cell": "B2"}, "go together"),
    ({"updated_tab": "Dashboard", "updated_cell": "b2"}, "a cell like"),
    ({"updated_tab": "<tab>", "updated_cell": "B2"}, "placeholder"),
])
def test_a_bad_update_cell_fails_clearly(extra, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(doc() | {"sheets": {"recon": SHEETS["recon"] | extra}})
