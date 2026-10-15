/**
 * AgentEval — Frontend SPA
 * Bauhaus Design System + FastAPI Backend + WebSocket live traces
 */

// ─────────────────────────────────────────────────────────────────
// CONFIG
// ─────────────────────────────────────────────────────────────────
const API_BASE = '';          // Same origin
const WS_BASE  = `ws://${location.host}`;

// ─────────────────────────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────────────────────────
const state = {
  currentView:    'dashboard',
  tasks:          [],
  evaluations:    [],
  selectedTasks:  new Set(),
  currentEvalId:  null,
  liveTaskStatus: {},   // taskId → {status, score}
  ws:             null,
};

// ─────────────────────────────────────────────────────────────────
// UTILITIES
// ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const el = (tag, cls, html = '') => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html) e.innerHTML = html;
  return e;
};

function scoreClass(score) {
  if (score >= 80) return 'high';
  if (score >= 55) return 'medium';
  return 'low';
}

function scoreColor(score) {
  if (score >= 80) return 'var(--blue)';
  if (score >= 55) return 'var(--yellow)';
  return 'var(--red)';
}

function categoryBadge(cat) {
  const map = {
    BUG_FIX:    ['badge-blue',   'Bug Fix'],
    API:        ['badge-black',  'API'],
    SECURITY:   ['badge-red',    'Security'],
    DATABASE:   ['badge-yellow', 'Database'],
    REGRESSION: ['badge-muted',  'Regression'],
    SYSTEM:     ['badge-black',  'System'],
    ALGORITHM:  ['badge-blue',   'Algorithm'],
  };
  const [cls, label] = map[cat] || ['badge-muted', cat];
  return `<span class="badge ${cls}">${label}</span>`;
}

function failureCategoryBadge(cat) {
  const map = {
    INCORRECT_IMPLEMENTATION: ['badge-yellow', 'Incorrect Implementation'],
    TOOL_API_ERROR:           ['badge-red',    'Tool/API Error'],
    ENVIRONMENT_FAILURE:      ['badge-black',  'Environment Failure'],
    SECURITY_VIOLATION:       ['badge-red',    'Security Violation'],
    REGRESSION:               ['badge-muted',  'Regression'],
  };
  const [cls, label] = map[cat] || ['badge-muted', cat || 'Unclassified'];
  return `<span class="badge ${cls}">${label}</span>`;
}

function fmtScore(score) {
  if (score == null) return '—';
  return `${Math.round(score)}%`;
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'Z');
  return d.toLocaleString('en-US', { month:'short', day:'numeric',
    hour:'2-digit', minute:'2-digit' });
}

function makeScoreRing(score, size = 72) {
  const r = (size - 10) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (score / 100) * circ;
  const cls = scoreClass(score);
  return `
    <div class="score-ring-wrap">
      <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
        <circle class="score-ring-track" cx="${size/2}" cy="${size/2}" r="${r}"/>
        <circle class="score-ring-prog ${cls}"
                cx="${size/2}" cy="${size/2}" r="${r}"
                stroke-dasharray="${circ.toFixed(1)}"
                stroke-dashoffset="${offset.toFixed(1)}"/>
        <text x="${size/2}" y="${size/2 + 1}"
              text-anchor="middle" dominant-baseline="middle"
              font-family="Outfit,sans-serif" font-weight="900"
              font-size="${size < 60 ? 13 : 16}"
              fill="var(--fg)"
              transform="rotate(90 ${size/2} ${size/2})">
          ${Math.round(score)}%
        </text>
      </svg>
    </div>`;
}


// ─────────────────────────────────────────────────────────────────
// ROUTING
// ─────────────────────────────────────────────────────────────────
function navigate(view, param = null) {
  // Hide all views
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));

  state.currentView = view;

  if (view === 'dashboard') {
    $('view-dashboard').classList.add('active');
    $('nav-dashboard').classList.add('active');
    loadDashboard();
  } else if (view === 'new') {
    $('view-new').classList.add('active');
    $('nav-new').classList.add('active');
    renderNewEval();
  } else if (view === 'live') {
    $('view-live').classList.add('active');
  } else if (view === 'detail') {
    $('view-detail').classList.add('active');
    loadDetail(param);
  }
}


// ─────────────────────────────────────────────────────────────────
// API CALLS
// ─────────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const res = await fetch(API_BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API ${path}: ${res.status} — ${err}`);
  }
  return res.json();
}


