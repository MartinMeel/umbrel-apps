#!/bin/bash

set -eu

APP_ID="martinmeel-system-status"
UMBREL_ROOT="/home/umbrel/umbrel"
APP_DATA_DIR="$UMBREL_ROOT/app-data/$APP_ID"
CUSTOM_HOOKS_DIR="$UMBREL_ROOT/custom-hooks"
HOME_DIR="$UMBREL_ROOT/home"
STATUS_DIR="$HOME_DIR/status"
HOST_PRE_START="$CUSTOM_HOOKS_DIR/pre-start"
HOST_GLUETUN="$CUSTOM_HOOKS_DIR/gluetun-daily-restart.sh"
PRE_START_BACKUP="$CUSTOM_HOOKS_DIR/pre-start.$APP_ID.user-backup"
GLUETUN_BACKUP="$CUSTOM_HOOKS_DIR/gluetun-daily-restart.sh.$APP_ID.user-backup"
MARKER="# Managed by $APP_ID"
SMB_CREDENTIALS="$HOME_DIR/.smbcredentials"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_FILE="$SYSTEMD_DIR/umbrel-gluetun-daily-restart.service"
TIMER_FILE="$SYSTEMD_DIR/umbrel-gluetun-daily-restart.timer"
RUNTIME_DIR="$APP_DATA_DIR/runtime"
HOST_PRE_START_SCRIPT="$APP_DATA_DIR/hooks/host-pre-start"
GLUETUN_SCRIPT="$APP_DATA_DIR/hooks/gluetun-daily-restart"

backup_if_needed() {
  local target="$1"
  local backup="$2"

  if [ -f "$target" ] && ! grep -qF "$MARKER" "$target" 2>/dev/null; then
    if [ ! -e "$backup" ]; then
      cp -a "$target" "$backup"
    fi
  fi
}

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

generate_runtime() {
  mkdir -p "$RUNTIME_DIR/static"

  cat >"$RUNTIME_DIR/server.js" <<'EOF'
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");

const APP_PORT = Number(process.env.APP_PORT || 3000);
const STATIC_ROOT = path.join(__dirname, "static");
const HOST_HOME = "/host-home";
const HOST_HOOKS = "/host-hooks";
const HOST_SYSTEMD = "/host-systemd";
const DOCKER_SOCKET = "/var/run/docker.sock";
const GLUETUN_CONTAINER = "martinmeel-gluetun_server_1";
const STATUS_FILE = path.join(HOST_HOME, "status", "gluetun-restart-state.json");
const POLL_SECONDS = 10;
const SMB_CREDENTIALS = path.join(HOST_HOME, ".smbcredentials");
const SHARES = [
  { share: "//192.168.2.168/Films", mountpoint: path.join(HOST_HOME, "Downloads", "Films") },
  { share: "//192.168.2.168/Films2", mountpoint: path.join(HOST_HOME, "Downloads", "Films2") },
  { share: "//192.168.2.168/TVSeries", mountpoint: path.join(HOST_HOME, "Downloads", "TVSeries") },
  { share: "//192.168.2.168/TVSeriesOLD", mountpoint: path.join(HOST_HOME, "Downloads", "TVSeriesOLD") },
];

function dockerRequest(pathname) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { socketPath: DOCKER_SOCKET, path: pathname, method: "GET" },
      (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => { body += chunk; });
        res.on("end", () => {
          if (res.statusCode < 200 || res.statusCode >= 300) {
            reject(new Error(`Docker API ${pathname} returned ${res.statusCode}`));
            return;
          }
          resolve(body);
        });
      },
    );
    req.on("error", reject);
    req.end();
  });
}

async function listContainers() {
  return JSON.parse(await dockerRequest("/containers/json?all=1"));
}

async function inspectContainer(name) {
  return JSON.parse(await dockerRequest(`/containers/${encodeURIComponent(name)}/json`));
}

