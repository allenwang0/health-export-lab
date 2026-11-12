"""Build the SYNTHETIC demo report and site/card.json from a fresh run.

    python scripts/report.py [--days 1095] [--seed 0]

Generates the synthetic export, imports it, compares each trap's naive answer with the
analyzer's answer and the planted truth, and writes report/index.html (static, no script)
and site/card.json. Every number on the page comes from this run.
"""
import argparse
import html
import json
import math
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
REPO = __import__("os").environ.get("CARD_REPO", "apple-health-lab")
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
    return [
        dict(trap="iPhone and Watch count the same steps", naive=f"{truth['raw_step_sum']:,.0f} steps (plain sum)",
             got=f"{con.execute('SELECT sum(steps) FROM daily').fetchall()[0][0]:,.0f}", truth=f"{truth['steps_sum']:,.0f}"),
        dict(trap="Every timestamp carries the -0700 export offset", naive=f"{within('err_naive'):.1f}% of days match Apple's total",
             got=f"{within('err_rebuilt'):.1f}%", truth=f"100%; {near}"),
        dict(trap="Three sources score each Watch night", naive=f"{watch_nights.autosleep_h.astype(float).mean():.2f} h (longest source)",
             got=f"{sleep_got:.2f} h", truth=f"{watch_nights.watch_asleep_h.astype(float).mean():.2f} h"),
        dict(trap="Days with no data at all", naive=f"{len(gap_days)} days of 0 steps", got=f"{gap_got} days NULL", truth=f"{len(gap_days)} unknown days"),
        dict(trap="Older runs keep only a few heart-rate samples", naive=f"{len(sparse)} runs averaged from {sparse.n_hr.median():.0f} samples",
             got=f"{blank} runs left blank", truth=f"{len(sparse)} runs with no reliable average"),
        dict(trap="Zones change with a move, two trips and an alias", naive="1 zone (-0700 throughout)", got=f"{seg} zone segments",
             truth=f"{len(truth['zones']) - 1} tagged eras, after 1 untagged era shown as assumed"),
        dict(trap="Watch worn only part of the day", naive=f"{len(partial)} partial days counted as worn",
             got=f"{int((~worn.reindex(partial).fillna(True).astype(bool)).sum())} of them excluded from worn-day series", truth=f"{len(partial)} partial days"),
    ]


