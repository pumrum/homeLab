from types import SimpleNamespace

from automation_agent.config import Config, Job, Mqtt
from automation_agent.main import Agent
from automation_agent.runner import JobResult
from automation_common.secrets import SecretMasker

MQTT = Mqtt("broker.invalid")
JOB = Job("example", "example-job")
CONNECTED = SimpleNamespace(is_failure=False)
REFUSED = SimpleNamespace(is_failure=True)
RUN_TOPIC = "automation/example/run"
STATE_TOPIC = "automation/example/state"


class FakeClient:
    def __init__(self):
        self.published = []
        self.subscribed = []

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)


def _agent(result=None):
    client = FakeClient()
    notes = []
    ran = []

    def run(job):
        ran.append(job.name)
        return result or JobResult(True, "exit code 0", True)

    def notify(note, masker):
        notes.append(note)
        return True

    agent = Agent(Config(MQTT, [JOB]), client, SecretMasker(), run, notify)
    return agent, client, ran, notes


def _message(payload=b"run", topic=RUN_TOPIC, retain=False):
    return SimpleNamespace(topic=topic, payload=payload, retain=retain)


def _states(client):
    return [p[1] for p in client.published if p[0] == STATE_TOPIC]


def _connect(agent, client, reason=CONNECTED):
    agent.on_connect(client, None, None, reason, None)


def test_connect_announces_everything():
    agent, client, _, _ = _agent()
    _connect(agent, client)
    assert client.subscribed == [RUN_TOPIC]
    topics = {p[0]: p for p in client.published}
    assert topics["automation/agent/status"] == ("automation/agent/status", "online", True)
    assert topics[STATE_TOPIC] == (STATE_TOPIC, "idle", True)
    assert "homeassistant/button/automation_example_run/config" in topics
    assert "homeassistant/sensor/automation_example_state/config" in topics


def test_reconnect_keeps_the_last_result():
    agent, client, _, _ = _agent()
    _connect(agent, client)
    client.published.clear()
    _connect(agent, client)
    assert _states(client) == []
    assert client.subscribed == [RUN_TOPIC, RUN_TOPIC]  # subscriptions are redone


def test_refused_connection_announces_nothing():
    agent, client, _, _ = _agent()
    _connect(agent, client, REFUSED)
    assert client.published == [] and client.subscribed == []


def test_run_message_queues_the_job():
    agent, client, ran, _ = _agent()
    agent.on_message(client, None, _message())
    agent.runner.process_one(agent.runner._queue.get_nowait())
    assert ran == ["example"]
    assert _states(client) == ["running", "PASS"]


def test_other_payloads_never_run_anything():
    agent, client, _, _ = _agent()
    for payload in (b"", b"RUN", b"run now", b"cmd /c del", b'{"command": "x"}'):
        agent.on_message(client, None, _message(payload))
    assert agent.runner._queue.empty()


def test_retained_run_message_is_ignored():
    agent, client, _, _ = _agent()
    agent.on_message(client, None, _message(retain=True))
    assert agent.runner._queue.empty()


def test_unknown_topic_is_ignored():
    agent, client, _, _ = _agent()
    agent.on_message(client, None, _message(topic="automation/other/run"))
    assert agent.runner._queue.empty()


def test_job_that_reported_itself_gets_no_second_notification():
    agent, client, _, notes = _agent(JobResult(False, "exit code 1", True))
    agent.on_message(client, None, _message())
    agent.runner.process_one(agent.runner._queue.get_nowait())
    assert _states(client) == ["running", "FAIL"]
    assert notes == []


def test_job_that_could_not_report_is_notified_by_the_agent():
    agent, client, _, notes = _agent(JobResult(False, "stopped after limit", False))
    agent.on_message(client, None, _message())
    agent.runner.process_one(agent.runner._queue.get_nowait())
    assert _states(client) == ["running", "FAIL"]
    assert notes == [{"title": "example", "message": "FAIL"}]
