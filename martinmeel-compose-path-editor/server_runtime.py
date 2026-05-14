import json, os, re, shutil, subprocess, time
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
  return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Compose Path Editor</title><style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#111827;color:#e5e7eb}main{max-width:980px;margin:0 auto;padding:32px 18px}.card{background:#1f2937;border:1px solid #374151;border-radius:18px;padding:22px;margin:18px 0;box-shadow:0 10px 30px #0004}h1{margin:0 0 8px;font-size:32px}h2{font-size:18px;margin:0 0 14px}.muted{color:#9ca3af}.small{font-size:13px}select,input,button{width:100%;font:inherit;border-radius:12px;border:1px solid #4b5563;background:#111827;color:#f9fafb;padding:12px;box-sizing:border-box}.checks{display:grid;gap:8px}.check{display:flex;align-items:center;gap:10px;padding:11px 12px;border:1px solid #4b5563;border-radius:12px;background:#111827;cursor:pointer}.check input{width:auto;margin:0}.check span{overflow-wrap:anywhere}.operation{display:grid;gap:8px;margin-bottom:14px}.operation label{display:flex;gap:10px;align-items:flex-start;padding:12px;border:1px solid #4b5563;border-radius:12px;background:#111827;cursor:pointer}.operation input{width:auto;margin-top:3px}.operation strong{display:block}.operation small{display:block;color:#9ca3af;margin-top:3px}button{background:#2563eb;border:0;font-weight:700;cursor:pointer;margin-top:10px}button:disabled{opacity:.45}pre{white-space:pre-wrap;background:#0b1220;border-radius:12px;padding:14px;overflow:auto}.ok{color:#86efac}.err{color:#fca5a5}code{color:#bfdbfe}</style></head><body><main><h1>Compose Path Editor</h1><p class="muted">Select an installed Umbrel app, choose an existing volume line, then decide whether you want to replace that line, add new line(s), or do both. A backup is created before every change, and backups can be restored from this page.</p><div class="card"><h2>1. Select docker-compose.yml</h2><select id="apps"><option>Loading...</option></select></div><div class="card"><h2>2. Select volume line from docker-compose.yml</h2><select id="lines"><option>Select an app first</option></select><pre id="preview" class="muted">No volume line selected.</pre></div><div class="card"><h2>3. Choose action and path(s)</h2><div class="operation"><label><input type="radio" name="op" value="replace" checked><span><strong>Replace only</strong><small>Replace the source path of the selected volume line. Select exactly one path below.</small></span></label><label><input type="radio" name="op" value="add"><span><strong>Add only</strong><small>Do not change the selected line. Add one or more new volume lines directly after it.</small></span></label><label><input type="radio" name="op" value="both"><span><strong>Replace and add</strong><small>Replace the selected line with the first selected path, and add the remaining selected paths after it.</small></span></label></div><div class="checks" id="paths"></div><p class="muted small">New paths use <code>${UMBREL_ROOT}</code>. Added lines use the folder name as container target, for example <code>${UMBREL_ROOT}/home/Downloads/Films:/Films</code>.</p><button id="apply" disabled>Apply selected action</button><p id="status" class="muted"></p></div><div class="card"><h2>4. Restore a backup</h2><p class="muted small">Select a backup created by this app and restore it to docker-compose.yml. A safety backup of the current docker-compose.yml is created before restoring.</p><select id="backups"><option>Select an app first</option></select><button id="restore" disabled>Restore selected backup</button><p id="restoreStatus" class="muted"></p></div></main><script>const allowed = """ + paths + r""";const apps=document.getElementById('apps'),paths=document.getElementById('paths'),lines=document.getElementById('lines'),preview=document.getElementById('preview'),applyBtn=document.getElementById('apply'),status=document.getElementById('status'),backups=document.getElementById('backups'),restoreBtn=document.getElementById('restore'),restoreStatus=document.getElementById('restoreStatus');let current=[];function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}function op(){return document.querySelector('input[name=op]:checked').value;}function selectedPaths(){return Array.from(paths.querySelectorAll('input[type=checkbox]:checked')).map(o=>o.value);}function renderPaths(){paths.innerHTML=allowed.map((p,i)=>`<label class="check"><input type="checkbox" value="${esc(p)}" ${i==0?'checked':''}><span>${esc(p)}</span></label>`).join('');paths.querySelectorAll('input').forEach(x=>x.onchange=renderPreview);}async function loadBackups(){restoreStatus.textContent='';restoreBtn.disabled=true;const id=apps.value;if(!id){backups.innerHTML='<option value="">Select an app first</option>';return;}const r=await fetch('/api/backups?app_id='+encodeURIComponent(id));const data=await r.json();if(!r.ok){backups.innerHTML='<option value="">Could not load backups</option>';restoreStatus.innerHTML='<span class="err">Error:</span> '+esc(data.error||'Unknown');return;}backups.innerHTML=data.backups.length?data.backups.map(b=>`<option value="${esc(b.name)}">${esc(b.name)} - ${esc(b.size)} bytes</option>`).join(''):'<option value="">No backups found for this app</option>';restoreBtn.disabled=!data.backups.length;}async function loadApps(){const r=await fetch('/api/apps');const data=await r.json();apps.innerHTML=data.apps.length?data.apps.map(a=>`<option value="${esc(a.id)}">${esc(a.id)} - ${esc(a.path)}</option>`).join(''):'<option value="">No apps found</option>';await loadLines();await loadBackups();}async function loadLines(){status.textContent='';applyBtn.disabled=true;preview.textContent='Loading...';const id=apps.value;if(!id){preview.textContent='No app selected.';return;}const r=await fetch('/api/file?app_id='+encodeURIComponent(id));const data=await r.json();current=data.lines||[];lines.innerHTML=current.length?current.map(l=>`<option value="${l.no}">${l.no+1}: ${esc(l.text).slice(0,180)}</option>`).join(''):'<option>No replaceable or extendable volume lines found</option>';renderPreview();}async function refreshLineListPreserveResult(selectedNo){const id=apps.value;if(!id)return;const r=await fetch('/api/file?app_id='+encodeURIComponent(id));const data=await r.json();current=data.lines||[];lines.innerHTML=current.length?current.map(l=>`<option value="${l.no}">${l.no+1}: ${esc(l.text).slice(0,180)}</option>`).join(''):'<option>No replaceable or extendable volume lines found</option>';if(current.some(l=>l.no===selectedNo)){lines.value=String(selectedNo);}applyBtn.disabled=false;}function renderPreview(){const no=Number(lines.value);const item=current.find(x=>x.no===no);const chosen=selectedPaths();const mode=op();if(!item){preview.textContent='No volume line selected.';applyBtn.disabled=true;return;}let message='Selected line:\n'+item.text+'\n\nAction: '+({'replace':'Replace only','add':'Add only','both':'Replace and add'}[mode])+'\n\nSelected path(s):\n'+(chosen.length?chosen.join('\n'):'No path selected');let valid=false;if(mode==='replace'){valid=chosen.length===1;message+='\n\nResult: the selected line will be replaced. Nothing will be added.';}else if(mode==='add'){valid=chosen.length>=1;message+='\n\nResult: the selected line will stay unchanged. New line(s) will be added after it.';}else{valid=chosen.length>=1;message+='\n\nResult: the selected line will be replaced with the first selected path. Any extra selected paths will be added after it.';}preview.textContent=message;applyBtn.disabled=!valid;}document.querySelectorAll('input[name=op]').forEach(x=>x.onchange=renderPreview);apps.onchange=async()=>{await loadLines();await loadBackups();};lines.onchange=renderPreview;applyBtn.onclick=async()=>{const chosen=selectedPaths();const mode=op();status.textContent='Step 1/4: stopping the selected target app with /opt/umbreld/umbreld. This can take up to 45 seconds...';applyBtn.disabled=true;let payload={app_id:apps.value,line_no:Number(lines.value),operation:mode,paths:chosen};const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),90000);let r,data;try{r=await fetch('/api/modify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});data=await r.json();}catch(e){status.innerHTML='<span class="err">Error:</span> The request timed out or was interrupted while stopping/restarting the target app. The file was not confirmed changed. Check the app logs for the exact Umbrel command output.';applyBtn.disabled=false;return;}finally{clearTimeout(timer);}if(r.ok){let details=[];if(data.old&&data.new)details.push('Old line:\n'+data.old+'\n\nNew line:\n'+data.new);if(data.added&&data.added.length)details.push('Inserted line(s):\n'+data.added.join('\n'));status.innerHTML='<span class="ok">Target app restarted. Changes verified and saved.</span> I stopped the selected app, wrote the change, inspected docker-compose.yml, confirmed the selected change is present, and restarted the app. Backup: '+esc(data.backup);preview.textContent=(details.join('\n\n')||'Applied.')+'\n\nVerification: '+(data.verified_message||'The docker-compose.yml file was read back and the requested change was found.');await refreshLineListPreserveResult(Number(lines.value));await loadBackups();}else{status.innerHTML='<span class="err">Error:</span> '+esc(data.error||'Unknown');renderPreview();}};restoreBtn.onclick=async()=>{const backup=backups.value;if(!backup)return;restoreStatus.textContent='Step 1/4: stopping the selected target app with /opt/umbreld/umbreld. This can take up to 45 seconds...';restoreBtn.disabled=true;const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),90000);let r,data;try{r=await fetch('/api/restore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({app_id:apps.value,backup_name:backup}),signal:controller.signal});data=await r.json();}catch(e){restoreStatus.innerHTML='<span class="err">Error:</span> The request timed out or was interrupted while stopping/restarting the target app. The file was not confirmed changed. Check the app logs for the exact Umbrel command output.';restoreBtn.disabled=false;return;}finally{clearTimeout(timer);}if(r.ok){restoreStatus.innerHTML='<span class="ok">Target app restarted. Backup restored and verified.</span> I stopped the selected app, restored and verified the backup, and restarted the app. Restored '+esc(data.restored)+' to docker-compose.yml. Safety backup before restore: '+esc(data.safety_backup);preview.textContent='Restored backup:\n'+data.restored+'\n\nVerification: '+(data.verified_message||'docker-compose.yml now matches the selected backup.');await loadLines();await loadBackups();}else{restoreStatus.innerHTML='<span class="err">Error:</span> '+esc(data.error||'Unknown');restoreBtn.disabled=false;}};renderPaths();loadApps().catch(e=>{status.innerHTML='<span class="err">'+esc(String(e))+'</span>';});</script></body></html>"""

