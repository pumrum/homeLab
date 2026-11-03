"""Secrets for the current Windows user. Never logs values.

Entry names: `<process>/<name>` for one automation (e.g. `<process>/login`),
`automation/<name>` for shared ones (e.g. `automation/home-assistant-webhook`).

Two stores, both readable only by the Windows user that created them:
  - Windows Credential Manager (via keyring) for short values: passwords,
    tokens, URLs. Created by hand on each machine; nothing here writes them.
  - DPAPI-encrypted files for values too large for Credential Manager
    (it holds at most 2,560 bytes), e.g. a Google service account key:
    `%USERPROFILE%\\.<prefix>\\<name>.dpapi`. Created with
    tools/import_secret_file.py.
"""

import ctypes
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import keyring
from keyring.errors import KeyringError

log = logging.getLogger(__name__)

MASK = "***"
SECRET_FILE_SUFFIX = ".dpapi"


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


# ---------------------------------------------------------------- secret files


def secret_file_path(name: str, home: Path | None = None) -> Path:
    """Where secret `name` is stored.

    `automation/google-service-account` ->
    %USERPROFILE%\\.automation\\google-service-account.dpapi
    """
    prefix, sep, rest = name.partition("/")
    if not sep or not prefix or not rest or "/" in rest or "\\" in name or ".." in name:
        raise ValueError(f"secret name must look like <prefix>/<name>, got {name!r}")
    home = home or Path(os.environ.get("USERPROFILE") or Path.home())
    return home / f".{prefix}" / f"{rest}{SECRET_FILE_SUFFIX}"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(protect: bool, data: bytes, entropy: bytes) -> bytes:
    """Encrypt or decrypt with Windows DPAPI for the current user, without any UI."""
    if sys.platform != "win32":
        raise RuntimeError("encrypted secret files need Windows (DPAPI)")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffers = [ctypes.create_string_buffer(b, len(b)) for b in (data, entropy)]
    blob_in, blob_entropy = (
        _Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        for b, buf in zip((data, entropy), buffers, strict=True)
    )
    blob_out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ui_forbidden = 0x1  # CRYPTPROTECT_UI_FORBIDDEN: never show a prompt
    if not fn(ctypes.byref(blob_in), None, ctypes.byref(blob_entropy), None, None,
              ui_forbidden, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def write_secret_file(name: str, data: bytes, home: Path | None = None) -> Path:
    """Encrypt `data` for the current Windows user and save it as the secret file `name`."""
    path = secret_file_path(name, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The entry name is mixed in, so a file renamed to another entry won't decrypt
    path.write_bytes(_dpapi(True, data, name.encode("utf-8")))
    return path


def read_secret_file(name: str, home: Path | None = None) -> bytes | None:
    """Decrypt the secret file `name`, or return None if it doesn't exist."""
    path = secret_file_path(name, home)
    if not path.is_file():
        log.info("secret file for %s not found", name)
        return None
    try:
        return _dpapi(False, path.read_bytes(), name.encode("utf-8"))
    except OSError as e:
        # Wrong Windows user, or the file was made for a different entry name
        raise RuntimeError(
            f"secret file for {name} can't be decrypted by this Windows user "
            f"({type(e).__name__}); import it again as this user"
        ) from e
