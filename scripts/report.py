"""Run the SYNTHETIC trap experiment and write the result files the report is built from.

    python scripts/report.py [--days 1095] [--seed 0] [--out results/report.json]
    python scripts/report.py --seeds 20 [--out results/seeds.json]

Generates the synthetic export, imports it, and compares each trap's naive answer with the
analyzer's answer and the planted truth. The first form writes the full detail for one seed;
the second writes the key numbers for seeds 0..N-1. scripts/build_report.py renders
report/index.html, site/card.json and the README numbers from these files, so every number
on the page comes from a committed run.
"""
import argparse
import contextlib
import io
import json
import platform
import subprocess
import sys
import tempfile
import warnings
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from healthlab import build, data, insights, parse, synth  # noqa: E402

warnings.simplefilter("ignore")
EXERCISE = {"run_mi", "run_min", "runs", "workout_min", "workout_kcal", "active_kcal_worn", "active_kcal"}


def planted(r):
    """The generator's one planted effect: exercise lowers resting heart rate on the following days."""
    return bool(r.y == "rhr" and r.lag >= 1 and r.x in EXERCISE)


def r6(v):
    return None if v is None or v != v else round(float(v), 6)


def daily_errors(con):
    """Per day: Apple's ring total against hourly active energy cut at -0700 and at rebuilt local time."""
    return con.execute("""
      WITH s AS (SELECT date::DATE d, active_energy::DOUBLE v FROM activity_summaries_raw WHERE active_energy::DOUBLE > 0),
      naive AS (SELECT (hour_utc - INTERVAL 7 HOUR)::DATE d, sum(value) v FROM hourly WHERE metric='active_kcal' GROUP BY 1),
      rec AS (SELECT local_date d, sum(value) v FROM hourly WHERE metric='active_kcal' GROUP BY 1)
      SELECT s.d, s.v apple, abs(coalesce(rec.v, 0) - s.v) err_rebuilt, abs(coalesce(naive.v, 0) - s.v) err_naive
      FROM s LEFT JOIN rec USING (d) LEFT JOIN naive USING (d) ORDER BY 1""").df()


def step_example(con):
    """First hour in which both iPhone and Watch logged steps: raw samples and the deduplicated hour."""
    hour = con.execute("""SELECT date_trunc('hour', start_utc) h FROM samples WHERE type='StepCount'
                          GROUP BY 1 HAVING count(DISTINCT source_name) = 2 ORDER BY 1 LIMIT 1""").fetchall()[0][0]
    rows = con.execute("""SELECT source_name, start_utc, end_utc, value FROM samples WHERE type='StepCount'
                          AND date_trunc('hour', start_utc) = ? ORDER BY start_utc, source_name""", [hour]).fetchall()
    kept = con.execute("SELECT sum(value) FROM hourly WHERE metric='steps' AND hour_utc = ?", [hour]).fetchall()[0][0]
    return dict(hour_utc=f"{hour:%Y-%m-%d %H:%M}", kept=r6(kept),
                samples=[dict(source=s.replace("\xa0", " "), start=f"{a:%H:%M}", end=f"{b:%H:%M}", value=r6(v)) for s, a, b, v in rows])


