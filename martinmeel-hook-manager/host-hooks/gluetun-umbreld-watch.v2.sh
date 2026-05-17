#!/usr/bin/env bash
set -euo pipefail

GLUETUN_NAME="martinmeel-gluetun_server_1"
APP_DATA_DIR="/home/umbrel/umbrel/app-data"
LOG_FILE="/home/umbrel/umbrel/app-data/martinmeel-hook-manager/logs/hook-manager.log"

START_COOLDOWN_SECONDS=30
STOP_LOCK_SECONDS=180
PER_APP_DELAY_SECONDS=1
HEALTH_TIMEOUT_SECONDS=120
HEALTH_POLL_SECONDS=2
FALLBACK_START_DELAY_SECONDS=5

STATE_DIR="/tmp/gluetun-umbreld-watch"
START_LAST_FILE="${STATE_DIR}/last_start_epoch"
STOP_LOCK_FILE="${STATE_DIR}/stop_lock_until_epoch"
STOPPED_APPS_FILE="${STATE_DIR}/stopped_apps"

mkdir -p "$STATE_DIR"

log() {
  mkdir -p "$(dirname "$LOG_FILE")"
  printf '%s gluetun-umbreld-watch: %s\n' "$(date -Iseconds)" "$*" | tee -a "$LOG_FILE"
}

now_epoch() { date +%s; }

discover_apps() {
  grep -Rl "network_mode:.*container:${GLUETUN_NAME}" \
    "${APP_DATA_DIR}"/*/docker-compose.yml 2>/dev/null \
    | sed "s#${APP_DATA_DIR}/##; s#/docker-compose.yml##" \
    | sort -u
}

discover_apps_start_order() {
  local apps
  mapfile -t apps < <(discover_apps)

  for app in "${apps[@]}"; do
    [[ "$app" == *prowlarr* ]] && echo "$app"
  done
  for app in "${apps[@]}"; do
    [[ "$app" == *qbittorrent* || "$app" == *sabnzbd* ]] && echo "$app"
  done
  for app in "${apps[@]}"; do
    [[ "$app" != *prowlarr* && "$app" != *qbittorrent* && "$app" != *sabnzbd* ]] && echo "$app"
  done
}

start_cooldown_ok() {
  local now last
  now="$(now_epoch)"
  last="0"
  [[ -f "$START_LAST_FILE" ]] && last="$(cat "$START_LAST_FILE" 2>/dev/null || echo 0)"

  if (( now - last < START_COOLDOWN_SECONDS )); then
    return 1
  fi

  echo "$now" > "$START_LAST_FILE"
  return 0
}

stop_lock_active() {
  local now until
  now="$(now_epoch)"
  until="0"
  [[ -f "$STOP_LOCK_FILE" ]] && until="$(cat "$STOP_LOCK_FILE" 2>/dev/null || echo 0)"
  (( now < until ))
}

set_stop_lock() {
  echo "$(( $(now_epoch) + STOP_LOCK_SECONDS ))" > "$STOP_LOCK_FILE"
}

gluetun_health_status() {
  local out
  if ! out="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$GLUETUN_NAME" 2>/dev/null)"; then
    echo "missing"
    return 0
  fi

  [[ -z "$out" ]] && echo "none" || echo "$out"
}

wait_for_gluetun_healthy() {
  local start now status
  start="$(now_epoch)"
  status="$(gluetun_health_status)"

  if [[ "$status" == "none" ]]; then
    log "Gluetun has no Docker healthcheck; sleeping ${FALLBACK_START_DELAY_SECONDS}s."
    sleep "$FALLBACK_START_DELAY_SECONDS"
    return 0
  fi

  log "Waiting for Gluetun health=healthy..."

  while true; do
    status="$(gluetun_health_status)"

    if [[ "$status" == "healthy" ]]; then
      log "Gluetun is healthy."
      return 0
    fi

    now="$(now_epoch)"
    if (( now - start >= HEALTH_TIMEOUT_SECONDS )); then
      log "Timed out waiting for Gluetun health. Last status: ${status}. Proceeding anyway."
      return 0
    fi

    sleep "$HEALTH_POLL_SECONDS"
  done
}

stop_apps() {
  local apps=()
  mapfile -t apps < <(discover_apps)

  if (( ${#apps[@]} == 0 )); then
    log "No apps found using network_mode container:${GLUETUN_NAME}"
    : > "$STOPPED_APPS_FILE"
    return 0
  fi

  printf '%s\n' "${apps[@]}" > "$STOPPED_APPS_FILE"

  log "Stopping discovered Gluetun-dependent apps: ${apps[*]}"

  for app in "${apps[@]}"; do
    log "STOP -> ${app}"
    umbreld client apps.stop.mutate --appId "$app" >/dev/null 2>&1 || true
    sleep "$PER_APP_DELAY_SECONDS"
  done

  log "Stop sequence completed."
}

start_apps() {
  local apps=()

  if [[ -s "$STOPPED_APPS_FILE" ]]; then
    mapfile -t apps < <(
      while read -r app; do
        discover_apps_start_order | grep -Fx "$app" || true
      done < "$STOPPED_APPS_FILE" | awk '!seen[$0]++'
    )
  else
    mapfile -t apps < <(discover_apps_start_order)
  fi

  if (( ${#apps[@]} == 0 )); then
    log "No apps to start."
    return 0
  fi

  log "Starting Gluetun-dependent apps: ${apps[*]}"

  for app in "${apps[@]}"; do
    log "START -> ${app}"
    umbreld client apps.start.mutate --appId "$app" >/dev/null 2>&1 || true
    sleep "$PER_APP_DELAY_SECONDS"
  done

  log "Start sequence completed."
}

log "Service started. Watching Docker events for ${GLUETUN_NAME}"
log "Auto-discovery path: ${APP_DATA_DIR}/*/docker-compose.yml"

docker events --format '{{.Action}}' \
  --filter type=container \
  --filter container="${GLUETUN_NAME}" \
  --filter event=stop \
  --filter event=die \
  --filter event=destroy \
  --filter event=start \
  --filter event=health_status \
  | while read -r action; do
      case "$action" in
        stop|die|destroy)
          log "Gluetun event: ${action}"

          if stop_lock_active; then
            log "Stop ignored because stop-lock is active."
            continue
          fi

          set_stop_lock
          stop_apps
          ;;

        start)
          log "Gluetun event: start"

          if ! start_cooldown_ok; then
            log "Start ignored because cooldown is active."
            continue
          fi

          wait_for_gluetun_healthy
          start_apps
          ;;

        "health_status: healthy")
          log "Gluetun event: health_status healthy"

          if ! start_cooldown_ok; then
            log "Healthy event ignored because cooldown is active."
            continue
          fi

          start_apps
          ;;
      esac
    done