async function readJsonIfPresent(file) {
  try {
    return JSON.parse(await fsp.readFile(file, "utf8"));
  } catch {
    return null;
  }
}

async function pathInfo(targetPath) {
  try {
    const stats = await fsp.stat(targetPath);
    return { exists: true, mode: (stats.mode & 0o777).toString(8), modifiedAt: stats.mtime.toISOString() };
  } catch {
    return { exists: false, mode: null, modifiedAt: null };
  }
}

async function getMountedShares() {
  let mountsContent = "";
  try {
    mountsContent = await fsp.readFile("/proc/mounts", "utf8");
  } catch {}

  return SHARES.map((entry) => {
    const encodedPath = entry.mountpoint.replace(/ /g, "\\040");
    return { ...entry, exists: fs.existsSync(entry.mountpoint), mounted: mountsContent.includes(` ${encodedPath} `) };
  });
}

async function getDockerStatus() {
  try {
    const containers = await listContainers();
    const gluetunSummary = containers.find((container) => container.Names.includes(`/${GLUETUN_CONTAINER}`)) || null;
    let gluetunDetails = null;
    if (gluetunSummary) {
      const inspect = await inspectContainer(GLUETUN_CONTAINER);
      gluetunDetails = {
        name: GLUETUN_CONTAINER,
        state: inspect.State?.Status || "unknown",
        running: Boolean(inspect.State?.Running),
        startedAt: inspect.State?.StartedAt || null,
        health: inspect.State?.Health?.Status || "none",
      };
    }

    const dependentContainers = [];
    for (const container of containers) {
      const inspect = await inspectContainer(container.Names[0].slice(1));
      if (inspect.HostConfig?.NetworkMode === `container:${GLUETUN_CONTAINER}`) {
        dependentContainers.push({
          name: inspect.Name.replace(/^\//, ""),
          state: inspect.State?.Status || "unknown",
          running: Boolean(inspect.State?.Running),
          startedAt: inspect.State?.StartedAt || null,
          health: inspect.State?.Health?.Status || "none",
        });
      }
    }
    dependentContainers.sort((a, b) => a.name.localeCompare(b.name));
    return { dockerAvailable: true, gluetun: gluetunDetails, dependentContainers };
  } catch (error) {
    return { dockerAvailable: false, error: error.message, gluetun: null, dependentContainers: [] };
  }
}

async function buildStatus() {
  const [credentials, mountedShares, dockerStatus, restartState, preStartInfo, restartScriptInfo, timerInfo, serviceInfo] =
    await Promise.all([
      pathInfo(SMB_CREDENTIALS),
      getMountedShares(),
      getDockerStatus(),
      readJsonIfPresent(STATUS_FILE),
      pathInfo(path.join(HOST_HOOKS, "pre-start")),
      pathInfo(path.join(HOST_HOOKS, "gluetun-daily-restart.sh")),
      pathInfo(path.join(HOST_SYSTEMD, "umbrel-gluetun-daily-restart.timer")),
      pathInfo(path.join(HOST_SYSTEMD, "umbrel-gluetun-daily-restart.service")),
    ]);

  return {
    generatedAt: new Date().toISOString(),
    pollSeconds: POLL_SECONDS,
    smbCredentials: credentials,
    shares: mountedShares,
    hooks: { preStart: preStartInfo, gluetunRestartScript: restartScriptInfo },
    systemd: { timer: timerInfo, service: serviceInfo },
    docker: dockerStatus,
    lastRestart: restartState,
  };
}

function contentType(filePath) {
  if (filePath.endsWith(".html")) return "text/html; charset=utf-8";
  if (filePath.endsWith(".css")) return "text/css; charset=utf-8";
  if (filePath.endsWith(".js")) return "application/javascript; charset=utf-8";
  if (filePath.endsWith(".svg")) return "image/svg+xml";
  return "text/plain; charset=utf-8";
}

function sendJson(res, statusCode, payload) {
  res.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload, null, 2));
}

