#!/bin/bash

set -u

GLUETUN_CONTAINER="martinmeel-gluetun_server_1"
STATE_DIR="/home/umbrel/umbrel/app-data/status"
STATE_FILE="$STATE_DIR/gluetun-restart-state.json"
LOCK_DIR="/run/umbrel-gluetun-daily-restart.lock"
APP_LOG_FILE="/home/umbrel/umbrel/app-data/martinmeel-hook-manager/logs/hook-manager.log"

log() {
  mkdir -p "$(dirname "$APP_LOG_FILE")"
  printf '%s gluetun-daily-restart: %s\n' "$(date -Iseconds)" "$*" | tee -a "$APP_LOG_FILE"
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
  local apps_json=""
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

  for name in "${STOPPED_APPS[@]:-}"; do
    if [ -n "$name" ]; then
      if [ -n "$apps_json" ]; then
        apps_json="$apps_json,"
      fi
      apps_json="$apps_json\"$(json_escape "$name")\""
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
  "stoppedContainers": [$containers_json],
  "stoppedApps": [$apps_json]
}
EOF
}

cleanup() {
  rm -rf "$LOCK_DIR"
}

discover_dependent_containers() {
  local name
  local network_mode
  local gluetun_id
  local -a found=()

  gluetun_id="$(docker inspect -f '{{.Id}}' "$GLUETUN_CONTAINER" 2>/dev/null || true)"

  while IFS= read -r name; do
    [ -n "$name" ] || continue
    network_mode="$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$name" 2>/dev/null || true)"
    if [ "$network_mode" = "container:$GLUETUN_CONTAINER" ] || { [ -n "$gluetun_id" ] && [ "$network_mode" = "container:$gluetun_id" ]; }; then
      found+=("$name")
    fi
  done < <(docker ps --format '{{.Names}}')

  printf '%s\n' "${found[@]}"
}

app_id_for_container() {
  local container="$1"
  docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$container" 2>/dev/null || true
}

gluetun_app_id() {
  app_id_for_container "$GLUETUN_CONTAINER"
}

discover_dependent_apps() {
  local container
  local app_id
  local -A seen=()
  local -a found=()

  for container in "${STOPPED_CONTAINERS[@]:-}"; do
    [ -n "$container" ] || continue
    app_id="$(app_id_for_container "$container")"
    [ -n "$app_id" ] || continue
    if [ -z "${seen[$app_id]:-}" ]; then
      seen["$app_id"]=1
      found+=("$app_id")
    fi
  done

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

  if ! command -v umbreld >/dev/null 2>&1; then
    log "umbreld is not available"
    exit 0
  fi

  if ! docker inspect "$GLUETUN_CONTAINER" >/dev/null 2>&1; then
    STOPPED_CONTAINERS=()
    STOPPED_APPS=()
    write_state "error" "gluetun container $GLUETUN_CONTAINER was not found"
    log "gluetun container $GLUETUN_CONTAINER was not found"
    exit 0
  fi

  mapfile -t STOPPED_CONTAINERS < <(discover_dependent_containers)
  mapfile -t STOPPED_APPS < <(discover_dependent_apps)
  GLUETUN_APP_ID="$(gluetun_app_id)"
  write_state "stopping" "stopping apps connected through $GLUETUN_CONTAINER"

  for app_id in "${STOPPED_APPS[@]}"; do
    log "stopping app $app_id"
    umbreld client apps.stop.mutate --appId "$app_id" >/dev/null
  done

  write_state "restarting-gluetun" "restarting $GLUETUN_CONTAINER via Umbrel app lifecycle"
  if [ -n "$GLUETUN_APP_ID" ]; then
    log "restarting gluetun app $GLUETUN_APP_ID"
    umbreld client apps.restart.mutate --appId "$GLUETUN_APP_ID" >/dev/null
  else
    log "could not resolve gluetun app id, falling back to docker restart for $GLUETUN_CONTAINER"
    docker restart "$GLUETUN_CONTAINER" >/dev/null
  fi

  if wait_for_gluetun; then
    write_state "starting-dependents" "starting dependent apps"
  else
    write_state "warning" "gluetun did not become healthy in time, attempting dependent app restart anyway"
  fi

  for app_id in "${STOPPED_APPS[@]}"; do
    log "restarting app $app_id"
    umbreld client apps.restart.mutate --appId "$app_id" >/dev/null
    sleep 3
  done

  write_state "complete" "daily gluetun restart finished"
  log "completed daily restart"
}

main "$@"
