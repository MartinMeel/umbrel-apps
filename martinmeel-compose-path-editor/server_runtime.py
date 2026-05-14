import json, os, re, shutil, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(os.environ.get('HOST_UMBREL_ROOT', '/host/umbrel')).resolve()
APP_DATA = (ROOT / 'app-data').resolve()
SELF_APP_ID = os.environ.get('APP_ID', 'martinmeel-compose-path-editor')
ALLOWED_PATHS = [
  '${UMBREL_ROOT}/home/Downloads/qbittorrent/complete',
  '${UMBREL_ROOT}/home/Downloads/qbittorrent/incomplete',
  '${UMBREL_ROOT}/home/Downloads/sabnzbd/complete',
  '${UMBREL_ROOT}/home/Downloads/Films',
  '${UMBREL_ROOT}/home/Downloads/Films2',
  '${UMBREL_ROOT}/home/Downloads/TVSerie',
  '${UMBREL_ROOT}/home/Downloads/TVSeriesOLD',
]
SOURCE_RE = re.compile(r"(?P<source>(?:/home/umbrel/umbrel|\$[{]UMBREL_ROOT[}])/home/Downloads[^:\s\"']*|\$[{]APP_DATA_DIR[}][^:\s\"']*)(?=:[^:]+)")

def preferred_compose_path(old_source, new_path):
  prefix = '/home/umbrel/umbrel'
  if new_path.startswith('${UMBREL_ROOT}'):
      return new_path
  if old_source.startswith('${UMBREL_ROOT}') and new_path.startswith(prefix):
      return '${UMBREL_ROOT}' + new_path[len(prefix):]
  return new_path

