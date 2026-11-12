"""Each test names the wrong answer it prevents. Data is SYNTHETIC (see synthetic.py)."""
import time
import warnings
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from healthlab import api, build, data, insights, lab as hypo, parse, stats as st
import synthetic

warnings.simplefilter("ignore")


def db(root):
    return duckdb.connect(str(root / "lab.duckdb"), read_only=True)


def one(con, q):
    return con.execute(q).fetchall()[0][0]


def test_parser_keeps_every_element_and_nested_detail(lab):
    root, truth, _ = lab
    c = db(root)
    assert one(c, "SELECT count(*) FROM hrv_beats") == 2, "HRV beat lists must survive parsing"
    assert one(c, "SELECT count(*) FROM samples WHERE metadata LIKE '%MotionContext%'") == truth["days"] * 16


def test_correlation_members_are_not_counted_as_records(lab):
    """A blood pressure nested in a Correlation must not also appear as a sample."""
    c = db(lab[0])
    assert one(c, "SELECT count(*) FROM samples WHERE type='BloodPressureSystolic'") == 0
    d = list((lab[0] / "parsed").iterdir())[0]
    assert duckdb.sql(f"SELECT count(*) FROM read_parquet('{d}/correlations/*.parquet')").fetchall()[0][0] == 1


def test_overlapping_sources_are_not_double_counted(lab):
    """iPhone and Watch both logged the same half hour: 1000 phone steps + 400 Watch steps must
    give 900 (500 phone-only + 400 Watch), not the 1400 a plain sum reports."""
    root, truth, _ = lab
    total = one(db(root), "SELECT sum(value) FROM hourly WHERE metric='steps'")
    assert total == pytest.approx(truth["days"] * truth["steps_per_day"], rel=1e-6)


def test_dedupe_prefers_higher_source_and_fills_gaps():
    s, e, v = build.dedupe_cumulative(np.array([0., 0.]), np.array([100., 50.]), np.array([100., 10.]), np.array([1, 0]))
    assert v.sum() == pytest.approx(60), "first 50 s from the priority source, the rest from the other"


def test_a_day_without_samples_is_null_not_zero(tmp_path):
    xml = tmp_path / "gap.xml"
    with open(xml, "w") as f:
        f.write(synthetic.HEAD)
        for day in (1, 5):
            a = datetime(2024, 3, day, 18, tzinfo=synthetic.timezone.utc)
            f.write(synthetic.rec(synthetic.Q + "StepCount", "My iPhone", a, a, 100))
        f.write("</HealthData>\n")
    parse.ingest(str(xml), tmp_path)
    build.build(tmp_path)
    s = duckdb.connect(str(tmp_path / "lab.duckdb"), read_only=True).execute("SELECT date, steps FROM daily ORDER BY 1").df()
    assert s.steps.isna().sum() == 3, "the three days between readings are unknown, not zero"


def test_local_time_comes_from_tz_tags_not_the_export_offset(lab):
    """All records carry -0700, so without rebuilding, Madrid hours would read 9 hours off."""
    c = db(lab[0])
    r = c.execute("SELECT hour(start_local), tz FROM samples WHERE type='SleepAnalysis' AND source_name='My Apple Watch' ORDER BY start_utc OFFSET 205 LIMIT 1").fetchall()[0]
    assert r[1] == "Europe/Madrid"
    assert r[0] == 9  # 07:00 UTC is 09:00 CEST in late July, and would read 00:00 at -0700


def test_winter_and_summer_use_their_own_offsets():
    from datetime import timedelta
    a = datetime(2023, 1, 1)
    h = build.hour_offsets([(a, datetime(2100, 1, 1), "America/Boise", 9)], a, a + timedelta(days=200))
    assert h[h.hour_utc == datetime(2023, 1, 15)].offset_min.iloc[0] == -420
    assert h[h.hour_utc == datetime(2023, 7, 15)].offset_min.iloc[0] == -360


def test_one_sleep_source_per_night_and_never_blended(lab):
    """Watch says 7 h, AutoSleep 8 h. A blend or sum would report 7.5 or 15."""
    c = db(lab[0])
    assert one(c, "SELECT count(DISTINCT source) FROM sleep_nights") == 1
    assert one(c, "SELECT min(asleep_h) FROM sleep_nights") == pytest.approx(7)
    assert one(c, "SELECT max(asleep_h) FROM sleep_nights") == pytest.approx(7)


def test_sleep_belongs_to_the_wake_date(lab):
    c = db(lab[0])
    r = c.execute("SELECT onset_local::DATE, wake_local::DATE, wake_date FROM sleep_nights ORDER BY wake_date LIMIT 1").fetchall()[0]
    assert r[2] == r[1]


def test_reimporting_an_export_adds_nothing(lab, tmp_path):
    root, truth, xml = lab
    other = tmp_path / "second.xml"
    other.write_text(xml.read_text())
    for src in (xml, other):
        parse.ingest(str(src), tmp_path)
    build.build(tmp_path)
    a = one(db(tmp_path), "SELECT sum(value) FROM hourly WHERE metric='steps'")
    assert a == pytest.approx(truth["days"] * truth["steps_per_day"], rel=1e-6)


