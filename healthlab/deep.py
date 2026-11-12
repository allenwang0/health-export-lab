"""Second-order investigations: questions a metric-by-metric scan cannot ask.

Each function returns a data payload for its view plus insight cards. Event studies are
tested against placebo dates drawn from the same record, which keeps season, trend and
autocorrelation intact. Null results are reported too; "nothing there" is a finding.
"""
import numpy as np
import pandas as pd

from . import stats as st
from .insights import card, clock

SEED = st.SEED
HOME_MIN_DAYS = 14


def place_name(tz):
    return (tz or "?").split("/")[-1].replace("_", " ")


def trips(con):
    """Offset changes of 2+ hours, excluding DST, from the rebuilt zone timeline."""
    seg = con.execute("SELECT start_utc, tz FROM tz_segments ORDER BY start_utc").df()
    from zoneinfo import ZoneInfo
    out = []
    for a, b in zip(seg.itertuples(), seg.iloc[1:].itertuples()):
        t = b.start_utc.tz_localize("UTC")
        shift = (t.astimezone(ZoneInfo(b.tz)).utcoffset() - t.astimezone(ZoneInfo(a.tz)).utcoffset()).total_seconds() / 3600
        if abs(shift) >= 2:
            out.append(dict(date=t.tz_convert(ZoneInfo(b.tz)).normalize().tz_localize(None), shift=shift, frm=place_name(a.tz), to=place_name(b.tz)))
    return out


def deviation(s, window=28):
    """Distance from the personal trailing median, excluding the last 2 days."""
    base = s.shift(2).rolling(window, min_periods=8).median()
    return s - base


def event_curve(dev, dates, lo=-3, hi=8):
    rows = {k: [] for k in range(lo, hi + 1)}
    for t in dates:
        for k in rows:
            v = dev.get(t + pd.Timedelta(days=k))
            if v is not None and not pd.isna(v):
                rows[k].append(v)
    return [dict(k=k, mean=float(np.mean(v)) if len(v) >= 3 else None, n=len(v), se=float(np.std(v) / np.sqrt(len(v))) if len(v) >= 3 else None) for k, v in rows.items()]


def placebo_p(dev, dates, window=(1, 3), n=999):
    """Mean deviation over the window after events, against random dates from the same series."""
    vals = dev.dropna()
    ix = vals.index

    def stat(ds):
        v = []
        for t in ds:
            v += [dev.get(t + pd.Timedelta(days=k)) for k in range(window[0], window[1] + 1)]
        v = [x for x in v if x is not None and not pd.isna(x)]
        return float(np.mean(v)) if v else np.nan
    obs = stat(dates)
    rng = np.random.default_rng(SEED)
    null = np.array([stat(ix[rng.integers(0, len(ix), len(dates))]) for _ in range(n)])
    null = null[~np.isnan(null)]
    return obs, float((1 + (np.abs(null - null.mean()) >= abs(obs - null.mean())).sum()) / (len(null) + 1))


def jet_lag(con, d):
    tr = trips(con)
    east = [t["date"] for t in tr if t["shift"] > 0]
    west = [t["date"] for t in tr if t["shift"] < 0]
    bed, wake, sl, rhr, hrv = (deviation(d[m]) for m in ["bed_clock_adj", "wake_clock", "asleep_watch_h", "rhr", "hrv_sdnn"])
    curves = {n: dict(east=event_curve(s, east), west=event_curve(s, west)) for n, s in [("bed", bed), ("wake", wake), ("sleep", sl), ("rhr", rhr), ("hrv", hrv)]}
    tests, cards = [], []
    all_dates = east + west
    rhr_d, rhr_p = placebo_p(rhr, all_dates)
    sl_d, sl_p = placebo_p(sl, all_dates)
    hrv_d, hrv_p = placebo_p(hrv, all_dates)
    long_haul = [t["date"] for t in tr if abs(t["shift"]) >= 5]
    lh_d, lh_p = placebo_p(rhr, long_haul) if len(long_haul) >= 5 else (None, None)
    tests += [("trip rhr", rhr_p), ("trip sleep", sl_p), ("trip hrv", hrv_p)]

    def settle(direction):
        """First day after arrival from which onset stays within 45 minutes of normal."""
        c = [x for x in curves["bed"][direction] if x["k"] >= 1 and x["mean"] is not None]
        for i, x in enumerate(c):
            if all(abs(y["mean"]) <= .75 for y in c[i:]):
                return x["k"]
        return None
    cards.append(dict(kw=dict(cat="Travel", headline=f"Crossing time zones costs you about {abs(sl_d):.1f} hours of sleep" if sl_d < -.2 else "Crossing time zones barely dents your sleep",
        finding=f"In the 3 nights after {len(all_dates)} shifts of 2+ hours, sleep ran {sl_d:+.2f} h against your own prior 4 weeks, resting HR {rhr_d:+.1f} bpm and HRV {hrv_d:+.1f} ms.",
        compared=f"{len(east)} eastward and {len(west)} westward shifts against 999 sets of placebo dates", n=len(all_dates), effect=f"{sl_d:+.2f} h, {rhr_d:+.1f} bpm", uncertainty=f"Placebo p: sleep {sl_p:.3f}, RHR {rhr_p:.3f}, HRV {hrv_p:.3f}",
        confounders=["Flight stress and short nights before travel", "Alcohol and restaurant meals on trips"], limitations=["Zone tags place a shift within about a day."], next_q="Is long-haul travel worse than short hops?", viz=dict(kind="event", key="jet"), raw=dict(view="discover"), effect_size=abs(rhr_d) / 3 + abs(sl_d), quality=.8, novelty=1), p=min(sl_p, rhr_p)))
    e, w = settle("east"), settle("west")
    return dict(trips=[dict(date=str(t["date"].date()), shift=t["shift"], frm=t["frm"], to=t["to"]) for t in tr], curves=curves, settle=dict(east=e, west=w), long_haul=dict(n=len(long_haul), rhr=lh_d, p=lh_p)), cards, tests