def html():
  paths = json.dumps(ALLOWED_PATHS)
  return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Compose Path Editor</title><style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#111827;color:#e5e7eb}main{max-width:980px;margin:0 auto;padding:32px 18px}.card{background:#1f2937;border:1px solid #374151;border-radius:18px;padding:22px;margin:18px 0;box-shadow:0 10px 30px #0004}h1{margin:0 0 8px;font-size:32px}h2{font-size:18px;margin:0 0 14px}.muted{color:#9ca3af}.small{font-size:13px}select,input,button{width:100%;font:inherit;border-radius:12px;border:1px solid #4b5563;background:#111827;color:#f9fafb;padding:12px;box-sizing:border-box}.checks{display:grid;gap:8px}.check{display:flex;align-items:center;gap:10px;padding:11px 12px;border:1px solid #4b5563;border-radius:12px;background:#111827;cursor:pointer}.check input{width:auto;margin:0}.check span{overflow-wrap:anywhere}.operation{display:grid;gap:8px;margin-bottom:14px}.operation label{display:flex;gap:10px;align-items:flex-start;padding:12px;border:1px solid #4b5563;border-radius:12px;background:#111827;cursor:pointer}.operation input{width:auto;margin-top:3px}.operation strong{display:block}.operation small{display:block;color:#9ca3af;margin-top:3px}button{background:#2563eb;border:0;font-weight:700;cursor:pointer;margin-top:10px}button:disabled{opacity:.45}pre{white-space:pre-wrap;background:#0b1220;border-radius:12px;padding:14px;overflow:auto}.ok{color:#86efac}.err{color:#fca5a5}code{color:#bfdbfe}</style></head><body><main><h1>Compose Path Editor</h1><p class="muted">Select an installed Umbrel app, choose an existing volume line, then decide whether you want to replace that line, add new line(s), or do both. A backup is created before every change.</p><div class="card"><h2>1. Select docker-compose.yml</h2><select id="apps"><option>Loading...</option></select></div><div class="card"><h2>2. Select volume line from docker-compose.yml</h2><select id="lines"><option>Select an app first</option></select><pre id="preview" class="muted">No volume line selected.</pre></div><div class="card"><h2>3. Choose action and path(s)</h2><div class="operation"><label><input type="radio" name="op" value="replace" checked><span><strong>Replace only</strong><small>Replace the source path of the selected volume line. Select exactly one path below.</small></span></label><label><input type="radio" name="op" value="add"><span><strong>Add only</strong><small>Do not change the selected line. Add one or more new volume lines directly after it.</small></span></label><label><input type="radio" name="op" value="both"><span><strong>Replace and add</strong><small>Replace the selected line with the first selected path, and add the remaining selected paths after it.</small></span></label></div><div class="checks" id="paths"></div><p class="muted small">New paths use <code>${UMBREL_ROOT}</code>. Added lines use the folder name as container target, for example <code>${UMBREL_ROOT}/home/Downloads/Films:/Films</code>.</p><button id="apply" disabled>Apply selected action</button><p id="status" class="muted"></p></div></main><script>const allowed = """ + paths + r""";const apps=document.getElementById('apps'),paths=document.getElementById('paths'),lines=document.getElementById('lines'),preview=document.getElementById('preview'),applyBtn=document.getElementById('apply'),status=document.getElementById('status');let current=[];function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}function op(){return document.querySelector('input[name=op]:checked').value;}function selectedPaths(){return Array.from(paths.querySelectorAll('input[type=checkbox]:checked')).map(o=>o.value);}function renderPaths(){paths.innerHTML=allowed.map((p,i)=>`<label class="check"><input type="checkbox" value="${esc(p)}" ${i==0?'checked':''}><span>${esc(p)}</span></label>`).join('');paths.querySelectorAll('input').forEach(x=>x.onchange=renderPreview);}async function loadApps(){const r=await fetch('/api/apps');const data=await r.json();apps.innerHTML=data.apps.length?data.apps.map(a=>`<option value="${esc(a.id)}">${esc(a.id)} - ${esc(a.path)}</option>`).join(''):'<option value="">No apps found</option>';await loadLines();}async function loadLines(){status.textContent='';applyBtn.disabled=true;preview.textContent='Loading...';const id=apps.value;if(!id){preview.textContent='No app selected.';return;}const r=await fetch('/api/file?app_id='+encodeURIComponent(id));const data=await r.json();current=data.lines||[];lines.innerHTML=current.length?current.map(l=>`<option value="${l.no}">${l.no+1}: ${esc(l.text).slice(0,180)}</option>`).join(''):'<option>No replaceable or extendable volume lines found</option>';renderPreview();}function renderPreview(){const no=Number(lines.value);const item=current.find(x=>x.no===no);const chosen=selectedPaths();const mode=op();if(!item){preview.textContent='No volume line selected.';applyBtn.disabled=true;return;}let message='Selected line:\n'+item.text+'\n\nAction: '+({'replace':'Replace only','add':'Add only','both':'Replace and add'}[mode])+'\n\nSelected path(s):\n'+(chosen.length?chosen.join('\n'):'No path selected');let valid=false;if(mode==='replace'){valid=chosen.length===1;message+='\n\nResult: the selected line will be replaced. Nothing will be added.';}else if(mode==='add'){valid=chosen.length>=1;message+='\n\nResult: the selected line will stay unchanged. New line(s) will be added after it.';}else{valid=chosen.length>=1;message+='\n\nResult: the selected line will be replaced with the first selected path. Any extra selected paths will be added after it.';}preview.textContent=message;applyBtn.disabled=!valid;}document.querySelectorAll('input[name=op]').forEach(x=>x.onchange=renderPreview);apps.onchange=loadLines;lines.onchange=renderPreview;applyBtn.onclick=async()=>{const chosen=selectedPaths();const mode=op();status.textContent='Applying changes...';applyBtn.disabled=true;let payload={app_id:apps.value,line_no:Number(lines.value),operation:mode,paths:chosen};const r=await fetch('/api/modify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await r.json();if(r.ok){let details=[];if(data.old&&data.new)details.push('Old line:\n'+data.old+'\n\nNew line:\n'+data.new);if(data.added&&data.added.length)details.push('Inserted line(s):\n'+data.added.join('\n'));status.innerHTML='<span class="ok">Applied.</span> Backup: '+esc(data.backup);preview.textContent=details.join('\n\n')||'Applied.';await loadLines();}else{status.innerHTML='<span class="err">Error:</span> '+esc(data.error||'Unknown');renderPreview();}};renderPaths();loadApps().catch(e=>{status.innerHTML='<span class="err">'+esc(String(e))+'</span>';});</script></body></html>"""

