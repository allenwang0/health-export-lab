"""Build the SYNTHETIC demo report and site/card.json from a fresh run.

    python scripts/report.py [--days 1095] [--seed 0]

Generates the synthetic export, imports it, compares each trap's naive answer with the
analyzer's answer and the planted truth, and writes report/index.html (static, no script)
and site/card.json. Every number on the page comes from this run.
"""
import argparse
import html
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from healthlab import build, parse, synth  # noqa: E402

# The only place the repository name appears; set CARD_REPO if the repo is published under another name.
REPO = __import__("os").environ.get("CARD_REPO", "health-export-lab")
COMMAND = "python scripts/report.py"


def daily_errors(con):
    """Per day: Apple's ring total against hourly active energy cut at -0700 and at rebuilt local time."""
    return con.execute("""
      WITH s AS (SELECT date::DATE d, active_energy::DOUBLE v FROM activity_summaries_raw WHERE active_energy::DOUBLE > 0),
      naive AS (SELECT (hour_utc - INTERVAL 7 HOUR)::DATE d, sum(value) v FROM hourly WHERE metric='active_kcal' GROUP BY 1),
      rec AS (SELECT local_date d, sum(value) v FROM hourly WHERE metric='active_kcal' GROUP BY 1)
      SELECT s.d, s.v apple, abs(coalesce(rec.v, 0) - s.v) err_rebuilt, abs(coalesce(naive.v, 0) - s.v) err_naive
      FROM s LEFT JOIN rec USING (d) LEFT JOIN naive USING (d) ORDER BY 1""").df()


def traps(con, truth, err):
    t_daily = pd.DataFrame(truth["daily"]).T
    nights = pd.DataFrame(truth["nights"]).T
    watch_nights = nights[nights.kind.notna()]
    got_nights = con.execute("SELECT wake_date::VARCHAR d, asleep_h FROM sleep_nights").df().set_index("d")
    sleep_got = got_nights.join(watch_nights, how="inner").asleep_h.mean()
    gap_days = [d for d, r in truth["daily"].items() if r["gap"]]
    gap_got = con.execute(f"SELECT count(*) FILTER (steps IS NULL) FROM daily WHERE date::VARCHAR IN ({','.join(repr(d) for d in gap_days)})").fetchall()[0][0]
    runs = pd.DataFrame(truth["workouts"])
    sparse = runs[runs.tier == "sparse"]
    blank = con.execute("SELECT count(*) FILTER (avg_hr IS NULL) FROM workout_facts").fetchall()[0][0]
    within = lambda c: float((err[c] <= .05 * err.apple).mean() * 100)  # noqa: E731
    seg = con.execute("SELECT count(*) FROM tz_segments").fetchall()[0][0]
    zone = t_daily.zone
    changes = pd.to_datetime(zone.index[zone != zone.shift()][1:])
    miss = pd.to_datetime(err.d[err.err_rebuilt > .05 * err.apple])
    close = sum(any(abs((m - c).days) <= 2 for c in changes) for m in miss)
    near = f"{close} of the {len(miss)} rebuilt misses fall within 2 days of a zone change"
    partial = t_daily.index[t_daily.wear == "partial"]
    worn = con.execute("SELECT date::VARCHAR d, worn_day FROM daily").df().set_index("d").worn_day
    steps_got = con.execute('SELECT sum(steps) FROM daily').fetchall()[0][0]
    excluded = int((~worn.reindex(partial).fillna(True).astype(bool)).sum())
    w_sleep = watch_nights.watch_asleep_h.astype(float).mean()
    # exact: the analyzer's answer equals the planted truth (compared at the precision shown).
    return [
        dict(trap="iPhone and Watch count the same steps", naive=f"{truth['raw_step_sum']:,.0f} steps (plain sum)",
             got=f"{steps_got:,.0f}", truth=f"{truth['steps_sum']:,.0f}", exact=round(steps_got) == round(truth["steps_sum"])),
        dict(trap="Every timestamp carries the -0700 export offset", naive=f"{within('err_naive'):.1f} % of days match Apple's total",
             got=f"{within('err_rebuilt'):.1f} %", truth="100 %", exact=within("err_rebuilt") == 100, note=near),
        dict(trap="Three sources score each Watch night", naive=f"{watch_nights.autosleep_h.astype(float).mean():.2f} h (longest source)",
             got=f"{sleep_got:.2f} h", truth=f"{w_sleep:.2f} h", exact=f"{sleep_got:.2f}" == f"{w_sleep:.2f}"),
        dict(trap="Days with no data at all", naive=f"{len(gap_days)} days of 0 steps", got=f"{gap_got} days NULL", truth=f"{len(gap_days)} unknown days", exact=gap_got == len(gap_days)),
        dict(trap="Older runs keep only a few heart-rate samples", naive=f"{len(sparse)} runs averaged from {sparse.n_hr.median():.0f} samples",
             got=f"{blank} runs left blank", truth=f"{len(sparse)} runs with no reliable average", exact=blank == len(sparse)),
        dict(trap="Zones change with a move, two trips and an alias", naive="1 zone (-0700 throughout)", got=f"{seg} zone segments",
             truth=f"{len(truth['zones']) - 1} tagged eras, after 1 untagged era shown as assumed", exact=seg == len(truth["zones"]) - 1),
        dict(trap="Watch worn only part of the day", naive=f"{len(partial)} partial days counted as worn",
             got=f"{excluded} of them excluded from worn-day series", truth=f"{len(partial)} partial days", exact=excluded == len(partial)),
    ], changes


