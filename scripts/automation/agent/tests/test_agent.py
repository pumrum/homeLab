import ssl
from types import SimpleNamespace

from automation_agent.config import Config, Job, Mqtt
from automation_agent.main import (
    UPDATE_WAIT_S,
    Agent,
    build_client,
    connect_until_up,
    report_update_result,
)
from automation_agent.runner import JobResult
from automation_common.notify import Template
from automation_common.secrets import Credential, SecretMasker

MQTT = Mqtt("broker.invalid")
JOB = Job("example", "example-job")
CONNECTED = SimpleNamespace(is_failure=False)
REFUSED = SimpleNamespace(is_failure=True)
RUN_TOPIC = "automation/example/run"
STATE_TOPIC = "automation/example/state"
UPDATE_TOPIC = "automation/agent/update"
VERSION_TOPIC = "automation/agent/version"
UPDATE_FAIL = {"title": "agent update", "message": "FAIL"}


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

    # Never the real helper: a test must not start a real scheduled task
    agent = Agent(Config(MQTT, [JOB]), client, SecretMasker(), run, notify,
                  start_update=lambda: True, version="abc1234", after=lambda d, a: None)
    return agent, client, ran, notes


def _updatable(started=True):
    """An agent that records helper starts and timers."""
    agent, client, ran, notes = _agent()
    starts = []
    timers = []

    def start_update():
        starts.append(True)
        return started

    agent.start_update = start_update
    agent.after = lambda delay, action: timers.append((delay, action))
    return agent, client, ran, notes, starts, timers


def _message(payload=b"run", topic=RUN_TOPIC, retain=False):
    return SimpleNamespace(topic=topic, payload=payload, retain=retain)


def _update_message(payload=b"update", retain=False):
    return _message(payload, UPDATE_TOPIC, retain)


def _states(client):
    return [p[1] for p in client.published if p[0] == STATE_TOPIC]


def _connect(agent, client, reason=CONNECTED):
    agent.on_connect(client, None, None, reason, None)


def test_the_config_sets_the_words_of_the_agents_own_pushes(tmp_path):
    (tmp_path / "update_result.txt").write_text("FAIL\n", encoding="ascii")
    notes = []
    words = {"update_fail": Template("Update", "Didn't work"),
             "update_pass": Template("Update", "Worked")}
    report_update_result(tmp_path, SecretMasker(), lambda n, m: notes.append(n) or True, words)
    assert notes == [{"title": "Update", "message": "Didn't work"}]


def test_connect_retries_with_backoff_and_logs_why(caplog):
    attempts = []
    waits = []

    class Flaky:
        def connect(self, host, port):
            attempts.append((host, port))
            if len(attempts) < 6:
                raise ssl.SSLCertVerificationError("certificate verify failed")

    caplog.set_level("ERROR")
    connect_until_up(Flaky(), MQTT, wait=waits.append)
    assert attempts[0] == ("broker.invalid", 8883)
    assert waits == [5, 10, 20, 40, 60]
    assert "certificate verify failed" in caplog.text


def test_build_client_with_tls_requires_a_verified_certificate():
    client = build_client(Config(MQTT, [JOB]), Credential("made-up", "made-up"))
    context = client._ssl_context
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_build_client_warns_when_tls_is_off(caplog):
    caplog.set_level("WARNING")
    plain = Mqtt("broker.invalid", 1883, False)
    client = build_client(Config(plain, [JOB]), Credential("made-up", "made-up"))
    assert client._ssl_context is None
    assert "unencrypted" in caplog.text


def test_connect_announces_everything():
    agent, client, _, _ = _agent()
    _connect(agent, client)
    assert client.subscribed == [UPDATE_TOPIC, RUN_TOPIC]
    topics = {p[0]: p for p in client.published}
    assert topics["automation/agent/status"] == ("automation/agent/status", "online", True)
    assert topics[STATE_TOPIC] == (STATE_TOPIC, "idle", True)
    assert topics[VERSION_TOPIC] == (VERSION_TOPIC, "abc1234", True)
    assert "homeassistant/button/automation_example_run/config" in topics
    assert "homeassistant/sensor/automation_example_state/config" in topics
    assert "homeassistant/button/automation_agent_update/config" in topics
    assert "homeassistant/sensor/automation_agent_version/config" in topics


