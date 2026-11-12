"""Automated discovery. Runs a fixed family of analyses and reports the whole family.

Honesty rules enforced here:
- Every cross-metric test is counted; q-values use the complete family, including nulls.
- A cross-metric result must survive detrending, deseasonalising, an autocorrelation-aware
  test and sign agreement across both halves of the record to reach grade A or B.
- A shift near a device, wear or sleep-source transition is reported as a coverage question.
"""
import json
import re

import numpy as np
import pandas as pd

from .catalog import METRICS, PAIRS, TREND_METRICS
from .data import ROOT, connect, json_safe, load_daily
from . import stats as st

NON_CLINICAL = "An observed pattern in device data. It is not a medical finding."


def lab(m):
    return METRICS[m]["label"]


def unit(m):
    return METRICS[m]["unit"]


def fmt(v, m=None):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    a = abs(v)
    return f"{v:,.0f}" if a >= 100 else f"{v:.1f}" if a >= 10 else f"{v:.2f}"


def clock(h):
    h = h % 24
    return f"{int(h) % 12 or 12}:{int(round((h % 1) * 60)) % 60:02d} {'am' if h < 12 else 'pm'}"


SLEEP = {"asleep_watch_h", "asleep_h", "deep_h", "rem_h", "core_h", "awake_h", "sleep_eff", "bed_clock_adj", "wake_clock"}
PHONE = {"steps", "distance_mi", "flights"}
WEAR = {"steps", "distance_mi", "flights", "active_kcal_worn", "hr_mean"}
WATCH = {k for k, v in METRICS.items() if k not in {"weight_lb", "water_ml"}}


def transitions(con, d):
    """(date, label, metrics affected). Recording changes, kept separate from behaviour."""
    out = []
    for row in con.execute("""SELECT device, min(first_day) a, sum(n) n FROM device_periods
                              WHERE device LIKE 'iPhone%' GROUP BY 1 HAVING sum(n)>2000 ORDER BY a""").fetchall():
        out.append((pd.Timestamp(row[1]), f"{row[0]} starts recording", PHONE))
    first_watch = con.execute("SELECT min(first_day) FROM device_periods WHERE device LIKE 'Watch%'").fetchall()[0][0]
    if first_watch:
        out.append((pd.Timestamp(first_watch), "First Apple Watch data", WATCH))
    for major, day in con.execute("""SELECT split_part(source_version,'.',1)::INT m, min(local_date) FROM samples
            WHERE type='HeartRate' AND regexp_matches(source_version,'^[0-9]+') AND hardware=(SELECT hardware FROM samples
            WHERE type='HeartRate' AND hardware LIKE 'Watch%' GROUP BY 1 ORDER BY count(*) DESC LIMIT 1) GROUP BY 1 ORDER BY 2""").fetchall()[1:]:
        out.append((pd.Timestamp(day), f"watchOS {major} installed", WATCH))
    wear = d.worn_day.astype(float).where(d.hr_n.notna()).rolling(60, min_periods=20).mean()
    was_high = False
    for t, v in wear.dropna().items():
        if v > .9:
            was_high = True
        if was_high and v < .7:
            out.append((t, "Watch wear falls below 70% of days", WEAR))
            was_high = False
    for kind, label in (("stages", "Watch sleep stages begin"), ("%", "Watch sleep tracking begins")):
        first = con.execute("SELECT min(wake_date) FROM sleep_nights WHERE source LIKE 'Watch%' AND kind LIKE ?", [kind]).fetchall()[0][0]
        if first:
            out.append((pd.Timestamp(first), label, SLEEP))
    return sorted(out, key=lambda t: t[0])


def near_transition(date, trans, metric, days=45):
    t = pd.Timestamp(date)
    return [n for tt, n, scope in trans if abs((tt - t).days) <= days and metric in scope]


def lname(m):
    return re.sub(r"\b(Walking|Sleep|Steps|Body|Resting|Running|Deep|Active|Mean|Physical|Workout|Daily)\b", lambda x: x.group(0).lower(), lab(m))


def show(v, m):
    return clock(v) if m in ("bed_clock_adj", "wake_clock") else f"{fmt(v)} {unit(m)}"


