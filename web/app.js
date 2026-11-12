/* Core: data, global filters, aggregation, chart helpers. All filtering runs in the browser
   against the daily table, so controls respond instantly and never re-query. */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const api = async (u, o) => { const r = await fetch(u, o); if (!r.ok) throw new Error((await r.text()).slice(0, 200)); return r.json(); };
const post = (u, b) => api(u, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(b)});
const nf = (v, d) => v == null || Number.isNaN(v) ? 'n/a' : Number(v).toLocaleString('en-US', {maximumFractionDigits: d ?? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2)});
const clock = h => { if (h == null) return 'n/a'; h = ((h % 24) + 24) % 24; const m = Math.round((h % 1) * 60) % 60; return `${Math.floor(h) % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`; };
const pace = p => p == null ? 'n/a' : `${Math.floor(p)}:${String(Math.round((p % 1) * 60) % 60).padStart(2, '0')}`;
const CLOCK = new Set(['bed_clock_adj', 'wake_clock']);
const show = (m, v) => CLOCK.has(m) ? clock(v) : nf(v) + (B.metrics[m] ? ' ' + B.metrics[m].unit : '');

let B = null, CH = [], STATE = null;
const G = {start:null, end:null, freq:'W', roll:0, day:'all', act:'all', worn:false, norm:'abs'};
const FREQ = {D:'Day', W:'Week', M:'Month', Q:'Quarter', Y:'Year'};
const series = n => css('--s' + n);
const DOMAIN = {Activity:2, Training:3, Heart:8, Sleep:7, Body:1, Mobility:6, Other:5};
const domainColor = m => series(DOMAIN[B.metrics[m]?.group] || 1);

function dayIdx() {
  const out = [], D = B.data;
  for (let i = 0; i < B.dates.length; i++) {
    const t = B.dates[i];
    if (t < G.start || t > G.end) continue;
    if (G.day === 'weekend' && !D.weekend[i]) continue;
    if (G.day === 'weekday' && D.weekend[i]) continue;
    if (G.act === 'workout' && !D.workouts[i]) continue;
    if (G.act === 'rest' && D.workouts[i] !== 0) continue;
    if (G.worn && !D.worn_day[i]) continue;
    out.push(i);
  }
  return out;
}
function bucket(t, f) {
  if (f === 'D') return t;
  if (f === 'Y') return t.slice(0, 4);
  if (f === 'M') return t.slice(0, 7);
  if (f === 'Q') return t.slice(0, 4) + ' Q' + (Math.floor((+t.slice(5, 7) - 1) / 3) + 1);
  const d = new Date(t + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - (d.getUTCDay() + 6) % 7); return d.toISOString().slice(0, 10);
}
const median = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), n = s.length >> 1; return s.length % 2 ? s[n] : (s[n - 1] + s[n]) / 2; };
const mean = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
const quant = (a, q) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), p = (s.length - 1) * q, l = Math.floor(p); return s[l] + (s[Math.min(l + 1, s.length - 1)] - s[l]) * (p - l); };
function combine(vals, how) { return how === 'sum' ? vals.reduce((a, b) => a + b, 0) : how === 'max' ? Math.max(...vals) : how === 'median' ? median(vals) : mean(vals); }

/* A bucket with no observations is null, never zero. n carries the coverage into tooltips. */
function aggregate(m, f = G.freq, idx = dayIdx()) {
  const col = B.data[m], how = B.metrics[m]?.agg || 'mean', map = new Map();
  for (const i of idx) { const k = bucket(B.dates[i], f); if (!map.has(k)) map.set(k, []); if (col[i] != null) map.get(k).push(col[i]); }
  const t = [], v = [], n = [];
  for (const [k, a] of map) { t.push(k); n.push(a.length); v.push(a.length ? combine(a, how) : null); }
  return {t, v, n};
}
function rolling(v, w) { return v.map((_, i) => { const a = v.slice(Math.max(0, i - w + 1), i + 1).filter(x => x != null); return a.length >= Math.ceil(w / 2) ? mean(a) : null; }); }
function normalise(m, v) {
  if (G.norm === 'abs') return v;
  const all = B.data[m].filter(x => x != null), med = median(all);
  if (G.norm === 'pct') return v.map(x => x == null ? null : 100 * (x - med) / med);
  const mad = 1.4826 * median(all.map(x => Math.abs(x - med))) || 1;
  return v.map(x => x == null ? null : (x - med) / mad);
}
function metricSeries(m) {
  const a = aggregate(m); let v = a.v; if (G.roll > 1) v = rolling(v, G.roll);
  return {t: a.t, v: normalise(m, v), n: a.n, raw: a.v};
}

