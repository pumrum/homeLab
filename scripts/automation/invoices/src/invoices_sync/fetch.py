"""Read files over SFTP, signing in with a private key held only in memory.

The server's host key must already be in the process's known_hosts file. An
unknown or changed host key is refused, never accepted automatically.
"""

import io
import logging
import posixpath
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

import paramiko

from invoices_sync.config import Sftp

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 20  # each of: network connection, server greeting, sign-in
OPERATION_TIMEOUT_SECONDS = 60  # each wait on the server once connected


class FetchError(Exception):
    """The connection can't be set up. The message says how to fix it."""


class FileSource(Protocol):
    """What the run needs from the server (tests use a fake)."""

    def read(self, file_name: str) -> bytes: ...


class _SftpSource:
    def __init__(self, sftp: paramiko.SFTPClient, folder: str) -> None:
        self._sftp = sftp
        self._folder = folder

    def read(self, file_name: str) -> bytes:
        with self._sftp.open(posixpath.join(self._folder, file_name), "rb") as f:
            return f.read()


def load_private_key(text: str) -> paramiko.PKey:
    """Parse a private key from text. Errors never include any of the key."""
    for key_class in paramiko.key_classes:
        try:
            return key_class.from_private_key(io.StringIO(text))
        except paramiko.PasswordRequiredException:
            raise FetchError(
                "the stored SSH key has a passphrase; this job runs unattended, "
                "so it needs a key without one"
            ) from None
        except (paramiko.SSHException, ValueError, TypeError, IndexError):
            continue  # not this type of key: try the next one
    raise FetchError("the stored SSH key can't be read as a private key")


@contextmanager
def open_source(cfg: Sftp, key_text: str, known_hosts: Path) -> Iterator[FileSource]:
    """Connect, yield a FileSource, and always disconnect."""
    if not known_hosts.is_file():
        raise FetchError(f"no trusted host key: {known_hosts} doesn't exist; see the README")
    key = load_private_key(key_text)
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    client = paramiko.SSHClient()
    try:
        client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            cfg.host,
            port=cfg.port,
            username=cfg.user,
            pkey=key,
            allow_agent=False,
            look_for_keys=False,
            timeout=CONNECT_TIMEOUT_SECONDS,
            banner_timeout=CONNECT_TIMEOUT_SECONDS,
            auth_timeout=CONNECT_TIMEOUT_SECONDS,
            channel_timeout=CONNECT_TIMEOUT_SECONDS,
        )
        sftp = client.open_sftp()
        try:
            sftp.get_channel().settimeout(OPERATION_TIMEOUT_SECONDS)
            yield _SftpSource(sftp, cfg.folder)
        finally:
            sftp.close()
    finally:
        client.close()
