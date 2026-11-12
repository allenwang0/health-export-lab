"""Normalise parsed Parquet into data/lab.duckdb.

Three decisions dominate this file, each forced by what real exports contain:

1. Time. export.xml stamps every record with the offset in force at *export*, not at
   recording, so the instant is right but the wall clock is wrong in winter and on
   trips. Local time is rebuilt from HKTimeZone metadata (see tz_segments).
2. Cumulative metrics. iPhone and Watch record the same steps. Overlap is removed by
   source priority, interval by interval (see dedupe_cumulative).
3. Sleep. Up to six apps describe a night. One source is chosen per night and named;
   sources are never blended (see build_sleep).

Missing stays NULL throughout: a day with no samples is unknown, not zero.
"""
from bisect import bisect_right
import glob
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

# Legacy zone names that devices still write; each is a link to the canonical zone.
TZ_ALIASES = {"US/Pacific": "America/Los_Angeles", "US/Mountain": "America/Denver", "US/Central": "America/Chicago",
              "US/Eastern": "America/New_York", "US/Arizona": "America/Phoenix", "US/Hawaii": "Pacific/Honolulu",
              "US/Alaska": "America/Anchorage", "GB": "Europe/London", "Japan": "Asia/Tokyo"}
OBSERVED_GAP_H = 72  # segments between observations further apart than this are "inferred"

# Higher priority wins on overlap. Lower sources only fill time the higher ones left empty.
SOURCE_PRIORITY = ["apple watch", "iphone", "mi fit"]
CUMULATIVE = {"StepCount": "steps", "DistanceWalkingRunning": "distance_mi",
              "ActiveEnergyBurned": "active_kcal", "BasalEnergyBurned": "basal_kcal",
              "FlightsClimbed": "flights"}
SLEEP_TIERS = [("Watch (stages)", "apple watch", "stages"), ("Watch", "apple watch", "asleep"),
               ("AutoSleep", "autosleep", "asleep"), ("Sleep Cycle", "sleep cycle", "asleep"),
               ("Mi Fit", "mi fit", "asleep"), ("Sleep Cycle (in bed)", "sleep cycle", "inbed"),
               ("iPhone (in bed)", "iphone", "inbed"), ("Clock (in bed)", "clock", "inbed")]
SESSION_GAP = pd.Timedelta(hours=3)

UTC_SQL = ("(substr({c},1,19)::TIMESTAMP - to_minutes((substr({c},21,1)||'1')::INT * "
           "(substr({c},22,2)::INT*60 + substr({c},24,2)::INT)))")


def utc(col):
    return UTC_SQL.format(c=col)


def short(col):
    return (f"replace(replace(replace({col},'HKQuantityTypeIdentifier',''),"
            f"'HKCategoryTypeIdentifier',''),'HKDataType','')")


def tz_segments(obs):
    """Turn (utc, tz) observations into contiguous zone segments.

    obs must be sorted by time. A zone holds from its first observation until the next
    differing one. Returns [(from_utc, to_utc, tz, n_obs, confidence)].
    """
    segs = []
    for t, z in obs:
        z = TZ_ALIASES.get(z, z)
        if segs and segs[-1][2] == z:
            segs[-1][1], segs[-1][3] = t, segs[-1][3] + 1
        else:
            segs.append([t, t, z, 1])
    out = []
    for i, (a, b, z, n) in enumerate(segs):
        end = segs[i + 1][0] if i + 1 < len(segs) else datetime(2100, 1, 1)
        out.append((a, end, z, n))
    return out