def run_umbreld_app_command(app_id, action):
  """Run the host-side Umbrel app control command with a hard timeout.

  The app runs in a container, so this uses chroot into the mounted host root
  and then runs exactly the Umbrel command that works on the host:
      /opt/umbreld/umbreld client apps.<action>.mutate --appId <app-id>
  """
  if action not in ('stop', 'restart'):
      raise ValueError('Invalid app control action')
  if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', app_id or ''):
      raise ValueError('Invalid app id')
  if app_id == SELF_APP_ID:
      raise ValueError('Refusing to stop or restart this editor app')
  if not Path('/hostfs/opt/umbreld/umbreld').exists():
      raise RuntimeError('Host /opt/umbreld/umbreld was not found at /hostfs/opt/umbreld/umbreld')

  subcommand = 'apps.%s.mutate' % action
  base_command = '/opt/umbreld/umbreld client ' + subcommand + ' --appId ' + app_id
  host_path = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/umbreld/bin:/opt/umbreld'
  # Add host-side timeout too. If umbreld/tsx hangs, this returns instead of leaving the UI waiting forever.
  host_shell_command = (
      'cd /home/umbrel 2>/dev/null || cd /; '
      'export PATH="' + host_path + '"; '
      'export HOME=/home/umbrel USER=umbrel LOGNAME=umbrel TMPDIR=/tmp; '
      'if command -v timeout >/dev/null 2>&1; then timeout 45s ' + base_command + '; else ' + base_command + '; fi'
  )
  candidates = [
      ['/usr/sbin/chroot', '/hostfs', '/bin/sh', '-lc', host_shell_command],
      ['chroot', '/hostfs', '/bin/sh', '-lc', host_shell_command],
  ]
  env = os.environ.copy()
  env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:' + env.get('PATH', '')
  errors = []
  for cmd in candidates:
      try:
          completed = subprocess.run(cmd, cwd='/', env=env, text=True, capture_output=True, timeout=55, stdin=subprocess.DEVNULL)
      except FileNotFoundError:
          errors.append('not found: ' + cmd[0])
          continue
      except subprocess.TimeoutExpired as e:
          errors.append('timeout after 55s: ' + ' '.join(cmd) + ' :: stdout=' + ((e.stdout or '') if isinstance(e.stdout, str) else '').strip() + ' stderr=' + ((e.stderr or '') if isinstance(e.stderr, str) else '').strip())
          continue
      except Exception as e:
          errors.append('failed to run ' + ' '.join(cmd) + ': ' + str(e))
          continue
      stdout = (completed.stdout or '').strip()
      stderr = (completed.stderr or '').strip()
      if completed.returncode == 0:
          return {'action': action, 'command': base_command, 'stdout': stdout, 'stderr': stderr}
      output = (stderr + '\n' + stdout).strip()
      if completed.returncode == 124:
          errors.append('host command timed out after 45s: ' + base_command + ' :: ' + output)
      else:
          errors.append('command failed with exit code %s: %s :: %s' % (completed.returncode, ' '.join(cmd), output))
  raise RuntimeError('Could not run /opt/umbreld/umbreld client apps.%s.mutate for %s. Tried: %s' % (action, app_id, ' | '.join(errors)))

