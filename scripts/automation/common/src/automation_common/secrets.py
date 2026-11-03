"""Secrets from Windows Credential Manager via keyring. Never logs values.

Entry names: `<process>/<name>` for one automation (e.g. `<process>/login`),
`automation/<name>` for shared ones (e.g. `automation/home-assistant-webhook`).
Entries are created by hand on each machine; nothing here writes them.
"""

import logging
from dataclasses import dataclass, field

import keyring
from keyring.errors import KeyringError

log = logging.getLogger(__name__)

MASK = "***"


class SecretMasker:
    """Remembers secret values and replaces them in text (used by the log formatter)."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def add(self, value: str) -> None:
        if value:
            self._values.add(value)

    def mask(self, text: str) -> str:
        # Longest first, so a secret containing another is fully hidden
        for value in sorted(self._values, key=len, reverse=True):
            text = text.replace(value, MASK)
        return text


@dataclass(frozen=True)
class Credential:
    username: str
    password: str = field(repr=False)


def get_credential(name: str, masker: SecretMasker | None = None) -> Credential | None:
    """Return the Credential Manager entry `name`, or None if it doesn't exist.

    The password is added to `masker` so it is hidden if it ever reaches a log.
    """
    try:
        cred = keyring.get_credential(name, None)
    except KeyringError as e:
        log.warning("credential store error for %s: %s", name, type(e).__name__)
        return None
    if cred is None or not cred.password:
        log.info("credential %s not found", name)
        return None
    if masker is not None:
        masker.add(cred.password)
    return Credential(cred.username or "", cred.password)


def get_secret(name: str, masker: SecretMasker | None = None) -> str | None:
    """Return just the secret value of entry `name` (e.g. a webhook URL), or None."""
    cred = get_credential(name, masker)
    return cred.password if cred else None
