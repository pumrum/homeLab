"""Optional Home Assistant webhook: POST the run summary as JSON.

The webhook URL is a secret (`automation/home-assistant-webhook`); it is
never logged. If the entry doesn't exist, the summary is only logged.
"""

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Any

from automation_common.secrets import SecretMasker, get_secret

log = logging.getLogger(__name__)

WEBHOOK_SECRET = "automation/home-assistant-webhook"


def post_json(url: str, payload: dict[str, Any], timeout_s: float = 15) -> int:
    """POST `payload` as JSON and return the HTTP status."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - https from secret
        return resp.status


def notify_summary(
    summary: dict[str, Any],
    masker: SecretMasker,
    lookup: Callable[[str, SecretMasker], str | None] = get_secret,
    post: Callable[[str, dict[str, Any]], int] = post_json,
) -> bool:
    """Send the summary to the webhook if one is configured. Never raises."""
    try:
        url = lookup(WEBHOOK_SECRET, masker)
        if not url:
            log.info("no webhook configured; summary: %s", json.dumps(summary))
            return False
        status = post(url, summary)
        log.info("summary sent to webhook (HTTP %s)", status)
        return True
    except Exception as e:  # noqa: BLE001 - a failed notification must not fail the run
        log.error("webhook notification failed: %s", type(e).__name__)
        return False