def run_with_target_app_restart(app_id, change_func):
  stop_result = run_umbreld_app_command(app_id, 'stop')
  change_result = None
  change_error = None
  try:
      change_result = change_func()
  except Exception as e:
      change_error = e
  start_result = None
  start_error = None
  try:
      start_result = run_umbreld_app_command(app_id, 'restart')
  except Exception as e:
      start_error = e

  if change_error is not None:
      msg = 'Target app was stopped, but the compose change failed: %s' % change_error
      if start_error is not None:
          msg += ' Also failed to restart the target app: %s' % start_error
      else:
          msg += ' The target app was restarted.'
      raise RuntimeError(msg)
  if start_error is not None:
      raise RuntimeError('Compose change was written and verified, but restarting the target app failed: %s. Backup: %s' % (start_error, change_result.get('backup', 'unknown')))

  change_result['app_control'] = {
      'stopped': True,
      'restarted': True,
      'stop_command': stop_result.get('command'),
      'restart_command': start_result.get('command'),
  }
  change_result['verified_message'] = (change_result.get('verified_message') or 'Verified.') + ' The target app was stopped before writing and restarted after verification.'
  return change_result

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

def verify_file_contains(path, expected_lines, label):
  content = path.read_text(encoding='utf-8', errors='replace')
  missing = [line.rstrip('\n') for line in expected_lines if line.rstrip('\n') not in content]
  if missing:
      raise RuntimeError(f'Change was written, but verification failed: missing {label}. Restore from the backup before trying again.')
  return True

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
  verify_file_contains(p, [new], 'replacement')
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
  verify_file_contains(p, added, 'added line(s)')
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
  expected=[]
  if new:
      expected.append(new)
  expected.extend(added)
  verify_file_contains(p, expected, 'requested change(s)')
  return {
      'backup': str(backup).replace(str(ROOT), '${UMBREL_ROOT}'),
      'old': old,
      'new': new.rstrip('\n') if new else None,
      'added': [x.rstrip('\n') for x in added],
      'verified': True,
      'verified_message': 'The docker-compose.yml file was read back after saving, and the requested change(s) were found.',
  }


