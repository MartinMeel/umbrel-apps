import json, os, re, shutil, time, subprocess, uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(os.environ.get('HOST_UMBREL_ROOT', '/host/umbrel')).resolve()
APP_DATA = (ROOT / 'app-data').resolve()
SELF_APP_ID = os.environ.get('APP_ID', 'martinmeel-compose-path-editor')
PORT = int(os.environ.get('PORT', '8099'))
UMBREL_VAR = chr(36) + '{UMBREL_ROOT}'
APP_DATA_VAR = chr(36) + '{APP_DATA_DIR}'

ALLOWED_PATHS = [
    UMBREL_VAR + '/home/Downloads/complete:/complete',
    UMBREL_VAR + '/home/Downloads/incomplete:/incomplete',
    UMBREL_VAR + '/home/Downloads/Films:/movies',
    UMBREL_VAR + '/home/Downloads/Films2:/movies2',
    UMBREL_VAR + '/home/Downloads/TVSeries:/tv',
    UMBREL_VAR + '/home/Downloads/TVSeriesOLD:/tvold',
]

SRC_RE = re.compile(
    r'(?P<prefix>^\s*-\s*)'
    r'(?P<src>(?:' + re.escape(UMBREL_VAR) + r'|/home/umbrel/umbrel)/home/Downloads(?:/[^:\s"\']+)?|'
    + re.escape(APP_DATA_VAR) + r'/[^:\s"\']+)'
    r'(?P<rest>:.+)$'
)
LOGS = {}


def addlog(op, msg):
    line = '[{}] [{}] {}'.format(time.strftime('%Y-%m-%d %H:%M:%S'), op, msg)
    LOGS.setdefault(op, []).append(line)
    try:
        with open('/tmp/compose-path-editor-debug.log', 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass
    print(line, flush=True)


def host_display(p):
    return str(p).replace(str(ROOT), UMBREL_VAR)


def safe_compose(app_id):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', app_id or ''):
        raise ValueError('Invalid app id')
    p = (APP_DATA / app_id / 'docker-compose.yml').resolve()
    if APP_DATA not in p.parents or p.name != 'docker-compose.yml':
        raise ValueError('Path outside app-data')
    if not p.exists():
        raise FileNotFoundError('docker-compose.yml not found')
    return p


def list_apps():
    out = []
    if APP_DATA.exists():
        for p in sorted(APP_DATA.glob('*/docker-compose.yml')):
            if p.parent.name == SELF_APP_ID:
                continue
            out.append({'id': p.parent.name, 'path': host_display(p)})
    return out


def volume_lines(app_id):
    p = safe_compose(app_id)
    lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
    out = []
    for i, line in enumerate(lines):
        m = SRC_RE.search(line)
        if m:
            out.append({'no': i, 'text': line, 'source': m.group('src'), 'rest': m.group('rest')})
    return out


def backup_file(p, suffix='bak'):
    b = p.with_name('docker-compose.yml.{}-{}'.format(suffix, time.strftime('%Y%m%d-%H%M%S')))
    shutil.copy2(p, b)
    return b


def modify_file(app_id, line_no, paths, action, op):
    p = safe_compose(app_id)
    if action not in ('replace', 'add', 'both'):
        raise ValueError('Invalid action')
    if not paths or any(x not in ALLOWED_PATHS for x in paths):
        raise ValueError('One or more selected volume mappings are not allowed')
    lines = p.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)
    if line_no < 0 or line_no >= len(lines):
        raise ValueError('Invalid line number')
    old = lines[line_no]
    m = SRC_RE.search(old.rstrip('\n'))
    if not m:
        raise ValueError('Selected line is not a recognized volume source line')
    indent = m.group('prefix')
    if action == 'replace':
        if len(paths) != 1:
            raise ValueError('Replace only requires exactly one selected volume mapping')
        lines[line_no] = indent + paths[0] + ('\n' if old.endswith('\n') else '')
    elif action == 'add':
        lines[line_no + 1:line_no + 1] = [indent + mapping + '\n' for mapping in paths]
    else:
        lines[line_no] = indent + paths[0] + ('\n' if old.endswith('\n') else '')
        lines[line_no + 1:line_no + 1] = [indent + mapping + '\n' for mapping in paths[1:]]
    b = backup_file(p)
    tmp = p.with_suffix('.yml.tmp')
    tmp.write_text(''.join(lines), encoding='utf-8')
    tmp.replace(p)
    text = p.read_text(encoding='utf-8', errors='replace')
    expected = []
    if action in ('replace', 'both'):
        expected.append(paths[0])
    if action == 'add':
        expected += paths
    if action == 'both':
        expected += paths[1:]
    missing = [x for x in expected if x not in text]
    if missing:
        raise RuntimeError('Verification failed. Missing after write: ' + ', '.join(missing))
    addlog(op, 'File write verified. Backup created: ' + host_display(b))
    return host_display(b)