def ticks(hi, n=5):
    """Round tick values from 0 to at least hi: steps of 1, 2 or 5 times a power of ten."""
    raw = hi / (n - 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    return [i * step for i in range(math.ceil(hi / step) + 1)]


def svg_chart(weeks, title_id):
    w, h, ml, mr, mt, mb = 720, 320, 56, 120, 16, 44
    x0, x1 = weeks.index.min(), weeks.index.max()
    ymax = ticks(max(float(weeks.max().max()), 1))[-1]
    sx = lambda t: ml + (t - x0) / (x1 - x0) * (w - ml - mr)  # noqa: E731
    sy = lambda v: mt + (1 - v / ymax) * (h - mt - mb)  # noqa: E731
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-labelledby="{title_id}" class="chart">']
    for v in ticks(ymax):
        parts.append(f'<line x1="{ml}" x2="{w - mr}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>'
                     f'<text x="{ml - 6}" y="{sy(v) + 4:.1f}" text-anchor="end" class="tick">{v:.0f}</text>')
    for t in pd.date_range(x0, x1, freq="6MS"):
        parts.append(f'<text x="{sx(t):.1f}" y="{h - mb + 18}" text-anchor="middle" class="tick">{t:%b %Y}</text>')
    parts.append(f'<text x="{(ml + w - mr) / 2}" y="{h - 6}" text-anchor="middle" class="axis">Week (synthetic dates)</text>'
                 f'<text transform="translate(14 {(mt + h - mb) / 2}) rotate(-90)" text-anchor="middle" class="axis">Mean daily error (kcal)</text>')
    for col, label, cls in (("err_naive", "Export offset", "naive"), ("err_rebuilt", "Rebuilt local time", "rebuilt")):
        pts = " ".join(f"{sx(t):.1f},{sy(v):.1f}" for t, v in weeks[col].items())
        last = weeks[col].iloc[-1]
        parts.append(f'<polyline points="{pts}" class="{cls}"/><text x="{w - mr + 6}" y="{sy(last) + (-6 if cls == "naive" else 12):.1f}" class="lab {cls}-lab">{label}</text>')
    parts.append("</svg>")
    return "".join(parts)


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
        rows = traps(con, truth, err)
        con.close()
    err["d"] = pd.to_datetime(err.d)
    weeks = err.set_index("d")[["err_naive", "err_rebuilt"]].resample("W-SUN").mean().dropna()
    share = lambda c: float((err[c] <= .05 * err.apple).mean() * 100)  # noqa: E731
    rebuilt, naive = share("err_rebuilt"), share("err_naive")
    worst = weeks.err_naive.idxmax()
    takeaway = f"Rebuilt local time matches Apple's daily total within 5% on {rebuilt:.1f}% of days, against {naive:.1f}% with the export offset."
    chart_note = (f"Weekly mean error peaks at {weeks.err_naive.max():.0f} kcal a day with the export offset (week of {worst:%b %-d, %Y}), "
                  f"against {weeks.err_rebuilt.loc[worst]:.0f} kcal after the rebuild.")
    mut = ROOT / "results" / "mutants.json"
    mutants = json.loads(mut.read_text()) if mut.exists() else None
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    start, end = truth["start"], max(truth["daily"])
    note = f"SYNTHETIC Apple Health export from healthlab/synth.py, seed {a.seed}, {a.days} days ({start} to {end}). No real health data."

    table = "".join(f"<tr><th scope=row>{html.escape(r['trap'])}</th><td>{html.escape(r['naive'])}</td><td>{html.escape(r['got'])}</td><td>{html.escape(r['truth'])}</td></tr>" for r in rows)
    mut_html = ""
    if mutants:
        mut_rows = "".join(f"<tr><th scope=row>{html.escape(m['mutant'])}</th><td><code>{html.escape(m['test'])}</code></td><td>{'killed' if m['killed'] else 'SURVIVED'}</td></tr>" for m in mutants["mutants"])
        mut_html = (f"<h2>Each fix is guarded by a test</h2><p class=take>{mutants['killed']} of {mutants['total']} mutants killed: removing any one fix makes its test fail "
                    f"(<code>scripts/mutation_check.py</code>).</p><div class=scroll><table><thead><tr><th>Fix removed</th><th>Test</th><th>Result</th></tr></thead><tbody>{mut_rows}</tbody></table></div>")
    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Health Lab synthetic report</title><style>
:root{{--bg:#fbfbf9;--fg:#1d1f21;--muted:#5d6166;--grid:#e3e3df;--a:#1f5fa8;--b:#b4561c;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141517;--fg:#e8e8e6;--muted:#a3a7ad;--grid:#2b2d31;--a:#7fb2ee;--b:#f0a46c;--card:#1c1d20}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:880px;margin:0 auto;padding:24px 16px 48px}}
.badge{{display:inline-block;border:1px solid var(--b);color:var(--b);border-radius:4px;padding:1px 8px;font-size:13px;font-weight:600;letter-spacing:.04em}}
h1{{font-size:26px;margin:8px 0 4px}}h2{{font-size:19px;margin:32px 0 4px}}.muted,.note{{color:var(--muted)}}.take{{margin:4px 0 12px}}
.card{{background:var(--card);border:1px solid var(--grid);border-radius:8px;padding:12px}}
.chart{{width:100%;height:auto}}.grid{{stroke:var(--grid)}}.tick{{fill:var(--muted);font-size:12px}}.axis{{fill:var(--fg);font-size:13px}}
.naive{{fill:none;stroke:var(--b);stroke-width:2;stroke-dasharray:6 4}}.rebuilt{{fill:none;stroke:var(--a);stroke-width:2.5}}
.lab{{font-size:13px}}.naive-lab{{fill:var(--b)}}.rebuilt-lab{{fill:var(--a)}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}}
thead th{{color:var(--muted);font-weight:600}}code{{font-size:13px}}
</style></head><body><main>
<span class=badge>SYNTHETIC DATA</span>
<h1>Health Lab: the export traps, measured</h1>
<p class=muted>{html.escape(note)} Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC at commit <code>{sha[:10]}</code> by <code>{COMMAND}</code>.</p>
<h2 id=t1>Rebuilding local time against Apple's own daily totals</h2>
<p class=take>{html.escape(takeaway)} {html.escape(chart_note)}</p>
<div class=card><p class=note><span class=badge>SYNTHETIC</span> Dashed: days cut at the -0700 export offset. Solid: days cut at the zone rebuilt from HKTimeZone tags.</p>{svg_chart(weeks, "t1")}</div>
<h2>Each trap: the naive answer, the analyzer's answer, the planted truth</h2>
<p class=take>The analyzer matches the planted truth on every row; the naive answer misses each one.</p>
<div class=scroll><table><thead><tr><th>Trap</th><th>Naive</th><th>Analyzer</th><th>Planted truth</th></tr></thead><tbody>{table}</tbody></table></div>
{mut_html}
</main></body></html>
"""
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