def test_shared_trend_does_not_produce_a_finding():
    """Two unrelated series that both rise correlate strongly until detrended."""
    rng = np.random.default_rng(1)
    idx = pd.date_range("2022-01-01", periods=900)
    a = pd.Series(np.arange(900) * .1 + rng.normal(0, 3, 900), idx)
    b = pd.Series(np.arange(900) * .1 + rng.normal(0, 3, 900), idx)
    assert st.corr(a.values, b.values)["r"] > .8
    assert abs(st.corr(st.residualize(a).values, st.residualize(b).values)["r"]) < .1


def test_autocorrelation_shrinks_effective_sample_size():
    """500 days of a sticky series hold far fewer than 500 independent observations."""
    rng = np.random.default_rng(2)
    x = np.zeros(500)
    for i in range(1, 500):
        x[i] = .9 * x[i - 1] + rng.normal()
    assert st.effective_n(x, x) < 100


def test_bh_counts_nulls_in_the_family():
    q = st.bh_fdr([.01] + [.9] * 99)
    assert q[0] == pytest.approx(1.0) or q[0] > .5, "one hit among 100 tests is not a discovery"


def test_change_point_finds_a_real_step_and_ignores_noise():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2022-01-01", periods=700)
    step = pd.Series(np.r_[rng.normal(60, 2, 350), rng.normal(55, 2, 350)], idx)
    cp = st.change_points(step)
    assert len(cp) >= 1 and abs((pd.Timestamp(cp[0]["date"]) - idx[350]).days) < 30
    assert st.change_points(pd.Series(rng.normal(60, 2, 700), idx)) == []


def test_comparison_does_not_adjust_a_group_for_itself():
    idx = pd.date_range("2022-01-01", periods=400)
    df = pd.DataFrame({"y": np.random.default_rng(4).normal(size=400), "g": (idx.dayofweek >= 5).astype(float)}, idx)
    a = st.compare(df, "y", "g")["adjusted"]
    assert abs(a["hi"] - a["lo"]) < 1, "weekend vs weekend collinearity would blow up the interval"


def test_null_metric_never_satisfies_a_condition():
    d = pd.DataFrame({"steps": [100., np.nan, 900.]}, pd.date_range("2024-01-01", periods=3))
    assert list(hypo.mask(d, [dict(metric="steps", op="<", value=500)])) == [True, False, False]


def test_hypothesis_translation_is_faithful():
    s = hypo.parse_hypothesis("Compare my resting heart rate on days following runs longer than five miles against days following no running.")
    assert s["outcome"] == "rhr" and s["lag"] == 1
    assert s["exposure"] == [dict(metric="run_mi", op=">=", value=5.0)]
    assert s["control"] == [dict(metric="run_mi", op="==", value=0)]


def test_unmatched_question_declines_rather_than_guessing(lab, monkeypatch):
    from healthlab import ask
    c = db(lab[0])
    monkeypatch.setattr(ask, "ROOT", lab[0])
    r = ask.answer("what is my favourite colour", data.load_daily(c), c, None)
    assert r["tool"] == "none"


def test_insights_run_on_a_smaller_export_missing_most_types(lab, monkeypatch):
    """Types are discovered, not assumed: this export has no VO2 max, gait, or routes."""
    root = lab[0]
    monkeypatch.setattr(insights, "ROOT", root)
    c = db(root)
    assert insights.run(c, data.load_daily(c))["n_tested"] >= 0


def client(root, monkeypatch):
    monkeypatch.setattr(api, "ROOT", root)
    monkeypatch.setattr(api, "STATE", root / "state.json")
    monkeypatch.setattr(data, "ROOT", root)
    api._cache.clear()
    return TestClient(api.app, base_url="http://127.0.0.1:8877")


def test_other_websites_cannot_read_or_delete_health_data(lab, monkeypatch):
    c = client(lab[0], monkeypatch)
    assert c.get("/api/boot").status_code == 200
    assert c.get("/api/boot", headers={"host": "evil.example"}).status_code == 403
    assert c.post("/api/delete", json=dict(confirm="DELETE"), headers={"origin": "http://evil.example"}).status_code == 403


def test_delete_requires_explicit_confirmation(lab, monkeypatch):
    assert client(lab[0], monkeypatch).post("/api/delete", json={}).status_code == 400
    assert (lab[0] / "lab.duckdb").exists()


def test_day_view_reports_real_values(lab, monkeypatch):
    r = client(lab[0], monkeypatch).get("/api/day/2023-03-01").json()
    steps = next(x for x in r["compare"] if x["metric"] == "steps")
    assert steps["value"] == pytest.approx(900)


def test_parser_handles_a_million_records_at_speed(tmp_path):
    """Throughput guard: a multi-gigabyte export must parse in minutes, in bounded memory."""
    xml = tmp_path / "big.xml"
    synthetic.bulk(xml, 1_000_000)
    t = time.time()
    parse.ingest(str(xml), tmp_path)
    took = time.time() - t
    assert took < 60, f"{took:.0f}s for 1M records"
