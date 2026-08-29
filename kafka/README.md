# Kafka on the production host

GetReplay uses one Apache Kafka 4.3.1 broker in KRaft mode for the durable demo-processing
pipeline. This directory and the two units in `../systemd/` are a reviewed installation
preview; committing them does not install or start anything on production.

No production action was performed while preparing these files. Every command below is an
operator-reviewed, privileged production step.

## Durability boundary

The broker listens only on `127.0.0.1:9092`, persists its log in `/var/lib/kafka`, disables
automatic topic creation, and keeps records for at least seven days. This preserves queued
work across application deploys, process crashes, and broker restarts.

It is **not high availability**. One broker on one host cannot survive loss of that host or
disk. If host-level continuity is required, run at least three brokers on independent hosts
and raise topic/internal replication factors before switching clients.

## Reviewed one-time installation

These commands require a human with production root access. Before running them, install a
signature/checksum-verified Apache Kafka 4.3.1 distribution and a supported Java runtime,
then make `/opt/kafka` a root-owned symlink to that exact distribution. Do not use an
unversioned download or an unverified archive.

```bash
getent group kafka >/dev/null || sudo groupadd --system kafka
id kafka >/dev/null 2>&1 || sudo useradd \
  --system --gid kafka --home-dir /var/lib/kafka --shell /usr/sbin/nologin kafka
sudo install -d -o root -g root -m 0755 /etc/kafka /usr/local/libexec/getreplay-kafka
sudo install -d -o kafka -g kafka -m 0750 /var/lib/kafka /var/log/kafka
sudo install -o root -g root -m 0644 \
  /home/solo/infra/kafka/server.properties /etc/kafka/getreplay-server.properties
sudo install -o root -g root -m 0755 \
  /home/solo/infra/kafka/create-topics.sh /usr/local/libexec/getreplay-kafka/create-topics.sh
sudo install -o root -g root -m 0644 \
  /home/solo/infra/systemd/kafka.service \
  /home/solo/infra/systemd/kafka-topics.service \
  /etc/systemd/system/
```

Format the storage exactly once. **Never format a directory containing
`/var/lib/kafka/meta.properties`**: a new cluster ID makes the existing log unusable.

```bash
sudo test ! -e /var/lib/kafka/meta.properties
KAFKA_CLUSTER_ID="$(sudo -u kafka /opt/kafka/bin/kafka-storage.sh random-uuid)"
sudo -u kafka /opt/kafka/bin/kafka-storage.sh format \
  --cluster-id "$KAFKA_CLUSTER_ID" \
  --config /etc/kafka/getreplay-server.properties
sudo systemctl daemon-reload
sudo systemctl enable --now kafka.service
sudo systemctl enable --now kafka-topics.service
```

Create the Match Updater artifact directories before installing the updated Go unit. Uploads and
network downloads share the first directory; parsed replays are published into the second one.

```bash
sudo install -d -o www-data -g www-data -m 0750 \
  /var/www/getreplay-go/downloads \
  /var/www/getreplay-storage/replays
```

Whenever `create-topics.sh`, its partition setting, or the topic list changes, reinstall the
script and explicitly rerun the one-shot unit:

```bash
sudo install -o root -g root -m 0755 \
  /home/solo/infra/kafka/create-topics.sh /usr/local/libexec/getreplay-kafka/create-topics.sh
sudo systemctl restart kafka-topics.service
sudo systemctl status kafka-topics.service --no-pager
```

Provisioning now fails if any existing topic has a partition count or replication factor other
than the reviewed values. It does not silently accept drift. Kafka cannot reduce an existing
topic's partition count; resolve such a mismatch with a separately reviewed migration.

## Verification

```bash
sudo systemctl is-active kafka.service kafka-topics.service
/opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server 127.0.0.1:9092
/opt/kafka/bin/kafka-topics.sh --bootstrap-server 127.0.0.1:9092 --list
sudo journalctl -u kafka.service -u kafka-topics.service -n 100 --no-pager
```

Expected topics:

- `getreplay.match-refresh.v1`
- `getreplay.demo-download.v1`
- `getreplay.demo-download-retry.v1`
- `getreplay.demo-parse.v1`
- `getreplay.demo-events.v1`
- `getreplay.demo-dlq.v1`

All six topics start with six partitions and replication factor one. Partition count can be
increased but not reduced. Producers should use idempotence and `acks=all`; consumers must
commit only after successful, idempotent processing. Monitor broker disk usage, consumer lag,
offline partitions, and failed/DLQ records.

Production Go services connect with `DEMO_QUEUE_BROKER_SERVERS=127.0.0.1:9092`. The current
legacy Sarama client must advertise protocol version `2.6.0` even though the broker itself is
4.3.1; upgrade the client before raising `DEMO_QUEUE_VERSION`.

The application drain, ownerless `is_user_match` audit, release pinning, and rollback
sequence are mandatory parts of the first rollout; follow the production cutover runbook in
[`../go/README.md`](../go/README.md#production-queue-cutover-reviewed-runbook-not-executed).
