"""Natural-language explorer, rule-based by design.

Each question maps to a computed tool. Nothing here calls a model, so no health data leaves
the machine and no number can be invented. Unmatched questions say so.
"""
import json
import re

import pandas as pd

from .catalog import METRICS
from .data import ROOT
from . import lab, stats as st


def res(answer, evidence=None, chart=None, assumptions=None, tool=""):
    return dict(answer=answer, evidence=evidence or [], chart=chart, assumptions=assumptions or [], tool=tool)


def changed_most(d, year):
    cur, prev = d[d.yr == year], d[d.yr == year - 1]
    out = []
    for m in ["steps", "distance_mi", "rhr", "hrv_sdnn", "vo2max", "asleep_watch_h", "deep_h", "rem_h", "bed_clock_adj", "walking_hr", "resp_rate", "walk_speed_mph"]:
        a, b = cur[m].dropna(), prev[m].dropna()
        if len(a) > 30 and len(b) > 30:
            out.append(dict(metric=METRICS[m]["label"], key=m, cur=float(a.median()), prev=float(b.median()), g=st.hedges_g(a.values, b.values)))
    return sorted(out, key=lambda r: -abs(r["g"]))


def answer(q, d, con, explore):
    t = q.lower().strip()
    insights = json.loads((ROOT / "insights.json").read_text()) if (ROOT / "insights.json").exists() else dict(cards=[], family=[])
    if not t:
        return res("Ask about your recorded data.")
    if re.search(r"changed? (the )?most", t):
        y = int(re.search(r"20\d\d", t).group(0)) if re.search(r"20\d\d", t) else int(d.yr.max())
        r = changed_most(d, y)
        top = r[0]
        return res(f"{top['metric']} changed most in {y} compared with {y-1}: median {top['cur']:.2f} against {top['prev']:.2f} (g = {top['g']:.2f}).", r[:8], dict(kind="series", metric=top["key"], freq="M"), [f"{y} is compared with {y-1} using medians.", "Partial years are compared as they stand, so season may contribute.", "Effect size is Hedges g."], "changed_most")
    if re.search(r"fitness.*best|best.*fitness|fastest", t):
        v = d.vo2max.dropna().rolling(90, min_periods=3).median()
        w = con.execute("SELECT local_date::VARCHAR d, pace_min_mi p, avg_hr h, distance_mi m FROM workout_facts WHERE type='Running' AND distance_mi>=2 AND pace_min_mi BETWEEN 5 AND 16 AND avg_hr IS NOT NULL").df()
        w["eff"] = w.p * w.h
        w["q"] = pd.to_datetime(w.d).dt.to_period("Q").astype(str)
        g = w.groupby("q").agg(eff=("eff", "median"), n=("eff", "size"))
        g = g[g.n >= 5].sort_values("eff")
        best = g.index[0] if len(g) else "n/a"
        return res(f"VO2 max estimate peaked around {v.idxmax().date()} at {v.max():.1f}. Pace multiplied by heart rate, where lower means faster for the effort, was best in {best}.", [dict(quarter=i, index=round(float(r.eff), 1), runs=int(r.n)) for i, r in g.head(5).iterrows()], dict(kind="series", metric="vo2max", freq="M"), ["VO2 max is a device estimate.", "Pace times heart rate is a rough proxy that ignores terrain and heat."], "fitness_peak")
    m = re.search(r"after (?:a )?long runs?|after (?:a )?run", t)
    if m:
        out = lab.find_metric(t.split("after")[0]) or "rhr"
        r = lab.run_experiment(d, dict(outcome=out, lag=1, exposure=[dict(metric="run_mi", op=">=", value=5)], control=[dict(metric="run_mi", op="==", value=0)]))
        return res(r["plain"], [dict(group="After 5+ mile run", n=r["result"]["n_a"], mean=r["result"]["mean_a"]), dict(group="After no run", n=r["result"]["n_b"], mean=r["result"]["mean_b"])], dict(kind="note", text=r["query"]), [r["query"]] + r["confounding"], "experiment")
    if re.search(r"(which|what) month.*consistent.*sleep|consistent sleep", t):
        s = d.asleep_watch_h.dropna()
        g = s.groupby(s.index.to_period("M")).agg(["std", "count", "mean"])
        g = g[g["count"] >= 20].sort_values("std")
        i = g.index[0]
        return res(f"{i.strftime('%B %Y')} had the most consistent sleep duration: SD {g['std'].iloc[0]:.2f} h around a mean of {g['mean'].iloc[0]:.2f} h, across {int(g['count'].iloc[0])} nights.", [dict(month=str(k), sd=round(float(r['std']), 2), mean=round(float(r['mean']), 2), nights=int(r['count'])) for k, r in g.head(6).iterrows()], None, ["Watch-scored nights only.", "Months need at least 20 nights."], "sleep_consistency")
    if re.search(r"workouts?.*(frequent|more often)|frequen.*workouts?", t):
        y = d.groupby("yr").workouts.sum(min_count=1).dropna()
        return res(f"Workouts by year run from {int(y.min())} to {int(y.max())}, peaking in {y.idxmax()}. The most recent year has {int(y.iloc[-1])} so far.", [dict(year=int(k), workouts=int(v)) for k, v in y.items()], dict(kind="bars", x=[str(i) for i in y.index], y=[int(v) for v in y.values], unit="workouts", title="Workouts by year"), ["Only logged workouts count."], "workout_frequency")
    if re.search(r"unusual|anomal|strange", t):
        c = [x for x in insights["cards"] if x["category"] == "Anomalies"]
        return res(f"{len(c)} periods stand out on the composite physiological deviation score.", [dict(headline=x["headline"], finding=x["finding"]) for x in c], None, ["Composite of resting HR, HRV, respiratory rate and sleeping HR, against the trailing 90 days.", "This describes data only. It is not a medical finding."], "anomalies")
    m = re.search(r"after (\d{4}-\d{2}-\d{2})", t)
    if m:
        a = pd.Timestamp(m.group(1))
        pre, post = d[a - pd.Timedelta(days=90):a - pd.Timedelta(days=1)], d[a:a + pd.Timedelta(days=89)]
        ev = [dict(metric=METRICS[k]["label"], before=round(float(pre[k].median()), 2), after=round(float(post[k].median()), 2), g=st.hedges_g(post[k].values, pre[k].values)) for k in ["steps", "rhr", "hrv_sdnn", "asleep_watch_h", "workout_min"] if pre[k].notna().sum() > 10 and post[k].notna().sum() > 10]
        return res(f"Compared with the 90 days before {a.date()}, here is how the next 90 days look.", ev, None, ["Windows are 90 days either side.", "Season differs between windows."], "before_after")
    if re.search(r"not explored|haven.t explored|unexplored", t):
        fam = pd.DataFrame(insights.get("family", []))
        low = fam[fam.n < 400].sort_values("n") if len(fam) else fam
        return res("These pre-listed pairs have the thinnest evidence so far, so they are the least explored.", [dict(x=METRICS[r.x]["label"], y=METRICS[r.y]["label"], lag=int(r.lag), n=int(r.n)) for _, r in low.head(8).iterrows()], None, ["Limited to the list of pre-specified pairs."], "family_gaps")
    if re.search(r"associations?|relationships?|correlat", t):
        fam = pd.DataFrame(insights.get("family", []))
        f = fam[(fam.q <= .05) & fam.stable.astype(bool)].copy()
        f = f.reindex(f.r.abs().sort_values(ascending=False).index)
        return res(f"{len(f)} of {int(fam.p.notna().sum())} pre-listed pairs survive false-discovery correction with the same sign in both halves of your record.", [dict(x=METRICS[r.x]["label"], y=METRICS[r.y]["label"], lag=int(r.lag), r=round(r.r, 2), q=round(r.q, 4), n=int(r.n)) for _, r in f.head(10).iterrows()], None, ["Series are detrended and deseasonalised first.", "All of these are associations, not causes."], "family")
    if re.search(r"distinctive|pattern", t):
        c = [x for x in insights["cards"] if x["category"] in ("Seasonality", "Consistency changes")]
        return res(f"{len(c)} patterns specific to your history were found.", [dict(headline=x["headline"], finding=x["finding"], grade=x["grade"]) for x in c[:8]], None, ["Graded by the discovery engine."], "patterns")
    spec = lab.parse_hypothesis(t)
    if spec:
        r = lab.run_experiment(d, spec)
        if "error" not in r:
            return res(r["plain"], [dict(group="Exposed", n=r["result"]["n_a"], mean=r["result"]["mean_a"]), dict(group="Comparison", n=r["result"]["n_b"], mean=r["result"]["mean_b"])], None, [r["query"]] + r["confounding"], "experiment")
    m = lab.find_metric(t)
    if m:
        s = d[m].dropna()
        return res(f"{METRICS[m]['label']}: median {s.median():,.2f} {METRICS[m]['unit']} across {len(s):,} days, from {s.index[0].date()} to {s.index[-1].date()}. Last 30 days median {s.tail(30).median():,.2f}.", [], dict(kind="series", metric=m, freq="M"), [METRICS[m]["note"] or "Daily values."], "metric_summary")
    return res("I could not match that to an analysis. Nothing was guessed. Try one of the suggested questions, or use the Lab to build the comparison yourself.", tool="none")