def delta(v, m):
    if m in ("bed_clock_adj", "wake_clock"):
        return f"{abs(v) * 60:.0f} min {'later' if v > 0 else 'earlier'}"
    return f"{v:+,.2f} {unit(m)}" if abs(v) < 100 else f"{v:+,.0f} {unit(m)}"


def adverb(m, up):
    if m in ("bed_clock_adj", "wake_clock"):
        return "moved later" if up else "moved earlier"
    return "rose" if up else "fell"


def sleep_crosscheck(con, a, b):
    """Change in AutoSleep's nightly total over the same windows: separately scored."""
    r = con.execute("""SELECT avg(asleep_h) FILTER (wake_date>=?), avg(asleep_h) FILTER (wake_date<?) FROM sleep_cross
                       WHERE source='autosleep' AND asleep_h BETWEEN 3 AND 14 AND wake_date>=?""", [a, a, b]).fetchall()[0]
    return None if None in r else r[0] - r[1]


def card(cat, headline, finding, compared, n, effect, uncertainty, grade, confounders, limitations, next_q, viz,
         raw=None, effect_size=0.0, quality=.7, novelty=.5, tests=None):
    grade_w = dict(A=1, B=.75, C=.45, D=.2)[grade]
    size = min(abs(effect_size or 0) / 1.0, 1)
    parts = dict(effect=round(size, 2), quality=round(quality, 2), stability=grade_w, novelty=novelty)
    score = .35 * size + .2 * quality + .3 * grade_w + .15 * novelty
    return dict(id=f"{cat}:{headline}"[:90], category=cat, headline=headline, finding=finding, compared=compared, n=n, effect=effect,
                uncertainty=uncertainty, grade=grade, confounders=confounders, limitations=limitations, next_q=next_q, viz=viz,
                raw=raw or {}, score=round(score, 3), score_parts=parts, tests=tests)


def coverage_cards(con, d):
    cards = []
    y = d[d.hr_n.notna()].groupby("yr").worn_day.mean()
    if len(y) > 2:
        peak, last = y.idxmax(), y.index[-1]
        if y[peak] - y[last] > .2:
            cards.append(card("Data coverage", f"Your Watch recorded full days far less often in {last} than in {peak}",
                f"Days with at least 14 hours of heart-rate coverage fell from {y[peak]:.0%} in {peak} to {y[last]:.0%} in {last}.",
                f"{peak} against {last}", int(d.hr_n.notna().sum()), f"{(y[last]-y[peak])*100:+.0f} percentage points", "A direct count, so there is no sampling error.",
                "A", ["Charging habits", "A second wearable"],
                ["Totals for active energy and stand time drop on unworn days without any change in behaviour."],
                "Which metrics change when you restrict to worn days only?", dict(kind="bars", x=[str(i) for i in y.index], y=[round(v*100,1) for v in y.values], unit="% of days", title="Days with 14+ hours of Watch coverage"),
                raw=dict(metric="hr_hours"), effect_size=1, quality=1, novelty=.9))
    b = con.execute("""SELECT count(*), avg(a.asleep_h-b.asleep_h), stddev(a.asleep_h-b.asleep_h) FROM sleep_cross a JOIN sleep_cross b USING(wake_date)
                       WHERE a.source='apple watch' AND b.source='autosleep' AND a.asleep_h BETWEEN 3 AND 13""").fetchall()[0]
    if b[0] and b[0] > 50:
        cards.append(card("Data coverage", f"AutoSleep reports about {abs(b[1]):.1f} hours more sleep per day than your Watch",
            f"On {b[0]:,} days with both sources, AutoSleep averaged {abs(b[1]):.2f} h more than Apple's scoring (SD {b[2]:.2f} h). It also logs more sessions a day.",
            "Same calendar days, two sources", b[0], f"{abs(b[1]):.2f} h", f"SD of daily differences {b[2]:.2f} h", "A", ["AutoSleep infers rest periods from stillness"],
            ["Neither source is validated here. The dashboard uses one source per night and never averages them."],
            "Which source better tracks next-day resting heart rate?", dict(kind="note", text="Sleep trends use Watch-scored nights only."), effect_size=.9, quality=1, novelty=.8))
    v = json.loads(con.execute("SELECT value FROM meta WHERE key='report'").fetchall()[0][0]).get("time_validation")
    if v and v.get("n") and v.get("within5_rebuilt") is not None:
        cards.append(card("Data coverage", "Export timestamps ignore where you were, so local time was rebuilt",
            f"Every record carries the export's offset. Rebuilding from HKTimeZone tags brought {v['within5_rebuilt']:.1%} of days within 5% of Apple's own ring total, against {v['within5_naive']:.1%} using export offsets.",
            f"{int(v['n']):,} days checked against ActivitySummary", int(v['n']), f"median error {v['med_abs_err_rebuilt']:.3f} vs {v['med_abs_err_naive']:.2f} kcal", "Direct comparison with Apple's daily totals.", "A",
            [], ["Trip boundaries are accurate to within about a day. Before the first time zone tag, the earliest zone seen is assumed."], "Which days still disagree with Apple's totals?",
            dict(kind="note", text="See Data Quality for the time zone timeline."), effect_size=.8, quality=1, novelty=.9))
    wt = con.execute("""SELECT device, min(first_day), max(last_day), sum(n) FROM device_periods WHERE device LIKE 'Watch%' GROUP BY 1
                          HAVING max(last_day) - min(first_day) < 31 ORDER BY 4 DESC LIMIT 1""").fetchall()
    if wt and con.execute("SELECT count(DISTINCT device) FROM device_periods WHERE device LIKE 'Watch%'").fetchall()[0][0] > 1:
        days = (wt[0][2] - wt[0][1]).days + 1
        cards.append(card("Data coverage", f"A different Watch recorded for only {days} days",
            f"Hardware {wt[0][0]} appears only from {wt[0][1]} to {wt[0][2]}. Metrics that only this model records exist only in that window.",
            "Device hardware field", int(wt[0][3]), "One short device window", "A direct count.", "A", [],
            ["Metrics limited to this window cannot be trended."], "Did any metric step during that window?",
            dict(kind="note", text="Metrics limited to this window are excluded from trends."), effect_size=.5, quality=1, novelty=.8))
    return cards


