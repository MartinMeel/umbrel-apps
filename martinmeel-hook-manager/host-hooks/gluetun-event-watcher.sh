#!/bin/bash

set -u

GLUETUN_CONTAINER="martinmeel-gluetun_server_1"
APP_LOG_FILE="/home/umbrel/umbrel/app-data/martinmeel-hook-manager/logs/hook-manager.log"
STATE_DIR="/run/umbrel-gluetun-event-watcher"
STOPPED_APPS_FILE="$STATE_DIR/stopped-apps"
LOCK_DIR="/run/umbrel-gluetun-daily-restart.lock"

log() {
  mkdir -p "$(dirname "$APP_LOG_FILE")"
  printf '%s gluetun-event-watcher: %s\n' "$(date -Iseconds)" "$*" | tee -a "$APP_LOG_FILE"
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

discover_dependent_apps() {
  local container
  local app_id
  local -A seen=()

  : >"$STOPPED_APPS_FILE"
  while IFS= read -r container; do
    [ -n "$container" ] || continue
    app_id="$(app_id_for_container "$container")"
    [ -n "$app_id" ] || continue
    if [ -z "${seen[$app_id]:-}" ]; then
      seen["$app_id"]=1
      printf '%s\n' "$app_id" >>"$STOPPED_APPS_FILE"
    fi
  done < <(discover_dependent_containers)
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

stop_dependents() {
  mkdir -p "$STATE_DIR"
  discover_dependent_apps

  if [ ! -s "$STOPPED_APPS_FILE" ]; then
    log "no dependent apps found to stop for manual Gluetun restart"
    return 0
  fi

  while IFS= read -r app_id; do
    [ -n "$app_id" ] || continue
    log "stopping app $app_id because Gluetun is restarting manually"
    umbreld client apps.stop.mutate --appId "$app_id" >/dev/null
  done <"$STOPPED_APPS_FILE"
}

restart_dependents() {
  if [ ! -s "$STOPPED_APPS_FILE" ]; then
    return 0
  fi

  if wait_for_gluetun; then
    while IFS= read -r app_id; do
      [ -n "$app_id" ] || continue
      log "restarting app $app_id after manual Gluetun restart"
      umbreld client apps.restart.mutate --appId "$app_id" >/dev/null
      sleep 3
    done <"$STOPPED_APPS_FILE"
    rm -f "$STOPPED_APPS_FILE"
    log "manual Gluetun restart handling completed"
  else
    log "Gluetun did not become healthy in time after manual restart"
  fi
}

main() {
  mkdir -p "$STATE_DIR"
  log "watching Docker events for manual Gluetun restarts"

  docker events \
    --filter type=container \
    --filter container="$GLUETUN_CONTAINER" \
    --filter event=die \
    --filter event=start \
    --format '{{.Action}}' | while IFS= read -r action; do
      [ -n "$action" ] || continue

      if [ -d "$LOCK_DIR" ]; then
        log "ignoring $action event because scheduled restart is already in progress"
        continue
      fi

      case "$action" in
        die)
          stop_dependents
          ;;
        start)
          restart_dependents
          ;;
      esac
    done
}

main "$@"
