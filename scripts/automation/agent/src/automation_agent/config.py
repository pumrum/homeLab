"""Load and validate C:\\automation\\agent\\config.toml."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from automation_common.config import (
    ConfigError,
    load_toml,
    reject_unknown_keys,
    require_str,
    require_tables,
)

_JOB = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# A console script's bare name: no paths, spaces, or arguments can get through
_COMMAND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PREFIX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_PLACEHOLDERS = ("<", ">", "...")
DEFAULT_TIMEOUT_MINUTES = 30


@dataclass(frozen=True)
class Mqtt:
    host: str
    port: int = 1883
    tls: bool = False
    topic_prefix: str = "automation"


@dataclass(frozen=True)
class Job:
    name: str
    command: str
    timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES


@dataclass(frozen=True)
class Config:
    mqtt: Mqtt
    jobs: list[Job]


def _int(table: dict[str, Any], key: str, default: int, low: int, high: int, where: str) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f'{where}: "{key}" must be a whole number from {low} to {high}')
    return value


def _check_placeholders(where: str, *values: str) -> None:
    if any(p in v for v in values for p in _PLACEHOLDERS):
        raise ConfigError(f"{where}: still has placeholder values from config.example.toml")


def _mqtt(table: object) -> Mqtt:
    where = "[mqtt]"
    if not isinstance(table, dict):
        raise ConfigError(f"a {where} section is required")
    reject_unknown_keys(table, {"host", "port", "tls", "topic_prefix"}, where)
    host = require_str(table, "host", where)
    prefix = table.get("topic_prefix", "automation")
    if not isinstance(prefix, str):
        raise ConfigError(f'{where}: "topic_prefix" must be a quoted text value')
    _check_placeholders(where, host, prefix)
    if not _PREFIX.match(prefix):
        raise ConfigError(f'{where}: "topic_prefix" must be lowercase letters, digits, - or _')
    tls = table.get("tls", False)
    if not isinstance(tls, bool):
        raise ConfigError(f'{where}: "tls" must be true or false')
    return Mqtt(host, _int(table, "port", 1883, 1, 65535, where), tls, prefix)


def _job(table: dict[str, Any], n: int) -> Job:
    where = f"[[jobs]] #{n}"
    reject_unknown_keys(table, {"name", "command", "timeout_minutes"}, where)
    name = require_str(table, "name", where)
    command = require_str(table, "command", where)
    _check_placeholders(where, name, command)
    if not _JOB.match(name):
        raise ConfigError(f'{where}: "name" must be lowercase letters, digits, or -')
    if not _COMMAND.match(command):
        raise ConfigError(f'{where}: "command" must be a console script name, nothing else')
    minutes = _int(table, "timeout_minutes", DEFAULT_TIMEOUT_MINUTES, 1, 1440, where)
    return Job(name, command, minutes)


def parse_config(doc: dict[str, Any]) -> Config:
    """Validate an already-parsed TOML document."""
    reject_unknown_keys(doc, {"mqtt", "jobs"}, "config")
    mqtt = _mqtt(doc.get("mqtt"))
    jobs = [_job(t, n) for n, t in enumerate(require_tables(doc, "jobs"), start=1)]
    names = [j.name for j in jobs]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ConfigError(f"duplicate job name(s): {', '.join(dupes)}")
    return Config(mqtt, jobs)


def load_config(path: Path) -> Config:
    return parse_config(load_toml(path))
