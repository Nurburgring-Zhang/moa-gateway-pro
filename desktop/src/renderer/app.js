'use strict';

/**
 * app.js — renderer controller for MOA Gateway Desktop.
 *
 * Talks to the main process exclusively through window.moaGateway
 * (see preload.js). Two views:
 *   - console: iframe embedding the gateway webui when the service is ready,
 *     real waiting page (with live startup log) otherwise
 *   - service: status card, controls, log panel, settings, API keys, system
 */

const api = window.moaGateway;

const STATE_LABELS = {
  stopped: '已停止',
  starting: '启动中',
  running: '运行中',
  degraded: '降级（健康检查失败）',
  stopping: '停止中',
  backoff: '崩溃，等待自动重启',
  failed: '启动失败',
};

const $ = (sel) => document.querySelector(sel);

const els = {
  pill: $('#status-pill'),
  pillText: $('#status-pill-text'),
  statusMeta: $('#status-meta'),
  btnOpenBrowser: $('#btn-open-browser'),
  navItems: Array.from(document.querySelectorAll('.nav-item')),
  navLight: $('#nav-service-light'),
  navVersion: $('#nav-version'),
  views: { console: $('#view-console'), service: $('#view-service') },
  frame: $('#console-frame'),
  waiting: $('#console-waiting'),
  waitingSpinner: $('#waiting-spinner'),
  waitingTitle: $('#waiting-title'),
  waitingDetail: $('#waiting-detail'),
  waitingError: $('#waiting-error'),
  waitingActions: $('#waiting-actions'),
  btnWaitingStart: $('#btn-waiting-start'),
  btnWaitingService: $('#btn-waiting-service'),
  waitingLog: $('#waiting-log'),
  waitingLogCount: $('#waiting-log-count'),
  btnStart: $('#btn-start'),
  btnStop: $('#btn-stop'),
  btnRestart: $('#btn-restart'),
  svcLight: $('#svc-light'),
  svcState: $('#svc-state'),
  svcStateSub: $('#svc-state-sub'),
  mUrl: $('#m-url'),
  mPort: $('#m-port'),
  mPid: $('#m-pid'),
  mUptime: $('#m-uptime'),
  mRestarts: $('#m-restarts'),
  mHealth: $('#m-health'),
  mPython: $('#m-python'),
  mRepo: $('#m-repo'),
  mErrorRow: $('#m-error-row'),
  mError: $('#m-error'),
  logPanel: $('#log-panel'),
  logCount: $('#log-count'),
  logAutoscroll: $('#log-autoscroll'),
  btnLogClear: $('#btn-log-clear'),
  btnLogExport: $('#btn-log-export'),
  btnProbe: $('#btn-probe'),
  probeResult: $('#probe-result'),
  btnSaveConfig: $('#btn-save-config'),
  cfgPython: $('#cfg-python'),
  cfgRepo: $('#cfg-repo'),
  cfgHost: $('#cfg-host'),
  cfgPort: $('#cfg-port'),
  cfgReadyTimeout: $('#cfg-ready-timeout'),
  cfgMaxLines: $('#cfg-max-lines'),
  cfgAutostart: $('#cfg-autostart'),
  cfgRestartEnabled: $('#cfg-restart-enabled'),
  configNote: $('#config-note'),
  secretsUnavailable: $('#secrets-unavailable'),
  secretName: $('#secret-name'),
  secretValue: $('#secret-value'),
  btnSecretSave: $('#btn-secret-save'),
  secretRows: $('#secret-rows'),
  secretEmpty: $('#secret-empty'),
  sysOpenAtLogin: $('#sys-open-at-login'),
  sysMinimizeTray: $('#sys-minimize-tray'),
  appInfo: $('#app-info'),
  toast: $('#toast'),
};

const state = {
  status: null,
  config: null,
  loadedFrameUrl: null,
  logDomCount: 0,
  waitingLogCount: 0,
  toastTimer: null,
};

const MAX_LOG_DOM_LINES = 1200;

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function toast(message, kind = 'ok', ms = 3200) {
  els.toast.textContent = message;
  els.toast.className = `toast toast-${kind}`;
  els.toast.hidden = false;
  if (state.toastTimer) clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { els.toast.hidden = true; }, ms);
}

