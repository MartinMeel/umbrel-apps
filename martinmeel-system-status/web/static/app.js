const meta = document.querySelector("#meta");
const credentials = document.querySelector("#credentials");
const restart = document.querySelector("#restart");
const shares = document.querySelector("#shares");
const hooks = document.querySelector("#hooks");
const gluetun = document.querySelector("#gluetun");
const containers = document.querySelector("#containers");

function badge(ok, label) {
  const tone = ok ? "ok" : "bad";
  return `<span class="badge ${tone}">${label}</span>`;
}

function safe(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function yesNo(value) {
  return value ? "Yes" : "No";
}

function renderKeyValue(items) {
  return `
    <div class="list">
      ${items
        .map(
          (item) => `
            <div class="row">
              <span class="label">${safe(item.label)}</span>
              <span class="value">${item.value}</span>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderShares(data) {
  shares.innerHTML = data.shares
    .map(
      (entry) => `
        <article class="card">
          <div class="card-top">
            <strong>${safe(entry.share)}</strong>
            ${badge(entry.exists && entry.mounted, entry.exists && entry.mounted ? "Mounted" : "Attention")}
          </div>
          ${renderKeyValue([
            { label: "Target", value: safe(entry.mountpoint) },
            { label: "Directory exists", value: yesNo(entry.exists) },
            { label: "Mounted", value: yesNo(entry.mounted) },
          ])}
        </article>
      `,
    )
    .join("");
}

function renderCredentials(data) {
  credentials.innerHTML = renderKeyValue([
    {
      label: "Exists",
      value: `${badge(data.smbCredentials.exists, data.smbCredentials.exists ? "Present" : "Missing")} `,
    },
    { label: "Mode", value: safe(data.smbCredentials.mode || "n/a") },
    { label: "Last modified", value: safe(data.smbCredentials.modifiedAt || "n/a") },
  ]);
}

function renderHooks(data) {
  hooks.innerHTML = `
    <div class="stack">
      <article class="card">
        <div class="card-top">
          <strong>pre-start</strong>
          ${badge(data.hooks.preStart.exists, data.hooks.preStart.exists ? "Installed" : "Missing")}
        </div>
        ${renderKeyValue([
          { label: "Mode", value: safe(data.hooks.preStart.mode || "n/a") },
          { label: "Modified", value: safe(data.hooks.preStart.modifiedAt || "n/a") },
        ])}
      </article>
      <article class="card">
        <div class="card-top">
          <strong>gluetun-daily-restart.sh</strong>
          ${badge(data.hooks.gluetunRestartScript.exists, data.hooks.gluetunRestartScript.exists ? "Installed" : "Missing")}
        </div>
        ${renderKeyValue([
          { label: "Mode", value: safe(data.hooks.gluetunRestartScript.mode || "n/a") },
          { label: "Modified", value: safe(data.hooks.gluetunRestartScript.modifiedAt || "n/a") },
        ])}
      </article>
      <article class="card">
        <div class="card-top">
          <strong>Systemd timer</strong>
          ${badge(data.systemd.timer.exists && data.systemd.service.exists, data.systemd.timer.exists ? "Active on host" : "Not found")}
        </div>
        ${renderKeyValue([
          { label: "Timer file", value: safe(data.systemd.timer.modifiedAt || "n/a") },
          { label: "Service file", value: safe(data.systemd.service.modifiedAt || "n/a") },
        ])}
      </article>
    </div>
  `;
}

function renderRestart(data) {
  if (!data.lastRestart) {
    restart.innerHTML = `<p class="empty">No restart state file yet. It will appear after the first timer run or manual script run.</p>`;
    return;
  }

  restart.innerHTML = renderKeyValue([
    { label: "Timestamp", value: safe(data.lastRestart.timestamp || "n/a") },
    { label: "Phase", value: safe(data.lastRestart.phase || "n/a") },
    { label: "Message", value: safe(data.lastRestart.message || "n/a") },
    { label: "Gluetun health", value: safe(data.lastRestart.gluetunHealth || "n/a") },
    {
      label: "Stopped containers",
      value: safe((data.lastRestart.stoppedContainers || []).join(", ") || "none"),
    },
  ]);
}

function renderGluetun(data) {
  if (!data.docker.dockerAvailable) {
    gluetun.innerHTML = `<p class="empty">Docker API unavailable: ${safe(data.docker.error || "unknown error")}</p>`;
    return;
  }

  if (!data.docker.gluetun) {
    gluetun.innerHTML = `<p class="empty">Container ${safe("martinmeel-gluetun_server_1")} was not found.</p>`;
    return;
  }

  gluetun.innerHTML = renderKeyValue([
    { label: "Name", value: safe(data.docker.gluetun.name) },
    { label: "State", value: safe(data.docker.gluetun.state) },
    { label: "Running", value: yesNo(data.docker.gluetun.running) },
    { label: "Health", value: safe(data.docker.gluetun.health) },
    { label: "Started", value: safe(data.docker.gluetun.startedAt || "n/a") },
  ]);
}

function renderContainers(data) {
  if (!data.docker.dockerAvailable) {
    containers.innerHTML = `<p class="empty">Docker API unavailable.</p>`;
    return;
  }

  if (!data.docker.dependentContainers.length) {
    containers.innerHTML = `<p class="empty">No running containers currently depend on Gluetun.</p>`;
    return;
  }

  containers.innerHTML = data.docker.dependentContainers
    .map(
      (container) => `
        <article class="card">
          <div class="card-top">
            <strong>${safe(container.name)}</strong>
            ${badge(container.running, container.running ? "Running" : "Stopped")}
          </div>
          ${renderKeyValue([
            { label: "State", value: safe(container.state) },
            { label: "Health", value: safe(container.health) },
            { label: "Started", value: safe(container.startedAt || "n/a") },
          ])}
        </article>
      `,
    )
    .join("");
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    meta.textContent = `Last refresh ${new Date(data.generatedAt).toLocaleString()} • Auto-refresh every ${data.pollSeconds} seconds`;
    renderCredentials(data);
    renderRestart(data);
    renderShares(data);
    renderHooks(data);
    renderGluetun(data);
    renderContainers(data);
  } catch (error) {
    meta.textContent = `Status refresh failed: ${error.message}`;
  }
}

loadStatus();
setInterval(loadStatus, 10000);