def safe_compose(app_id):
  if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', app_id or ''):
      raise ValueError('Invalid app id')
  path = (APP_DATA / app_id / 'docker-compose.yml').resolve()
  if APP_DATA not in path.parents or path.name != 'docker-compose.yml':
      raise ValueError('Path is outside app-data')
  if not path.exists():
      raise FileNotFoundError('docker-compose.yml not found')
  return path

def list_apps():
  if not APP_DATA.exists(): return []
  out=[]
  for p in sorted(APP_DATA.glob('*/docker-compose.yml')):
      app_id=p.parent.name
      if app_id == SELF_APP_ID: continue
      out.append({'id': app_id, 'path': str(p).replace(str(ROOT), '${UMBREL_ROOT}')})
  return out

def compose_lines(app_id):
  p=safe_compose(app_id)
  lines=p.read_text(encoding='utf-8', errors='replace').splitlines()
  return [{'no': i, 'text': line} for i,line in enumerate(lines) if SOURCE_RE.search(line)]

def apply(app_id, line_no, new_path):
  if new_path not in ALLOWED_PATHS: raise ValueError('New path is not in the whitelist')
  p=safe_compose(app_id)
  lines=p.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)
  if not isinstance(line_no, int) or line_no < 0 or line_no >= len(lines): raise ValueError('Invalid line number')
  old=lines[line_no]
  match=SOURCE_RE.search(old)
  if not match: raise ValueError('Selected line does not contain a replaceable volume source')
  replacement = preferred_compose_path(match.group('source'), new_path)
  new = old[:match.start('source')] + replacement + old[match.end('source'):]
  if new == old: raise ValueError('Nothing changed')
  backup=p.with_name('docker-compose.yml.bak-' + time.strftime('%Y%m%d-%H%M%S'))
  shutil.copy2(p, backup)
  lines[line_no]=new
  tmp=p.with_suffix('.yml.tmp')
  tmp.write_text(''.join(lines), encoding='utf-8')
  tmp.replace(p)
  return str(backup).replace(str(ROOT), '${UMBREL_ROOT}'), old.rstrip('\n'), new.rstrip('\n')


def container_target_for(path):
  name = path.rstrip('/').split('/')[-1] or 'downloads'
  safe = re.sub(r'[^A-Za-z0-9_.-]+', '-', name).strip('-') or 'downloads'
  return '/' + safe

def source_for_style(reference_source, new_path):
  prefix = '/home/umbrel/umbrel'
  if new_path.startswith('${UMBREL_ROOT}'):
      return new_path
  if reference_source.startswith('${UMBREL_ROOT}') and new_path.startswith(prefix):
      return '${UMBREL_ROOT}' + new_path[len(prefix):]
  return new_path

def indentation_for(line):
  return line[:len(line)-len(line.lstrip())]

def add_paths(app_id, line_no, new_paths):
  if not isinstance(new_paths, list) or not new_paths:
      raise ValueError('Select at least one path to add')
  clean=[]
  for path in new_paths:
      if path not in ALLOWED_PATHS:
          raise ValueError('One or more selected paths are not in the whitelist')
      if path not in clean:
          clean.append(path)
  p=safe_compose(app_id)
  lines=p.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)
  if not isinstance(line_no, int) or line_no < 0 or line_no >= len(lines):
      raise ValueError('Invalid line number')
  reference=lines[line_no]
  match=SOURCE_RE.search(reference)
  if not match:
      raise ValueError('Selected line is not a usable volume line')
  indent=indentation_for(reference)
  newline='\n' if reference.endswith('\n') else '\n'
  existing=''.join(lines)
  added=[]
  for path in clean:
      source=source_for_style(match.group('source'), path)
      target=container_target_for(path)
      candidate=f'{indent}- {source}:{target}{newline}'
      if f'{source}:{target}' in existing or candidate in added:
          continue
      added.append(candidate)
  if not added:
      raise ValueError('No new lines to add; they may already exist')
  backup=p.with_name('docker-compose.yml.bak-' + time.strftime('%Y%m%d-%H%M%S'))
  shutil.copy2(p, backup)
  lines[line_no+1:line_no+1]=added
  tmp=p.with_suffix('.yml.tmp')
  tmp.write_text(''.join(lines), encoding='utf-8')
  tmp.replace(p)
  return str(backup).replace(str(ROOT), '${UMBREL_ROOT}'), [x.rstrip('\n') for x in added]