function fmtUptime(ms) {
  if (!ms || ms <= 0) return '—';
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m ${sec}s`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function setBusy(btn, busy) {
  if (!btn.dataset.label) btn.dataset.label = btn.textContent;
  btn.disabled = busy;
  btn.textContent = busy ? '处理中…' : btn.dataset.label;
}

function switchView(name) {
  els.navItems.forEach((item) => item.classList.toggle('active', item.dataset.view === name));
  Object.entries(els.views).forEach(([key, view]) => view.classList.toggle('active', key === name));
}

// ---------------------------------------------------------------------------
// log rendering (shared by service log panel and waiting page tail)
// ---------------------------------------------------------------------------

function lineToHtml(entry) {
  const stream = ['stdout', 'stderr', 'system'].includes(entry.stream) ? entry.stream : 'system';
  return `<span class="log-line ${stream}"><span class="ts">${fmtTime(entry.ts)}</span> ${escapeHtml(entry.text)}</span>`;
}

function appendLines(container, entries, cap) {
  if (!entries || entries.length === 0) return;
  const frag = document.createElement('span');
  frag.innerHTML = entries.map(lineToHtml).join('\n');
  container.appendChild(frag);
  // Trim oldest lines beyond the cap to keep the DOM bounded.
  let lines = container.querySelectorAll('.log-line');
  let excess = lines.length - cap;
  while (excess > 0) {
    const first = container.querySelector('.log-line');
    if (!first) break;
    first.remove();
    excess -= 1;
  }
  return container.querySelectorAll('.log-line').length;
}

function isScrolledToBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
}

function pushToLogPanel(entries) {
  const stick = els.logAutoscroll.checked && isScrolledToBottom(els.logPanel);
  appendLines(els.logPanel, entries, MAX_LOG_DOM_LINES);
  state.logDomCount = els.logPanel.querySelectorAll('.log-line').length;
  els.logCount.textContent = `（面板 ${state.logDomCount} 行）`;
  if (stick) els.logPanel.scrollTop = els.logPanel.scrollHeight;
}

function pushToWaitingLog(entries) {
  appendLines(els.waitingLog, entries, 200);
  state.waitingLogCount = els.waitingLog.querySelectorAll('.log-line').length;
  els.waitingLogCount.textContent = `${state.waitingLogCount} 行`;
  els.waitingLog.scrollTop = els.waitingLog.scrollHeight;
}

// ---------------------------------------------------------------------------
// status rendering
// ---------------------------------------------------------------------------

function applyStatus(status) {
  state.status = status;
  const st = status.state || 'stopped';
  const label = STATE_LABELS[st] || st;

  // Top-bar pill + sidebar light
  els.pill.className = `pill pill-${st}`;
  els.pillText.textContent = label;
  els.navLight.className = `nav-light light-${st}`;

  const metaBits = [];
  if (status.port) metaBits.push(`:${status.port}`);
  if (st === 'running') metaBits.push(fmtUptime(Date.now() - status.startedAt));
  els.statusMeta.textContent = metaBits.join(' · ');

  // Service card
  els.svcLight.className = `svc-light light-${st}`;
  els.svcState.textContent = label;
  const subBits = [];
  if (st === 'backoff' && status.nextRestartAt) {
    subBits.push(`下次自动重启：${fmtTime(status.nextRestartAt)}`);
  }
  if (status.restartCount > 0) subBits.push(`累计自动重启 ${status.restartCount} 次`);
  if (!status.restartEnabled && st !== 'running') subBits.push('自动重启已禁用');
  els.svcStateSub.textContent = subBits.join(' · ');

  els.mUrl.textContent = status.url || '—';
  els.mPort.textContent = status.port ? String(status.port) : '—';
  els.mPid.textContent = status.pid ? String(status.pid) : '—';
  els.mUptime.textContent = fmtUptime(status.uptimeMs);
  els.mRestarts.textContent = String(status.restartCount);
  els.mHealth.textContent = status.lastHealthCheckAt
    ? `${fmtTime(status.lastHealthCheckAt)}${status.consecutiveFailures > 0 ? `（连续失败 ${status.consecutiveFailures}）` : ''}`
    : '—';
  els.mPython.textContent = status.pythonPath || '—';
  els.mRepo.textContent = status.repoPath || '—';
  if (status.lastError) {
    els.mErrorRow.hidden = false;
    els.mError.textContent = status.lastError;
  } else {
    els.mErrorRow.hidden = true;
  }

  // Buttons
  const active = ['starting', 'running', 'degraded', 'stopping'].includes(st);
  els.btnStart.disabled = active || st === 'backoff';
  els.btnStop.disabled = !active;
  els.btnRestart.disabled = !active && st !== 'backoff' && st !== 'failed';
  els.btnOpenBrowser.disabled = st !== 'running' || !status.url;

  renderConsole(status);
}

function renderConsole(status) {
  const st = status.state;
  if (st === 'running' && status.url) {
    if (state.loadedFrameUrl !== status.url) {
      els.frame.src = status.url;
      state.loadedFrameUrl = status.url;
    }
    els.frame.style.display = 'block';
    els.waiting.style.display = 'none';
    return;
  }

  // Not ready: show the real waiting page, drop any stale frame.
  if (state.loadedFrameUrl) {
    els.frame.removeAttribute('src');
    state.loadedFrameUrl = null;
  }
  els.frame.style.display = 'none';
  els.waiting.style.display = 'flex';

  els.waitingError.hidden = true;
  els.waitingActions.hidden = true;
  els.waitingSpinner.classList.remove('hidden');

  switch (st) {
    case 'starting':
      els.waitingTitle.textContent = '正在启动网关服务…';
      els.waitingDetail.textContent = status.port
        ? `进程已拉起（PID ${status.pid || '—'}），正在轮询 http://${status.host}:${status.port}/health/ready`
        : '正在定位 Python 解释器与网关代码…';
      break;
    case 'backoff':
      els.waitingTitle.textContent = '服务进程退出，等待自动重启';
      els.waitingDetail.textContent = status.nextRestartAt
        ? `下次重试：${fmtTime(status.nextRestartAt)}（可在服务管理中禁用自动重启）`
        : '正在安排重试…';
      break;
    case 'degraded':
      els.waitingTitle.textContent = '服务运行中，但健康检查失败';
      els.waitingDetail.textContent = `连续 ${status.consecutiveFailures} 次未通过 /health/ready，正在持续轮询…`;
      break;
    case 'stopping':
      els.waitingTitle.textContent = '正在停止服务…';
      els.waitingDetail.textContent = '正在结束网关进程树';
      break;
    case 'failed':
      els.waitingSpinner.classList.add('hidden');
      els.waitingTitle.textContent = '服务启动失败';
      els.waitingDetail.textContent = '请查看下方日志与错误信息，修复后重试。';
      if (status.lastError) {
        els.waitingError.textContent = status.lastError;
        els.waitingError.hidden = false;
      }
      els.waitingActions.hidden = false;
      break;
    case 'stopped':
    default:
      els.waitingSpinner.classList.add('hidden');
      els.waitingTitle.textContent = '网关服务未启动';
      els.waitingDetail.textContent = '点击下方按钮启动 MOA Gateway 服务。';
      els.waitingActions.hidden = false;
      break;
  }
}