async function serveStatic(req, res) {
  const requestPath = req.url === "/" ? "/index.html" : req.url;
  const safePath = path.normalize(requestPath).replace(/^(\.\.[/\\])+/, "");
  const filePath = path.join(STATIC_ROOT, safePath);
  if (!filePath.startsWith(STATIC_ROOT)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }
  try {
    const body = await fsp.readFile(filePath);
    res.writeHead(200, { "Content-Type": contentType(filePath) });
    res.end(body);
  } catch {
    res.writeHead(404);
    res.end("Not found");
  }
}

const server = http.createServer(async (req, res) => {
  if (!req.url) {
    res.writeHead(400);
    res.end("Bad request");
    return;
  }
  if (req.url === "/api/status") {
    try {
      sendJson(res, 200, await buildStatus());
    } catch (error) {
      sendJson(res, 500, { error: error.message });
    }
    return;
  }
  await serveStatic(req, res);
});

server.listen(APP_PORT, "0.0.0.0");
EOF

  cat >"$RUNTIME_DIR/static/index.html" <<'EOF'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>MartinMeel Media Automation</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
    <main class="page">
      <section class="hero">
        <p class="eyebrow">UmbrelOS automation</p>
        <h1>MartinMeel Media Automation</h1>
        <p class="lede">SMB mounts, host hook status, daily Gluetun automation, and every running container that routes through the VPN.</p>
        <p id="meta" class="meta">Loading live status…</p>
      </section>
      <section class="grid">
        <article class="panel"><h2>SMB Credentials</h2><div id="credentials"></div></article>
        <article class="panel"><h2>Scheduled Restart</h2><div id="restart"></div></article>
      </section>
      <section class="panel"><h2>Mounted Shares</h2><div id="shares"></div></section>
      <section class="grid">
        <article class="panel"><h2>Hooks and Timer</h2><div id="hooks"></div></article>
        <article class="panel"><h2>Gluetun</h2><div id="gluetun"></div></article>
      </section>
      <section class="panel"><h2>Dependent Containers</h2><div id="containers"></div></section>
    </main>
    <script src="/app.js"></script>
  </body>
</html>
EOF

  cat >"$RUNTIME_DIR/static/app.js" <<'EOF'