def trend_cards(con, d, trans):
    cards = []
    for m in TREND_METRICS:
        s = d[m].dropna()
        if len(s) < 200:
            continue
        last = d.index[-1]
        recent, prior = s[s.index > last - pd.Timedelta(days=365)], s[(s.index <= last - pd.Timedelta(days=365)) & (s.index > last - pd.Timedelta(days=730))]
        if len(recent) < 60 or len(prior) < 60:
            continue
        frame = pd.DataFrame({"y": s, "g": (s.index > last - pd.Timedelta(days=365)).astype(float)}, index=s.index)
        frame = frame[frame.index > last - pd.Timedelta(days=730)]
        fit = st.ols_hac(st.calendar_terms(frame)[0], "y", ["g", "_weekend", "_sin", "_cos"]) if len(frame) > 120 else None
        if not fit:
            continue
        t = fit["terms"]["g"]
        g = st.hedges_g(recent.values, prior.values)
        if t["p"] > .01 or abs(g or 0) < .25:
            continue
        hits = near_transition(recent.index[0], trans, m, 120) + near_transition(prior.index[0], trans, m, 120)
        checks = []
        grade = "C" if hits else "B"
        if m == "asleep_watch_h":
            alt = sleep_crosscheck(con, recent.index[0].date(), prior.index[0].date())
            if alt is not None and np.sign(alt) == np.sign(t["b"]) and abs(alt) > .25:
                checks.append(f"AutoSleep, scored separately, moved the same way ({alt:+.2f} h).")
                grade = "A" if not hits else "B"
        pooled = pd.concat([recent, prior])
        cards.append(card("Long-term trends", f"{lab(m)} {adverb(m, t['b'] > 0)} over the past year",
            f"Median {show(recent.median(), m)} in the last 365 days against {show(prior.median(), m)} in the year before. Seasonally adjusted change {delta(t['b'], m)}. " + " ".join(checks),
            f"{prior.index[0].date()} to {prior.index[-1].date()} against {recent.index[0].date()} to {recent.index[-1].date()}", int(len(pooled)),
            f"g = {g:.2f}", f"95% CI {delta(t['lo'], m)} to {delta(t['hi'], m)} (Newey-West)", grade,
            ["Season", "Watch wear", "Illness or travel"], ([f"Overlaps a recording change: {', '.join(hits)}."] if hits else []) + ["Two years cannot separate trend from cycle."],
            f"Does {lname(m)} show the same change on fully worn days?", dict(kind="series", metric=m, freq="M"), raw=dict(metric=m), effect_size=g, quality=.8 if not hits else .5))
    return cards