def list_backups(app_id):
    p = safe_compose(app_id)
    arr = []
    for b in sorted(p.parent.glob('docker-compose.yml.bak-*'), reverse=True):
        arr.append({'name': b.name, 'path': host_display(b)})
    return arr


def restore_backup(app_id, backup_name, op):
    p = safe_compose(app_id)
    if '/' in backup_name or not backup_name.startswith('docker-compose.yml.bak-'):
        raise ValueError('Invalid backup name')
    b = (p.parent / backup_name).resolve()
    if p.parent not in b.parents or not b.exists():
        raise FileNotFoundError('Backup not found')
    safety = backup_file(p, 'before-restore')
    shutil.copy2(b, p)
    if p.read_bytes() != b.read_bytes():
        raise RuntimeError('Restore verification failed')
    addlog(op, 'Restore verified. Safety backup created: ' + host_display(safety))
    return host_display(safety)


def run_cmd(args, op, timeout=60):
    addlog(op, 'Trying command: ' + ' '.join(args))
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        addlog(op, 'Exit code {}. stdout={} stderr={}'.format(cp.returncode, cp.stdout.strip(), cp.stderr.strip()))
        return cp.returncode, cp.stdout.strip(), cp.stderr.strip()
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ''
        err = e.stderr or ''
        addlog(op, 'TIMEOUT after {}s. stdout={} stderr={}'.format(timeout, str(out).strip(), str(err).strip()))
        return 124, out, err or 'timeout'


def control_app(app_id, verb, op):
    if verb not in ('stop', 'restart'):
        raise ValueError('Invalid app control verb')
    mutation = 'apps.{}.mutate'.format(verb)
    addlog(op, 'Preparing app control action: {} for {}'.format(verb, app_id))
    script = (
        'cd /home/umbrel 2>/dev/null || cd /; '
        'export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/umbreld/bin:/opt/umbreld"; '
        'export HOME=/home/umbrel USER=umbrel LOGNAME=umbrel TMPDIR=/tmp; '
        '/opt/umbreld/umbreld client {} --appId {}'.format(mutation, app_id)
    )
    attempts = [
        ['/usr/sbin/chroot', '/hostfs', '/bin/su', '-', 'umbrel', '-c', script],
        ['chroot', '/hostfs', '/bin/su', '-', 'umbrel', '-c', script],
        ['/usr/sbin/chroot', '/hostfs', '/bin/sh', '-lc', script],
        ['chroot', '/hostfs', '/bin/sh', '-lc', script],
    ]
    errors = []
    for a in attempts:
        code, out, err = run_cmd(a, op, timeout=60)
        if code == 0 and (out.strip() == 'true' or out.strip().endswith('true') or not err):
            addlog(op, 'App control {} succeeded for {}'.format(verb, app_id))
            return
        errors.append('{} => code {}; stdout={}; stderr={}'.format(' '.join(a), code, out, err))
    raise RuntimeError('Could not run umbreld client ' + mutation + ' for ' + app_id + '. Attempts:\n' + '\n'.join(errors))


