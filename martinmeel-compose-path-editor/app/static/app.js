const state = {
  apps: [],
  paths: [],
  lines: [],
  selectedFile: null,
  selectedPath: null,
  selectedLineNumber: null,
  hasPreview: false,
};

const el = {
  appSelect: document.getElementById('appSelect'),
  pathSelect: document.getElementById('pathSelect'),
  selectedFile: document.getElementById('selectedFile'),
  lineList: document.getElementById('lineList'),
  recommendedOnly: document.getElementById('recommendedOnly'),
  refreshButton: document.getElementById('refreshButton'),
  previewButton: document.getElementById('previewButton'),
  applyButton: document.getElementById('applyButton'),
  status: document.getElementById('status'),
  diff: document.getElementById('diff'),
};

function setStatus(message, kind = '') {
  el.status.textContent = message;
  el.status.className = `status ${kind}`.trim();
}

function showDiff(text) {
  el.diff.textContent = text || '';
  el.diff.classList.toggle('visible', Boolean(text));
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function renderApps() {
  el.appSelect.innerHTML = '';
  if (state.apps.length === 0) {
    const option = document.createElement('option');
    option.textContent = 'Geen docker-compose.yml bestanden gevonden';
    option.value = '';
    el.appSelect.append(option);
    state.selectedFile = null;
    return;
  }

  for (const app of state.apps) {
    const option = document.createElement('option');
    option.value = app.file;
    option.textContent = `${app.installed ? '✓ ' : ''}${app.label}`;
    option.title = app.displayPath;
    el.appSelect.append(option);
  }

  state.selectedFile = el.appSelect.value || state.apps[0].file;
  const selected = state.apps.find((item) => item.file === state.selectedFile);
  el.selectedFile.textContent = selected ? selected.displayPath : '';
}

function renderPaths() {
  el.pathSelect.innerHTML = '';
  for (const path of state.paths) {
    const option = document.createElement('option');
    option.value = path;
    option.textContent = path;
    el.pathSelect.append(option);
  }
  state.selectedPath = el.pathSelect.value || state.paths[0];
}

function renderLines() {
  el.lineList.innerHTML = '';
  state.hasPreview = false;
  el.applyButton.disabled = true;
  showDiff('');

  const onlyRecommended = el.recommendedOnly.checked;
  const visibleLines = state.lines.filter((line) => {
    if (!line.selectable) return false;
    if (onlyRecommended && !line.recommended) return false;
    return true;
  });

  if (visibleLines.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = onlyRecommended
      ? 'Geen aanbevolen regels gevonden. Zet “Toon alleen aanbevolen regels” uit om alle niet-lege regels te bekijken.'
      : 'Geen selecteerbare regels gevonden.';
    el.lineList.append(empty);
    return;
  }

  for (const line of visibleLines) {
    const wrapper = document.createElement('label');
    wrapper.className = `line ${line.number === state.selectedLineNumber ? 'selected' : ''}`;

    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'composeLine';
    radio.value = String(line.number);
    radio.checked = line.number === state.selectedLineNumber;
    radio.addEventListener('change', () => {
      state.selectedLineNumber = line.number;
      renderLines();
    });

    const text = document.createElement('div');
    text.innerHTML = `<div class="line-number">regel ${line.number}</div><div class="line-text"></div>`;
    text.querySelector('.line-text').textContent = line.text || ' ';

    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = line.volumeLike ? 'volume' : line.recommended ? 'pad' : 'regel';

    wrapper.append(radio, text, badge);
    el.lineList.append(wrapper);
  }
}

async function loadFile(file) {
  if (!file) return;
  state.selectedLineNumber = null;
  setStatus('Compose-bestand wordt geladen...');
  showDiff('');
  try {
    const data = await api(`/api/file?file=${encodeURIComponent(file)}`);
    state.lines = data.lines;
    el.selectedFile.textContent = data.displayPath;

    const firstRecommended = state.lines.find((line) => line.selectable && line.recommended);
    const firstSelectable = state.lines.find((line) => line.selectable);
    state.selectedLineNumber = (firstRecommended || firstSelectable || {}).number || null;

    renderLines();
    setStatus(state.selectedLineNumber ? 'Kies eventueel een andere regel en toon daarna het voorbeeld.' : 'Geen selecteerbare regel gevonden.', state.selectedLineNumber ? '' : 'error');
  } catch (error) {
    state.lines = [];
    renderLines();
    setStatus(error.message, 'error');
  }
}

async function refreshAll() {
  setStatus('Apps worden opgehaald...');
  showDiff('');
  el.applyButton.disabled = true;
  try {
    const [appsData, pathsData] = await Promise.all([api('/api/apps'), api('/api/paths')]);
    state.apps = appsData.apps;
    state.paths = pathsData.paths;
    renderApps();
    renderPaths();
    await loadFile(state.selectedFile);
  } catch (error) {
    setStatus(error.message, 'error');
  }
}

async function previewChange() {
  if (!state.selectedFile || !state.selectedPath || !state.selectedLineNumber) {
    setStatus('Kies eerst een bestand, pad en regel.', 'error');
    return;
  }

  setStatus('Voorbeeld wordt gemaakt...');
  el.applyButton.disabled = true;
  state.hasPreview = false;

  try {
    const data = await api('/api/preview', {
      method: 'POST',
      body: JSON.stringify({
        file: state.selectedFile,
        newPath: state.selectedPath,
        lineNumber: state.selectedLineNumber,
      }),
    });
    showDiff(data.diff);
    state.hasPreview = true;
    el.applyButton.disabled = false;
    setStatus(`Voorbeeld klaar: ${data.mode}.`, 'ok');
  } catch (error) {
    showDiff('');
    setStatus(error.message, 'error');
  }
}

async function applyChange() {
  if (!state.hasPreview) {
    setStatus('Toon eerst het voorbeeld voordat je de wijziging toepast.', 'error');
    return;
  }

  const confirmed = window.confirm('Weet je zeker dat je deze docker-compose.yml wilt aanpassen? Er wordt eerst een backup gemaakt.');
  if (!confirmed) return;

  setStatus('Wijziging wordt opgeslagen...');
  el.applyButton.disabled = true;

  try {
    const data = await api('/api/apply', {
      method: 'POST',
      body: JSON.stringify({
        file: state.selectedFile,
        newPath: state.selectedPath,
        lineNumber: state.selectedLineNumber,
      }),
    });
    setStatus(`${data.message} Backup: ${data.backupPath}`, 'ok');
    await loadFile(state.selectedFile);
  } catch (error) {
    setStatus(error.message, 'error');
  }
}

el.refreshButton.addEventListener('click', refreshAll);
el.previewButton.addEventListener('click', previewChange);
el.applyButton.addEventListener('click', applyChange);
el.recommendedOnly.addEventListener('change', renderLines);
el.appSelect.addEventListener('change', async () => {
  state.selectedFile = el.appSelect.value;
  const selected = state.apps.find((item) => item.file === state.selectedFile);
  el.selectedFile.textContent = selected ? selected.displayPath : '';
  await loadFile(state.selectedFile);
});
el.pathSelect.addEventListener('change', () => {
  state.selectedPath = el.pathSelect.value;
  state.hasPreview = false;
  el.applyButton.disabled = true;
  showDiff('');
});

document.addEventListener('DOMContentLoaded', refreshAll);