def regime_cards(d, trans):
    cards = []
    for m in TREND_METRICS:
        for cp in st.change_points(d[m])[:3]:
            rel = abs(cp["delta"]) / max(abs(cp["before"]), 1e-9)
            if abs(cp["g"] or 0) < .8 or (rel < .03 and m not in ("bed_clock_adj",)):  # too small to matter
                continue
            hits = near_transition(cp["date"], trans, m)
            word = {"bed_clock_adj": "moved later" if cp["delta"] > 0 else "moved earlier"}.get(m, "stepped up" if cp["delta"] > 0 else "stepped down")
            cards.append(card("Regime shifts", f"{lab(m)} {word} around {pd.Timestamp(cp['date']).strftime('%b %Y')}",
                f"Weekly median moved from {show(cp['before'], m)} to {show(cp['after'], m)}.", f"{cp['n_before']} weeks before against {cp['n_after']} weeks after", cp["n_before"] + cp["n_after"],
                f"g = {cp['g']:.2f}", "Change point from penalised binary segmentation; date is uncertain by weeks.", "C" if hits else "B",
                ["Life changes not recorded in Health", "Seasonal timing"], ([f"Coincides with: {', '.join(hits)}. This may be a measurement change."] if hits else [f"No recording change affecting {lname(m)} within 45 days."]),
                "Add a life event near this date, then run a before and after analysis.", dict(kind="series", metric=m, freq="W", mark=cp["date"]), raw=dict(metric=m, date=cp["date"]), effect_size=cp["g"] / 4, quality=.5 if hits else .8, novelty=.4))
    return cards


def relationship_family(d):
    rows = []
    for x, y, lag in PAIRS:
        rx, ry = st.residualize(d[x]), st.residualize(d[y]).shift(-lag)
        c = st.corr(rx.values, ry.values)
        rec = dict(x=x, y=y, lag=lag, **c)
        if c["r"] is not None:
            df = pd.DataFrame({"x": rx, "y": ry}).dropna()
            mid = len(df) // 2
            rec["r_early"], rec["r_late"] = df.x[:mid].corr(df.y[:mid]), df.x[mid:].corr(df.y[mid:])
            rec["perm_p"] = st.circular_shift_p(rx.values, ry.values, 499)
        rows.append(rec)
    f = pd.DataFrame(rows)
    f["q"] = st.bh_fdr(f.p.values)
    f["stable"] = np.sign(f.r_early) == np.sign(f.r_late)
    return f


def relationship_cards(f):
    cards = []
    tested = int(f.p.notna().sum())
    for _, r in f[f.p.notna()].sort_values("p").iterrows():
        if r.q > .10 or abs(r.r) < .08:
            continue
        grade = "A" if r.q < .01 and r.stable and abs(r.r) >= .2 and (r.perm_p or 1) < .01 else "B" if r.q < .05 and r.stable else "C"
        when = "the same day" if r.lag == 0 else f"{int(r.lag)} day{'s' if r.lag > 1 else ''} later"
        word = "higher" if r.r > 0 else "lower"
        cards.append(card("Cross-metric relationships" if r.lag == 0 else "Lagged relationships",
            f"{'Later' if r.x == 'bed_clock_adj' else 'Higher'} {lname(r.x)} goes with {word} {lname(r.y)} {when}",
            f"Correlation of detrended, deseasonalised series: r = {r.r:.2f} (Spearman {r.rho:.2f}). Early half {r.r_early:.2f}, late half {r.r_late:.2f}.",
            f"{r.n:,} paired days", int(r.n), f"r = {r.r:.2f}", f"95% CI {r.ci[0]:.2f} to {r.ci[1]:.2f}; q = {r.q:.3f} across {tested} pre-listed tests; effective n {r.neff:.0f}", grade,
            ["Illness", "Travel", "Alcohol or stress, which are not recorded"], ["An association, not a cause.", "The direction of influence is not established by a lag."],
            "Open this pair in the Explorer and add covariates.", dict(kind="scatter", x=r.x, y=r.y, lag=int(r.lag)), raw=dict(x=r.x, y=r.y, lag=int(r.lag)),
            effect_size=abs(r.r) * 2, quality=min(r.n / 1000, 1), tests=tested))
    return cards


