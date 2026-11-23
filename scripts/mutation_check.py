"""Remove each data-trap fix in turn and require its test to fail.

    python scripts/mutation_check.py [--out results/mutants.json]

Each mutant is a one-line patch that brings back the naive behaviour. It runs in a scratch
copy of the repo, so the working tree is never modified. A mutant that survives means the
test does not guard the fix.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAPS = "tests/test_traps.py"

MUTANTS = [
    ("Use the export offset for every hour", "healthlab/build.py",
     "        if not segments:\n            z, conf = fallback", "        if True:\n            z, conf = fallback",
     "test_local_time_is_rebuilt_from_zone_tags_not_the_export_offset"),
    ("One fixed offset per zone, ignoring DST", "healthlab/build.py",
     "off = t.replace(tzinfo=timezone.utc).astimezone(zi).utcoffset()", "off = datetime(2001, 1, 15, tzinfo=timezone.utc).astimezone(zi).utcoffset()",
     "test_each_hour_uses_its_own_dst_offset"),
    ("Treat zone aliases as different places", "healthlab/build.py",
     "z = TZ_ALIASES.get(z, z)", "z = z", "test_zone_aliases_merge_into_one_place"),
    ("Present untagged days as observed", "healthlab/build.py",
     'z, conf = segments[0][2], "assumed"', 'z, conf = segments[0][2], "observed"', "test_days_before_the_first_zone_tag_are_marked_assumed"),
    ("Sum steps across sources", "healthlab/build.py",
     "seg_s, seg_e, seg_v = dedupe_cumulative(d.s.to_numpy(float), d.e.to_numpy(float), d.value.to_numpy(float), r)",
     "seg_s, seg_e, seg_v = d.s.to_numpy(float), d.e.to_numpy(float), d.value.to_numpy(float)",
     "test_iphone_and_watch_steps_are_not_double_counted"),
    ("Fill missing days with zero", "healthlab/build.py",
     "h.* EXCLUDE d, hr.* EXCLUDE d,", "h.* EXCLUDE (d, steps), coalesce(h.steps, 0) steps, hr.* EXCLUDE d,",
     "test_a_day_without_samples_is_null_not_zero"),
    ("Longest sleep source wins", "healthlab/build.py",
     "best = max((c for c in mains if c[0] == best_tier), key=lambda c: c[4] - c[3])", "best = max(mains, key=lambda c: c[4] - c[3])",
     "test_one_sleep_source_per_night_named_and_never_blended"),
    ("Key a night by the date it started", "healthlab/build.py",
     "by_date[(b + off).date()]", "by_date[(a + off).date()]", "test_one_sleep_source_per_night_named_and_never_blended"),
    ("Match device names on a plain space", "healthlab/build.py",
     'sl.source_name.str.lower().str.replace("\\xa0", " ").map(', "sl.source_name.str.lower().map(",
     "test_device_names_with_a_non_breaking_space_still_match"),
    ("Hard-code the sleep tracking dates", "healthlab/insights.py",
     """first = con.execute("SELECT min(wake_date) FROM sleep_nights WHERE source LIKE 'Watch%' AND kind LIKE ?", [kind]).fetchall()[0][0]""",
     'first = "2000-01-01"', "test_sleep_tracking_and_stage_dates_come_from_the_data"),
    ("Count any heart-rate hour as a worn day", "healthlab/build.py",
     "worn_day = coalesce(hr_hours,0) >= 14", "worn_day = coalesce(hr_hours,0) >= 1", "test_partial_wear_days_are_not_treated_as_worn"),
    ("Average a handful of workout samples", "healthlab/build.py",
     "coalesce(hr_avg, CASE WHEN h.n>=20 THEN h.avg_hr END) avg_hr", "coalesce(hr_avg, h.avg_hr) avg_hr",
     "test_sparse_workout_heart_rate_is_left_blank"),
    ("Count Correlation members as samples", "healthlab/parse.py",
     'if tag == "Record" and parent is not None and parent.tag == "Correlation":', "if False:",
     "test_correlation_members_are_not_counted_as_samples"),
    ("Append every import without matching", "healthlab/build.py",
     "SELECT DISTINCT ON (type, source_name, start_date, end_date, value, creation_date) *", "SELECT *",
     "test_importing_an_overlapping_export_again_adds_nothing"),
]


def pytest(cwd, test):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", TRAPS + (f"::{test}" if test else "")],
                          cwd=cwd, capture_output=True, text=True).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "results" / "mutants.json"))
    out = Path(ap.parse_args().out)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for part in ("healthlab", "tests", "results", "pyproject.toml"):
            src = ROOT / part
            (shutil.copytree if src.is_dir() else shutil.copy)(src, Path(tmp) / part)
        if pytest(tmp, "") != 0:
            sys.exit("trap tests fail before mutation; fix the suite first")
        for name, rel, old, new, test in MUTANTS:
            f = Path(tmp) / rel
            original = f.read_text()
            if original.count(old) != 1:
                sys.exit(f"mutant {name!r}: patch text found {original.count(old)} times in {rel}")
            f.write_text(original.replace(old, new))
            try:
                killed = pytest(tmp, test) != 0
            finally:
                f.write_text(original)
            rows.append(dict(mutant=name, file=rel, test=test, killed=killed))
            print(f"{'killed  ' if killed else 'SURVIVED'} {name}  ({test})", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(killed=sum(r["killed"] for r in rows), total=len(rows), mutants=rows), indent=1) + "\n")
    print(f"{sum(r['killed'] for r in rows)}/{len(rows)} mutants killed")
    sys.exit(0 if all(r["killed"] for r in rows) else 1)


if __name__ == "__main__":
    main()