def list_backups(app_id):
  p=safe_compose(app_id)
  backups=[]
  for b in sorted(p.parent.glob('docker-compose.yml.bak-*'), key=lambda x: x.name, reverse=True):
      if not b.is_file():
          continue
      backups.append({'name': b.name, 'size': b.stat().st_size})
  return backups

def restore_backup(app_id, backup_name):
  if not re.fullmatch(r'docker-compose[.]yml[.]bak-[0-9]{8}-[0-9]{6}', backup_name or ''):
      raise ValueError('Invalid backup name')
  p=safe_compose(app_id)
  backup=(p.parent / backup_name).resolve()
  if p.parent not in backup.parents or not backup.exists() or not backup.is_file():
      raise FileNotFoundError('Selected backup not found')
  backup_content=backup.read_bytes()
  safety=p.with_name('docker-compose.yml.bak-before-restore-' + time.strftime('%Y%m%d-%H%M%S'))
  shutil.copy2(p, safety)
  tmp=p.with_suffix('.yml.restore-tmp')
  tmp.write_bytes(backup_content)
  tmp.replace(p)
  if p.read_bytes() != backup_content:
      raise RuntimeError('Restore was written, but verification failed: docker-compose.yml does not match the selected backup')
  return {
      'restored': backup.name,
      'safety_backup': str(safety).replace(str(ROOT), '${UMBREL_ROOT}'),
      'verified': True,
      'verified_message': 'docker-compose.yml was read back after restoring and matches the selected backup exactly.',
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
          elif u.path == '/api/backups': self.send_json({'backups': list_backups(parse_qs(u.query).get('app_id',[''])[0])})
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
          elif path == '/api/modify':
              app_id = body.get('app_id')
              result = run_with_target_app_restart(app_id, lambda: modify_paths(app_id, body.get('line_no'), body.get('operation'), body.get('paths')))
              self.send_json(result)
          elif path == '/api/restore':
              app_id = body.get('app_id')
              result = run_with_target_app_restart(app_id, lambda: restore_backup(app_id, body.get('backup_name')))
              self.send_json(result)
          else:
              self.send_json({'error':'Not found'},404)
      except Exception as e: self.send_json({'error':str(e)},400)
  def log_message(self, fmt, *args): print('%s - %s' % (self.address_string(), fmt%args), flush=True)

ThreadingHTTPServer(('0.0.0.0', 8099), Handler).serve_forever()