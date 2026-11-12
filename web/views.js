/* Views. Each receives a host element; charts and data come from app.js helpers. */
let INS = null;
const insights = async () => INS ??= await api('/api/insights');
const lastN = (m, n) => B.data[m].slice(-n).filter(x => x != null);
const lifetimeMedian = m => median(B.data[m].filter(x => x != null));
const go = h => location.hash = h;
const MONTHS = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ');
const niceDate = s => { const [y, m, d] = s.split('-'); return `${MONTHS[+m - 1]} ${d ? +d + ', ' : ''}${y}`; };
const pl = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

function bento(host) { const g = document.createElement('div'); g.className = 'grid'; host.append(g); return g; }
function tile(g, cls, html, onClick) { const c = document.createElement('section'); c.className = `card ${cls}${onClick ? ' link' : ''}`; c.innerHTML = html; if (onClick) c.onclick = onClick; g.append(c); return c; }
function spark(el, v, color) {
  const clean = v.map(x => x ?? null);
  mount(el, {animation:false, grid:{left:0, right:0, top:2, bottom:2}, xAxis:{type:'category', show:false, data:clean.map((_, i) => i)}, yAxis:{type:'value', show:false, scale:true},
    series:[{type:'line', data:clean, showSymbol:false, lineStyle:{width:2, color}, areaStyle:{color:{type:'linear', x:0, y:0, x2:0, y2:1, colorStops:[{offset:0, color:color + '40'}, {offset:1, color:color + '00'}]}}}]});
}
function vitalTile(g, cls, m, label, unit, days, opt = {}) {
  const now = mean(lastN(m, days)), prior = median(B.data[m].slice(-days - 365, -days).filter(x => x != null));
  const diff = now - prior, f = opt.fmt || nf;
  const dir = prior == null || now == null ? '' : Math.abs(diff) < (opt.flat ?? .01 * Math.abs(prior)) ? 'About the same as your past year' : `${f(Math.abs(diff))}${unit && !opt.fmt ? ' ' + unit : ''} ${diff > 0 ? 'above' : 'below'} your prior year`;
  const c = tile(g, cls, `<div class="eyebrow"><span class="dot" style="background:${opt.color}"></span>${label}</div><div class="big">${f(now)}<small>${unit}</small></div><div class="muted">${days}-day average</div><div class="delta">${dir}</div><div class="spark"></div>`, () => go(opt.to));
  const a = aggregate(m, opt.sparkFreq || 'W', B.dates.map((_, i) => i).slice(-(opt.sparkDays || 182)));
  spark($('.spark', c), a.v.filter(x => x != null), opt.color);
}
function yearHeat(el, m, color) {
  const rows = [], yrs = [...new Set(B.dates.map(t => t.slice(0, 4)))], col = B.data[m];
  const doy = t => Math.floor((Date.parse(t) - Date.parse(t.slice(0, 4) + '-01-01')) / 864e5);
  B.dates.forEach((t, i) => { if (col[i] != null) rows.push([doy(t), yrs.indexOf(t.slice(0, 4)), col[i], t]); });
  const hi = quant(col.filter(x => x != null), .97), o = base();
  delete o.toolbox; o.grid = {left:38, right:6, top:4, bottom:20};
  const ticks = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
  o.xAxis = {type:'category', data:[...Array(366).keys()], axisLine:{show:false}, axisTick:{show:false}, axisLabel:{color:css('--muted'), fontSize:11, interval:i => ticks.includes(i), formatter:v => MONTHS[ticks.indexOf(+v)] || ''}, splitArea:{show:false}};
  o.yAxis = {type:'category', data:yrs, inverse:true, axisLine:{show:false}, axisTick:{show:false}, axisLabel:{color:css('--muted'), fontSize:11}};
  o.visualMap = {show:false, min:0, max:hi, dimension:2, inRange:{color:[css('--seq0'), color || css('--seq1')]}};
  o.tooltip = {...o.tooltip, trigger:'item', formatter:p => `<b>${niceDate(p.data[3])}</b><br>${nf(p.data[2])} ${B.metrics[m].unit}`};
  o.series = [{type:'heatmap', data:rows, progressive:0, itemStyle:{borderColor:css('--surface'), borderWidth:.5}}];
  const c = mount(el, o); c.on('click', p => go('day/' + p.data[3]));
}
function clock24(el, vals, color) {
  const o = base(); delete o.xAxis; delete o.yAxis; delete o.toolbox; delete o.grid;
  o.polar = {radius:['18%', '86%']}; o.angleAxis = {type:'category', data:[...Array(24).keys()], startAngle:90, clockwise:true, axisLine:{show:false}, axisTick:{show:false}, splitLine:{show:false}, axisLabel:{color:css('--muted'), fontSize:11, interval:i => i % 6 === 0, formatter:v => ({0:'12a', 6:'6a', 12:'12p', 18:'6p'})[v] || ''}};
  o.radiusAxis = {show:false, min:0, max:'dataMax'}; o.tooltip = {...o.tooltip, trigger:'item', formatter:p => `${clock(p.dataIndex)}: ${nf(p.value)}`};
  o.series = [{type:'bar', coordinateSystem:'polar', data:vals, itemStyle:{color, borderRadius:3}, barCategoryGap:'14%'}];
  mount(el, o);
}
function hbars(el, labels, vals, color, fmt) {
  const o = base(); delete o.toolbox; o.grid = {left:92, right:44, top:4, bottom:4};
  o.xAxis = {type:'value', show:false, min:0}; o.yAxis = {type:'category', data:labels, inverse:true, axisLine:{show:false}, axisTick:{show:false}, axisLabel:{color:css('--text2'), fontSize:12.5}};
  o.tooltip = {...o.tooltip, trigger:'item', formatter:p => `${p.name}: ${fmt ? fmt(p.value) : nf(p.value)}`};
  o.series = [{type:'bar', data:vals, itemStyle:{color, borderRadius:[0, 5, 5, 0]}, barMaxWidth:16, label:{show:true, position:'right', color:css('--text2'), fontSize:12, formatter:p => fmt ? fmt(p.value) : nf(p.value)}}];
  mount(el, o);
}
const gradeBadge = g => `<span class="badge grade ${g}">Evidence ${g}</span>`;
let ERA_MAX = 1;
function eraColor(e) { return `color-mix(in srgb, var(--s2) ${Math.round(18 + 82 * Math.min(1, e.steps / ERA_MAX))}%, var(--fill))`; }
function eraRibbon(host, eras) {
  const total = eras.reduce((a, e) => a + e.months, 0); ERA_MAX = Math.max(...eras.map(e => e.steps));
  host.insertAdjacentHTML('beforeend', `<div class="ribbon">${eras.map(e => `<div style="flex:${e.months / total};background:${eraColor(e)}" title="${e.start} to ${e.end}: ${esc(e.place)}, ${esc(e.tag)}, ${nf(e.steps)} steps a day"></div>`).join('')}</div><div class="ribbonlab"><span>${niceDate(eras[0].start)}</span><span>${niceDate(eras.at(-1).end)}</span></div>`);
}
function eraList(host, eras) {
  host.insertAdjacentHTML('beforeend', `<div class="eras">${eras.map(e => `<div><h3><span class="dot" style="display:inline-block;margin-right:6px;background:${eraColor(e)}"></span>${niceDate(e.start)} to ${niceDate(e.end)}</h3><div class="muted">${esc(e.place)}. ${esc(e.tag)}.<br>${nf(e.steps)} steps a day${e.wake ? `, up at ${clock(e.wake)}` : ''}${e.rhr ? `, resting HR ${nf(e.rhr)}` : ''}</div></div>`).join('')}</div>`);
}