// ─────────────────────────────────────────────────────────────────
// MODEL LOADER  — populates the Agent Model dropdown dynamically
// ─────────────────────────────────────────────────────────────────
async function loadModels() {
  const select = $('model-select');
  const badge  = $('ollama-status-badge');

  try {
    const data = await apiFetch('/api/models');
    const localModels = data.models || [];

    // Clear the loading placeholder
    select.innerHTML = '';

    // Always provide a Mock option first
    const mockOpt = document.createElement('option');
    mockOpt.value = 'mock';
    mockOpt.textContent = '🎭  Mock Agent  (Demo — no Ollama required)';
    select.appendChild(mockOpt);

    if (data.ollama_running && localModels.length > 0) {
      // Separator
      const sep = document.createElement('option');
      sep.disabled = true;
      sep.textContent = '──── Locally Installed (Ollama) ────';
      select.appendChild(sep);

      localModels.forEach((name, i) => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = `🤖  ${name}`;
        select.appendChild(opt);
        // Pre-select the first real local model
        if (i === 0) opt.selected = true;
      });

      badge.textContent = `✅ Ollama running — ${localModels.length} model${localModels.length > 1 ? 's' : ''} available`;
      badge.style.color = 'var(--blue, #4fa)';
    } else if (data.ollama_running && localModels.length === 0) {
      badge.textContent = '⚠️ Ollama running but no models found — run: ollama pull <model>';
      badge.style.color = 'var(--yellow, #fa0)';
      select.value = 'mock';
    } else {
      badge.textContent = '⚠️ Ollama not running — only Mock Agent available';
      badge.style.color = 'var(--red, #f44)';
      select.value = 'mock';
    }
  } catch (err) {
    // API call failed — fall back to just the mock option
    select.innerHTML = '<option value="mock">🎭  Mock Agent  (Demo — no Ollama required)</option>';
    if (badge) {
      badge.textContent = '⚠️ Could not load models';
      badge.style.color = 'var(--red, #f44)';
    }
  }
}


// ─────────────────────────────────────────────────────────────────
// DASHBOARD
// ─────────────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    // Load tasks (once)
    if (state.tasks.length === 0) {
      state.tasks = await apiFetch('/api/tasks');
    }
    const statTasksEl = $('stat-tasks');
    if (statTasksEl) statTasksEl.textContent = state.tasks.length;
    renderTaskOverview();

    // Load evaluations
    $('dash-loading').style.display = 'flex';
    $('eval-list').style.display = 'none';
    $('eval-empty').style.display = 'none';

    state.evaluations = await apiFetch('/api/evaluations');
    renderEvaluations();
    updateHeroStats();
  } catch (err) {
    $('dash-loading').innerHTML = `<div class="loading-text" style="color:var(--red);">Error: ${err.message}</div>`;
  }
}

function updateHeroStats() {
  const evals = state.evaluations;
  $('hs-evals').textContent = evals.length || '0';

  const completed = evals.filter(e => e.status === 'completed');
  if (completed.length === 0) {
    $('hs-success').textContent = '—';
    $('hs-avg').textContent = '—';
    $('stat-runs').textContent = '0';
    $('stat-passed').textContent = '0';
    return;
  }

  // Pull run stats
  const totalRuns  = completed.reduce((s, e) => s + (e.run_count || 0), 0);
  const totalPass  = completed.reduce((s, e) => s + (e.passed_count || 0), 0);
  const avgScores  = completed.filter(e => e.avg_score != null).map(e => e.avg_score);
  const overallAvg = avgScores.length ? (avgScores.reduce((a, b) => a + b, 0) / avgScores.length) : 0;
  const successPct = totalRuns > 0 ? Math.round(totalPass / totalRuns * 100) : 0;

  $('hs-evals').textContent = evals.length;
  $('hs-success').textContent = `${successPct}%`;
  $('hs-avg').textContent = `${Math.round(overallAvg)}%`;
  $('stat-runs').textContent = totalRuns;
  $('stat-passed').textContent = totalPass;
}

function renderEvaluations() {
  $('dash-loading').style.display = 'none';

  if (state.evaluations.length === 0) {
    $('eval-empty').style.display = 'block';
    return;
  }

  $('eval-list').style.display = 'block';
  $('eval-list').innerHTML = '';

  state.evaluations.forEach(ev => {
    const statusCls = ev.status === 'completed'
      ? (ev.avg_score >= 70 ? 'passed' : 'failed')
      : ev.status;

    const scoreColor = ev.avg_score != null ? scoreClass(ev.avg_score) : '';

    const card = el('div', 'eval-card');
    card.innerHTML = `
      <div class="eval-card-id">#${ev.id}</div>
      <div class="eval-card-info">
        <div class="eval-card-model">${ev.agent_model}</div>
        <div class="eval-card-meta">${fmtDate(ev.created_at)} · ${ev.total_tasks} tasks · ${ev.status.toUpperCase()}</div>
      </div>
      <div class="eval-card-score" style="color:${scoreColor === 'high' ? 'var(--blue)' : scoreColor === 'medium' ? '#b08800' : 'var(--red)'}">
        ${ev.avg_score != null ? Math.round(ev.avg_score) + '%' : '—'}
      </div>
      <div class="eval-card-status">
        <div class="status-indicator ${statusCls}" title="${ev.status}"></div>
      </div>
    `;
    card.onclick = () => navigate('detail', ev.id);
    $('eval-list').appendChild(card);
  });
}