/* Charts */
function base() {
  const text = css('--text2'), line = css('--grid');
  return {animation:false, textStyle:{color:text, fontFamily:'inherit'}, grid:{left:52, right:18, top:30, bottom:28, containLabel:false},
    tooltip:{trigger:'axis', backgroundColor:css('--raised'), borderColor:css('--line'), textStyle:{color:css('--text'), fontSize:12.5}, axisPointer:{lineStyle:{color:css('--muted')}}, confine:true},
    xAxis:{type:'category', axisLine:{lineStyle:{color:css('--line')}}, axisTick:{show:false}, axisLabel:{color:css('--muted'), fontSize:11, hideOverlap:true}, splitLine:{show:false}},
    yAxis:{type:'value', scale:true, axisLine:{show:false}, axisTick:{show:false}, axisLabel:{color:css('--muted'), fontSize:11}, splitLine:{lineStyle:{color:line}}},
    toolbox:{right:0, top:-4, itemSize:12, feature:{saveAsImage:{title:'PNG', pixelRatio:2, backgroundColor:css('--surface')}}, iconStyle:{borderColor:css('--muted')}}};
}
function mount(el, opt, onPick) {
  const c = echarts.init(el, null, {renderer:'canvas'}); if (opt.toolbox) opt.toolbox.show = false; c.setOption(opt); CH.push(c);
  if (opt.toolbox) { el.addEventListener('mouseenter', () => c.setOption({toolbox:{show:true}})); el.addEventListener('mouseleave', () => c.setOption({toolbox:{show:false}})); }
  if (onPick) c.getZr().on('click', e => { if (!c.containPixel('grid', [e.offsetX, e.offsetY])) return; const p = c.convertFromPixel({seriesIndex:0}, [e.offsetX, e.offsetY]); const k = opt.xAxis.data?.[Math.round(p[0])]; if (k != null) onPick(k); });
  return c;
}
function pick(f) { return k => { if (f === 'D' || (k.length === 10 && G.freq !== 'W')) location.hash = 'day/' + k;
  else if (k.length === 10) { location.hash = 'day/' + k; }
  else if (k.length === 7) { G.start = k + '-01'; G.end = k + '-31'; G.freq = 'D'; route(); }
  else if (k.length === 4) { G.start = k + '-01-01'; G.end = k + '-12-31'; G.freq = 'M'; route(); } }; }