def strain_spells(d, trip_dates):
    z = pd.DataFrame({"rhr": st.rolling_robust_z(d.rhr), "hrv": st.rolling_robust_z(d.hrv_sdnn), "resp": st.rolling_robust_z(d.resp_rate), "shr": st.rolling_robust_z(d.sleep_hr)})
    flag = (z.rhr >= 2) & ((z.hrv <= -1) | (z.resp >= 1.5) | (z.shr >= 1.5))
    # exclude the first days of a trip so jet lag itself cannot be counted as a spell
    days = flag[flag].index
    spells, cur = [], []
    for t in days:
        if cur and (t - cur[-1]).days > 2:
            spells.append(cur)
            cur = []
        cur.append(t)
    if cur:
        spells.append(cur)
    rows = []
    for s in spells:
        win = d.loc[s[0]:s[-1]]
        back = next((k for k in range(1, 22) if s[-1] + pd.Timedelta(days=k) in z.index and abs(z.rhr.get(s[-1] + pd.Timedelta(days=k), 9)) < 1), None)
        rows.append(dict(start=str(s[0].date()), end=str(s[-1].date()), days=len(s), peak_rhr=float(win.rhr.max()), low_hrv=None if win.hrv_sdnn.isna().all() else float(win.hrv_sdnn.min()), recover=back, tz=place_name(win.tz.iloc[0])))
    starts = [pd.Timestamp(r["start"]) for r in rows]
    valid = z.rhr.dropna().index
    win = 7
    near = set()
    for t in trip_dates:
        near |= {t + pd.Timedelta(days=k) for k in range(0, win + 1)}
    share_days = np.mean([t in near for t in valid])
    share_spells = np.mean([t in near for t in starts]) if starts else 0
    rng = np.random.default_rng(SEED)
    hits = sum(t in near for t in starts)
    null = []
    for _ in range(4999):  # rotate spell starts through the calendar, keeping their spacing
        k = int(rng.integers(30, len(valid) - 30))
        null.append(sum(valid[(valid.get_loc(t) + k) % len(valid)] in near for t in starts if t in valid))
    p = (1 + sum(n >= hits for n in null)) / 5000
    rr = share_spells / share_days if share_days else None
    # sleep in the 3 nights before a spell, against that person's own 28-day level
    dev = deviation(d.asleep_watch_h)
    pre = [dev.get(t - pd.Timedelta(days=k)) for t in starts for k in (1, 2, 3)]
    pre = [x for x in pre if x is not None and not pd.isna(x)]
    pre_mean = float(np.mean(pre)) if pre else None
    by_year = pd.Series([t.year for t in starts]).value_counts().sort_index()
    by_month = pd.Series([t.month for t in starts]).value_counts().reindex(range(1, 13), fill_value=0)
    card_ = dict(kw=dict(cat="Recovery patterns", headline=f"High-strain spells are {rr:.1f}x more likely in the week after a time zone change" if rr and rr > 1.3 and p < .05 else f"A hint, not proof: high-strain spells run {rr:.1f}x more often after travel" if rr and rr > 1.3 and p < .15 else "High-strain spells do not cluster after travel",
        finding=f"{len(rows)} spells where resting HR ran high alongside low HRV or raised breathing or sleeping HR. {share_spells:.0%} began within a week of a shift, while those weeks are {share_days:.0%} of recorded days. {sum(r['days'] >= 2 for r in rows)} lasted 2+ flagged days; the longest ran {max(r['days'] for r in rows)}. Sleep in the 3 nights before was {pre_mean:+.2f} h against your norm, so short sleep does not appear to set them up.",
        compared=f"{hits} of {len(rows)} spell starts against rotated calendars", n=len(rows), effect=f"{rr:.2f}x" if rr else "n/a", uncertainty=f"Rotation test p = {p:.3f}", confounders=["Illness caught while travelling", "Alcohol and late nights on trips", "Jet lag itself raises resting HR"],
        limitations=["A spell is a data pattern, not a diagnosis.", "Only an algorithmic definition; thresholds were set before looking at travel."], next_q="Which spells do you remember as being ill? Annotate them on the Timeline.", viz=dict(kind="spells"), raw=dict(view="discover"), effect_size=min((rr or 1) - 1, 1), quality=.75, novelty=1), p=p)
    return dict(spells=rows, by_year={int(k): int(v) for k, v in by_year.items()}, by_month=[int(v) for v in by_month.values], rr=rr, p=p, sleep_before=pre_mean), [card_], [("spell travel", p)]


