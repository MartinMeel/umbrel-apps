#!/bin/bash
# ============================================================
#  NAS Init — Startup Script
#  Stored at: /home/umbrel/umbrel/app-data/martinmeel-umbrelinit/startup.sh
#  Called by the martinmeel-umbrelinit Umbrel app container.
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

STATUS_FILE=/home/umbrel/umbrel/app-data/martinmeel-umbrelinit/status.json
mkdir -p /home/umbrel/umbrel/app-data/martinmeel-umbrelinit
echo '{"status":"starting","timestamp":"'$(date -Iseconds)'"}' > "$STATUS_FILE"

# nsenter shortcut — runs commands directly on the HOST
HOST="nsenter --target 1 --mount --uts --ipc --net --pid --root --wd --"

info "============================================================"
info " Umbrel Init starting ..."
info "============================================================"

# ── Step 0: Recreate .smbcredentials if missing ──────────────
SMB_FILE=/home/umbrel/.smbcredentials
if [ ! -f "$SMB_FILE" ]; then
    info "Step 0 — Recreating missing .smbcredentials ..."
    cat > "$SMB_FILE" <<EOF
username=user
password=pword
EOF
    chmod 600 "$SMB_FILE"
    info "  Done. Edit $SMB_FILE with your real credentials if needed."
else
    info "Step 0 — .smbcredentials exists. Skipping."
fi

# ── Step 1: cifs-utils ───────────────────────────────────────
info "Step 1 — Checking cifs-utils on host ..."
if ! $HOST dpkg-query -W -f='${Status}' cifs-utils 2>/dev/null | grep -q 'install ok installed'; then
    info "  Installing cifs-utils ..."
    $HOST apt-get update -qq
    $HOST apt-get install -y -qq cifs-utils
    info "  Done."
else
    info "  Already installed."
fi

# ── Step 2: mc ───────────────────────────────────────────────
info "Step 2 — Checking mc on host ..."
if ! $HOST which mc > /dev/null 2>&1; then
    info "  Running apt-get update ..."
    $HOST apt-get update -qq
    info "  Installing mc ..."
    $HOST apt-get install -y -qq mc
    info "  Done."
else
    info "  Already installed."
fi

# ── Step 3: Mount NAS shares ─────────────────────────────────
info "Step 3 — Mounting NAS shares on host ..."

CREDENTIALS=/home/umbrel/.smbcredentials

SOURCES=(
    "//192.168.2.168/Downloads/qbittorrent/complete"
    "//192.168.2.168/Downloads/qbittorrent/incomplete"
    "//192.168.2.168/Downloads/sabnzbd/complete"
    "//192.168.2.168/Downloads/sabnzbd/incomplete"
)

TARGETS=(
    "/home/umbrel/umbrel/home/Downloads/NAS/qbittorrent/complete"
    "/home/umbrel/umbrel/home/Downloads/NAS/qbittorrent/incomplete"
    "/home/umbrel/umbrel/home/Downloads/NAS/sabnzbd/complete"
    "/home/umbrel/umbrel/home/Downloads/NAS/sabnzbd/incomplete"
)

QBT_COMPLETE="error"
QBT_INCOMPLETE="error"
SAB_COMPLETE="error"
SAB_INCOMPLETE="error"
STATUSES=( QBT_COMPLETE QBT_INCOMPLETE SAB_COMPLETE SAB_INCOMPLETE )

for i in "${!SOURCES[@]}"; do
    src="${SOURCES[$i]}"
    tgt="${TARGETS[$i]}"
    $HOST mkdir -p "$tgt"
    if $HOST mount | grep -q " on $tgt "; then
        warn "  Already mounted: $tgt"
        declare "${STATUSES[$i]}=ok"
    else
        info "  Mounting $src → $tgt"
        if $HOST mount -t cifs "$src" "$tgt" -o "credentials=$CREDENTIALS,uid=1000,user"; then
            info "  Mounted successfully."
            declare "${STATUSES[$i]}=ok"
        else
            error "  Failed: $src"
        fi
    fi
done

# ── Step 4: Write status file ─────────────────────────────────
MC_STATUS="ok"
if ! $HOST which mc > /dev/null 2>&1; then MC_STATUS="error"; fi

cat > "$STATUS_FILE" <<EOF
{
  "status": "done",
  "timestamp": "$(date -Iseconds)",
  "mc": "$MC_STATUS",
  "mounts": {
    "qbittorrent_complete":   "$QBT_COMPLETE",
    "qbittorrent_incomplete": "$QBT_INCOMPLETE",
    "sabnzbd_complete":       "$SAB_COMPLETE",
    "sabnzbd_incomplete":     "$SAB_INCOMPLETE"
  }
}
EOF

info "All tasks complete. Starting status server on port 7891 ..."

# ── Step 5: Start status web server ──────────────────────────
# Install python3 in the container if needed
if ! command -v python3 > /dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq python3
fi

exec python3 /home/umbrel/umbrel/app-data/martinmeel-umbrelinit/status_server.py
