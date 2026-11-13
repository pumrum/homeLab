"""Small page helpers every command in this package shares.

The site builds its controls from web components, so most of its text sits in shadow roots,
which `inner_text` never reads. `visible_text` walks into them, and into every frame,
and keeps only what's on screen, since pages hold hidden copies of controls.
"""

import logging

from playwright.sync_api import Error as PwError
from playwright.sync_api import Locator, Page

from automation_common.browser import Scope, find_first

log = logging.getLogger(__name__)

VISIBLE_TEXT_JS = r"""
(root) => {
  const out = [], skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
  const shown = (el) => el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden';
  const walk = (node) => {
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        const text = child.textContent.trim();
        if (text && child.parentElement && shown(child.parentElement)) out.push(text);
      } else if (child.nodeType === Node.ELEMENT_NODE && !skip.has(child.tagName)) {
        if (child.shadowRoot) walk(child.shadowRoot);
        walk(child);
      }
    }
  };
  const start = root || document.body || document.documentElement;
  if (start.shadowRoot) walk(start.shadowRoot);  // an element that is itself a component
  walk(start);
  return out.join('\n');
}
"""


class FetchError(Exception):
    """A page didn't do what a command needed. The message never holds a private value."""


def visible_text(page: Page) -> str:
    """The text on screen in the page and all its frames, including shadow roots."""
    parts: list[str] = []
    for frame in page.frames:
        try:
            parts.append(frame.evaluate(VISIBLE_TEXT_JS))
        except PwError:
            continue  # a frame that's navigating or gone
    return "\n".join(parts)


def element_text(loc: Locator) -> str:
    """The text on screen inside one element, including its shadow roots."""
    return loc.evaluate(VISIBLE_TEXT_JS)


def required(scopes: list[Scope], candidates: list[str], what: str, timeout_ms: int) -> Locator:
    found, selector = find_first(scopes, candidates, timeout_ms)
    if found is None:
        raise FetchError(f"no {what} within {timeout_ms // 1000}s")
    log.info("found the %s (%s)", what, selector)
    return found
