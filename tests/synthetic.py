"""SYNTHETIC Apple Health export for tests. Nothing here is real health data.

The generator plants known truths so tests can assert them: overlapping sources with an
exact deduplicated total, a -0700 export offset on every record, a Madrid trip, a sleep
night told by two sources, a correlation with nested records, and a step change.
"""
import random
from datetime import datetime, timedelta, timezone

EXPORT_TZ = timezone(timedelta(hours=-7))
HEAD = '<?xml version="1.0"?>\n<!DOCTYPE HealthData [<!ELEMENT HealthData ANY>]>\n<HealthData locale="en_US">\n <ExportDate value="2026-01-01 12:00:00 -0700"/>\n'
WATCH = '&lt;&lt;HKDevice: 0x1&gt;, name:Apple Watch, manufacturer:Apple Inc., model:Watch, hardware:Watch6,1, software:9.0&gt;'


def stamp(t):
    return t.astimezone(EXPORT_TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def rec(type_, src, a, b, value, unit="count", meta=None, device=None):
    m = "".join(f'<MetadataEntry key="{k}" value="{v}"/>' for k, v in (meta or {}).items())
    d = f' device="{device}"' if device else ""
    u = f' unit="{unit}"' if unit else ""
    return (f' <Record type="{type_}" sourceName="{src}" sourceVersion="1"{d}{u} creationDate="{stamp(b)}" '
            f'startDate="{stamp(a)}" endDate="{stamp(b)}" value="{value}">{m}</Record>\n')


Q, C = "HKQuantityTypeIdentifier", "HKCategoryTypeIdentifier"
S = C + "SleepAnalysis"
V = "HKCategoryValueSleepAnalysis"


def generate(path, days=420, seed=7):
    """Writes the export. Returns the truths tests assert against."""
    rng = random.Random(seed)
    start = datetime(2023, 1, 1, 18, tzinfo=timezone.utc)
    with open(path, "w") as f:
        f.write(HEAD)
        for i in range(days):
            day = start + timedelta(days=i)
            tz = "Europe/Madrid" if 200 <= i < 214 else "America/Boise"
            # 1000 phone steps across an hour; the Watch covers the second half of it with 400
            f.write(rec(Q + "StepCount", "My iPhone", day, day + timedelta(hours=1), 1000))
            f.write(rec(Q + "StepCount", "My Apple Watch", day + timedelta(minutes=30), day + timedelta(hours=1), 400, device=WATCH))
            for h in range(0, 16):
                t = day + timedelta(hours=h)
                f.write(rec(Q + "HeartRate", "My Apple Watch", t, t, 60 + rng.randint(0, 30), "count/min", {"HKMetadataKeyHeartRateMotionContext": 1}, WATCH))
            f.write(rec(Q + "ActiveEnergyBurned", "My Apple Watch", day, day + timedelta(minutes=10), 300, "Cal", device=WATCH))
            rhr = 62 if i < days // 2 else 56
            f.write(rec(Q + "RestingHeartRate", "My Apple Watch", day, day + timedelta(minutes=1), rhr + rng.gauss(0, 1), "count/min", device=WATCH))
            f.write(rec(Q + "HeartRateVariabilitySDNN", "My Apple Watch", day, day + timedelta(minutes=1), 40 + rng.gauss(0, 4), "ms", device=WATCH))
            bed = day + timedelta(hours=13)  # 07:00 UTC, which is midnight in Boise in winter
            wake = bed + timedelta(hours=7)
            f.write(rec(S, "My Apple Watch", bed, wake, V + "AsleepCore", None, {"HKTimeZone": tz}, WATCH))
            f.write(rec(S, "AutoSleep", bed, wake + timedelta(hours=1), V + "AsleepUnspecified", None, None))
            f.write(f' <ActivitySummary dateComponents="{(day.astimezone(EXPORT_TZ)).date()}" activeEnergyBurned="300" activeEnergyBurnedGoal="500" activeEnergyBurnedUnit="Cal" appleMoveTime="0" appleMoveTimeGoal="0" appleExerciseTime="20" appleExerciseTimeGoal="30" appleStandHours="10" appleStandHoursGoal="12"/>\n')
            if i % 3 == 0:
                e = day + timedelta(minutes=40)
                f.write(f' <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="40" durationUnit="min" sourceName="My Apple Watch" sourceVersion="1" device="{WATCH}" creationDate="{stamp(e)}" startDate="{stamp(day)}" endDate="{stamp(e)}"><MetadataEntry key="HKTimeZone" value="{tz}"/><WorkoutStatistics type="{Q}DistanceWalkingRunning" startDate="{stamp(day)}" endDate="{stamp(e)}" sum="5" unit="mi"/><WorkoutStatistics type="{Q}HeartRate" startDate="{stamp(day)}" endDate="{stamp(e)}" average="150" minimum="120" maximum="170" unit="count/min"/></Workout>\n')
        t = start
        beat = '<HeartRateVariabilityMetadataList><InstantaneousBeatsPerMinute bpm="60" time="1:00:00.00 AM"/><InstantaneousBeatsPerMinute bpm="61" time="1:00:01.00 AM"/></HeartRateVariabilityMetadataList>'
        f.write(rec(Q + "HeartRateVariabilitySDNN", "Beats Source", t, t, 50, "ms").replace("</Record>", beat + "</Record>"))
        f.write(f' <Correlation type="HKCorrelationTypeIdentifierBloodPressure" sourceName="Cuff" sourceVersion="1" creationDate="{stamp(t)}" startDate="{stamp(t)}" endDate="{stamp(t)}">{rec(Q + "BloodPressureSystolic", "Cuff", t, t, 120, "mmHg").strip()}</Correlation>\n')
        f.write("</HealthData>\n")
    return dict(days=days, steps_per_day=1000 - 500 + 400, raw_steps_per_day=1400, trip=(200, 214))


def bulk(path, n, seed=1):
    """A large SYNTHETIC export of n heart-rate records, for parser throughput tests."""
    t = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with open(path, "w") as f:
        f.write(HEAD)
        for i in range(n):
            a = t + timedelta(seconds=i * 30)
            f.write(rec(Q + "HeartRate", "My Apple Watch", a, a, 60 + i % 60, "count/min", {"HKMetadataKeyHeartRateMotionContext": i % 3}, WATCH))
        f.write("</HealthData>\n")
