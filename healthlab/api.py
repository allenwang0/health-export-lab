"""Local API. Binds to 127.0.0.1 only; rejects foreign Host and Origin headers so a web
page cannot read health data through the browser. No access logging, so no dates or
metrics reach any log."""
import json
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import build as builder, insights, lab, parse
from .catalog import METRICS
from .data import ROOT, connect, json_safe, load_daily
from . import stats as st

WEB = Path(__file__).resolve().parent.parent / "web"
STATE = ROOT / "state.json"
ALLOWED = {"127.0.0.1", "localhost"}
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_cache = {}


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.headers.get("host", "").rsplit(":", 1)[0]
    origin = re.sub(r"^https?://|:\d+$", "", request.headers.get("origin", "")) or host
    if host not in ALLOWED or origin not in ALLOWED:
        return PlainTextResponse("Forbidden", 403)
    return await call_next(request)


def con():
    if not (ROOT / "lab.duckdb").exists():
        raise HTTPException(404, "No data imported.")
    return connect()


def daily():
    if "d" not in _cache:
        c = con()
        _cache["d"] = load_daily(c)
        c.close()
    return _cache["d"]


def q(sql, *args):
    c = con()
    try:
        return c.execute(sql, list(args)).df()
    finally:
        c.close()


def rows(df):
    return json_safe(json.loads(df.to_json(orient="records", date_format="iso")))


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else dict(annotations=[], notes={}, experiments=[], cohorts=[], dismissed=[])


@app.get("/api/boot")
def boot():
    if not (ROOT / "lab.duckdb").exists():
        return dict(empty=True)
    d = daily()
    cols = [c for c in d.columns if c not in ("tz_conf",)]
    data = {c: [None if pd.isna(v) else (bool(v) if isinstance(v, (bool, np.bool_)) else (v if isinstance(v, str) else round(float(v), 3))) for v in d[c]] for c in cols}
    report = json.loads(q("SELECT value FROM meta WHERE key='report'").value[0])
    avail = {m: int(d[m].notna().sum()) for m in METRICS if m in d}
    return dict(dates=[str(x.date()) for x in d.index], data=data, metrics=METRICS, available=avail, report=report,
                devices=rows(q("SELECT device, source, first_day::VARCHAR first_day, last_day::VARCHAR last_day, n FROM device_periods ORDER BY first_day")))


@app.get("/api/quality")
def quality():
    inv = rows(q("""SELECT type, unit, n_units, n, first_day::VARCHAR first_day, last_day::VARCHAR last_day, n_days, n_sources, sources, min_value, max_value, median_value, n_numeric FROM type_inventory"""))
    dup = rows(q("""SELECT type, count(*) dup_groups, sum(c-1) extra_rows FROM (SELECT type, source_name, start_utc, end_utc, count(*) c, count(DISTINCT value_raw) v FROM samples GROUP BY 1,2,3,4 HAVING c>1) GROUP BY 1 ORDER BY 3 DESC LIMIT 15"""))
    # plausibility limits are broad on purpose: they flag values to inspect, they do not delete them
    limits = dict(HeartRate=(25, 230), RestingHeartRate=(30, 120), BodyMass=(60, 400), VO2Max=(15, 90), RespiratoryRate=(5, 40), HeartRateVariabilitySDNN=(3, 300))
    outliers = []
    for t, (lo, hi) in limits.items():
        r = q("SELECT count(*) n, min(value) mn, max(value) mx FROM samples WHERE type=? AND (value<? OR value>?)", t, lo, hi).iloc[0]
        if r.n:
            outliers.append(dict(type=t, n=int(r.n), low=lo, high=hi, min=float(r.mn), max=float(r.mx)))
    d = daily()
    gaps = []
    for col, name in [("steps", "Any steps"), ("hr_n", "Heart rate"), ("asleep_h", "Sleep")]:
        s = d[col].notna()
        grp = (s != s.shift()).cumsum()
        for _, g in s[~s].groupby(grp):
            if len(g) >= 7 and g.index[0] > d[col].first_valid_index():
                gaps.append(dict(series=name, start=str(g.index[0].date()), end=str(g.index[-1].date()), days=len(g)))
    return dict(inventory=inv, duplicates=dup, outliers=outliers, gaps=sorted(gaps, key=lambda x: x["start"]),
                dedupe=rows(q("SELECT * FROM dedupe_report")),
                tz=rows(q("SELECT start_utc::DATE::VARCHAR a, end_utc::DATE::VARCHAR b, tz, conf, round(n_days,1) n_days FROM tz_segments ORDER BY start_utc")),
                sources=rows(q("SELECT year(wake_date) yr, source, count(*) n FROM sleep_nights GROUP BY 1,2 ORDER BY 1,2")),
                wear=rows(q("SELECT yr, count(*) FILTER (hr_n IS NOT NULL) hr_days, avg(CASE WHEN worn_day THEN 1.0 ELSE 0 END) FILTER (hr_n IS NOT NULL) worn FROM daily GROUP BY 1 ORDER BY 1")),
                absent=[t for t in ["Blood pressure", "Blood oxygen", "Blood glucose", "Nutrition", "Clinical records", "Correlations", "Menstrual cycle", "Symptoms", "Medications"]],
                ingest={k: v for k, v in json.loads(q("SELECT value FROM meta WHERE key='report'").value[0]).items() if k != "files"})


