#!/usr/bin/env bash
set -euo pipefail

GLUETUN_NAME="martinmeel-gluetun_server_1"

STOP_APPS=(
  "martinmeel-sonarr"
  "martinmeel-radarr"
  "martinmeel-prowlarr"
  "martinmeel-qbittorrent"
  "martinmeel-sabnzbd"
  "martinmeel-profilarr"
  "martinmeel-huntarr"
  "martinmeel-tautulli"
  "martinmeel-seerr"
  "martinmeel-spotweb"
)

START_APPS=(
  "martinmeel-prowlarr"
  "martinmeel-qbittorrent"
  "martinmeel-sabnzbd"
  "martinmeel-sonarr"
  "martinmeel-radarr"
  "martinmeel-profilarr"
  "martinmeel-huntarr"
  "martinmeel-tautulli"
  "martinmeel-seerr"
  "martinmeel-spotweb"
)

START_COOLDOWN_SECONDS=30
STOP_LOCK_SECONDS=180
PER_APP_DELAY_SECONDS=1
HEALTH_TIMEOUT_SECONDS=120
HEALTH_POLL_SECONDS=2
FALLBACK_START_DELAY_SECONDS=5

STATE_DIR="/tmp/gluetun-umbreld-watch"
START_LAST_FILE="${STATE_DIR}/last_start_epoch"
STOP_LOCK_FILE="${STATE_DIR}/stop_lock_until_epoch"
mkdir -p "$STATE_DIR"

APP_LOG_FILE="/home/umbrel/umbrel/app-data/martinmeel-hook-manager/logs/hook-manager.log"

log() {
  mkdir -p "$(dirname "$APP_LOG_FILE")"
  printf '%s gluetun-umbreld-watch: %s\n' "$(date -Iseconds)" "$*" | tee -a "$APP_LOG_FILE"
}

now_epoch() { date +%s; }

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
  local now until
  now="$(now_epoch)"
  until=$(( now + STOP_LOCK_SECONDS ))
  echo "$until" > "$STOP_LOCK_FILE"
}

stop_apps() {
  log "Stopping apps (order): ${STOP_APPS[*]}"
  for app in "${STOP_APPS[@]}"; do
    log "STOP -> ${app}"
    umbreld client apps.stop.mutate --appId "$app" >/dev/null 2>&1 || true
    sleep "$PER_APP_DELAY_SECONDS"
  done
  log "Stop sequence completed."
}

start_apps() {
  log "Starting apps (order): ${START_APPS[*]}"
  for app in "${START_APPS[@]}"; do
    log "START -> ${app}"
    umbreld client apps.start.mutate --appId "$app" >/dev/null 2>&1 || true
    sleep "$PER_APP_DELAY_SECONDS"
  done
  log "Start sequence completed."
}

gluetun_health_status() {
  local out
  if ! out="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$GLUETUN_NAME" 2>/dev/null)"; then
    echo "missing"
    return 0
  fi
  if [[ -z "$out" ]]; then
    echo "none"
  else
    echo "$out"
  fi
}

wait_for_gluetun_healthy() {
  local start now status
  start="$(now_epoch)"

  status="$(gluetun_health_status)"
  if [[ "$status" == "none" ]]; then
    log "Gluetun has no Docker healthcheck; sleeping ${FALLBACK_START_DELAY_SECONDS}s (fallback)."
    sleep "$FALLBACK_START_DELAY_SECONDS"
    return 0
  fi

  log "Waiting for Gluetun Docker health=healthy (timeout ${HEALTH_TIMEOUT_SECONDS}s)..."
  while true; do
    status="$(gluetun_health_status)"

    if [[ "$status" == "healthy" ]]; then
      log "Gluetun is healthy."
      return 0
    fi

    now="$(now_epoch)"
    if (( now - start >= HEALTH_TIMEOUT_SECONDS )); then
      log "Timed out waiting for Gluetun health (last status: ${status}). Proceeding anyway."
      return 0
    fi

    sleep "$HEALTH_POLL_SECONDS"
  done
}

log "Service started. Watching Docker events for Gluetun: ${GLUETUN_NAME}"
log "Start cooldown: ${START_COOLDOWN_SECONDS}s | Stop lock: ${STOP_LOCK_SECONDS}s | Per-app delay: ${PER_APP_DELAY_SECONDS}s"
log "Health wait: timeout ${HEALTH_TIMEOUT_SECONDS}s, poll ${HEALTH_POLL_SECONDS}s, fallback sleep ${FALLBACK_START_DELAY_SECONDS}s"

docker events --format '{{.Action}}' \
  --filter type=container \
  --filter container="${GLUETUN_NAME}" \
  --filter event=die \
  --filter event=destroy \
  --filter event=start \
| while read -r action; do
    case "$action" in
      die|destroy)
        log "Gluetun event: ${action}"
        if stop_lock_active; then
          log "Stop ignored (stop-lock active for die/destroy burst)"
          continue
        fi
        set_stop_lock
        stop_apps
        ;;
      start)
        log "Gluetun event: start"
        if ! start_cooldown_ok; then
          log "Start ignored (cooldown ${START_COOLDOWN_SECONDS}s)"
          continue
        fi
        wait_for_gluetun_healthy
        start_apps
        ;;
    esac
  done
