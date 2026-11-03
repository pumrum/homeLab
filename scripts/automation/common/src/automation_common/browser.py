"""Browser helpers (Playwright driving the installed Microsoft Edge).

Learned on a real bank site and kept on purpose:
  - pick the first VISIBLE match among candidate selectors, most specific first
    (pages hold hidden copies of controls)
  - escalate clicks: normal, forced, keyboard; verify the result after each
  - type dates key by key, then Tab out, then read the value back
  - never use fixed sleeps: wait for elements, URLs, or downloads
  - on failure save a screenshot and a shadow-DOM-piercing page outline
"""

import logging
import time
from collections.abc import Callable, Sequence
from functools import reduce
from pathlib import Path

from playwright.sync_api import BrowserContext, Locator, Page, Playwright
from playwright.sync_api import Error as PwError
from playwright.sync_api import TimeoutError as PwTimeout

log = logging.getLogger(__name__)

# Keep rendering on a locked or hidden desktop. None of these weakens security.
_ARGS = [
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=CalculateNativeWinOcclusion",
]

Scope = Page | Locator  # a page, or a frame_locator / element to search inside


def launch_edge(pw: Playwright, profile_dir: Path) -> BrowserContext:
    """Start headed Edge with a persistent profile, sandbox on, downloads accepted."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        channel="msedge",
        headless=False,
        chromium_sandbox=True,
        accept_downloads=True,
        viewport={"width": 1300, "height": 850},
        args=_ARGS,
    )
    ctx.set_default_timeout(20_000)
    return ctx


def first_page(ctx: BrowserContext) -> Page:
    return ctx.pages[0] if ctx.pages else ctx.new_page()


def _first_visible(scope: Scope, candidates: Sequence[str]) -> tuple[Locator, str] | None:
    """Check candidates in order, every match of each, without waiting."""
    for sel in candidates:
        try:
            matches = scope.locator(sel)
            for i in range(min(matches.count(), 20)):
                loc = matches.nth(i)
                if loc.is_visible():
                    return loc, sel
        except PwError:
            continue
    return None


def find_first(
    scopes: Sequence[Scope], candidates: Sequence[str], timeout_ms: int = 8000
) -> tuple[Locator | None, str | None]:
    """Return the first VISIBLE match among candidate selectors and scopes.

    Waits (with Playwright's own waiting, not sleeps) until any candidate is
    visible in a scope, then picks the most specific one that is.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for scope in scopes:
            hit = _first_visible(scope, candidates)
            if hit:
                return hit
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            return None, None
        # Wait for any candidate to become visible, in each scope in turn
        slice_ms = max(250, remaining_ms // max(len(scopes), 1))
        for scope in scopes:
            anyof = reduce(Locator.or_, (scope.locator(s) for s in candidates))
            anyof = anyof.filter(visible=True).first
            try:
                anyof.wait_for(state="visible", timeout=min(slice_ms, remaining_ms))
                break
            except (PwTimeout, PwError):
                continue


def click_until(loc: Locator, done: Callable[[], bool], label: str) -> str | None:
    """Click, escalating only if `done()` says it didn't work: normal, forced, keyboard."""
    attempts: list[tuple[str, Callable[[], None]]] = [
        ("click", lambda: loc.click(timeout=5000)),
        ("forced click", lambda: loc.click(force=True, timeout=5000)),
        ("keyboard", lambda: (loc.focus(timeout=5000), loc.press("Enter", timeout=5000))),
    ]
    for how, attempt in attempts:
        try:
            attempt()
        except PwError as e:
            log.debug("%s: %s failed: %s", label, how, str(e).splitlines()[0])
            continue
        if done():
            return f"{label} via {how}"
    return None


def as_input(loc: Locator) -> Locator:
    """If the match is a wrapper element, use the <input> inside it."""
    tag = loc.evaluate("e => e.tagName.toLowerCase()")
    return loc if tag in ("input", "textarea") else loc.locator("input").first


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def type_date(loc: Locator, value: str) -> str:
    """Type a date key by key, Tab out, and check the box shows it.

    Some date boxes show a pasted value but don't register it, so the next
    button silently does nothing. Real keystrokes plus leaving the field
    (blur) make the page accept it.
    """
    box = as_input(loc)
    box.click()
    box.press("Control+A")
    box.press("Backspace")
    box.press_sequentially(value, delay=60)
    box.press("Tab")
    got = box.input_value()
    if _digits(got) != _digits(value):
        raise RuntimeError(f"date box shows '{got}', expected '{value}'")
    return got


# Outline of the page that walks into shadow roots, so a failed selector can be
# fixed from the saved file. Lists tag#id.class[role][aria-label] (hidden) "leaf text".
DOM_OUTLINE_JS = r"""
() => {
  const out = [], skip = new Set(['SCRIPT','STYLE','NOSCRIPT','svg','SVG','PATH']);
  const walk = (node, d) => {
    for (const el of node.children) {
      if (skip.has(el.tagName) || out.length > 6000) continue;
      try {
      const r = el.getBoundingClientRect();
      const role = el.getAttribute('role'), aria = el.getAttribute('aria-label');
      const cls = typeof el.className === 'string' && el.className.trim()
        ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
      const leaf = el.childElementCount === 0 ? (el.textContent || '').trim().slice(0, 60) : '';
      out.push('  '.repeat(d) + el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + cls
        + (role ? `[role=${role}]` : '') + (aria ? `[aria-label="${aria.slice(0, 40)}"]` : '')
        + (r.width && r.height ? '' : ' (hidden)') + (leaf ? ` "${leaf}"` : ''));
      if (el.shadowRoot) {
        out.push('  '.repeat(d + 1) + '#shadow-root');
        walk(el.shadowRoot, d + 2);
      }
      walk(el, d + 1);
      } catch (e) { out.push('  '.repeat(d) + '!error ' + e); }
    }
  };
  walk(document, 0);
  return out.join('\n');
}
"""


def save_page_evidence(page: Page, run_dir: Path, prefix: str) -> list[Path]:
    """Save <prefix>.png and <prefix>_page.txt (URL + outline). Never raises."""
    saved: list[Path] = []
    shot = run_dir / f"{prefix}.png"
    try:
        page.screenshot(path=str(shot), full_page=True)
        saved.append(shot)
    except PwError as e:
        log.warning("screenshot failed: %s", str(e).splitlines()[0])
    outline = run_dir / f"{prefix}_page.txt"
    try:
        text = page.url + "\n\n" + page.evaluate(DOM_OUTLINE_JS)
    except Exception as e:  # noqa: BLE001 - evidence is best effort
        text = f"page outline failed: {type(e).__name__}: {e}"
    outline.write_text(text, encoding="utf-8")
    saved.append(outline)
    return saved


def start_trace(ctx: BrowserContext) -> None:
    """Start a Playwright trace. Call only AFTER any sign-in is complete."""
    ctx.tracing.start(screenshots=True, snapshots=True)


def stop_trace(ctx: BrowserContext, save_to: Path | None) -> None:
    """Stop the trace, saving it only when `save_to` is given (i.e. on failure)."""
    try:
        if save_to is None:
            ctx.tracing.stop()
        else:
            ctx.tracing.stop(path=str(save_to))
    except PwError as e:
        log.warning("stopping trace failed: %s", str(e).splitlines()[0])