@app.get("/api/raw")
def raw(type: str, day: str, limit: int = 500):
    return rows(q("""SELECT start_local::VARCHAR start_local, (end_utc-start_utc)::VARCHAR dur, value_raw, unit, replace(source_name,chr(160),' ') source, device_name, hardware, tz, metadata
                     FROM samples WHERE type=? AND local_date=?::DATE ORDER BY start_utc LIMIT ?""", type, day, min(limit, 5000)))


@app.get("/api/insights")
def get_insights():
    f = ROOT / "insights.json"
    if not f.exists():
        return recompute()
    o = json.loads(f.read_text())
    gone = set(state()["dismissed"])
    o["cards"] = [c for c in o["cards"] if c["id"] not in gone]
    o["dismissed"] = len(gone)
    return o


@app.post("/api/insights/recompute")
def recompute():
    c = con()
    try:
        return insights.run(c, daily())
    finally:
        c.close()


@app.get("/api/day/{day}")
def day(day: str):
    d = daily()
    t = pd.Timestamp(day)
    if t not in d.index:
        raise HTTPException(404, "Day is outside the recorded range.")
    r = d.loc[t]
    base = d[(d.index < t) & (d.index >= t - pd.Timedelta(days=90))]
    cmp_rows, unusual = [], []
    for m in ["steps", "distance_mi", "flights", "active_kcal", "rhr", "hr_mean", "hr_max", "hrv_sdnn", "asleep_h", "deep_h", "rem_h", "resp_rate", "workout_min", "vo2max", "weight_lb"]:
        v = r.get(m)
        if pd.isna(v):
            continue
        b = base[m].dropna()
        med, mad = (b.median(), (b - b.median()).abs().median() * 1.4826) if len(b) >= 14 else (None, None)
        z = (v - med) / mad if mad else None
        cmp_rows.append(dict(metric=m, label=METRICS[m]["label"].replace(" (all days)", "").replace(" (best source)", ""), unit=METRICS[m]["unit"], value=float(v), baseline=med, z=z, kind=METRICS[m]["kind"]))
        if z is not None and abs(z) >= 2.5 and m not in ("workout_min",):
            unusual.append(f"{METRICS[m]['label'].replace(' (all days)', '').replace(' (best source)', '')} {'above' if z > 0 else 'below'} your usual range: {v:,.1f} against a median of {med:,.1f} {METRICS[m]['unit']}")
    hr = q("""SELECT start_local::VARCHAR t, value v, metadata->>'$.HKMetadataKeyHeartRateMotionContext' ctx FROM samples WHERE type='HeartRate' AND local_date=?::DATE ORDER BY start_utc""", day)
    hours = q("SELECT local_hour h, metric, value v FROM hourly WHERE local_date=?::DATE AND metric IN ('steps','active_kcal') ORDER BY 1", day)
    stages = q("""SELECT stage, (start + to_minutes(start_offset))::VARCHAR s, ("end" + to_minutes(start_offset))::VARCHAR e, source FROM sleep_stages WHERE wake_date=?::DATE ORDER BY start""", day)
    night = q("SELECT * EXCLUDE (start_utc,end_utc) FROM sleep_nights WHERE wake_date=?::DATE", day)
    hrv = q("SELECT start_local::VARCHAR t, value v FROM samples WHERE type='HeartRateVariabilitySDNN' AND local_date=?::DATE ORDER BY start_utc", day)
    w = q("SELECT workout_id, type, start_local::VARCHAR start_local, duration_min, distance_mi, avg_hr, kcal FROM workout_facts WHERE local_date=?::DATE ORDER BY start_utc", day)
    parts = [f"{t.strftime('%A, %B %-d, %Y')}, recorded in {r.tz}."]
    if not pd.isna(r.steps):
        parts.append(f"{r.steps:,.0f} steps" + (f", against a 90-day median of {base.steps.median():,.0f}." if base.steps.notna().sum() >= 14 else "."))
    if not pd.isna(r.asleep_h):
        parts.append(f"{r.asleep_h:.1f} hours of sleep scored by {r.sleep_source}.")
    if not r.get("worn_day"):
        parts.append("The Watch recorded fewer than 14 hours, so day totals may be incomplete.")
    if not unusual and len(base):
        parts.append("Nothing fell outside your usual 90-day range.")
    return json_safe(dict(date=day, summary=" ".join(parts), compare=cmp_rows, unusual=unusual, hr=rows(hr), hours=rows(hours), stages=rows(stages), night=rows(night.astype(str)), hrv=rows(hrv), workouts=rows(w),
                          coverage=dict(hr_samples=None if pd.isna(r.hr_n) else int(r.hr_n), hr_hours=None if pd.isna(r.hr_hours) else int(r.hr_hours), step_hours=None if pd.isna(r.step_hours) else int(r.step_hours), tz=r.tz, tz_conf=r.tz_conf), note=state()["notes"].get(day, "")))


