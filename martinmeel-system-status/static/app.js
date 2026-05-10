const meta = document.querySelector("#meta");
const credentials = document.querySelector("#credentials");
const mc = document.querySelector("#mc");
const restart = document.querySelector("#restart");
const shares = document.querySelector("#shares");
const hooks = document.querySelector("#hooks");
const gluetun = document.querySelector("#gluetun");

function badge(ok, label) {
  return `<span class="badge ${ok ? "ok" : "bad"}">${label}</span>`;
}

function stateBadge(kind, label) {
  return `<span class="badge ${kind}">${label}</span>`;
}

function safe(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function yesNo(value) {
  return value ? "Yes" : "No";
}

function renderKeyValue(items) {
  return `<div class="list">${items
    .map(
      (item) =>
        `<div class="row"><span class="label">${safe(item.label)}</span><span class="value">${item.value}</span></div>`,
    )
    .join("")}</div>`;
}

function renderShares(data) {
  shares.innerHTML = data.shares
    .map(
      (entry) => {
        const statusBadge =
          entry.strictState === "mounted"
            ? badge(true, "Mounted")
            : entry.strictState === "stale-suspected"
              ? stateBadge("warn", "Stale suspected")
              : badge(false, "Unmounted");
        return `<article class="card"><div class="card-top"><strong>${safe(entry.share)}</strong>${statusBadge}</div>${renderKeyValue([{ label: "Target", value: safe(entry.hostPath) }, { label: "Directory exists", value: yesNo(entry.exists) }, { label: "Mounted in kernel", value: yesNo(entry.mounted) }, { label: "Reachable now", value: yesNo(entry.responsive) }, { label: "Filesystem", value: safe(entry.fsType || "n/a") }, { label: "Mount source", value: safe(entry.mountSource || "n/a") }, { label: "Probe", value: safe(entry.probe || "n/a") }])}</article>`;
      },
    )
    .join("");
}

function renderCredentials(data) {
  credentials.innerHTML = renderKeyValue([
    { label: "Exists", value: `${badge(data.smbCredentials.exists, data.smbCredentials.exists ? "Present" : "Missing")} ` },
    { label: "Path", value: safe(data.smbCredentialsPath) },
    { label: "Mode", value: safe(data.smbCredentials.mode || "n/a") },
    { label: "Last modified", value: safe(data.smbCredentials.modifiedAt || "n/a") },
  ]);
}

function renderMc(data) {
  mc.innerHTML = renderKeyValue([
    { label: "Installed", value: `${badge(data.mc.installed, data.mc.installed ? "Installed" : "Missing")} ` },
    { label: "Source", value: safe("/var/lib/dpkg/status") },
  ]);
}

function renderHooks(data) {
  hooks.innerHTML = `<div class="stack"><article class="card"><div class="card-top"><strong>pre-start</strong>${badge(data.hooks.preStart.exists, data.hooks.preStart.exists ? "Installed" : "Missing")}</div>${renderKeyValue([{ label: "Mode", value: safe(data.hooks.preStart.mode || "n/a") }, { label: "Modified", value: safe(data.hooks.preStart.modifiedAt || "n/a") }])}</article><article class="card"><div class="card-top"><strong>gluetun-daily-restart.sh</strong>${badge(data.hooks.gluetunRestartScript.exists, data.hooks.gluetunRestartScript.exists ? "Installed" : "Missing")}</div>${renderKeyValue([{ label: "Mode", value: safe(data.hooks.gluetunRestartScript.mode || "n/a") }, { label: "Modified", value: safe(data.hooks.gluetunRestartScript.modifiedAt || "n/a") }])}</article><article class="card"><div class="card-top"><strong>Systemd timer</strong>${badge(data.systemd.timer.exists && data.systemd.service.exists, data.systemd.timer.exists ? "Active on host" : "Not found")}</div>${renderKeyValue([{ label: "Timer file", value: safe(data.systemd.timer.modifiedAt || "n/a") }, { label: "Service file", value: safe(data.systemd.service.modifiedAt || "n/a") }])}</article></div>`;
}

function renderRestart(data) {
  if (!data.lastRestart) {
    restart.innerHTML = `<p class="empty">No restart state file yet. It will appear after the first timer run or manual service test.</p>`;
    return;
  }
  const phase = data.lastRestart.phase || "unknown";
  const healthy = data.lastRestart.gluetunHealth === "healthy" || data.lastRestart.gluetunHealth === "none";
  restart.innerHTML = renderKeyValue([
    { label: "Result", value: `${stateBadge(healthy ? "ok" : "warn", healthy ? "Healthy" : "Needs attention")} ` },
    { label: "Last run", value: safe(data.lastRestart.timestamp || "n/a") },
    { label: "Phase", value: safe(data.lastRestart.phase || "n/a") },
    { label: "Message", value: safe(data.lastRestart.message || "n/a") },
    { label: "Gluetun healthy after run", value: safe(data.lastRestart.gluetunHealth || "n/a") },
    { label: "Stopped before restart", value: safe((data.lastRestart.stoppedContainers || []).join(", ") || "none") },
    { label: "State file", value: safe(data.statusFilePath) },
  ]);
}

function renderGluetun(data) {
  if (!data.docker.dockerAvailable) {
    gluetun.innerHTML = `<p class="empty">Docker API unavailable: ${safe(data.docker.error || "unknown error")}</p>`;
    return;
  }
  if (!data.docker.gluetun) {
    gluetun.innerHTML = `<p class="empty">Container martinmeel-gluetun_server_1 was not found.</p>`;
    return;
  }
  const healthy = data.docker.gluetun.running && (data.docker.gluetun.health === "healthy" || data.docker.gluetun.health === "none");
  gluetun.innerHTML = renderKeyValue([
    { label: "Overview", value: `${stateBadge(healthy ? "ok" : "warn", healthy ? "Active" : "Attention")} ` },
    { label: "Name", value: safe(data.docker.gluetun.name) },
    { label: "State", value: safe(data.docker.gluetun.state) },
    { label: "Running", value: yesNo(data.docker.gluetun.running) },
    { label: "Health", value: safe(data.docker.gluetun.health) },
    { label: "Started", value: safe(data.docker.gluetun.startedAt || "n/a") },
  ]);
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    meta.textContent = `Last refresh ${new Date(data.generatedAt).toLocaleString()} • Auto-refresh every ${data.pollSeconds} seconds`;
    renderCredentials(data);
    renderMc(data);
    renderRestart(data);
    renderShares(data);
    renderHooks(data);
    renderGluetun(data);
  } catch (error) {
    meta.textContent = `Status refresh failed: ${error.message}`;
  }
}

loadStatus();
setInterval(loadStatus, 10000);
