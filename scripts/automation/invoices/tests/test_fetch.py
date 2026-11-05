"""The SFTP step, with paramiko's client faked: nothing touches a network."""

import io

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization as ser
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from invoices_sync import fetch
from invoices_sync.config import Sftp
from invoices_sync.fetch import FetchError, load_private_key, open_source

CFG = Sftp("sftp.example.invalid", 2222, "made-up", "/made/up")


def _key_text(key, encryption=None):
    return key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.OpenSSH,
                             encryption or ser.NoEncryption()).decode()


@pytest.fixture(scope="module")
def key_text():
    return _key_text(ed25519.Ed25519PrivateKey.generate())


@pytest.mark.parametrize(("make", "expected"), [
    (ed25519.Ed25519PrivateKey.generate, paramiko.Ed25519Key),
    (lambda: ec.generate_private_key(ec.SECP256R1()), paramiko.ECDSAKey),
    (lambda: rsa.generate_private_key(65537, 2048), paramiko.RSAKey),
])
def test_keys_load_from_memory(make, expected):
    assert isinstance(load_private_key(_key_text(make())), expected)


def test_a_key_with_a_passphrase_is_refused():
    text = _key_text(ed25519.Ed25519PrivateKey.generate(),
                     ser.BestAvailableEncryption(b"made-up"))
    with pytest.raises(FetchError, match="passphrase"):
        load_private_key(text)


def test_a_bad_key_error_does_not_repeat_the_key():
    with pytest.raises(FetchError, match="can't be read") as e:
        load_private_key("not-a-real-key-value")
    assert "not-a-real-key-value" not in str(e.value)


class FakeRemoteFile(io.BytesIO):
    pass


class FakeSftp:
    def __init__(self):
        self.closed = False
        self.opened = []
        self.channel_timeout = None

    def get_channel(self):
        return self

    def settimeout(self, seconds):
        self.channel_timeout = seconds

    def open(self, path, mode):
        self.opened.append((path, mode))
        if path.endswith("missing.txt"):
            raise FileNotFoundError(2, "No such file")
        return FakeRemoteFile(b"one\ntwo\n")

    def close(self):
        self.closed = True


class FakeClient:
    last = None

    def __init__(self):
        FakeClient.last = self
        self.host_key_files = []
        self.policy = None
        self.connect_args = None
        self.closed = False
        self.sftp = FakeSftp()
        self.connect_error = None

    def load_host_keys(self, path):
        self.host_key_files.append(path)

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, host, **kwargs):
        if FakeClient.connect_error:
            raise FakeClient.connect_error
        self.connect_args = {"host": host, **kwargs}

    def open_sftp(self):
        return self.sftp

    def close(self):
        self.closed = True


@pytest.fixture
def client(monkeypatch):
    FakeClient.connect_error = None
    monkeypatch.setattr(fetch.paramiko, "SSHClient", FakeClient)


@pytest.fixture
def known_hosts(tmp_path):
    path = tmp_path / "known_hosts"
    path.write_text("made-up\n", encoding="ascii")
    return path


def test_connection_verifies_the_host_and_uses_only_the_given_key(client, known_hosts, key_text):
    with open_source(CFG, key_text, known_hosts):
        c = FakeClient.last
        assert c.host_key_files == [str(known_hosts)]
        assert isinstance(c.policy, paramiko.RejectPolicy)
        args = c.connect_args
        assert (args["host"], args["port"], args["username"]) == (CFG.host, 2222, CFG.user)
        assert isinstance(args["pkey"], paramiko.Ed25519Key)
        assert args["allow_agent"] is False and args["look_for_keys"] is False


def test_every_wait_has_a_timeout(client, known_hosts, key_text):
    with open_source(CFG, key_text, known_hosts):
        c = FakeClient.last
        for name in ("timeout", "banner_timeout", "auth_timeout", "channel_timeout"):
            assert c.connect_args[name] == fetch.CONNECT_TIMEOUT_SECONDS
        assert c.sftp.channel_timeout == fetch.OPERATION_TIMEOUT_SECONDS


def test_missing_known_hosts_is_refused_before_connecting(client, tmp_path, key_text):
    FakeClient.last = None
    with pytest.raises(FetchError, match="no trusted host key"):
        with open_source(CFG, key_text, tmp_path / "known_hosts"):
            pass
    assert FakeClient.last is None


def test_read_joins_the_folder_with_forward_slashes_and_only_reads(client, known_hosts, key_text):
    with open_source(CFG, key_text, known_hosts) as source:
        assert source.read("inv_a.txt") == b"one\ntwo\n"
    assert FakeClient.last.sftp.opened == [("/made/up/inv_a.txt", "rb")]


def test_missing_file_raises_for_that_file(client, known_hosts, key_text):
    with open_source(CFG, key_text, known_hosts) as source:
        with pytest.raises(FileNotFoundError):
            source.read("missing.txt")


def test_connection_closes_after_an_error(client, known_hosts, key_text):
    with pytest.raises(RuntimeError):
        with open_source(CFG, key_text, known_hosts):
            raise RuntimeError("made up")
    assert FakeClient.last.sftp.closed and FakeClient.last.closed


def test_connection_closes_when_sign_in_fails(client, known_hosts, key_text):
    FakeClient.connect_error = paramiko.AuthenticationException("made up")
    with pytest.raises(paramiko.AuthenticationException):
        with open_source(CFG, key_text, known_hosts):
            pass
    assert FakeClient.last.closed
