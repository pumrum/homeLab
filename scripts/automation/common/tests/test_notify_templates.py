import pytest

from automation_common.config import ConfigError
from automation_common.notify import (
    STANDARD_NOTIFICATIONS,
    Template,
    parse_notifications,
    send_notification,
)
from automation_common.secrets import SecretMasker

DEFAULTS = {
    **STANDARD_NOTIFICATIONS,
    "waiting": Template("{command}", "{who} waits", frozenset({"command", "who"})),
    "plain": Template("plain", "text", frozenset()),
}


def _with(event, **table):
    return parse_notifications({"notifications": {event: table}}, DEFAULTS)


def test_no_section_keeps_every_default():
    assert parse_notifications({}, DEFAULTS) == DEFAULTS


def test_a_key_left_out_keeps_its_default():
    parsed = _with("fail", message="It broke")
    assert parsed["fail"].render(command="c") == {"title": "c", "message": "It broke"}
    assert parsed["pass"] == DEFAULTS["pass"]


def test_an_event_keeps_its_own_variables():
    assert _with("waiting", title="{who}")["waiting"].render(who="x", command="c") == {
        "title": "x", "message": "x waits"}


def test_a_value_is_never_expanded_again():
    note = Template("t", "{who}, {command}").render(who="{command}", command="c")
    assert note["message"] == "{command}, c"


def test_a_misspelled_variable_says_what_it_can_use():
    with pytest.raises(ConfigError, match=r"\[notifications.waiting\].*Who.*command, who"):
        _with("waiting", message="{Who} waits")


def test_format_syntax_is_not_a_variable():
    with pytest.raises(ConfigError, match="unknown variable"):
        _with("fail", message="{command.__class__}")


def test_a_variable_from_another_event_is_refused():
    with pytest.raises(ConfigError, match="who"):
        _with("fail", message="{who}")
    with pytest.raises(ConfigError, match="can use: none"):
        _with("plain", title="{command}")


@pytest.mark.parametrize("doc, match", [
    ({"notifications": "x"}, "must be a table"),
    ({"notifications": {"fail": "x"}}, "must be a table"),
    ({"notifications": {"typo": {}}}, "unknown key"),
    ({"notifications": {"fail": {"body": "x"}}}, "unknown key"),
    ({"notifications": {"fail": {"message": ""}}}, "empty"),
    ({"notifications": {"fail": {"message": 5}}}, "quoted text"),
])
def test_bad_tables_are_clear(doc, match):
    with pytest.raises(ConfigError, match=match):
        parse_notifications(doc, DEFAULTS)


def test_the_webhook_gets_a_masked_value_as_written():
    # Masking is for what's written to disk. A value someone chose to put in a push reaches them
    masker = SecretMasker()
    masker.add("ABCLLC")
    posted = []
    note = {"title": "t", "message": "ABCLLC: MFA link needed"}
    send_notification(note, masker, lambda n, m: "https://example.invalid/hook",
                      lambda u, p: posted.append(p) or 200)
    assert posted == [note]