VIEWS.overview = async host => {
  const ins = await insights(), D = ins.deep, c = ins.cards;
  const yrs = (Date.parse(B.dates.at(-1)) - Date.parse(B.dates[0])) / 864e5 / 365.25;
  const steps = B.data.steps.reduce((a, b) => a + (b || 0), 0), miles = B.data.distance_mi.reduce((a, b) => a + (b || 0), 0);
  const g = bento(host); host.prepend(Object.assign(document.createElement('h1'), {textContent:'Your health, at a glance'}));
  const top = [...c].filter(x => !['Data coverage', 'Anomalies', 'Life eras', 'Places'].includes(x.category) && !/^Since /.test(x.headline) && x.grade !== 'C').sort((a, b) => b.score_parts.novelty - a.score_parts.novelty || b.score - a.score);
  const loss = D.sleep_loss, recent = mean(lastN('asleep_watch_h', 30));
  const sinceAvg = loss?.date ? mean(B.data.asleep_watch_h.filter((x, i) => x != null && B.dates[i] >= loss.date)) : null;
  const recovering = loss?.date && recent - sinceAvg >= .3;
  const stat = (v, l) => `<div><b>${v}</b><span>${l}</span></div>`;
  tile(g, 'c6 r2 hero', `<div class="eyebrow">Your record</div><div class="huge">${nf(steps / 1e6, 1)}M</div><div class="muted">steps across ${nf(yrs, 1)} years and ${nf(miles, 0)} miles on foot</div><p>${loss?.date ? `The biggest recent change: since ${niceDate(loss.date.slice(0, 7))} you have slept ${Math.abs(loss.sleep).toFixed(1)} hours ${loss.sleep < 0 ? 'less' : 'more'} a night, mostly from falling asleep ${nf(Math.abs(loss.bed) * 60, 0)} minutes ${loss.bed > 0 ? 'later' : 'earlier'}.${recovering ? ` The last 30 days are recovering, at ${recent.toFixed(1)} hours.` : ''}` : ''}</p><div class="herostats">${stat(nf(B.report.records_distinct / 1e6, 1) + 'M', 'records')}${stat(nf(B.data.steps.filter(x => x != null).length, 0), 'days of steps')}${stat(nf(B.data.runs.reduce((a, b) => a + (b || 0), 0), 0), 'runs')}${stat(D.places?.rows?.length ?? 0, 'zones with long stays')}</div>`);
  const col = {steps:series(2), sleep:series(7), heart:series(8), hrv:series(1), vo2:series(3)};
  vitalTile(g, 'c3', 'asleep_watch_h', 'Sleep', 'h', 30, {color:col.sleep, to:'sleep', flat:.1});
  vitalTile(g, 'c3', 'steps', 'Steps', '', 30, {color:col.steps, to:'activity', flat:300});
  vitalTile(g, 'c3', 'rhr', 'Resting heart rate', 'bpm', 30, {color:col.heart, to:'heart', flat:1});
  vitalTile(g, 'c3', 'hrv_sdnn', 'HRV (SDNN)', 'ms', 30, {color:col.hrv, to:'heart', flat:1});
  vitalTile(g, 'c3', 'vo2max', 'VO2 max estimate', '', 90, {color:col.vo2, to:'heart', flat:.4, sparkFreq:'M', sparkDays:730});
  const era = D.eras?.eras;
  if (era) { const t = tile(g, 'c9', `<div class="eyebrow">Life eras</div><div class="story">Your routine splits into ${era.length} chapters</div><div class="muted">Grouped by when your steps happen, when you wake and how much you move. Darker means more steps.</div>`, () => go('discover')); eraRibbon(t, era); eraList(t, era.slice(-3)); }
  const t1 = tile(g, 'c8', `<div class="eyebrow">Every day since ${B.dates[0].slice(0, 4)}</div><div class="story">Steps, one row per year</div><div class="chart" style="height:250px"></div><div class="muted">Blank cells are days with no data, not zero. Click any day to open it.</div>`);
  yearHeat($('.chart', t1), 'steps', series(2));
  const pl_ = D.places;
  if (pl_?.rows) { const r_ = [...pl_.rows].sort((a, b) => b.steps - a.steps), t = tile(g, 'c4', `<div class="eyebrow">Where you are</div><div class="story">You move differently by place</div><div class="chart" style="height:240px"></div><div class="muted">Median daily steps by time zone, stays of 7+ days.</div>`, () => go('discover')); hbars($('.chart', t), r_.map(r => r.place), r_.map(r => Math.round(r.steps)), series(2)); }
  const hourly = await api(`/api/rhythm?start=${new Date(Date.parse(B.dates.at(-1)) - 365 * 864e5).toISOString().slice(0, 10)}`);
  const hrs = Array(24).fill(0); hourly.hour_dow.forEach(r => hrs[r.h] += r.v / 7);
  const peak = hrs.indexOf(Math.max(...hrs));
  const t2 = tile(g, 'c4', `<div class="eyebrow">Your day</div><div class="story">You move most around ${clock(peak).replace(':00', '')}</div><div class="chart" style="height:300px"></div><div class="muted">Average steps per hour, past year.</div>`, () => go('rhythms'));
  clock24($('.chart', t2), hrs.map(v => Math.round(v)), series(2));
  briefing(g, ins);
  for (const x of top.slice(0, 3)) tile(g, 'c4', `<div class="eyebrow">${esc(x.category)}</div><div class="story" style="font-size:19px">${esc(x.headline)}</div><div class="muted">${esc(x.effect)}. ${esc(x.uncertainty.slice(0, 96))}</div><div style="margin-top:8px">${gradeBadge(x.grade)}</div>`, () => go('discover'));
  compareTile(g);
};
function briefing(g, ins) {
  const c = ins.cards, D = ins.deep, by = cat => c.filter(x => x.category === cat);
  const last = m => mean(lastN(m, 30)), rows = [], pct = (m, higher) => ({m, a:median(B.data[m].slice(-365).filter(x => x != null)), b:median(B.data[m].slice(-730, -365).filter(x => x != null)), higher});
  const moves = [pct('hrv_sdnn', 1), pct('rhr', -1), pct('vo2max', 1), pct('asleep_watch_h', 1), pct('steps', 1)].filter(x => x.a != null && x.b != null).map(x => ({...x, ch:(x.a - x.b) / x.b * x.higher}));
  const best = [...moves].sort((a, b) => b.ch - a.ch)[0], worst = [...moves].sort((a, b) => a.ch - b.ch)[0];
  rows.push(['Where you are now', `Averaging ${nf(last('steps'))} steps a day, ${nf(last('asleep_watch_h'))} h of sleep and a resting HR of ${nf(last('rhr'))} bpm over the last 30 days.`]);
  rows.push(['Most improved', `${B.metrics[best.m].label}: ${show(best.m, best.b)} to ${show(best.m, best.a)} year on year.`]);
  rows.push(['Most deteriorated', `${B.metrics[worst.m].label}: ${show(worst.m, worst.b)} to ${show(worst.m, worst.a)} year on year.`]);
  const dist = c.find(x => x.category === 'Life eras'); if (dist) rows.push(['Most distinctive', dist.finding.split('. ').slice(0, 1) + '.']);
  rows.push(['Changed recently', by('Long-term trends').slice(0, 1).map(x => x.headline)[0] || 'Nothing passed the evidence bar.']);
  const look = by('Data coverage')[0]; rows.push(['Worth a closer look', look ? look.headline + '.' : 'Nothing flagged.']);
  const robust = c.filter(x => x.grade === 'A').length, weak = c.filter(x => x.grade === 'C').length;
  rows.push(['How sure is this', `${robust} findings are strong, ${weak} are tentative. ${ins.n_surviving} of ${ins.n_tested} pre-listed relationships survived correction; the rest found nothing and are counted anyway.`]);
  tile(g, 'c8', `<div class="eyebrow">Briefing</div><div class="story">Seven answers from your data</div><dl class="brief">${rows.map(r => `<dt>${esc(r[0])}</dt><dd>${esc(r[1])}</dd>`).join('')}</dl>`);
}
function compareTile(g) {
  const ms = ['steps', 'asleep_watch_h', 'rhr', 'hrv_sdnn', 'vo2max'], n = B.dates.length, val = (m, a, b) => { const v = B.data[m].slice(Math.max(0, a), b).filter(x => x != null); return v.length ? mean(v) : null; };
  const cols = [['7 days', n - 7, n], ['30 days', n - 30, n], ['90 days', n - 90, n], ['1 year', n - 365, n], ['Same 90 days, last year', n - 455, n - 365], ['Lifetime', 0, n]];
  const t = tile(g, 'c12', `<div class="eyebrow">Baselines</div><div class="story">Now against your history</div>`);
  t.append(table([{k:'label', t:'Metric'}, ...cols.map((c, i) => ({k:'c' + i, t:c[0], f:v => nf(v)}))], ms.map(m => ({label:B.metrics[m].label, ...Object.fromEntries(cols.map((c, i) => ['c' + i, val(m, c[1], c[2])]))}))));
}