// ---------------------------------------------------------------------------
// config form
// ---------------------------------------------------------------------------

async function loadConfigIntoForm() {
  const { config, loadWarnings } = await api.config.get();
  state.config = config;
  els.cfgPython.value = config.gateway.pythonPath || '';
  els.cfgRepo.value = config.gateway.repoPath || '';
  els.cfgHost.value = config.gateway.host === '0.0.0.0' ? '0.0.0.0' : '127.0.0.1';
  els.cfgPort.value = String(config.gateway.port);
  els.cfgReadyTimeout.value = String(Math.round(config.gateway.readyTimeoutMs / 1000));
  els.cfgMaxLines.value = String(config.logs.maxLines);
  els.cfgAutostart.checked = config.gateway.autoStart;
  els.cfgRestartEnabled.checked = config.restart.enabled;
  els.sysMinimizeTray.checked = config.ui.minimizeToTray;
  if (loadWarnings && loadWarnings.length > 0) {
    toast(`配置加载警告：${loadWarnings.join('；')}`, 'warn', 6000);
  }
}

async function saveConfigFromForm() {
  const port = Number(els.cfgPort.value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    toast('端口必须是 1–65535 的整数', 'err');
    return;
  }
  const patch = {
    gateway: {
      pythonPath: els.cfgPython.value.trim(),
      repoPath: els.cfgRepo.value.trim(),
      host: els.cfgHost.value,
      port,
      autoStart: els.cfgAutostart.checked,
      readyTimeoutMs: Math.max(5, Number(els.cfgReadyTimeout.value) || 120) * 1000,
    },
    restart: { enabled: els.cfgRestartEnabled.checked },
    logs: { maxLines: Math.max(500, Number(els.cfgMaxLines.value) || 5000) },
    ui: { minimizeToTray: els.sysMinimizeTray.checked },
  };
  setBusy(els.btnSaveConfig, true);
  try {
    const res = await api.config.set(patch);
    state.config = res.config;
    toast(res.message, 'ok');
    els.configNote.hidden = !res.needsRestart;
    els.configNote.textContent = res.needsRestart
      ? '主机/端口/解释器变更需要重启服务后生效。'
      : '';
  } catch (err) {
    toast(`保存失败：${err.message || err}`, 'err', 6000);
  } finally {
    setBusy(els.btnSaveConfig, false);
  }
}