def run_decoupling(con, d):
    r = con.execute("""
      WITH w AS (SELECT workout_id, local_date, start_utc a, end_utc b, start_utc + (end_utc-start_utc)/2 a_mid, route_path, distance_mi, temp_f, duration_min FROM workout_facts
       WHERE type='Running' AND route_path IS NOT NULL AND duration_min>=20 AND distance_mi>=2),
      sp AS (SELECT w.workout_id, avg(speed) FILTER (ts_utc<a_mid) s1, avg(speed) FILTER (ts_utc>=a_mid) s2 FROM w JOIN route_points p USING(route_path)
             WHERE p.speed>0.8 AND ts_utc>a + INTERVAL 5 MINUTE GROUP BY 1),
      hr AS (SELECT w.workout_id, avg(value) FILTER (start_utc<a_mid) h1, avg(value) FILTER (start_utc>=a_mid) h2, count(*) n FROM w JOIN samples s ON s.type='HeartRate' AND s.start_utc BETWEEN a + INTERVAL 5 MINUTE AND b GROUP BY 1)
      SELECT w.local_date::VARCHAR date, w.distance_mi, w.temp_f, w.duration_min, s1, s2, h1, h2, n FROM w JOIN sp USING(workout_id) JOIN hr USING(workout_id) WHERE n>=20""").df()
    if len(r) < 15:
        return {}, [], []
    r["decouple"] = ((r.h2 / r.s2) / (r.h1 / r.s1) - 1) * 100
    r = r[r.decouple.between(-25, 40)].sort_values("date").reset_index(drop=True)
    r["t"] = (pd.to_datetime(r.date) - pd.Timestamp(r.date.iloc[0])).dt.days / 365.25
    y = r.groupby(r.date.str[:4]).decouple.agg(["median", "count"])
    fit = st.ols_hac(r, "decouple", ["t", "distance_mi"], 5)
    t, dist = fit["terms"]["t"], fit["terms"]["distance_mi"]
    med = r.decouple.median()
    c = dict(kw=dict(cat="Running", headline=f"Too few runs to judge heart-rate drift: only {len(r)} keep dense heart-rate samples",
        finding=f"Across those {len(r)} runs, heart rate per unit of speed changed a median {med:+.1f}% from the first half to the second. Intervals and warm-ups can push this either way, so it says little yet about your endurance.", compared=f"First half against second half of each run, {r.date.iloc[0]} to {r.date.iloc[-1]}",
        n=len(r), effect=f"{med:.1f}% median drift", uncertainty=f"Per mile 95% CI {dist['lo']:+.1f} to {dist['hi']:+.1f}; per year {t['lo']:+.1f} to {t['hi']:+.1f}", confounders=["Hills in the second half", "Intervals or negative splits", "Heat rising during the run"], limitations=["Only recent runs keep dense heart-rate samples, so this covers a small share of your runs.", "Uses GPS speed; the first 5 minutes are excluded as warm-up."], next_q="Does drift shrink in a training block?", viz=dict(kind="drift"), raw=dict(view="running"), effect_size=abs(med) / 10, quality=.4, novelty=.8), p=dist["p"], cap="C")
    return dict(points=[[x.date, round(x.decouple, 2), round(x.distance_mi, 2)] for x in r.itertuples()], by_year=[dict(y=k, med=float(v["median"]), n=int(v["count"])) for k, v in y.iterrows()], median=float(med)), [c], [("drift per mile", dist["p"]), ("drift per year", t["p"])]


def heat_penalty(con):
    r = con.execute("""SELECT local_date, pace_min_mi p, avg_hr h, distance_mi m, temp_f, humidity_pct hu FROM workout_facts WHERE type='Running' AND temp_f IS NOT NULL AND distance_mi>=1.5 AND pace_min_mi BETWEEN 5 AND 16 AND avg_hr IS NOT NULL AND NOT coalesce(indoor,false) ORDER BY start_utc""").df()
    if len(r) < 30:
        return {}, [], []
    r["t"] = (pd.to_datetime(r.local_date) - pd.Timestamp(r.local_date.min())).dt.days / 365.25
    r["temp10"] = r.temp_f / 10
    fit_hr = st.ols_hac(r, "h", ["temp10", "p", "m", "t"], 5)["terms"]["temp10"]
    fit_p = st.ols_hac(r, "p", ["temp10", "h", "m", "t"], 5)["terms"]["temp10"]
    hot = fit_hr["p"] < .05 and fit_hr["b"] > 0
    c = dict(kw=dict(cat="Running", headline=f"Every 10°F warmer adds about {fit_hr['b']:.1f} bpm at the same pace" if hot else "Temperature has no clear effect on your running heart rate",
        finding=f"Across {len(r)} outdoor runs ({r.temp_f.min():.0f} to {r.temp_f.max():.0f}°F), holding pace, distance and year fixed, heart rate moved {fit_hr['b']:+.1f} bpm per 10°F. Holding heart rate fixed, pace moved {fit_p['b']*60:+.0f} s/mi per 10°F.", compared="Outdoor runs with Apple Weather data", n=len(r), effect=f"{fit_hr['b']:+.1f} bpm per 10°F",
        uncertainty=f"95% CI {fit_hr['lo']:+.1f} to {fit_hr['hi']:+.1f}", confounders=["Humidity", "Run choice on hot days"], limitations=["Weather tags exist for only part of your runs."], next_q="Should old summer and winter paces be compared at all?", viz=dict(kind="heat"), raw=dict(view="running"), effect_size=abs(fit_hr["b"]) / 4, quality=.75, novelty=1), p=fit_hr["p"])
    return dict(points=[[float(x.temp_f), float(x.h), float(x.p)] for x in r.itertuples()], slope=fit_hr["b"]), [c], [("heat", fit_hr["p"])]


