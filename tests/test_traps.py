"""One test per data trap, checked against the truth the SYNTHETIC generator planted.

Each docstring names the wrong answer the analyzer would give without its fix.
scripts/mutation_check.py removes each fix in turn and requires the matching test to fail.
Days next to a zone change are left out of per-day checks: zone tags place a change only to
within about a day, which the README states as a limitation.
"""
import shutil
from datetime import date, timedelta

import duckdb
import pandas as pd
import pytest

from healthlab import build, parse, synth


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("synth")
    truth = synth.generate(root / "export.zip", days=1095, seed=0)
    parse.ingest(str(root / "export.zip"), root)
    build.build(root)
    con = duckdb.connect(str(root / "lab.duckdb"), read_only=True)
    daily = con.execute("SELECT date::VARCHAR d, * EXCLUDE (date) FROM daily").df().set_index("d")
    planted = pd.DataFrame(truth["daily"]).T.add_prefix("t_")
    zone = planted.t_zone
    near_change = {str(date.fromisoformat(k) + timedelta(days=s)) for k in zone.index[zone != zone.shift()] for s in (-1, 0, 1)}
    return dict(root=root, truth=truth, con=con, daily=daily.join(planted), stable=[k for k in zone.index if k not in near_change])


def test_local_time_is_rebuilt_from_zone_tags_not_the_export_offset(demo):
    """Without the rebuild every record keeps -0700, so Tokyo and Madrid mornings land on the previous
    evening."""
    d = demo["daily"].loc[demo["stable"]]
    tagged = d[d.t_tagged.eq(True)]
    assert (tagged.tz == tagged.t_zone).all()
    v = build.validate_time(demo["con"])
    assert v["within5_rebuilt"] > v["within5_naive"] + .02, v


def test_each_hour_uses_its_own_dst_offset(demo):
    """One fixed offset per zone would put every summer hour in Boise an hour early."""
    off = demo["con"].execute("""SELECT offset_min FROM hour_tz WHERE hour_utc IN
        (TIMESTAMP '2024-01-15 20:00:00', TIMESTAMP '2024-07-15 20:00:00') ORDER BY hour_utc""").fetchall()
    assert [o[0] for o in off] == [-420, -360]


def test_zone_aliases_merge_into_one_place(demo):
    """Phone records say US/Eastern and Watch records America/New_York. Treated as two zones,
    the timeline flips between them every night and travel detection breaks."""
    segs = [r[0] for r in demo["con"].execute("SELECT tz FROM tz_segments ORDER BY start_utc").fetchall()]
    assert segs == demo["truth"]["zones"][1:]


def test_days_before_the_first_zone_tag_are_marked_assumed(demo):
    """The untagged era was lived one zone west of the first tag. The analyzer cannot know that,
    so it must say the zone is assumed rather than present it as observed."""
    d = demo["daily"]
    early = d[d.t_tagged.eq(False)]
    assert len(early) > 30 and (early.tz_conf == "assumed").all()
    tagged = d.loc[demo["stable"]]
    assert (tagged[tagged.t_tagged.eq(True)].tz_conf != "assumed").all()


def test_iphone_and_watch_steps_are_not_double_counted(demo):
    """A plain sum counts the hours both devices carried twice."""
    d = demo["daily"].loc[demo["stable"]]
    d = d[d.t_steps.notna()]
    assert d.steps.to_numpy() == pytest.approx(d.t_steps.astype(float).to_numpy(), abs=1e-6)
    assert demo["daily"].steps.sum() < .7 * demo["truth"]["raw_step_sum"]


def test_a_day_without_samples_is_null_not_zero(demo):
    """Zero steps on a day nothing was recorded would drag every average down."""
    gap = demo["daily"][demo["daily"].t_gap.eq(True)]
    assert len(gap) == 5 and gap.steps.isna().all()


def test_one_sleep_source_per_night_named_and_never_blended(demo):
    """AutoSleep scores about an hour longer than the Watch. Averaging or taking the longest
    source would inflate every night."""
    n = demo["con"].execute("SELECT wake_date::VARCHAR d, source, asleep_h, n_candidates FROM sleep_nights").df().set_index("d")
    t = pd.DataFrame(demo["truth"]["nights"]).T
    watch = n.join(t, how="inner")[t.kind.notna()]
    assert len(watch) > 500
    assert set(watch.source) == {"Watch", "Watch (stages)"}
    assert watch.asleep_h.to_numpy() == pytest.approx(watch.watch_asleep_h.astype(float).to_numpy(), abs=1e-6)
    assert (watch.n_candidates >= 3).all()


