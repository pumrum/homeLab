import keyring
import pytest
from keyring.credentials import SimpleCredential
from keyring.errors import KeyringError

from automation_common import secrets
from automation_common.notify import WEBHOOK_SECRET, send_notification
from automation_common.secrets import SecretMasker, get_credential, get_secret


def test_masker_hides_longest_first():
    m = SecretMasker()
    m.add("abc")
    m.add("abcdef")
    assert m.mask("x abcdef y abc") == "x *** y ***"


def test_get_credential_registers_value_with_masker(monkeypatch):
    monkeypatch.setattr(
        secrets.keyring, "get_credential", lambda name, user: SimpleCredential("u", "pw-123")
    )
    m = SecretMasker()
    cred = get_credential("example/test", m)
    assert cred is not None and cred.username == "u"
    assert "pw-123" not in repr(cred)
    assert m.mask("pw-123") == "***"


def test_missing_credential_returns_none(monkeypatch):
    monkeypatch.setattr(keyring, "get_credential", lambda name, user: None)
    assert get_secret("automation/nothing") is None


def test_keyring_error_returns_none(monkeypatch):
    def boom(name, user):
        raise KeyringError("no backend")

    monkeypatch.setattr(keyring, "get_credential", boom)
    assert get_secret("automation/nothing") is None


NOTE = {"title": "example", "message": "PASS"}


def test_notify_without_webhook_only_logs(caplog):
    posted = []
    caplog.set_level("INFO")
    ok = send_notification(NOTE, SecretMasker(), lambda n, m: None,
                           lambda u, p: posted.append(u))
    assert ok is False and posted == []
    assert "no webhook configured" in caplog.text


def test_notify_posts_and_never_logs_url(caplog):
    posted = []
    caplog.set_level("DEBUG")

    def lookup(name, masker):
        assert name == WEBHOOK_SECRET
        return "https://example.invalid/hook"

    ok = send_notification(NOTE, SecretMasker(), lookup,
                           lambda u, p: posted.append((u, p)) or 200)
    assert ok is True
    assert posted == [("https://example.invalid/hook", NOTE)]
    assert "example.invalid" not in caplog.text


@pytest.mark.parametrize("exc", [OSError("down"), TimeoutError()])
def test_notify_failure_never_raises(exc):
    def post(url, payload):
        raise exc

    assert send_notification({}, SecretMasker(), lambda n, m: "https://x.invalid", post) is False
