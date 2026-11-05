"""capture_pdf in real Edge against a local server on 127.0.0.1, with made-up PDFs.

Skipped where Edge isn't installed. It runs headless by default, but headless Edge treats
PDFs differently, and the jobs run headed. To check the real behavior, open windows with:

    $env:AUTOMATION_TEST_HEADED = "1"; uv run pytest common/tests/test_capture_pdf.py
"""

import http.server
import os
import threading
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error as PwError
from playwright.sync_api import sync_playwright

from automation_common.browser import PdfCaptureError, capture_pdf

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
HEADED = bool(os.environ.get("AUTOMATION_TEST_HEADED"))

LIST_PAGE = b"""<!doctype html><title>list</title>
<a id="attachment" href="/attachment.pdf">a</a>
<a id="inline_same_tab" href="/inline.pdf">a</a>
<a id="inline_new_tab" href="/inline.pdf" target="_blank">a</a>
<button id="window_open" onclick="window.open('/inline.pdf')">a</button>
<a id="no_pdf_in_address" href="/document?id=1" target="_blank">a</a>
<a id="octet_stream" href="/octet" target="_blank">a</a>
<a id="redirect" href="/redirect" target="_blank">a</a>
<a id="single_use_inline" href="/once-inline.pdf" target="_blank">a</a>
<a id="single_use_attachment" href="/once-attachment.pdf">a</a>
<button id="from_script" onclick="fetch('/from-script.pdf').then(r => r.blob())
  .then(b => window.open(URL.createObjectURL(b)))">a</button>
<form method="post" action="/post-inline"><button id="post_inline">a</button></form>
<form method="post" action="/post-attachment"><button id="post_attachment">a</button></form>
<form method="post" action="/post-inline" target="_blank"><input type="hidden" name="n" value="1">
  <button id="post_new_tab">a</button></form>
<a id="needs_session" href="/private.pdf" target="_blank">a</a>
<a id="sign_in_page" href="/signin" target="_blank">a</a>
<a id="missing" href="/missing.pdf" target="_blank">a</a>
<button id="nothing">a</button>
<button id="built_in_page" onclick="window.open(URL.createObjectURL(new Blob(
  ['%PDF-1.4'], {type: 'application/pdf'})))">a</button>"""

ATTACHMENT = {"Content-Disposition": 'attachment; filename="0a1b-random.pdf"'}


class Site:
    def __init__(self):
        self.hits = {}
        self.post_bodies = []
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _send(self, status, body, kind="application/pdf", headers=None):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                path = urlsplit(self.path).path
                site.hits[path] = site.hits.get(path, 0) + 1
                length = int(self.headers.get("Content-Length") or 0)
                site.post_bodies.append(self.rfile.read(length))
                self._send(200, PDF, headers=ATTACHMENT if "attachment" in path else None)

            def do_GET(self):
                path = urlsplit(self.path).path
                site.hits[path] = site.hits.get(path, 0) + 1
                if path == "/":
                    self._send(200, LIST_PAGE, "text/html",
                               {"Set-Cookie": "session=made-up; HttpOnly"})
                elif path.startswith("/once") and site.hits[path] > 1:
                    self._send(403, b"this link was already used", "text/plain")
                elif path == "/redirect":
                    self._send(302, b"", "text/plain", {"Location": "/inline.pdf"})
                elif path == "/octet":
                    self._send(200, PDF, "application/octet-stream")
                elif path == "/signin":
                    self._send(200, b"<h1>Please sign in</h1>", "text/html")
                elif path == "/private.pdf":
                    if "session=" in self.headers.get("Cookie", ""):
                        self._send(200, PDF)
                    else:
                        self._send(401, b"sign in first", "text/plain")
                elif path == "/missing.pdf" or path == "/favicon.ico":
                    self._send(404, b"not here", "text/plain")
                else:
                    self._send(200, PDF, headers=ATTACHMENT if "attachment" in path else None)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture(scope="module")
def edge():
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="msedge", headless=not HEADED)
        except PwError:
            pytest.skip("Edge isn't installed here")
        yield browser
        browser.close()


@pytest.fixture
def site():
    fake = Site()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def page(edge, site):
    ctx = edge.new_context(accept_downloads=True)
    tab = ctx.new_page()
    tab.goto(site.base + "/")
    yield tab
    ctx.close()


def _capture(page, control, dest, timeout_ms=5_000):
    capture_pdf(page, lambda: page.locator(f"#{control}").click(), dest, timeout_ms)


@pytest.mark.parametrize(("control", "path"), [
    ("attachment", "/attachment.pdf"),
    ("inline_same_tab", "/inline.pdf"),
    ("inline_new_tab", "/inline.pdf"),
    ("window_open", "/inline.pdf"),
    ("no_pdf_in_address", "/document"),
    ("octet_stream", "/octet"),
    ("redirect", "/redirect"),
    ("single_use_inline", "/once-inline.pdf"),
    ("single_use_attachment", "/once-attachment.pdf"),
    ("from_script", "/from-script.pdf"),
    ("post_inline", "/post-inline"),
    ("post_attachment", "/post-attachment"),
    ("post_new_tab", "/post-inline"),
    ("needs_session", "/private.pdf"),
])
def test_saves_the_exact_pdf_with_one_request(page, site, tmp_path, control, path):
    dest = tmp_path / "invoice.pdf"
    _capture(page, control, dest)
    assert dest.read_bytes() == PDF
    assert site.hits[path] == 1


def test_a_post_keeps_its_form_data(page, site, tmp_path):
    _capture(page, "post_new_tab", tmp_path / "invoice.pdf")
    assert site.post_bodies == [b"n=1"]


@pytest.mark.parametrize("control", ["inline_new_tab", "window_open", "inline_same_tab"])
def test_leaves_the_starting_page_ready_for_the_next_click(page, site, tmp_path, control):
    _capture(page, control, tmp_path / "invoice.pdf")
    assert page.context.pages == [page]
    assert page.url == site.base + "/"
    assert page.locator("#nothing").is_visible()


def test_works_again_on_the_next_row(page, site, tmp_path):
    for n, control in enumerate(["inline_new_tab", "attachment", "inline_same_tab", "post_inline"]):
        dest = tmp_path / f"invoice_{n}.pdf"
        _capture(page, control, dest)
        assert dest.read_bytes() == PDF


@pytest.mark.parametrize("control", ["sign_in_page", "missing", "nothing", "built_in_page"])
def test_no_pdf_fails_clearly_and_saves_nothing(page, site, tmp_path, control):
    dest = tmp_path / "invoice.pdf"
    with pytest.raises(PdfCaptureError, match="no PDF arrived") as caught:
        _capture(page, control, dest, timeout_ms=1_500)
    assert site.base not in str(caught.value)
    assert list(tmp_path.iterdir()) == []
    assert page.context.pages == [page]


def test_requests_after_the_capture_are_left_alone(page, site, tmp_path):
    _capture(page, "inline_new_tab", tmp_path / "invoice.pdf")
    page.reload()
    assert page.title() == "list"
    # A routed page would keep answering through capture_pdf's handler
    assert page.context.pages == [page]


def test_a_failed_click_cleans_up_and_raises(page, tmp_path):
    def bad_click():
        raise RuntimeError("the button went away")
    with pytest.raises(RuntimeError, match="went away"):
        capture_pdf(page, bad_click, tmp_path / "invoice.pdf", 500)
    assert page.context.pages == [page]
    assert list(tmp_path.iterdir()) == []
