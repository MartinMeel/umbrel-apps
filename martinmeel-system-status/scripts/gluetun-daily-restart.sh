#!/bin/bash

set -u

GLUETUN_CONTAINER="martinmeel-gluetun_server_1"
STATE_DIR="/home/umbrel/umbrel/home/status"
STATE_FILE="$STATE_DIR/gluetun-restart-state.json"
LOCK_DIR="/run/umbrel-gluetun-daily-restart.lock"

log() {
  echo "gluetun-daily-restart: $*"
}

json_escape() {
  local value="${1//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  printf '%s' "$value"
}

write_state() {
  local phase="$1"
  local message="$2"
  local timestamp
  local health="unknown"
  local running="false"
  local containers_json=""
  local name

  mkdir -p "$STATE_DIR"
  timestamp="$(date -Iseconds)"

  if docker inspect "$GLUETUN_CONTAINER" >/dev/null 2>&1; then
    running="$(docker inspect -f '{{if .State.Running}}true{{else}}false{{end}}' "$GLUETUN_CONTAINER" 2>/dev/null || echo false)"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$GLUETUN_CONTAINER" 2>/dev/null || echo unknown)"
  fi

  for name in "${STOPPED_CONTAINERS[@]:-}"; do
    if [ -n "$name" ]; then
      if [ -n "$containers_json" ]; then
        containers_json="$containers_json,"
      fi
      containers_json="$containers_json\"$(json_escape "$name")\""
    fi
  done

  cat >"$STATE_FILE" <<EOF
{
  "timestamp": "$(json_escape "$timestamp")",
  "phase": "$(json_escape "$phase")",
  "message": "$(json_escape "$message")",
  "gluetunContainer": "$(json_escape "$GLUETUN_CONTAINER")",
  "gluetunRunning": $running,
  "gluetunHealth": "$(json_escape "$health")",
  "stoppedContainers": [$containers_json]
}
EOF
}

cleanup() {
  rm -rf "$LOCK_DIR"
}

discover_dependent_containers() {
  local name
  local network_mode
  local -a found=()

  while IFS= read -r name; do
    [ -n "$name" ] || continue
    network_mode="$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$name" 2>/dev/null || true)"
    if [ "$network_mode" = "container:$GLUETUN_CONTAINER" ]; then
      found+=("$name")
    fi
  done < <(docker ps --format '{{.Names}}')

  printf '%s\n' "${found[@]}"
}

wait_for_gluetun() {
  local attempt=0
  local max_attempts=40
  local running
  local health

  while [ "$attempt" -lt "$max_attempts" ]; do
    running="$(docker inspect -f '{{if .State.Running}}true{{else}}false{{end}}' "$GLUETUN_CONTAINER" 2>/dev/null || echo false)"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$GLUETUN_CONTAINER" 2>/dev/null || echo unknown)"

    if [ "$running" = "true" ] && { [ "$health" = "healthy" ] || [ "$health" = "none" ]; }; then
      return 0
    fi

    sleep 3
    attempt=$((attempt + 1))
  done

  return 1
}

main() {
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "another run is already in progress"
    exit 0
  fi
  trap cleanup EXIT

  if ! command -v docker >/dev/null 2>&1; then
    log "docker is not available"
    exit 0
  fi

  if ! docker inspect "$GLUETUN_CONTAINER" >/dev/null 2>&1; then
    STOPPED_CONTAINERS=()
    write_state "error" "gluetun container $GLUETUN_CONTAINER was not found"
    exit 0
  fi

  mapfile -t STOPPED_CONTAINERS < <(discover_dependent_containers)
  write_state "stopping" "stopping containers connected through $GLUETUN_CONTAINER"

  for container in "${STOPPED_CONTAINERS[@]}"; do
    docker stop "$container" >/dev/null
  done

  write_state "restarting-gluetun" "restarting $GLUETUN_CONTAINER"
  docker restart "$GLUETUN_CONTAINER" >/dev/null

  if wait_for_gluetun; then
    write_state "starting-dependents" "starting dependent containers"
  else
    write_state "warning" "gluetun did not become healthy in time, attempting dependent container start anyway"
  fi

  for container in "${STOPPED_CONTAINERS[@]}"; do
    docker start "$container" >/dev/null
    sleep 3
  done

  write_state "complete" "daily gluetun restart finished"
}

main "$@"
