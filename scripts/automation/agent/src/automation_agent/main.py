"""automation-agent: stay connected to MQTT and run configured jobs on request.

Runs for as long as the robot is signed in. Only jobs named in its private
config can run, and the message's content is never used beyond the word "run".
Each job keeps its own run folder and notification; a job that hangs or can't
start is reported here, since it can't report itself.
"""

import logging
import logging.handlers
import ssl
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt_client

from automation_agent import PROCESS
from automation_agent.config import Config, Job, load_config
from automation_agent.runner import JobResult, Runner, run_job
from automation_agent.topics import (
    FAIL,
    IDLE,
    OFFLINE,
    ONLINE,
    PASS,
    RUN_PAYLOAD,
    RUNNING,
    discovery_messages,
    run_topic,
    state_topic,
    status_topic,
)
from automation_common.config import ConfigError, process_dir
from automation_common.notify import send_notification
from automation_common.run import MaskingFormatter
from automation_common.secrets import Credential, SecretMasker, get_credential

log = logging.getLogger(__name__)

MQTT_SECRET = "automation/mqtt"
LOG_BYTES = 1_000_000
LOG_BACKUPS = 5
QOS = 1


class Agent:
    """Connects Home Assistant's messages to the job runner."""

    def __init__(
        self,
        cfg: Config,
        client: Any,
        masker: SecretMasker,
        run: Callable[[Job], JobResult],
        notify: Callable[[dict[str, str], SecretMasker], bool] = send_notification,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.masker = masker
        self.notify = notify
        self.connected_before = False
        self.jobs ={run_topic(cfg.mqtt, j.name): j for j in cfg.jobs}
        self.runner = Runner(run, self._on_start, self._on_finish)

    def on_connect(self, client: Any, userdata: Any, flags: Any, reason: Any, props: Any) -> None:
        if reason.is_failure:
            log.error("MQTT connection refused: %s", reason)
            return
        log.info("connected to MQTT; %d job(s)", len(self.cfg.jobs))
        for job in self.cfg.jobs:
            for topic, payload in discovery_messages(self.cfg.mqtt, job).items():
                client.publish(topic, payload, qos=QOS, retain=True)
            client.subscribe(run_topic(self.cfg.mqtt, job.name), qos=QOS)
            # First connect only: clears a stale "running" if the agent was killed mid-job.
            # A later reconnect keeps the last PASS or FAIL.
            if not self.connected_before and not self.runner.is_active(job.name):
                self._publish_state(job, IDLE)
        self.connected_before = True
        client.publish(status_topic(self.cfg.mqtt), ONLINE, qos=QOS, retain=True)

    def on_disconnect(
        self, client: Any, userdata: Any, flags: Any, reason: Any, props: Any
    ) -> None:
        log.warning("disconnected from MQTT (%s); reconnecting", reason)

    def on_message(self, client: Any, userdata: Any, message: Any) -> None:
        job = self.jobs.get(message.topic)
        if job is None:
            return
        if message.retain:
            log.warning("ignored a retained run message for %s: it may be old", job.name)
            return
        if message.payload != RUN_PAYLOAD.encode():
            log.warning("ignored an unexpected message for %s", job.name)
            return
        if self.runner.request(job):
            log.info("%s requested", job.name)
        else:
            log.info("%s is already running or waiting; ignored", job.name)

    def _publish_state(self, job: Job, state: str) -> None:
        self.client.publish(state_topic(self.cfg.mqtt, job.name), state, qos=QOS, retain=True)

    def _on_start(self, job: Job) -> None:
        log.info("%s started", job.name)
        self._publish_state(job, RUNNING)

    def _on_finish(self, job: Job, result: JobResult) -> None:
        log.log(logging.INFO if result.ok else logging.ERROR, "%s finished: %s (%s)",
                job.name, PASS if result.ok else FAIL, result.detail)
        self._publish_state(job, PASS if result.ok else FAIL)
        if not result.reported_by_job:
            self.notify({"title": job.name, "message": FAIL}, self.masker)


def setup_logging(data_dir: Path, masker: SecretMasker) -> None:
    """A rotating log, since this process runs for days rather than one run."""
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.handlers.RotatingFileHandler(data_dir / "logs" / "agent.log", maxBytes=LOG_BYTES,
                                             backupCount=LOG_BACKUPS, encoding="utf-8"),
        logging.StreamHandler(),
    ]
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in handlers:
        handler.setFormatter(MaskingFormatter(masker))
        root.addHandler(handler)


def build_client(cfg: Config, cred: Credential) -> mqtt_client.Client:
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2,
                                client_id=f"{cfg.mqtt.topic_prefix}-agent")
    client.username_pw_set(cred.username, cred.password)
    client.will_set(status_topic(cfg.mqtt), OFFLINE, qos=QOS, retain=True)
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    if cfg.mqtt.tls:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    return client


def main() -> int:
    data_dir = process_dir(PROCESS)
    masker = SecretMasker()
    setup_logging(data_dir, masker)
    try:
        cfg = load_config(data_dir / "config.toml")
    except ConfigError as e:
        log.error("%s", e)
        return 1
    masker.add(cfg.mqtt.host)
    cred = get_credential(MQTT_SECRET, masker)
    if cred is None:
        log.error("no MQTT login for this Windows user: add the Credential Manager entry %s",
                  MQTT_SECRET)
        return 1

    scripts_dir = Path(sys.executable).parent
    workspace = scripts_dir.parents[1]
    client = build_client(cfg, cred)
    agent = Agent(cfg, client, masker, lambda job: run_job(job, scripts_dir, workspace))
    client.on_connect = agent.on_connect
    client.on_disconnect = agent.on_disconnect
    client.on_message = agent.on_message
    agent.runner.start()
    client.connect_async(cfg.mqtt.host, cfg.mqtt.port)
    log.info("agent started")
    try:
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        client.publish(status_topic(cfg.mqtt), OFFLINE, qos=QOS, retain=True).wait_for_publish(5)
        client.disconnect()
    return 0
