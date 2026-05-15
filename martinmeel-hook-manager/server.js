const fs = require("fs");
const path = require("path");
const http = require("http");
const { URL } = require("url");

const PORT = Number(process.env.APP_PORT || 3000);
const APP_DATA_ROOT = "/host-app-data";
const CUSTOM_HOOKS_ROOT = "/host-custom-hooks";
const APP_SPECIFIC_DATA = path.join(APP_DATA_ROOT, "martinmeel-hook-manager");
const CREDENTIALS_PATH = path.join(APP_DATA_ROOT, ".smbcredentials");
const STATUS_PATH = path.join(APP_DATA_ROOT, "status", "gluetun-restart-state.json");
const APP_LOG_PATH = path.join(APP_SPECIFIC_DATA, "logs", "hook-manager.log");
const WRAPPER_PRE_START = path.join(CUSTOM_HOOKS_ROOT, "pre-start");
const MANAGED_PRE_START = path.join(CUSTOM_HOOKS_ROOT, "pre-start.martinmeel-hook-manager");
const GLUETUN_SCRIPT = path.join(CUSTOM_HOOKS_ROOT, "gluetun-daily-restart.sh");
const GLUETUN_WATCH_SCRIPT = path.join(CUSTOM_HOOKS_ROOT, "gluetun-umbreld-watch.v2.sh");
const GLUETUN_WATCH_SERVICE_SOURCE = path.join(CUSTOM_HOOKS_ROOT, "gluetun-umbreld-watch.v2.service");
const APPLY_PATH = "/host-systemd/umbrel-smb-credentials-apply.path";
const GLUETUN_WATCHER_SERVICE_PATH = "/host-systemd/gluetun-umbreld-watch.service";

function sendJson(res, statusCode, payload) {
  res.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload));
}

function sendText(res, statusCode, payload) {
  res.writeHead(statusCode, { "Content-Type": "text/plain; charset=utf-8" });
  res.end(payload);
}

function fileExists(filePath) {
  try {
    fs.accessSync(filePath);
    return true;
  } catch {
    return false;
  }
}

function readCredentials() {
  if (!fileExists(CREDENTIALS_PATH)) {
    return "username=CHANGE_ME\npassword=CHANGE_ME\n# domain=WORKGROUP\n";
  }
  return fs.readFileSync(CREDENTIALS_PATH, "utf8");
}

function maskCredentials(contents) {
  return contents
    .split(/\r?\n/)
    .map((line) => {
      if (line.startsWith("username=")) return "username=********";
      if (line.startsWith("password=")) return "password=********";
      return line;
    })
    .join("\n");
}

function credentialsReady(contents) {
  return !contents.includes("CHANGE_ME") && /(^|\n)username=/.test(contents) && /(^|\n)password=/.test(contents);
}

function readRestartState() {
  if (!fileExists(STATUS_PATH)) {
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(STATUS_PATH, "utf8"));
  } catch {
    return null;
  }
}

function readLogTail(maxLines = 200) {
  if (!fileExists(APP_LOG_PATH)) {
    return "";
  }
  const lines = fs.readFileSync(APP_LOG_PATH, "utf8").split(/\r?\n/);
  return lines.slice(-maxLines).join("\n");
}

function safeMode(filePath) {
  try {
    return (fs.statSync(filePath).mode & 0o777).toString(8);
  } catch {
    return null;
  }
}

function collectStatus() {
  const credentials = readCredentials();
  return {
    customHooksDir: CUSTOM_HOOKS_ROOT.replace("/host-custom-hooks", "/home/umbrel/umbrel/custom-hooks"),
    credentialsPath: CREDENTIALS_PATH.replace("/host-app-data", "/home/umbrel/umbrel/app-data"),
    logPath: APP_LOG_PATH.replace("/host-app-data", "/home/umbrel/umbrel/app-data"),
    wrapperPreStartExists: fileExists(WRAPPER_PRE_START),
    managedPreStartExists: fileExists(MANAGED_PRE_START),
    gluetunScriptExists: fileExists(GLUETUN_SCRIPT),
    gluetunWatcherScriptExists: fileExists(GLUETUN_WATCH_SCRIPT),
    gluetunWatcherSourceServiceExists: fileExists(GLUETUN_WATCH_SERVICE_SOURCE),
    credentialsWatcherExists: fileExists(APPLY_PATH),
    gluetunWatcherServiceExists: fileExists(GLUETUN_WATCHER_SERVICE_PATH),
    credentialsExists: fileExists(CREDENTIALS_PATH),
    credentialsMode: safeMode(CREDENTIALS_PATH),
    credentialsReady: credentialsReady(credentials),
    restartState: readRestartState(),
  };
}

function serveStatic(req, res) {
  const pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
  const relative = pathname === "/" ? "/index.html" : pathname;
  const filePath = path.join(__dirname, "static", relative);

  if (!filePath.startsWith(path.join(__dirname, "static"))) {
    sendText(res, 403, "Forbidden");
    return;
  }

  fs.readFile(filePath, (err, data) => {
    if (err) {
      sendText(res, 404, "Not found");
      return;
    }
    const contentType =
      relative.endsWith(".html") ? "text/html; charset=utf-8" :
      relative.endsWith(".css") ? "text/css; charset=utf-8" :
      relative.endsWith(".js") ? "application/javascript; charset=utf-8" :
      "application/octet-stream";
    res.writeHead(200, { "Content-Type": contentType });
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  const parsedUrl = new URL(req.url, `http://${req.headers.host}`);

  if (req.method === "GET" && parsedUrl.pathname === "/api/status") {
    sendJson(res, 200, collectStatus());
    return;
  }

  if (req.method === "GET" && parsedUrl.pathname === "/api/credentials") {
    const contents = readCredentials();
    sendJson(res, 200, {
      path: CREDENTIALS_PATH.replace("/host-app-data", "/home/umbrel/umbrel/app-data"),
      contents: credentialsReady(contents) ? maskCredentials(contents) : contents,
      ready: credentialsReady(contents),
      masked: credentialsReady(contents),
    });
    return;
  }

  if (req.method === "GET" && parsedUrl.pathname === "/api/log") {
    sendJson(res, 200, {
      path: APP_LOG_PATH.replace("/host-app-data", "/home/umbrel/umbrel/app-data"),
      contents: readLogTail(),
    });
    return;
  }

  if (req.method === "PUT" && parsedUrl.pathname === "/api/credentials") {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk.toString("utf8");
      if (body.length > 64 * 1024) {
        req.destroy();
      }
    });
    req.on("end", () => {
      let payload;
      try {
        payload = JSON.parse(body || "{}");
      } catch {
        sendJson(res, 400, { error: "Invalid JSON payload." });
        return;
      }

      if (typeof payload.contents !== "string") {
        sendJson(res, 400, { error: "contents must be a string." });
        return;
      }

      fs.writeFileSync(CREDENTIALS_PATH, payload.contents.endsWith("\n") ? payload.contents : `${payload.contents}\n`, { mode: 0o600 });
      fs.chmodSync(CREDENTIALS_PATH, 0o600);

      sendJson(res, 200, {
        saved: true,
        ready: credentialsReady(payload.contents),
        message: credentialsReady(payload.contents)
          ? "Credentials saved. The host watcher should run the managed pre-start automatically in a few seconds and mount the shares."
          : "Credentials saved, but CHANGE_ME is still present so mounts will continue to be skipped.",
      });
    });
    return;
  }

  serveStatic(req, res);
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`MartinMeel Hook Manager listening on ${PORT}`);
});