function renderTaskOverview() {
  const container = $('task-overview');
  if (!container || state.tasks.length === 0) return;
  container.innerHTML = '';

  const accents = ['accent-red', 'accent-blue', 'accent-yellow', 'accent-red', 'accent-blue', 'accent-yellow'];

  state.tasks.forEach((task, i) => {
    const card = el('div', `card task-card ${accents[i % accents.length]}`);
    card.innerHTML = `
      <div class="task-card-top">
        <div>
          <div class="task-card-name">${task.name}</div>
          <div style="margin-top:8px;">${categoryBadge(task.category)}</div>
        </div>
        <span class="badge badge-muted">${task.difficulty || 'medium'}</span>
      </div>
      <div class="task-card-body">
        <div class="task-card-desc">${task.description?.substring(0, 130)}…</div>
      </div>
      <div class="task-card-footer">
        <span style="font-size:11px; opacity:.5; font-weight:700; letter-spacing:.1em; text-transform:uppercase;">
          ~${task.estimated_minutes || 10} min
        </span>
        <div style="display:flex; gap:4px;">
          ${(task.tags || []).slice(0, 2).map(t =>
            `<span class="badge badge-muted">${t}</span>`
          ).join('')}
        </div>
      </div>
    `;
    container.appendChild(card);
  });
}


// ─────────────────────────────────────────────────────────────────
// NEW EVALUATION
// ─────────────────────────────────────────────────────────────────
function renderNewEval() {
  // Load tasks if needed
  if (state.tasks.length === 0) {
    apiFetch('/api/tasks').then(tasks => {
      state.tasks = tasks;
      renderNewEval();
    });
    return;
  }

  // Default: all tasks selected
  if (state.selectedTasks.size === 0) {
    state.tasks.forEach(t => state.selectedTasks.add(t.id));
  }

  const container = $('new-task-selector');
  container.innerHTML = '';

  state.tasks.forEach(task => {
    const selected = state.selectedTasks.has(task.id);
    const opt = el('div', `task-option ${selected ? 'selected' : ''}`);
    opt.id = `task-opt-${task.id}`;
    opt.innerHTML = `
      <div class="task-option-check">${selected ? '✓' : ''}</div>
      <div>
        <div class="task-option-name">${task.name}</div>
        <div class="task-option-cat">${task.category}</div>
      </div>
    `;
    opt.onclick = () => toggleTask(task.id);
    container.appendChild(opt);
  });

  updateTaskCountLabel();
}

function toggleTask(taskId) {
  if (state.selectedTasks.has(taskId)) {
    state.selectedTasks.delete(taskId);
  } else {
    state.selectedTasks.add(taskId);
  }
  renderNewEval();
}

function updateTaskCountLabel() {
  const n = state.selectedTasks.size;
  $('task-count-label').textContent = `(${n} selected)`;
}

async function launchEvaluation() {
  if (state.selectedTasks.size === 0) {
    alert('Please select at least one task.');
    return;
  }

  const model = $('model-select').value;
  const taskIds = Array.from(state.selectedTasks);

  $('launch-btn').textContent = 'Launching…';
  $('launch-btn').disabled = true;

  try {
    const data = await apiFetch('/api/evaluations', {
      method: 'POST',
      body: JSON.stringify({ model, task_ids: taskIds }),
    });

    state.currentEvalId = data.evaluation_id;
    state.liveTaskStatus = {};
    taskIds.forEach(id => { state.liveTaskStatus[id] = { status: 'pending', score: null }; });

    // Switch to live view
    navigate('live');
    setupLiveView(data.evaluation_id, model, taskIds);

  } catch (err) {
    alert(`Failed to start evaluation:\n${err.message}`);
  } finally {
    $('launch-btn').textContent = '▶ Launch Evaluation';
    $('launch-btn').disabled = false;
  }
}