def test_device_names_with_a_non_breaking_space_still_match(demo):
    """Real device names contain U+00A0. Matching on a plain space drops every Watch night."""
    stages = demo["con"].execute("SELECT count(*) FROM sleep_nights WHERE source='Watch (stages)'").fetchall()[0][0]
    assert stages == sum(1 for n in demo["truth"]["nights"].values() if n["kind"] == "stages")


def test_sleep_tracking_and_stage_dates_come_from_the_data(demo):
    """Dates typed into the code are wrong for every other export."""
    from healthlab import data, insights
    trans = {label: str(t.date()) for t, label, _ in insights.transitions(demo["con"], data.load_daily(demo["con"]))}
    assert trans["Watch sleep stages begin"] == demo["truth"]["stages_start"]
    assert trans["Watch sleep tracking begins"] == demo["truth"]["watch_sleep_start"]


def test_partial_wear_days_are_not_treated_as_worn(demo):
    """On a day the Watch was off for hours, active energy is low because of wear, not behaviour."""
    d = demo["daily"].loc[demo["stable"]]
    d = d[d.t_wear != "none"]
    assert (d.worn_day == (d.t_wear == "full")).all()
    assert d[d.t_wear == "partial"].active_kcal_worn.isna().all()


def test_sparse_workout_heart_rate_is_left_blank(demo):
    """A handful of samples is not an average. Older runs must show no average heart rate; newer
    runs use the workout statistic, middle ones the dense samples."""
    w = demo["con"].execute("SELECT strftime(start_utc, '%Y-%m-%d %H:%M:%S') s, avg_hr, hr_basis FROM workout_facts").df().set_index("s")
    t = pd.DataFrame(demo["truth"]["workouts"]).set_index("start_utc").join(w, rsuffix="_got")
    assert t[t.tier == "sparse"].avg_hr_got.isna().all()
    assert (t[t.tier == "dense"].hr_basis == "samples").all() and (t[t.tier == "stat"].hr_basis == "workout stat").all()
    dense = t[t.tier != "sparse"]
    assert dense.avg_hr_got.to_numpy() == pytest.approx(dense.avg_hr.astype(float).to_numpy(), abs=1e-6)


def test_correlation_members_are_not_counted_as_samples(demo):
    """Blood pressure sits in a Correlation with nested Records; counting those as samples too
    would double every reading."""
    con = demo["con"]
    assert con.execute("SELECT count(*) FROM samples WHERE type LIKE 'BloodPressure%'").fetchall()[0][0] == 0
    part = next((demo["root"] / "parsed").iterdir())
    n = duckdb.sql(f"SELECT count(DISTINCT correlation_id) FROM read_parquet('{part}/correlations/*.parquet')").fetchall()[0][0]
    assert n == demo["truth"]["bp_readings"] > 0


def test_importing_an_overlapping_export_again_adds_nothing(tmp_path):
    """Exports are cumulative, so a second export repeats every earlier record. Appending it
    would double every heart-rate sample and every count, even where step dedupe hides it."""
    synth.generate(tmp_path / "export.zip", days=120, seed=1)
    second = tmp_path / "export-later.zip"
    shutil.copy(tmp_path / "export.zip", second)
    parse.ingest(str(tmp_path / "export.zip"), tmp_path / "once")
    for src in (tmp_path / "export.zip", second):
        parse.ingest(str(src), tmp_path / "twice")
    assert len(list((tmp_path / "twice" / "parsed").iterdir())) == 2
    q = "SELECT (SELECT count(*) FROM samples), (SELECT sum(hr_n) FROM daily), (SELECT sum(steps) FROM daily)"
    got = []
    for name in ("once", "twice"):
        build.build(tmp_path / name)
        got.append(duckdb.connect(str(tmp_path / name / "lab.duckdb"), read_only=True).execute(q).fetchall()[0])
    assert got[0] == got[1]