def adaptation_lag(d):
    """At what lead does 4-week running volume best track VO2 max and resting HR?"""
    w = pd.DataFrame({"vol": d.run_mi.rolling(28, min_periods=14).sum(), "vo2": d.vo2max, "rhr": d.rhr}).resample("W-SUN").agg(dict(vol="last", vo2="median", rhr="median"))
    w["vo2"] = w.vo2.interpolate(limit=3)
    w = w[w.vol.notna()]
    out, cards, tests = {}, [], []
    for m, nm, sign in [("vo2", "VO2 max estimate", 1), ("rhr", "resting heart rate", -1)]:
        rx = w.vol - w.vol.rolling(52, center=True, min_periods=20).mean()
        ry = w[m] - w[m].rolling(52, center=True, min_periods=20).mean()
        curve = [dict(lag=k, **st.corr(rx.values, ry.shift(-k).values)) for k in range(-4, 17)]
        pos = [c for c in curve if c["lag"] >= 0 and c["r"] is not None]
        if not pos:
            continue
        best = max(pos, key=lambda c: sign * c["r"])
        p = st.circular_shift_p(rx.values, ry.shift(-best["lag"]).values, 999)
        p_adj = min(1, (p or 1) * 17)  # 17 leads were examined, so Bonferroni for choosing the best
        out[m] = dict(curve=[dict(lag=c["lag"], r=c["r"]) for c in curve], best=best["lag"], r=best["r"], p=p_adj)
        tests.append((f"adapt {m}", p_adj))
        cards.append(dict(kw=dict(cat="Lagged relationships", headline=f"Running volume shows up in your {nm} about {best['lag']} week{'s' if best['lag'] != 1 else ''} later" if p_adj < .1 else f"Running volume has no clear lead on your {nm}",
            finding=f"Detrended 4-week running volume correlates best with {nm} {best['lag']} week{'s' if best['lag'] != 1 else ''} later: r = {best['r']:.2f}, compared across leads of 0 to 16 weeks.", compared=f"{best['n']} weekly pairs", n=int(best["n"]), effect=f"r = {best['r']:.2f} at {best['lag']} weeks", uncertainty=f"Rotation p {p:.3f}, {p_adj:.3f} after correcting for picking the best of 17 leads",
            confounders=["Other training and walking", "VO2 max only updates on qualifying workouts"], limitations=["A lead in time does not establish cause."], next_q="How long after a break does this fade?", viz=dict(kind="adapt", key=m), raw=dict(view="discover"), effect_size=abs(best["r"]) * 1.5, quality=.7, novelty=1), p=p_adj))
    return out, cards, tests


def sleep_loss(d):
    s = d.asleep_watch_h
    cps = [c for c in st.change_points(s) if abs(c["delta"]) >= .4]
    if not cps:
        return {}, [], []
    cp = cps[-1]
    t0 = pd.Timestamp(cp["date"])
    aft, bef = d[t0:], d[t0 - pd.Timedelta(days=365):t0 - pd.Timedelta(days=1)]
    r = {}
    for k, col in [("sleep", "asleep_watch_h"), ("bed", "bed_clock_adj"), ("wake", "wake_clock"), ("deep", "deep_h"), ("rem", "rem_h"), ("core", "core_h"), ("awake", "awake_h")]:
        r[k] = float(aft[col].mean() - bef[col].mean())
    wd = float(aft[~aft.weekend.astype(bool)].asleep_watch_h.mean() - bef[~bef.weekend.astype(bool)].asleep_watch_h.mean())
    we = float(aft[aft.weekend.astype(bool)].asleep_watch_h.mean() - bef[bef.weekend.astype(bool)].asleep_watch_h.mean())
    t = pd.concat([bef.assign(g=0.0), aft.assign(g=1.0)])
    # season and weekend only: a linear trend across 18 months would be collinear with the period
    tt, _ = st.calendar_terms(t[["asleep_watch_h", "g"]].dropna())
    fit = st.ols_hac(tt, "asleep_watch_h", ["g", "_weekend", "_sin", "_cos"])["terms"]["g"]
    lost_in = "falling asleep later" if r["bed"] > abs(r["wake"]) else "waking earlier" if -r["wake"] > r["bed"] else "both ends of the night"
    stages = max([("REM", r["rem"]), ("core", r["core"]), ("deep", r["deep"])], key=lambda x: abs(x[1]))
    c = dict(kw=dict(cat="Long-term trends", headline=f"Since {t0.strftime('%b %Y')} you sleep {abs(r['sleep']):.1f} hours {'less' if r['sleep'] < 0 else 'more'}, mostly from {lost_in}",
        finding=f"Average sleep changed {r['sleep']:+.2f} h. You fall asleep {r['bed']*60:+.0f} min and wake {r['wake']*60:+.0f} min relative to the year before. Weekdays changed {wd:+.2f} h, weekends {we:+.2f} h. The biggest stage change is {stages[0]} ({stages[1]:+.2f} h).",
        compared=f"{len(aft)} days from {t0.date()} against the 365 days before", n=len(aft) + len(bef), effect=f"{r['sleep']:+.2f} h per night", uncertainty=f"Adjusted 95% CI {fit['lo']:+.2f} to {fit['hi']:+.2f} h. The change date is accurate to a few weeks.", confounders=["Schedule or job change", "Watch worn less overnight"], limitations=["Stage estimates are device-derived."], next_q="What changed in your life around then? Add it to the Timeline.", viz=dict(kind="sleeploss"), raw=dict(metric="asleep_watch_h", date=cp["date"]), effect_size=abs(r["sleep"]), quality=.85, novelty=1), p=fit["p"])
    return dict(date=cp["date"], weekday=wd, weekend=we, **r), [c], [("sleep loss", fit["p"])]