// ─────────────────────────────────────────────────────────────────
// LIVE EVALUATION VIEW
// ─────────────────────────────────────────────────────────────────
function setupLiveView(evalId, model, taskIds) {
  $('live-title').textContent = `Evaluation #${evalId}`;
  $('live-meta').textContent = `Agent: ${model} · ${taskIds.length} tasks`;
  $('live-progress').style.width = '0%';
  $('view-results-btn').style.display = 'none';
  $('live-final-score').style.display = 'none';
  $('trace-dot').style.display = 'block';

  // Build task status list
  renderLiveTaskList(taskIds);

  // Reset trace entries (header stays)
  const traceEntries = $('live-trace-entries');
  if (traceEntries) traceEntries.innerHTML = '';

  // Connect WebSocket
  if (state.ws) {
    state.ws.close();
  }

  const ws = new WebSocket(`${WS_BASE}/ws/evaluations/${evalId}`);
  state.ws = ws;

  let completed = 0;
  const total = taskIds.length;

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);

    if (msg.type === 'trace_step') {
      appendTraceEntry(msg.task_id, msg.action, msg.detail, msg);

    } else if (msg.type === 'task_start') {
      updateLiveTask(msg.task_id, 'running', null);

    } else if (msg.type === 'task_complete') {
      completed++;
      updateLiveTask(msg.task_id, msg.status, msg.score);
      $('live-progress').style.width = `${(completed / total) * 100}%`;

      if (completed >= total) {
        $('trace-dot').style.display = 'none';
      }

    } else if (msg.type === 'evaluation_complete') {
      $('trace-dot').style.display = 'none';
      $('live-progress').style.width = '100%';
      $('view-results-btn').style.display = 'inline-flex';
      $('live-final-score').style.display = 'block';
      $('live-avg').textContent = `${Math.round(msg.summary.avg_score)}%`;

      appendTraceEntry('SYSTEM', 'EVALUATION COMPLETE',
        `${msg.summary.passed}/${msg.summary.total} tasks passed · Avg score: ${Math.round(msg.summary.avg_score)}%`);
    }
  };

  ws.onerror = () => {
    appendTraceEntry('SYSTEM', 'CONNECTION ERROR', 'WebSocket error. Results will still be saved.');
  };
}

function renderLiveTaskList(taskIds) {
  const list = $('live-task-list');
  list.innerHTML = '';

  // Map task IDs to names
  const nameMap = {};
  state.tasks.forEach(t => { nameMap[t.id] = t.name; });

  taskIds.forEach(id => {
    const item = el('div', 'live-task-item');
    item.id = `live-task-${id}`;
    item.innerHTML = `
      <div class="live-task-indicator pending" id="live-ind-${id}"></div>
      <div class="live-task-name">${nameMap[id] || id}</div>
      <div class="live-task-score" id="live-score-${id}">—</div>
    `;
    list.appendChild(item);
  });
}

function updateLiveTask(taskId, status, score) {
  const item = $(`live-task-${taskId}`);
  const ind  = $(`live-ind-${taskId}`);
  const scoreEl = $(`live-score-${taskId}`);
  if (!item) return;

  item.className = `live-task-item ${status}`;
  ind.className  = `live-task-indicator ${status}`;

  const icons = { pending: '', running: '…', passed: '✓', failed: '✗' };
  ind.textContent = icons[status] || '';

  if (score != null) {
    scoreEl.textContent = `${Math.round(score)}%`;
    scoreEl.style.color = scoreColor(score);
  }
}

function appendTraceEntry(taskId, action, detail, meta = {}) {
  const entries = $('live-trace-entries');
  if (!entries) return;
  const entry = el('div', 'trace-entry');

  // Classify action type for color
  const laction = action.toLowerCase();
  if (laction.includes('complete'))     entry.classList.add('action-complete');
  else if (laction.includes('error'))   entry.classList.add('action-error');
  else if (laction.includes('hidden'))  entry.classList.add('action-hidden');
  else if (laction.includes('scor'))    entry.classList.add('action-scoring');

  const shortTask = String(taskId).toUpperCase().slice(0, 10);

  // Build metadata chips
  const chips = [];
  if (meta.duration_ms && meta.duration_ms > 0) {
    const durStr = meta.duration_ms >= 1000 ? `${(meta.duration_ms / 1000).toFixed(2)}s` : `${Math.round(meta.duration_ms)}ms`;
    chips.push(`<span class="trace-chip chip-time">⏱ ${durStr}</span>`);
  }
  if (meta.token_usage) {
    const tokCount = typeof meta.token_usage === 'object' ? (meta.token_usage.total_tokens || meta.token_usage.output_tokens) : null;
    if (tokCount) chips.push(`<span class="trace-chip">🔤 ${tokCount} tok</span>`);
  }
  if (meta.cost_usd && meta.cost_usd > 0) {
    chips.push(`<span class="trace-chip chip-cost">$${meta.cost_usd.toFixed(4)}</span>`);
  }
  if (meta.tool_name) {
    chips.push(`<span class="trace-chip">🔧 ${meta.tool_name}</span>`);
  }
  if (meta.exit_code !== undefined && meta.exit_code !== null) {
    const cls = meta.exit_code === 0 ? 'chip-exit-ok' : 'chip-exit-err';
    chips.push(`<span class="trace-chip ${cls}">exit ${meta.exit_code}</span>`);
  }

  entry.innerHTML = `
    <div class="trace-task-badge">${shortTask}</div>
    <div class="trace-action">${action}</div>
    <div class="trace-detail">${detail || ''}</div>
    ${chips.length ? `<div class="trace-chips">${chips.join('')}</div>` : ''}
  `;
  entries.appendChild(entry);
  // Auto-scroll the entries panel to the bottom
  entries.scrollTop = entries.scrollHeight;
}