def html():
    allowed_json = json.dumps(ALLOWED_PATHS)
    return '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Compose Path Editor</title><style>body{font-family:system-ui;margin:0;background:#111827;color:#e5e7eb}main{max-width:1100px;margin:auto;padding:24px}.card{background:#1f2937;border:1px solid #374151;border-radius:18px;padding:22px;margin:18px 0}select,button{width:100%;font:inherit;border-radius:12px;border:1px solid #4b5563;background:#111827;color:#fff;padding:12px}button{background:#2563eb;border:0;font-weight:700;margin-top:14px}.box{display:block;padding:12px;border:1px solid #4b5563;border-radius:12px;background:#111827;margin:8px 0}.muted{color:#a8b0bf}pre{white-space:pre-wrap;background:#0b1220;padding:14px;border-radius:12px;overflow:auto}.ok{color:#86efac}.err{color:#fca5a5}</style></head><body><main><h1>Compose Path Editor</h1><p class="muted">Select an installed Umbrel app, choose a volume line, then replace, add, or restore backups. The target app is stopped first and restarted after verified changes.</p><div class="card"><h2>1. Select docker-compose.yml</h2><select id="apps"></select></div><div class="card"><h2>2. Select volume line from docker-compose.yml</h2><select id="lines"></select><pre id="preview">Loading...</pre></div><div class="card"><h2>3. Choose action and volume mapping(s)</h2><label class="box"><input type="radio" name="action" value="replace" checked> <b>Replace only</b><br><span class="muted">Replace the selected source. Select exactly one volume mapping.</span></label><label class="box"><input type="radio" name="action" value="add"> <b>Add only</b><br><span class="muted">Keep the selected line and add one or more new volume lines after it.</span></label><label class="box"><input type="radio" name="action" value="both"> <b>Replace and add</b><br><span class="muted">Replace with first selected mapping and add remaining selected mappings.</span></label><div id="paths"></div><button id="apply">Apply selected action</button><p id="status" class="muted"></p></div><div class="card"><h2>4. Restore a backup</h2><select id="backups"></select><button id="restore">Restore selected backup</button><p id="restoreStatus" class="muted"></p></div><div class="card"><h2>Live debug log</h2><pre id="log">No operation yet.</pre></div></main><script>\n''' + "const allowed=" + allowed_json + ";\n" + r'''
function ge(id){return document.getElementById(id)}
let current=[];
function esc(s){return String(s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
function selectedPaths(){return Array.prototype.slice.call(document.querySelectorAll('#paths input:checked')).map(function(x){return x.value})}
function action(){return document.querySelector('input[name="action"]:checked').value}
function renderPaths(){
  ge('paths').innerHTML=allowed.map(function(p,i){return '<label class="box"><input type="checkbox" value="'+esc(p)+'" '+(i===0?'checked':'')+'> '+esc(p)+'</label>'}).join('');
  Array.prototype.slice.call(document.querySelectorAll('input')).forEach(function(x){x.onchange=preview})
}
async function loadApps(){let d=await (await fetch('/api/apps')).json(); ge('apps').innerHTML=d.apps.map(function(a){return '<option value="'+esc(a.id)+'">'+esc(a.id)+' - '+esc(a.path)+'</option>'}).join(''); await loadLines(); await loadBackups()}
async function loadLines(){let id=ge('apps').value; let d=await (await fetch('/api/file?app_id='+encodeURIComponent(id))).json(); current=d.lines||[]; ge('lines').innerHTML=current.length?current.map(function(l){return '<option value="'+l.no+'">'+(l.no+1)+': '+esc(l.text)+'</option>'}).join(''):'<option>No replaceable volume lines found</option>'; preview()}
async function loadBackups(){let id=ge('apps').value; let d=await (await fetch('/api/backups?app_id='+encodeURIComponent(id))).json(); ge('backups').innerHTML=(d.backups||[]).length?d.backups.map(function(b){return '<option value="'+esc(b.name)+'">'+esc(b.name)+'</option>'}).join(''):'<option value="">No backups found</option>'}
function preview(){let item=current.find(function(x){return x.no==Number(ge('lines').value)}); let ps=selectedPaths(); ge('preview').textContent=item?('Selected line:\n'+item.text+'\n\nAction: '+action()+'\nSelected volume mapping(s):\n'+ps.join('\n')):'No line selected'}
async function poll(op){ if(!op) return; let d=await (await fetch('/api/logs?op='+encodeURIComponent(op))).json(); ge('log').textContent=(d.logs||[]).join('\n')||'No logs yet.'; ge('log').scrollTop=ge('log').scrollHeight }
async function post(url, body, statusEl){let op=Date.now()+'-'+Math.random().toString(16).slice(2); body.op=op; let timer=setInterval(function(){poll(op)},1000); statusEl.textContent='Stopping target app, applying changes, verifying, then restarting target app...'; try{let ctrl=new AbortController(); let to=setTimeout(function(){ctrl.abort()},120000); let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:ctrl.signal}); clearTimeout(to); let d=await r.json(); await poll(op); if(r.ok){statusEl.innerHTML='<span class="ok">Changes verified and saved. Target app restarted.</span>'; ge('preview').textContent=d.message+'\n\nBackup: '+(d.backup||d.safety_backup||''); await loadLines(); await loadBackups()} else {statusEl.innerHTML='<span class="err">Error:</span> '+esc(d.error||'Unknown')}}catch(e){statusEl.innerHTML='<span class="err">Error:</span> request timed out or was interrupted. See live log.'} finally{clearInterval(timer); await poll(op)}}
ge('apps').onchange=async function(){await loadLines(); await loadBackups()}; ge('lines').onchange=preview; ge('apply').onclick=function(){post('/api/modify',{app_id:ge('apps').value,line_no:Number(ge('lines').value),paths:selectedPaths(),action:action()},ge('status'))}; ge('restore').onclick=function(){post('/api/restore',{app_id:ge('apps').value,backup_name:ge('backups').value},ge('restoreStatus'))}; renderPaths(); loadApps().catch(function(e){ge('status').textContent=e});
</script></body></html>'''