function lineChart(el, cfg) {
  const o = base(), xs = cfg.series[0].t, un = cfg.unit || '';
  o.xAxis.data = xs; o.yAxis.axisLabel.formatter = cfg.yfmt || (v => nf(v));
  if (cfg.bars) o.yAxis.scale = false;  // bars encode length, so the axis must include zero
  if (cfg.hideX) { o.xAxis.axisLabel.show = false; o.grid.bottom = 8; o.grid.top = 10; }
  if (cfg.series.length > 1) o.legend = {top:0, left:0, icon:'roundRect', itemWidth:12, itemHeight:3, textStyle:{color:css('--text2'), fontSize:12}};
  o.tooltip.formatter = ps => `<b>${ps[0].axisValue}</b><br>` + ps.map(p => `${p.marker}${esc(p.seriesName)}: ${p.value == null ? 'no data' : (cfg.fmt ? cfg.fmt(p.value) : nf(p.value) + ' ' + un)}${cfg.series[p.seriesIndex].n ? ` <span style="opacity:.6">(${cfg.series[p.seriesIndex].n[p.dataIndex]} days)</span>` : ''}`).join('<br>');
  const fade = c => ({type:'linear', x:0, y:0, x2:0, y2:1, colorStops:[{offset:0, color:c + '38'}, {offset:1, color:c + '00'}]});
  o.series = cfg.series.map((s, i) => ({name:s.name, type:cfg.bars ? 'bar' : 'line', data:s.v, connectNulls:false, showSymbol:xs.length < 40, symbolSize:6, lineStyle:{width:2, color:s.color || series(i + 1)}, itemStyle:{color:s.color || series(i + 1), borderRadius:[4, 4, 0, 0]}, barMaxWidth:14, smooth:false, stack:cfg.stack ? 'a' : undefined, areaStyle:cfg.stack ? {opacity:.8} : (cfg.series.length === 1 && !cfg.bars ? {color:fade(s.color || series(i + 1))} : undefined), z:cfg.series.length - i,
    markLine:i === 0 && cfg.marks?.length ? {silent:true, symbol:'none', lineStyle:{color:css('--muted'), type:'dashed', width:1}, label:{color:css('--muted'), fontSize:10.5, formatter:p => p.data.name || ''}, data:cfg.marks} : undefined}));
  if (cfg.zoom !== false && xs.length > 60) { o.dataZoom = [{type:'inside'}]; }
  return mount(el, o, cfg.pick);
}
function card(html, cls = 'c12') { const d = document.createElement('section'); d.className = 'card ' + cls; d.innerHTML = html; return d; }
function chartCard(parent, title, sub, cls = 'c6', size = '') {
  const c = card(`<h2>${esc(title)}</h2>${sub ? `<div class="muted">${sub}</div>` : ''}<div class="chart ${size}"></div>`, cls); parent.append(c); return $('.chart', c);
}
function metricChart(parent, m, opts = {}) {
  const meta = B.metrics[m]; if (!B.available[m]) return dataGap(parent, meta.label, opts.cls);
  const el = chartCard(parent, opts.title || meta.label, `${meta.kind}${meta.note ? '. ' + esc(meta.note) : ''}`, opts.cls || 'c6', opts.size);
  const s = metricSeries(m);
  lineChart(el, {series:[{name:meta.label, t:s.t, v:s.v, n:s.n, color:domainColor(m)}], unit:G.norm === 'abs' ? meta.unit : G.norm === 'pct' ? '% vs lifetime median' : 'MAD vs lifetime median', bars:opts.bars, marks:opts.marks, pick:pick(G.freq), fmt:CLOCK.has(m) && G.norm === 'abs' ? clock : null, yfmt:CLOCK.has(m) && G.norm === 'abs' ? clock : null});
}
function dataGap(parent, label, cls = 'c6') { parent.append(card(`<h2>${esc(label)}</h2><p class="empty">No ${esc(label.toLowerCase())} in this export, so this section is empty.</p>`, cls)); }
function table(cols, rows, opts = {}) {
  const w = document.createElement('div'); w.className = 'scroll';
  const render = () => { w.innerHTML = `<table><thead><tr>${cols.map(c => `<th data-k="${c.k}">${esc(c.t)}</th>`).join('')}</tr></thead><tbody>${rows.map((r, i) => `<tr data-i="${i}" class="${opts.onRow ? 'link' : ''}">${cols.map(c => `<td>${c.f ? c.f(r[c.k], r) : esc(r[c.k] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table>`; };
  let sort = null, dir = 1;
  w.addEventListener('click', e => {
    const th = e.target.closest('th[data-k]');
    if (th) { const k = th.dataset.k; dir = sort === k ? -dir : 1; sort = k; rows.sort((a, b) => (a[k] == null) - (b[k] == null) || (a[k] > b[k] ? dir : -dir)); render(); return; }
    const tr = e.target.closest('tr[data-i]'); if (tr && opts.onRow) opts.onRow(rows[tr.dataset.i]);
  });
  render(); return w;
}
function tiles(items) { const d = document.createElement('div'); d.className = 'tiles'; d.innerHTML = items.map(t => `<div class="tile"><span>${esc(t[0])}</span><b>${esc(t[1])}</b><i>${esc(t[2] || '')}</i></div>`).join(''); return d; }
function metricSelect(val, id, includeNone) {
  const groups = {}; for (const [k, m] of Object.entries(B.metrics)) if (B.available[k]) (groups[m.group] ??= []).push([k, m.label]);
  return `<select id="${id}" aria-label="metric">${includeNone ? '<option value="">None</option>' : ''}${Object.entries(groups).map(([g, a]) => `<optgroup label="${g}">${a.map(([k, l]) => `<option value="${k}" ${k === val ? 'selected' : ''}>${esc(l)}</option>`).join('')}</optgroup>`).join('')}</select>`;
}

/* Shell */
const NAV = [['Summary', [['overview', 'Overview'], ['discover', 'Discoveries'], ['timeline', 'Timeline'], ['records', 'Records']]],
  ['Domains', [['activity', 'Activity'], ['running', 'Running'], ['workouts', 'Workouts'], ['heart', 'Heart'], ['sleep', 'Sleep'], ['body', 'Body and other'], ['rhythms', 'Rhythms']]],
  ['Investigate', [['explorer', 'Explorer'], ['lab', 'Lab'], ['cohorts', 'Cohorts'], ['ask', 'Ask']]],
  ['Review', [['reports', 'Reports'], ['quality', 'Data quality'], ['privacy', 'Privacy and data']]]];
function drawNav() { $('#links').innerHTML = NAV.map(([s, a]) => `<div class="sec">${s}</div>` + a.map(([k, l]) => `<a href="#${k}" data-k="${k}">${l}</a>`).join('')).join(''); }
const FILTERED = new Set(['activity', 'heart', 'sleep', 'body']);
function drawControls(name) {
  const box = $('#controls'); if (!FILTERED.has(name)) { box.innerHTML = ''; return; }
  const seg = (id, opts, val) => `<span class="seg" data-g="${id}">${opts.map(([k, l]) => `<button type="button" data-v="${k}" class="${String(val) === String(k) ? 'on' : ''}">${l}</button>`).join('')}</span>`;
  const sel = (id, label, opts, val) => `<label>${label}</label><select data-s="${id}">${opts.map(([k, l]) => `<option value="${k}" ${String(val) === String(k) ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
  const end = B.dates.at(-1), back = d => new Date(Date.parse(end) - d * 864e5).toISOString().slice(0, 10);
  const rng = G.start === B.dates[0] ? 'all' : G.start === back(365) ? 'y' : G.start === back(90) ? 'q' : G.start === back(30) ? 'm' : '';
  box.innerHTML = seg('range', [['all', 'All'], ['y', '1Y'], ['q', '90D'], ['m', '30D']], rng) + seg('freq', Object.entries({D:'D', W:'W', M:'M', Q:'Q', Y:'Y'}), G.freq) +
   `<details class="more"><summary>Filters${(G.day !== 'all' || G.act !== 'all' || G.worn || G.norm !== 'abs' || G.roll) ? ' on' : ''}</summary><div class="pop"><label>From</label><input type="date" data-s="start" value="${G.start}"><label>To</label><input type="date" data-s="end" value="${G.end}">
   ${sel('roll', 'Rolling mean', [[0, 'Off'], [4, '4 points'], [8, '8 points'], [12, '12 points']], G.roll)}${sel('day', 'Days', [['all', 'All days'], ['weekday', 'Weekdays'], ['weekend', 'Weekends']], G.day)}${sel('act', 'Activity', [['all', 'All days'], ['workout', 'Workout days'], ['rest', 'Non-workout days']], G.act)}
   ${sel('norm', 'Scale', [['abs', 'Absolute'], ['pct', '% from lifetime median'], ['z', 'Robust z-score']], G.norm)}<label>Fully worn days only</label><input type="checkbox" data-s="worn" ${G.worn ? 'checked' : ''}></div></details>`;
  box.querySelector('[data-g=freq]').onclick = e => { if (e.target.dataset.v) { G.freq = e.target.dataset.v; route(); } };
  box.querySelector('[data-g=range]').onclick = e => { const v = e.target.dataset.v; if (!v) return; G.end = end; G.start = {all:B.dates[0], y:back(365), q:back(90), m:back(30)}[v]; if (v === 'm' || v === 'q') G.freq = 'D'; route(); };
  box.querySelectorAll('[data-s]').forEach(i => i.onchange = () => { const k = i.dataset.s; G[k] = i.type === 'checkbox' ? i.checked : k === 'roll' ? Number(i.value) : i.value; route(); });
}
const VIEWS = {};
async function route() {
  CH.forEach(c => c.dispose()); CH = [];
  const [name, ...rest] = (location.hash.slice(1) || 'overview').split('/');
  document.querySelectorAll('#links a').forEach(a => a.classList.toggle('on', a.dataset.k === name));
  drawControls(name);
  const v = $('#view'); v.innerHTML = '<p class="empty">Computing.</p>';
  try { const host = document.createElement('div'); await (VIEWS[name] || VIEWS.overview)(host, rest); v.replaceChildren(host); CH.forEach(c => c.resize()); }
  catch (e) { v.innerHTML = `<p class="empty">This view failed: ${esc(e.message)}</p>`; console.error(e); }
}
function head(host, title, lede) { host.insertAdjacentHTML('beforeend', `<h1>${esc(title)}</h1><p class="lede">${lede || ''}</p>`); }
function saveState(key) { return fetch('/api/state/' + key, {method:'PUT', headers:{'content-type':'application/json'}, body:JSON.stringify(STATE[key])}); }
window.addEventListener('hashchange', route);
window.addEventListener('resize', () => CH.forEach(c => c.resize()));
async function start() {
  drawNav();
  $('#theme').onclick = () => { const r = document.documentElement, dark = (r.dataset.theme || (matchMedia('(prefers-color-scheme:dark)').matches ? 'dark' : 'light')) === 'dark'; r.dataset.theme = dark ? 'light' : 'dark'; try { localStorage.theme = r.dataset.theme; } catch {} route(); };
  try { if (localStorage.theme) document.documentElement.dataset.theme = localStorage.theme; } catch {}
  B = await api('/api/boot');
  if (B.empty) { $('#view').innerHTML = '<h1>No data imported</h1><p class="lede">Run <code>./run.sh path/to/export.zip</code>.</p>'; return; }
  STATE = await api('/api/state'); G.start = B.dates[0]; G.end = B.dates.at(-1); route();
}
start();