function viewCurrentResults() {
  if (state.currentEvalId) {
    navigate('detail', state.currentEvalId);
  }
}


// ─────────────────────────────────────────────────────────────────
// EVALUATION DETAIL
// ─────────────────────────────────────────────────────────────────
async function loadDetail(evalId) {
  const content = $('detail-content');
  content.innerHTML = `<div class="loading-wrap"><div class="loading-geo"></div><div class="loading-text">Loading results…</div></div>`;

  try {
    const data = await apiFetch(`/api/evaluations/${evalId}`);
    renderDetail(data);
  } catch (err) {
    content.innerHTML = `<div class="section-body" style="color:var(--red);">Error: ${err.message}</div>`;
  }
}

function renderDetail(ev) {
  const content = $('detail-content');
  const runs = ev.task_runs || [];

  // Compute summary
  const completed = runs.filter(r => r.status !== 'pending' && r.status !== 'running');
  const passed    = completed.filter(r => r.status === 'passed').length;
  const avgScore  = completed.length
    ? completed.reduce((s, r) => s + (r.final_score || 0), 0) / completed.length : 0;

  content.innerHTML = '';

  // ── Header ──────────────────────────────────────────────────
  const hdr = el('div');
  hdr.style.cssText = 'background:var(--fg); color:var(--white); border-bottom:4px solid #121212;';
  hdr.innerHTML = `
    <div style="padding:40px 56px;">
      <div class="detail-back" onclick="navigate('dashboard')">← Back to Dashboard</div>
      <div style="display:flex; align-items:flex-end; justify-content:space-between; flex-wrap:wrap; gap:24px;">
        <div>
          <div style="font-size:11px; font-weight:700; letter-spacing:.18em; text-transform:uppercase; opacity:.5; margin-bottom:12px;">
            Evaluation #${ev.id}
          </div>
          <h1 style="font-size:clamp(36px,5vw,64px); font-weight:900; text-transform:uppercase; line-height:.95; letter-spacing:-.01em;">
            ${ev.agent_model}
          </h1>
          <div style="margin-top:16px; display:flex; gap:20px; flex-wrap:wrap;">
            <span style="font-size:11px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; opacity:.5;">${fmtDate(ev.created_at)}</span>
            <span style="font-size:11px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; opacity:.5;">${ev.total_tasks} Tasks</span>
            <span class="badge ${ev.status === 'completed' ? 'badge-blue' : 'badge-yellow'}">${ev.status.toUpperCase()}</span>
          </div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:80px; font-weight:900; color:var(--yellow); line-height:1;">${Math.round(avgScore)}%</div>
          <div style="font-size:11px; font-weight:700; letter-spacing:.18em; text-transform:uppercase; opacity:.5;">Average Score</div>
          <div style="margin-top:8px; font-size:14px; font-weight:700; opacity:.6;">${passed}/${completed.length} Passed</div>
        </div>
      </div>
    </div>
  `;
  content.appendChild(hdr);

  // ── Overall score bar ──────────────────────────────────────
  const scoreBar = el('div');
  scoreBar.style.cssText = 'background:var(--yellow); border-bottom:4px solid #121212;';
  scoreBar.innerHTML = `
    <div style="display:grid; grid-template-columns: repeat(${Math.min(runs.length, 6)}, 1fr);">
      ${runs.map(r => `
        <div style="padding:20px 24px; border-right:3px solid #121212; text-align:center;">
          <div style="font-size:28px; font-weight:900; color:var(--fg);">${Math.round(r.final_score || 0)}%</div>
          <div style="font-size:9px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; opacity:.5; margin-top:4px;">${r.task_id}</div>
          <div style="margin-top:6px;">
            <div class="status-indicator ${r.status}" style="margin:0 auto;"></div>
          </div>
        </div>
      `).join('')}
    </div>
  `;
  content.appendChild(scoreBar);

  // ── Task runs ─────────────────────────────────────────────
  const runsSection = el('div');
  const secHdr = el('div', 'section-header');
  secHdr.innerHTML = `<h2>Task Results</h2><span class="badge badge-muted">${runs.length} tasks</span>`;
  runsSection.appendChild(secHdr);

  runs.forEach(run => {
    runsSection.appendChild(renderTaskRunRow(run));
  });

  content.appendChild(runsSection);

  // ── Full trace section ────────────────────────────────────
  const allTraces = runs.flatMap(r =>
    (r.traces || []).map(t => ({ ...t, task_id: r.task_id }))
  ).sort((a, b) => a.timestamp?.localeCompare(b.timestamp));

  if (allTraces.length > 0) {
    const traceSection = el('div', '', `
      <div class="section-header" style="border-top:4px solid #121212;">
        <h2>Full Agent Trace</h2>
        <span class="badge badge-black">${allTraces.length} steps</span>
      </div>
    `);

    const traceLog = el('div', 'trace-log');
    traceLog.style.cssText = 'margin:0 40px 40px; border:4px solid #121212; max-height:480px;';
    traceLog.innerHTML = `<div class="trace-header">AGENT TRACE LOG · Evaluation #${ev.id}</div>`;

    allTraces.forEach(t => {
      const entry = el('div', 'trace-entry');
      const laction = (t.action || '').toLowerCase();
      if (laction.includes('complete'))    entry.classList.add('action-complete');
      else if (laction.includes('error'))  entry.classList.add('action-error');
      else if (laction.includes('hidden')) entry.classList.add('action-hidden');
      else if (laction.includes('scor'))   entry.classList.add('action-scoring');

      const chips = [];
      if (t.duration_ms && t.duration_ms > 0) {
        const durStr = t.duration_ms >= 1000 ? `${(t.duration_ms / 1000).toFixed(2)}s` : `${Math.round(t.duration_ms)}ms`;
        chips.push(`<span class="trace-chip chip-time">⏱ ${durStr}</span>`);
      }
      if (t.token_usage) {
        const tokCount = typeof t.token_usage === 'object' ? (t.token_usage.total_tokens || t.token_usage.output_tokens) : null;
        if (tokCount) chips.push(`<span class="trace-chip">🔤 ${tokCount} tok</span>`);
      }
      if (t.estimated_cost && t.estimated_cost > 0) {
        chips.push(`<span class="trace-chip chip-cost">$${Number(t.estimated_cost).toFixed(4)}</span>`);
      }
      if (t.tool_name) {
        chips.push(`<span class="trace-chip">🔧 ${t.tool_name}</span>`);
      }
      if (t.exit_code !== undefined && t.exit_code !== null) {
        const cls = t.exit_code === 0 ? 'chip-exit-ok' : 'chip-exit-err';
        chips.push(`<span class="trace-chip ${cls}">exit ${t.exit_code}</span>`);
      }

      entry.innerHTML = `
        <div class="trace-task-badge">${String(t.task_id).toUpperCase().slice(0,10)}</div>
        <div class="trace-action">${t.action}</div>
        <div class="trace-detail">${t.detail || ''}</div>
        ${chips.length ? `<div class="trace-chips">${chips.join('')}</div>` : ''}
      `;
      traceLog.appendChild(entry);
    });

    traceSection.appendChild(traceLog);
    content.appendChild(traceSection);
  }
}