def sleep_carryover(d):
    s = d.asleep_watch_h
    dv = deviation(d.rhr)
    frame = pd.DataFrame({"one": (s < 6) & ~(s.shift(1) < 6), "two": (s < 6) & (s.shift(1) < 6), "ok": s >= 7, "rhr": dv.shift(-1), "hrv": deviation(d.hrv_sdnn).shift(-1), "next": s.shift(-1), "steps": d.steps.shift(-1)}).where(s.notna())
    base_sleep = s.rolling(56, min_periods=14).median()
    rebound = (frame.next - base_sleep)[frame.one == True]  # noqa: E712
    out = {}
    for k in ("rhr", "hrv", "steps", "next"):
        out[k] = {g: dict(mean=float(frame[frame[g] == True][k].mean()), n=int(frame[frame[g] == True][k].notna().sum())) for g in ("ok", "one", "two")}  # noqa: E712
    g = pd.DataFrame({"y": frame.rhr, "g": np.where(frame.two == True, 1.0, np.where(frame.ok == True, 0.0, np.nan))}, index=d.index)  # noqa: E712
    c2 = st.compare(g.dropna(), "y", "g")
    a = c2.get("adjusted")
    if not a:
        return out, [], []
    c = dict(kw=dict(cat="Recovery patterns", headline=(f"Two short nights raise your resting HR by {a['b']:.1f} bpm, and one night barely does" if a["p"] < .05 and a["b"] > 0 else "Back-to-back short nights do not visibly raise your resting HR"),
        finding=f"After 7+ hours resting HR sits {out['rhr']['ok']['mean']:+.2f} bpm from baseline, after one short night {out['rhr']['one']['mean']:+.2f}, and after two in a row {out['rhr']['two']['mean']:+.2f}. The night after a short night runs {rebound.mean():+.2f} h against your norm.",
        compared=f"{c2['n_a']} days after two nights under 6 h against {c2['n_b']} after 7+ h", n=c2["n_a"] + c2["n_b"], effect=f"{a['b']:+.2f} bpm", uncertainty=f"95% CI {a['lo']:+.2f} to {a['hi']:+.2f}", confounders=["Illness shortens sleep and raises HR", "Alcohol and late nights"], limitations=["Direction of influence is not established."], next_q="Does this differ on weekends?", viz=dict(kind="carry"), raw=dict(metric="rhr"), effect_size=abs(a["b"]) / 3, quality=.8, novelty=.9), p=a["p"])
    return out, [c], [("two short nights", a["p"])]


def late_runs(d):
    late = np.where(d.run_hour >= 18, 1.0, np.where(d.run_hour < 16, 0.0, np.nan))
    f = pd.DataFrame({"g": late, "bed": d.bed_clock_adj.shift(-1), "sl": d.asleep_watch_h.shift(-1), "deep": d.deep_h.shift(-1), "sleep_hr": d.sleep_hr.shift(-1)}, index=d.index)
    res = {k: st.compare(f[[k, "g"]].dropna(), k, "g") for k in ("bed", "sl", "deep", "sleep_hr")}
    a = res["bed"].get("adjusted")
    if not a:
        return {}, [], []
    b = res["sl"].get("adjusted")
    hr = res["sleep_hr"].get("adjusted")
    c = dict(kw=dict(cat="Lagged relationships", headline=(f"After evening runs you fall asleep {abs(a['b'])*60:.0f} minutes {'later' if a['b'] > 0 else 'earlier'}" if a["p"] < .05 else "Evening runs do not push back your bedtime"),
        finding=f"Compared with earlier runs, runs from 6 pm move sleep onset {a['b']*60:+.0f} min, sleep length {b['b']:+.2f} h and sleeping heart rate {hr['b']:+.1f} bpm that night.", compared=f"{res['bed']['n_a']} evening runs against {res['bed']['n_b']} earlier runs", n=res["bed"]["n_a"] + res["bed"]["n_b"], effect=f"{a['b']*60:+.0f} min", uncertainty=f"Onset 95% CI {a['lo']*60:+.0f} to {a['hi']*60:+.0f} min; sleeping HR {hr['lo']:+.1f} to {hr['hi']:+.1f} bpm",
        confounders=["Evening runs fall on late days anyway"], limitations=["Run timing was your choice, not randomised."], next_q="Is it the run, or a late day?", viz=dict(kind="note", text="Compare evening and earlier runs in the Lab."), raw=dict(view="lab"), effect_size=abs(a["b"]) / 1.5, quality=.7, novelty=.9), p=a["p"])
    return {k: v.get("adjusted") for k, v in res.items()}, [c], [("late run bed", a["p"]), ("late run sleep hr", hr["p"])]


