from pathlib import Path

import pytest

from automation_agent.config import Job, Mqtt, load_config, parse_config
from automation_common.config import ConfigError

EXAMPLE = Path(__file__).parents[1] / "config.example.toml"
MQTT = {"host": "broker.invalid"}
JOB = {"name": "example", "command": "example-job"}


def test_defaults_are_tls_on_8883():
    cfg = parse_config({"mqtt": MQTT, "jobs": [JOB]})
    assert cfg.mqtt == Mqtt("broker.invalid", 8883, True, "automation", None)
    assert cfg.jobs == [Job("example", "example-job", 30)]


def test_turning_tls_off_must_be_explicit_and_changes_the_default_port():
    cfg = parse_config({"mqtt": MQTT | {"tls": False}, "jobs": [JOB]})
    assert (cfg.mqtt.tls, cfg.mqtt.port) == (False, 1883)


def test_all_values(tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_text("made up", encoding="utf-8")
    cfg = parse_config({
        "mqtt": MQTT | {"port": 18883, "ca_file": str(ca), "topic_prefix": "automation-test"},
        "jobs": [JOB | {"timeout_minutes": 5}],
    })
    assert cfg.mqtt == Mqtt("broker.invalid", 18883, True, "automation-test", str(ca))
    assert cfg.jobs[0].timeout_minutes == 5


def test_ca_file_must_exist(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        parse_config({"mqtt": MQTT | {"ca_file": str(tmp_path / "no.crt")}, "jobs": [JOB]})


def test_ca_file_needs_tls(tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_text("made up", encoding="utf-8")
    with pytest.raises(ConfigError, match="tls = true"):
        parse_config({"mqtt": MQTT | {"ca_file": str(ca), "tls": False}, "jobs": [JOB]})


def test_example_is_valid_toml_but_rejected_as_placeholder():
    with pytest.raises(ConfigError, match="placeholder"):
        load_config(EXAMPLE)


@pytest.mark.parametrize(
    "command",
    [r"C:\Windows\System32\cmd.exe", "../evil", "cmd /c del", "job;other", "job&other", "a b"],
)
def test_command_must_be_a_bare_script_name(command):
    with pytest.raises(ConfigError, match="console script name"):
        parse_config({"mqtt": MQTT, "jobs": [JOB | {"command": command}]})


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ({"jobs": [JOB]}, r"\[mqtt\]"),
        ({"mqtt": MQTT}, r"\[\[jobs\]\]"),
        ({"mqtt": MQTT | {"port": 0}, "jobs": [JOB]}, "whole number"),
        ({"mqtt": MQTT | {"port": True}, "jobs": [JOB]}, "whole number"),
        ({"mqtt": MQTT | {"tls": "yes"}, "jobs": [JOB]}, "true or false"),
        ({"mqtt": MQTT | {"topic_prefix": "a/b"}, "jobs": [JOB]}, "topic_prefix"),
        ({"mqtt": MQTT | {"topic_prefix": 5}, "jobs": [JOB]}, "topic_prefix"),
        ({"mqtt": MQTT | {"password": "x"}, "jobs": [JOB]}, "unknown"),
        ({"mqtt": MQTT, "jobs": [JOB | {"name": "Has Space"}]}, '"name"'),
        ({"mqtt": MQTT, "jobs": [JOB | {"timeout_minutes": 0}]}, "whole number"),
        ({"mqtt": MQTT, "jobs": [JOB, JOB]}, "duplicate"),
        ({"mqtt": MQTT, "jobs": [JOB], "extra": 1}, "unknown"),
    ],
)
def test_invalid_configs(doc, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(doc)