function renderTaskRunRow(run) {
  const row = el('div', 'detail-task-row');
  row.id = `run-row-${run.task_id}`;

  const failedTests = (run.failure_details || []).filter(t => !t.passed);
  const passedTests = (run.failure_details || []).filter(t => t.passed);
  const statusIcon  = run.status === 'passed' ? '✓' : run.status === 'failed' ? '✗' : '…';
  const statusColor = run.status === 'passed' ? 'var(--blue)' : run.status === 'failed' ? 'var(--red)' : 'var(--yellow)';

  // Summary row (always visible)
  const summary = el('div', 'detail-task-summary');
  summary.innerHTML = `
    <div class="detail-task-toggle">▶</div>
    <div style="flex:1;">
      <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <span style="font-size:18px; font-weight:900; text-transform:uppercase; letter-spacing:.04em;">${run.task_name}</span>
        ${categoryBadge(run.category)}
        ${run.failure_category ? failureCategoryBadge(run.failure_category) : ''}
      </div>
      <div style="font-size:11px; opacity:.6; font-weight:700; letter-spacing:.08em; margin-top:6px; display:flex; gap:16px; flex-wrap:wrap; text-transform:uppercase;">
        <span>${passedTests.length} passed · ${failedTests.length} failed</span>
        <span>⏱ ${run.latency_ms ? Math.round(run.latency_ms) + 'ms' : '—'}</span>
        <span>🔤 ${run.total_tokens || 0} tokens</span>
        <span>💲 $${(run.estimated_cost || 0).toFixed(4)}</span>
        ${run.retries_used ? `<span style="color:var(--yellow);">🔄 ${run.retries_used} retries</span>` : ''}
      </div>
    </div>
    <div style="font-size:42px; font-weight:900; color:${scoreColor(run.final_score || 0)};">${Math.round(run.final_score || 0)}%</div>
    <div style="width:32px; height:32px; background:${statusColor}; border:3px solid #121212; display:flex; align-items:center; justify-content:center; color:var(--white); font-weight:900; font-size:16px;">
      ${statusIcon}
    </div>
  `;
  summary.onclick = () => {
    row.classList.toggle('open');
  };
  row.appendChild(summary);

  // Expanded content
  const expanded = el('div', 'detail-task-expanded');
  expanded.innerHTML = buildExpandedContent(run);
  row.appendChild(expanded);

  return row;
}