async function runProbe() {
  setBusy(els.btnProbe, true);
  els.probeResult.hidden = false;
  els.probeResult.textContent = '正在探测…';
  try {
    const { repo, python } = await api.env.probe();
    const lines = [];
    if (repo.path) {
      lines.push(`<span class="ok">✔ 网关目录：</span>${escapeHtml(repo.path)}（来源：${escapeHtml(repo.source)}）`);
    } else {
      lines.push(`<span class="bad">✘ 未找到网关目录。</span>已尝试：${escapeHtml((repo.candidates || []).join('、'))}`);
    }
    if (python.path) {
      lines.push(`<span class="ok">✔ Python：</span>${escapeHtml(python.path)} — ${escapeHtml(python.version || '')}（来源：${escapeHtml(python.source)}）`);
    } else {
      const tried = (python.candidates || []).map((c) => `${c.path}（${c.reason}）`).join('、');
      lines.push(`<span class="bad">✘ 未找到可用 Python。</span>已尝试：${escapeHtml(tried)}`);
    }
    lines.push('提示：探测结果即为「留空自动探测」时实际使用的路径。');
    els.probeResult.innerHTML = lines.join('\n');
  } catch (err) {
    els.probeResult.innerHTML = `<span class="bad">探测失败：${escapeHtml(err.message || String(err))}</span>`;
  } finally {
    setBusy(els.btnProbe, false);
  }
}

// ---------------------------------------------------------------------------
// secrets
// ---------------------------------------------------------------------------

async function refreshSecrets() {
  let available = false;
  try { available = await api.secrets.available(); } catch { /* keep false */ }
  els.secretsUnavailable.hidden = available;
  els.secretName.disabled = !available;
  els.secretValue.disabled = !available;
  els.btnSecretSave.disabled = !available;

  const entries = await api.secrets.list();
  els.secretRows.innerHTML = '';
  els.secretEmpty.hidden = entries.length > 0;
  for (const entry of entries) {
    const tr = document.createElement('tr');
    const tdName = document.createElement('td');
    tdName.textContent = entry.name;
    const tdTime = document.createElement('td');
    tdTime.textContent = entry.updatedAt ? new Date(entry.updatedAt).toLocaleString() : '—';
    const tdAction = document.createElement('td');
    const btn = document.createElement('button');
    btn.className = 'btn btn-danger btn-sm';
    btn.textContent = '删除';
    btn.addEventListener('click', async () => {
      if (!window.confirm(`确定删除密钥「${entry.name}」？删除后需重启服务生效。`)) return;
      try {
        await api.secrets.remove(entry.name);
        toast(`已删除 ${entry.name}`, 'ok');
        refreshSecrets();
      } catch (err) {
        toast(`删除失败：${err.message || err}`, 'err');
      }
    });
    tdAction.appendChild(btn);
    tr.append(tdName, tdTime, tdAction);
    els.secretRows.appendChild(tr);
  }
}

async function saveSecret() {
  const name = els.secretName.value.trim();
  const value = els.secretValue.value;
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
    toast('名称必须是环境变量格式：字母/下划线开头，仅含字母、数字、下划线', 'err', 5000);
    return;
  }
  if (!value) {
    toast('API Key 不能为空', 'err');
    return;
  }
  setBusy(els.btnSecretSave, true);
  try {
    await api.secrets.set(name, value);
    els.secretValue.value = '';
    toast(`已加密保存 ${name}（重启服务后注入生效）`, 'ok', 4500);
    refreshSecrets();
  } catch (err) {
    toast(`保存失败：${err.message || err}`, 'err', 6000);
  } finally {
    setBusy(els.btnSecretSave, false);
  }
}

// ---------------------------------------------------------------------------
// system section
// ---------------------------------------------------------------------------

async function refreshSystem() {
  try {
    const { openAtLogin } = await api.autostart.get();
    els.sysOpenAtLogin.checked = openAtLogin;
  } catch { /* unsupported platform */ }

  try {
    const info = await api.app.info();
    els.appInfo.textContent =
      `app ${info.appVersion} · electron ${info.electron} · node ${info.node} · chrome ${info.chrome}\n` +
      `${info.platform}/${info.arch} · userData: ${info.userData}`;
    els.navVersion.textContent = `v${info.appVersion}`;
  } catch { /* info is best-effort */ }
}

