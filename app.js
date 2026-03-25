(()=>{
const $ = id => document.getElementById(id);
const INPUT_IDS = [
  'dose','weight','cl','v','ka','ktr','pka','logP','permeability','solubility',
  'refDiss','refSol','refPart','refFabs','testDiss','testSol','testPart','testFabs',
  'subjects','trials','clcv','vcv','kacv','food','emax','ec50','hill','e0'
];
const ui = Object.fromEntries(INPUT_IDS.map(id => [id, $(id)]));
ui.json = $('json');

const state = {
  latest: null,
  caseName: '真实本地 PBPK/BE 任务',
  busy: false,
  jobId: null,
  localLogs: [],
  backendLogs: [],
  logPage: 1,
  logPageSize: 10,
};

const F = (n, d = 2) => Number.isFinite(n) ? Number(n).toFixed(d) : 'NA';
const P = (n, d = 1) => Number.isFinite(n) ? `${(n * 100).toFixed(d)}%` : 'NA';
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

function readCase() {
  const payload = { name: state.caseName || '真实本地 PBPK/BE 任务' };
  INPUT_IDS.forEach(id => {
    payload[id] = Number(ui[id].value);
  });
  return payload;
}

function updateJsonFromForm() {
  const payload = readCase();
  ui.json.value = JSON.stringify(payload, null, 2);
  persistCase();
}

function applyCase(payload, skipLog = false) {
  const merged = { ...payload };
  state.caseName = merged.name || state.caseName || '真实本地 PBPK/BE 任务';
  INPUT_IDS.forEach(id => {
    if (merged[id] != null) ui[id].value = merged[id];
  });
  $('caseName').textContent = state.caseName;
  ui.json.value = JSON.stringify(readCase(), null, 2);
  persistCase();
  if (!skipLog) logLocal(`已载入参数集：${state.caseName}`);
}

function persistCase() {
  try {
    localStorage.setItem('pbpk-real-case', JSON.stringify(readCase()));
  } catch (_) {
    // ignore localStorage failures
  }
}

function restoreCase() {
  try {
    const raw = localStorage.getItem('pbpk-real-case');
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (_) {
    return null;
  }
}

function setStatus(kind, title, text) {
  $('state').className = `state ${kind}`;
  $('state').textContent = title;
  $('stext').textContent = text;
}

function setProgress(value) {
  $('bar').style.width = `${clamp((value || 0) * 100, 0, 100)}%`;
}

function logLocal(text) {
  state.localLogs.unshift({ at: new Date().toLocaleString('zh-CN'), text });
  state.localLogs = state.localLogs.slice(0, 20);
  if (!state.backendLogs.length) renderLogs(state.localLogs);
}

function setLogPage(nextPage, entries) {
  const list = entries && entries.length ? entries : [];
  const totalPages = Math.max(1, Math.ceil(Math.max(list.length, 1) / state.logPageSize));
  state.logPage = clamp(nextPage, 1, totalPages);
  renderLogs(entries);
}

function renderLogs(entries) {
  const list = entries && entries.length ? entries : [{ at: new Date().toLocaleString('zh-CN'), text: 'No logs yet.' }];
  const totalPages = Math.max(1, Math.ceil(list.length / state.logPageSize));
  state.logPage = clamp(state.logPage, 1, totalPages);
  const start = (state.logPage - 1) * state.logPageSize;
  const pageItems = list.slice(start, start + state.logPageSize);
  $('log').innerHTML = pageItems.map(item => `<div><strong>${item.at}</strong><br>${item.text || item.t || ''}</div>`).join('');
  $('logPageInfo').textContent = `${state.logPage} / ${totalPages}`;
  $('logPrev').disabled = state.logPage <= 1;
  $('logNext').disabled = state.logPage >= totalPages;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = text;
  }
  if (!response.ok) {
    throw new Error(typeof data === 'string' ? data : data?.detail || `HTTP ${response.status}`);
  }
  return data;
}

function renderChecks(checks) {
  $('badge').textContent = `${checks.filter(item => item.ok).length}/${checks.length}`;
  $('checks').innerHTML = checks.map(item => `
    <div class="check">
      <div style="display:flex;gap:10px;align-items:center"><span class="dot ${item.ok ? 'ok' : 'bad'}"></span><span>${item.name}</span></div>
      <div class="detail">${item.detail}</div>
    </div>
  `).join('');
}

async function selfCheck() {
  setStatus('run', '自检中', '正在核验 Python 包、仓库落地和后端冒烟测试。');
  setProgress(0.08);
  $('countdown').textContent = '倒计时 --';
  $('countdownDetail').textContent = '系统自检';
  try {
    const data = await api('/api/self-check');
    renderChecks(data.checks || []);
    setStatus(data.ok ? 'ok' : 'bad', data.ok ? '自检通过' : '自检有缺口', data.ok ? '后端、包依赖和本地模拟链路已通过检查。' : '有仓库或依赖未齐全，请查看自检列表。');
    setProgress(data.ok ? 0.16 : 0.12);
    logLocal(`系统自检完成：${data.ok ? '通过' : '存在缺口'}。`);
  } catch (error) {
    setStatus('bad', '自检失败', error.message);
    setProgress(0);
    logLocal(`系统自检失败：${error.message}`);
  }
}

function chartLine({ series, width = 860, height = 350, xLabel, yLabel, yMin = 0, yMax }) {
  if (!series.length) return '';
  const p = { l: 56, r: 18, t: 18, b: 42 };
  const pw = width - p.l - p.r;
  const ph = height - p.t - p.b;
  const allPoints = series.flatMap(item => item.data);
  const x0 = Math.min(...allPoints.map(point => point[0]));
  const x1 = Math.max(...allPoints.map(point => point[0]));
  const computedMax = Math.max(...allPoints.map(point => point[1]), 0.001) * 1.12;
  const maxY = yMax ?? computedMax;
  const sx = x => p.l + ((x - x0) / (x1 - x0 || 1)) * pw;
  const sy = y => p.t + ph - ((y - yMin) / (maxY - yMin || 1)) * ph;
  const grid = [
    ...Array.from({ length: 7 }, (_, i) => `<line x1="${p.l + (pw * i) / 6}" y1="${p.t}" x2="${p.l + (pw * i) / 6}" y2="${p.t + ph}" class="tick" />`),
    ...Array.from({ length: 6 }, (_, i) => `<line x1="${p.l}" y1="${p.t + (ph * i) / 5}" x2="${p.l + pw}" y2="${p.t + (ph * i) / 5}" class="tick" />`),
  ];
  const paths = series.map(item => `<path d="${item.data.map((point, index) => `${index ? 'L' : 'M'} ${sx(point[0]).toFixed(1)} ${sy(point[1]).toFixed(1)}`).join(' ')}" fill="none" stroke="${item.color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />`).join('');
  const legends = series.map((item, index) => `<rect x="${p.l + 8 + index * 160}" y="14" width="12" height="12" rx="3" fill="${item.color}" /><text x="${p.l + 26 + index * 160}" y="24" class="leg">${item.name}</text>`).join('');
  const xticks = Array.from({ length: 7 }, (_, i) => `<text x="${p.l + (pw * i) / 6}" y="${height - 12}" text-anchor="middle" class="lab">${F(x0 + ((x1 - x0) * i) / 6, 1)}</text>`).join('');
  const yticks = Array.from({ length: 6 }, (_, i) => `<text x="10" y="${p.t + (ph * i) / 5 + 4}" class="lab">${F(maxY - ((maxY - yMin) * i) / 5, 2)}</text>`).join('');
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${grid.join('')}<line x1="${p.l}" y1="${p.t + ph}" x2="${p.l + pw}" y2="${p.t + ph}" class="axis" /><line x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${p.t + ph}" class="axis" />${paths}${legends}${xticks}${yticks}<text x="${width / 2}" y="${height - 2}" text-anchor="middle" class="lab">${xLabel}</text><text x="16" y="${height / 2}" transform="rotate(-90 16 ${height / 2})" text-anchor="middle" class="lab">${yLabel}</text></svg>`;
}

function heatmap(reference, test) {
  const times = reference.times;
  const regions = [
    ['胃', 'stomach', '#7ee6ff'], ['十二指肠', 'duodenum', '#8b5cf6'], ['空肠', 'jejunum', '#34d399'], ['回肠', 'ileum', '#fbbf24'], ['结肠', 'colon', '#fb7185'],
    ['门静脉', 'portal', '#93c5fd'], ['肝脏', 'liver', '#c084fc'], ['血浆', 'plasma', '#7ee6ff'], ['心脏', 'heart', '#38bdf8'], ['肾脏', 'kidney', '#22c55e'], ['肌肉', 'muscle', '#f97316'], ['脂肪', 'fat', '#fb7185'],
  ];
  const w = 640, h = 350, p = { l: 140, r: 18, t: 18, b: 36 };
  const pw = w - p.l - p.r;
  const ph = h - p.t - p.b;
  const cellW = pw / times.length;
  const cellH = ph / regions.length;
  const maxValue = Math.max(...regions.flatMap(([, key]) => [...reference.series[key], ...test.series[key]]), 0.001);
  const alpha = value => clamp(value / maxValue, 0, 1);
  let body = '';
  regions.forEach(([label, key, color], row) => {
    const merged = reference.series[key].map((value, index) => Math.max(value, test.series[key][index] || 0));
    const peak = Math.max(...merged);
    const auc = merged.reduce((sum, value, index) => {
      if (!index) return 0;
      return sum + (times[index] - times[index - 1]) * (value + merged[index - 1]) / 2;
    }, 0);
    body += `<text x="8" y="${p.t + row * cellH + cellH * .66}" class="leg">${label}</text>`;
    times.forEach((timePoint, col) => {
      const value = merged[col];
      const x = p.l + col * cellW;
      const y = p.t + row * cellH;
      body += `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${Math.ceil(cellW) + 1}" height="${Math.ceil(cellH) + 1}" rx="2" fill="${color}" fill-opacity="${0.14 + alpha(value) * 0.78}" />`;
    });
    body += `<text x="${w - 4}" y="${p.t + row * cellH + cellH * .66}" text-anchor="end" class="lab">${F(peak, 2)} | AUC ${F(auc, 2)}</text>`;
  });
  const xticks = [0,4,8,12,16,20,24].map((tick, index) => `<text x="${p.l + (pw * index) / 6}" y="${h - 8}" text-anchor="middle" class="lab">${tick}</text>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><rect x="${p.l}" y="${p.t}" width="${pw}" height="${ph}" rx="16" fill="rgba(255,255,255,0.015)" stroke="rgba(148,163,184,0.10)" />${body}${xticks}<text x="${w / 2}" y="14" text-anchor="middle" class="lab">时间 (h)</text></svg>`;
}

function bodyFlow(result) {
  const ref = result.reference.series;
  const w = 620, h = 350;
  const nodes = [
    { x: 90, y: 60, label: '胃', color: '#7ee6ff', value: ref.stomach.at(-1) },
    { x: 210, y: 60, label: '十二指肠', color: '#8b5cf6', value: ref.duodenum.at(-1) },
    { x: 330, y: 60, label: '空肠', color: '#34d399', value: ref.jejunum.at(-1) },
    { x: 450, y: 60, label: '回肠', color: '#fbbf24', value: ref.ileum.at(-1) },
    { x: 570, y: 60, label: '结肠', color: '#fb7185', value: ref.colon.at(-1) },
    { x: 170, y: 180, label: '门静脉', color: '#93c5fd', value: ref.portal.at(-1) },
    { x: 330, y: 180, label: '肝脏', color: '#c084fc', value: ref.liver.at(-1) },
    { x: 490, y: 180, label: '血浆', color: '#7ee6ff', value: ref.plasma.at(-1) },
    { x: 110, y: 290, label: '心脏', color: '#38bdf8', value: ref.heart.at(-1) },
    { x: 250, y: 290, label: '肾脏', color: '#22c55e', value: ref.kidney.at(-1) },
    { x: 390, y: 290, label: '肌肉', color: '#f97316', value: ref.muscle.at(-1) },
    { x: 530, y: 290, label: '脂肪', color: '#fb7185', value: ref.fat.at(-1) },
  ];
  const maxValue = Math.max(...nodes.map(item => item.value), 0.001);
  const lines = [[90,86,210,86],[210,86,330,86],[330,86,450,86],[450,86,570,86],[330,112,330,154],[330,206,490,206],[490,230,110,266],[490,230,250,266],[490,230,390,266],[490,230,530,266]];
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><rect x="12" y="16" width="${w - 24}" height="${h - 32}" rx="24" fill="rgba(255,255,255,0.03)" stroke="rgba(148,163,184,0.10)" />${lines.map(([x1,y1,x2,y2]) => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="rgba(126,230,255,0.34)" stroke-width="2" stroke-dasharray="5 6" />`).join('')}${nodes.map(node => { const intensity = clamp(node.value / maxValue, 0, 1); const bw = 100, bh = 52; return `<rect x="${node.x - bw / 2}" y="${node.y - bh / 2}" width="${bw}" height="${bh}" rx="18" fill="rgba(255,255,255,0.04)" stroke="${node.color}" stroke-opacity="${0.35 + intensity * 0.55}" /><rect x="${node.x - bw / 2 + 6}" y="${node.y + 12}" width="${(bw - 12) * intensity}" height="6" rx="3" fill="${node.color}" /><text x="${node.x}" y="${node.y - 2}" text-anchor="middle" class="leg">${node.label}</text><text x="${node.x}" y="${node.y + 24}" text-anchor="middle" class="lab">${F(node.value, 2)}</text>`; }).join('')}</svg>`;
}

function renderReport(rows) {
  $('rep').innerHTML = rows.map(row => `<tr><td>${row.region}</td><td>${F(row.reference_auc, 2)}</td><td>${F(row.test_auc, 2)}</td><td>${F(row.reference_cmax, 2)}</td><td>${F(row.test_cmax, 2)}</td><td>${F(row.reference_tmax, 1)} / ${F(row.test_tmax, 1)}</td></tr>`).join('');
}

function renderCards(result) {
  const study = result.study.bioeq;
  const cards = [
    { label: 'f2 因子', value: F(result.f2, 1), sub: '溶出相似性' },
    { label: 'IVIVC r', value: F(result.ivivc.r, 3), sub: `r² ${F(result.ivivc.r2, 3)}` },
    { label: 'Ref AUC', value: F(result.reference.auc, 2), sub: `Cmax ${F(result.reference.cmax, 2)}` },
    { label: 'Test AUC', value: F(result.test.auc, 2), sub: `Cmax ${F(result.test.cmax, 2)}` },
    { label: 'AUC GMR', value: P(study.auc.point_estimate), sub: `${P(study.auc.lower_90ci)} - ${P(study.auc.upper_90ci)}` },
    { label: 'Cmax GMR', value: P(study.cmax.point_estimate), sub: `${P(study.cmax.lower_90ci)} - ${P(study.cmax.upper_90ci)}` },
    { label: 'BE 成功率', value: P(result.study.passRate), sub: study.pass ? '本次统计满足 80-125%' : '本次统计未满足 80-125%' },
    { label: '后端耗时', value: `${F(result.runtimeSeconds, 2)}s`, sub: '真实本地计算耗时' },
  ];
  $('cards').innerHTML = cards.map(card => `<div class="card"><div class="k">${card.label}</div><div class="v">${card.value}</div><div class="s">${card.sub}</div></div>`).join('');
}

function renderResult(result) {
  state.latest = result;
  renderCards(result);
  const ref = result.reference;
  const test = result.test;
  $('steps').textContent = ref.times.length;
  $('beMini').textContent = P(result.study.passRate);
  $('flowHint').textContent = `f2 ${F(result.f2, 1)} / IVIVC r ${F(result.ivivc.r, 3)}`;
  $('meta').textContent = `AUC ${P(result.study.bioeq.auc.point_estimate)} (${P(result.study.bioeq.auc.lower_90ci)} - ${P(result.study.bioeq.auc.upper_90ci)}) | Cmax ${P(result.study.bioeq.cmax.point_estimate)} (${P(result.study.bioeq.cmax.lower_90ci)} - ${P(result.study.bioeq.cmax.upper_90ci)})`;
  $('pk').innerHTML = chartLine({
    series: [
      { name: 'Reference', color: '#7ee6ff', data: ref.times.map((time, index) => [time, ref.series.plasma[index]]) },
      { name: 'Test', color: '#8b5cf6', data: test.times.map((time, index) => [time, test.series.plasma[index]]) },
    ],
    xLabel: 'Time (h)',
    yLabel: 'Plasma concentration',
  });
  $('iv').innerHTML = chartLine({
    series: [
      { name: 'Ref dissolution', color: '#34d399', data: ref.times.map((time, index) => [time, ref.fractionDissolved[index]]) },
      { name: 'Test dissolution', color: '#fb7185', data: test.times.map((time, index) => [time, test.fractionDissolved[index]]) },
      { name: 'Ref absorbed', color: '#fbbf24', data: ref.times.map((time, index) => [time, ref.fractionAbsorbed[index]]) },
    ],
    xLabel: 'Time (h)',
    yLabel: 'Fraction',
    yMax: 1.05,
  });
  $('be').innerHTML = chartLine({
    series: [
      { name: 'BE success', color: '#fb7185', data: result.study.trialCurve.map(item => [item.trial, item.passRate]) },
    ],
    xLabel: 'Trial',
    yLabel: 'Cumulative pass rate',
    yMax: 1.05,
  });
  $('sil').innerHTML = bodyFlow(result);
  $('heat').innerHTML = heatmap(ref, test);
  renderReport(result.regionReport || []);
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await api(`/api/jobs/${state.jobId}`);
    state.backendLogs = job.logs || [];
    renderLogs(state.backendLogs);
    $('countdown').textContent = job.status === 'completed' ? '倒计时 0s' : `倒计时 ${job.eta_seconds ?? '--'}s`;
    $('countdownDetail').textContent = job.phase_label || job.phase || '运行中';
    setProgress(job.progress || 0);
    $('caseName').textContent = job.params?.name || state.caseName;
    if (job.status === 'failed') {
      state.busy = false;
      $('run').disabled = false;
      setStatus('bad', '任务失败', job.error || job.message || '后端执行失败。');
      logLocal(`后端任务失败：${job.message || job.error}`);
      return;
    }
    if (job.status === 'completed') {
      state.busy = false;
      $('run').disabled = false;
      setStatus('ok', '已完成', job.message || '后端任务已完成。');
      renderResult(job.result);
      return;
    }
    setStatus('run', job.phase_label || '运行中', job.message || '本地后端正在执行。');
    window.setTimeout(pollJob, 800);
  } catch (error) {
    state.busy = false;
    $('run').disabled = false;
    setStatus('bad', '轮询失败', error.message);
    logLocal(`轮询任务失败：${error.message}`);
  }
}

async function runAnalysis() {
  if (state.busy) return;
  state.busy = true;
  $('run').disabled = true;
  state.backendLogs = [];
  state.logPage = 1;
  updateJsonFromForm();
  setStatus('run', '提交任务', '正在把参数提交给本地后端。');
  setProgress(0.02);
  $('countdown').textContent = '倒计时 --';
  $('countdownDetail').textContent = '任务创建中';
  logLocal('开始提交真实后端任务。');
  try {
    const response = await api('/api/run', {
      method: 'POST',
      body: JSON.stringify(readCase()),
    });
    state.jobId = response.job_id;
    $('countdown').textContent = `倒计时 ${response.eta_seconds ?? '--'}s`;
    $('countdownDetail').textContent = '后端已接单';
    logLocal(`任务已提交，Job ${response.job_id}`);
    window.setTimeout(pollJob, 250);
  } catch (error) {
    state.busy = false;
    $('run').disabled = false;
    setStatus('bad', '提交失败', error.message);
    setProgress(0);
    logLocal(`任务提交失败：${error.message}`);
  }
}

function exportJson() {
  const payload = state.latest || readCase();
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `pbpk-real-${Date.now()}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
  logLocal('已导出当前结果/参数 JSON。');
}

function loadJsonText(text) {
  const parsed = JSON.parse(text);
  applyCase(parsed);
  setStatus('ok', 'JSON 已应用', '参数已写入表单，可直接运行。');
}

function demo() {
  applyCase({
    name: '真实本地 PBPK/BE 任务',
    dose: 100,
    weight: 70,
    cl: 15,
    v: 80,
    ka: 1.2,
    ktr: 1.05,
    pka: 5.2,
    logP: 2.4,
    permeability: 1.4,
    solubility: 1.0,
    refDiss: 1.00,
    refSol: 1.00,
    refPart: 1.00,
    refFabs: 0.92,
    testDiss: 0.82,
    testSol: 0.95,
    testPart: 1.15,
    testFabs: 0.86,
    subjects: 24,
    trials: 40,
    clcv: 20,
    vcv: 15,
    kacv: 18,
    food: 1.00,
    emax: 1.0,
    ec50: 2.0,
    hill: 1.2,
    e0: 0,
  });
  setStatus('ok', '示例已载入', '当前参数会提交给本地 Python 后端执行。');
}

function bind() {
  INPUT_IDS.forEach(id => ui[id].addEventListener('change', updateJsonFromForm));
  $('logPrev').addEventListener('click', () => setLogPage(state.logPage - 1, state.backendLogs.length ? state.backendLogs : state.localLogs));
  $('logNext').addEventListener('click', () => setLogPage(state.logPage + 1, state.backendLogs.length ? state.backendLogs : state.localLogs));
  $('demo').addEventListener('click', demo);
  $('run').addEventListener('click', runAnalysis);
  $('check').addEventListener('click', selfCheck);
  $('exp').addEventListener('click', exportJson);
  $('load').addEventListener('click', () => $('file').click());
  $('file').addEventListener('change', async event => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const text = await file.text();
    try {
      loadJsonText(text);
    } catch (error) {
      setStatus('bad', 'JSON 失败', error.message);
      logLocal(`JSON 文件解析失败：${error.message}`);
    }
  });
  ui.json.addEventListener('change', () => {
    try {
      loadJsonText(ui.json.value);
    } catch (error) {
      setStatus('bad', 'JSON 失败', error.message);
      logLocal(`JSON 文本解析失败：${error.message}`);
    }
  });
}

async function boot() {
  bind();
  const restored = restoreCase();
  if (restored) {
    applyCase(restored, true);
    logLocal('已恢复上次参数。');
  } else {
    demo();
  }
  ui.json.value = JSON.stringify(readCase(), null, 2);
  renderLogs(state.localLogs);
  await selfCheck();
}

boot();
})();