function buildExpandedContent(run) {
  const scores = [
    { label: 'Visible Tests', value: run.visible_score, weight: '—' },
    { label: 'Hidden Tests', value: run.hidden_score, weight: '×0.40' },
    { label: 'Regression', value: run.regression_score, weight: '×0.30' },
    { label: 'Requirements', value: run.requirements_score, weight: '×0.20' },
    { label: 'Code Quality', value: run.quality_score, weight: '×0.10' },
  ];

  const failedTests = (run.failure_details || []).filter(t => !t.passed);
  const passedTests = (run.failure_details || []).filter(t => t.passed);
  const diag = run.diagnosis || {};

  return `
    <!-- Telemetry & Recovery Banner -->
    <div class="recovery-metrics-grid">
      <div>
        <div class="recovery-stat-val">${run.latency_ms ? Math.round(run.latency_ms) + 'ms' : '—'}</div>
        <div class="recovery-stat-lbl">Execution Time</div>
      </div>
      <div>
        <div class="recovery-stat-val">${run.total_tokens || 0}</div>
        <div class="recovery-stat-lbl">Tokens Consumed</div>
      </div>
      <div>
        <div class="recovery-stat-val">$${(run.estimated_cost || 0).toFixed(4)}</div>
        <div class="recovery-stat-lbl">Est. Cost (USD)</div>
      </div>
      <div>
        <div class="recovery-stat-val">${run.retries_used || 0}</div>
        <div class="recovery-stat-lbl">Retries Used</div>
      </div>
      <div>
        <div class="recovery-stat-val" style="color:${run.recovery_succeeded ? 'var(--blue)' : run.recovery_attempted ? 'var(--red)' : 'var(--fg)'}">
          ${run.recovery_attempted ? (run.recovery_succeeded ? 'Recovered ✓' : 'Failed ✗') : 'None Needed'}
        </div>
        <div class="recovery-stat-lbl">Self-Recovery</div>
      </div>
    </div>

    <!-- Score breakdown -->
    <div style="display:grid; grid-template-columns:repeat(5,1fr); border:3px solid #121212; margin-bottom:24px;">
      ${scores.map((s, i) => `
        <div style="padding:20px 16px; ${i < scores.length - 1 ? 'border-right:3px solid #121212;' : ''}">
          <div style="font-size:36px; font-weight:900; color:${scoreColor(s.value || 0)};">${Math.round(s.value || 0)}%</div>
          <div style="font-size:9px; font-weight:700; letter-spacing:.16em; text-transform:uppercase; opacity:.5; margin-top:4px;">${s.label}</div>
          <div style="font-size:9px; opacity:.35; font-weight:700; margin-top:2px;">${s.weight}</div>
        </div>
      `).join('')}
    </div>

    <!-- Root Cause Diagnosis Banner (if diagnosis present) -->
    ${diag.summary ? `
      <div style="background:var(--white); border:3px solid #121212; padding:16px 20px; margin-bottom:24px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
          <span style="font-size:11px; font-weight:700; letter-spacing:.14em; text-transform:uppercase;">Root Cause Diagnosis</span>
          ${failureCategoryBadge(diag.category || run.failure_category)}
        </div>
        <div style="font-size:14px; font-weight:700; color:var(--red); margin-bottom:6px;">
          ${diag.summary}
        </div>
        <div style="font-size:12px; opacity:.85; font-family:'Courier New',monospace; margin-bottom:8px; line-height:1.4;">
          ${diag.root_cause || ''}
        </div>
        ${diag.recovery_suggestion ? `
          <div style="font-size:11px; background:var(--muted); padding:8px 12px; border-left:4px solid var(--blue); font-weight:500;">
            <strong>Suggested Fix:</strong> ${diag.recovery_suggestion}
          </div>
        ` : ''}
      </div>
    ` : ''}

    <div style="display:grid; grid-template-columns:1fr 1fr; gap:24px;">

      <!-- Hidden test results -->
      <div>
        <div style="font-size:11px; font-weight:700; letter-spacing:.16em; text-transform:uppercase; opacity:.5; margin-bottom:12px;">
          Hidden Test Results
        </div>
        <div class="test-list" style="border:3px solid #121212;">
          ${(run.failure_details || []).map(t => `
            <div class="test-item ${t.passed ? 'passed' : 'failed'}">
              <div class="test-icon">${t.passed ? '✓' : '✗'}</div>
              <div class="test-name">${t.name}</div>
              ${!t.passed && t.error ? `<div class="test-error">${t.error?.substring(0, 80)}</div>` : ''}
            </div>
          `).join('') || '<div class="test-item"><div class="test-detail" style="opacity:.5;">No test data</div></div>'}
        </div>
      </div>

      <!-- Failure analysis -->
      <div>
        ${run.failure_category && failedTests.length > 0 ? `
          <div class="failure-block">
            <div class="failure-header">
              <h3>Failure Analysis</h3>
              <div class="failure-category-badge">${run.failure_category}</div>
            </div>
            <div class="failure-body">
              ${passedTests.map(t => `
                <div class="failure-item">
                  <div class="failure-icon">✓</div>
                  <div class="failure-name">${t.name}</div>
                </div>
              `).join('')}
              ${failedTests.map(t => `
                <div class="failure-item" style="background:rgba(208,32,32,.05);">
                  <div class="failure-icon" style="color:var(--red);">✗</div>
                  <div class="failure-name" style="color:var(--red);">${t.name}</div>
                </div>
              `).join('')}
            </div>
          </div>
        ` : `
          <div style="border:3px solid #121212; padding:32px; text-align:center; background:var(--white);">
            <div style="font-size:32px; margin-bottom:12px;">✓</div>
            <div style="font-size:14px; font-weight:700; text-transform:uppercase; letter-spacing:.06em;">All Tests Passed</div>
          </div>
        `}

        <!-- Agent trace for this task -->
        ${run.traces && run.traces.length > 0 ? `
          <div style="margin-top:16px;">
            <div style="font-size:11px; font-weight:700; letter-spacing:.16em; text-transform:uppercase; opacity:.5; margin-bottom:12px;">
              Agent Steps (${run.traces.length})
            </div>
            <div style="background:var(--fg); border:3px solid #121212; padding:12px; max-height:200px; overflow-y:auto;">
              ${run.traces.map((t, i) => `
                <div style="display:flex; gap:12px; padding:6px 0; border-bottom:1px solid rgba(255,255,255,.06); align-items:baseline; justify-content:space-between;">
                  <div style="display:flex; gap:8px; align-items:baseline; overflow:hidden;">
                    <span style="font-size:10px; color:rgba(255,255,255,.3); font-weight:700; min-width:20px;">${i + 1}.</span>
                    <span style="font-size:11px; color:var(--yellow); font-weight:700; white-space:nowrap;">${t.action}</span>
                    <span style="font-size:11px; color:rgba(255,255,255,.5); font-family:'Courier New',monospace; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${t.detail || ''}</span>
                  </div>
                  ${t.duration_ms ? `<span style="font-size:10px; color:#38bdf8; font-family:'Courier New',monospace;">${Math.round(t.duration_ms)}ms</span>` : ''}
                </div>
              `).join('')}
            </div>
          </div>
        ` : ''}
      </div>
    </div>
  `;
}


// ─────────────────────────────────────────────────────────────────
// INIT
// ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Load dashboard on start
  loadDashboard();

  // Populate model dropdown from local Ollama
  loadModels();

  // Reload models whenever the user navigates to the New Evaluation view
  // so if they start Ollama mid-session the list refreshes
  const origNavigate = navigate;
  window.navigateWithModelRefresh = function(view, param) {
    origNavigate(view, param);
    if (view === 'new') loadModels();
  };

  // Handle back/forward via popstate
  window.addEventListener('popstate', (e) => {
    if (e.state?.view) navigate(e.state.view, e.state.param);
  });
});
