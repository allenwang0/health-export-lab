"""SYNTHETIC Apple Health export. No value here comes from a real person.

    python -m healthlab.synth demo-data/export.zip [--days 1095] [--seed 0]

Writes apple_health_export/export.xml inside a zip, in the element and attribute shape Apple
uses, and writes truth.json beside it. The export plants every trap healthlab handles:

- every timestamp carries a fixed -0700 offset (the offset at export time), whatever the zone
  was when the sample was recorded;
- an early era with no HKTimeZone tags, lived in a different zone from the first tagged one;
- a move between zones with DST, two long trips, and a "US/Eastern" alias on phone records;
- iPhone and Watch counting the same steps over the same hours;
- one night told by Watch stages, AutoSleep (about an hour longer) and an iPhone in-bed record;
- Watch wear that is full, partial or absent by day, and falls in the last era;
- five days with no data at all;
- runs whose heart rate is sparse (older), dense (middle) or summarised by a statistic (newer);
- source names containing a non-breaking space, as real device names do;
- blood pressure stored as a Correlation with nested Records;
- ActivitySummary rows keyed by the true local date, so time reconstruction can be scored.
"""
import argparse
import json
import random
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import quoteattr
from zoneinfo import ZoneInfo

EXPORT_OFFSET = timezone(timedelta(hours=-7))
START = date(2022, 1, 3)
WATCH = "Synthetic Apple\xa0Watch"
PHONE = "Synthetic iPhone"
WATCH_DEV = "<<HKDevice: 0x0>, name:Apple Watch, manufacturer:Apple Inc., model:Watch, hardware:Watch6,1, software:9.0>"
PHONE_DEV = "<<HKDevice: 0x0>, name:iPhone, manufacturer:Apple Inc., model:iPhone, hardware:iPhone13,2, software:16.0>"
Q, C = "HKQuantityTypeIdentifier", "HKCategoryTypeIdentifier"
SLEEP = C + "SleepAnalysis"
V = "HKCategoryValueSleepAnalysis"
STAGE_CYCLE = [("AsleepCore", 35), ("AsleepDeep", 20), ("AsleepCore", 20), ("AsleepREM", 22), ("Awake", 3)]


def eras(days):
    """Day index -> plan. Era edges are fractions of the record so a short export keeps every trap."""
    def at(f):
        return int(f * days)
    madrid, tokyo, gap = range(at(.55), at(.55) + 14), range(at(.73), at(.73) + 10), range(at(.48), at(.48) + 5)
    out = []
    for i in range(days):
        zone = "America/Halifax" if i < at(.08) else "America/New_York" if i < at(.41) else "America/Boise"
        zone = "Europe/Madrid" if i in madrid else "Asia/Tokyo" if i in tokyo else zone
        out.append(dict(zone=zone, tagged=i >= at(.08), watch=i >= at(.08), gap=i in gap, stages=i >= at(.27),
                        hr_tier="sparse" if i < at(.46) else "dense" if i < at(.68) else "stat", late=i >= at(.82)))
    return out


def stamp(t):
    return t.astimezone(EXPORT_OFFSET).strftime("%Y-%m-%d %H:%M:%S %z")


def meta(d):
    return "".join(f"<MetadataEntry key={quoteattr(k)} value={quoteattr(str(v))}/>" for k, v in d.items())


def rec(type_, src, a, b, value, unit=None, device=None, md=None, inner=""):
    u = f" unit={quoteattr(unit)}" if unit else ""
    dv = f" device={quoteattr(device)}" if device else ""
    body = meta(md or {}) + inner
    return (f' <Record type="{type_}" sourceName={quoteattr(src)} sourceVersion="16.0"{dv}{u} creationDate="{stamp(b)}" '
            f'startDate="{stamp(a)}" endDate="{stamp(b)}" value={quoteattr(str(value))}' + (f">{body}</Record>\n" if body else "/>\n"))


def local(d, hour, minute=0, zone="UTC"):
    return datetime.combine(d, time(hour, minute), ZoneInfo(zone)).astimezone(timezone.utc)


