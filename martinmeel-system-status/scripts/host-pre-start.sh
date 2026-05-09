#!/bin/bash

set -u

UMBREL_ROOT="/home/umbrel/umbrel"
HOME_DIR="$UMBREL_ROOT/home"
DOWNLOADS_DIR="$HOME_DIR/Downloads"
SMB_CREDENTIALS="$HOME_DIR/.smbcredentials"
CUSTOM_HOOKS_DIR="$UMBREL_ROOT/custom-hooks"
GLUETUN_SCRIPT="$CUSTOM_HOOKS_DIR/gluetun-daily-restart.sh"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_FILE="$SYSTEMD_DIR/umbrel-gluetun-daily-restart.service"
TIMER_FILE="$SYSTEMD_DIR/umbrel-gluetun-daily-restart.timer"

SHARES=(
  "//192.168.2.168/Films|$DOWNLOADS_DIR/Films"
  "//192.168.2.168/Films2|$DOWNLOADS_DIR/Films2"
  "//192.168.2.168/TVSeries|$DOWNLOADS_DIR/TVSeries"
  "//192.168.2.168/TVSeriesOLD|$DOWNLOADS_DIR/TVSeriesOLD"
)

log() {
  echo "martinmeel-system-status host-pre-start: $*"
}

ensure_dir() {
  local dir="$1"
  if [ ! -d "$dir" ]; then
    mkdir -p "$dir"
  fi
}

ensure_smb_credentials() {
  ensure_dir "$HOME_DIR"

  if [ ! -f "$SMB_CREDENTIALS" ]; then
    cat >"$SMB_CREDENTIALS" <<'EOF'
username=CHANGE_ME
password=CHANGE_ME
domain=WORKGROUP
EOF
  fi

  chmod 600 "$SMB_CREDENTIALS"
  if id -u umbrel >/dev/null 2>&1; then
    chown umbrel:umbrel "$SMB_CREDENTIALS" || true
  fi
}

mount_share() {
  local share="$1"
  local mountpoint="$2"
  local options

  ensure_dir "$mountpoint"

  if mountpoint -q "$mountpoint"; then
    return 0
  fi

  if ! command -v mount.cifs >/dev/null 2>&1; then
    log "mount.cifs unavailable, skipping $share"
    return 0
  fi

  options="credentials=$SMB_CREDENTIALS,uid=1000,gid=1000,iocharset=utf8,file_mode=0664,dir_mode=0775,vers=3.0,nofail"
  mount -t cifs "$share" "$mountpoint" -o "$options" || log "failed to mount $share"
}

install_timer() {
  cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=Restart Gluetun and dependent containers daily
After=network-online.target docker.service umbrel.service
Wants=network-online.target docker.service

[Service]
Type=oneshot
ExecStart=$GLUETUN_SCRIPT
EOF

  cat >"$TIMER_FILE" <<'EOF'
[Unit]
Description=Run Gluetun maintenance every day at 06:00

[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true
Unit=umbrel-gluetun-daily-restart.service

[Install]
WantedBy=timers.target
EOF

  if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
    systemctl enable --now umbrel-gluetun-daily-restart.timer || true
  fi
}

main() {
  ensure_dir "$DOWNLOADS_DIR"
  ensure_smb_credentials

  for mapping in "${SHARES[@]}"; do
    share="${mapping%%|*}"
    mountpoint="${mapping#*|}"
    mount_share "$share" "$mountpoint"
  done

  if [ -x "$GLUETUN_SCRIPT" ]; then
    install_timer
  fi
}

main "$@"
