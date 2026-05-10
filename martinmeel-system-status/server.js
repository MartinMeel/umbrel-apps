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
        res.on("data", (chunk) => {
          body += chunk;
        });
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
    return {
      exists: true,
      mode: (stats.mode & 0o777).toString(8),
      modifiedAt: stats.mtime.toISOString(),
    };
  } catch {
    return {
      exists: false,
      mode: null,
      modifiedAt: null,
    };
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
    return {
      ...entry,
      exists: fs.existsSync(entry.mountpoint),
      mounted: mountsContent.includes(` ${encodedPath} `),
    };
  });
}

async function getDockerStatus() {
  try {
    const containers = await listContainers();
    const gluetunSummary =
      containers.find((container) => container.Names.includes(`/${GLUETUN_CONTAINER}`)) || null;
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

    return { dockerAvailable: true, gluetun: gluetunDetails };
  } catch (error) {
    return { dockerAvailable: false, error: error.message, gluetun: null };
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
    pollSeconds: 10,
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
