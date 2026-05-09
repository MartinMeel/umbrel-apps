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
      {
        socketPath: DOCKER_SOCKET,
        path: pathname,
        method: "GET",
      },
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
  const raw = await dockerRequest("/containers/json?all=1");
  return JSON.parse(raw);
}

async function inspectContainer(name) {
  const raw = await dockerRequest(`/containers/${encodeURIComponent(name)}/json`);
  return JSON.parse(raw);
}

async function readJsonIfPresent(file) {
  try {
    const content = await fsp.readFile(file, "utf8");
    return JSON.parse(content);
  } catch (error) {
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
  } catch (error) {
    return {
      exists: false,
      mode: null,
      modifiedAt: null,
    };
  }
}

async function getMountedShares() {
  let mountsContent = "";
  try {
    mountsContent = await fsp.readFile("/proc/mounts", "utf8");
  } catch (error) {
    mountsContent = "";
  }

  return SHARES.map((entry) => {
    const encodedPath = entry.mountpoint.replace(/ /g, "\\040");
    const mounted = mountsContent.includes(` ${encodedPath} `);
    return {
      ...entry,
      exists: fs.existsSync(entry.mountpoint),
      mounted,
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

    return {
      dockerAvailable: true,
      gluetun: gluetunDetails,
      dependentContainers,
    };
  } catch (error) {
    return {
      dockerAvailable: false,
      error: error.message,
      gluetun: null,
      dependentContainers: [],
    };
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
    hooks: {
      preStart: preStartInfo,
      gluetunRestartScript: restartScriptInfo,
    },
    systemd: {
      timer: timerInfo,
      service: serviceInfo,
    },
    docker: dockerStatus,
    lastRestart: restartState,
  };
}

function contentType(filePath) {
  if (filePath.endsWith(".html")) return "text/html; charset=utf-8";
  if (filePath.endsWith(".css")) return "text/css; charset=utf-8";
  if (filePath.endsWith(".js")) return "application/javascript; charset=utf-8";
  if (filePath.endsWith(".svg")) return "image/svg+xml";
  if (filePath.endsWith(".json")) return "application/json; charset=utf-8";
  return "text/plain; charset=utf-8";
}

function sendJson(res, statusCode, payload) {
  const body = JSON.stringify(payload, null, 2);
  res.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8" });
  res.end(body);
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
  } catch (error) {
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
      const status = await buildStatus();
      sendJson(res, 200, status);
    } catch (error) {
      sendJson(res, 500, { error: error.message });
    }
    return;
  }

  await serveStatic(req, res);
});

server.listen(APP_PORT, "0.0.0.0", () => {
  console.log(`martinmeel-system-status listening on ${APP_PORT}`);
});
