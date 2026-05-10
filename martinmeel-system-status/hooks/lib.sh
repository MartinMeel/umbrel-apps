#!/bin/bash

set -eu

APP_ID="martinmeel-system-status"
UMBREL_ROOT="/home/umbrel/umbrel"
APP_DATA_DIR="$UMBREL_ROOT/app-data/$APP_ID"
RUNTIME_DIR="$APP_DATA_DIR/runtime"

generate_runtime() {
  mkdir -p "$RUNTIME_DIR/static"

  cat >"$RUNTIME_DIR/server.js" <<'EOF'
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");

const APP_PORT = Number(process.env.APP_PORT || 3000);
const STATIC_ROOT = path.join(__dirname, "static");
const HOST_APP_DATA = "/host-app-data";
const HOST_HOME = "/host-home";
const HOST_HOOKS = "/host-hooks";
const HOST_SYSTEMD = "/host-systemd";
const HOST_DPKG_STATUS = "/host-dpkg-status";
const DOCKER_SOCKET = "/var/run/docker.sock";
const GLUETUN_CONTAINER = "martinmeel-gluetun_server_1";
const STATUS_FILE = path.join(HOST_APP_DATA, "status", "gluetun-restart-state.json");
const POLL_SECONDS = 10;
const SMB_CREDENTIALS = path.join(HOST_APP_DATA, ".smbcredentials");
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

async function getMcStatus() {
  try {
    const content = await fsp.readFile(HOST_DPKG_STATUS, "utf8");
    const installed = /Package: mc\nStatus: install ok installed\n/m.test(content);
    return { installed };
  } catch (error) {
    return { installed: false, error: error.message };
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
  const [credentials, mountedShares, mcStatus, dockerStatus, restartState, preStartInfo, restartScriptInfo, timerInfo, serviceInfo] =
    await Promise.all([
      pathInfo(SMB_CREDENTIALS),
      getMountedShares(),
      getMcStatus(),
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
    mc: mcStatus,
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
    <title>MartinMeel System Status</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
    <main class="page">
      <section class="hero">
        <p class="eyebrow">UmbrelOS overview</p>
        <h1>MartinMeel System Status</h1>
        <p class="lede">Live overview of your mounted shares, mc install state, and Gluetun restart and health status.</p>
        <p id="meta" class="meta">Loading live status…</p>
      </section>
      <section class="grid">
        <article class="panel"><h2>SMB Credentials</h2><div id="credentials"></div></article>
        <article class="panel"><h2>mc</h2><div id="mc"></div></article>
      </section>
      <section class="panel"><h2>Mounted Shares</h2><div id="shares"></div></section>
      <section class="grid">
        <article class="panel"><h2>Restart Status</h2><div id="restart"></div></article>
        <article class="panel"><h2>Gluetun</h2><div id="gluetun"></div></article>
      </section>
      <section class="panel"><h2>Host Hooks</h2><div id="hooks"></div></section>
    </main>
    <script src="/app.js"></script>
  </body>
</html>
EOF

  cat >"$RUNTIME_DIR/static/app.js" <<'EOF'
const meta = document.querySelector("#meta");
const credentials = document.querySelector("#credentials");
const mc = document.querySelector("#mc");
const restart = document.querySelector("#restart");
const shares = document.querySelector("#shares");
const hooks = document.querySelector("#hooks");
const gluetun = document.querySelector("#gluetun");
function badge(ok, label) { return `<span class="badge ${ok ? "ok" : "bad"}">${label}</span>`; }
function safe(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"); }
function yesNo(value) { return value ? "Yes" : "No"; }
function renderKeyValue(items) {
  return `<div class="list">${items.map((item) => `<div class="row"><span class="label">${safe(item.label)}</span><span class="value">${item.value}</span></div>`).join("")}</div>`;
}
function renderShares(data) {
  shares.innerHTML = data.shares.map((entry) => `<article class="card"><div class="card-top"><strong>${safe(entry.share)}</strong>${badge(entry.mounted, entry.mounted ? "Mounted" : "Unmounted")}</div>${renderKeyValue([{ label: "Target", value: safe(entry.mountpoint) }, { label: "Directory exists", value: yesNo(entry.exists) }, { label: "Really mounted", value: yesNo(entry.mounted) }])}</article>`).join("");
}
function renderCredentials(data) {
  credentials.innerHTML = renderKeyValue([{ label: "Exists", value: `${badge(data.smbCredentials.exists, data.smbCredentials.exists ? "Present" : "Missing")} ` }, { label: "Mode", value: safe(data.smbCredentials.mode || "n/a") }, { label: "Last modified", value: safe(data.smbCredentials.modifiedAt || "n/a") }]);
}
function renderMc(data) {
  mc.innerHTML = renderKeyValue([{ label: "Installed", value: `${badge(data.mc.installed, data.mc.installed ? "Installed" : "Missing")} ` }, { label: "Source", value: safe("/var/lib/dpkg/status") }]);
}
function renderHooks(data) {
  hooks.innerHTML = `<div class="stack"><article class="card"><div class="card-top"><strong>pre-start</strong>${badge(data.hooks.preStart.exists, data.hooks.preStart.exists ? "Installed" : "Missing")}</div>${renderKeyValue([{ label: "Mode", value: safe(data.hooks.preStart.mode || "n/a") }, { label: "Modified", value: safe(data.hooks.preStart.modifiedAt || "n/a") }])}</article><article class="card"><div class="card-top"><strong>gluetun-daily-restart.sh</strong>${badge(data.hooks.gluetunRestartScript.exists, data.hooks.gluetunRestartScript.exists ? "Installed" : "Missing")}</div>${renderKeyValue([{ label: "Mode", value: safe(data.hooks.gluetunRestartScript.mode || "n/a") }, { label: "Modified", value: safe(data.hooks.gluetunRestartScript.modifiedAt || "n/a") }])}</article><article class="card"><div class="card-top"><strong>Systemd timer</strong>${badge(data.systemd.timer.exists && data.systemd.service.exists, data.systemd.timer.exists ? "Active on host" : "Not found")}</div>${renderKeyValue([{ label: "Timer file", value: safe(data.systemd.timer.modifiedAt || "n/a") }, { label: "Service file", value: safe(data.systemd.service.modifiedAt || "n/a") }])}</article></div>`;
}
function renderRestart(data) {
  if (!data.lastRestart) { restart.innerHTML = `<p class="empty">No restart state file yet. It will appear after the first timer run or manual service test.</p>`; return; }
  restart.innerHTML = renderKeyValue([{ label: "Last run", value: safe(data.lastRestart.timestamp || "n/a") }, { label: "Phase", value: safe(data.lastRestart.phase || "n/a") }, { label: "Message", value: safe(data.lastRestart.message || "n/a") }, { label: "Gluetun healthy after run", value: safe(data.lastRestart.gluetunHealth || "n/a") }, { label: "Stopped before restart", value: safe((data.lastRestart.stoppedContainers || []).join(", ") || "none") }]);
}
function renderGluetun(data) {
  if (!data.docker.dockerAvailable) { gluetun.innerHTML = `<p class="empty">Docker API unavailable: ${safe(data.docker.error || "unknown error")}</p>`; return; }
  if (!data.docker.gluetun) { gluetun.innerHTML = `<p class="empty">Container martinmeel-gluetun_server_1 was not found.</p>`; return; }
  gluetun.innerHTML = renderKeyValue([{ label: "Name", value: safe(data.docker.gluetun.name) }, { label: "State", value: safe(data.docker.gluetun.state) }, { label: "Running", value: yesNo(data.docker.gluetun.running) }, { label: "Health", value: safe(data.docker.gluetun.health) }, { label: "Started", value: safe(data.docker.gluetun.startedAt || "n/a") }]);
}
async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    meta.textContent = `Last refresh ${new Date(data.generatedAt).toLocaleString()} • Auto-refresh every ${data.pollSeconds} seconds`;
    renderCredentials(data); renderMc(data); renderRestart(data); renderShares(data); renderHooks(data); renderGluetun(data);
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
EOF
