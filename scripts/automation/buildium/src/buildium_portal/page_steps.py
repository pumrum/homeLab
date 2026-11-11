"""Small browser steps that every command in this package shares."""

import logging

from playwright.sync_api import Locator, Page

from automation_common.browser import click_until, find_first

log = logging.getLogger(__name__)


class FetchError(Exception):
    """A page didn't do what a command needed. The message never holds a private value."""


def required(page: Page, candidates: list[str], what: str, timeout_ms: int) -> Locator:
    found, selector = find_first([page], candidates, timeout_ms)
    if found is None:
        raise FetchError(f"no {what} within {timeout_ms // 1000}s")
    log.info("found %s (%s)", what, selector)
    return found


def open_menu(page: Page, menu: Locator, item: list[str], what: str, timeout_ms: int) -> None:
    def item_showing() -> bool:
        return find_first([page], item, timeout_ms)[0] is not None
    if click_until(menu, item_showing, what) is None:
        raise FetchError(f"the {what} did not open")