// ---------------------------------------------------------------------------
// wiring
// ---------------------------------------------------------------------------

function wireEvents() {
  els.navItems.forEach((item) => {
    item.addEventListener('click', () => switchView(item.dataset.view));
  });

  els.btnStart.addEventListener('click', async () => {
    setBusy(els.btnStart, true);
    try { await api.service.start(); } catch (err) { toast(`启动失败：${err.message || err}`, 'err'); }
    finally { setBusy(els.btnStart, false); }
  });
  els.btnStop.addEventListener('click', async () => {
    setBusy(els.btnStop, true);
    try { await api.service.stop(); } catch (err) { toast(`停止失败：${err.message || err}`, 'err'); }
    finally { setBusy(els.btnStop, false); }
  });
  els.btnRestart.addEventListener('click', async () => {
    setBusy(els.btnRestart, true);
    try { await api.service.restart(); } catch (err) { toast(`重启失败：${err.message || err}`, 'err'); }
    finally { setBusy(els.btnRestart, false); }
  });
  els.btnWaitingStart.addEventListener('click', () => els.btnStart.click());
  els.btnWaitingService.addEventListener('click', () => switchView('service'));

  els.btnOpenBrowser.addEventListener('click', async () => {
    const status = state.status;
    if (status && status.url) {
      try { await api.app.openExternal(status.url); } catch (err) { toast(err.message || String(err), 'err'); }
    }
  });

  els.btnLogClear.addEventListener('click', async () => {
    try {
      await api.logs.clear();
      els.logPanel.innerHTML = '';
      els.waitingLog.innerHTML = '';
      els.logCount.textContent = '';
    } catch (err) { toast(`清空失败：${err.message || err}`, 'err'); }
  });
  els.btnLogExport.addEventListener('click', async () => {
    setBusy(els.btnLogExport, true);
    try {
      const res = await api.logs.export();
      if (!res.canceled) toast(`日志已导出：${res.filePath}`, 'ok', 5000);
    } catch (err) { toast(`导出失败：${err.message || err}`, 'err'); }
    finally { setBusy(els.btnLogExport, false); }
  });

  els.btnSaveConfig.addEventListener('click', saveConfigFromForm);
  els.btnProbe.addEventListener('click', runProbe);
  els.btnSecretSave.addEventListener('click', saveSecret);

  els.sysOpenAtLogin.addEventListener('change', async () => {
    try {
      const res = await api.autostart.set(els.sysOpenAtLogin.checked);
      els.sysOpenAtLogin.checked = res.openAtLogin;
      toast(res.openAtLogin ? '已开启开机自启' : '已关闭开机自启', 'ok');
    } catch (err) {
      toast(`设置失败：${err.message || err}`, 'err');
      refreshSystem();
    }
  });
  els.sysMinimizeTray.addEventListener('change', async () => {
    try {
      await api.config.set({ ui: { minimizeToTray: els.sysMinimizeTray.checked } });
    } catch (err) { toast(`保存失败：${err.message || err}`, 'err'); }
  });

  // Push channels
  api.service.onStatus(applyStatus);
  api.logs.onEntries(({ entries }) => {
    pushToLogPanel(entries);
    pushToWaitingLog(entries);
  });
  api.logs.onCleared(() => {
    els.logPanel.innerHTML = '';
    els.waitingLog.innerHTML = '';
  });

  // Uptime ticker
  setInterval(() => {
    const status = state.status;
    if (status && ['running', 'degraded', 'starting'].includes(status.state) && status.startedAt) {
      els.mUptime.textContent = fmtUptime(Date.now() - status.startedAt);
      const bits = [];
      if (status.port) bits.push(`:${status.port}`);
      bits.push(fmtUptime(Date.now() - status.startedAt));
      els.statusMeta.textContent = bits.join(' · ');
    }
  }, 1000);
}

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------

async function boot() {
  wireEvents();
  try {
    const [status, logs] = await Promise.all([
      api.service.status(),
      api.logs.get(500),
    ]);
    pushToLogPanel(logs.entries || []);
    pushToWaitingLog((logs.entries || []).slice(-80));
    applyStatus(status);
  } catch (err) {
    toast(`初始化失败：${err.message || err}`, 'err', 8000);
  }
  loadConfigIntoForm();
  refreshSecrets();
  refreshSystem();
}

boot();
