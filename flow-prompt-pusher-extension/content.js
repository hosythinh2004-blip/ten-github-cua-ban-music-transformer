(() => {
  'use strict';

  if (window.__FLOW_PROMPT_PUSHER_LOADED__) return;
  window.__FLOW_PROMPT_PUSHER_LOADED__ = true;

  const ROOT_ID = 'fpp-root';
  const state = {
    prompts: [],
    current: 0,
    running: false,
    paused: false,
    stopRequested: false,
    promptTarget: null,
    generateTarget: null,
    picker: null,
  };

  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const visible = (el) => {
    if (!el || !el.isConnected) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 20 && rect.height > 10 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0;
  };
  const insidePanel = (el) => Boolean(el?.closest?.(`#${ROOT_ID}`));
  const norm = (v) => String(v || '').trim().toLowerCase();

  function labelText(el) {
    return norm([
      el?.innerText,
      el?.textContent,
      el?.getAttribute?.('aria-label'),
      el?.getAttribute?.('placeholder'),
      el?.getAttribute?.('title'),
    ].filter(Boolean).join(' '));
  }

  function promptScore(el) {
    if (!visible(el) || insidePanel(el)) return -Infinity;
    const rect = el.getBoundingClientRect();
    const text = labelText(el);
    let score = Math.min(rect.width * rect.height / 5000, 120);
    if (/prompt|describe|scene|mô tả|câu lệnh|ý tưởng/.test(text)) score += 120;
    if (el.tagName === 'TEXTAREA') score += 80;
    if (el.getAttribute('role') === 'textbox') score += 45;
    if (el.isContentEditable) score += 50;
    if (rect.top > window.innerHeight * 0.45) score += 25;
    if (/search|tìm kiếm|comment|chat/.test(text)) score -= 120;
    return score;
  }

  function findPromptTarget() {
    if (visible(state.promptTarget)) return state.promptTarget;
    const candidates = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
      .filter(el => !insidePanel(el));
    candidates.sort((a, b) => promptScore(b) - promptScore(a));
    return candidates[0] || null;
  }

  function generateScore(el) {
    if (!visible(el) || insidePanel(el)) return -Infinity;
    const text = labelText(el);
    let score = 0;
    if (/generate video|generate image|generate/.test(text)) score += 220;
    if (/tạo video|tạo hình ảnh|tạo ảnh|tạo$/.test(text)) score += 220;
    if (/render/.test(text)) score += 100;
    if (el.tagName === 'BUTTON') score += 35;
    const rect = el.getBoundingClientRect();
    if (rect.top > window.innerHeight * 0.4) score += 20;
    if (/cancel|stop|delete|xóa|download|tải/.test(text)) score -= 300;
    return score;
  }

  function findGenerateTarget() {
    if (visible(state.generateTarget)) return state.generateTarget;
    const candidates = [...document.querySelectorAll('button, [role="button"]')]
      .filter(el => !insidePanel(el));
    candidates.sort((a, b) => generateScore(b) - generateScore(a));
    return candidates.find(el => generateScore(el) >= 150) || null;
  }

  function isDisabled(el) {
    return !el || el.disabled === true || el.getAttribute?.('aria-disabled') === 'true';
  }

  function setPrompt(el, value) {
    if (!el) throw new Error('Không tìm thấy ô prompt của Flow.');
    el.focus();

    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) setter.call(el, value);
      else el.value = value;
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }

    if (el.isContentEditable || el.getAttribute('role') === 'textbox') {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(el);
      selection.removeAllRanges();
      selection.addRange(range);
      let inserted = false;
      try { inserted = document.execCommand('insertText', false, value); } catch (_) {}
      if (!inserted || !norm(el.innerText).includes(norm(value).slice(0, 20))) {
        el.textContent = value;
      }
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }

    throw new Error('Ô đã chọn không phải trường nhập prompt.');
  }

  async function waitForGenerateReady(timeoutMs = 30000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (state.stopRequested) return null;
      const button = findGenerateTarget();
      if (button && !isDisabled(button)) return button;
      await sleep(500);
    }
    return null;
  }

  function parsePrompts() {
    return ui.prompts.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  }

  function setStatus(message, type = '') {
    ui.status.textContent = message;
    ui.status.dataset.type = type;
  }

  function refreshCounts() {
    const parsed = parsePrompts();
    ui.detected.textContent = `${parsed.length} prompt`;
    if (!state.running) ui.progress.textContent = `0 / ${parsed.length}`;
  }

  function saveSettings() {
    chrome.storage.local.set({
      fppPrompts: ui.prompts.value,
      fppDelay: ui.delay.value,
      fppWaitReady: ui.waitReady.checked,
      fppCollapsed: ui.body.hidden,
    });
  }

  async function loadSettings() {
    const saved = await chrome.storage.local.get(['fppPrompts', 'fppDelay', 'fppWaitReady', 'fppCollapsed']);
    if (typeof saved.fppPrompts === 'string') ui.prompts.value = saved.fppPrompts;
    if (saved.fppDelay) ui.delay.value = saved.fppDelay;
    if (typeof saved.fppWaitReady === 'boolean') ui.waitReady.checked = saved.fppWaitReady;
    if (saved.fppCollapsed === true) ui.body.hidden = true;
    refreshCounts();
  }

  function describeTarget(el) {
    if (!el) return 'chưa chọn';
    const text = labelText(el).slice(0, 48);
    return `${el.tagName.toLowerCase()}${text ? ` · ${text}` : ''}`;
  }

  function highlight(el) {
    if (!el) return;
    const old = el.style.outline;
    el.style.outline = '3px solid #7c5cff';
    setTimeout(() => { if (el?.style) el.style.outline = old; }, 1500);
  }

  function beginPick(type) {
    state.picker = type;
    document.addEventListener('click', pickHandler, true);
    setStatus(type === 'prompt'
      ? 'Bây giờ hãy click trực tiếp vào ô nhập prompt của Flow.'
      : 'Bây giờ hãy click trực tiếp vào nút Generate của Flow.', 'pick');
  }

  function pickHandler(event) {
    if (!state.picker || insidePanel(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const target = event.target.closest?.('button, [role="button"], textarea, [contenteditable="true"], [role="textbox"]') || event.target;
    if (state.picker === 'prompt') {
      state.promptTarget = target;
      ui.promptTarget.textContent = describeTarget(target);
      highlight(target);
      setStatus('Đã chọn ô prompt.', 'ok');
    } else {
      state.generateTarget = target;
      ui.generateTarget.textContent = describeTarget(target);
      highlight(target);
      setStatus('Đã chọn nút Generate.', 'ok');
    }
    state.picker = null;
    document.removeEventListener('click', pickHandler, true);
  }

  function autoDetect() {
    state.promptTarget = findPromptTarget();
    state.generateTarget = findGenerateTarget();
    ui.promptTarget.textContent = describeTarget(state.promptTarget);
    ui.generateTarget.textContent = describeTarget(state.generateTarget);
    if (state.promptTarget && state.generateTarget) {
      highlight(state.promptTarget);
      highlight(state.generateTarget);
      setStatus('Đã nhận diện ô prompt và nút Generate.', 'ok');
    } else {
      setStatus('Chưa nhận diện đủ. Dùng hai nút “Chọn…” bên dưới.', 'warn');
    }
  }

  async function runQueue() {
    if (state.running) return;
    state.prompts = parsePrompts();
    if (!state.prompts.length) return setStatus('Bạn chưa dán prompt.', 'warn');

    state.promptTarget = findPromptTarget();
    state.generateTarget = findGenerateTarget();
    ui.promptTarget.textContent = describeTarget(state.promptTarget);
    ui.generateTarget.textContent = describeTarget(state.generateTarget);
    if (!state.promptTarget) return setStatus('Không tìm thấy ô prompt. Bấm “Chọn ô Prompt”.', 'warn');
    if (!state.generateTarget) return setStatus('Không tìm thấy nút Generate. Bấm “Chọn nút Generate”.', 'warn');

    state.running = true;
    state.paused = false;
    state.stopRequested = false;
    state.current = 0;
    ui.start.disabled = true;
    ui.pause.disabled = false;
    ui.stop.disabled = false;
    ui.pause.textContent = 'Tạm dừng';

    try {
      for (let i = 0; i < state.prompts.length; i++) {
        state.current = i;
        while (state.paused && !state.stopRequested) await sleep(250);
        if (state.stopRequested) break;

        const promptBox = findPromptTarget();
        if (!promptBox) throw new Error('Flow không còn thấy ô prompt. Hãy chọn lại ô Prompt.');
        setStatus(`Đang gửi prompt ${i + 1}/${state.prompts.length}…`);
        ui.progress.textContent = `${i + 1} / ${state.prompts.length}`;
        setPrompt(promptBox, state.prompts[i]);
        await sleep(450);

        let button = findGenerateTarget();
        if (!button || isDisabled(button)) {
          button = await waitForGenerateReady();
        }
        if (!button) throw new Error('Nút Generate chưa sẵn sàng. Tool đã dừng để tránh gửi sai.');

        button.click();
        setStatus(`Đã đẩy prompt ${i + 1}/${state.prompts.length}.`, 'ok');

        const delaySeconds = Math.max(1, Math.min(120, Number(ui.delay.value) || 5));
        await sleep(delaySeconds * 1000);
        if (ui.waitReady.checked && i < state.prompts.length - 1) {
          const ready = await waitForGenerateReady(45000);
          if (!ready) throw new Error('Flow chưa sẵn sàng nhận prompt tiếp theo sau 45 giây.');
        }
      }

      if (state.stopRequested) setStatus(`Đã dừng tại prompt ${state.current + 1}.`, 'warn');
      else setStatus(`Hoàn tất: đã đẩy ${state.prompts.length} prompt vào Flow.`, 'ok');
    } catch (error) {
      setStatus(error.message || String(error), 'error');
    } finally {
      state.running = false;
      state.paused = false;
      ui.start.disabled = false;
      ui.pause.disabled = true;
      ui.stop.disabled = true;
      ui.pause.textContent = 'Tạm dừng';
    }
  }

  const root = document.createElement('aside');
  root.id = ROOT_ID;
  root.innerHTML = `
    <div class="fpp-head">
      <div><strong>FLOW PROMPT PUSHER</strong><small>1 dòng = 1 prompt</small></div>
      <button class="fpp-icon" id="fpp-toggle" title="Thu gọn">—</button>
    </div>
    <div class="fpp-body">
      <textarea id="fpp-prompts" placeholder="Prompt video 1\nPrompt video 2\nPrompt video 3"></textarea>
      <div class="fpp-row fpp-meta"><span id="fpp-detected">0 prompt</span><span id="fpp-progress">0 / 0</span></div>
      <div class="fpp-grid">
        <button id="fpp-autodetect">Tự nhận diện</button>
        <button id="fpp-pick-prompt">Chọn ô Prompt</button>
        <button id="fpp-pick-generate">Chọn nút Generate</button>
      </div>
      <div class="fpp-target"><b>Prompt:</b> <span id="fpp-prompt-target">chưa chọn</span></div>
      <div class="fpp-target"><b>Generate:</b> <span id="fpp-generate-target">chưa chọn</span></div>
      <div class="fpp-row fpp-options">
        <label>Khoảng cách <input id="fpp-delay" type="number" min="1" max="120" value="5"> giây</label>
        <label><input id="fpp-wait-ready" type="checkbox" checked> Đợi Flow sẵn sàng</label>
      </div>
      <div class="fpp-actions">
        <button class="fpp-primary" id="fpp-start">▶ BẮT ĐẦU</button>
        <button id="fpp-pause" disabled>Tạm dừng</button>
        <button id="fpp-stop" disabled>Dừng</button>
      </div>
      <div id="fpp-status" class="fpp-status">Mở project Flow, cài model/ảnh tham chiếu trước rồi chạy.</div>
      <div class="fpp-note">Extension không đăng nhập thay bạn, không đọc mật khẩu/cookie và không gọi Veo API. Nó chỉ thao tác ô prompt + nút Generate trên trang Flow bạn đang mở.</div>
    </div>`;
  document.documentElement.appendChild(root);

  const ui = {
    body: root.querySelector('.fpp-body'),
    prompts: root.querySelector('#fpp-prompts'),
    detected: root.querySelector('#fpp-detected'),
    progress: root.querySelector('#fpp-progress'),
    delay: root.querySelector('#fpp-delay'),
    waitReady: root.querySelector('#fpp-wait-ready'),
    promptTarget: root.querySelector('#fpp-prompt-target'),
    generateTarget: root.querySelector('#fpp-generate-target'),
    status: root.querySelector('#fpp-status'),
    start: root.querySelector('#fpp-start'),
    pause: root.querySelector('#fpp-pause'),
    stop: root.querySelector('#fpp-stop'),
  };

  root.querySelector('#fpp-toggle').addEventListener('click', () => {
    ui.body.hidden = !ui.body.hidden;
    saveSettings();
  });
  root.querySelector('#fpp-autodetect').addEventListener('click', autoDetect);
  root.querySelector('#fpp-pick-prompt').addEventListener('click', () => beginPick('prompt'));
  root.querySelector('#fpp-pick-generate').addEventListener('click', () => beginPick('generate'));
  ui.start.addEventListener('click', runQueue);
  ui.pause.addEventListener('click', () => {
    if (!state.running) return;
    state.paused = !state.paused;
    ui.pause.textContent = state.paused ? 'Tiếp tục' : 'Tạm dừng';
    setStatus(state.paused ? 'Đã tạm dừng hàng đợi.' : 'Đang tiếp tục…', state.paused ? 'warn' : '');
  });
  ui.stop.addEventListener('click', () => { state.stopRequested = true; state.paused = false; });
  ui.prompts.addEventListener('input', () => { refreshCounts(); saveSettings(); });
  ui.delay.addEventListener('change', saveSettings);
  ui.waitReady.addEventListener('change', saveSettings);

  loadSettings().then(() => setTimeout(autoDetect, 1200));
})();