@app.get("/api/rhythm")
def rhythm(start: str = "1900-01-01", end: str = "2100-01-01", metric: str = "steps"):
    a = q("SELECT isodow(local_date) dow, local_hour h, avg(value) v FROM hourly WHERE metric=? AND local_date BETWEEN ?::DATE AND ?::DATE GROUP BY 1,2", metric, start, end)
    b = q("SELECT year(local_date) y, week(local_date) w, sum(value) v, count(DISTINCT local_date) n FROM hourly WHERE metric=? AND local_date BETWEEN ?::DATE AND ?::DATE GROUP BY 1,2", metric, start, end)
    c = q("SELECT year(local_date) y, local_hour h, sum(value)/count(DISTINCT local_date) v FROM hourly WHERE metric=? AND local_date BETWEEN ?::DATE AND ?::DATE GROUP BY 1,2", metric, start, end)
    wk = q("SELECT hour(start_local) h, type, count(*) n FROM workout_facts WHERE local_date BETWEEN ?::DATE AND ?::DATE GROUP BY 1,2", start, end)
    return dict(hour_dow=rows(a), year_week=rows(b), year_hour=rows(c), workouts=rows(wk))


@app.get("/api/archetypes")
def archetypes():
    d = daily()
    cols = ["steps", "asleep_watch_h", "workout_min", "rhr", "flights"]
    c = st.cluster_days(d[d.worn_day], cols)
    if not c:
        return dict(error="Too few complete days.")
    raw = d.loc[c["labels"].index, cols].groupby(c["labels"]).median()
    names = {}
    for k, row in raw.iterrows():
        z = c["centers"].loc[k]
        top = z.abs().idxmax()
        word = {"steps": "walking", "asleep_watch_h": "sleep", "workout_min": "workout", "rhr": "resting HR", "flights": "stairs"}[top]
        names[int(k)] = f"{'High' if z[top] > 0 else 'Low'} {word} days"
    by = c["labels"].groupby(c["labels"].index.year).value_counts().unstack(fill_value=0)
    return json_safe(dict(k=c["k"], silhouette=c["silhouette"], stability=c["stability_ari"], columns=cols, names=names, centers=json.loads(raw.reset_index(drop=True).to_json(orient="records")), sizes=c["labels"].value_counts().to_dict(),
                          by_year=dict(years=[int(y) for y in by.index], series={int(k): [int(v) for v in by[k]] for k in by.columns}),
                          caution="Silhouette below 0.25 means clusters are weakly separated: these are convenient labels, not natural types."))


