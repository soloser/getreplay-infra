#!/usr/bin/env bash

# Idempotently provisions every topic used by the durable demo pipeline.
set -euo pipefail

KAFKA_BIN="${KAFKA_BIN:-/opt/kafka/bin}"
BOOTSTRAP_SERVER="${BOOTSTRAP_SERVER:-127.0.0.1:9092}"
TOPIC_PARTITIONS="${TOPIC_PARTITIONS:-6}"
TOPIC_REPLICATION_FACTOR="${TOPIC_REPLICATION_FACTOR:-1}"

[[ "$TOPIC_PARTITIONS" =~ ^[1-9][0-9]*$ ]] \
  || { echo "TOPIC_PARTITIONS must be a positive integer" >&2; exit 1; }
[[ "$TOPIC_REPLICATION_FACTOR" =~ ^[1-9][0-9]*$ ]] \
  || { echo "TOPIC_REPLICATION_FACTOR must be a positive integer" >&2; exit 1; }

topics=(
  getreplay.match-refresh.v1
  getreplay.demo-download.v1
  getreplay.demo-download-retry.v1
  getreplay.demo-parse.v1
  getreplay.demo-events.v1
  getreplay.demo-dlq.v1
)

ready=false
for attempt in {1..30}; do
  if "$KAFKA_BIN/kafka-broker-api-versions.sh" --bootstrap-server "$BOOTSTRAP_SERVER" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 2
done

if [ "$ready" != true ]; then
  echo "Kafka is not ready at $BOOTSTRAP_SERVER after 60 seconds" >&2
  exit 1
fi

for topic in "${topics[@]}"; do
  "$KAFKA_BIN/kafka-topics.sh" \
    --bootstrap-server "$BOOTSTRAP_SERVER" \
    --create \
    --if-not-exists \
    --topic "$topic" \
    --partitions "$TOPIC_PARTITIONS" \
    --replication-factor "$TOPIC_REPLICATION_FACTOR"
done

for topic in "${topics[@]}"; do
  description="$("$KAFKA_BIN/kafka-topics.sh" \
    --bootstrap-server "$BOOTSTRAP_SERVER" \
    --describe \
    --topic "$topic")"
  printf '%s\n' "$description"

  case "$description" in
    *"Topic: $topic"*"PartitionCount: $TOPIC_PARTITIONS"*) ;;
    *)
      echo "Topic $topic does not have exactly $TOPIC_PARTITIONS partitions" >&2
      exit 1
      ;;
  esac
  case "$description" in
    *"Topic: $topic"*"ReplicationFactor: $TOPIC_REPLICATION_FACTOR"*) ;;
    *)
      echo "Topic $topic does not have replication factor $TOPIC_REPLICATION_FACTOR" >&2
      exit 1
      ;;
  esac
done