def measure(days, seed, detail):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        truth = synth.generate(tmp / "export.zip", days, seed)
        with contextlib.redirect_stdout(io.StringIO()):
            parse.ingest(str(tmp / "export.zip"), tmp)
            build.build(tmp)
        con = duckdb.connect(str(tmp / "lab.duckdb"), read_only=True)
        err = daily_errors(con)
        t_daily = pd.DataFrame(truth["daily"]).T
        nights = pd.DataFrame(truth["nights"]).T
        watch_nights = nights[nights.kind.notna()]
        got_nights = con.execute("SELECT wake_date::VARCHAR d, asleep_h FROM sleep_nights").df().set_index("d")
        sleep_got = got_nights.join(watch_nights, how="inner").asleep_h
        gap_days = [d for d, r in truth["daily"].items() if r["gap"]]
        gap_null = con.execute(f"SELECT count(*) FILTER (steps IS NULL) FROM daily WHERE date::VARCHAR IN ({','.join(repr(d) for d in gap_days)})").fetchall()[0][0]
        runs = pd.DataFrame(truth["workouts"])
        sparse = runs[runs.tier == "sparse"]
        blank = con.execute("SELECT count(*) FILTER (avg_hr IS NULL) FROM workout_facts").fetchall()[0][0]
        within = lambda c: float((err[c] <= .05 * err.apple).mean())  # noqa: E731
        zone = t_daily.zone
        changes = pd.to_datetime(zone.index[zone != zone.shift()][1:])
        miss = pd.to_datetime(err.d[err.err_rebuilt > .05 * err.apple])
        partial = t_daily.index[t_daily.wear == "partial"]
        worn = con.execute("SELECT date::VARCHAR d, worn_day FROM daily").df().set_index("d").worn_day
        fam = insights.relationship_family(data.load_daily(con))
        surv = fam[fam.q <= .10]
        out = dict(
            seed=seed, days=days,
            steps=dict(plain_sum=r6(truth["raw_step_sum"]), recovered=r6(con.execute("SELECT sum(steps) FROM daily").fetchall()[0][0]), truth=r6(truth["steps_sum"])),
            time=dict(days_compared=int(len(err)), within5_naive=r6(within("err_naive")), within5_rebuilt=r6(within("err_rebuilt")),
                      misses_rebuilt=int(len(miss)), misses_near_change=int(sum(any(abs((m - c).days) <= 2 for c in changes) for m in miss))),
            sleep=dict(nights=int(len(sleep_got)), longest_source_h=r6(watch_nights.autosleep_h.astype(float).mean()),
                       analyzer_h=r6(sleep_got.mean()), truth_h=r6(watch_nights.watch_asleep_h.astype(float).mean())),
            gaps=dict(days=len(gap_days), analyzer_null=int(gap_null)),
            sparse_runs=dict(runs=int(len(sparse)), median_samples=r6(sparse.n_hr.median()), analyzer_blank=int(blank)),
            partial_wear=dict(days=int(len(partial)), excluded=int((~worn.reindex(partial).fillna(True).astype(bool)).sum())),
            family=dict(tested=int(fam.p.notna().sum()), surviving=int(len(surv)),
                        survivors_planted=bool(len(surv) and all(planted(r) for r in surv.itertuples())),
                        survivors=[dict(x=r.x, y=r.y, lag=int(r.lag), r=r6(r.r), q=float(f"{r.q:.3g}"), planted=planted(r)) for r in surv.itertuples()]),
        )
        if detail:
            err["d"] = pd.to_datetime(err.d)
            weeks = err.set_index("d")[["err_naive", "err_rebuilt"]].resample("W-SUN").mean().dropna()
            out["start"], out["end"] = truth["start"], max(truth["daily"])
            out["steps"]["example"] = step_example(con)
            out["time"]["weeks"] = [[f"{t:%Y-%m-%d}", r6(a), r6(b)] for t, (a, b) in weeks.iterrows()]
            out["time"]["zone_changes"] = [[f"{c:%Y-%m-%d}", zone[f"{c:%Y-%m-%d}"]] for c in changes]
            out["zones"] = dict(segments=int(con.execute("SELECT count(*) FROM tz_segments").fetchall()[0][0]), tagged_eras=len(truth["zones"]) - 1)
        con.close()
    return out


def run_info():
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return dict(code_sha=sha, date=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), python=platform.python_version(),
                duckdb=duckdb.__version__, pandas=pd.__version__, platform=platform.platform(terse=True), machine=platform.machine())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, help="run seeds 0..N-1 and write the summary instead")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.seeds:
        rows = []
        for s in range(a.seeds):
            rows.append(measure(a.days, s, False))
            print(f"seed {s}: steps {rows[-1]['steps']}, within 5% {rows[-1]['time']['within5_naive']:.3f} -> {rows[-1]['time']['within5_rebuilt']:.3f}", flush=True)
        result, out = dict(synthetic=True, days=a.days, run=run_info(), seeds=rows), Path(a.out or ROOT / "results" / "seeds.json")
    else:
        result, out = dict(synthetic=True, run=run_info(), **measure(a.days, a.seed, True)), Path(a.out or ROOT / "results" / "report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