@app.get("/api/workouts")
def workouts():
    return rows(q("""SELECT workout_id, type, replace(source_name,chr(160),' ') source, local_date::VARCHAR date, start_local::VARCHAR start_local, tz, duration_min, distance_mi, pace_min_mi, avg_hr, max_hr, hr_basis, kcal, elev_ft, temp_f, humidity_pct, cadence, power, stride, gct, vosc, mets, brand, indoor, route_path IS NOT NULL has_route FROM workout_facts ORDER BY start_utc DESC"""))


@app.get("/api/workout/{wid}")
def workout(wid: str):
    w = q("SELECT * EXCLUDE (metadata_json), start_local::VARCHAR s FROM workout_facts WHERE workout_id=?", wid)
    if w.empty:
        raise HTTPException(404, "Unknown workout.")
    a, b, path = w.start_utc[0], w.end_utc[0], w.route_path[0]
    hr = q("SELECT (start_local)::VARCHAR t, value v FROM samples WHERE type='HeartRate' AND start_utc BETWEEN ? AND ? ORDER BY start_utc", a, b)
    ev = q("SELECT type, count(*) n FROM workout_events WHERE workout_id=? GROUP BY 1", wid)
    pts = pd.DataFrame()
    if path is not None and not pd.isna(path):
        pts = q("SELECT lat, lon, ele, speed FROM route_points WHERE route_path=? ORDER BY ts_utc", path)
        pts = pts.iloc[:: max(1, len(pts) // 800)]
    return json_safe(dict(workout=json.loads(w.drop(columns=["start_utc", "end_utc", "start_local", "local_date"]).to_json(orient="records"))[0], stats=rows(q("SELECT * FROM workout_stats WHERE workout_id=?", wid)), hr=rows(hr), events=rows(ev), route=rows(pts)))


@app.get("/api/running")
def running():
    w = q("""SELECT workout_id, local_date::VARCHAR date, start_local::VARCHAR start_local, distance_mi, duration_min, pace_min_mi, avg_hr, max_hr, elev_ft, temp_f, cadence, power, stride, gct, vosc, kcal, indoor, replace(source_name,chr(160),' ') source FROM workout_facts WHERE type='Running' ORDER BY start_utc""")
    d = daily()
    r = d.run_mi.dropna()
    wk = r.resample("W-SUN").sum()
    acute, chronic = r.rolling(7).sum(), r.rolling(28).sum() / 4
    # monotony and strain use Foster's definitions on distance; they describe load, not injury risk
    roll_mean, roll_sd = r.rolling(7).mean(), r.rolling(7).std()
    monotony = (roll_mean / roll_sd.where(roll_sd > 0))
    load = pd.DataFrame({"acute": acute, "chronic": chronic, "acwr": acute / chronic.where(chronic > 1), "monotony": monotony, "strain": monotony * acute, "r90": r.rolling(90).sum()}).iloc[::7].dropna(subset=["acute"])
    hrm = float(q("SELECT quantile_cont(value,0.999) v FROM samples WHERE type='HeartRate'").v[0])
    z = q("""SELECT year(start_local) y, CASE WHEN value<?*.6 THEN 'Z1' WHEN value<?*.7 THEN 'Z2' WHEN value<?*.8 THEN 'Z3' WHEN value<?*.9 THEN 'Z4' ELSE 'Z5' END z, count(*) n
             FROM samples s JOIN (SELECT start_utc a, end_utc b FROM workout_facts WHERE type='Running') w ON s.start_utc BETWEEN w.a AND w.b WHERE s.type='HeartRate' GROUP BY 1,2""", hrm, hrm, hrm, hrm)
    gaps = pd.to_datetime(w.date).drop_duplicates().diff().dt.days.dropna()
    run_days = sorted(set(w.date))
    best, cur = 1, 1
    for x, y in zip(run_days, run_days[1:]):
        cur = cur + 1 if (pd.Timestamp(y) - pd.Timestamp(x)).days == 1 else 1
        best = max(best, cur)
    return json_safe(dict(runs=rows(w), weekly=dict(t=[str(x.date()) for x in wk.index], v=[round(float(v), 2) for v in wk.values]), load=dict(t=[str(x.date()) for x in load.index], **{c: [None if pd.isna(v) else round(float(v), 2) for v in load[c]] for c in load.columns}),
                          zones=rows(z), hr_ref=hrm, summary=dict(n=len(w), miles=float(w.distance_mi.sum()), hours=float(w.duration_min.sum() / 60), med_dist=float(w.distance_mi.median()), med_pace=float(w.pace_min_mi[w.pace_min_mi.between(4, 20)].median()), longest=float(w.distance_mi.max()), med_rest=float(gaps.median()), streak=best)))


@app.get("/api/sleep")
def sleep():
    n = q("""SELECT wake_date::VARCHAR date, source, kind, onset_local::VARCHAR onset, wake_local::VARCHAR wake, span_h, asleep_h, in_bed_h, core_h, deep_h, rem_h, awake_h, efficiency, wake_events, tz_offset_min FROM sleep_nights ORDER BY wake_date""")
    d = daily()
    s = d.asleep_watch_h
    wk = d[d.weekend == False]  # noqa: E712
    we = d[d.weekend == True]  # noqa: E712
    target = s.rolling(365, min_periods=60).median()
    debt = (target - s).clip(lower=0).rolling(14, min_periods=7).sum()
    reg = pd.DataFrame({"sri": d.bed_clock_adj.rolling(28, min_periods=10).std(), "dur_sd": s.rolling(28, min_periods=10).std(), "debt": debt}).iloc[::7]
    sj = wk.groupby(wk.yr).apply(lambda g: (g.bed_clock_adj + g.asleep_h / 2).median()) - 0
    sj2 = we.groupby(we.yr).apply(lambda g: (g.bed_clock_adj + g.asleep_h / 2).median())
    after = pd.DataFrame({"short": s.shift(1) < 6, "two": (s.shift(1) < 6) & (s.shift(2) < 6), "rhr": d.rhr, "hrv": d.hrv_sdnn, "steps": d.steps, "next": s})
    def grp(m):
        return [dict(metric=k, value=float(after[m & after.rhr.notna()][k].mean()), n=int((m & after[k].notna()).sum())) for k in ("rhr", "hrv", "steps", "next")]
    return json_safe(dict(nights=rows(n), regularity=dict(t=[str(x.date()) for x in reg.index], bed_sd=[None if pd.isna(v) else round(float(v), 2) for v in reg.sri], dur_sd=[None if pd.isna(v) else round(float(v), 2) for v in reg.dur_sd], debt=[None if pd.isna(v) else round(float(v), 1) for v in reg.debt]),
                          social_jetlag=[dict(yr=int(y), weekday=float(sj[y]), weekend=float(sj2[y]), lag_h=float(sj2[y] - sj[y])) for y in sj.index if y in sj2.index and not pd.isna(sj[y]) and not pd.isna(sj2[y])],
                          naps=int(q("SELECT count(*) n FROM sleep_naps").n[0]), short=dict(one=grp(after.short & ~after.two), two=grp(after.two), normal=grp(s.shift(1) >= 7))))


def explore_pack(d, x, y, lag, covs, start=None, end=None):
    f = d[(d.index >= (start or d.index[0])) & (d.index <= (end or d.index[-1]))]
    a, b = f[x], f[y].shift(-lag)
    raw = st.corr(a.values, b.values)
    rx, ry = st.residualize(f[x]), st.residualize(f[y]).shift(-lag)
    adj = st.corr(rx.values, ry.values)
    adj["perm_p"] = st.circular_shift_p(rx.values, ry.values, 499)
    t = pd.DataFrame({"x": a, "y": b}).dropna()
    t2, cal = st.calendar_terms(pd.DataFrame({"x": a, "y": b, **{c: f[c] for c in covs}}, index=f.index).dropna(subset=["x", "y"]))
    fit = st.ols_hac(t2, "y", ["x"] + cal + covs)
    slope = np.polyfit(t.x, t.y, 1).tolist() if len(t) > 3 else None
    ci = st.block_bootstrap(lambda u, v: np.polyfit(u, v, 1)[0] if np.std(u) else np.nan, [t.x.values, t.y.values], n=500)
    qs = t.groupby(pd.qcut(t.x, 5, duplicates="drop"), observed=True).agg(x=("x", "median"), y=("y", "mean"), n=("y", "size"), sd=("y", "std")).reset_index(drop=True) if len(t) >= 50 else pd.DataFrame()
    roll = st.rolling_corr(f[x], f[y].shift(-lag), 180)
    sample = t.iloc[:: max(1, len(t) // 1500)]
    n_total = len(f)
    plain = (f"Across {raw['n']:,} paired days, r = {raw['r']:.2f}. After removing trend and calendar effects r = {adj['r']:.2f} (95% CI {adj['ci'][0]:.2f} to {adj['ci'][1]:.2f}, effective n {adj['neff']:.0f}). "
             + ("There is no clear relationship after adjustment. " if adj["p"] and adj["p"] > .05 else "") + "This is an association, not evidence of cause." ) if raw.get("r") is not None and adj.get("r") is not None else "Too few paired days."
    return json_safe(dict(x=x, y=y, lag=lag, raw=raw, adjusted=adj, ols=fit, slope=slope, slope_ci=ci, quintiles=rows(qs), lag_curve=st.lag_curve(f[x], f[y], 7), rolling=dict(t=[str(i.date()) for i in roll.index], r=[round(float(v), 3) for v in roll.values]),
                          points=[[round(float(u), 3), round(float(v), 3), str(i.date())] for i, u, v in zip(sample.index, sample.x, sample.y)], disclosure=dict(days_in_range=n_total, paired=raw["n"], missing=n_total - raw["n"]), plain=plain))


@app.post("/api/explore")
def explore(b: dict = Body(...)):
    d = daily()
    x, y = b["x"], b["y"]
    if x not in d or y not in d:
        raise HTTPException(400, "Unknown metric.")
    return explore_pack(d, x, y, int(b.get("lag", 0)), [c for c in b.get("covariates", []) if c in d], b.get("start"), b.get("end"))


@app.get("/api/matrix")
def matrix():
    d = daily()
    cols = ["steps", "active_kcal_worn", "run_mi", "workout_min", "rhr", "hrv_sdnn", "sleep_hr", "vo2max", "resp_rate", "asleep_watch_h", "deep_h", "rem_h", "bed_clock_adj", "walk_speed_mph"]
    r = pd.DataFrame({c: st.residualize(d[c]) for c in cols}).corr(min_periods=100)
    return dict(columns=cols, labels=[METRICS[c]["label"] for c in cols], r=[[None if pd.isna(v) else round(float(v), 3) for v in row] for row in r.values])


@app.post("/api/experiment")
def experiment(b: dict = Body(...)):
    spec = b
    if b.get("text"):
        spec = lab.parse_hypothesis(b["text"])
        if not spec:
            return dict(error="I could not translate that into a comparison. Try: resting heart rate on days following runs longer than 5 miles against days following no running.")
    return lab.run_experiment(daily(), spec)


@app.post("/api/cohort")
def cohort(b: dict = Body(...)):
    return lab.compare_cohorts(daily(), b.get("a", []), b.get("b", []), b.get("metrics") or ["steps", "rhr", "hrv_sdnn", "asleep_watch_h", "deep_h", "workout_min", "resp_rate"])


@app.get("/api/records")
def records():
    d = daily()
    out = []

    def rec(label, value, when, caveat, day=None):
        out.append(dict(label=label, value=value, when=str(when), caveat=caveat, day=day))
    t = d.steps.idxmax()
    rec("Most steps in a day", f"{d.steps.max():,.0f}", t.date(), f"Source: deduplicated steps. Watch coverage that day {0 if pd.isna(d.hr_hours[t]) else int(d.hr_hours[t])} hours.", str(t.date()))
    mo = d.steps.resample("MS").sum(min_count=25)
    rec("Most steps in a month", f"{mo.max():,.0f}", mo.idxmax().strftime("%B %Y"), "Months with at least 25 days of step data.")
    lo = d.steps[d.steps > 0].idxmin()
    rec("Fewest steps in a day", f"{d.steps[d.steps > 0].min():,.0f}", lo.date(), "Very low values usually mean the phone was left behind, not that you did not move.", str(lo.date()))
    w = q("SELECT local_date::VARCHAR d, distance_mi, pace_min_mi, duration_min, source_name s FROM workout_facts WHERE type='Running' AND distance_mi IS NOT NULL")
    if len(w):
        i = w.distance_mi.idxmax()
        rec("Longest run", f"{w.distance_mi[i]:.2f} mi", w.d[i], f"Source: {w.s[i].replace(chr(160), ' ')}.", w.d[i])
        for lo_d, hi_d, nm in [(3, 3.5, "3 mile"), (5, 5.6, "5 mile"), (6.2, 7, "10K")]:
            c = w[w.distance_mi.between(lo_d, hi_d)]
            if len(c):
                j = c.pace_min_mi.idxmin()
                rec(f"Fastest {nm} band ({lo_d} to {hi_d} mi)", f"{int(c.pace_min_mi[j])}:{int((c.pace_min_mi[j] % 1) * 60):02d} /mi", c.d[j], f"Fastest of {len(c)} runs in this band, by elapsed pace. Not a race result; GPS distance error applies.", c.d[j])
    wk = d.run_mi.resample("W-SUN").sum(min_count=1)
    if wk.notna().any():
        rec("Highest running week", f"{wk.max():.1f} mi", f"week ending {wk.idxmax().date()}", "Recorded runs only.")
    n = d[d.sleep_source.fillna("").str.startswith("Watch")]
    if len(n):
        a, b = n.asleep_h.idxmax(), n[n.hr_hours >= 14].asleep_h.idxmin()
        rec("Longest sleep (Watch)", f"{n.asleep_h.max():.1f} h", a.date(), "Watch-scored nights only.", str(a.date()))
        rec("Shortest adequately recorded sleep", f"{n[n.hr_hours >= 14].asleep_h.min():.1f} h", b.date(), "A Watch-scored night on a day with at least 14 hours of wear.", str(b.date()))
    r30 = d.rhr.rolling(30, min_periods=20).mean()
    t = r30.idxmin()
    rec("Lowest 30-day resting heart rate", f"{r30.min():.1f} bpm", f"30 days ending {t.date()}", "Device estimate.")
    v = d.vo2max.dropna()
    if len(v):
        s = v.rolling(90, min_periods=3).median()
        rec("Peak VO2 max estimate (90-day median)", f"{s.max():.1f}", s.idxmax().date(), "Device-estimated, and only updated on qualifying outdoor walks and runs.")
    cv = d.workouts.resample("MS").agg(lambda s: (s > 0).sum())
    rec("Most workout days in a month", f"{int(cv.max())} days", cv.idxmax().strftime("%B %Y"), "Logged workouts only.")
    return dict(records=out, note="These are records within the data recorded here. They are not necessarily lifetime bests, because nothing outside the export is known.")


def period_frame(kind, key):
    d = daily()
    if kind == "week":
        a = pd.Timestamp(key)
        a = a - pd.Timedelta(days=a.dayofweek)
        b, p = a + pd.Timedelta(days=6), (a - pd.Timedelta(days=7), a - pd.Timedelta(days=1))
    elif kind == "month":
        a = pd.Timestamp(key + "-01" if len(key) == 7 else key).replace(day=1)
        b, p = a + pd.offsets.MonthEnd(0), ((a - pd.DateOffset(months=1)), a - pd.Timedelta(days=1))
    else:
        a = pd.Timestamp(f"{key[:4]}-01-01")
        b, p = a + pd.offsets.YearEnd(0), (a - pd.DateOffset(years=1), a - pd.Timedelta(days=1))
    return d[a:b], d[p[0]:p[1]], a, b


@app.get("/api/report/{kind}/{key}")
def report(kind: str, key: str):
    if kind not in ("week", "month", "year"):
        raise HTTPException(400, "kind must be week, month or year")
    cur, prev, a, b = period_frame(kind, key)
    d = daily()
    if cur.empty:
        raise HTTPException(404, "No data in that period.")
    rows_, sums = [], ["steps", "distance_mi", "flights", "run_mi", "runs", "workouts", "workout_min"]
    for m in sums + ["rhr", "hrv_sdnn", "vo2max", "asleep_watch_h", "deep_h", "rem_h", "resp_rate", "bed_clock_adj", "wake_clock"]:
        f = np.nansum if m in sums else np.nanmean
        c, p = cur[m], prev[m]
        if c.notna().sum() == 0:
            continue
        rows_.append(dict(metric=m, label=METRICS[m]["label"], unit=METRICS[m]["unit"], kind=METRICS[m]["kind"], cur=float(f(c)), prev=None if p.notna().sum() == 0 else float(f(p)), n=int(c.notna().sum()), sum=m in sums))
    base = d[d.index < a]
    z = pd.DataFrame({m: (cur[m] - base[m].tail(365).median()) / ((base[m].tail(365) - base[m].tail(365).median()).abs().median() * 1.4826 or np.nan) for m in ["steps", "rhr", "hrv_sdnn", "asleep_watch_h"]})
    odd = None
    if z.notna().any().any():
        st_ = z.abs().stack()
        (day_, m) = st_.idxmax()
        odd = dict(day=str(day_.date()), metric=METRICS[m]["label"], z=float(z.loc[day_, m]), value=float(cur.loc[day_, m]))
    extra = {}
    if kind in ("month", "year"):
        mo = cur.resample("W-SUN" if kind == "month" else "MS").agg(dict(steps="sum", run_mi="sum", asleep_watch_h="mean", rhr="mean"))
        extra["series"] = dict(t=[str(x.date()) for x in mo.index], steps=mo.steps.round(0).tolist(), run_mi=mo.run_mi.round(1).tolist(), sleep=mo.asleep_watch_h.round(2).tolist(), rhr=mo.rhr.round(1).tolist())
    if kind == "year":
        top = cur.steps.nlargest(5)
        extra["top_days"] = [dict(day=str(i.date()), steps=float(v)) for i, v in top.items()]
        mt = cur.steps.resample("MS").sum(min_count=1)
        extra["best_month"] = mt.idxmax().strftime("%B") if mt.notna().any() else None
        extra["records_in_year"] = [c for c in insights_for(a, b)]
        extra["totals"] = dict(steps=float(np.nansum(cur.steps)), miles=float(np.nansum(cur.distance_mi)), run_miles=float(np.nansum(cur.run_mi)), workout_hours=float(np.nansum(cur.workout_min) / 60), avg_sleep=None if cur.asleep_watch_h.notna().sum() == 0 else float(cur.asleep_watch_h.mean()), runs=float(np.nansum(cur.runs)))
    return json_safe(dict(kind=kind, start=str(a.date()), end=str(b.date()), rows=rows_, unusual=odd, days=dict(t=[str(x.date()) for x in cur.index], steps=[None if pd.isna(v) else float(v) for v in cur.steps], sleep=[None if pd.isna(v) else float(v) for v in cur.asleep_watch_h]), coverage=dict(steps_days=int(cur.steps.notna().sum()), worn_days=int(cur.worn_day.sum()), total=len(cur)), **extra))


def insights_for(a, b):
    f = ROOT / "insights.json"
    if not f.exists():
        return []
    out = []
    for c in json.loads(f.read_text())["cards"]:
        w = c.get("raw", {})
        when = w.get("date") or w.get("day")
        if when and a <= pd.Timestamp(when) <= b:
            out.append(dict(headline=c["headline"], grade=c["grade"], finding=c["finding"]))
    return out[:8]


@app.post("/api/ask")
def ask(b: dict = Body(...)):
    from .ask import answer
    c = con()
    try:
        return json_safe(answer(b.get("q", ""), daily(), c, explore_pack))
    finally:
        c.close()


@app.get("/api/state")
def get_state():
    return state()


@app.put("/api/state/{key}")
def put_state(key: str, value=Body(...)):
    if key not in ("annotations", "notes", "experiments", "cohorts", "dismissed"):
        raise HTTPException(400, "Unknown key.")
    s = state()
    s[key] = value
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(STATE)
    return dict(ok=True)


@app.get("/api/export/daily.csv")
def export_daily():
    return Response(daily().to_csv(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=health_daily.csv"})


@app.get("/api/export/data_dictionary.json")
def export_dict():
    return dict(metrics=METRICS, types=rows(q("SELECT * EXCLUDE(first_day,last_day), first_day::VARCHAR first_day, last_day::VARCHAR last_day FROM type_inventory")))


@app.post("/api/import")
def import_export(b: dict = Body(...)):
    """Re-import a newer export by local path. Exports are cumulative; duplicates are removed by natural key."""
    p = Path(b.get("path", "")).expanduser()
    if p.suffix not in (".zip", ".xml") or not p.is_file():
        raise HTTPException(400, "Provide the path of an export.zip or export.xml on this computer.")
    parse.ingest(str(p), ROOT)
    builder.build(ROOT)
    _cache.clear()
    return recompute() and dict(ok=True)


@app.post("/api/delete")
def delete_all(b: dict = Body(...)):
    if b.get("confirm") != "DELETE":
        raise HTTPException(400, "Send confirm=DELETE.")
    for item in ROOT.iterdir():
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    _cache.clear()
    return dict(deleted=True)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