function eventChart(el, curves, unit, title) {
  const k = curves.east.map(x => x.k), o = {series:[{name:'Eastward', t:k.map(x => 'Day ' + x), v:curves.east.map(x => x.mean), color:series(1)}, {name:'Westward', t:k, v:curves.west.map(x => x.mean), color:series(2)}], unit, marks:[{xAxis:'Day 0', name:'Arrival'}], zoom:false};
  lineChart(el, o);
}
function renderViz(el, v, D) {
  if (!v) return false;
  if (v.kind === 'series') {
    const a = aggregate(v.metric, v.freq, B.dates.map((_, i) => i).filter(i => !v.window || (B.dates[i] >= v.window[0] && B.dates[i] <= v.window[1])));
    const mark = v.mark ? a.t.reduce((b, t) => t <= (v.freq === 'M' ? v.mark.slice(0, 7) : v.mark) ? t : b, a.t[0]) : null;
    lineChart(el, {series:[{name:B.metrics[v.metric].label, t:a.t, v:a.v, n:a.n, color:domainColor(v.metric)}], unit:B.metrics[v.metric].unit, marks:mark ? [{xAxis:mark}] : [], fmt:CLOCK.has(v.metric) ? clock : null, yfmt:CLOCK.has(v.metric) ? clock : null, pick:pick(v.freq)}); return true; }
  if (v.kind === 'bars') { lineChart(el, {series:[{name:v.title, t:v.x, v:v.y, color:series(1)}], unit:v.unit, bars:true, zoom:false}); return true; }
  if (v.kind === 'scatter') { post('/api/explore', {x:v.x, y:v.y, lag:v.lag}).then(r => scatter(el, r)); return true; }
  if (v.kind === 'dow') { const a = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((_, i) => mean(B.data[v.metric].filter((x, j) => x != null && B.data.dow[j] === i + 1))); lineChart(el, {series:[{name:B.metrics[v.metric].label, t:['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], v:a, color:domainColor(v.metric)}], bars:true, zoom:false, unit:B.metrics[v.metric].unit, fmt:CLOCK.has(v.metric) ? clock : null, yfmt:CLOCK.has(v.metric) ? clock : null}); return true; }
  if (v.kind === 'month') { const a = MONTHS.map((_, i) => mean(B.data[v.metric].filter((x, j) => x != null && B.data.mo[j] === i + 1))); lineChart(el, {series:[{name:B.metrics[v.metric].label, t:MONTHS, v:a, color:domainColor(v.metric)}], zoom:false, unit:B.metrics[v.metric].unit}); return true; }
  if (v.kind === 'event') { eventChart(el, D.jet.curves.bed, 'h vs prior 4 weeks'); return true; }
  if (v.kind === 'adapt') { const c = D.adapt[v.key].curve; lineChart(el, {series:[{name:'Correlation', t:c.map(x => x.lag + ' wk'), v:c.map(x => x.r), color:series(3)}], zoom:false, unit:'r', marks:[{xAxis:D.adapt[v.key].best + ' wk', name:'Best'}]}); return true; }
  if (v.kind === 'drivers') { hbars(el, D.drivers.parts.map(p => p.name), D.drivers.parts.map(p => Math.max(0, +(p.partial_r2 * 100).toFixed(1))), series(8), x => x + '%'); return true; }
  if (v.kind === 'places') { hbars(el, D.places.rows.map(r => r.place), D.places.rows.map(r => Math.round(r.steps)), series(2)); return true; }
  if (v.kind === 'spells') { const y = Object.keys(D.spells.by_year); lineChart(el, {series:[{name:'Spells', t:y, v:y.map(k => D.spells.by_year[k]), color:series(8)}], bars:true, zoom:false, unit:'spells'}); return true; }
  if (v.kind === 'drift') { lineChart(el, {series:[{name:'Median drift', t:D.drift.by_year.map(x => x.y), v:D.drift.by_year.map(x => x.med), n:D.drift.by_year.map(x => x.n), color:series(3)}], bars:true, zoom:false, unit:'%'}); return true; }
  if (v.kind === 'heat') { const o = base(); delete o.toolbox; o.xAxis = {...o.xAxis, type:'value', scale:true, name:'°F', nameGap:4}; o.yAxis.name = 'bpm'; o.tooltip.trigger = 'item'; o.tooltip.formatter = p => `${p.value[0]}°F, ${nf(p.value[1])} bpm`; o.series = [{type:'scatter', data:D.heat.points.map(p => p.slice(0, 2)), symbolSize:8, itemStyle:{color:series(4), borderColor:css('--surface'), borderWidth:1}}]; mount(el, o); return true; }
  if (v.kind === 'sleeploss') { const L = D.sleep_loss, it = [['Fall asleep', L.bed * 60], ['Wake up', L.wake * 60]]; lineChart(el, {series:[{name:'Shift', t:it.map(x => x[0]), v:it.map(x => Math.round(x[1])), color:series(7)}], bars:true, zoom:false, unit:'min later'}); return true; }
  if (v.kind === 'carry') { const r = D.carry.rhr; lineChart(el, {series:[{name:'Next-day resting HR vs baseline', t:['7+ hours', 'One short night', 'Two short nights'], v:[r.ok.mean, r.one.mean, r.two.mean], color:series(8)}], bars:true, zoom:false, unit:'bpm'}); return true; }
  if (v.kind === 'nights') { lineChart(el, {series:[{name:'Sleep', t:'Mon Tue Wed Thu Fri Sat Sun'.split(' '), v:D.nights, color:series(7)}], bars:true, zoom:false, unit:'h'}); return true; }
  return false;
}
function scatter(el, r) {
  const o = base(), xm = B.metrics[r.x], ym = B.metrics[r.y], xs = r.points.map(p => p[0]); delete o.xAxis.data;
  o.xAxis = {...o.xAxis, type:'value', scale:true, name:xm.label, nameLocation:'middle', nameGap:22}; o.yAxis.name = ym.label; o.yAxis.nameTextStyle = {color:css('--muted'), align:'left'}; o.grid.bottom = 40;
  o.tooltip.trigger = 'item'; o.tooltip.formatter = p => p.seriesIndex ? '' : `<b>${p.value[2]}</b><br>${xm.label}: ${nf(p.value[0])}<br>${ym.label}: ${nf(p.value[1])}`;
  const a = Math.min(...xs), b = Math.max(...xs);
  o.series = [{type:'scatter', data:r.points, symbolSize:5, itemStyle:{color:domainColor(r.y), opacity:.5}}];
  if (r.slope) o.series.push({type:'line', data:[[a, r.slope[0] * a + r.slope[1]], [b, r.slope[0] * b + r.slope[1]]], showSymbol:false, lineStyle:{width:2, color:css('--text2')}, silent:true});
  const c = mount(el, o); c.on('click', p => p.seriesIndex === 0 && go('day/' + p.value[2]));
}
function insightCard(g, x, D, cls = 'c6') {
  const t = tile(g, cls + ' insight', `<div class="eyebrow">${esc(x.category)}</div><h2>${esc(x.headline)}</h2><div class="muted" style="font-size:14px;color:var(--text2)">${esc(x.finding)}</div><div class="chart short"></div>
   <div class="row">${gradeBadge(x.grade)}<span class="badge">${esc(x.effect)}</span><span class="badge">n = ${nf(x.n, 0)}</span></div>
   <details><summary>Evidence and limits</summary><dl class="kv"><dt>Compared</dt><dd>${esc(x.compared)}</dd><dt>Uncertainty</dt><dd>${esc(x.uncertainty)}</dd><dt>Confounders</dt><dd>${esc(x.confounders.join(', ') || 'None identified')}</dd><dt>Limits</dt><dd>${esc(x.limitations.join(' '))}</dd><dt>Ranking</dt><dd>Score ${x.score}: effect ${x.score_parts.effect}, data ${x.score_parts.quality}, stability ${x.score_parts.stability}, novelty ${x.score_parts.novelty}</dd><dt>Next</dt><dd>${esc(x.next_q)}</dd></dl>
   <div class="row" style="margin-top:8px">${x.raw?.day ? `<button data-go="day/${x.raw.day}" type="button">Open raw day</button>` : ''}${x.raw?.x ? `<button data-go="explorer/${x.raw.x}/${x.raw.y}/${x.raw.lag}" type="button">Open in Explorer</button>` : ''}<button data-dismiss type="button">Dismiss</button></div></details>`);
  t.addEventListener('click', e => { const b = e.target.closest('[data-go]'); if (b) go(b.dataset.go); if (e.target.matches('[data-dismiss]')) { STATE.dismissed.push(x.id); saveState('dismissed'); t.remove(); } });
  const el = $('.chart', t);
  if (!(x.viz.kind !== 'note' && renderViz(el, x.viz, D))) { el.remove(); if (x.viz.text) t.querySelector('.row').insertAdjacentHTML('beforebegin', `<div class="muted">${esc(x.viz.text)}</div>`); }
}
VIEWS.discover = async host => {
  const ins = await (INS = null, insights()), D = ins.deep, cats = [...new Set(ins.cards.map(c => c.category))];
  head(host, 'Discoveries', `${ins.cards.length} findings, ranked by a transparent score. ${ins.n_tested + D.n_tests} tests were pre-listed and all are counted when correcting for false discoveries, including those that found nothing.`);
  const bar = document.createElement('div'); bar.className = 'row'; bar.style.marginBottom = '16px';
  bar.innerHTML = `<span class="seg" id="cat">${['Highlights', 'All', ...cats].map(c => `<button type="button" data-v="${c}">${c}</button>`).join('')}</span><button id="recalc" type="button">Recalculate</button>`;
  bar.querySelector('.seg').style.flexWrap = 'wrap'; host.append(bar);
  const g = bento(host);
  const draw = cat => { CH.forEach(c => c.dispose()); CH = []; g.innerHTML = ''; bar.querySelectorAll('[data-v]').forEach(b => b.classList.toggle('on', b.dataset.v === cat));
    if (cat === 'Highlights') { const t = tile(g, 'c12', `<div class="eyebrow">Life eras</div><div class="story">The chapters of your routine</div>`); if (D.eras?.eras) { eraRibbon(t, D.eras.eras); eraList(t, D.eras.eras); }
      if (D.jet?.curves) { const j = tile(g, 'c6', `<div class="eyebrow">Travel</div><h2>Sleep timing after crossing zones</h2><div class="muted">Sleep onset against your prior 4 weeks. Onset stays within 45 minutes of normal from ${D.jet.settle.east ? 'day ' + D.jet.settle.east : 'beyond day 8'} going east and ${D.jet.settle.west ? 'day ' + D.jet.settle.west : 'beyond day 8'} going west. Means of few trips, so the curve is noisy.</div><div class="chart"></div>`); eventChart($('.chart', j), D.jet.curves.bed, 'h'); }
      if (D.places?.rows) { const p = tile(g, 'c6', `<div class="eyebrow">Places</div><h2>You, by time zone</h2><div class="muted">Stays of 7+ days since Watch data began. Each place is also a period of your life.</div>`);
      p.append(table([{k:'place', t:'Zone'}, {k:'days', t:'Days'}, {k:'steps', t:'Steps', f:v => nf(v)}, {k:'asleep_watch_h', t:'Sleep', f:v => nf(v)}, {k:'wake_clock', t:'Wake', f:clock}, {k:'rhr', t:'RHR', f:v => nf(v)}, {k:'hrv_sdnn', t:'HRV', f:v => nf(v)}], D.places.rows)); }
      ins.cards.filter(c => c.score_parts.novelty >= .9 && c.grade !== 'C' && !['Data coverage', 'Anomalies', 'Life eras', 'Places'].includes(c.category)).slice(0, 8).forEach(c => insightCard(g, c, D));
      const nulls = ins.cards.filter(c => c.grade === 'C' && c.score_parts.novelty >= .8).slice(0, 8); tile(g, 'c12', `<div class="eyebrow">Things that turned out not to matter</div><div class="story">Tested, and not established</div><dl class="brief">${nulls.map(c => `<dt>${esc(c.headline)}</dt><dd>${esc(c.finding)} ${esc(c.uncertainty)}</dd>`).join('')}</dl>${D.skipped?.length ? `<p class="muted">Skipped for lack of data: ${esc(D.skipped.map(x => x.name).join(', '))}.</p>` : ''}`); return; }
    ins.cards.filter(c => cat === 'All' || c.category === cat).forEach(c => insightCard(g, c, D)); };
  bar.querySelector('.seg').onclick = e => e.target.dataset.v && draw(e.target.dataset.v);
  bar.querySelector('#recalc').onclick = async e => { e.target.textContent = 'Recalculating'; await post('/api/insights/recompute', {}); route(); };
  host.ready = () => draw('Highlights'); setTimeout(() => draw('Highlights'));
};
VIEWS.activity = async host => {
  const idx = dayIdx(), s = idx.map(i => B.data.steps[i]).filter(x => x != null); head(host, 'Activity', `${nf(s.length, 0)} days with step data in this range. Deduplicated by source so Watch and iPhone are not double-counted.`);
  const a = idx.map(i => B.data.steps[i]), sorted = [...s].sort((x, y) => x - y);
  let best = 0, cur = 0; a.forEach(v => { cur = v != null && v >= 10000 ? cur + 1 : 0; best = Math.max(best, cur); });
  host.append(tiles([['Median day', nf(median(s)), 'steps'], ['Top 10% of days', nf(quant(s, .9)), 'steps or more'], ['Total', nf(s.reduce((x, y) => x + y, 0) / 1e6, 2) + 'M', 'in range'], ['Spread', nf(100 * Math.sqrt(mean(s.map(x => (x - mean(s)) ** 2))) / mean(s), 0) + '%', 'coefficient of variation'], ['Longest 10k streak', best + ' days', 'consecutive'], ['Under 5,000', nf(100 * s.filter(x => x < 5000).length / s.length, 0) + '%', 'of days']]));
  const g = bento(host);
  const t = tile(g, 'c12', `<div class="eyebrow">Calendar</div><h2>Every recorded day</h2><div class="chart" style="height:280px"></div>`); yearHeat($('.chart', t), 'steps', series(2));
  metricChart(g, 'steps', {cls:'c8', bars:G.freq !== 'D'}); weekdayTile(g, 'steps');
  const hist = chartCard(g, 'Distribution of daily steps', 'Bins of 1,000 steps', 'c6'), bins = Array(32).fill(0); s.forEach(x => bins[Math.min(31, Math.floor(x / 1000))]++);
  lineChart(hist, {series:[{name:'Days', t:bins.map((_, i) => i + 'k'), v:bins, color:series(2)}], bars:true, zoom:false, unit:'days'});
  const cum = chartCard(g, 'Cumulative steps', 'Running total of recorded steps', 'c6'), cumv = []; let tot = 0; idx.forEach(i => { tot += B.data.steps[i] || 0; cumv.push(tot); });
  lineChart(cum, {series:[{name:'Cumulative', t:idx.map(i => B.dates[i]), v:cumv, color:series(2)}], unit:'steps', yfmt:v => nf(v / 1e6, 0) + 'M'});
  ['distance_mi', 'flights', 'active_kcal_worn', 'basal_kcal', 'ring_exercise_min', 'ring_stand_h', 'walk_speed_mph', 'step_length_in'].forEach(m => metricChart(g, m, {cls:'c6', size:'short'}));
};
function weekdayTile(g, m) { const el = chartCard(g, 'By day of week', 'Mean, current filters', 'c4'), idx = dayIdx(), n = 'Mon Tue Wed Thu Fri Sat Sun'.split(' '); hbars(el, n, n.map((_, i) => Math.round(mean(idx.filter(j => B.data.dow[j] === i + 1 && B.data[m][j] != null).map(j => B.data[m][j])) || 0)), domainColor(m)); }
VIEWS.heart = async host => {
  head(host, 'Heart', 'Apple Watch estimates and measurements. HRV here is SDNN and cannot be compared with RMSSD from other devices. Nothing here is a clinical assessment.');
  const last = m => mean(lastN(m, 30));
  host.append(tiles([['Resting HR, 30 days', nf(last('rhr')) + ' bpm', 'estimate'], ['Walking HR', nf(last('walking_hr')) + ' bpm', 'estimate'], ['Sleeping HR', nf(last('sleep_hr')) + ' bpm', 'derived'], ['HRV SDNN', nf(last('hrv_sdnn')) + ' ms', 'observed'], ['VO2 max', nf(lastN('vo2max', 365).at(-1)), 'estimate'], ['Highest HR', nf(Math.max(...B.data.hr_max.filter(x => x != null)), 0) + ' bpm', 'observed']]));
  const g = bento(host); ['rhr', 'hrv_sdnn', 'vo2max', 'sleep_hr', 'walking_hr', 'resp_rate'].forEach(m => metricChart(g, m));
};
VIEWS.sleep = async host => {
  const S = await api('/api/sleep'), n = S.nights.filter(x => x.date >= G.start && x.date <= G.end);
  head(host, 'Sleep', 'One source is chosen for each night and named, because sources disagree by over an hour. Trends use Watch-scored nights only. Nights belong to the date you woke up.');
  const w = n.filter(x => x.source.startsWith('Watch'));
  host.append(tiles([['Typical night', nf(median(w.map(x => x.asleep_h).filter(Boolean))) + ' h', 'Watch-scored'], ['Under 6 hours', nf(100 * w.filter(x => x.asleep_h < 6).length / (w.length || 1), 0) + '%', 'of nights'], ['Nights', nf(n.length, 0), 'all sources'], ['Efficiency', nf(100 * mean(w.map(x => x.efficiency).filter(Boolean)), 0) + '%', 'asleep / span'], ['Naps logged', nf(S.naps, 0), 'whole record']]));
  const g = bento(host); const el = chartCard(g, 'Your nights on a clock', 'The last 365 nights in range. Each bar runs from falling asleep to waking, in local time rebuilt across time zones.', 'c12', 'tall');
  const clockOf = t => { const h = +t.slice(11, 13) + t.slice(14, 16) / 60; return h < 14 ? h + 24 : h; };
  const rows = n.slice(-365).map((x, i) => { const a = clockOf(x.onset), raw = +x.wake.slice(11, 13) + x.wake.slice(14, 16) / 60; return {value:[i, a, raw + 24, x.date]}; });
  const smooth = k => rows.map((_, i) => mean(rows.slice(Math.max(0, i - 27), i + 1).map(r => r.value[k])));
  const o = base(); o.xAxis.data = rows.map(r => r.value[3]); o.yAxis = {...o.yAxis, scale:false, min:21, max:36, inverse:true, interval:3, axisLabel:{...o.yAxis.axisLabel, formatter:clock}}; o.grid.left = 62;
  o.legend = {top:0, left:0, data:[{name:'Typical onset and wake, 28 nights', itemStyle:{color:css('--text')}}], textStyle:{color:css('--text2'), fontSize:12}, itemHeight:3, itemWidth:12, icon:'roundRect'};
  o.tooltip.trigger = 'item'; o.tooltip.formatter = p => p.seriesIndex ? '' : `<b>${p.value[3]}</b><br>${clock(p.value[1])} to ${clock(p.value[2])}`;
  o.series = [{type:'custom', data:rows, clip:true, encode:{x:0, y:[1, 2]}, renderItem:(p, a) => { const s = a.coord([a.value(0), a.value(1)]), e = a.coord([a.value(0), a.value(2)]), w = Math.max(1.5, a.size([1, 0])[0] * .68); return {type:'rect', shape:{x:s[0] - w / 2, y:s[1], width:w, height:e[1] - s[1], r:w / 2}, style:{fill:series(7), opacity:.55}}; }},
    {type:'line', name:'Typical onset and wake, 28 nights', data:smooth(1), showSymbol:false, lineStyle:{width:2, color:css('--text')}, silent:true}, {type:'line', name:'Typical onset and wake, 28 nights', data:smooth(2), showSymbol:false, lineStyle:{width:2, color:css('--text')}, silent:true}];
  o.dataZoom = [{type:'inside'}]; mount(el, o, k => go('day/' + k));
  metricChart(g, 'asleep_watch_h');
  const stageEl = chartCard(g, 'Sleep stages', 'Monthly mean. Device-estimated.', 'c6'), f = 'M';
  const st = ['core_h', 'rem_h', 'deep_h', 'awake_h'].map((m, i) => { const a = aggregate(m, f), k = Math.max(0, a.v.findIndex(v => v != null)); return {name:B.metrics[m].label, t:a.t.slice(k), v:a.v.slice(k), color:series([1, 7, 3, 2][i])}; });
  lineChart(stageEl, {series:st, bars:true, stack:true, unit:'h', zoom:false});
  metricChart(g, 'bed_clock_adj'); metricChart(g, 'wake_clock');
  const reg = chartCard(g, 'Sleep regularity', 'Rolling 28-day SD of onset time. Lower is steadier.', 'c6', 'short'); lineChart(reg, {series:[{name:'Onset SD', t:S.regularity.t, v:S.regularity.bed_sd, color:series(7)}], unit:'h'});
  const debt = chartCard(g, 'Sleep debt proxy', 'Estimate: hours below your own rolling yearly median, summed over 14 nights.', 'c6', 'short'); lineChart(debt, {series:[{name:'14-day shortfall', t:S.regularity.t, v:S.regularity.debt, color:series(7)}], unit:'h'});
  const sj = tile(g, 'c6', `<h2>Social jet lag</h2><div class="muted">Median weekend sleep midpoint minus weekday midpoint. A proxy.</div><div class="chart short"></div>`); lineChart($('.chart', sj), {series:[{name:'Weekend shift', t:S.social_jetlag.map(x => x.yr), v:S.social_jetlag.map(x => x.lag_h * 60), color:series(7)}], bars:true, zoom:false, unit:'min'});
  const lbl = {rhr:'Resting HR', hrv:'HRV', steps:'Steps', next:'Next night'}, cols = [['normal', 'After 7+ h'], ['one', 'After one short night'], ['two', 'After two short nights']];
  const rec = tile(g, 'c6', `<h2>After short nights</h2><div class="muted">Short is under 6 hours. Unadjusted means.</div>`); rec.append(table([{k:'m', t:'Metric'}, ...cols.map(([k, l]) => ({k, t:l, f:v => nf(v?.value) + ` (n ${v?.n})`}))], ['rhr', 'hrv', 'steps', 'next'].map((k, i) => ({m:lbl[k], normal:S.short.normal[i], one:S.short.one[i], two:S.short.two[i]}))));
};
VIEWS.body = async host => {
  head(host, 'Body and other data', 'Sections appear only when your export contains them. Body composition cannot be inferred from weight alone.');
  const g = bento(host); ['weight_lb', 'walk_speed_mph', 'step_length_in', 'double_support_pct', 'asymmetry_pct', 'steadiness_pct', 'stair_up_fps', 'stair_down_fps', 'water_ml', 'headphone_db', 'effort'].forEach(m => metricChart(g, m, {size:'short', cls:'c4'}));
  const q = await api('/api/quality'); tile(g, 'c12', `<div class="eyebrow">Not in this export</div><h2>Unavailable, so not shown</h2><div class="chips" style="margin-top:8px">${q.absent.map(a => `<span class="badge">${a}</span>`).join('')}</div><div class="muted" style="margin-top:8px">These sections will appear automatically if a future export includes the data.</div>`);
};
VIEWS.running = async host => {
  const R = await api('/api/running'), s = R.summary, ins = await insights(), D = ins.deep;
  head(host, 'Running', 'Device-recorded runs. Training-load figures describe your volume; they are not injury predictors.');
  host.append(tiles([['Runs', nf(s.n, 0), 'recorded'], ['Distance', nf(s.miles, 0) + ' mi', ''], ['Time', nf(s.hours, 0) + ' h', ''], ['Median run', nf(s.med_dist) + ' mi', ''], ['Median pace', pace(s.med_pace), 'min/mi, elapsed'], ['Longest', nf(s.longest) + ' mi', ''], ['Median rest', nf(s.med_rest, 0) + ' days', 'between runs'], ['Longest streak', pl(s.streak, 'day'), 'consecutive']]));
  const g = bento(host);
  const w = chartCard(g, 'Weekly distance', '', 'c12'); lineChart(w, {series:[{name:'Miles', t:R.weekly.t, v:R.weekly.v, color:series(3)}], bars:true, unit:'mi', pick:k => go('day/' + k)});
  const pc = chartCard(g, 'Pace against heart rate over time', 'Each dot is a run with reliable heart rate. Later runs are darker.', 'c6'), P = R.runs.filter(r => r.avg_hr && r.pace_min_mi > 5 && r.pace_min_mi < 15);
  const o = base(); o.xAxis = {...o.xAxis, type:'value', scale:true, name:'Avg HR', nameLocation:'middle', nameGap:22}; o.yAxis.inverse = true; o.yAxis.axisLabel.formatter = pace; o.grid.bottom = 40; o.tooltip.trigger = 'item'; o.tooltip.formatter = p => `<b>${p.value[3]}</b><br>${pace(p.value[1])}/mi at ${nf(p.value[0])} bpm`;
  const tmin = Date.parse(P[0]?.date), tmax = Date.parse(P.at(-1)?.date); o.visualMap = {show:false, dimension:2, min:0, max:1, inRange:{color:[css('--seq0'), series(3)]}}; o.series = [{type:'scatter', data:P.map(r => [r.avg_hr, r.pace_min_mi, (Date.parse(r.date) - tmin) / (tmax - tmin), r.date]), symbolSize:8}]; mount(pc, o);
  const lv = chartCard(g, 'Rolling training volume', '7-day total against 28-day weekly average. A descriptive load comparison.', 'c6'); lineChart(lv, {series:[{name:'7-day miles', t:R.load.t, v:R.load.acute, color:series(3)}, {name:'28-day weekly avg', t:R.load.t, v:R.load.chronic, color:series(1)}], unit:'mi'});
  const zs = chartCard(g, 'Heart-rate zones by year', `Share of samples. Reference max ${nf(R.hr_ref, 0)} bpm (observed). Older runs hold few samples.`, 'c6'), yrs = [...new Set(R.zones.map(z => z.y))].sort();
  lineChart(zs, {series:['Z1', 'Z2', 'Z3', 'Z4', 'Z5'].map((z, i) => ({name:z, t:yrs, v:yrs.map(y => { const all = R.zones.filter(q => q.y === y), n = all.reduce((a, b) => a + b.n, 0); return 100 * (all.find(q => q.z === z)?.n || 0) / n; }), color:series([3, 1, 4, 2, 8][i])})), bars:true, stack:true, zoom:false, unit:'%'});
  const mo = chartCard(g, 'Training monotony', 'Mean daily miles over its SD, 7-day window. Higher means less varied.', 'c6'); lineChart(mo, {series:[{name:'Monotony', t:R.load.t, v:R.load.monotony, color:series(3)}], unit:''});
  ins.cards.filter(c => c.category === 'Running').forEach(c => insightCard(g, c, D));
  const list = tile(g, 'c12', `<h2>All runs</h2>`); list.append(workoutTable([...R.runs].reverse().map(r => ({...r, type:'Running'}))));
};
const WCOLS = [{k:'date', t:'Date'}, {k:'type', t:'Type'}, {k:'distance_mi', t:'Miles', f:v => v == null ? '' : nf(v)}, {k:'duration_min', t:'Minutes', f:v => nf(v, 0)}, {k:'pace_min_mi', t:'Pace', f:v => v ? pace(v) : ''}, {k:'avg_hr', t:'Avg HR', f:v => v ? nf(v, 0) : ''}, {k:'max_hr', t:'Max HR', f:v => v ? nf(v, 0) : ''}, {k:'elev_ft', t:'Elev ft', f:v => v ? nf(v, 0) : ''}, {k:'source', t:'Source'}];
const workoutTable = r => table(WCOLS, r, {onRow:x => go('workout/' + x.workout_id)});
VIEWS.workouts = async host => {
  const W = await api('/api/workouts'); head(host, 'Workouts', `${W.length} workouts. Search, sort any column, and open one to see its route and heart rate.`);
  const c = card(`<div class="row" style="margin-bottom:10px"><input id="q" placeholder="Search type, source or date" style="min-width:260px" aria-label="Search workouts"><span class="muted" id="cnt"></span></div><div id="wt"></div>`); host.append(c);
  const f = s => { const r = W.filter(w => `${w.type} ${w.source} ${w.date} ${w.brand || ''}`.toLowerCase().includes(s.toLowerCase())); $('#cnt', c).textContent = pl(r.length, 'workout'); $('#wt', c).replaceChildren(workoutTable(r)); };
  $('#q', c).oninput = e => f(e.target.value); f('');
};
function routeSvg(pts) {
  if (pts.length < 2) return '<p class="empty">No route recorded for this workout.</p>';
  const lat = pts.map(p => p.lat), lon = pts.map(p => p.lon), k = Math.cos(mean(lat) * Math.PI / 180), minX = Math.min(...lon), maxX = Math.max(...lon), minY = Math.min(...lat), maxY = Math.max(...lat), W = 800, H = 360, s = Math.min(W * .9 / (((maxX - minX) * k) || 1e-6), H * .9 / ((maxY - minY) || 1e-6));
  const xy = p => [((p.lon - minX) * k * s + (W - (maxX - minX) * k * s) / 2).toFixed(1), (H - ((p.lat - minY) * s + (H - (maxY - minY) * s) / 2)).toFixed(1)], line = pts.map(xy), a = line[0], b = line.at(-1);
  return `<svg class="route" viewBox="0 0 ${W} ${H}" role="img" aria-label="Shape of the route"><polyline points="${line.map(p => p.join(',')).join(' ')}" fill="none" stroke="${series(3)}" stroke-width="3" stroke-linejoin="round"/><circle cx="${a[0]}" cy="${a[1]}" r="6" fill="${series(1)}"/><circle cx="${b[0]}" cy="${b[1]}" r="6" fill="${series(2)}"/></svg><div class="legend"><span><i style="background:${series(1)}"></i>Start</span><span><i style="background:${series(2)}"></i>End</span><span class="muted">Drawn from your GPS file. No map service is contacted.</span></div>`;
}
VIEWS.workout = async (host, [id]) => {
  const r = await api('/api/workout/' + id), w = r.workout; head(host, `${w.type}, ${niceDate(w.s.slice(0, 10))}`, `${w.s.slice(11, 16)} local, ${esc(w.tz)}. Source: ${esc(w.source_name)}.`);
  host.append(tiles([['Duration', nf(w.duration_min, 0) + ' min'], ['Distance', w.distance_mi ? nf(w.distance_mi) + ' mi' : 'n/a'], ['Pace', w.pace_min_mi ? pace(w.pace_min_mi) : 'n/a'], ['Avg HR', w.avg_hr ? nf(w.avg_hr, 0) : 'n/a', w.hr_basis || 'too few samples'], ['Max HR', w.max_hr ? nf(w.max_hr, 0) : 'n/a'], ['Energy', w.kcal ? nf(w.kcal, 0) + ' kcal' : 'n/a'], ['Elevation', w.elev_ft ? nf(w.elev_ft, 0) + ' ft' : 'n/a'], ['Temperature', w.temp_f ? nf(w.temp_f, 0) + '°F' : 'n/a']].filter(x => x[1] !== 'n/a')));
  const g = bento(host); if (r.hr.length > 5) { const el = chartCard(g, 'Heart rate', `${r.hr.length} samples`, 'c7'); lineChart(el, {series:[{name:'Heart rate', t:r.hr.map(x => x.t.slice(11, 19)), v:r.hr.map(x => x.v), color:series(8)}], unit:'bpm'}); } else tile(g, 'c7', `<h2>Heart rate</h2><p class="empty">The export kept only ${r.hr.length} heart-rate samples for this workout, so no curve can be drawn.</p>`);
  tile(g, 'c5', `<h2>Route</h2>${routeSvg(r.route)}`);
};
VIEWS.rhythms = async host => {
  const R = await api(`/api/rhythm?start=${B.dates[0]}&end=${B.dates.at(-1)}`), ar = await api('/api/archetypes'); head(host, 'Rhythms', 'When you move, across the hours of the day and the years. Local time is rebuilt for travel.');
  const g = bento(host), days = 'Mon Tue Wed Thu Fri Sat Sun'.split(' ');
  const heat = (el, rows, ys, ylab) => { const o = base(); delete o.toolbox; o.grid = {left:46, right:8, top:6, bottom:24}; o.xAxis = {...o.xAxis, data:[...Array(24).keys()].map(h => clock(h).replace(':00 ', '')), splitArea:{show:false}}; o.yAxis = {type:'category', data:ys, inverse:true, axisLine:{show:false}, axisTick:{show:false}, axisLabel:{color:css('--muted'), fontSize:11}}; o.visualMap = {show:false, min:0, max:quant(rows.map(r => r[2]), .98), inRange:{color:[css('--seq0'), series(2)]}}; o.tooltip = {...o.tooltip, trigger:'item', formatter:p => `${ys[p.value[1]]}, ${clock(p.value[0])}: ${nf(p.value[2])} steps`}; o.series = [{type:'heatmap', data:rows, itemStyle:{borderRadius:3, borderColor:css('--surface'), borderWidth:2}}]; mount(el, o); };
  heat(chartCard(g, 'Weekly rhythm', 'Average steps per hour by day of week', 'c8', ''), R.hour_dow.map(r => [r.h, r.dow - 1, r.v]), days);
  const hr = Array(24).fill(0); R.hour_dow.forEach(r => hr[r.h] += r.v / 7); clock24(chartCard(g, '24-hour clock', 'Lifetime average', 'c4'), hr.map(Math.round), series(2));
  const yrs = [...new Set(R.year_hour.map(r => r.y))].sort(); const odd = yrs.filter(y => { const v = Array(24).fill(0); R.year_hour.filter(r => r.y === y).forEach(r => v[r.h] = r.v); const q = v.map((_, h) => [0, 1, 2, 3, 4, 5].reduce((a, k) => a + v[(h + k) % 24], 0)), st = q.indexOf(Math.min(...q)); return st >= 7 && st <= 19; });
  heat(chartCard(g, 'How your day changed by year', `Each row is a year: darker hours held more steps.${odd.length ? ` Time zone tags are missing in early years, so your earliest known zone is assumed. In ${odd.join(' and ')} your quietest hours fall in daytime on that clock, which suggests you were in a distant time zone and those rows are shifted.` : ''}`, 'c12', 'tall'), R.year_hour.map(r => [r.h, yrs.indexOf(r.y), r.v]), yrs.map(String));
  if (!ar.error) { const a = tile(g, 'c12', `<h2>Day archetypes</h2><div class="muted">${ar.k} clusters of worn days. Silhouette ${nf(ar.silhouette)}, stability ${nf(ar.stability)}. ${esc(ar.caution)}</div><div class="chart short"></div>`); lineChart($('.chart', a), {series:Object.keys(ar.by_year.series).slice(0, 8).map((k, i) => ({name:ar.names[k] + ' ' + (+k + 1), t:ar.by_year.years, v:ar.by_year.series[k], color:series(i + 1)})), bars:true, stack:true, zoom:false, unit:'days'}); }
};
VIEWS.explorer = async (host, [x = 'steps', y = 'rhr', lag = '1']) => {
  head(host, 'Explorer', 'Pick any two metrics. Results show raw and adjusted relationships, because shared trends often fake a correlation.');
  const form = card(`<div class="row"><label>If ${metricSelect(x, 'ex')}</label><label>then ${metricSelect(y, 'ey')}</label><label>days later <input id="el" type="number" min="-14" max="14" value="${lag}" style="width:60px"></label><label>adjust for ${metricSelect('', 'ec', true)}</label><button class="primary" id="run" type="button">Analyse</button></div>`); host.append(form);
  const out = document.createElement('div'); out.style.marginTop = '16px'; host.append(out);
  const run = async () => { CH.forEach(c => c.dispose()); CH = []; out.innerHTML = '';
    const r = await post('/api/explore', {x:$('#ex').value, y:$('#ey').value, lag:+$('#el').value, covariates:$('#ec').value ? [$('#ec').value] : []});
    out.insertAdjacentHTML('beforeend', `<div class="callout">${esc(r.plain)}</div>`); out.append(tiles([['Pearson r', nf(r.raw.r), 'raw'], ['Spearman', nf(r.raw.rho), 'raw'], ['Adjusted r', nf(r.adjusted.r), 'detrended, deseasonalised'], ['Effective n', nf(r.adjusted.neff, 0), `${r.disclosure.paired} paired, ${r.disclosure.missing} missing`], ['Rotation p', nf(r.adjusted.perm_p, 3), 'time-respecting'], ['Slope 95% CI', `${nf(r.slope_ci[0])} to ${nf(r.slope_ci[1])}`, 'block bootstrap']]));
    const g = bento(out); scatter(chartCard(g, 'Scatter', 'Sampled to 1,500 points. Click a dot to open that day.', 'c6'), r);
    const l = chartCard(g, 'Lag curve', 'Adjusted r by lead. Positive means the first metric leads.', 'c6'); lineChart(l, {series:[{name:'r', t:r.lag_curve.map(c => c.lag), v:r.lag_curve.map(c => c.r), color:series(1)}], zoom:false, unit:''});
    const ro = chartCard(g, 'Rolling correlation', '180-day window. A stable relationship stays on one side of zero.', 'c6'); lineChart(ro, {series:[{name:'r', t:r.rolling.t, v:r.rolling.r, color:series(1)}], unit:''});
    if (r.quintiles.length) { const qq = chartCard(g, 'Threshold view', 'Mean outcome by quintile of the first metric, which reveals non-linear shapes.', 'c6'); lineChart(qq, {series:[{name:B.metrics[r.y].label, t:r.quintiles.map(q => nf(q.x)), v:r.quintiles.map(q => q.y), n:r.quintiles.map(q => q.n), color:domainColor(r.y)}], zoom:false, unit:B.metrics[r.y].unit}); }
    const mx = await api('/api/matrix'), mh = chartCard(g, 'All key metrics', 'Adjusted correlations. Blue is positive, orange negative, gray near zero.', 'c12', 'tall'), o = base(); delete o.toolbox; o.grid = {left:170, right:10, top:6, bottom:90};
    const cells = []; mx.r.forEach((row, i) => row.forEach((v, j) => v != null && cells.push([j, i, v]))); o.xAxis = {...o.xAxis, data:mx.labels, axisLabel:{...o.xAxis.axisLabel, rotate:38, interval:0}}; o.yAxis = {type:'category', data:mx.labels, inverse:true, axisLine:{show:false}, axisTick:{show:false}, axisLabel:{color:css('--text2'), fontSize:11.5}};
    o.visualMap = {show:false, min:-.5, max:.5, inRange:{color:[series(2), css('--fill'), series(1)]}}; o.tooltip = {...o.tooltip, trigger:'item', formatter:p => `${mx.labels[p.value[1]]} and ${mx.labels[p.value[0]]}: r = ${p.value[2]}`}; o.series = [{type:'heatmap', data:cells, itemStyle:{borderRadius:3, borderColor:css('--surface'), borderWidth:2}}]; mount(mh, o); };
  $('#run', form).onclick = run; setTimeout(run);
};
function resultView(out, r, onSave) {
  CH.forEach(c => c.dispose()); CH = []; out.innerHTML = r.error ? `<div class="callout">${esc(r.error)}</div>` : '';
  if (r.error) return;
  const rr = r.result, a = rr.adjusted; out.insertAdjacentHTML('beforeend', `<div class="callout"><b>${esc(r.plain)}</b><br>Query: ${esc(r.query)}</div>`);
  r.warnings.forEach(w => out.insertAdjacentHTML('beforeend', `<div class="callout">${esc(w)}</div>`));
  out.append(tiles([['Exposed days', nf(rr.n_a, 0), 'mean ' + nf(rr.mean_a)], ['Comparison days', nf(rr.n_b, 0), 'mean ' + nf(rr.mean_b)], ['Raw difference', nf(rr.diff), ''], ['Adjusted', a ? nf(a.b) : 'n/a', a ? `95% CI ${nf(a.lo)} to ${nf(a.hi)}` : ''], ['Effect size g', nf(rr.g), 'Hedges']]));
  const g = bento(out), o = base(); delete o.toolbox; const dist = chartCard(g, 'Distributions', 'Minimum, quartiles and maximum of each group', 'c6');
  o.xAxis.data = ['Exposed', 'Comparison']; o.tooltip.trigger = 'item'; o.series = [{type:'boxplot', data:[r.dist.a, r.dist.b].map(x => x || []), itemStyle:{color:css('--fill'), borderColor:series(1), borderWidth:2}}]; mount(dist, o);
  const alt = tile(g, 'c6', `<h2>Alternative specifications</h2><div class="muted">If the estimate changes a lot here, it depends on the choices made.</div>`); alt.append(table([{k:'name', t:'Specification'}, {k:'n_a', t:'n exposed'}, {k:'n_b', t:'n comparison'}, {k:'est', t:'Adjusted', f:(v, x) => x.adjusted ? `${nf(x.adjusted.b)} (${nf(x.adjusted.lo)} to ${nf(x.adjusted.hi)})` : 'n/a'}], r.alternatives));
  tile(g, 'c12', `<h2>Why this could mislead</h2><ul class="muted">${r.confounding.map(c => `<li>${esc(c)}</li>`).join('')}</ul><button id="save" type="button">Save to notebook</button>`); $('#save', g).onclick = onSave;
}
VIEWS.lab = async host => {
  head(host, 'Lab', 'State a hypothesis in plain language or build one by hand. The translation is shown, so you can check it. Saved analyses rerun on future exports.');
  const f = card(`<textarea id="h" aria-label="Hypothesis">Compare my resting heart rate on days following runs longer than five miles against days following no running.</textarea><div class="row" style="margin-top:8px"><button class="primary" id="go" type="button">Run hypothesis</button></div>
   <details style="margin-top:10px"><summary>Build one by hand</summary><div class="row" style="margin-top:8px"><label>Outcome ${metricSelect('rhr', 'o')}</label><label>Lag <input id="lag" type="number" value="1" style="width:56px"></label><label>Exposure ${metricSelect('run_mi', 'em')}</label><select id="eo"><option>&gt;=</option><option>&gt;</option><option>&lt;</option><option>&lt;=</option><option>==</option></select><input id="ev" type="number" value="5" style="width:80px"><label>Comparison ${metricSelect('run_mi', 'cm')}</label><select id="co"><option>==</option><option>&lt;</option><option>&gt;=</option></select><input id="cv" type="number" value="0" style="width:80px"><label>Adjust ${metricSelect('', 'cov', true)}</label><button id="go2" type="button">Run</button></div></details>`);
  host.append(f); const out = document.createElement('div'); out.style.marginTop = '16px'; host.append(out); let last = null;
  const show_ = r => { last = r; resultView(out, r, async () => { STATE.experiments.unshift({spec:r.spec, query:r.query, plain:r.plain, saved:new Date().toISOString().slice(0, 10), note:''}); await saveState('experiments'); route(); }); };
  $('#go', f).onclick = async () => show_(await post('/api/experiment', {text:$('#h').value}));
  $('#go2', f).onclick = async () => show_(await post('/api/experiment', {outcome:$('#o').value, lag:+$('#lag').value, exposure:[{metric:$('#em').value, op:$('#eo').value, value:+$('#ev').value}], control:[{metric:$('#cm').value, op:$('#co').value, value:+$('#cv').value}], covariates:$('#cov').value ? [$('#cov').value] : []}));
  if (STATE.experiments.length) { const n = card('<h2>Notebook</h2><div class="muted">Saved analyses. Click one to rerun it on your current data.</div>'); n.style.marginTop = '16px'; n.append(table([{k:'saved', t:'Saved'}, {k:'query', t:'Query'}, {k:'plain', t:'Result when saved'}], STATE.experiments, {onRow:async x => show_(await post('/api/experiment', x.spec))})); host.append(n); }
  setTimeout(() => $('#go', f).click());
};
const COHORTS = {'15,000+ step days':[{metric:'steps', op:'>', value:15000}], 'Under 10,000 steps':[{metric:'steps', op:'<', value:10000}], 'Run days':[{metric:'runs', op:'>=', value:1}], 'Rest days':[{metric:'workouts', op:'==', value:0}], 'Short sleep':[{metric:'asleep_watch_h', op:'<', value:6}], '7+ hours sleep':[{metric:'asleep_watch_h', op:'>=', value:7}], 'Weekends':[{metric:'weekend', value:true}], 'Weekdays':[{metric:'weekend', value:false}], 'High resting HR':[{metric:'rhr', op:'>=', value:72}]};
VIEWS.cohorts = async host => {
  head(host, 'Cohorts', 'Compare two groups of days across several metrics. Cohorts picked after seeing the data invite selection bias, so treat results as leads.');
  const all = {...COHORTS, ...Object.fromEntries(STATE.cohorts.map(c => [c.name, c.conds]))}, opts = v => Object.keys(all).map(k => `<option ${k === v ? 'selected' : ''}>${esc(k)}</option>`).join('');
  const f = card(`<div class="row"><label>Group A <select id="a">${opts('15,000+ step days')}</select></label><label>Group B <select id="b">${opts('Under 10,000 steps')}</select></label><button class="primary" id="go" type="button">Compare</button><span style="flex:1"></span><label>New cohort ${metricSelect('steps', 'nm')}</label><select id="no"><option>&gt;=</option><option>&lt;</option></select><input id="nv" type="number" value="12000" style="width:90px"><input id="nn" placeholder="Name" style="width:130px" aria-label="Cohort name"><button id="sv" type="button">Save</button></div>`); host.append(f);
  const out = document.createElement('div'); out.style.marginTop = '16px'; host.append(out);
  $('#go', f).onclick = async () => { const r = await post('/api/cohort', {a:all[$('#a').value], b:all[$('#b').value]}); out.innerHTML = `<div class="callout">${esc(r.a)}: ${r.n_a} days. ${esc(r.b)}: ${r.n_b} days. ${esc(r.warning)}</div>`; const c = card('<h2>Comparison</h2>'); c.append(table([{k:'label', t:'Metric'}, {k:'mean_a', t:'Group A', f:v => nf(v)}, {k:'mean_b', t:'Group B', f:v => nf(v)}, {k:'g', t:'Effect g', f:v => nf(v)}, {k:'adj', t:'Adjusted difference', f:(_, x) => x.adjusted ? `${nf(x.adjusted.b)} (${nf(x.adjusted.lo)} to ${nf(x.adjusted.hi)})` : 'n/a'}, {k:'q', t:'q', f:v => nf(v, 3)}], r.rows)); out.append(c); };
  $('#sv', f).onclick = async () => { const name = $('#nn').value.trim(); if (!name) return; STATE.cohorts.push({name, conds:[{metric:$('#nm').value, op:$('#no').value, value:+$('#nv').value}]}); await saveState('cohorts'); route(); }; setTimeout(() => $('#go', f).click());
};
VIEWS.ask = async host => {
  head(host, 'Ask', 'Each question runs a real analysis on this computer. Nothing is sent to any model, and a question that cannot be matched says so rather than guessing.');
  const qs = ['What changed most in my health metrics this year?', 'When was my running fitness at its best?', 'What happens to my resting heart rate after a long run?', 'Which month had the most consistent sleep?', 'Did my workouts become more frequent over time?', 'Which periods were most unusual?', 'How did my activity change after 2024-01-09?', 'What relationships have I not explored yet?', 'Find the strongest robust associations in my data.', 'Show me my most distinctive physiological patterns.'];
  const f = card(`<div class="row"><input id="q" style="flex:1;min-width:280px;font-size:17px;padding:9px 14px" placeholder="Ask about your data" aria-label="Question"><button class="primary" id="go" type="button">Ask</button></div>`); host.append(f);
  const ch = document.createElement('div'); ch.className = 'chips'; ch.style.margin = '14px 0'; ch.innerHTML = qs.map(q => `<button type="button">${esc(q)}</button>`).join(''); host.append(ch);
  const out = document.createElement('div'); host.append(out);
  const ask = async q => { $('#q', f).value = q; CH.forEach(c => c.dispose()); CH = []; const r = await post('/api/ask', {q}), ins = await insights(); out.innerHTML = ''; const c = card(`<div class="answer">${esc(r.answer)}</div>`); out.append(c);
    if (r.chart) { const d = document.createElement('div'); d.className = 'chart'; d.style.marginTop = '12px'; c.append(d); if (!(r.chart.kind !== 'note' && renderViz(d, r.chart, ins.deep))) d.remove(); }
    if (r.evidence.length) { const keys = Object.keys(r.evidence[0]).filter(k => k !== 'key'); const w = document.createElement('div'); w.style.marginTop = '12px'; w.append(table(keys.map(k => ({k, t:k.replace(/_/g, ' '), f:v => typeof v === 'number' ? nf(v) : esc(v)})), r.evidence)); c.append(w); }
    c.insertAdjacentHTML('beforeend', `<details style="margin-top:10px"><summary>Assumptions and method</summary><p class="muted">Tool: ${esc(r.tool)}.</p><ul class="muted">${r.assumptions.map(a => `<li>${esc(a)}</li>`).join('')}</ul></details>`); };
  $('#go', f).onclick = () => ask($('#q', f).value); $('#q', f).onkeydown = e => e.key === 'Enter' && ask(e.target.value); ch.onclick = e => e.target.tagName === 'BUTTON' && ask(e.target.textContent);
};
VIEWS.timeline = async host => {
  const ins = await insights(), D = ins.deep; head(host, 'Timeline', 'Your whole record on one shared time axis. Dashed lines mark recording changes and your own events, so a shift can be read against what else happened.');
  const marks = aMonth => [...STATE.annotations.map(a => ({xAxis:a.date.slice(0, 7), name:a.label})), ...ins.transitions.filter(t => /watchOS|First|falls/.test(t[1])).map(t => ({xAxis:t[0].slice(0, 7), name:''}))].filter(m => aMonth.includes(m.xAxis));
  const add = card(`<div class="row"><b>Add a life event</b><input id="ad" type="date" aria-label="Event date"><input id="al" placeholder="Started a new job" style="min-width:240px" aria-label="Event label"><button class="primary" id="ab" type="button">Add</button><span style="flex:1"></span><span class="muted">Stored only on this computer. Nothing is invented for you.</span></div>`); host.append(add); add.style.marginBottom = '16px';
  $('#ab', add).onclick = async () => { if (!$('#ad').value || !$('#al').value) return; STATE.annotations.push({date:$('#ad').value, label:$('#al').value}); STATE.annotations.sort((a, b) => a.date < b.date ? -1 : 1); await saveState('annotations'); route(); };
  const g = bento(host); if (D.eras?.eras) { const t = tile(g, 'c12', '<div class="eyebrow">Eras</div>'); eraRibbon(t, D.eras.eras); }
  const stack = tile(g, 'c12 stack', ''), list = ['steps', 'run_mi', 'asleep_watch_h', 'rhr', 'hrv_sdnn', 'vo2max', 'weight_lb'];
  list.forEach((m, i) => stack.insertAdjacentHTML('beforeend', `<div class="eyebrow" style="margin:${i ? 10 : 0}px 0 0"><span class="dot" style="background:${domainColor(m)}"></span>${esc(B.metrics[m].label)}<span class="muted" style="font-weight:400">${esc(B.metrics[m].unit)}</span></div><div class="chart xs" id="tl${i}"></div>`));
  list.forEach((m, i) => { const a = aggregate(m, 'M', B.dates.map((_, i) => i)), last = i === list.length - 1, el = $('#tl' + i, stack); if (last) el.style.height = '116px'; const c = lineChart(el, {series:[{name:B.metrics[m].label, t:a.t, v:a.v, n:a.n, color:domainColor(m)}], unit:B.metrics[m].unit, marks:marks(a.t), pick:pick('M'), bars:m === 'run_mi', hideX:!last, zoom:false}); c.group = 'tl'; });
  echarts.connect('tl');
  if (STATE.annotations.length) { const c = tile(g, 'c12', `<h2>Your events</h2><div class="muted">Click an event for a 90-day before and after comparison.</div>`); c.append(table([{k:'date', t:'Date'}, {k:'label', t:'Event'}], STATE.annotations, {onRow:async a => { const w = new Date(Date.parse(a.date) + 90 * 864e5).toISOString().slice(0, 10), b = new Date(Date.parse(a.date) - 90 * 864e5).toISOString().slice(0, 10), rs = await Promise.all(['steps', 'rhr', 'hrv_sdnn', 'asleep_watch_h'].map(m => post('/api/experiment', {outcome:m, lag:0, exposure:[{metric:'date', op:'>=', value:a.date}, {metric:'date', op:'<', value:w}], control:[{metric:'date', op:'>=', value:b}, {metric:'date', op:'<', value:a.date}]}))); $('#ba', c).innerHTML = rs.map(r => `<p>${esc(r.plain)}</p>`).join(''); }})); c.insertAdjacentHTML('beforeend', '<div id="ba" class="muted"></div>'); }
  const sp = tile(g, 'c12', `<h2>High-strain spells</h2><div class="muted">${D.spells?.spells?.length ?? 0} data patterns, not diagnoses. You may recognise some as illness or heavy travel: add them above.</div>`); sp.append(table([{k:'start', t:'Start'}, {k:'days', t:'Flagged days'}, {k:'peak_rhr', t:'Peak RHR', f:v => nf(v, 0)}, {k:'low_hrv', t:'Lowest HRV', f:v => nf(v, 0)}, {k:'tz', t:'Zone'}], [...(D.spells?.spells || [])].reverse(), {onRow:x => go('day/' + x.start)}));
};
VIEWS.records = async host => {
  const r = await api('/api/records'); head(host, 'Records', r.note); const g = bento(host);
  r.records.forEach(x => tile(g, 'c4', `<div class="eyebrow">${esc(x.label)}</div><div class="big" style="font-size:32px">${esc(x.value)}</div><div class="delta">${esc(/^\d{4}-\d\d-\d\d$/.test(x.when) ? niceDate(x.when) : x.when)}</div><div class="muted" style="margin-top:6px">${esc(x.caveat)}</div>`, x.day ? () => go('day/' + x.day) : null));
};
VIEWS.day = async (host, [day]) => {
  const r = await api('/api/day/' + day); head(host, niceDate(day), esc(r.summary)); if (r.unusual.length) host.insertAdjacentHTML('beforeend', `<ul class="unusual">${r.unusual.map(u => `<li>${esc(u)}</li>`).join('')}</ul>`);
  const nav = shift => new Date(Date.parse(day) + shift * 864e5).toISOString().slice(0, 10);
  const row = document.createElement('div'); row.className = 'row'; row.style.marginBottom = '14px'; row.innerHTML = `<button type="button" data-go="${nav(-1)}">Previous day</button><button type="button" data-go="${nav(1)}">Next day</button>`; row.onclick = e => e.target.dataset.go && go('day/' + e.target.dataset.go); host.append(row);
  host.append(tiles(r.compare.slice(0, 8).map(c => [c.label, `${nf(c.value)} ${c.unit}`, c.baseline == null ? c.kind : `90-day median ${nf(c.baseline)}`])));
  const g = bento(host); if (r.hr.length) lineChart(chartCard(g, 'Heart rate', `${r.hr.length} samples across ${r.coverage.hr_hours} hours`, 'c8'), {series:[{name:'Heart rate', t:r.hr.map(x => x.t.slice(11, 16)), v:r.hr.map(x => x.v), color:series(8)}], unit:'bpm'}); else tile(g, 'c8', '<h2>Heart rate</h2><p class="empty">No heart-rate samples on this day.</p>');
  const st = r.hours.filter(x => x.metric === 'steps'); lineChart(chartCard(g, 'Steps by hour', '', 'c4'), {series:[{name:'Steps', t:[...Array(24).keys()].map(h => clock(h).replace(':00 ', '')), v:[...Array(24).keys()].map(h => st.find(x => x.h === h)?.v ?? null), color:series(2)}], bars:true, zoom:false, unit:'steps'});
  if (r.stages.length) { const order = ['Awake', 'AsleepREM', 'AsleepCore', 'AsleepDeep', 'AsleepUnspecified'], el = chartCard(g, 'Sleep stages', `${r.stages[0].source}. Overlaps removed.`, 'c8', 'short'), o = base(); delete o.toolbox; o.xAxis = {type:'time', axisLabel:{color:css('--muted'), fontSize:11}, axisLine:{show:false}}; o.yAxis = {type:'category', data:order.map(s => s.replace('Asleep', '')), axisLine:{show:false}, axisTick:{show:false}, axisLabel:{color:css('--muted'), fontSize:11}}; o.tooltip.trigger = 'item'; o.tooltip.formatter = p => p.value[3];
    o.series = [{type:'custom', data:r.stages.filter(s => order.includes(s.stage)).map(s => [order.indexOf(s.stage), Date.parse(s.s.replace(' ', 'T') + 'Z'), Date.parse(s.e.replace(' ', 'T') + 'Z'), s.stage.replace('Asleep', '')]), renderItem:(p, a) => { const s = a.coord([a.value(1), a.value(0)]), e = a.coord([a.value(2), a.value(0)]), h = a.size([0, 1])[1] * .7; return {type:'rect', shape:{x:s[0], y:s[1] - h / 2, width:Math.max(1, e[0] - s[0]), height:h}, style:{fill:series(7)}}; }, encode:{x:[1, 2], y:0}}]; mount(el, o); }
  if (r.workouts.length) { const w = tile(g, 'c4', '<h2>Workouts</h2>'); w.append(table([{k:'type', t:'Type'}, {k:'duration_min', t:'Min', f:v => nf(v, 0)}, {k:'distance_mi', t:'Miles', f:v => v ? nf(v) : ''}], r.workouts, {onRow:x => go('workout/' + x.workout_id)})); }
  const raw = tile(g, 'c8', `<h2>Compared with your baseline</h2><div class="muted">Every value, with its type and the prior 90-day median. ${r.coverage.tz ? `Time zone ${esc(r.coverage.tz)} (${esc(r.coverage.tz_conf)}).` : ''}</div>`); raw.append(table([{k:'label', t:'Metric'}, {k:'value', t:'Value', f:v => nf(v)}, {k:'baseline', t:'Baseline', f:v => nf(v)}, {k:'z', t:'Robust z', f:v => v == null ? '' : nf(v)}, {k:'kind', t:'Type'}], r.compare));
  const nt = tile(g, 'c4', `<h2>Your note</h2><textarea id="note" aria-label="Note for this day">${esc(r.note)}</textarea><button id="sn" type="button" style="margin-top:8px">Save note</button>`); $('#sn', nt).onclick = async e => { STATE.notes[day] = $('#note', nt).value; await saveState('notes'); e.target.textContent = 'Saved'; };
};
VIEWS.reports = async (host, [kind = 'year', key]) => {
  const yrs = [...new Set(B.dates.map(t => t.slice(0, 4)))], last = B.dates.at(-1); key ??= kind === 'year' ? yrs.at(-2) : kind === 'month' ? last.slice(0, 7) : last;
  head(host, 'Reports', 'Look back over a week, a month or a year. Print to PDF from your browser, or download the data.');
  const c = card(`<div class="row"><span class="seg" id="k">${[['week', 'Week'], ['month', 'Month'], ['year', 'Year']].map(([k, l]) => `<button type="button" data-v="${k}" class="${k === kind ? 'on' : ''}">${l}</button>`).join('')}</span>${kind === 'year' ? `<select id="kk">${yrs.map(y => `<option ${y === key ? 'selected' : ''}>${y}</option>`).join('')}</select>` : `<input id="kk" type="${kind === 'month' ? 'month' : 'date'}" value="${key}">`}<button id="dl" type="button">Download data</button><button onclick="print()" type="button">Print or PDF</button></div>`); host.append(c); c.style.marginBottom = '16px';
  $('#k', c).onclick = e => e.target.dataset.v && go('reports/' + e.target.dataset.v); $('#kk', c).onchange = e => go(`reports/${kind}/${e.target.value}`);
  const r = await api(`/api/report/${kind}/${key}`); $('#dl', c).onclick = () => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(r, null, 1)], {type:'application/json'})); a.download = `health-${kind}-${key}.json`; a.click(); };
  const g = bento(host);
  if (kind === 'year' && r.totals) { const T = r.totals; tile(g, 'c6 r2 hero', `<div class="eyebrow">${key} in review</div><div class="huge">${nf(T.steps / 1e6, 2)}M</div><div class="muted">steps, or ${nf(T.miles, 0)} miles on foot</div><p>${r.best_month ? `Your biggest month was ${r.best_month}. ` : ''}${T.runs ? `You logged ${nf(T.runs, 0)} runs for ${nf(T.run_miles, 0)} miles` : 'No runs were logged'}, plus ${nf(T.workout_hours, 0)} hours of workouts in total.</p><div class="herostats"><div><b>${nf(T.miles, 0)}</b><span>miles on foot</span></div><div><b>${nf(T.runs, 0)}</b><span>runs</span></div><div><b>${nf(T.workout_hours, 0)}</b><span>workout hours</span></div><div><b>${T.avg_sleep ? T.avg_sleep.toFixed(1) : 'n/a'}</b><span>hours of sleep</span></div></div>`);
    tile(g, 'c3', `<div class="eyebrow">Sleep</div><div class="big">${nf(T.avg_sleep)}<small>h</small></div><div class="delta">average night</div>`); tile(g, 'c3', `<div class="eyebrow">Running</div><div class="big">${nf(T.run_miles, 0)}<small>mi</small></div><div class="delta">${nf(T.runs, 0)} runs</div>`);
    const t = tile(g, 'c6', '<div class="eyebrow">Biggest days</div>'); t.append(table([{k:'day', t:'Day'}, {k:'steps', t:'Steps', f:v => nf(v)}], r.top_days, {onRow:x => go('day/' + x.day)}));
    if (r.series) lineChart(chartCard(g, 'Steps by month', '', 'c6'), {series:[{name:'Steps', t:r.series.t.map(x => x.slice(0, 7)), v:r.series.steps, color:series(2)}], bars:true, zoom:false, unit:'steps'});
    if (r.series) lineChart(chartCard(g, 'Sleep by month', 'Watch-scored', 'c6'), {series:[{name:'Sleep', t:r.series.t.map(x => x.slice(0, 7)), v:r.series.sleep, color:series(7)}], zoom:false, unit:'h'});
    if (r.records_in_year?.length) tile(g, 'c6', `<div class="eyebrow">Surprises this year</div><dl class="brief">${r.records_in_year.map(x => `<dt>${esc(x.headline)}</dt><dd>${esc(x.finding)}</dd>`).join('')}</dl>`); }
  else lineChart(chartCard(g, 'Daily steps', `${niceDate(r.start)} to ${niceDate(r.end)}`, 'c12', 'short'), {series:[{name:'Steps', t:r.days.t, v:r.days.steps, color:series(2)}], bars:true, zoom:false, unit:'steps', pick:k => go('day/' + k)});
  const t = tile(g, 'c12', `<div class="eyebrow">Compared with the previous ${kind}</div><div class="muted">${r.coverage.steps_days} of ${r.coverage.total} days have steps; ${r.coverage.worn_days} were fully worn.${r.unusual ? ` Most unusual: ${esc(r.unusual.metric)} on ${r.unusual.day}.` : ''}</div>`); t.append(table([{k:'label', t:'Metric'}, {k:'cur', t:'This period', f:(v, x) => show(x.metric, v)}, {k:'prev', t:'Previous', f:(v, x) => v == null ? 'n/a' : show(x.metric, v)}, {k:'ch', t:'Change', f:(_, x) => x.prev == null || CLOCK.has(x.metric) ? '' : (x.cur >= x.prev ? '+' : '') + nf(100 * (x.cur - x.prev) / (x.prev || 1), 0) + '%'}, {k:'kind', t:'Type'}], r.rows));
};
VIEWS.quality = async host => {
  const Q = await api('/api/quality'), v = Q.ingest.time_validation; head(host, 'Data quality', 'What the export contains, how it was cleaned, and where to be careful.');
  host.append(tiles([['Records', nf(Q.ingest.records_distinct, 0), 'verified against the raw XML'], ['Data types', nf(Q.inventory.length, 0), ''], ['Workouts', nf(Q.ingest.rows.workouts, 0), ''], ['Route points', nf(Q.ingest.rows.route_points, 0), ''], ['Days match Apple', nf(100 * v.within5_rebuilt, 1) + '%', `within 5%, was ${nf(100 * v.within5_naive, 1)}%`], ['Parse time', nf(Q.ingest.seconds, 0) + ' s', '']]));
  const g = bento(host); const s = v => nf(v, 0);
  const dd = tile(g, 'c6', '<h2>Double counting removed</h2><div class="muted">Overlap between sources, resolved by priority: Watch, then iPhone, then other apps.</div>'); dd.append(table([{k:'metric', t:'Metric'}, {k:'raw_sum', t:'Raw', f:s}, {k:'deduped_sum', t:'After', f:s}, {k:'x', t:'Removed', f:(_, x) => nf(100 * (1 - x.deduped_sum / x.raw_sum), 1) + '%'}], Q.dedupe));
  lineChart(chartCard(g, 'Watch wear', 'Share of days with heart rate that reached 14+ hours of coverage', 'c6', 'short'), {series:[{name:'Fully worn', t:Q.wear.filter(x => x.worn != null).map(x => x.yr), v:Q.wear.filter(x => x.worn != null).map(x => x.worn * 100), color:series(1)}], bars:true, zoom:false, unit:'%'});
  const dev = tile(g, 'c6', '<h2>Devices and apps</h2>'); dev.append(table([{k:'device', t:'Device'}, {k:'source', t:'Source'}, {k:'first_day', t:'First'}, {k:'last_day', t:'Last'}, {k:'n', t:'Records', f:s}], B.devices));
  const tz = tile(g, 'c6', '<h2>Time zone timeline</h2><div class="muted">Rebuilt from HKTimeZone, because every export timestamp carries the same -0700 offset.</div>'); tz.append(table([{k:'a', t:'From'}, {k:'b', t:'To'}, {k:'tz', t:'Zone'}, {k:'conf', t:'Basis'}], [...Q.tz].reverse()));
  const gp = tile(g, 'c6', '<h2>Gaps of 7+ days</h2>'); gp.append(table([{k:'series', t:'Series'}, {k:'start', t:'Start'}, {k:'end', t:'End'}, {k:'days', t:'Days'}], Q.gaps));
  const ot = tile(g, 'c6', '<h2>Values to inspect</h2><div class="muted">Outside broad plausibility limits. Flagged only, never deleted.</div>'); ot.append(Q.outliers.length ? table([{k:'type', t:'Type'}, {k:'n', t:'Count'}, {k:'min', t:'Lowest'}, {k:'max', t:'Highest'}], Q.outliers) : Object.assign(document.createElement('p'), {className:'empty', textContent:'None found.'})); ot.insertAdjacentHTML('beforeend', '<h3>Same-time duplicates by type</h3>'); ot.append(table([{k:'type', t:'Type'}, {k:'dup_groups', t:'Groups'}, {k:'extra_rows', t:'Extra rows'}], Q.duplicates));
  const inv = tile(g, 'c12', `<h2>Data dictionary</h2><div class="muted">Every discovered type.</div><div class="row" style="margin:8px 0"><input id="rt" placeholder="Type, such as RestingHeartRate" aria-label="Record type"><input id="rd" type="date" value="${B.dates.at(-1)}" aria-label="Day"><button id="rg" type="button">Inspect raw records</button></div><div id="rawout"></div>`); inv.append(table([{k:'type', t:'Type'}, {k:'n', t:'Records', f:s}, {k:'n_days', t:'Days', f:s}, {k:'first_day', t:'First'}, {k:'last_day', t:'Last'}, {k:'unit', t:'Unit'}, {k:'median_value', t:'Median', f:v => nf(v)}, {k:'sources', t:'Sources'}], Q.inventory, {onRow:x => { $('#rt', inv).value = x.type; $('#rd', inv).value = x.last_day; $('#rg', inv).click(); }}));
  $('#rg', inv).onclick = async () => { const r = await api(`/api/raw?type=${encodeURIComponent($('#rt', inv).value)}&day=${$('#rd', inv).value}`); $('#rawout', inv).replaceChildren(r.length ? table(Object.keys(r[0]).filter(k => k !== 'metadata').map(k => ({k, t:k})), r) : Object.assign(document.createElement('p'), {className:'empty', textContent:'No records for that type and day.'})); };
};
VIEWS.privacy = async host => {
  head(host, 'Privacy and data', 'Everything runs on this computer. The server listens on 127.0.0.1 only, rejects other sites, keeps no access logs, sends no telemetry and loads no remote code, fonts or map tiles.');
  const g = bento(host);
  tile(g, 'c6', `<h2>Export</h2><p class="muted">Your normalised data, in open formats.</p><div class="row"><a href="/api/export/daily.csv"><button type="button">Daily table (CSV)</button></a><a href="/api/export/data_dictionary.json" target="_blank"><button type="button">Data dictionary</button></a></div>`);
  const im = tile(g, 'c6', `<h2>Import a newer export</h2><p class="muted">Exports are cumulative. Records already seen are matched and not duplicated.</p><div class="row"><input id="ip" placeholder="~/Downloads/export.zip" style="flex:1" aria-label="Path to export"><button id="ib" type="button">Import</button></div><p id="im" class="muted"></p>`); $('#ib', im).onclick = async () => { $('#im', im).textContent = 'Importing.'; try { await post('/api/import', {path:$('#ip').value}); location.reload(); } catch (e) { $('#im', im).textContent = e.message; } };
  const del = tile(g, 'c12', `<h2>Delete everything</h2><p class="muted">Removes parsed data, the database, notes, annotations and saved analyses from this app. Your original export.zip is not touched. This cannot be undone.</p><div class="row"><input id="dc" placeholder="Type DELETE" aria-label="Type DELETE to confirm"><button id="db" type="button">Delete all data</button></div>`); $('#db', del).onclick = async () => { if ($('#dc').value !== 'DELETE') return; await post('/api/delete', {confirm:'DELETE'}); location.reload(); };
};
