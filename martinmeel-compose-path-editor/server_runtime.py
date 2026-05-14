import json, os, re, shutil, time, subprocess, uuid, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(os.environ.get('HOST_UMBREL_ROOT', '/host/umbrel')).resolve()
APP_DATA = (ROOT / 'app-data').resolve()
SELF_APP_ID = os.environ.get('APP_ID', 'martinmeel-compose-path-editor')
PORT = int(os.environ.get('PORT','8099'))

ALLOWED_PATHS = [
  '${UMBREL_ROOT}/home/Downloads/complete:/complete',
  '${UMBREL_ROOT}/home/Downloads/incomplete:/incomplete',
  '${UMBREL_ROOT}/home/Downloads/Films:/movies',
  '${UMBREL_ROOT}/home/Downloads/Films2:/movies2',
  '${UMBREL_ROOT}/home/Downloads/TVSeries:/tv',
  '${UMBREL_ROOT}/home/Downloads/TVSeriesOLD:/tvold',
]

SRC_RE = re.compile(r'(?P<prefix>^\s*-\s*)(?P<src>(?:\$\{UMBREL_ROOT\}|/home/umbrel/umbrel)/home/Downloads(?:/[^:\s"\']+)?|\$\{APP_DATA_DIR\}/[^:\s"\']+)(?P<rest>:.+)$')
LOGS = {}


def addlog(op, msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{op}] {msg}"
    LOGS.setdefault(op, []).append(line)
    try:
        with open('/tmp/compose-path-editor-debug.log','a',encoding='utf-8') as f: f.write(line+'\n')
    except Exception: pass
    print(line, flush=True)


def host_display(p):
    s=str(p)
    return s.replace(str(ROOT), '${UMBREL_ROOT}')


def safe_compose(app_id):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', app_id or ''): raise ValueError('Invalid app id')
    p=(APP_DATA/app_id/'docker-compose.yml').resolve()
    if APP_DATA not in p.parents or p.name != 'docker-compose.yml': raise ValueError('Path outside app-data')
    if not p.exists(): raise FileNotFoundError('docker-compose.yml not found')
    return p


def list_apps():
    out=[]
    if APP_DATA.exists():
        for p in sorted(APP_DATA.glob('*/docker-compose.yml')):
            if p.parent.name == SELF_APP_ID: continue
            out.append({'id':p.parent.name,'path':host_display(p)})
    return out


def volume_lines(app_id):
    p=safe_compose(app_id)
    lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
    out=[]
    for i,line in enumerate(lines):
        m=SRC_RE.search(line)
        if m:
            out.append({'no':i,'text':line,'source':m.group('src'),'rest':m.group('rest')})
    return out


def backup_file(p, suffix='bak'):
    b=p.with_name('docker-compose.yml.%s-%s' % (suffix, time.strftime('%Y%m%d-%H%M%S')))
    shutil.copy2(p,b)
    return b


def modify_file(app_id, line_no, paths, action, op):
    p=safe_compose(app_id)
    if action not in ('replace','add','both'): raise ValueError('Invalid action')
    if not paths or any(x not in ALLOWED_PATHS for x in paths): raise ValueError('One or more selected volume mappings are not allowed')
    lines=p.read_text(encoding='utf-8',errors='replace').splitlines(keepends=True)
    if line_no < 0 or line_no >= len(lines): raise ValueError('Invalid line number')
    old=lines[line_no]
    m=SRC_RE.search(old.rstrip('\n'))
    if not m: raise ValueError('Selected line is not a recognized volume source line')
    indent=m.group('prefix')
    new_lines=[]
    if action == 'replace':
        if len(paths)!=1: raise ValueError('Replace only requires exactly one selected volume mapping')
        lines[line_no] = indent + paths[0] + ('\n' if old.endswith('\n') else '')
    elif action == 'add':
        new_lines=[indent + mapping + '\n' for mapping in paths]
        lines[line_no+1:line_no+1]=new_lines
    else:
        lines[line_no] = indent + paths[0] + ('\n' if old.endswith('\n') else '')
        new_lines=[indent + mapping + '\n' for mapping in paths[1:]]
        lines[line_no+1:line_no+1]=new_lines
    b=backup_file(p)
    tmp=p.with_suffix('.yml.tmp')
    tmp.write_text(''.join(lines), encoding='utf-8')
    tmp.replace(p)
    text=p.read_text(encoding='utf-8',errors='replace')
    expected=[]
    if action in ('replace','both'): expected.append(paths[0])
    if action=='add': expected += paths
    if action=='both': expected += paths[1:]
    missing=[x for x in expected if x not in text]
    if missing: raise RuntimeError('Verification failed. Missing after write: '+', '.join(missing))
    addlog(op, 'File write verified. Backup created: '+host_display(b))
    return host_display(b)


