import json

from automation_agent.config import Job, Mqtt
from automation_agent.topics import (
    agent_discovery_messages,
    discovery_messages,
    run_topic,
    state_topic,
    status_topic,
    update_topic,
    version_topic,
)

MQTT = Mqtt("broker.invalid", topic_prefix="automation")
JOB = Job("my-job", "my-job-command")


def test_topic_names():
    assert status_topic(MQTT) == "automation/agent/status"
    assert run_topic(MQTT, "my-job") == "automation/my-job/run"
    assert state_topic(MQTT, "my-job") == "automation/my-job/state"
    assert update_topic(MQTT) == "automation/agent/update"
    assert version_topic(MQTT) == "automation/agent/version"


def test_agent_discovery_gives_an_update_button_and_a_version_sensor():
    messages = agent_discovery_messages(MQTT)
    button = json.loads(messages["homeassistant/button/automation_agent_update/config"])
    sensor = json.loads(messages["homeassistant/sensor/automation_agent_version/config"])
    assert button["command_topic"] == "automation/agent/update"
    assert button["payload_press"] == "update"
    assert sensor["state_topic"] == "automation/agent/version"
    for entity in (button, sensor):
        assert entity["availability_topic"] == "automation/agent/status"
        assert entity["device"]["identifiers"] == ["automation_robot"]  # same device as the jobs
    assert "broker.invalid" not in json.dumps(messages)


def test_discovery_gives_a_button_and_a_sensor():
    messages = discovery_messages(MQTT, JOB)
    button = json.loads(messages["homeassistant/button/automation_my_job_run/config"])
    sensor = json.loads(messages["homeassistant/sensor/automation_my_job_state/config"])
    assert button["command_topic"] == "automation/my-job/run"
    assert button["payload_press"] == "run"
    assert sensor["state_topic"] == "automation/my-job/state"
    for entity in (button, sensor):
        assert entity["availability_topic"] == "automation/agent/status"
        assert entity["device"]["name"] == "Automation robot"
    assert button["unique_id"] != sensor["unique_id"]


def test_discovery_never_includes_the_command_or_host():
    text = json.dumps(discovery_messages(MQTT, JOB))
    assert "my-job-command" not in text
    assert "broker.invalid" not in text


def test_a_test_prefix_keeps_everything_separate():
    test = Mqtt("broker.invalid", topic_prefix="automation-test")
    assert run_topic(test, "my-job") == "automation-test/my-job/run"
    assert not set(discovery_messages(test, JOB)) & set(discovery_messages(MQTT, JOB))
    assert update_topic(test) == "automation-test/agent/update"
    assert not set(agent_discovery_messages(test)) & set(agent_discovery_messages(MQTT))
