import json
from types import SimpleNamespace

import pytest

from automation_common.secrets import SecretMasker
from automation_common.sheets import (
    SheetError,
    _api_error,
    load_service_account,
    open_worksheet,
)

FAKE_KEY = {
    "type": "service_account",
    "client_email": "robot@example.invalid",
    "private_key_id": "made-up-key-id",
    "private_key": "-----MADE UP KEY-----",
}


def test_missing_key_says_how_to_import():
    with pytest.raises(SheetError, match="import_secret_file"):
        load_service_account(SecretMasker(), read=lambda name: None)


@pytest.mark.parametrize(("data", "message"), [
    (b"not json", "not valid JSON"),
    (json.dumps({"type": "authorized_user"}).encode(), "not a service account"),
])
def test_bad_key_is_refused(data, message):
    with pytest.raises(SheetError, match=message):
        load_service_account(SecretMasker(), read=lambda name: data)


def test_key_parts_are_masked():
    m = SecretMasker()
    info = load_service_account(m, read=lambda name: json.dumps(FAKE_KEY).encode())
    assert info["client_email"] == "robot@example.invalid"
    text = m.mask(json.dumps(FAKE_KEY))
    for secret in ("-----MADE UP KEY-----", "made-up-key-id", "robot@example.invalid"):
        assert secret not in text


def test_spreadsheet_id_is_masked_even_when_opening_fails():
    m = SecretMasker()

    def no_key(masker):
        raise SheetError("no key")

    with pytest.raises(SheetError):
        open_worksheet("made-up-spreadsheet-id-0123456789", "NAV", m, load=no_key)
    assert m.mask("id made-up-spreadsheet-id-0123456789") == "id ***"


@pytest.mark.parametrize(("status", "hint"), [(403, True), (404, True), (500, False)])
def test_api_error_message_has_only_the_status(status, hint):
    e = SimpleNamespace(response=SimpleNamespace(status_code=status))
    msg = str(_api_error("reading", e))
    assert msg.startswith(f"Google Sheets error {status} reading the sheet")
    assert ("shared with the service account" in msg) is hint
