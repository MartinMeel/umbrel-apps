#!/usr/bin/env python3
# ============================================================
#  Umbrel Init — Status Web Server
#  Stored at: /home/umbrel/umbrel/app-data/martinmeel-umbrelinit/status_server.py
#  Serves a status dashboard on port 7891.
# ============================================================

import http.server
import json
import signal
import sys

STATUS_FILE = '/home/umbrel/umbrel/app-data/martinmeel-umbrelinit/status.json'
PORT = 7891

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="30">
<title>NAS Init - Status</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: system-ui, -apple-system, sans-serif;
    background: #0f0f0f;
    color: #e0e0e0;
    padding: 2rem;
    max-width: 600px;
    margin: 0 auto;
  }}
  h1 {{ color: #fff; font-size: 1.5rem; margin-bottom: 0.25rem; }}
  .subtitle {{ color: #666; font-size: 0.85rem; margin-bottom: 1.5rem; }}
  .card {{
    background: #1a1a1a;
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
    border: 1px solid #2a2a2a;
  }}
  .card-title {{
    font-weight: 600;
    color: #888;
    margin-bottom: 0.75rem;
    font-size: 0.9rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.6rem 0;
    border-bottom: 1px solid #222;
  }}
  .row:last-child {{ border-bottom: none; }}
  .label {{ color: #ccc; font-size: 0.95rem; }}
  .ok    {{ color: #4ade80; font-weight: 600; font-size: 0.9rem; }}
  .error {{ color: #f87171; font-weight: 600; font-size: 0.9rem; }}
  .footer {{ font-size: 0.8rem; color: #444; margin-top: 1rem; }}
</style>
</head>
<body>
<h1>NAS Init</h1>
<p class="subtitle">Auto-refreshes every 30 seconds</p>
{content}
<p class="footer">Last run: {timestamp}</p>
</body>
</html>"""


def badge(val):
    if val == 'ok':
        return '<span class="ok">&#10003; OK</span>'
    return '<span class="error">&#10007; Error</span>'


class StatusHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # suppress access logs

    def do_GET(self):
        try:
            with open(STATUS_FILE) as f:
                data = json.load(f)

            mounts = data.get('mounts', {})
            content = """
            <div class="card">
                <div class="card-title">Tools</div>
                <div class="row">
                    <span class="label">mc (Midnight Commander)</span>
                    {mc}
                </div>
            </div>
            <div class="card">
                <div class="card-title">NAS Mounts</div>
                <div class="row">
                    <span class="label">qbittorrent / complete</span>
                    {qbt_c}
                </div>
                <div class="row">
                    <span class="label">qbittorrent / incomplete</span>
                    {qbt_i}
                </div>
                <div class="row">
                    <span class="label">sabnzbd / complete</span>
                    {sab_c}
                </div>
                <div class="row">
                    <span class="label">sabnzbd / incomplete</span>
                    {sab_i}
                </div>
            </div>""".format(
                mc=badge(data.get('mc', 'error')),
                qbt_c=badge(mounts.get('qbittorrent_complete', 'error')),
                qbt_i=badge(mounts.get('qbittorrent_incomplete', 'error')),
                sab_c=badge(mounts.get('sabnzbd_complete', 'error')),
                sab_i=badge(mounts.get('sabnzbd_incomplete', 'error')),
            )
            timestamp = data.get('timestamp', 'unknown')

        except Exception as e:
            content = '<div class="card"><p class="error">Could not read status: {}</p></div>'.format(e)
            timestamp = 'N/A'

        body = HTML_TEMPLATE.format(content=content, timestamp=timestamp).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def shutdown(*_):
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

print(f'[INFO] Status server running on port {PORT}', flush=True)
http.server.HTTPServer(('0.0.0.0', PORT), StatusHandler).serve_forever()