const meta = document.querySelector("#meta");
const credentials = document.querySelector("#credentials");
const restart = document.querySelector("#restart");
const shares = document.querySelector("#shares");
const hooks = document.querySelector("#hooks");
const gluetun = document.querySelector("#gluetun");
const containers = document.querySelector("#containers");
function badge(ok, label) { return `<span class="badge ${ok ? "ok" : "bad"}">${label}</span>`; }
function safe(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"); }
function yesNo(value) { return value ? "Yes" : "No"; }
function renderKeyValue(items) {
  return `<div class="list">${items.map((item) => `<div class="row"><span class="label">${safe(item.label)}</span><span class="value">${item.value}</span></div>`).join("")}</div>`;
}
function renderShares(data) {
  shares.innerHTML = data.shares.map((entry) => `<article class="card"><div class="card-top"><strong>${safe(entry.share)}</strong>${badge(entry.exists && entry.mounted, entry.exists && entry.mounted ? "Mounted" : "Attention")}</div>${renderKeyValue([{ label: "Target", value: safe(entry.mountpoint) }, { label: "Directory exists", value: yesNo(entry.exists) }, { label: "Mounted", value: yesNo(entry.mounted) }])}</article>`).join("");
}
function renderCredentials(data) {
  credentials.innerHTML = renderKeyValue([{ label: "Exists", value: `${badge(data.smbCredentials.exists, data.smbCredentials.exists ? "Present" : "Missing")} ` }, { label: "Mode", value: safe(data.smbCredentials.mode || "n/a") }, { label: "Last modified", value: safe(data.smbCredentials.modifiedAt || "n/a") }]);
}
function renderHooks(data) {
  hooks.innerHTML = `<div class="stack"><article class="card"><div class="card-top"><strong>pre-start</strong>${badge(data.hooks.preStart.exists, data.hooks.preStart.exists ? "Installed" : "Missing")}</div>${renderKeyValue([{ label: "Mode", value: safe(data.hooks.preStart.mode || "n/a") }, { label: "Modified", value: safe(data.hooks.preStart.modifiedAt || "n/a") }])}</article><article class="card"><div class="card-top"><strong>gluetun-daily-restart.sh</strong>${badge(data.hooks.gluetunRestartScript.exists, data.hooks.gluetunRestartScript.exists ? "Installed" : "Missing")}</div>${renderKeyValue([{ label: "Mode", value: safe(data.hooks.gluetunRestartScript.mode || "n/a") }, { label: "Modified", value: safe(data.hooks.gluetunRestartScript.modifiedAt || "n/a") }])}</article><article class="card"><div class="card-top"><strong>Systemd timer</strong>${badge(data.systemd.timer.exists && data.systemd.service.exists, data.systemd.timer.exists ? "Active on host" : "Not found")}</div>${renderKeyValue([{ label: "Timer file", value: safe(data.systemd.timer.modifiedAt || "n/a") }, { label: "Service file", value: safe(data.systemd.service.modifiedAt || "n/a") }])}</article></div>`;
}
function renderRestart(data) {
  if (!data.lastRestart) { restart.innerHTML = `<p class="empty">No restart state file yet. It will appear after the first timer run or manual script run.</p>`; return; }
  restart.innerHTML = renderKeyValue([{ label: "Timestamp", value: safe(data.lastRestart.timestamp || "n/a") }, { label: "Phase", value: safe(data.lastRestart.phase || "n/a") }, { label: "Message", value: safe(data.lastRestart.message || "n/a") }, { label: "Gluetun health", value: safe(data.lastRestart.gluetunHealth || "n/a") }, { label: "Stopped containers", value: safe((data.lastRestart.stoppedContainers || []).join(", ") || "none") }]);
}
function renderGluetun(data) {
  if (!data.docker.dockerAvailable) { gluetun.innerHTML = `<p class="empty">Docker API unavailable: ${safe(data.docker.error || "unknown error")}</p>`; return; }
  if (!data.docker.gluetun) { gluetun.innerHTML = `<p class="empty">Container martinmeel-gluetun_server_1 was not found.</p>`; return; }
  gluetun.innerHTML = renderKeyValue([{ label: "Name", value: safe(data.docker.gluetun.name) }, { label: "State", value: safe(data.docker.gluetun.state) }, { label: "Running", value: yesNo(data.docker.gluetun.running) }, { label: "Health", value: safe(data.docker.gluetun.health) }, { label: "Started", value: safe(data.docker.gluetun.startedAt || "n/a") }]);
}
function renderContainers(data) {
  if (!data.docker.dockerAvailable) { containers.innerHTML = `<p class="empty">Docker API unavailable.</p>`; return; }
  if (!data.docker.dependentContainers.length) { containers.innerHTML = `<p class="empty">No running containers currently depend on Gluetun.</p>`; return; }
  containers.innerHTML = data.docker.dependentContainers.map((container) => `<article class="card"><div class="card-top"><strong>${safe(container.name)}</strong>${badge(container.running, container.running ? "Running" : "Stopped")}</div>${renderKeyValue([{ label: "State", value: safe(container.state) }, { label: "Health", value: safe(container.health) }, { label: "Started", value: safe(container.startedAt || "n/a") }])}</article>`).join("");
}
async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    meta.textContent = `Last refresh ${new Date(data.generatedAt).toLocaleString()} • Auto-refresh every ${data.pollSeconds} seconds`;
    renderCredentials(data); renderRestart(data); renderShares(data); renderHooks(data); renderGluetun(data); renderContainers(data);
  } catch (error) { meta.textContent = `Status refresh failed: ${error.message}`; }
}
loadStatus();
setInterval(loadStatus, 10000);
EOF

  cat >"$RUNTIME_DIR/static/styles.css" <<'EOF'
