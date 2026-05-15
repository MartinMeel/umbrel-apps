const statusList = document.getElementById("status-list");
const credentialsPath = document.getElementById("credentials-path");
const editor = document.getElementById("credentials-editor");
const readyBadge = document.getElementById("ready-badge");
const saveButton = document.getElementById("save-button");
const saveMessage = document.getElementById("save-message");
const logPath = document.getElementById("log-path");
const logOutput = document.getElementById("log-output");
const refreshLogButton = document.getElementById("refresh-log-button");

function badge(label, good) {
  readyBadge.textContent = label;
  readyBadge.className = `badge ${good ? "good" : "warn"}`;
}

function row(label, value) {
  const wrapper = document.createElement("div");
  const key = document.createElement("dt");
  const val = document.createElement("dd");
  key.textContent = label;
  val.textContent = value;
  wrapper.appendChild(key);
  wrapper.appendChild(val);
  return wrapper;
}

async function load() {
  const [statusRes, credentialsRes] = await Promise.all([
    fetch("/api/status"),
    fetch("/api/credentials"),
  ]);

  const status = await statusRes.json();
  const credentials = await credentialsRes.json();

  statusList.innerHTML = "";
  statusList.appendChild(row("custom-hooks directory", status.customHooksDir));
  statusList.appendChild(row("wrapper pre-start", status.wrapperPreStartExists ? "Installed" : "Missing"));
  statusList.appendChild(row("managed pre-start", status.managedPreStartExists ? "Installed" : "Missing"));
  statusList.appendChild(row("gluetun restart script", status.gluetunScriptExists ? "Installed" : "Missing"));
  statusList.appendChild(row("gluetun watch script", status.gluetunWatcherScriptExists ? "Installed" : "Missing"));
  statusList.appendChild(row("gluetun watch service source", status.gluetunWatcherSourceServiceExists ? "Installed" : "Missing"));
  statusList.appendChild(row("credentials watcher", status.credentialsWatcherExists ? "Installed" : "Missing"));
  statusList.appendChild(row("gluetun watch service", status.gluetunWatcherServiceExists ? "Installed" : "Missing"));
  statusList.appendChild(row("credentials file", status.credentialsExists ? "Present" : "Missing"));
  statusList.appendChild(row("credentials mode", status.credentialsMode || "n/a"));
  statusList.appendChild(row("credentials ready", status.credentialsReady ? "Yes" : "No"));

  credentialsPath.textContent = credentials.path;
  editor.value = credentials.contents;
  badge(credentials.ready ? "Ready to Mount" : "Edit Required", credentials.ready);
  logPath.textContent = status.logPath;
  await loadLog();
}

async function loadLog() {
  const response = await fetch("/api/log");
  const log = await response.json();
  logPath.textContent = log.path;
  logOutput.textContent = log.contents || "No log entries yet.";
}

saveButton.addEventListener("click", async () => {
  saveButton.disabled = true;
  saveMessage.textContent = "Saving...";

  const response = await fetch("/api/credentials", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contents: editor.value }),
  });
  const result = await response.json();

  saveMessage.textContent = result.message || (result.saved ? "Saved." : "Save failed.");
  badge(result.ready ? "Ready to Mount" : "Edit Required", Boolean(result.ready));
  saveButton.disabled = false;
  await load();
});

refreshLogButton.addEventListener("click", () => {
  loadLog().catch((error) => {
    logOutput.textContent = `Failed to load log: ${error.message}`;
  });
});

load().catch((error) => {
  saveMessage.textContent = `Failed to load app state: ${error.message}`;
  badge("Error", false);
});
