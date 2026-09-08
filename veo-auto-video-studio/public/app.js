const $ = selector => document.querySelector(selector);
const state = { items: [], refs: [], running: 0, paused: false, generationToken: 0 };

const promptInput = $('#promptInput');
const detected = $('#detected');
const queue = $('#queue');
const queueSummary = $('#queueSummary');
const referenceInput = $('#referenceInput');
const referencePreview = $('#referencePreview');

function parsePrompts() {
  const lines = promptInput.value.split(/\r?\n/);
  return ($('#ignoreEmpty').checked ? lines.filter(line => line.trim()) : lines).map(line => line.trim());
}
function updateDetected() { detected.textContent = `${parsePrompts().length} prompts`; }
promptInput.addEventListener('input', updateDetected);
$('#ignoreEmpty').addEventListener('change', updateDetected);

async function checkApi() {
  const el = $('#apiState');
  try {
    const data = await fetch('/api/health').then(r => r.json());
    el.textContent = data.apiConfigured ? '● Veo API đã sẵn sàng' : '● Chưa cấu hình GEMINI_API_KEY';
    el.className = `api-state ${data.apiConfigured ? 'ok' : 'bad'}`;
  } catch {
    el.textContent = '● Không kết nối được server';
    el.className = 'api-state bad';
  }
}

referenceInput.addEventListener('change', () => {
  const chosen = [...referenceInput.files];
  for (const file of chosen) {
    if (state.refs.length >= 3) break;
    if (!state.refs.some(x => x.name === file.name && x.size === file.size)) state.refs.push(file);
  }
  referenceInput.value = '';
  renderRefs();
});
function renderRefs() {
  referencePreview.innerHTML = '';
  state.refs.forEach((file, i) => {
    const wrap = document.createElement('div'); wrap.className = 'ref-item';
    const img = document.createElement('img'); img.src = URL.createObjectURL(file);
    const btn = document.createElement('button'); btn.textContent = '×';
    btn.onclick = () => { state.refs.splice(i, 1); renderRefs(); };
    wrap.append(img, btn); referencePreview.append(wrap);
  });
}

$('#importBtn').addEventListener('click', () => {
  const prompts = parsePrompts();
  state.items = prompts.map((prompt, i) => ({ localId: crypto.randomUUID(), index: i + 1, prompt, status: 'READY', jobId: null, videoUrl: null, error: null }));
  state.paused = false;
  state.generationToken++;
  renderQueue();
});
$('#clearBtn').addEventListener('click', () => { state.items = []; state.generationToken++; renderQueue(); });
$('#pauseBtn').addEventListener('click', e => { state.paused = !state.paused; e.currentTarget.textContent = state.paused ? '▶ RESUME' : 'Ⅱ PAUSE'; if (!state.paused) pump(); });
$('#retryBtn').addEventListener('click', () => { state.items.filter(x => x.status === 'ERROR').forEach(x => { x.status = 'READY'; x.error = null; x.jobId = null; }); renderQueue(); pump(); });
$('#generateBtn').addEventListener('click', () => { state.paused = false; $('#pauseBtn').textContent = 'Ⅱ PAUSE'; pump(); });

function currentSettings() {
  return { model: $('#model').value, aspectRatio: $('#ratio').value, resolution: $('#resolution').value, durationSeconds: $('#duration').value };
}
function statusLabel(status) { return ({ READY:'Ready', WAITING:'Waiting', GENERATING:'Generating', COMPLETED:'Done', ERROR:'Error' })[status] || status; }
function renderQueue() {
  queue.innerHTML = '';
  queue.classList.toggle('empty', state.items.length === 0);
  if (!state.items.length) queue.textContent = 'Dán prompt bên trái rồi bấm IMPORT PROMPTS.';
  const done = state.items.filter(x => x.status === 'COMPLETED').length;
  queueSummary.textContent = state.items.length ? `${state.items.length} video · ${done} hoàn tất` : 'Chưa có prompt';
  state.items.forEach(item => {
    const node = $('#jobTemplate').content.firstElementChild.cloneNode(true);
    node.dataset.id = item.localId;
    node.querySelector('.job-index').textContent = `#${String(item.index).padStart(2, '0')}`;
    node.querySelector('.job-prompt').textContent = item.prompt;
    node.querySelector('.job-meta').textContent = `${$('#model').selectedOptions[0].text} · ${$('#ratio').value} · ${$('#resolution').value}`;
    const status = node.querySelector('.status'); status.textContent = statusLabel(item.status); status.className = `status ${item.status}`;
    const video = node.querySelector('.result'); if (item.videoUrl) { video.src = item.videoUrl; video.classList.add('show'); }
    const error = node.querySelector('.error-text'); if (item.error) { error.textContent = item.error; error.classList.add('show'); }
    node.querySelector('.remove').onclick = () => { if (item.status === 'GENERATING') return; state.items = state.items.filter(x => x.localId !== item.localId); reindex(); renderQueue(); };
    queue.append(node);
  });
}
function reindex() { state.items.forEach((x, i) => x.index = i + 1); }

async function createJob(item) {
  item.status = 'WAITING'; item.error = null; renderQueue();
  const settings = currentSettings();
  const form = new FormData();
  form.set('prompt', item.prompt); form.set('index', item.index);
  Object.entries(settings).forEach(([k,v]) => form.set(k,v));
  if ($('#applyRefs').checked) state.refs.forEach(file => form.append('references', file, file.name));
  const response = await fetch('/api/jobs', { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Không tạo được job.');
  item.jobId = data.id; item.status = data.status; renderQueue();
  return data.id;
}
async function pollJob(item, token) {
  while (token === state.generationToken) {
    await new Promise(resolve => setTimeout(resolve, 5000));
    const response = await fetch(`/api/jobs/${item.jobId}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Không đọc được trạng thái job.');
    item.status = data.status; item.videoUrl = data.videoUrl; item.error = data.error; renderQueue();
    if (data.status === 'COMPLETED' || data.status === 'ERROR') return;
  }
}
async function processItem(item, token) {
  state.running++;
  try { await createJob(item); await pollJob(item, token); }
  catch (error) { item.status = 'ERROR'; item.error = error.message; renderQueue(); }
  finally { state.running--; pump(token); }
}
function pump(token = state.generationToken) {
  if (state.paused) return;
  const max = Number($('#concurrency').value || 1);
  while (state.running < max) {
    const next = state.items.find(x => x.status === 'READY');
    if (!next) break;
    processItem(next, token);
  }
}

['model','ratio','resolution','duration'].forEach(id => $(`#${id}`).addEventListener('change', renderQueue));
updateDetected(); renderQueue(); checkApi();