def list_backups(app_id):
    p=safe_compose(app_id)
    arr=[]
    for b in sorted(p.parent.glob('docker-compose.yml.bak-*'), reverse=True):
        arr.append({'name':b.name,'path':host_display(b)})
    return arr


def restore_backup(app_id, backup_name, op):
    p=safe_compose(app_id)
    if '/' in backup_name or not backup_name.startswith('docker-compose.yml.bak-'): raise ValueError('Invalid backup name')
    b=(p.parent/backup_name).resolve()
    if p.parent not in b.parents or not b.exists(): raise FileNotFoundError('Backup not found')
    safety=backup_file(p,'before-restore')
    shutil.copy2(b,p)
    if p.read_bytes()!=b.read_bytes(): raise RuntimeError('Restore verification failed')
    addlog(op, 'Restore verified. Safety backup created: '+host_display(safety))
    return host_display(safety)


def run_cmd(args, op, timeout=60):
    addlog(op, 'Trying command: ' + ' '.join(args))
    try:
        cp=subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        addlog(op, f'Exit code {cp.returncode}. stdout={cp.stdout.strip()} stderr={cp.stderr.strip()}')
        return cp.returncode, cp.stdout.strip(), cp.stderr.strip()
    except subprocess.TimeoutExpired as e:
        addlog(op, f'TIMEOUT after {timeout}s. stdout={(e.stdout or "").strip()} stderr={(e.stderr or "").strip()}')
        return 124, e.stdout or '', e.stderr or 'timeout'


def control_app(app_id, verb, op):
    if verb not in ('stop','restart'): raise ValueError('Invalid app control verb')
    mutation=f'apps.{verb}.mutate'
    addlog(op, f'Preparing app control action: {verb} for {app_id}')
    script=(
      'cd /home/umbrel 2>/dev/null || cd /; '
      'export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/umbreld/bin:/opt/umbreld"; '
      'export HOME=/home/umbrel USER=umbrel LOGNAME=umbrel TMPDIR=/tmp; '
      f'/opt/umbreld/umbreld client {mutation} --appId {app_id}'
    )
    # preferred: host namespace + host user. This mirrors: umbrel@host$ /opt/umbreld/umbreld client ...
    attempts=[
      ['/usr/sbin/chroot','/hostfs','/bin/su','-','umbrel','-c',script],
      ['chroot','/hostfs','/bin/su','-','umbrel','-c',script],
      ['/usr/sbin/chroot','/hostfs','/bin/sh','-lc',script],
      ['chroot','/hostfs','/bin/sh','-lc',script],
    ]
    errors=[]
    for a in attempts:
        code,out,err=run_cmd(a,op,timeout=60)
        if code==0 and (out.strip()=='true' or out.strip().endswith('true') or not err):
            addlog(op, f'App control {verb} succeeded for {app_id}')
            return
        errors.append(f"{' '.join(a)} => code {code}; stdout={out}; stderr={err}")
    raise RuntimeError('Could not run umbreld client '+mutation+' for '+app_id+'. Attempts:\n'+'\n'.join(errors))