def weekly_cards(d):
    cards = []
    for m in ["steps", "asleep_watch_h", "bed_clock_adj", "wake_clock", "rhr", "workout_min", "hrv_sdnn"]:
        f = d[[m]].copy()
        f["we"] = d.weekend.astype(float)
        c = st.compare(f.dropna(), m, "we")
        if "adjusted" not in c:
            continue
        a = c["adjusted"]
        if a["p"] > .001 or abs(c["g"] or 0) < .2:
            continue
        cards.append(card("Seasonality", f"{lab(m)} is different on weekends",
            f"Weekends average {show(c['mean_a'], m)} against {show(c['mean_b'], m)} on weekdays.", "Saturday and Sunday against Monday to Friday", c["n_a"] + c["n_b"],
            f"g = {c['g']:.2f}", f"Adjusted difference {delta(a['b'], m)} (95% CI {delta(a['lo'], m)} to {delta(a['hi'], m)})", "A" if abs(c["g"]) > .4 else "B", ["Work schedule changes across years"],
            ["Pooled across all years; the pattern may differ by year."], "Has this weekend effect changed from year to year?", dict(kind="dow", metric=m), raw=dict(metric=m), effect_size=c["g"], quality=.9))
    for m in ["steps", "asleep_watch_h", "rhr", "hrv_sdnn"]:
        trend = d[m].rolling(365, center=True, min_periods=120).median()
        dev = (d[m] - trend).dropna()
        if len(dev) < 700:
            continue
        mo = dev.groupby(dev.index.month).mean()
        hi, lo = mo.idxmax(), mo.idxmin()
        terms = st.calendar_terms(pd.DataFrame({"y": dev}))
        fit = st.ols_hac(terms[0], "y", ["_sin", "_cos"], 30)
        if not fit or min(fit["terms"]["_sin"]["p"], fit["terms"]["_cos"]["p"]) > .01:
            continue
        names = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
        amp = mo[hi] - mo[lo]
        sd = dev.std()
        cards.append(card("Seasonality", f"{lab(m)} follows an annual cycle, peaking near {names[hi-1]}",
            f"Relative to your rolling yearly level, {names[hi-1]} runs {fmt(mo[hi])} and {names[lo-1]} {fmt(mo[lo])} {unit(m)}.", "Calendar months pooled across years", len(dev), f"peak to trough {fmt(amp)} {unit(m)}",
            f"Harmonic terms p = {min(fit['terms']['_sin']['p'], fit['terms']['_cos']['p']):.1e} with 30-day HAC lags", "B", ["Where you lived changed between years"], ["Few complete years contribute to each month."],
            "Does the pattern repeat in each individual year?", dict(kind="month", metric=m), raw=dict(metric=m), effect_size=amp / sd if sd else 0, quality=.7))
    return cards


