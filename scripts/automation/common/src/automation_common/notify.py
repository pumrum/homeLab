"""Optional Home Assistant webhook: POST {"title": ..., "message": ...} as JSON.

Only what the notification shows is sent; the full run summary stays in the
run folder. The webhook URL is a secret (`automation/home-assistant-webhook`);
it is never logged. If the entry doesn't exist, the notification is only logged.

The words live in each process's config.toml, one optional table per event:

    [notifications.fail]
    title = "{command}"
    message = "FAIL: {failed} of {total}"

A process's defaults name its events and the {variables} each one can use. A
missing table or key keeps the default, and those defaults are also what a run
sends when its config can't be loaded.
"""

import json
import logging
import re
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from automation_common.config import ConfigError, reject_unknown_keys, require_str
from automation_common.secrets import SecretMasker, get_secret

log = logging.getLogger(__name__)

WEBHOOK_SECRET = "automation/home-assistant-webhook"

_VARIABLE = re.compile(r"\{([^{}]*)\}")

# What Run fills in for every event
RUN_VARIABLES = frozenset({"process", "command", "run_id", "ok", "unchanged", "failed", "total"})


@dataclass(frozen=True)
class Template:
    """One event's words. `variables` is what its {placeholders} may name."""

    title: str
    message: str
    variables: frozenset[str] = RUN_VARIABLES

    def render(self, **values: object) -> dict[str, str]:
        """The notification to send. Each value goes in as is, and is never expanded again."""
        def fill(text: str) -> str:
            return _VARIABLE.sub(lambda m: str(values.get(m[1], m[0])), text)
        return {"title": fill(self.title), "message": fill(self.message)}


# The events every Run sends. A process adds its own to these
STANDARD_NOTIFICATIONS: Mapping[str, Template] = {
    "pass": Template("{command}", "PASS"),
    "fail": Template("{command}", "FAIL"),
}


def _text(table: Mapping[str, Any], key: str, default: Template, where: str) -> str:
    text = require_str(table, key, where) if key in table else getattr(default, key)
    unknown = sorted(set(_VARIABLE.findall(text)) - default.variables)
    if unknown:
        raise ConfigError(
            f'{where}: "{key}" uses unknown variable(s) {", ".join(unknown)}. '
            f"It can use: {', '.join(sorted(default.variables)) or 'none'}"
        )
    return text


def parse_notifications(
    doc: Mapping[str, Any], defaults: Mapping[str, Template]
) -> Mapping[str, Template]:
    """Read the optional [notifications.<event>] tables over `defaults`."""
    tables = doc.get("notifications", {})
    if not isinstance(tables, dict):
        raise ConfigError("[notifications] must be a table")
    reject_unknown_keys(tables, set(defaults), "[notifications]")
    parsed = {}
    for event, default in defaults.items():
        where = f"[notifications.{event}]"
        table = tables.get(event, {})
        if not isinstance(table, dict):
            raise ConfigError(f"{where} must be a table")
        reject_unknown_keys(table, {"title", "message"}, where)
        parsed[event] = Template(_text(table, "title", default, where),
                                 _text(table, "message", default, where), default.variables)
    return parsed


def post_json(url: str, payload: dict[str, Any], timeout_s: float = 15) -> int:
    """POST `payload` as JSON and return the HTTP status."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - URL from secret
        return resp.status


def send_notification(
    notification: dict[str, str],
    masker: SecretMasker,
    lookup: Callable[[str, SecretMasker], str | None] = get_secret,
    post: Callable[[str, dict[str, Any]], int] = post_json,
) -> bool:
    """Send {"title", "message"} to the webhook if one is configured. Never raises."""
    try:
        url = lookup(WEBHOOK_SECRET, masker)
        if not url:
            log.info("no webhook configured; notification: %s", json.dumps(notification))
            return False
        status = post(url, notification)
        log.info("notification sent to webhook (HTTP %s)", status)
        return True
    except Exception as e:  # noqa: BLE001 - a failed notification must not fail the run
        log.error("webhook notification failed: %s", type(e).__name__)
        return False
