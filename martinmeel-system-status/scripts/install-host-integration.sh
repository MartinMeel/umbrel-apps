#!/bin/bash

set -eu

APP_ID="martinmeel-system-status"
UMBREL_ROOT="/home/umbrel/umbrel"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CUSTOM_HOOKS_DIR="$UMBREL_ROOT/custom-hooks"
HOME_DIR="$UMBREL_ROOT/home"
STATUS_DIR="$HOME_DIR/status"
HOST_PRE_START="$CUSTOM_HOOKS_DIR/pre-start"
HOST_GLUETUN="$CUSTOM_HOOKS_DIR/gluetun-daily-restart.sh"
PRE_START_BACKUP="$CUSTOM_HOOKS_DIR/pre-start.$APP_ID.user-backup"
GLUETUN_BACKUP="$CUSTOM_HOOKS_DIR/gluetun-daily-restart.sh.$APP_ID.user-backup"
MARKER="# Managed by $APP_ID"
APP_PRE_START_SCRIPT="$APP_DIR/scripts/host-pre-start.sh"
APP_GLUETUN_SCRIPT="$APP_DIR/scripts/gluetun-daily-restart.sh"
SMB_CREDENTIALS="$HOME_DIR/.smbcredentials"

write_wrapper() {
  local target="$1"
  local backup="$2"
  local script_path="$3"

  cat >"$target" <<EOF
#!/bin/bash
$MARKER
set -u

backup="$backup"
script="$script_path"

if [ -x "\$backup" ]; then
  "\$backup" "\$@" || true
fi

if [ -x "\$script" ]; then
  exec "\$script" "\$@"
fi

echo "$APP_ID: target script not found at \$script"
exit 0
EOF

  chmod 755 "$target"
}

backup_if_needed() {
  local target="$1"
  local backup="$2"

  if [ -f "$target" ] && ! grep -qF "$MARKER" "$target" 2>/dev/null; then
    if [ ! -e "$backup" ]; then
      cp -a "$target" "$backup"
    fi
  fi
}

ensure_credentials() {
  mkdir -p "$HOME_DIR"

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

main() {
  mkdir -p "$CUSTOM_HOOKS_DIR" "$STATUS_DIR"

  backup_if_needed "$HOST_PRE_START" "$PRE_START_BACKUP"
  backup_if_needed "$HOST_GLUETUN" "$GLUETUN_BACKUP"

  write_wrapper "$HOST_PRE_START" "$PRE_START_BACKUP" "$APP_PRE_START_SCRIPT"
  write_wrapper "$HOST_GLUETUN" "$GLUETUN_BACKUP" "$APP_GLUETUN_SCRIPT"

  ensure_credentials

  if [ -x "$APP_PRE_START_SCRIPT" ]; then
    "$APP_PRE_START_SCRIPT" || true
  fi
}

main "$@"