def consistency_cards(d):
    cards = []
    for m, nm in [("bed_clock_adj", "Sleep onset timing"), ("wake_clock", "Wake timing"), ("asleep_watch_h", "Sleep duration"), ("steps", "Daily steps")]:
        s = d[m].dropna()
        by = s.groupby(s.index.year).agg(["std", "count"])
        by = by[by["count"] >= 150]
        if len(by) < 3:
            continue
        # clock times keep SD; quantities use CV so a higher level alone cannot read as inconsistency
        clock_metric = m in ("bed_clock_adj", "wake_clock")
        rel = by["std"] if clock_metric else by["std"] / s.groupby(s.index.year).mean().reindex(by.index)
        hi, lo = rel.idxmax(), rel.idxmin()
        if rel[hi] / rel[lo] < 1.25:
            continue
        cards.append(card("Consistency changes", f"{nm} was steadiest in {lo} and most variable in {hi}",
            (f"Standard deviation was {rel[lo]:.2f} h in {lo} and {rel[hi]:.2f} h in {hi}." if clock_metric else f"Coefficient of variation was {rel[lo]:.0%} in {lo} and {rel[hi]:.0%} in {hi}."), "Year by year", int(by['count'].sum()), f"{rel[hi]/rel[lo]:.2f}x spread", "Spread computed from all recorded days in each year.", "B",
            ["Travel and time zone changes", "Different mix of sources"], ["Only years with 150 or more days are compared."], f"Which months drove the spread in {hi}?",
            dict(kind="bars", x=[str(i) for i in rel.index], y=[round(float(v), 3) for v in rel.values], unit="h" if clock_metric else "CV", title=f"Yearly spread of {nm.lower()}"), raw=dict(metric=m), effect_size=(rel[hi]/rel[lo]-1), quality=.7))
    return cards


def running_cards(con, d):
    r = con.execute("""SELECT local_date, start_local, pace_min_mi, avg_hr, distance_mi, elev_ft, temp_f, duration_min, source_name FROM workout_facts
                       WHERE type='Running' AND distance_mi>=1 AND pace_min_mi BETWEEN 5 AND 16 AND avg_hr IS NOT NULL AND NOT coalesce(indoor,false)""").df()
    cards = []
    if len(r) < 40:
        return cards
    r["t"] = (pd.to_datetime(r.local_date) - pd.Timestamp(r.local_date.min())).dt.days / 365.25
    r = r.sort_values("start_local").reset_index(drop=True)
    fit = st.ols_hac(r, "pace_min_mi", ["t", "avg_hr", "distance_mi"], 5)
    raw = st.ols_hac(r, "pace_min_mi", ["t"], 5)
    if fit and raw:
        t, t0 = fit["terms"]["t"], raw["terms"]["t"]
        better = t["b"] < 0
        sig = t["p"] < .05
        head = f"Comparable-effort pace {'improved' if better else 'slowed'} over time" if sig else "No clear change in pace at comparable effort"
        cards.append(card("Long-term trends", head,
            f"Holding heart rate and distance fixed, pace changed {t['b']*60:+.1f} s/mi per year. Without adjustment it changed {t0['b']*60:+.1f} s/mi per year.",
            f"{len(r)} outdoor runs, {r.local_date.min()} to {r.local_date.max()}", len(r), f"{t['b']*60:+.1f} s/mi per year", f"95% CI {t['lo']*60:+.1f} to {t['hi']*60:+.1f} s/mi per year", "B" if sig else "C",
            ["Terrain", "Heat", "Run type", "Pauses included in elapsed time"], ["Elevation and temperature cover only part of the record, so they are not in this model."], "Does the trend hold for runs of 3 to 5 miles only?",
            dict(kind="runs"), raw=dict(view="running"), effect_size=abs(t["b"]*60)/20, quality=.7))
    f = pd.DataFrame({"y": d.rhr, "g": np.where(d.run_mi.shift(1) >= 5, 1.0, np.where(d.run_mi.shift(1) == 0, 0.0, np.nan))}, index=d.index)
    f["base"] = d.rhr.shift(2).rolling(14, min_periods=5).median()
    c = st.compare(f.dropna(subset=["y", "g"]), "y", "g", ["base"])
    if c.get("adjusted") and c["n_a"] >= 15:
        a = c["adjusted"]
        cards.append(card("Recovery patterns", f"Resting heart rate the day after a 5+ mile run is {'higher' if a['b']>0 else 'lower'} than after no run" if a["p"] < .05 else "Resting heart rate barely changes after 5+ mile runs",
            f"Adjusted difference {a['b']:+.2f} bpm, controlling for the prior 14-day baseline and calendar.", f"{c['n_a']} days after long runs against {c['n_b']} days after no run", c["n_a"] + c["n_b"], f"{a['b']:+.2f} bpm", f"95% CI {a['lo']:+.2f} to {a['hi']:+.2f} bpm", "B" if a["p"] < .05 else "C",
            ["Long runs may fall on days you feel well", "Day-of-week of long runs"], ["Observational comparison."], "Does the effect last to the second day?", dict(kind="note", text="Try this in the Lab with lag 2."), raw=dict(metric="rhr"), effect_size=abs(a["b"]) / 3, quality=.7))
    return cards