def hour_offsets(segments, first, last, fallback="UTC"):
    """UTC-hour -> offset minutes. A table join, so DST is resolved by zoneinfo once per hour.

    Before the first HKTimeZone tag the earliest observed zone is assumed and marked so.
    With no tags at all, the export's own offset (fallback) is the only evidence left.
    """
    first = first.replace(minute=0, second=0, microsecond=0)
    rows, i, t = [], 0, first
    zones = {}
    while t <= last:
        while i + 1 < len(segments) and t >= segments[i + 1][0]:
            i += 1
        if not segments:
            z, conf = fallback, "assumed"
        elif t < segments[0][0]:
            z, conf = segments[0][2], "assumed"
        else:
            a, end, z, n = segments[i]
            conf = "observed" if (end - a).total_seconds() / 3600 < OBSERVED_GAP_H or n >= 3 else "inferred"
        zi = zones.setdefault(z, ZoneInfo(z))
        off = t.replace(tzinfo=timezone.utc).astimezone(zi).utcoffset()
        rows.append((t, z, int(off.total_seconds() // 60), conf))
        t += timedelta(hours=1)
    return pd.DataFrame(rows, columns=["hour_utc", "tz", "offset_min", "tz_conf"])


def dedupe_cumulative(start, end, value, rank):
    """Remove overlap between sources, keeping priority order.

    Inputs are parallel arrays of epoch seconds, value and rank (0 = highest priority).
    Each sample keeps only the share of its value falling outside time claimed by a
    higher-priority source, assuming uniform rate within the sample. Within one source
    overlapping samples are also resolved: an earlier-starting sample keeps its span.
    Returns (seg_start, seg_end, seg_value).
    """
    order = np.lexsort((start, rank))
    claimed_s, claimed_e = [], []  # sorted, disjoint spans from higher ranks
    out_s, out_e, out_v = [], [], []
    current, pending = None, []

    def merge(pending_spans):
        nonlocal claimed_s, claimed_e
        if not pending_spans:
            return
        spans = sorted(list(zip(claimed_s, claimed_e)) + pending_spans)
        ms, me = [], []
        for a, b in spans:
            if ms and a <= me[-1]:
                me[-1] = max(me[-1], b)
            else:
                ms.append(a)
                me.append(b)
        claimed_s, claimed_e = ms, me

    for i in order:
        if rank[i] != current:
            merge(pending)
            pending, current = [], rank[i]
            own_end = -np.inf
        a, b, v = float(start[i]), float(end[i]), float(value[i])
        dur = b - a
        if dur <= 0:  # instantaneous sample: keep unless inside a claimed span
            k = bisect_right(claimed_s, a) - 1
            if not (k >= 0 and a < claimed_e[k]):
                out_s.append(a), out_e.append(a), out_v.append(v)
            continue
        cur = max(a, own_end)  # same-source overlap: time already covered by this rank
        if cur < b:
            k = max(bisect_right(claimed_s, cur) - 1, 0)
            while cur < b:
                if k >= len(claimed_s) or claimed_s[k] >= b:
                    out_s.append(cur), out_e.append(b), out_v.append(v * (b - cur) / dur)
                    break
                if claimed_e[k] <= cur:
                    k += 1
                    continue
                if claimed_s[k] > cur:
                    out_s.append(cur), out_e.append(claimed_s[k])
                    out_v.append(v * (claimed_s[k] - cur) / dur)
                cur = claimed_e[k]
                k += 1
            own_end = max(own_end, b)
        pending.append((a, b))
    return np.array(out_s), np.array(out_e), np.array(out_v)


def split_hours(seg_s, seg_e, seg_v):
    """Split segments on UTC-hour edges, proportionally. Returns (hour_epoch, value)."""
    hours, vals = [], []
    for a, b, v in zip(seg_s, seg_e, seg_v):
        h = int(a // 3600) * 3600
        if b <= h + 3600:
            hours.append(h), vals.append(v)
            continue
        dur = b - a
        cur = a
        while cur < b:
            nxt = min(h + 3600, b)
            hours.append(h), vals.append(v * (nxt - cur) / dur)
            cur, h = nxt, h + 3600
    return np.array(hours, dtype=np.int64), np.array(vals)


def union_spans(spans):
    """Merge (start, end) pairs; returns disjoint sorted spans."""
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def span_hours(spans):
    return sum((b - a).total_seconds() for a, b in spans) / 3600


def night_kind(values):
    has = set(values)
    if has & {"AsleepCore", "AsleepDeep", "AsleepREM"}:
        return "stages"
    if "AsleepUnspecified" in has:
        return "asleep"
    return "inbed"


def build_sleep(s):
    """s: DataFrame of sleep rows [source, stage, start, end] in UTC plus local offset.

    Rows are grouped into candidate sessions per source (gap < 3h). A night is keyed by
    the local date of its wake time. The source for a night is the best tier available.
    Returns (nights, naps, intervals).
    """
    ivals = []
    s = s.sort_values("start")
    for src, g in s.groupby("src_key", sort=False):
        kind_rows = g[g.stage != "InBed"]
        base = kind_rows if len(kind_rows) else g
        base = base.sort_values("start")
        groups = (base.start > base.end.cummax().shift() + SESSION_GAP).cumsum()
        for _, sess in base.groupby(groups):
            a, b = sess.start.min(), sess.end.max()
            inbed = g[(g.stage == "InBed") & (g.end > a - SESSION_GAP) & (g.start < b + SESSION_GAP)]
            kind = night_kind(sess.stage)
            yield_row = (src, kind, a, b, sess, inbed)
            ivals.append(yield_row)
    # choose per wake-date; the longest session of the best tier wins, the rest become naps
    by_date = defaultdict(list)
    tier_of = {(k, kind): i for i, (_, k, kind) in enumerate(SLEEP_TIERS)}
    for src, kind, a, b, sess, inbed in ivals:
        if (src, kind) not in tier_of:
            continue
        off = pd.Timedelta(minutes=int(sess.end_offset.iloc[-1]))
        by_date[(b + off).date()].append((tier_of[(src, kind)], src, kind, a, b, sess, inbed))
    rows, naps, stages_out = [], [], []
    for date, cands in by_date.items():
        mains = [c for c in cands if span_hours(union_spans(zip(c[5].start, c[5].end))) >= 2 or c[2] == "inbed"]
        if not mains:
            for c in cands:
                naps.append((date, SLEEP_TIERS[c[0]][0], c[3], c[4]))
            continue
        best_tier = min(c[0] for c in mains)
        best = max((c for c in mains if c[0] == best_tier), key=lambda c: c[4] - c[3])
        tier, src, kind, a, b, sess, inbed = best
        for c in cands:
            if c is not best and c[0] == best_tier and (c[4] < a or c[3] > b):
                naps.append((date, SLEEP_TIERS[c[0]][0], c[3], c[4]))
        hrs = {}
        for stage in ("AsleepCore", "AsleepDeep", "AsleepREM", "AsleepUnspecified", "Awake"):
            sp = union_spans(zip(sess[sess.stage == stage].start, sess[sess.stage == stage].end))
            hrs[stage] = span_hours(sp) if len(sp) else 0.0
        asleep_spans = union_spans(zip(sess[sess.stage != "Awake"].start, sess[sess.stage != "Awake"].end))
        asleep = span_hours(asleep_spans) if kind != "inbed" else None
        bed_spans = union_spans(zip(inbed.start, inbed.end))
        in_bed = span_hours(bed_spans) if bed_spans else None
        if in_bed is not None and asleep and in_bed < 0.8 * asleep:  # partial InBed rows, not a bed period
            in_bed = None
        wakes = int((sess.stage == "Awake").sum()) if kind == "stages" else None
        awake_gaps = sum(1 for x, y in zip(asleep_spans, asleep_spans[1:]) if (y[0] - x[1]).total_seconds() > 120)
        off_a, off_b = int(sess.start_offset.iloc[0]), int(sess.end_offset.iloc[-1])
        onset = a + pd.Timedelta(minutes=off_a)
        wake = b + pd.Timedelta(minutes=off_b)
        span = (b - a).total_seconds() / 3600
        eff = asleep / span if asleep and span > 0 and kind in ("stages", "asleep") else None
        rows.append(dict(
            wake_date=date, source=SLEEP_TIERS[tier][0], tier=tier, kind=kind, start_utc=a, end_utc=b,
            onset_local=onset, wake_local=wake, span_h=span, asleep_h=asleep, in_bed_h=in_bed,
            core_h=hrs["AsleepCore"] if kind == "stages" else None,
            deep_h=hrs["AsleepDeep"] if kind == "stages" else None,
            rem_h=hrs["AsleepREM"] if kind == "stages" else None,
            awake_h=hrs["Awake"] if kind == "stages" else None,
            efficiency=eff, wake_events=wakes if wakes is not None else awake_gaps,
            n_candidates=len({c[1] for c in cands}), tz_offset_min=off_b))
        t = sess.assign(wake_date=date, source=SLEEP_TIERS[tier][0])
        stages_out.append(t[["wake_date", "source", "stage", "start", "end", "start_offset"]])
    n = pd.DataFrame(rows).sort_values("wake_date").reset_index(drop=True)
    return n, pd.DataFrame(naps, columns=["wake_date", "source", "start_utc", "end_utc"]), \
        pd.concat(stages_out, ignore_index=True)


def validate_time(con, hourly_col="active_kcal"):
    """Score local-time reconstruction against Apple's own calendar days.

    ActivitySummary rows are keyed by device-local date. If reconstructed days are right,
    summed active energy per day should match them better than days cut with -0700.
    """
    q = """
    WITH s AS (SELECT date::DATE d, active_energy::DOUBLE v FROM activity_summaries_raw WHERE active_energy::DOUBLE>0),
    naive AS (SELECT (hour_utc - INTERVAL 7 HOUR)::DATE d, sum(value) v FROM hourly WHERE metric='active_kcal' GROUP BY 1),
    rec AS (SELECT local_date d, sum(value) v FROM hourly WHERE metric='active_kcal' GROUP BY 1)
    SELECT count(*) n,
      median(abs(rec.v-s.v)) med_abs_err_rebuilt, median(abs(naive.v-s.v)) med_abs_err_naive,
      avg(abs(rec.v-s.v)) mean_abs_err_rebuilt, avg(abs(naive.v-s.v)) mean_abs_err_naive,
      sum(CASE WHEN abs(rec.v-s.v)<=0.05*s.v THEN 1 ELSE 0 END)*1.0/count(*) within5_rebuilt,
      sum(CASE WHEN abs(naive.v-s.v)<=0.05*s.v THEN 1 ELSE 0 END)*1.0/count(*) within5_naive
    FROM s JOIN rec USING(d) JOIN naive USING(d)"""
    return con.execute(q).df().iloc[0].to_dict()


def build(root: Path):
    parsed = sorted(glob.glob(str(root / "parsed" / "*")),
                    key=lambda p: json.loads(Path(p, "report.json").read_text()).get("export_id"))
    parsed.sort(key=lambda p: Path(p, "report.json").stat().st_mtime)
    db = root / "lab.tmp.duckdb"
    db.unlink(missing_ok=True)
    con = duckdb.connect(str(db))
    con.execute("SET TimeZone='UTC'")

    def src(table):
        files = [f"{p}/{table}/*.parquet" for p in parsed]
        return f"read_parquet({files!r}, filename=true)"

    # latest export wins when the same sample appears in several exports
    con.execute(f"""CREATE TABLE raw AS
      SELECT DISTINCT ON (type, source_name, start_date, end_date, value, creation_date) *
      FROM (SELECT * EXCLUDE filename FROM {src('records')})""")
    n_raw = con.execute(f"SELECT count(*) FROM {src('records')}").fetchall()[0][0]
    n_dist = con.execute("SELECT count(*) FROM raw").fetchall()[0][0]

    obs = con.execute(f"""SELECT {utc('start_date')} t, metadata->>'$.HKTimeZone' z FROM raw
                          WHERE metadata LIKE '%HKTimeZone%' UNION ALL
                          SELECT {utc('start_date')} t, metadata->>'$.HKTimeZone' z FROM {src('workouts')}
                          WHERE metadata LIKE '%HKTimeZone%' ORDER BY t""").fetchall()
    obs = [o for o in obs if o[1]]
    segs = tz_segments(obs)
    first, last = con.execute(f"SELECT min({utc('start_date')}), max({utc('end_date')}) FROM raw").fetchall()[0]
    sample = con.execute("SELECT start_date FROM raw LIMIT 1").fetchall()[0][0]
    hours = int(sample[20:23])  # '-0700' -> -7; Etc/GMT signs run opposite to ISO offsets
    fallback = f"Etc/GMT{-hours:+d}" if hours else "UTC"
    offs = hour_offsets(segs, first, last + timedelta(hours=2), fallback)
    con.register("offs", offs)
    con.execute("CREATE TABLE hour_tz AS SELECT * FROM offs")
    con.execute("CREATE TABLE tz_segments AS SELECT * FROM (SELECT min(hour_utc) start_utc, max(hour_utc) end_utc,"
                "tz, any_value(tz_conf) conf, count(*)/24.0 n_days, grp FROM (SELECT *, row_number() OVER (ORDER BY hour_utc)"
                "-row_number() OVER (PARTITION BY tz ORDER BY hour_utc) grp FROM hour_tz) GROUP BY tz, grp) ORDER BY start_utc")

    con.execute(f"""CREATE TABLE samples AS
      SELECT {short('type')} type, CASE WHEN r.device LIKE '%name:%' THEN regexp_extract(r.device,'name:([^,]*)',1) END device_name,
        regexp_extract(r.device,'hardware:([^,]*(?:,[0-9]+)?)',1) hardware,
        source_name, source_version, unit, value value_raw, TRY_CAST(value AS DOUBLE) value,
        replace(value,'HKCategoryValueSleepAnalysis','') value_label,
        {utc('r.start_date')} start_utc, {utc('r.end_date')} end_utc, {utc('r.creation_date')} created_utc, h.offset_min, h.tz, h.tz_conf,
        {utc('r.start_date')} + to_minutes(h.offset_min::BIGINT) start_local,
        ({utc('r.start_date')} + to_minutes(h.offset_min::BIGINT))::DATE local_date, metadata
      FROM raw r LEFT JOIN hour_tz h ON h.hour_utc = date_trunc('hour', {utc('r.start_date')})""")
    assert con.execute("SELECT count(*) FROM samples WHERE offset_min IS NULL").fetchall()[0][0] == 0

    # dedupe cumulative types to hourly buckets ----------------------------------------------
    frames, overlap = [], []
    for typ, metric in CUMULATIVE.items():
        d = con.execute("""SELECT source_name, epoch(start_utc) s, epoch(end_utc) e, value
                           FROM samples WHERE type=? AND value IS NOT NULL""", [typ]).df()
        if d.empty:
            continue
        low = d.source_name.str.lower().str.replace("\xa0", " ")

        def rank(n):
            return next((i for i, k in enumerate(SOURCE_PRIORITY) if k in n), len(SOURCE_PRIORITY))
        names = {n: rank(n) for n in low.unique()}
        r = low.map(names).to_numpy()
        seg_s, seg_e, seg_v = dedupe_cumulative(d.s.to_numpy(float), d.e.to_numpy(float), d.value.to_numpy(float), r)
        hrs, vals = split_hours(seg_s, seg_e, seg_v)
        f = pd.DataFrame({"h": hrs, "v": vals}).groupby("h", as_index=False).v.sum()
        f["metric"] = metric
        frames.append(f)
        overlap.append((metric, float(d.value.sum()), float(vals.sum())))
    hourly = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame({"h": pd.Series(dtype="int64"), "v": pd.Series(dtype="float64"), "metric": pd.Series(dtype="str")})
    hourly["hour_utc"] = pd.to_datetime(hourly.h, unit="s")
    con.register("hf", hourly[["hour_utc", "metric", "v"]])
    con.execute("""CREATE TABLE hourly AS SELECT f.hour_utc, f.metric, f.v value, h.offset_min, h.tz,
        f.hour_utc + to_minutes(h.offset_min::BIGINT) local_ts,
        (f.hour_utc + to_minutes(h.offset_min::BIGINT))::DATE local_date,
        hour(f.hour_utc + to_minutes(h.offset_min::BIGINT)) local_hour
        FROM hf f JOIN hour_tz h ON h.hour_utc=f.hour_utc""")
    con.execute("CREATE TABLE dedupe_report(metric VARCHAR, raw_sum DOUBLE, deduped_sum DOUBLE)")
    con.executemany("INSERT INTO dedupe_report VALUES (?,?,?)", overlap)

    # sleep --------------------------------------------------------------------------------
    sl = con.execute("""SELECT source_name, value_label stage, start_utc start, end_utc "end", offset_min start_offset
                        FROM samples WHERE type='SleepAnalysis'""").df()
    if sl.empty:  # an export with no sleep at all still gets empty, correctly typed tables
        con.execute("""CREATE TABLE sleep_nights(wake_date DATE, source VARCHAR, tier INT, kind VARCHAR, start_utc TIMESTAMP, end_utc TIMESTAMP, onset_local TIMESTAMP, wake_local TIMESTAMP,
            span_h DOUBLE, asleep_h DOUBLE, in_bed_h DOUBLE, core_h DOUBLE, deep_h DOUBLE, rem_h DOUBLE, awake_h DOUBLE, efficiency DOUBLE, wake_events INT, n_candidates INT, tz_offset_min INT);
            CREATE TABLE sleep_naps(wake_date DATE, source VARCHAR, start_utc TIMESTAMP, end_utc TIMESTAMP);
            CREATE TABLE sleep_stages(wake_date DATE, source VARCHAR, stage VARCHAR, start TIMESTAMP, "end" TIMESTAMP, start_offset INT);
            CREATE TABLE sleep_cross(wake_date DATE, source VARCHAR, asleep_h DOUBLE)""")
    else:
        sl["src_key"] = sl.source_name.str.lower().str.replace("\xa0", " ").map(
            lambda n: next((k for _, k, _k in SLEEP_TIERS if k in n), n))
        ho = offs.set_index("hour_utc").offset_min
        sl["end_offset"] = sl["end"].dt.floor("h").map(ho).fillna(sl.start_offset).astype(int)
        sl.loc[sl.stage == "Asleep", "stage"] = "AsleepUnspecified"
        nights, naps, stages = build_sleep(sl)
        con.register("n_", nights), con.register("p_", naps), con.register("st_", stages)
        con.execute('CREATE TABLE sleep_nights AS SELECT * FROM n_')
        con.execute('CREATE TABLE sleep_naps AS SELECT * FROM p_')
        con.execute('CREATE TABLE sleep_stages AS SELECT * FROM st_')
        # every source's nightly total, kept so bias between sources can be measured
        cross = []
        for key in ("apple watch", "autosleep", "sleep cycle", "mi fit"):
            g = sl[(sl.src_key == key) & (sl.stage != "InBed") & (sl.stage != "Awake")]
            if g.empty:
                continue
            wake = (g["end"] + pd.to_timedelta(g.end_offset, unit="m") + pd.Timedelta(hours=6)).dt.date
            for d, x in g.groupby(wake):
                cross.append((d, key, span_hours(union_spans(zip(x.start, x["end"])))))
        con.register("c_", pd.DataFrame(cross, columns=["wake_date", "source", "asleep_h"]))
        con.execute("CREATE TABLE sleep_cross AS SELECT * FROM c_")

    # workouts -----------------------------------------------------------------------------
    con.execute(f"""CREATE TABLE workouts AS
      SELECT w.workout_id, replace(activity_type,'HKWorkoutActivityType','') type, source_name, source_version,
        regexp_extract(device,'name:([^,]*)',1) device_name,
        {utc('start_date')} start_utc, {utc('end_date')} end_utc,
        duration::DOUBLE * CASE duration_unit WHEN 'min' THEN 1 WHEN 'hr' THEN 60 WHEN 's' THEN 1.0/60 ELSE NULL END duration_min,
        (metadata->>'$.HKIndoorWorkout')='1' indoor,
        regexp_extract(metadata->>'$.HKElevationAscended','([0-9.]+)',1) elev_raw,
        metadata->>'$.HKElevationAscended' elev_label,
        TRY_CAST(regexp_extract(metadata->>'$.HKWeatherTemperature','([0-9.]+)',1) AS DOUBLE) temp_f,
        TRY_CAST(regexp_extract(metadata->>'$.HKWeatherHumidity','([0-9.]+)',1) AS DOUBLE)/100 humidity_pct,
        TRY_CAST(regexp_extract(metadata->>'$.HKAverageMETs','([0-9.]+)',1) AS DOUBLE) mets,
        metadata->>'$.HKWorkoutBrandName' brand, metadata metadata_json, h.tz, h.offset_min,
        {utc('start_date')} + to_minutes(h.offset_min::BIGINT) start_local,
        ({utc('start_date')} + to_minutes(h.offset_min::BIGINT))::DATE local_date
      FROM (SELECT DISTINCT ON (workout_id) * FROM {src('workouts')}) w
      LEFT JOIN hour_tz h ON h.hour_utc=date_trunc('hour',{utc('start_date')})""")
    con.execute(f"CREATE TABLE workout_stats AS SELECT DISTINCT workout_id, {short('type')} type, unit, TRY_CAST(sum AS DOUBLE) sum, TRY_CAST(average AS DOUBLE) avg, TRY_CAST(minimum AS DOUBLE) min, TRY_CAST(maximum AS DOUBLE) max FROM (SELECT * EXCLUDE filename FROM {src('workout_stats')})")
    con.execute(f"CREATE TABLE workout_events AS SELECT DISTINCT workout_id, replace(type,'HKWorkoutEventType','') type, {utc('date')} ts_utc, TRY_CAST(duration AS DOUBLE) duration, duration_unit FROM (SELECT * EXCLUDE filename FROM {src('workout_events')})")
    con.execute(f"CREATE TABLE workout_routes AS SELECT DISTINCT workout_id, route_path FROM (SELECT * EXCLUDE filename FROM {src('workout_routes')})")
    con.execute(f"""CREATE TABLE route_points AS SELECT DISTINCT route_path, time::TIMESTAMP ts_utc, lat::DOUBLE lat, lon::DOUBLE lon,
        TRY_CAST(ele AS DOUBLE) ele, TRY_CAST(speed AS DOUBLE) speed, TRY_CAST(h_acc AS DOUBLE) h_acc
        FROM (SELECT * EXCLUDE filename FROM {src('route_points')}) ORDER BY route_path, ts_utc""")
    con.execute(f"CREATE TABLE activity_summaries_raw AS SELECT DISTINCT * EXCLUDE filename FROM {src('activity_summaries')}")
    con.execute(f"CREATE TABLE hrv_beats AS SELECT DISTINCT * EXCLUDE filename FROM {src('hrv_beats')}")
    con.execute(f"CREATE TABLE ecg AS SELECT DISTINCT * EXCLUDE filename FROM {src('ecg')} WHERE key NOT IN ('Name','Date of Birth')")

    # per-workout HR profile from raw samples. Workout stats only exist for newer watchOS.
    con.execute("""CREATE TABLE workout_hr AS SELECT w.workout_id, count(*) n, avg(s.value) avg_hr, max(s.value) max_hr
        FROM workouts w JOIN samples s ON s.type='HeartRate' AND s.start_utc BETWEEN w.start_utc AND w.end_utc GROUP BY 1""")
    con.execute("""CREATE TABLE workout_facts AS
      WITH st AS (SELECT workout_id,
         max(CASE WHEN type='DistanceWalkingRunning' THEN CASE unit WHEN 'mi' THEN sum WHEN 'km' THEN sum*0.621371 END END) d_run,
         max(CASE WHEN type='DistanceCycling' THEN sum END) d_cyc, max(CASE WHEN type='DistanceSwimming' THEN sum/1760 END) d_swim,
         max(CASE WHEN type='ActiveEnergyBurned' THEN sum END) kcal, max(CASE WHEN type='StepCount' THEN sum END) steps,
         max(CASE WHEN type='HeartRate' THEN avg END) hr_avg, max(CASE WHEN type='HeartRate' THEN max END) hr_max,
         max(CASE WHEN type='RunningPower' THEN avg END) power, max(CASE WHEN type='RunningStrideLength' THEN avg END) stride,
         max(CASE WHEN type='RunningGroundContactTime' THEN avg END) gct, max(CASE WHEN type='RunningVerticalOscillation' THEN avg END) vosc,
         max(CASE WHEN type='RunningSpeed' THEN avg END) speed_mph FROM workout_stats GROUP BY 1)
      SELECT w.*, coalesce(d_run,d_cyc,d_swim) distance_mi, kcal, st.steps, -- most older workouts hold only a few HR samples, too few to call an average
        coalesce(hr_avg, CASE WHEN h.n>=20 THEN h.avg_hr END) avg_hr, coalesce(hr_max, CASE WHEN h.n>=20 THEN h.max_hr END) max_hr,
        h.n hr_samples, CASE WHEN hr_avg IS NOT NULL THEN 'workout stat' WHEN h.n>=20 THEN 'samples' END hr_basis,
        power, stride, gct, vosc,
        duration_min/nullif(coalesce(d_run,d_cyc,d_swim),0) pace_min_mi,
        CASE WHEN st.steps IS NOT NULL AND duration_min>0 THEN st.steps/duration_min END cadence,
        CASE WHEN elev_label LIKE '% cm' THEN TRY_CAST(elev_raw AS DOUBLE)/30.48 WHEN elev_label LIKE '% m' THEN TRY_CAST(elev_raw AS DOUBLE)*3.28084 WHEN elev_label LIKE '% ft' THEN TRY_CAST(elev_raw AS DOUBLE) END elev_ft,
        r.route_path
      FROM workouts w LEFT JOIN st USING(workout_id) LEFT JOIN workout_hr h USING(workout_id) LEFT JOIN workout_routes r USING(workout_id)""")

    # daily wide table -----------------------------------------------------------------------
    con.execute("""CREATE TABLE daily AS
      WITH spine AS (SELECT unnest(generate_series((SELECT min(local_date) FROM samples), (SELECT max(local_date) FROM samples), INTERVAL 1 DAY))::DATE d),
      h AS (SELECT local_date d, sum(value) FILTER (metric='steps') steps, sum(value) FILTER (metric='distance_mi') distance_mi,
              sum(value) FILTER (metric='flights') flights, sum(value) FILTER (metric='active_kcal') active_kcal,
              sum(value) FILTER (metric='basal_kcal') basal_kcal, count(DISTINCT local_hour) FILTER (metric='steps') step_hours FROM hourly GROUP BY 1),
      hr AS (SELECT local_date d, avg(value) hr_mean, min(value) hr_min, max(value) hr_max, count(*) hr_n,
              count(DISTINCT hour(start_local)) hr_hours,
              avg(value) FILTER (metadata LIKE '%MotionContext": "1"%') hr_sedentary_mean FROM samples WHERE type='HeartRate' GROUP BY 1),
      q AS (SELECT local_date d,
              avg(value) FILTER (type='RestingHeartRate') rhr, avg(value) FILTER (type='WalkingHeartRateAverage') walking_hr,
              median(value) FILTER (type='HeartRateVariabilitySDNN') hrv_sdnn, count(*) FILTER (type='HeartRateVariabilitySDNN') hrv_n,
              median(value) FILTER (type='RespiratoryRate') resp_rate, last(value ORDER BY start_utc) FILTER (type='VO2Max') vo2max,
              avg(value) FILTER (type='BodyMass') weight_lb, median(value) FILTER (type='WalkingSpeed') walk_speed_mph,
              median(value) FILTER (type='WalkingStepLength') step_length_in, median(value) FILTER (type='WalkingDoubleSupportPercentage')*100 double_support_pct,
              median(value) FILTER (type='WalkingAsymmetryPercentage')*100 asymmetry_pct, median(value) FILTER (type='StairAscentSpeed') stair_up_fps,
              median(value) FILTER (type='StairDescentSpeed') stair_down_fps, sum(value) FILTER (type='DietaryWater') water_ml,
              avg(value) FILTER (type='HeadphoneAudioExposure') headphone_db, avg(value) FILTER (type='PhysicalEffort') effort,
              sum(value) FILTER (type='AppleStandTime') stand_min, sum(value) FILTER (type='AppleExerciseTime') exercise_min_watch,
              count(*) FILTER (type='MindfulSession') mindful_n, max(value) FILTER (type='AppleSleepingWristTemperature') wrist_temp_f,
              max(value) FILTER (type='HeartRateRecoveryOneMinute') hrr_1min, max(value) FILTER (type='AppleWalkingSteadiness')*100 steadiness_pct
            FROM samples GROUP BY 1),
      w AS (SELECT local_date d, count(*) workouts, sum(duration_min) workout_min, sum(kcal) workout_kcal,
              sum(distance_mi) FILTER (type='Running') run_mi, sum(duration_min) FILTER (type='Running') run_min,
              count(*) FILTER (type='Running') runs, min(extract('hour' FROM start_local)) FILTER (type='Running') run_hour FROM workout_facts GROUP BY 1),
      a AS (SELECT date::DATE d, active_energy::DOUBLE ring_move_kcal, exercise_time::DOUBLE ring_exercise_min, stand_hours::DOUBLE ring_stand_h,
              active_energy_goal::DOUBLE move_goal FROM activity_summaries_raw),
      s AS (SELECT wake_date d, source sleep_source, asleep_h, in_bed_h, core_h, deep_h, rem_h, awake_h, efficiency sleep_eff, wake_events,
              hour(onset_local)+minute(onset_local)/60.0 bed_clock, hour(wake_local)+minute(wake_local)/60.0 wake_clock FROM sleep_nights),
      tzd AS (SELECT (hour_utc+to_minutes(offset_min::BIGINT))::DATE d, any_value(tz) tz, any_value(tz_conf) tz_conf FROM hour_tz GROUP BY 1)
      SELECT spine.d AS date, year(spine.d) yr, month(spine.d) mo, isodow(spine.d) dow, isodow(spine.d)>=6 weekend, tzd.tz, tzd.tz_conf,
        h.* EXCLUDE d, hr.* EXCLUDE d, q.* EXCLUDE d, s.* EXCLUDE d, a.* EXCLUDE d,
        -- zero is only claimed once a tracker that logs workouts exists; earlier days stay NULL
        CASE WHEN spine.d >= (SELECT min(local_date) FROM workout_facts) THEN coalesce(w.workouts,0) END workouts,
        CASE WHEN spine.d >= (SELECT min(local_date) FROM workout_facts) THEN coalesce(w.workout_min,0) END workout_min,
        w.workout_kcal,
        CASE WHEN spine.d >= (SELECT min(local_date) FROM workout_facts WHERE type='Running') THEN coalesce(w.run_mi,0) END run_mi,
        CASE WHEN spine.d >= (SELECT min(local_date) FROM workout_facts WHERE type='Running') THEN coalesce(w.run_min,0) END run_min,
        CASE WHEN spine.d >= (SELECT min(local_date) FROM workout_facts WHERE type='Running') THEN coalesce(w.runs,0) END runs, w.run_hour
      FROM spine LEFT JOIN h ON h.d=spine.d LEFT JOIN hr ON hr.d=spine.d LEFT JOIN q ON q.d=spine.d LEFT JOIN s ON s.d=spine.d
      LEFT JOIN a ON a.d=spine.d LEFT JOIN w ON w.d=spine.d LEFT JOIN tzd ON tzd.d=spine.d ORDER BY 1""")

    # overnight physiology uses each night's own span, so it respects naps and travel
    con.execute("""CREATE TABLE sleep_physio AS SELECT n.wake_date, avg(s.value) FILTER (s.type='HeartRate') sleep_hr, min(s.value) FILTER (s.type='HeartRate') sleep_hr_min,
        count(*) FILTER (s.type='HeartRate') sleep_hr_n, avg(s.value) FILTER (s.type='RespiratoryRate') sleep_resp
        FROM sleep_nights n JOIN samples s ON s.start_utc BETWEEN n.start_utc AND n.end_utc AND s.type IN ('HeartRate','RespiratoryRate') GROUP BY 1""")
    con.execute("""ALTER TABLE daily ADD COLUMN sleep_hr DOUBLE; ALTER TABLE daily ADD COLUMN sleep_resp DOUBLE;
        UPDATE daily SET sleep_hr=p.sleep_hr, sleep_resp=p.sleep_resp FROM sleep_physio p WHERE p.wake_date=daily.date AND p.sleep_hr_n>=5""")

    # Comparable series. Wear time varies across the years, so anything the Watch
    # accumulates over a day is only comparable on days it was actually worn; sleep is
    # only comparable within the Watch tiers, because other sources score it longer.
    con.execute("""ALTER TABLE daily ADD COLUMN asleep_watch_h DOUBLE; ALTER TABLE daily ADD COLUMN active_kcal_worn DOUBLE;
        ALTER TABLE daily ADD COLUMN stand_min_worn DOUBLE; ALTER TABLE daily ADD COLUMN worn_day BOOLEAN;
        UPDATE daily SET asleep_watch_h = CASE WHEN sleep_source LIKE 'Watch%' THEN asleep_h END,
          worn_day = coalesce(hr_hours,0) >= 14,
          active_kcal_worn = CASE WHEN coalesce(hr_hours,0)>=14 THEN active_kcal END,
          stand_min_worn = CASE WHEN coalesce(hr_hours,0)>=14 THEN stand_min END""")
    con.execute("""CREATE TABLE device_periods AS SELECT coalesce(nullif(hardware,''),device_name,'(app)') device, source_name,
        replace(source_name,chr(160),' ') source, min(local_date) first_day, max(local_date) last_day, count(*) n, count(DISTINCT type) n_types
        FROM samples GROUP BY 1,2 ORDER BY first_day""")
    con.execute("""CREATE TABLE type_inventory AS SELECT type, any_value(unit) unit, count(DISTINCT unit) n_units, count(*) n, min(local_date) first_day,
        max(local_date) last_day, count(DISTINCT local_date) n_days, count(DISTINCT source_name) n_sources, string_agg(DISTINCT replace(source_name,chr(160),' '), ', ') sources,
        min(value) min_value, max(value) max_value, median(value) median_value, count(value) n_numeric FROM samples GROUP BY 1 ORDER BY n DESC""")

    # meta -----------------------------------------------------------------------------------
    report = json.loads(Path(parsed[-1], "report.json").read_text())
    report.update(records_seen=n_raw, records_distinct=n_dist, exports=len(parsed),
                  time_validation={k: None if v is None or pd.isna(v) else float(v) for k, v in validate_time(con).items()})
    con.execute("CREATE TABLE meta(key VARCHAR, value VARCHAR)")
    con.execute("INSERT INTO meta VALUES ('report', ?)", [json.dumps(report)])
    con.execute("DROP TABLE raw")
    con.close()
    db.replace(root / "lab.duckdb")
    print(json.dumps(report["time_validation"], indent=1))


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "data"))