def test_reconnect_keeps_the_last_result():
    agent, client, _, _ = _agent()
    _connect(agent, client)
    client.published.clear()
    _connect(agent, client)
    assert _states(client) == []
    assert client.subscribed == [UPDATE_TOPIC, RUN_TOPIC] * 2  # subscriptions are redone


def test_refused_connection_announces_nothing():
    agent, client, _, _ = _agent()
    _connect(agent, client, REFUSED)
    assert client.published == [] and client.subscribed == []


def test_failed_reconnect_attempt_is_logged(caplog):
    agent, client, _, _ = _agent()
    caplog.set_level("WARNING")
    agent.on_connect_fail(client, None)
    assert "reconnect attempt failed" in caplog.text


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


def test_update_message_starts_the_helper_and_waits_for_it():
    agent, client, _, notes, starts, timers = _updatable()
    agent.on_message(client, None, _update_message())
    assert starts == [True]
    assert [delay for delay, _ in timers] == [UPDATE_WAIT_S]
    assert notes == []


def test_only_the_exact_update_message_starts_the_helper():
    agent, client, _, _, starts, _ = _updatable()
    for payload in (b"", b"UPDATE", b"update now", b"run", b"git pull", b'{"branch": "x"}'):
        agent.on_message(client, None, _update_message(payload))
    agent.on_message(client, None, _message(b"update"))  # a job's topic never updates
    assert starts == []


def test_retained_update_message_is_ignored():
    agent, client, _, _, starts, _ = _updatable()
    agent.on_message(client, None, _update_message(retain=True))
    assert starts == []


def test_update_is_refused_while_a_job_waits_or_runs():
    agent, client, _, _, starts, _ = _updatable()
    agent.on_message(client, None, _message())  # waiting
    agent.on_message(client, None, _update_message())
    assert starts == []

    def run_and_press_update(job):
        agent.on_message(client, None, _update_message())  # running
        return JobResult(True, "exit code 0", True)

    agent.runner._run = run_and_press_update
    agent.runner.process_one(agent.runner._queue.get_nowait())
    assert starts == []
    agent.on_message(client, None, _update_message())  # free again
    assert starts == [True]


def test_second_update_press_is_ignored():
    agent, client, _, _, starts, _ = _updatable()
    agent.on_message(client, None, _update_message())
    agent.on_message(client, None, _update_message())
    assert starts == [True]


def test_run_requests_are_ignored_while_an_update_starts():
    agent, client, _, _, _, _ = _updatable()
    agent.on_message(client, None, _update_message())
    agent.on_message(client, None, _message())
    assert agent.runner._queue.empty()


def test_helper_that_cant_start_notifies_and_jobs_carry_on():
    agent, client, _, notes, _, timers = _updatable(started=False)
    agent.on_message(client, None, _update_message())
    assert notes == [UPDATE_FAIL]
    assert timers == []
    agent.on_message(client, None, _message())
    assert not agent.runner._queue.empty()


def test_helper_that_never_stops_the_agent_notifies_and_jobs_carry_on():
    agent, client, _, notes, _, timers = _updatable()
    agent.on_message(client, None, _update_message())
    timers[0][1]()  # the wait runs out
    assert notes == [UPDATE_FAIL]
    agent.on_message(client, None, _message())
    assert not agent.runner._queue.empty()


def test_restarted_agent_reports_the_update_once(tmp_path):
    (tmp_path / "update_result.txt").write_text("PASS\n", encoding="ascii")
    notes = []
    notify = lambda note, masker: notes.append(note) or True  # noqa: E731
    report_update_result(tmp_path, SecretMasker(), notify)
    report_update_result(tmp_path, SecretMasker(), notify)
    assert notes == [{"title": "agent update", "message": "PASS"}]


def test_normal_start_reports_nothing(tmp_path):
    notes = []
    report_update_result(tmp_path, SecretMasker(), lambda note, masker: notes.append(note))
    assert notes == []