def generate(path, days=1095, seed=0):
    """Write the zip and truth.json. Returns the truth dict."""
    rng = random.Random(seed)
    plan = eras(days)
    status = []
    for p in plan:
        if not p["watch"] or p["gap"]:
            status.append("none")
            continue
        r = rng.random()
        full, partial = (.45, .25) if p["late"] else (.9, .07)
        status.append("full" if r < full else "partial" if r < full + partial else "off")
    for i in range(days - 1):  # a travel day: a short morning on the phone, then the flight
        if plan[i + 1]["zone"] != plan[i]["zone"]:
            status[i] = "travel"
    truth = dict(synthetic=True, seed=seed, days=days, start=str(START), export_offset="-0700", daily={}, nights={}, workouts=[],
                 raw_step_sum=0.0, bp_readings=0)
    lines = []
    w = lines.append
    for i, p in enumerate(plan):
        d, z = START + timedelta(days=i), p["zone"]
        day = truth["daily"][str(d)] = dict(zone=z, tagged=p["tagged"], wear=status[i], gap=p["gap"], steps=None, active_kcal=None)
        if p["gap"]:
            continue
        tag = {"HKTimeZone": z} if p["tagged"] else {}
        phone_tag = {"HKTimeZone": "US/Eastern"} if p["tagged"] and z == "America/New_York" else tag
        worn_hours = range(7, 24) if status[i] == "full" else range(10, 15) if status[i] == "partial" else range(0)

        # sleep that ends this morning: the night belongs to this wake date
        prev_worn = i > 0 and status[i - 1] == "full"
        arrived = i > 0 and plan[i - 1]["zone"] != z
        workout_spans, hr_lines = [], []
        bed = local(d - timedelta(days=1), 23, 0, z) + timedelta(minutes=rng.randint(-40, 40))
        wake = bed + timedelta(minutes=rng.randint(360, 500))
        if not arrived:  # the night of a flight leaves no sleep record
            night = truth["nights"][str(wake.astimezone(ZoneInfo(z)).date())] = dict(watch_asleep_h=None, autosleep_h=None, kind=None)
            w(rec(SLEEP, PHONE, bed - timedelta(minutes=20), wake + timedelta(minutes=5), V + "InBed", device=PHONE_DEV, md=phone_tag))
        if prev_worn and not arrived:
            if p["stages"]:
                t, k, asleep = bed, 0, 0.0
                while t < wake:
                    stage, mins = STAGE_CYCLE[k % len(STAGE_CYCLE)]
                    e = min(t + timedelta(minutes=max(1, mins + rng.randint(-4, 4))), wake)
                    w(rec(SLEEP, WATCH, t, e, V + stage, device=WATCH_DEV, md=tag))
                    asleep += 0 if stage == "Awake" else (e - t).total_seconds() / 3600
                    t, k = e, k + 1
                night.update(watch_asleep_h=asleep, kind="stages")
            else:
                w(rec(SLEEP, WATCH, bed, wake, V + "Asleep", device=WATCH_DEV, md=tag))
                night.update(watch_asleep_h=(wake - bed).total_seconds() / 3600, kind="asleep")
            a_s, a_e = bed - timedelta(minutes=10), wake + timedelta(minutes=50)
            w(rec(SLEEP, "AutoSleep", a_s, a_e, V + "AsleepUnspecified"))
            night["autosleep_h"] = (a_e - a_s).total_seconds() / 3600
            t = bed
            while t < wake:
                hr_lines.append((t, 50 + rng.randint(0, 10), 0))
                t += timedelta(minutes=15)
            w(rec(Q + "RespiratoryRate", WATCH, bed + timedelta(hours=2), bed + timedelta(hours=2, minutes=1), round(rng.gauss(14.5, .6), 1), "count/min", WATCH_DEV))

        # a run every third fully worn day
        energy = {h: 0.0 for h in [*(range(0, 6) if prev_worn else ()), *worn_hours]}
        if status[i] == "full" and i % 3 == 0:
            a = local(d, 17, 30, z) if rng.random() < .7 else local(d, 12, 15, z)
            mins = rng.randint(25, 55)
            b = a + timedelta(minutes=mins)
            miles = round(mins / rng.uniform(8.5, 10.5), 3)
            samples = []
            if p["hr_tier"] == "sparse":
                samples = [(a + timedelta(minutes=m), 140 + rng.randint(0, 20)) for m in range(5, mins, 10)]
            else:
                samples = [(a + timedelta(seconds=s), round(135 + 30 * s / (mins * 60) + rng.gauss(0, 3))) for s in range(0, mins * 60, 30)]
            for t, v in samples:
                hr_lines.append((t, v, 2))
            mean = sum(v for _, v in samples) / len(samples)
            stat_avg = round(mean, 1)
            for m in range(mins):
                h = (a + timedelta(minutes=m)).astimezone(ZoneInfo(z)).hour
                energy[h] = energy.get(h, 0.0) + 10.0
            st = (f'<WorkoutStatistics type="{Q}DistanceWalkingRunning" startDate="{stamp(a)}" endDate="{stamp(b)}" sum="{miles}" unit="mi"/>'
                  f'<WorkoutStatistics type="{Q}ActiveEnergyBurned" startDate="{stamp(a)}" endDate="{stamp(b)}" sum="{mins * 10}" unit="kcal"/>')
            if p["hr_tier"] == "stat":
                st += (f'<WorkoutStatistics type="{Q}HeartRate" startDate="{stamp(a)}" endDate="{stamp(b)}" average="{stat_avg}" '
                       f'minimum="{min(v for _, v in samples)}" maximum="{max(v for _, v in samples)}" unit="count/min"/>')
            temp = round(rng.uniform(40, 90))
            md = dict(tag, HKIndoorWorkout=0, HKWeatherTemperature=f"{temp} degF", HKWeatherHumidity=f"{rng.randint(30, 90)} %",
                      HKElevationAscended=f"{rng.randint(500, 6000)} cm")
            ev = (f'<WorkoutEvent type="HKWorkoutEventTypePause" date="{stamp(a + timedelta(minutes=10))}"/>'
                  f'<WorkoutEvent type="HKWorkoutEventTypeResume" date="{stamp(a + timedelta(minutes=11))}"/>')
            w(f' <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="{mins}" durationUnit="min" sourceName={quoteattr(WATCH)} '
              f'sourceVersion="9.0" device={quoteattr(WATCH_DEV)} creationDate="{stamp(b)}" startDate="{stamp(a)}" endDate="{stamp(b)}">{meta(md)}{ev}{st}</Workout>\n')
            truth["workouts"].append(dict(date=str(d), start_utc=a.strftime("%Y-%m-%d %H:%M:%S"), tier=p["hr_tier"], n_hr=len(samples),
                                          sample_mean=mean, avg_hr=None if p["hr_tier"] == "sparse" else stat_avg if p["hr_tier"] == "stat" else mean,
                                          temp_f=temp, miles=miles))
            workout_spans.append((a, b))
            w(rec(Q + "VO2Max", WATCH, b, b, round(42 + 2 * i / days + rng.gauss(0, .4), 2), "mL/min·kg", WATCH_DEV))

        # steps: the phone counts every active hour; the Watch counts the hours it is worn, a little higher
        steps = 0.0
        for h in range(8, 13) if status[i] == "travel" else range(8, 22):
            a = local(d, h, 0, z)
            ph = rng.randint(100, 900)
            w(rec(Q + "StepCount", PHONE, a, a + timedelta(hours=1), ph, "count", PHONE_DEV))
            w(rec(Q + "DistanceWalkingRunning", PHONE, a, a + timedelta(hours=1), round(ph * .00043, 4), "mi", PHONE_DEV))
            truth["raw_step_sum"] += ph
            if h in worn_hours:
                halves = [round(ph * .54), round(ph * .54)]
                for k, v in enumerate(halves):
                    s = a + timedelta(minutes=30 * k)
                    w(rec(Q + "StepCount", WATCH, s, s + timedelta(minutes=30), v, "count", WATCH_DEV))
                truth["raw_step_sum"] += sum(halves)
                steps += sum(halves)
            else:
                steps += ph
        day["steps"] = steps
        if i % 2 == 0:
            a = local(d, 12, 0, z)
            w(rec(Q + "FlightsClimbed", PHONE, a, a + timedelta(minutes=5), rng.randint(1, 12), "count", PHONE_DEV))

        # daytime heart rate every 15 minutes, never inside a workout
        for h in worn_hours:
            for m in (0, 15, 30, 45):
                t = local(d, h, m, z)
                if not any(a <= t <= b for a, b in workout_spans):
                    hr_lines.append((t, 64 + rng.randint(0, 26), 1))
        for t, v, ctx in hr_lines:
            w(rec(Q + "HeartRate", WATCH, t, t, v, "count/min", WATCH_DEV, {"HKMetadataKeyHeartRateMotionContext": ctx}))

        if energy:
            total = 0.0
            for h in sorted(energy):
                energy[h] += round(rng.uniform(1, 4) if h < 7 or h == 23 else rng.uniform(8, 40), 3)
                a = local(d, h, 0, z)
                w(rec(Q + "ActiveEnergyBurned", WATCH, a, a + timedelta(hours=1), round(energy[h], 3), "Cal", WATCH_DEV))
                total += round(energy[h], 3)
            day["active_kcal"] = total
            w(f' <ActivitySummary dateComponents="{d}" activeEnergyBurned="{total:.3f}" activeEnergyBurnedGoal="500" activeEnergyBurnedUnit="Cal" '
              f'appleMoveTime="0" appleMoveTimeGoal="0" appleExerciseTime="{30 if workout_spans else 8}" appleExerciseTimeGoal="30" '
              f'appleStandHours="{min(len(worn_hours), 12)}" appleStandHoursGoal="12"/>\n')
        if status[i] == "full":
            a = local(d, 7, 30, z)
            ran = any(truth["workouts"][-1]["date"] == str(d - timedelta(days=k)) for k in (1, 2)) if truth["workouts"] else False
            w(rec(Q + "RestingHeartRate", WATCH, a, a + timedelta(minutes=1), round(rng.gauss(57 if ran else 59, 1.2), 1), "count/min", WATCH_DEV))
            beats = ""
            if i % 50 == 0:
                beats = ("<HeartRateVariabilityMetadataList>" + "".join(f'<InstantaneousBeatsPerMinute bpm="{60 + k}" time="7:3{k}:00.00 AM"/>' for k in range(3))
                         + "</HeartRateVariabilityMetadataList>")
            w(rec(Q + "HeartRateVariabilitySDNN", WATCH, a, a + timedelta(minutes=1), round(rng.gauss(45, 6), 1), "ms", WATCH_DEV, inner=beats))
        if p["tagged"] and z == "America/Boise" and d.day == 1:
            a = local(d, 9, 0, z)
            sys_, dia = rng.randint(108, 128), rng.randint(68, 82)
            nested = (rec(Q + "BloodPressureSystolic", "Synthetic Cuff", a, a, sys_, "mmHg").strip()
                      + rec(Q + "BloodPressureDiastolic", "Synthetic Cuff", a, a, dia, "mmHg").strip())
            w(f' <Correlation type="HKCorrelationTypeIdentifierBloodPressure" sourceName="Synthetic Cuff" sourceVersion="1" '
              f'creationDate="{stamp(a)}" startDate="{stamp(a)}" endDate="{stamp(a)}">{nested}</Correlation>\n')
            truth["bp_readings"] += 1

    truth["stages_start"] = min(k for k, n in truth["nights"].items() if n["kind"] == "stages")
    truth["watch_sleep_start"] = min(k for k, n in truth["nights"].items() if n["kind"])
    zones = [plan[0]["zone"]]
    for p in plan:
        if p["zone"] != zones[-1]:
            zones.append(p["zone"])
    truth["zones"] = zones
    truth["steps_sum"] = sum(v["steps"] or 0 for v in truth["daily"].values())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE HealthData [\n<!ELEMENT HealthData ANY>\n]>\n<HealthData locale="en_US">\n'
            f' <ExportDate value="{stamp(local(START + timedelta(days=days), 9))}"/>\n'
            ' <Me HKCharacteristicTypeIdentifierDateOfBirth="" HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet"/>\n')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("apple_health_export/export.xml", head + "".join(lines) + "</HealthData>\n")
    path.with_name("truth.json").write_text(json.dumps(truth, indent=1))
    return truth


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Write a SYNTHETIC Apple Health export.zip and truth.json.")
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    t = generate(a.out, a.days, a.seed)
    print(f"wrote {a.out}: {a.days} synthetic days, seed {a.seed}, {len(t['workouts'])} runs")