def html():
    return r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Compose Path Editor</title><style>body{font-family:system-ui;margin:0;background:#111827;color:#e5e7eb}main{max-width:1100px;margin:auto;padding:24px}.card{background:#1f2937;border:1px solid #374151;border-radius:18px;padding:22px;margin:18px 0}select,button{width:100%;font:inherit;border-radius:12px;border:1px solid #4b5563;background:#111827;color:#fff;padding:12px}button{background:#2563eb;border:0;font-weight:700;margin-top:14px}.box{display:block;padding:12px;border:1px solid #4b5563;border-radius:12px;background:#111827;margin:8px 0}.muted{color:#a8b0bf}pre{white-space:pre-wrap;background:#0b1220;padding:14px;border-radius:12px;overflow:auto}.ok{color:#86efac}.err{color:#fca5a5}</style></head><body><main><h1>Compose Path Editor</h1><p class="muted">Select an installed Umbrel app, choose a volume line, then replace, add, or restore backups. The target app is stopped first and restarted after verified changes.</p><div class="card"><h2>1. Select docker-compose.yml</h2><select id="apps"></select></div><div class="card"><h2>2. Select volume line from docker-compose.yml</h2><select id="lines"></select><pre id="preview">Loading...</pre></div><div class="card"><h2>3. Choose action and volume mapping(s)</h2><label class="box"><input type="radio" name="action" value="replace" checked> <b>Replace only</b><br><span class="muted">Replace the selected source. Select exactly one volume mapping.</span></label><label class="box"><input type="radio" name="action" value="add"> <b>Add only</b><br><span class="muted">Keep the selected line and add one or more new volume lines after it.</span></label><label class="box"><input type="radio" name="action" value="both"> <b>Replace and add</b><br><span class="muted">Replace with first selected path and add remaining selected paths.</span></label><div id="paths"></div><button id="apply">Apply selected action</button><p id="status" class="muted"></p></div><div class="card"><h2>4. Restore a backup</h2><select id="backups"></select><button id="restore">Restore selected backup</button><p id="restoreStatus" class="muted"></p></div><div class="card"><h2>Live debug log</h2><pre id="log">No operation yet.</pre></div></main><script>
const allowed=['${UMBREL_ROOT}/home/Downloads/complete:/complete','${UMBREL_ROOT}/home/Downloads/incomplete:/incomplete','${UMBREL_ROOT}/home/Downloads/Films:/movies','${UMBREL_ROOT}/home/Downloads/Films2:/movies2','${UMBREL_ROOT}/home/Downloads/TVSeries:/tv','${UMBREL_ROOT}/home/Downloads/TVSeriesOLD:/tvold'];
const $=id=>document.getElementById(id); let current=[]; function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function renderPaths(){ $('paths').innerHTML=allowed.map((p,i)=>`<label class="box"><input type="checkbox" value="${esc(p)}" ${i==0?'checked':''}> ${esc(p)}</label>`).join(''); document.querySelectorAll('input').forEach(x=>x.onchange=preview); }
async function loadApps(){let d=await (await fetch('/api/apps')).json(); $('apps').innerHTML=d.apps.map(a=>`<option value="${esc(a.id)}">${esc(a.id)} - ${esc(a.path)}</option>`).join(''); await loadLines(); await loadBackups();}
async function loadLines(){let id=$('apps').value; let d=await (await fetch('/api/file?app_id='+encodeURIComponent(id))).json(); current=d.lines||[]; $('lines').innerHTML=current.length?current.map(l=>`<option value="${l.no}">${l.no+1}: ${esc(l.text)}</option>`).join(''):'<option>No replaceable volume lines found</option>'; preview();}
async function loadBackups(){let id=$('apps').value; let d=await (await fetch('/api/backups?app_id='+encodeURIComponent(id))).json(); $('backups').innerHTML=(d.backups||[]).length?d.backups.map(b=>`<option value="${esc(b.name)}">${esc(b.name)}</option>`).join(''):'<option value="">No backups found</option>';}
function selectedPaths(){return [...document.querySelectorAll('#paths input:checked')].map(x=>x.value)} function action(){return document.querySelector('input[name="action"]:checked').value}
function preview(){let item=current.find(x=>x.no==Number($('lines').value)); let ps=selectedPaths(); $('preview').textContent=item?`Selected line:\n${item.text}\n\nAction: ${action()}\nSelected volume mapping(s):\n${ps.join('\n')}`:'No line selected'}
async function poll(op){ if(!op) return; let d=await (await fetch('/api/logs?op='+encodeURIComponent(op))).json(); $('log').textContent=(d.logs||[]).join('\n')||'No logs yet.'; $('log').scrollTop=$('log').scrollHeight; }
async function post(url, body, statusEl){let op=Date.now()+'-'+Math.random().toString(16).slice(2); body.op=op; let timer=setInterval(()=>poll(op),1000); statusEl.textContent='Stopping target app, applying changes, verifying, then restarting target app...'; try{let ctrl=new AbortController(); let to=setTimeout(()=>ctrl.abort(),120000); let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:ctrl.signal}); clearTimeout(to); let d=await r.json(); await poll(op); if(r.ok){statusEl.innerHTML='<span class="ok">Changes verified and saved. Target app restarted.</span>'; $('preview').textContent=d.message+'\n\nBackup: '+(d.backup||d.safety_backup||''); await loadLines(); await loadBackups();} else {statusEl.innerHTML='<span class="err">Error:</span> '+esc(d.error||'Unknown');}}catch(e){statusEl.innerHTML='<span class="err">Error:</span> request timed out or was interrupted. See live log.';} finally{clearInterval(timer); await poll(op);}}
$('apps').onchange=async()=>{await loadLines(); await loadBackups();}; $('lines').onchange=preview; $('apply').onclick=()=>post('/api/modify',{app_id:$('apps').value,line_no:Number($('lines').value),paths:selectedPaths(),action:action()},$('status')); $('restore').onclick=()=>post('/api/restore',{app_id:$('apps').value,backup_name:$('backups').value},$('restoreStatus')); renderPaths(); loadApps().catch(e=>$('status').textContent=e);
</script></body></html>'''

class H(BaseHTTPRequestHandler):
    def j(self,obj,code=200):
        data=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        u=urlparse(self.path)
        try:
            if u.path=='/':
                data=html().encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
            elif u.path=='/api/apps': self.j({'apps':list_apps()})
            elif u.path=='/api/file': self.j({'lines':volume_lines(parse_qs(u.query).get('app_id',[''])[0])})
            elif u.path=='/api/backups': self.j({'backups':list_backups(parse_qs(u.query).get('app_id',[''])[0])})
            elif u.path=='/api/logs': self.j({'logs':LOGS.get(parse_qs(u.query).get('op',[''])[0],[])})
            else: self.j({'error':'Not found'},404)
        except Exception as e: self.j({'error':str(e)},400)
    def do_POST(self):
        try:
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}'); op=body.get('op') or str(uuid.uuid4()); LOGS[op]=[]
            if urlparse(self.path).path=='/api/modify':
                app=body.get('app_id'); addlog(op,'Starting modify operation for '+app); control_app(app,'stop',op); backup=modify_file(app,int(body.get('line_no')),body.get('paths') or [],body.get('action'),op); control_app(app,'restart',op); self.j({'message':'Changes verified and saved. Target app stopped before the change and restarted after verification.','backup':backup,'logs':LOGS[op]})
            elif urlparse(self.path).path=='/api/restore':
                app=body.get('app_id'); addlog(op,'Starting restore operation for '+app); control_app(app,'stop',op); safety=restore_backup(app,body.get('backup_name'),op); control_app(app,'restart',op); self.j({'message':'Backup restored and verified. Target app restarted.','safety_backup':safety,'logs':LOGS[op]})
            else: self.j({'error':'Not found'},404)
        except Exception as e:
            addlog(op, 'ERROR: '+str(e)); self.j({'error':str(e),'logs':LOGS.get(op,[])},400)
    def log_message(self, fmt, *args): print(fmt%args, flush=True)

ThreadingHTTPServer(('0.0.0.0',PORT), H).serve_forever()