:root { --bg:#08121b; --panel:rgba(8,23,34,.88); --panel-strong:#102738; --line:rgba(125,211,252,.16); --text:#e7f7ff; --muted:#9bc0d0; --accent:#7dd3fc; --ok:#34d399; --bad:#f87171; --shadow:0 28px 60px rgba(0,0,0,.34); }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; font-family:"IBM Plex Sans","Segoe UI",sans-serif; color:var(--text); background:radial-gradient(circle at top left, rgba(125,211,252,.14), transparent 28%), radial-gradient(circle at top right, rgba(52,211,153,.12), transparent 24%), linear-gradient(180deg,#0a1722 0%,#08121b 100%); }
.page { width:min(1160px,calc(100% - 32px)); margin:0 auto; padding:36px 0 48px; }
.hero { padding:20px 4px 28px; }
.eyebrow { margin:0 0 10px; color:var(--accent); text-transform:uppercase; letter-spacing:.22em; font-size:.74rem; }
h1,h2,p { margin:0; }
h1 { font-size:clamp(2rem,5vw,3.7rem); line-height:.98; letter-spacing:-.04em; }
.lede,.meta,.empty,.label,.value { color:var(--muted); }
.lede { margin-top:14px; max-width:760px; font-size:1.02rem; }
.meta { margin-top:16px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; margin-bottom:16px; }
.panel { margin-bottom:16px; padding:20px; background:var(--panel); border:1px solid var(--line); border-radius:24px; box-shadow:var(--shadow); backdrop-filter:blur(10px); }
.panel h2 { margin-bottom:16px; font-size:1.05rem; }
.stack,#shares,#containers { display:grid; gap:12px; }
.card { padding:14px 16px; border-radius:18px; background:var(--panel-strong); border:1px solid rgba(125,211,252,.12); }
.card-top,.row { display:flex; gap:12px; justify-content:space-between; align-items:center; }
.card-top { margin-bottom:10px; }
.list { display:grid; gap:8px; }
.label,.value { font-size:.94rem; }
.value { text-align:right; }
.badge { display:inline-flex; align-items:center; justify-content:center; min-width:88px; padding:6px 10px; border-radius:999px; font-size:.78rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase; }
.badge.ok { color:#052018; background:var(--ok); }
.badge.bad { color:#2f0909; background:var(--bad); }
.empty { line-height:1.5; }
@media (max-width:640px) { .page { width:min(100% - 20px,1160px); padding-top:24px; } .panel { padding:16px; border-radius:20px; } .card-top,.row { flex-direction:column; align-items:flex-start; } .value { text-align:left; } }
EOF
}

generate_host_pre_start() {
  cat >"$APP_DATA_DIR/hooks/host-pre-start" <<'EOF'
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
SHARES=("//192.168.2.168/Films|$DOWNLOADS_DIR/Films" "//192.168.2.168/Films2|$DOWNLOADS_DIR/Films2" "//192.168.2.168/TVSeries|$DOWNLOADS_DIR/TVSeries" "//192.168.2.168/TVSeriesOLD|$DOWNLOADS_DIR/TVSeriesOLD")
log(){ echo "martinmeel-system-status host-pre-start: $*"; }
ensure_dir(){ [ -d "$1" ] || mkdir -p "$1"; }
ensure_smb_credentials(){ ensure_dir "$HOME_DIR"; [ -f "$SMB_CREDENTIALS" ] || cat >"$SMB_CREDENTIALS" <<'CRED'
username=CHANGE_ME
password=CHANGE_ME
domain=WORKGROUP
CRED
chmod 600 "$SMB_CREDENTIALS"; id -u umbrel >/dev/null 2>&1 && chown umbrel:umbrel "$SMB_CREDENTIALS" || true; }
mount_share(){ local share="$1"; local mountpoint="$2"; local options; ensure_dir "$mountpoint"; mountpoint -q "$mountpoint" && return 0; command -v mount.cifs >/dev/null 2>&1 || { log "mount.cifs unavailable, skipping $share"; return 0; }; options="credentials=$SMB_CREDENTIALS,uid=1000,gid=1000,iocharset=utf8,file_mode=0664,dir_mode=0775,vers=3.0,nofail"; mount -t cifs "$share" "$mountpoint" -o "$options" || log "failed to mount $share"; }
install_timer(){ cat >"$SERVICE_FILE" <<EOF2
[Unit]
Description=Restart Gluetun and dependent containers daily
After=network-online.target docker.service umbrel.service
Wants=network-online.target docker.service
[Service]
Type=oneshot
ExecStart=$GLUETUN_SCRIPT
EOF2
cat >"$TIMER_FILE" <<'EOF2'
[Unit]
Description=Run Gluetun maintenance every day at 06:00
[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true
Unit=umbrel-gluetun-daily-restart.service
[Install]
WantedBy=timers.target
EOF2
command -v systemctl >/dev/null 2>&1 && { systemctl daemon-reload || true; systemctl enable --now umbrel-gluetun-daily-restart.timer || true; }; }
main(){ ensure_dir "$DOWNLOADS_DIR"; ensure_smb_credentials; for mapping in "${SHARES[@]}"; do share="${mapping%%|*}"; mountpoint="${mapping#*|}"; mount_share "$share" "$mountpoint"; done; [ -x "$GLUETUN_SCRIPT" ] && install_timer; }
main "$@"
EOF
  chmod 755 "$APP_DATA_DIR/hooks/host-pre-start"
}

generate_gluetun_script() {
  cat >"$APP_DATA_DIR/hooks/gluetun-daily-restart" <<'EOF'
#!/bin/bash
set -u
GLUETUN_CONTAINER="martinmeel-gluetun_server_1"
STATE_DIR="/home/umbrel/umbrel/home/status"
STATE_FILE="$STATE_DIR/gluetun-restart-state.json"
LOCK_DIR="/run/umbrel-gluetun-daily-restart.lock"
log(){ echo "gluetun-daily-restart: $*"; }
json_escape(){ local value="${1//\\/\\\\}"; value="${value//\"/\\\"}"; value="${value//$'\n'/\\n}"; printf '%s' "$value"; }
write_state(){ local phase="$1"; local message="$2"; local timestamp; local health="unknown"; local running="false"; local containers_json=""; local name; mkdir -p "$STATE_DIR"; timestamp="$(date -Iseconds)"; if docker inspect "$GLUETUN_CONTAINER" >/dev/null 2>&1; then running="$(docker inspect -f '{{if .State.Running}}true{{else}}false{{end}}' "$GLUETUN_CONTAINER" 2>/dev/null || echo false)"; health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$GLUETUN_CONTAINER" 2>/dev/null || echo unknown)"; fi; for name in "${STOPPED_CONTAINERS[@]:-}"; do [ -n "$name" ] || continue; [ -n "$containers_json" ] && containers_json="$containers_json,"; containers_json="$containers_json\"$(json_escape "$name")\""; done; cat >"$STATE_FILE" <<EOF2
{
  "timestamp": "$(json_escape "$timestamp")",
  "phase": "$(json_escape "$phase")",
  "message": "$(json_escape "$message")",
  "gluetunContainer": "$(json_escape "$GLUETUN_CONTAINER")",
  "gluetunRunning": $running,
  "gluetunHealth": "$(json_escape "$health")",
  "stoppedContainers": [$containers_json]
}
EOF2
}
cleanup(){ rm -rf "$LOCK_DIR"; }
discover_dependent_containers(){ local name; local network_mode; local -a found=(); while IFS= read -r name; do [ -n "$name" ] || continue; network_mode="$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$name" 2>/dev/null || true)"; [ "$network_mode" = "container:$GLUETUN_CONTAINER" ] && found+=("$name"); done < <(docker ps --format '{{.Names}}'); printf '%s\n' "${found[@]}"; }
wait_for_gluetun(){ local attempt=0; local max_attempts=40; local running; local health; while [ "$attempt" -lt "$max_attempts" ]; do running="$(docker inspect -f '{{if .State.Running}}true{{else}}false{{end}}' "$GLUETUN_CONTAINER" 2>/dev/null || echo false)"; health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$GLUETUN_CONTAINER" 2>/dev/null || echo unknown)"; if [ "$running" = "true" ] && { [ "$health" = "healthy" ] || [ "$health" = "none" ]; }; then return 0; fi; sleep 3; attempt=$((attempt + 1)); done; return 1; }
main(){ mkdir "$LOCK_DIR" 2>/dev/null || exit 0; trap cleanup EXIT; command -v docker >/dev/null 2>&1 || exit 0; if ! docker inspect "$GLUETUN_CONTAINER" >/dev/null 2>&1; then STOPPED_CONTAINERS=(); write_state "error" "gluetun container $GLUETUN_CONTAINER was not found"; exit 0; fi; mapfile -t STOPPED_CONTAINERS < <(discover_dependent_containers); write_state "stopping" "stopping containers connected through $GLUETUN_CONTAINER"; for container in "${STOPPED_CONTAINERS[@]}"; do docker stop "$container" >/dev/null; done; write_state "restarting-gluetun" "restarting $GLUETUN_CONTAINER"; docker restart "$GLUETUN_CONTAINER" >/dev/null; if wait_for_gluetun; then write_state "starting-dependents" "starting dependent containers"; else write_state "warning" "gluetun did not become healthy in time, attempting dependent container start anyway"; fi; for container in "${STOPPED_CONTAINERS[@]}"; do docker start "$container" >/dev/null; sleep 3; done; write_state "complete" "daily gluetun restart finished"; }
main "$@"
EOF
  chmod 755 "$APP_DATA_DIR/hooks/gluetun-daily-restart"
}

install_host_integration() {
  mkdir -p "$CUSTOM_HOOKS_DIR" "$STATUS_DIR"
  backup_if_needed "$HOST_PRE_START" "$PRE_START_BACKUP"
  backup_if_needed "$HOST_GLUETUN" "$GLUETUN_BACKUP"
  generate_host_pre_start
  generate_gluetun_script
  write_wrapper "$HOST_PRE_START" "$PRE_START_BACKUP" "$HOST_PRE_START_SCRIPT"
  write_wrapper "$HOST_GLUETUN" "$GLUETUN_BACKUP" "$GLUETUN_SCRIPT"
  ensure_credentials
  "$HOST_PRE_START_SCRIPT" || true
}

cleanup_host_integration() {
  restore_or_remove() {
    local target="$1"
    local backup="$2"
    if [ -f "$target" ] && grep -qF "$MARKER" "$target" 2>/dev/null; then rm -f "$target"; fi
    if [ -e "$backup" ]; then mv "$backup" "$target"; fi
  }
  if command -v systemctl >/dev/null 2>&1; then systemctl disable --now umbrel-gluetun-daily-restart.timer >/dev/null 2>&1 || true; fi
  rm -f "$SERVICE_FILE" "$TIMER_FILE"
  if command -v systemctl >/dev/null 2>&1; then systemctl daemon-reload >/dev/null 2>&1 || true; fi
  restore_or_remove "$HOST_PRE_START" "$PRE_START_BACKUP"
  restore_or_remove "$HOST_GLUETUN" "$GLUETUN_BACKUP"
}
EOF