class H(BaseHTTPRequestHandler):
    def j(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == '/':
                data = html().encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif u.path == '/api/apps':
                self.j({'apps': list_apps()})
            elif u.path == '/api/file':
                self.j({'lines': volume_lines(parse_qs(u.query).get('app_id', [''])[0])})
            elif u.path == '/api/backups':
                self.j({'backups': list_backups(parse_qs(u.query).get('app_id', [''])[0])})
            elif u.path == '/api/logs':
                self.j({'logs': LOGS.get(parse_qs(u.query).get('op', [''])[0], [])})
            else:
                self.j({'error': 'Not found'}, 404)
        except Exception as e:
            self.j({'error': str(e)}, 400)

    def do_POST(self):
        op = str(uuid.uuid4())
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
            op = body.get('op') or op
            LOGS[op] = []
            if urlparse(self.path).path == '/api/modify':
                app = body.get('app_id')
                addlog(op, 'Starting modify operation for ' + app)
                control_app(app, 'stop', op)
                backup = modify_file(app, int(body.get('line_no')), body.get('paths') or [], body.get('action'), op)
                control_app(app, 'restart', op)
                self.j({'message': 'Changes verified and saved. Target app stopped before the change and restarted after verification.', 'backup': backup, 'logs': LOGS[op]})
            elif urlparse(self.path).path == '/api/restore':
                app = body.get('app_id')
                addlog(op, 'Starting restore operation for ' + app)
                control_app(app, 'stop', op)
                safety = restore_backup(app, body.get('backup_name'), op)
                control_app(app, 'restart', op)
                self.j({'message': 'Backup restored and verified. Target app restarted.', 'safety_backup': safety, 'logs': LOGS[op]})
            else:
                self.j({'error': 'Not found'}, 404)
        except Exception as e:
            addlog(op, 'ERROR: ' + str(e))
            self.j({'error': str(e), 'logs': LOGS.get(op, [])}, 400)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