def places(d):
    days = d[d.steps.notna()].copy()
    # a stay needs 7+ consecutive days in a zone, so short hops do not define a place
    grp = (days.tz != days.tz.shift()).cumsum()
    stay = days.groupby(grp).tz.transform("size")
    stays = days[stay >= 7]
    mets = ["steps", "asleep_watch_h", "bed_clock_adj", "wake_clock", "rhr", "hrv_sdnn", "flights"]
    table, tests = [], []
    since = d[d.hr_n.notna()].index.min()
    stays = stays[stays.index >= since]
    home = stays.tz.value_counts().idxmax()
    for tz, g in stays.groupby("tz"):
        if len(g) < HOME_MIN_DAYS:
            continue
        row = dict(tz=tz, place=place_name(tz), days=len(g), first=str(g.index[0].date()), **{m: None if g[m].notna().sum() < 5 else float(g[m].median()) for m in mets})
        if tz != home:
            f = pd.DataFrame({"g": (stays.tz == tz).astype(float).where(stays.tz.isin([tz, home])), **{m: stays[m] for m in ("steps", "rhr", "hrv_sdnn")}}, index=stays.index).dropna(subset=["g"])
            row["cmp"] = {m: st.compare(f[[m, "g"]].dropna(), m, "g", adjust=True).get("adjusted") for m in ("steps", "rhr", "hrv_sdnn")}
            for m, v in row["cmp"].items():
                if v:
                    tests.append((f"{tz} {m}", v["p"]))
        table.append(row)
    table.sort(key=lambda r: -r["days"])
    h = next(r for r in table if r["tz"] == home)
    others = [r for r in table if r["tz"] != home and r["steps"]]
    cards = []
    sized = [r for r in others if r["days"] >= 30] or others
    if sized:
        top = max(sized, key=lambda r: r["steps"])
        cm = top.get("cmp", {})
        a, rh = cm.get("steps"), cm.get("rhr")
        pct = top["steps"] / h["steps"] - 1
        cards.append(dict(kw=dict(cat="Places", headline=f"In {top['place']} you walk {pct:.0%} more, and your resting heart rate runs {h['rhr'] - top['rhr']:.0f} bpm lower" if top["rhr"] and top["rhr"] < h["rhr"] else f"In {top['place']} you walk {pct:.0%} more than in {h['place']}",
            finding=f"Median {top['steps']:,.0f} steps a day across {top['days']} days in {top['place']}, against {h['steps']:,.0f} in {h['place']}. Resting HR {top['rhr']:.0f} against {h['rhr']:.0f} bpm.", compared=f"{len(table)} zones with stays of 7+ days since Watch data began", n=sum(r['days'] for r in table), effect=f"{top['steps']-h['steps']:+,.0f} steps a day",
            uncertainty=(f"Adjusted 95% CI {a['lo']:+,.0f} to {a['hi']:+,.0f} steps" if a else "Too few days to adjust") + (f"; resting HR {rh['lo']:+.1f} to {rh['hi']:+.1f} bpm" if rh else ""), confounders=["Each place is also a period of your life", "Zones cover more than one city"], limitations=["Time zone is a stand-in for location. This is a comparison of periods, not of cities."], next_q="Is it the place, or what you were doing there?", viz=dict(kind="places"), raw=dict(view="discover"), effect_size=min(pct, 1), quality=.65, novelty=1), p=a["p"] if a else 1, cap="B"))
    return dict(rows=table, home=place_name(home)), cards, tests


