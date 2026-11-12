"""Metric registry. Each daily column declares what it is, so the UI can never present a
device estimate or a derived number as a direct measurement.

kind: observed (sensor/log), estimate (device or app model), derived (computed here)
agg:  how days roll up. sum metrics total; everything else averages.
"""
M = lambda label, unit, kind, agg, group, note="", higher=None: dict(
    label=label, unit=unit, kind=kind, agg=agg, group=group, note=note, higher=higher)

METRICS = {
    "steps": M("Steps", "steps", "observed", "sum", "Activity", "Source-deduplicated: Watch, then iPhone, then Mi Fit.", "good"),
    "distance_mi": M("Walking + running distance", "mi", "estimate", "sum", "Activity", "Device-estimated; deduplicated by source."),
    "flights": M("Flights climbed", "flights", "observed", "sum", "Activity"),
    "active_kcal_worn": M("Active energy (worn days)", "kcal", "estimate", "sum", "Activity", "Only days with at least 14 hours of heart-rate coverage."),
    "active_kcal": M("Active energy (all days)", "kcal", "estimate", "sum", "Activity", "Falls when the Watch is not worn. Not comparable across years."),
    "basal_kcal": M("Basal energy", "kcal", "estimate", "sum", "Activity"),
    "ring_exercise_min": M("Exercise minutes (ring)", "min", "estimate", "sum", "Activity", "Apple ActivitySummary."),
    "ring_stand_h": M("Stand hours (ring)", "h", "observed", "sum", "Activity"),
    "workouts": M("Workouts", "count", "observed", "sum", "Training", "Zero is only claimed after the first logged workout."),
    "workout_min": M("Workout minutes", "min", "observed", "sum", "Training"),
    "run_mi": M("Running distance", "mi", "observed", "sum", "Training"),
    "runs": M("Runs", "count", "observed", "sum", "Training"),
    "rhr": M("Resting heart rate", "bpm", "estimate", "mean", "Heart", "Apple's daily estimate.", "low"),
    "walking_hr": M("Walking heart rate", "bpm", "estimate", "mean", "Heart"),
    "hr_mean": M("Mean recorded heart rate", "bpm", "derived", "mean", "Heart", "Mean of irregular samples, not a true time average."),
    "hr_max": M("Highest recorded heart rate", "bpm", "observed", "max", "Heart"),
    "sleep_hr": M("Sleeping heart rate", "bpm", "derived", "mean", "Heart", "Mean of HR samples within each night's sleep span."),
    "hrv_sdnn": M("HRV (SDNN)", "ms", "observed", "median", "Heart", "Apple SDNN. Not comparable with RMSSD from other wearables.", "high"),
    "vo2max": M("VO2 max", "mL/kg/min", "estimate", "mean", "Heart", "Device-estimated cardio fitness.", "high"),
    "resp_rate": M("Respiratory rate", "/min", "estimate", "median", "Heart", "Recorded during sleep."),
    "asleep_watch_h": M("Sleep duration (Watch)", "h", "observed", "mean", "Sleep", "Comparable series: Watch-scored nights only.", "high"),
    "asleep_h": M("Sleep duration (best source)", "h", "estimate", "mean", "Sleep", "Mixes sources across the years. Prefer the Watch series for trends."),
    "deep_h": M("Deep sleep", "h", "estimate", "mean", "Sleep", "Stages exist only from watchOS versions that score them."),
    "rem_h": M("REM sleep", "h", "estimate", "mean", "Sleep"),
    "core_h": M("Core sleep", "h", "estimate", "mean", "Sleep"),
    "awake_h": M("Awake in night", "h", "estimate", "mean", "Sleep"),
    "sleep_eff": M("Sleep efficiency", "ratio", "derived", "mean", "Sleep", "Asleep divided by session span."),
    "bed_clock_adj": M("Sleep onset", "clock h", "derived", "mean", "Sleep", "Hours after midnight; 25.5 is 1:30 am."),
    "wake_clock": M("Wake time", "clock h", "derived", "mean", "Sleep"),
    "weight_lb": M("Body mass", "lb", "observed", "mean", "Body", "Logged manually or by an app."),
    "walk_speed_mph": M("Walking speed", "mph", "estimate", "median", "Mobility"),
    "step_length_in": M("Walking step length", "in", "estimate", "median", "Mobility"),
    "double_support_pct": M("Double support", "%", "estimate", "median", "Mobility"),
    "asymmetry_pct": M("Walking asymmetry", "%", "estimate", "median", "Mobility"),
    "stair_up_fps": M("Stair ascent speed", "ft/s", "estimate", "median", "Mobility"),
    "stair_down_fps": M("Stair descent speed", "ft/s", "estimate", "median", "Mobility"),
    "steadiness_pct": M("Walking steadiness", "%", "estimate", "mean", "Mobility"),
    "water_ml": M("Water", "mL", "observed", "sum", "Other", "Logged manually or by an app."),
    "headphone_db": M("Headphone audio", "dB", "estimate", "mean", "Other"),
    "effort": M("Physical effort", "kcal/hr/kg", "estimate", "mean", "Other"),
}

# Pairs tested by the discovery engine: (exposure, outcome, lag in days, rationale). The list is
# fixed before looking at results and every entry counts toward multiple-testing correction.
PAIRS = [(a, b, lag) for a, b, lags in [
    ("steps", "asleep_watch_h", (0, )), ("steps", "deep_h", (0,)), ("steps", "rhr", (1,)), ("steps", "hrv_sdnn", (1,)),
    ("run_mi", "asleep_watch_h", (0,)), ("run_mi", "deep_h", (0,)), ("run_mi", "rhr", (1, 2)), ("run_mi", "hrv_sdnn", (1, 2)),
    ("run_mi", "steps", (1,)), ("workout_min", "asleep_watch_h", (0,)), ("workout_min", "rhr", (1,)), ("workout_min", "hrv_sdnn", (1,)),
    ("asleep_watch_h", "rhr", (1,)), ("asleep_watch_h", "hrv_sdnn", (1,)), ("asleep_watch_h", "steps", (1,)),
    ("asleep_watch_h", "walking_hr", (1,)), ("asleep_watch_h", "workout_min", (1,)), ("deep_h", "hrv_sdnn", (1,)), ("rem_h", "hrv_sdnn", (1,)),
    ("bed_clock_adj", "asleep_watch_h", (1,)), ("bed_clock_adj", "rhr", (1,)), ("bed_clock_adj", "steps", (1,)),
    ("sleep_hr", "hrv_sdnn", (0,)), ("rhr", "hrv_sdnn", (0,)), ("rhr", "steps", (0,)), ("resp_rate", "rhr", (0,)),
    ("hrv_sdnn", "steps", (1,)), ("hrv_sdnn", "workout_min", (1,)), ("sleep_eff", "rhr", (1,)), ("active_kcal_worn", "asleep_watch_h", (0,)),
    ("active_kcal_worn", "rhr", (1,)), ("flights", "asleep_watch_h", (0,)), ("water_ml", "rhr", (0,)), ("headphone_db", "asleep_watch_h", (0,)),
    ("walk_speed_mph", "rhr", (0,)), ("walk_speed_mph", "steps", (0,)),
] for lag in lags]

TREND_METRICS = ["steps", "distance_mi", "flights", "active_kcal_worn", "rhr", "walking_hr", "sleep_hr", "hrv_sdnn", "vo2max", "resp_rate",
                 "asleep_watch_h", "deep_h", "rem_h", "bed_clock_adj", "walk_speed_mph", "step_length_in", "weight_lb"]
