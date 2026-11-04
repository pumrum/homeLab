"""Pure MQTT layout: topic names and Home Assistant discovery messages.

    <prefix>/agent/status     online / offline (retained; offline is the will)
    <prefix>/<job>/run        send "run" to start the job (never retained)
    <prefix>/<job>/state      idle, running, PASS, or FAIL (retained)

Home Assistant builds a Run button and a state sensor per job from the
discovery messages, all under one "Automation robot" device.
"""

import json

from automation_agent.config import Job, Mqtt

RUN_PAYLOAD = "run"
ONLINE = "online"
OFFLINE = "offline"
IDLE = "idle"
RUNNING = "running"
PASS = "PASS"
FAIL = "FAIL"
DISCOVERY_PREFIX = "homeassistant"


def status_topic(mqtt: Mqtt) -> str:
    return f"{mqtt.topic_prefix}/agent/status"


def run_topic(mqtt: Mqtt, job: str) -> str:
    return f"{mqtt.topic_prefix}/{job}/run"


def state_topic(mqtt: Mqtt, job: str) -> str:
    return f"{mqtt.topic_prefix}/{job}/state"


def discovery_messages(mqtt: Mqtt, job: Job) -> dict[str, str]:
    """Retained discovery messages for one job, as {topic: JSON payload}."""
    base_id = f"{mqtt.topic_prefix}_{job.name}".replace("-", "_")
    common = {
        "availability_topic": status_topic(mqtt),
        "payload_available": ONLINE,
        "payload_not_available": OFFLINE,
        "device": {"identifiers": [f"{mqtt.topic_prefix}_robot"], "name": "Automation robot"},
    }
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
