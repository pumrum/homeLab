import json
from types import SimpleNamespace

import pytest

from automation_common.secrets import SecretMasker
from automation_common.sheets import (
    GoogleWorksheet,
    SheetError,
    _api_error,
    load_service_account,
    open_worksheet,
    replace_requests,
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


def _cells(request):
    return [r["values"][0]["userEnteredValue"]["stringValue"]
            for r in request["updateCells"]["rows"]]


def test_replace_covers_only_the_column_from_the_start_cell_to_the_bottom():
    requests = replace_requests(7, 1000, "B5", ["one", "two"])
    assert len(requests) == 1
    update = requests[0]["updateCells"]
    assert update["range"] == {
        "sheetId": 7,
        "startRowIndex": 4,  # row 5: rows 1 to 4 are outside the range
        "endRowIndex": 1000,
        "startColumnIndex": 1,
        "endColumnIndex": 2,  # column B only
    }
    assert _cells(requests[0]) == ["one", "two"]
    assert update["fields"] == "userEnteredValue"  # formatting stays


def test_replace_writes_values_as_literal_text():
    requests = replace_requests(7, 1000, "A2", ["=SUM(A1:A9)", "2026-01-31", "00123"])
    assert _cells(requests[0]) == ["=SUM(A1:A9)", "2026-01-31", "00123"]


def test_replace_grows_a_tab_that_is_too_short_in_the_same_request():
    requests = replace_requests(7, 10, "B5", ["x"] * 20)  # needs rows up to 24
    assert requests[0] == {"appendDimension": {"sheetId": 7, "dimension": "ROWS", "length": 14}}
    assert requests[1]["updateCells"]["range"]["endRowIndex"] == 24


def test_replace_refuses_a_bad_cell():
    with pytest.raises(SheetError, match="not a cell"):
        replace_requests(7, 10, "not-a-cell", ["x"])


class _FakeSpreadsheet:
    def __init__(self, error=None):
        self.bodies = []
        self.error = error

    def batch_update(self, body):
        if self.error:
            raise self.error
        self.bodies.append(body)


def _worksheet(spreadsheet, row_count=10):
    return GoogleWorksheet(SimpleNamespace(id=7, row_count=row_count, spreadsheet=spreadsheet))


def test_replace_from_cell_sends_one_request_however_many_lines():
    book = _FakeSpreadsheet()
    _worksheet(book, 1000).replace_from_cell("B5", [f"line {n}" for n in range(500)])
    assert len(book.bodies) == 1


def test_a_second_replace_knows_the_first_one_grew_the_tab():
    book = _FakeSpreadsheet()
    ws = _worksheet(book, row_count=10)
    ws.replace_from_cell("A1", ["x"] * 20)
    ws.replace_from_cell("B1", ["y"])
    second = book.bodies[1]["requests"]
    assert len(second) == 1  # no second appendDimension
    assert second[0]["updateCells"]["range"]["endRowIndex"] == 20


def test_replace_failure_reports_only_the_status(monkeypatch):
    class FakeApiError(Exception):
        response = SimpleNamespace(status_code=500)

    monkeypatch.setattr("automation_common.sheets.gspread.exceptions.APIError", FakeApiError)
    with pytest.raises(SheetError, match="error 500 writing to the sheet"):
        _worksheet(_FakeSpreadsheet(FakeApiError("id made-up"))).replace_from_cell("A1", ["x"])


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
