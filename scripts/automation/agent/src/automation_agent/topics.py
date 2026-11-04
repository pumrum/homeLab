"""Pure MQTT layout: topic names and Home Assistant discovery messages.

    <prefix>/agent/status     online / offline (retained; offline is the will)
    <prefix>/agent/update     send "update" to update the code (never retained)
    <prefix>/agent/version    the running commit (retained)
    <prefix>/<job>/run        send "run" to start the job (never retained)
    <prefix>/<job>/state      idle, running, PASS, or FAIL (retained)

Home Assistant builds a Run button and a state sensor per job, plus one Update
button and one version sensor, from the discovery messages, all under one
"Automation robot" device.
"""

import json
from typing import Any

from automation_agent.config import Job, Mqtt

RUN_PAYLOAD = "run"
UPDATE_PAYLOAD = "update"
ONLINE = "online"
OFFLINE = "offline"
IDLE = "idle"
RUNNING = "running"
PASS = "PASS"
FAIL = "FAIL"
DISCOVERY_PREFIX = "homeassistant"


def status_topic(mqtt: Mqtt) -> str:
    return f"{mqtt.topic_prefix}/agent/status"


def update_topic(mqtt: Mqtt) -> str:
    return f"{mqtt.topic_prefix}/agent/update"


def version_topic(mqtt: Mqtt) -> str:
    return f"{mqtt.topic_prefix}/agent/version"


def run_topic(mqtt: Mqtt, job: str) -> str:
    return f"{mqtt.topic_prefix}/{job}/run"


def state_topic(mqtt: Mqtt, job: str) -> str:
    return f"{mqtt.topic_prefix}/{job}/state"


def _common(mqtt: Mqtt) -> dict[str, Any]:
    return {
        "availability_topic": status_topic(mqtt),
        "payload_available": ONLINE,
        "payload_not_available": OFFLINE,
        "device": {"identifiers": [f"{mqtt.topic_prefix}_robot"], "name": "Automation robot"},
    }


def agent_discovery_messages(mqtt: Mqtt) -> dict[str, str]:
    """Retained discovery messages for the agent itself, as {topic: JSON payload}."""
    base_id = f"{mqtt.topic_prefix}_agent".replace("-", "_")
    common = _common(mqtt)
    button = common | {
        "name": "Update",
        "unique_id": f"{base_id}_update",
        "command_topic": update_topic(mqtt),
        "payload_press": UPDATE_PAYLOAD,
        "device_class": "update",
        "entity_category": "config",
    }
    sensor = common | {
        "name": "Version",
        "unique_id": f"{base_id}_version",
        "state_topic": version_topic(mqtt),
        "entity_category": "diagnostic",
    }
    return {
        f"{DISCOVERY_PREFIX}/button/{base_id}_update/config": json.dumps(button),
        f"{DISCOVERY_PREFIX}/sensor/{base_id}_version/config": json.dumps(sensor),
    }


def discovery_messages(mqtt: Mqtt, job: Job) -> dict[str, str]:
    """Retained discovery messages for one job, as {topic: JSON payload}."""
    base_id = f"{mqtt.topic_prefix}_{job.name}".replace("-", "_")
    common = _common(mqtt)
    button = common | {
        "name": f"{job.name} run",
        "unique_id": f"{base_id}_run",
        "command_topic": run_topic(mqtt, job.name),
        "payload_press": RUN_PAYLOAD,
    }
    sensor = common | {
        "name": f"{job.name} state",
        "unique_id": f"{base_id}_state",
        "state_topic": state_topic(mqtt, job.name),
    }
    return {
        f"{DISCOVERY_PREFIX}/button/{base_id}_run/config": json.dumps(button),
        f"{DISCOVERY_PREFIX}/sensor/{base_id}_state/config": json.dumps(sensor),
    }