def modify_paths(app_id, line_no, operation, paths):
  if operation not in ('replace', 'add', 'both'):
      raise ValueError('Invalid operation')
  if not isinstance(paths, list) or not paths:
      raise ValueError('Select at least one path')
  clean=[]
  for path in paths:
      if path not in ALLOWED_PATHS:
          raise ValueError('One or more selected paths are not in the whitelist')
      if path not in clean:
          clean.append(path)
  if operation == 'replace' and len(clean) != 1:
      raise ValueError('Replace only needs exactly one selected path')

  p=safe_compose(app_id)
  lines=p.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)
  if not isinstance(line_no, int) or line_no < 0 or line_no >= len(lines):
      raise ValueError('Invalid line number')
  reference=lines[line_no]
  match=SOURCE_RE.search(reference)
  if not match:
      raise ValueError('Selected line is not a usable volume line')

  old=None
  new=None
  added=[]
  existing=''.join(lines)
  insert_at=line_no+1

  if operation in ('replace', 'both'):
      replacement = preferred_compose_path(match.group('source'), clean[0])
      new = reference[:match.start('source')] + replacement + reference[match.end('source'):]
      if new == reference and operation == 'replace':
          raise ValueError('Nothing changed')
      old = reference.rstrip('\n')
      lines[line_no] = new
      reference = new
      match = SOURCE_RE.search(reference)
      insert_at = line_no+1
      add_candidates = clean[1:] if operation == 'both' else []
  else:
      add_candidates = clean

  if operation in ('add', 'both'):
      indent=indentation_for(reference)
      newline='\n'
      for path in add_candidates:
          source=source_for_style(match.group('source'), path)
          target=container_target_for(path)
          candidate=f'{indent}- {source}:{target}{newline}'
          if f'{source}:{target}' in existing or candidate in added:
              continue
          added.append(candidate)
      if operation == 'add' and not added:
          raise ValueError('No new lines to add; they may already exist')
      if added:
          lines[insert_at:insert_at]=added

  if old is None and not added:
      raise ValueError('Nothing changed')

  backup=p.with_name('docker-compose.yml.bak-' + time.strftime('%Y%m%d-%H%M%S'))
  shutil.copy2(p, backup)
  tmp=p.with_suffix('.yml.tmp')
  tmp.write_text(''.join(lines), encoding='utf-8')
  tmp.replace(p)
  return {
      'backup': str(backup).replace(str(ROOT), '${UMBREL_ROOT}'),
      'old': old,
      'new': new.rstrip('\n') if new else None,
      'added': [x.rstrip('\n') for x in added],
  }

class Handler(BaseHTTPRequestHandler):
  def send_json(self, obj, code=200):
      data=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
  def do_GET(self):
      u=urlparse(self.path)
      try:
          if u.path == '/':
              data=html().encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
          elif u.path == '/api/apps': self.send_json({'apps': list_apps()})
          elif u.path == '/api/file': self.send_json({'lines': compose_lines(parse_qs(u.query).get('app_id',[''])[0])})
          else: self.send_json({'error':'Not found'},404)
      except Exception as e: self.send_json({'error':str(e)},400)
  def do_POST(self):
      try:
          path=urlparse(self.path).path
          length=int(self.headers.get('Content-Length','0'))
          body=json.loads(self.rfile.read(length) or b'{}')
          if path == '/api/apply':
              backup, old, new = apply(body.get('app_id'), body.get('line_no'), body.get('new_path'))
              self.send_json({'backup': backup, 'old': old, 'new': new})
          elif path == '/api/add':
              backup, added = add_paths(body.get('app_id'), body.get('line_no'), body.get('new_paths'))
              self.send_json({'backup': backup, 'added': added})
          else:
              self.send_json({'error':'Not found'},404)
      except Exception as e: self.send_json({'error':str(e)},400)
  def log_message(self, fmt, *args): print('%s - %s' % (self.address_string(), fmt%args), flush=True)

ThreadingHTTPServer(('0.0.0.0', 8099), Handler).serve_forever()