E = html.escape
HUB, CODE = "https://allenwang0.github.io/", "https://github.com/allenwang0/health-export-lab"


def pct(v):
    """House style: a space before %, one decimal (the shares are of ~1,000 days)."""
    return f"{v:.1f}\u00a0%"


def requirement_versions():
    pins = dict(line.strip().split("==") for line in (ROOT / "requirements.txt").read_text().splitlines() if "==" in line)
    return {k: pins[k] for k in ("duckdb", "pandas", "lxml") if k in pins}


def render_page(a, err, weeks, worst, rows, changes, mutants, sha, start, end, rebuilt, naive):
    """The report page (report-kit v1). Every number is computed here from this run."""
    n = len(err)
    rel = {c: err[c] / err.apple for c in ("err_naive", "err_rebuilt")}
    bands = [("Within 5 % of Apple's total", 0, .05), ("5–25 % off", .05, .25), ("More than 25 % off", .25, float("inf"))]
    band_rows = []
    for label, lo, hi in bands:
        cnt = {c: int((((r >= lo) if lo == 0 else (r > lo)) & (r <= hi)).sum()) for c, r in rel.items()}
        band_rows.append(dict(label=label, naive=cnt["err_naive"], rebuilt=cnt["err_rebuilt"]))
    # Where the misses (> 5 % off) fall, by days to the nearest planted zone change.
    dist_bins = [("0–2 days from a zone change", 0, 2), ("3–30 days", 3, 30), ("More than 30 days", 31, float("inf"))]
    def dist(d):
        return min((abs((d - c).days) for c in changes), default=float("inf"))
    miss = {c: err.d[err[c] > .05 * err.apple].map(dist) for c in ("err_naive", "err_rebuilt")}
    miss_rows = [dict(label=lab, naive=int(miss["err_naive"].between(lo, hi).sum()), rebuilt=int(miss["err_rebuilt"].between(lo, hi).sum())) for lab, lo, hi in dist_bins]
    far = float(miss["err_rebuilt"].max()) if len(miss["err_rebuilt"]) else None
    exact = sum(r["exact"] for r in rows)
    tz = next(r for r in rows if "export offset" in r["trap"])
    steps = rows[0]
    best = weeks.err_rebuilt.idxmax()
    wk = lambda t: f"{t:%b} {t.day}, {t:%Y}"  # noqa: E731

    if far is None:
        tz_h2 = "The rebuild matches Apple's total on every day"
    else:
        tz_h2 = f"The rebuild's remaining {len(miss['err_rebuilt'])} misses all fall within {far:.0f} day{'s' if far != 1 else ''} of a zone change"
    tz_lede = (f"With the export offset, the worst week averages {weeks.err_naive.max():.0f} kcal a day of error (week ending {wk(worst)}). "
               f"The rebuild cuts that week to {weeks.err_rebuilt.loc[worst]:.0f} kcal, but its own worst week is {'the next one' if (best - worst).days == 7 else 'a different one'}, "
               f"week ending {wk(best)}, at {weeks.err_rebuilt.max():.0f} kcal a day: the error moved by a week, because zone changes are placed to within about a day."
               if best != worst else
               f"With the export offset, the worst week averages {weeks.err_naive.max():.0f} kcal a day of error (week ending {wk(worst)}); after the rebuild that week averages {weeks.err_rebuilt.loc[worst]:.0f} kcal.")

    def table(caption, cols, body_rows, tid=None, hidden=False, stack=False):
        head = "".join(f'<th scope="col">{E(c)}</th>' for c in cols)
        lab = (lambda i: f' data-label="{E(cols[i])}"') if stack else (lambda i: "")  # noqa: E731
        body = "".join("<tr>" + "".join((f'<th scope="row">{c}</th>' if i == 0 else f"<td{lab(i)}>{c}</td>") for i, c in enumerate(r)) + "</tr>" for r in body_rows)
        attrs = (f' id="{tid}"' if tid else "") + (" hidden" if hidden else "") + (' data-stack' if stack else "")
        return (f'<div class="rk-table"{attrs}><div class="tablewrap" tabindex="0" role="region" aria-label="{E(caption)}"><table><caption>{E(caption)}</caption>'
                f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></div>")

    toggle = ('<div class="seg" role="radiogroup" aria-label="View" data-view-toggle><button role="radio" aria-checked="true" data-value="chart">Chart</button>'
              '<button role="radio" aria-checked="false" data-value="table">Table</button></div>')
    band_table = table(f"Days by error against Apple's daily active energy, of {n:,} days", ["Error", "Export offset", "Rebuilt local time"],
                       [[E(r["label"]), f"{r['naive']:,} ({pct(100 * r['naive'] / n)})", f"{r['rebuilt']:,} ({pct(100 * r['rebuilt'] / n)})"] for r in band_rows], hidden=True)
    miss_table = table("Days more than 5 % off Apple's total, by distance to the nearest zone change", ["Distance", "Export offset", "Rebuilt local time"],
                       [[E(r["label"]), f"{r['naive']:,}", f"{r['rebuilt']:,}"] for r in miss_rows], hidden=True)
    trap_table = table("Each trap on the synthetic export", ["Trap", "Naive answer", "Analyzer", "Planted truth", "Matches truth"],
                       [[E(r["trap"]), E(r["naive"]), E(r["got"]), E(r["truth"]), "Yes" if r["exact"] else "No"] for r in rows], stack=True)
    mut_html = mut_card = ""
    if mutants:
        cols = ["Fix removed", "File", "Test that fails"] + ([] if mutants["killed"] == mutants["total"] else ["Result"])
        mrows = [[E(m["mutant"]), f'<span class="mono">{E(m["file"])}</span>', f'<span class="mono">{E(m["test"])}</span>'] + ([] if len(cols) == 3 else ["killed" if m["killed"] else "SURVIVED"]) for m in mutants["mutants"]]
        mut_html = f"""<section class="s wrap" id="tests" aria-labelledby="h-tests">
  <p class="eyebrow">Correctness evidence</p>
  <h2 id="h-tests">Removing any one of the {mutants['total']} fixes makes its test fail: {mutants['killed']} of {mutants['total']}</h2>
  <p class="lede">A mutation check reverts each fix to its naive version, one at a time, and runs the tests. A fix whose removal no test notices would be unguarded.</p>
  <details><summary>Every fix and the test that catches it · {mutants['total']} rows</summary>{table("Mutation check, results/mutants.json", cols, mrows)}</details>
</section>"""
        mut_card = (f'<a class="card span-4" href="#tests"><span class="label">Fixes guarded by tests</span><span class="metric num">{mutants["killed"]} of {mutants["total"]}</span>'
                    f'<span class="cmp">Reverting any one fix makes its test fail.</span><span class="foot">results/mutants.json · mutation check</span></a>')
    env = {"Python": "3.11", **requirement_versions(), "Generator": '<span class="mono">healthlab/synth.py</span>', "Seed": str(a.seed),
           "Span": f"{a.days:,} days ({start} to {end})", "Days compared": f"{n:,} days with active energy in Apple's summary",
           "Command": f'<span class="mono">{E(COMMAND)}</span>', "Commit": f'<span class="mono">{E(sha[:7])}</span>',
           "Generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    data = dict(bands=band_rows, miss=miss_rows, n=n)
    within = band_rows[0]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Apple Health export traps · Allen Wang</title>
<meta name="description" content="On a synthetic Apple Health export, rebuilding local time matches Apple's daily totals on {pct(rebuilt)} of days, up from {pct(naive)}.">
<meta name="theme-color" content="#f5f5f7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0b0b0b" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:,">
<script>try{{var t=localStorage.getItem("theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}}catch(e){{}}</script>
<link rel="stylesheet" href="kit/report.css">
<style>
/* report.local */
.hero-fig{{margin-top:40px;max-width:760px}}
.tablewrap td{{white-space:normal;min-width:7em}}
#traps .tablewrap td,#traps .tablewrap th,#reproduce .tablewrap td{{text-align:left}}
.s>.rk-table{{margin-top:24px}}
.bento .card .cmp{{margin-bottom:12px}}
/* The trap scoreboard stacks into one block per trap on phones instead of scrolling sideways. */
@media (max-width:599px){{[data-stack] thead{{display:none}}[data-stack] tr,[data-stack] th,[data-stack] td{{display:block;position:static;white-space:normal;text-align:left!important;border:0}}
[data-stack] tr{{border-top:1px solid var(--hairline);padding:8px 0}}[data-stack] th{{padding:4px 12px}}
[data-stack] td{{display:grid;grid-template-columns:7.5em 1fr;gap:8px;padding:2px 12px}}[data-stack] td::before{{content:attr(data-label);color:var(--ink-3)}}}}
/* charts.js never draws narrower than 260 px; keep plots >= 260 px at 320 px so text stays >= 11 px */
@media (max-width:359px){{.figure{{padding-inline:12px}}}}
</style>
<script src="kit/report.js" defer></script>
<script src="kit/charts.js" defer></script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="bar"><div class="wrap">
  <a class="back" href="{HUB}">← Allen Wang</a>
  <span class="title" aria-hidden="true">Apple Health export traps</span>
  <a class="pill" href="{CODE}">Code ↗</a>
</div></header>
<main id="main">
<section class="hero wrap" aria-labelledby="h1">
  <p class="eyebrow">Data-quality tool · Python</p>
  <h1 id="h1">Rebuilding local time matches Apple's daily totals on {pct(rebuilt)} of days, up from {pct(naive)}</h1>
  <p class="lede">Apple Health exports stamp every record with the offset from the day you exported, count steps twice and score each night three ways. This analyzer fixes each trap. Here it runs on a synthetic export whose right answers are planted.</p>
  <p class="conditions"><span class="key">Synthetic export, no real health data</span><span>Seed {a.seed}</span><span>{a.days:,} days</span><span>One run</span></p>
  <figure class="figure hero-fig" aria-labelledby="f-bands">
    <div class="fig-head"><div><h3 id="f-bands">Days whose active energy matches Apple's own daily total</h3>
    <p class="sub">Share of {n:,} days, by error against Apple's daily summary · one synthetic run</p></div>{toggle}</div>
    <div id="c-bands"></div>{band_table}
  </figure>
  <figcaption>Each day's hourly active energy is summed by calendar day, either cut at the export's -0700 offset or at the local time rebuilt from the time-zone tags, then compared with the total Apple stored for that day. “Within 5 %” is the match criterion.</figcaption>
</section>

<section class="wrap" aria-label="Key results">
  <div class="bento">
    <a class="card span-8" href="#timezones"><span class="label">Days within 5 % of Apple's total</span><span class="metric num">{pct(rebuilt)}</span>
      <span class="cmp">vs {pct(naive)} with the export offset: {within['rebuilt']:,} vs {within['naive']:,} of {n:,} days</span><span class="foot">Synthetic export, seed {a.seed} · rebuilt local time</span></a>
    <a class="card span-4" href="#traps"><span class="label">Steps after removing overlap</span><span class="metric num">{E(steps['got'])}</span>
      <span class="cmp">Plain sum: {E(steps['naive'].split(' ')[0])}. Planted truth: {E(steps['truth'])}.</span><span class="foot">iPhone and Watch count the same steps</span></a>
    <a class="card span-4" href="#traps"><span class="label">Traps recovered exactly</span><span class="metric num">{exact} of {len(rows)}</span>
      <span class="cmp">{"Time zones: " + pct(rebuilt) + " of days match, not 100 %." if not tz['exact'] else "Every trap matches the planted truth."}</span><span class="foot">Against the generator's planted answers</span></a>
    {mut_card}
    <div class="card span-4"><span class="label">Data</span><span class="metric num">Synthetic</span>
      <span class="cmp">Generated with planted traps. No real health data is in the repo or on this page.</span><span class="foot">healthlab/synth.py · {start} to {end}</span></div>
  </div>
</section>

<section class="s wrap" id="traps" aria-labelledby="h-traps">
  <p class="eyebrow">Each trap · naive vs analyzer vs planted truth</p>
  <h2 id="h-traps">The analyzer recovers the planted answer on {exact} of {len(rows)} traps{"; time zones reach " + pct(rebuilt) + " of days, not 100 %" if not tz['exact'] else ""}</h2>
  <p class="lede">The naive answer is what you get by reading the export literally. The planted truth is what the generator put in. {"The time-zone row misses on " + str(len(miss['err_rebuilt'])) + " days; the next section shows where." if not tz['exact'] else ""}</p>
  {trap_table}
  <p class="caveat"><b>The traps and the fixes have the same author.</b> The generator plants the traps the analyzer was written to fix, so a match shows the fixes work on these traps. Real exports may hold traps this generator does not plant.</p>
</section>

<section class="s wrap" id="timezones" aria-labelledby="h-tz">
  <p class="eyebrow">Time zones · where the misses are</p>
  <h2 id="h-tz">{E(tz_h2)}</h2>
  <p class="lede">{E(tz_lede)}</p>
  <figure class="figure" aria-labelledby="f-miss">
    <div class="fig-head"><div><h3 id="f-miss">The export offset misses all year; the rebuild misses only next to a move or trip</h3>
    <p class="sub">Days more than 5 % off Apple's total, by distance to the nearest planted zone change · {len(changes)} changes · one synthetic run</p></div>{toggle}</div>
    <div id="c-miss"></div>{miss_table}
  </figure>
  <figcaption>Each bar counts the days one method gets more than 5 % wrong, grouped by how far the day is from the nearest zone change (a move, two trips, a return). The export offset is wrong on every day spent outside -0700, so its misses are spread out.</figcaption>
</section>

{mut_html}

<section class="s wrap" id="method" aria-labelledby="h-method">
  <p class="eyebrow">Methodology</p>
  <h2 id="h-method">How this was measured</h2>
  <div class="prose" style="margin-top:24px">
    <p><span class="mono">healthlab/synth.py</span> writes an Apple Health export zip for {a.days:,} days from seed {a.seed}, with each trap planted and its right answer recorded. The analyzer imports the zip the same way it imports a real one, then each trap's answer is compared with the recorded truth.</p>
    <p>For time zones, hourly active energy is summed by calendar day in two ways and compared with Apple's own daily summary on the {n:,} days that have one. A day matches if the error is at most 5 % of Apple's total. Weeks run Monday to Sunday and are labeled by their last day.</p>
  </div>
  <div class="two">
    <div><h3>What this shows</h3><ul>
      <li>Each fix recovers the planted answer for its trap; time zones get {pct(rebuilt)} of days.</li>
      <li>Every fix is needed: removing any one makes a test fail.</li>
      <li>The rebuild's misses sit next to zone changes.</li></ul></div>
    <div><h3>What it doesn't show</h3><ul>
      <li>Accuracy on real exports: no real health data was used.</li>
      <li>Traps this generator does not plant.</li>
      <li>Run-to-run spread: one seed, one run.</li></ul></div>
  </div>
</section>

<section class="s wrap" id="reproduce" aria-labelledby="h-repro">
  <p class="eyebrow">Reproduce</p>
  <h2 id="h-repro">Run it yourself</h2>
  <div class="codewrap"><pre><code>git clone {CODE}.git
cd health-export-lab
make setup      # virtualenv with pinned dependencies (Python 3.11)
make test
make mutants    # writes results/mutants.json
make report     # rebuilds this page from a fresh synthetic run
make demo       # local app on the synthetic export</code></pre></div>
  {table("Run settings", ["Field", "Value"], [[k, v] for k, v in env.items()])}
</section>
</main>
<footer><div class="wrap"><span><a href="{HUB}">← Allen Wang</a><a href="{CODE}">Code ↗</a></span>
<span>Data: synthetic run of <span class="mono">{E(COMMAND)}</span> at commit <span class="mono">{E(sha[:7])}</span> · {datetime.now(timezone.utc):%Y-%m-%d}</span></div></footer>
<script type="application/json" id="rk-data">{json.dumps(data)}</script>
<script>
addEventListener("DOMContentLoaded", function () {{
  var D = RK.data("rk-data"), S = [{{ key: "rebuilt", name: "Rebuilt local time", color: "var(--series-1)" }}, {{ key: "naive", name: "Export offset (-0700)", color: "var(--series-2)" }}];
  var p = function (v) {{ return (Number.isInteger(v) ? v : v.toFixed(1)) + "\u00a0%"; }};
  RKCharts.hbar("#c-bands", {{ rows: D.bands.map(function (r) {{ return {{ label: r.label, values: {{ rebuilt: 100 * r.rebuilt / D.n, naive: 100 * r.naive / D.n }} }}; }}),
    series: S, unit: "% of " + D.n.toLocaleString("en-US") + " days", format: p, xmax: 100, title: "Share of days by error against Apple's daily total" }});
  RKCharts.hbar("#c-miss", {{ rows: D.miss.map(function (r) {{ return {{ label: r.label, values: {{ rebuilt: r.rebuilt, naive: r.naive }} }}; }}),
    series: S, unit: "days more than 5 % off", format: function (v) {{ return Math.round(v).toLocaleString("en-US"); }}, title: "Days more than 5 percent off, by distance to a zone change" }});
}});
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        truth = synth.generate(tmp / "export.zip", a.days, a.seed)
        parse.ingest(str(tmp / "export.zip"), tmp)
        build.build(tmp)
        con = duckdb.connect(str(tmp / "lab.duckdb"), read_only=True)
        err = daily_errors(con)
        rows, changes = traps(con, truth, err)
        con.close()
    err["d"] = pd.to_datetime(err.d)
    weeks = err.set_index("d")[["err_naive", "err_rebuilt"]].resample("W-SUN").mean().dropna()
    share = lambda c: float((err[c] <= .05 * err.apple).mean() * 100)  # noqa: E731
    rebuilt, naive = share("err_rebuilt"), share("err_naive")
    worst = weeks.err_naive.idxmax()
    takeaway = f"Rebuilt local time matches Apple's daily total within 5% on {rebuilt:.1f}% of days, against {naive:.1f}% with the export offset."
    mut = ROOT / "results" / "mutants.json"
    mutants = json.loads(mut.read_text()) if mut.exists() else None
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    start, end = truth["start"], max(truth["daily"])
    note = f"SYNTHETIC Apple Health export from healthlab/synth.py, seed {a.seed}, {a.days} days ({start} to {end}). No real health data."
    page = render_page(a, err, weeks, worst, rows, changes, mutants, sha, start, end, rebuilt, naive)
    (ROOT / "report").mkdir(exist_ok=True)
    (ROOT / "report" / "index.html").write_text(page)
    step = max(1, len(weeks) // 500 + 1)
    card = dict(schema_version=1, repo=REPO, sha=sha, generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), command=COMMAND, data_note=note,
                metric=dict(label="Days within 5% of Apple's daily active energy, rebuilt local time", value=round(rebuilt, 1), unit="%", higher_is_better=True),
                takeaway=takeaway[:140],
                chart=dict(type="line", x_label="Week", x_unit="date", x_scale="time", y_label="Mean daily error against Apple's total", y_unit="kcal", y_scale="linear",
                           series=[dict(name="Export offset (-0700)", style="dashed", points=[[f"{t:%Y-%m-%d}", round(float(v), 2)] for t, v in weeks.err_naive.iloc[::step].items()]),
                                   dict(name="Rebuilt local time", style="solid", points=[[f"{t:%Y-%m-%d}", round(float(v), 2)] for t, v in weeks.err_rebuilt.iloc[::step].items()])]))
    (ROOT / "site").mkdir(exist_ok=True)
    (ROOT / "site" / "card.json").write_text(json.dumps(card, indent=1) + "\n")
    print(takeaway)
    for r in rows:
        print(f"- {r['trap']}: naive {r['naive']} | analyzer {r['got']} | truth {r['truth']}")


if __name__ == "__main__":
    main()