def anomaly_cards(d):
    z = pd.DataFrame({"rhr": st.rolling_robust_z(d.rhr), "hrv": -st.rolling_robust_z(d.hrv_sdnn), "resp": st.rolling_robust_z(d.resp_rate), "sleep_hr": st.rolling_robust_z(d.sleep_hr)})
    score = z.clip(-6, 8).mean(axis=1).where(z.notna().sum(axis=1) >= 3)
    top = score.dropna().sort_values(ascending=False)
    picks = []
    for t, v in top.items():
        if all(abs((t - p).days) > 7 for p, _ in picks):
            picks.append((t, v))
        if len(picks) == 5:
            break
    cards = []
    for t, v in picks:
        row = d.loc[t]
        cards.append(card("Anomalies", f"{t.strftime('%b %-d, %Y')} stands out physiologically",
            f"Resting HR {fmt(row.rhr)} bpm, HRV {fmt(row.hrv_sdnn)} ms, respiratory rate {fmt(row.resp_rate)}. Composite deviation {v:.1f} MADs from the prior 90 days.", "That day against the trailing 90 days", 90,
            f"{v:.1f} MAD", "Robust z-score; several tests were screened, so expect some false alarms.", "C", ["Illness", "Alcohol", "Travel", "Device fit"], [NON_CLINICAL], "What did the days around it look like?",
            dict(kind="series", metric="rhr", freq="D", window=[str((t-pd.Timedelta(days=45)).date()), str((t+pd.Timedelta(days=45)).date())], mark=str(t.date())), raw=dict(day=str(t.date())), effect_size=v / 6, quality=.6, novelty=.8))
    return cards


def extreme_cards(d):
    cards = []
    w = d.steps.resample("W-SUN").sum(min_count=7)
    t = w.idxmax()
    cards.append(card("Personal extremes", f"Your biggest recorded week of walking ended {t.strftime('%b %-d, %Y')}", f"{w.max():,.0f} steps in seven days.", "All complete weeks", int(w.notna().sum()), f"{w.max()/w.median():.1f}x median week", "Exact count after source deduplication.", "A", [], ["A record within the observed data."], "What else changed that week?",
                      dict(kind="series", metric="steps", freq="D", window=[str((t-pd.Timedelta(days=30)).date()), str((t+pd.Timedelta(days=30)).date())]), raw=dict(day=str(t.date())), effect_size=.4, quality=.9))
    return cards


def run(con=None, d=None):
    con = con or connect()
    d = load_daily(con) if d is None else d
    trans = transitions(con, d)
    f = relationship_family(d)
    cards = coverage_cards(con, d) + trend_cards(con, d, trans) + regime_cards(d, trans) + relationship_cards(f) + weekly_cards(d) + consistency_cards(d) + running_cards(con, d) + anomaly_cards(d) + extreme_cards(d)
    from . import deep
    deep_data, deep_cards = deep.run(con, d)
    cards += deep_cards
    seen, uniq = set(), []
    for c in sorted(cards, key=lambda c: -c["score"]):
        if c["id"] not in seen:
            seen.add(c["id"]), uniq.append(c)
    family = [json_safe({k: v for k, v in r.items()}) for r in f.to_dict("records")]
    out = dict(cards=[json_safe(c) for c in uniq], family=family, n_tested=int(f.p.notna().sum()), n_surviving=int((f.q <= .10).sum()), deep=json_safe(deep_data), transitions=[[str(a.date()), b] for a, b, _ in trans])
    (ROOT / "insights.json").write_text(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    o = run()
    print(o["n_tested"], "tested,", o["n_surviving"], "with q<=.10,", len(o["cards"]), "cards")
    for c in o["cards"]:
        print(f"[{c['grade']}] {c['score']:.2f} {c['category'][:14]:14} {c['headline']}\n      {c['finding'][:150]}  ({c['uncertainty'][:80]})")