def eras(con, d):
    """Segment life by the shape of the day, using each month's 24-hour step profile.

    Adjacent months merge bottom-up (contiguous Ward), so eras are unbroken periods. Months
    with fewer than 20 days of steps are left out.
    """
    h = con.execute("""SELECT strftime(local_date,'%Y-%m') mo, isodow(local_date)>=6 we, local_hour h, sum(value) v FROM hourly WHERE metric='steps' GROUP BY 1,2,3""").df()
    p = h.pivot_table(index="mo", columns=["we", "h"], values="v", fill_value=0)
    days = d[d.steps.notna()].groupby(d[d.steps.notna()].index.strftime("%Y-%m")).size()
    p = p[p.index.isin(days[days >= 20].index)]
    if len(p) < 18:
        return {}, []
    bins = lambda we: p.xs(we, axis=1, level=0).T.groupby(lambda hr: hr // 3).sum().T if we in p.columns.get_level_values(0) else None
    wk, we = bins(False), bins(True)
    feat = pd.concat([wk.div(wk.sum(1), axis=0), we.div(we.sum(1), axis=0)], axis=1).fillna(0)
    months_key = d.index.strftime("%Y-%m")
    lv = pd.DataFrame({"wake": d.wake_clock.groupby(months_key).median(), "bed": d.bed_clock_adj.groupby(months_key).median(), "steps": np.log(d.steps.groupby(months_key).median())}).reindex(p.index).ffill().bfill().fillna(0)
    z = lambda f: ((f - f.mean()) / f.std().replace(0, 1)).values
    X = np.c_[z(feat), 2 * z(lv)]  # levels count more than any single 3-hour share
    segs = [[i] for i in range(len(X))]

    def cost(a, b):
        ma, mb = X[a].mean(0), X[b].mean(0)
        return len(a) * len(b) / (len(a) + len(b)) * ((ma - mb) ** 2).sum()
    target = 7
    while len(segs) > target:
        c = [cost(segs[i], segs[i + 1]) for i in range(len(segs) - 1)]
        i = int(np.argmin(c))
        segs[i:i + 2] = [segs[i] + segs[i + 1]]
    while True:  # absorb stubs of under 3 months into the closer neighbour
        short = [i for i, s in enumerate(segs) if len(s) < 3]
        if not short:
            break
        i = short[0]
        j = i + 1 if i == 0 else i - 1 if i == len(segs) - 1 else (i - 1 if cost(segs[i - 1], segs[i]) < cost(segs[i], segs[i + 1]) else i + 1)
        a, b = min(i, j), max(i, j)
        segs[a:b + 1] = [segs[a] + segs[b]]
    months = list(p.index)
    rows = []
    for s in segs:
        a, b = months[s[0]], months[s[-1]]
        w = d[(d.index >= a + "-01") & (d.index <= pd.Timestamp(b + "-01") + pd.offsets.MonthEnd(0))]
        prof = p.iloc[s].sum()
        wk = prof.loc[False]
        wk = wk / wk.sum()
        peak = int(wk.idxmax())
        tz = w.tz.value_counts().index[0]
        run_wk = None if w.run_mi.notna().sum() == 0 else float(w.run_mi.sum() / len(w) * 7)
        wake_m = float(w.wake_clock.median()) if w.wake_clock.notna().any() else None
        level = "High movement" if w.steps.median() >= 11000 else "Low movement" if w.steps.median() < 6000 else "Steady movement"
        riser = None if wake_m is None else "early riser" if wake_m < 8 else "late riser" if wake_m > 9.75 else "mid-morning riser"
        tag = f"{level}, {riser}" if riser else level
        rows.append(dict(start=a, end=b, months=len(s), place=place_name(tz), steps=float(w.steps.median()), wake=wake_m, bed=None if w.bed_clock_adj.isna().all() else float(w.bed_clock_adj.median()), rhr=None if w.rhr.isna().all() else float(w.rhr.median()), run_mi_wk=run_wk,
                         peak_hour=peak, tag=tag, profile=[round(float(v), 4) for v in wk.values], weekend_profile=[round(float(v), 4) for v in (prof.loc[True] / prof.loc[True].sum()).values]))
    busiest = max(rows, key=lambda r: r["steps"])
    quiet = min(rows, key=lambda r: r["steps"])
    c = dict(kw=dict(cat="Life eras", headline=f"The shape of your day splits your record into {len(rows)} distinct eras",
        finding=f"Grouping months by when your steps happen, not how many, gives {len(rows)} eras. The busiest ({busiest['start']} to {busiest['end']}) ran {busiest['steps']:,.0f} steps a day, peaking at {clock(busiest['peak_hour'])} on weekdays. The quietest ({quiet['start']} to {quiet['end']}) ran {quiet['steps']:,.0f}.",
        compared=f"{len(months)} months of hourly step profiles", n=len(months), effect=f"{len(rows)} eras", uncertainty="Era edges come from clustering. Treat them as accurate to a month or two.", confounders=["Phone carrying habits"], limitations=["The number of eras is a chosen starting point (7), not a discovered truth."], next_q="Do these eras match jobs or moves you remember? Name them on the Timeline.", viz=dict(kind="eras"), raw=dict(view="discover"), effect_size=.8, quality=.75, novelty=1), p=None)
    return dict(eras=rows), [c]


def shortest_night(d):
    s = d.asleep_watch_h.dropna()
    night = (s.index.dayofweek - 1) % 7  # wake date Monday means Sunday night
    names = "Mon Tue Wed Thu Fri Sat Sun".split()
    m = s.groupby(night).mean()
    worst, best = int(m.idxmin()), int(m.idxmax())
    f = pd.DataFrame({"y": s, "g": (night == worst).astype(float)}, index=s.index)
    c = st.compare(f, "y", "g").get("adjusted")
    p = min(1, c["p"] * 7) if c else 1
    return [round(float(v), 2) for v in m.values], [dict(kw=dict(cat="Seasonality", headline=(f"{names[worst]} night is reliably your shortest" if p < .05 else f"No night of the week reliably runs short: only {(m[best]-m[worst])*60:.0f} minutes separate them"), finding=f"You sleep {m[worst]:.2f} h on {names[worst]} nights and {m[best]:.2f} h on {names[best]} nights.", compared="Nights of the week, Watch-scored", n=len(s), effect=f"{(m[best]-m[worst])*60:.0f} min spread", uncertainty=f"Adjusted gap {c['b']:+.2f} h (95% CI {c['lo']:+.2f} to {c['hi']:+.2f}), corrected p {p:.3f}", confounders=["Work schedule"], limitations=["Pooled across years."], next_q="Is this the same in each era?", viz=dict(kind="nights"), raw=dict(metric="asleep_watch_h"), effect_size=(m[best]-m[worst]), quality=.85, novelty=.8), p=p)], [("dow sleep", p)]


def rhr_drivers(d):
    """Which monthly behaviours account for month-to-month resting HR? Drop-one partial R2."""
    mo = d.index.to_period("M")
    f = pd.DataFrame({"rhr": d.rhr.groupby(mo).median(), "n": d.rhr.groupby(mo).count(), "run_mi": d.run_mi.groupby(mo).sum(), "steps": d.steps.groupby(mo).median(), "sleep": d.asleep_watch_h.groupby(mo).mean(), "bed_sd": d.bed_clock_adj.groupby(mo).std()})
    f = f[f.n >= 15].dropna()
    if len(f) < 30:
        return {}, [], []
    f.index = f.index.to_timestamp()
    terms = ["run_mi", "steps", "sleep", "bed_sd"]
    z = (f[terms] - f[terms].mean()) / f[terms].std()
    z["rhr"] = f.rhr
    full = st.ols_hac(z, "rhr", terms, 3)
    parts = []
    names = dict(run_mi="Running volume", steps="Daily walking", sleep="Sleep length", bed_sd="Bedtime irregularity")
    for t in terms:
        reduced = st.ols_hac(z, "rhr", [x for x in terms if x != t], 3)
        v = full["terms"][t]
        parts.append(dict(key=t, name=names[t], partial_r2=full["r2"] - reduced["r2"], b=v["b"], lo=v["lo"], hi=v["hi"], p=v["p"]))
    parts.sort(key=lambda x: -x["partial_r2"])
    top = parts[0]
    c = dict(kw=dict(cat="Cross-metric relationships", headline=f"{top['name']} explains more of your resting heart rate than anything else recorded",
        finding=f"Across {len(f)} months these four behaviours explain {full['r2']:.0%} of resting HR variation. {top['name']} alone uniquely accounts for {top['partial_r2']:.0%}: one SD more moves resting HR {top['b']:+.1f} bpm.", compared=f"{len(f)} months", n=len(f), effect=f"{top['b']:+.1f} bpm per SD", uncertainty=f"95% CI {top['lo']:+.1f} to {top['hi']:+.1f} bpm",
        confounders=["Fitness work other than running", "Seasons and relocations"], limitations=["Monthly averages hide day-level causes."], next_q="Does the same model hold for HRV?", viz=dict(kind="drivers"), raw=dict(view="discover"), effect_size=min(top["partial_r2"] * 3, 1), quality=.8, novelty=1), p=top["p"])
    return dict(r2=full["r2"], n=len(f), parts=parts), [c], [("rhr driver", top["p"])]


def vo2_context(d):
    """Pair the largest fitness decline with the running behind it, year on year."""
    last = d.index[-1]
    cut = last - pd.Timedelta(days=365)
    prev = cut - pd.Timedelta(days=365)
    a, b = d[d.index > cut], d[(d.index <= cut) & (d.index > prev)]
    v_a, v_b = a.vo2max.dropna(), b.vo2max.dropna()
    if len(v_a) < 5 or len(v_b) < 5:
        return {}, [], []
    t = pd.concat([b.assign(g=0.0), a.assign(g=1.0)])[["vo2max", "g"]].dropna()
    fit = st.compare(t, "vo2max", "g", adjust=False)["unadjusted"]
    mi_a, mi_b = np.nansum(a.run_mi), np.nansum(b.run_mi)
    n_a, n_b = int(np.nansum(a.runs)), int(np.nansum(b.runs))
    falling = fit["b"] < 0 and mi_a < mi_b
    c = dict(kw=dict(cat="Fitness", headline=(f"Your VO2 max estimate fell {abs(fit['b']):.1f} points in a year in which you ran {1 - mi_a / mi_b:.0%} {'fewer miles' if mi_b else ''}" if falling else f"VO2 max estimate moved {fit['b']:+.1f} points in a year"),
        finding=f"Median estimate {v_b.median():.1f} to {v_a.median():.1f} mL/kg/min. Running went from {mi_b:.0f} miles over {n_b} runs to {mi_a:.0f} miles over {n_a}. Only {len(v_a)} estimates exist in the past year, against {len(v_b)} before.", compared="Last 365 days against the 365 before", n=len(v_a) + len(v_b), effect=f"{fit['b']:+.1f} mL/kg/min", uncertainty=f"95% CI {fit['lo']:+.1f} to {fit['hi']:+.1f}",
        confounders=["Fewer runs also means fewer estimates, and those may come from less typical workouts", "Age and body weight, which are not updated in this export"], limitations=["Apple estimates VO2 max from outdoor walks and runs. It is a device model, not a lab test."], next_q="Does the estimate climb again in the weeks after your recent run block?", viz=dict(kind="series", metric="vo2max", freq="M"), raw=dict(metric="vo2max"), effect_size=abs(fit["b"]) / 6, quality=.7, novelty=1), p=fit["p"])
    return dict(delta=fit["b"], before=float(v_b.median()), after=float(v_a.median()), miles_before=float(mi_b), miles_after=float(mi_a)), [c], [("vo2 yoy", fit["p"])]


def grade_of(q, n):
    if q is None:
        return "B"
    return "A" if q < .01 and n >= 30 else "B" if q < .05 else "C"


def run(con, d):
    """Each investigation runs alone, so a type missing from an export skips that one
    analysis and names it, rather than taking the whole engine down."""
    out, cards, tests, skipped = {}, [], [], []
    steps = [("jet", "Travel", lambda: jet_lag(con, d)),
             ("spells", "High-strain spells", lambda: strain_spells(d, [pd.Timestamp(t["date"]) for t in out.get("jet", {}).get("trips", [])])),
             ("drift", "Run drift", lambda: run_decoupling(con, d)), ("heat", "Heat penalty", lambda: heat_penalty(con)),
             ("adapt", "Training adaptation", lambda: adaptation_lag(d)), ("sleep_loss", "Sleep change", lambda: sleep_loss(d)),
             ("carry", "Short-night carryover", lambda: sleep_carryover(d)), ("late", "Evening runs", lambda: late_runs(d)),
             ("places", "Places", lambda: places(d)), ("eras", "Life eras", lambda: eras(con, d)), ("nights", "Night of week", lambda: shortest_night(d)),
             ("drivers", "Resting HR drivers", lambda: rhr_drivers(d)), ("vo2", "VO2 context", lambda: vo2_context(d))]
    for key, name, fn in steps:
        try:
            res = fn()
        except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError, StopIteration, AttributeError) as e:
            skipped.append(dict(name=name, reason=f"Not enough data ({type(e).__name__})"))
            continue
        out[key] = res[0]
        cards += res[1]
        if len(res) > 2:
            tests += res[2]
    final = []
    for c in cards:
        p = c["p"]
        q = None if p is None else float(np.nanmin([st.bh_fdr([p] + [t[1] for t in tests])[0], 1]))
        kw = c["kw"]
        n = kw.pop("n")
        final.append(card(kw.pop("cat"), kw.pop("headline"), kw.pop("finding"), kw.pop("compared"), n, kw.pop("effect"), kw.pop("uncertainty"), max(grade_of(q, n), c.get("cap", "A")), kw.pop("confounders"), kw.pop("limitations"), kw.pop("next_q"), kw.pop("viz"), tests=len(tests), **kw))
    out.update(n_tests=len(tests), skipped=skipped)
    return out, final


if __name__ == "__main__":
    import warnings
    warnings.simplefilter("ignore")
    from .data import connect, load_daily
    con = connect()
    data, cards = run(con, load_daily(con))
    for c in cards:
        print(f"[{c['grade']}] {c['category']}: {c['headline']}\n    {c['finding']}\n    {c['uncertainty']}")
    print("skipped", data["skipped"])
