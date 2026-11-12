"""Invariants of the time and dedupe primitives, checked on generated inputs."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from healthlab import build

span = st.tuples(st.integers(0, 20_000), st.integers(1, 5_000))
samples = st.lists(st.tuples(span, st.floats(0, 1_000), st.integers(0, 3)), min_size=1, max_size=40)


def arrays(rows):
    s = np.array([float(a) for (a, _), _, _ in rows])
    e = np.array([float(a + d) for (a, d), _, _ in rows])
    return s, e, np.array([v for _, v, _ in rows]), np.array([r for _, _, r in rows])


@given(samples)
def test_dedupe_never_creates_value(rows):
    s, e, v, r = arrays(rows)
    _, _, out = build.dedupe_cumulative(s, e, v, r)
    assert out.sum() <= v.sum() + 1e-6
    assert (out >= -1e-9).all()


@given(samples)
def test_dedupe_output_segments_do_not_overlap(rows):
    s, e, v, r = arrays(rows)
    a, b, _ = build.dedupe_cumulative(s, e, v, r)
    keep = b > a
    order = np.argsort(a[keep])
    a, b = a[keep][order], b[keep][order]
    assert (a[1:] >= b[:-1] - 1e-9).all()


@given(st.lists(st.tuples(st.integers(1, 500), st.floats(0, 1_000)), min_size=1, max_size=30))
def test_one_source_without_overlap_is_kept_whole(chunks):
    edges = np.cumsum([0] + [d for d, _ in chunks]).astype(float)
    v = np.array([x for _, x in chunks])
    _, _, out = build.dedupe_cumulative(edges[:-1], edges[1:], v, np.zeros(len(v), int))
    assert out.sum() == pytest.approx(v.sum(), abs=1e-6)


@given(samples, st.floats(1, 1_000))
def test_a_covering_top_source_wins_exactly(rows, top):
    """A rank-0 sample spanning everything leaves nothing to lower ranks."""
    s, e, v, r = arrays(rows)
    s, e = np.append(s, -1.0), np.append(e, 30_000.0)
    v, r = np.append(v, top), np.append(r + 1, 0)
    _, _, out = build.dedupe_cumulative(s, e, v, r)
    assert out.sum() == pytest.approx(top, rel=1e-9)


@given(st.lists(st.tuples(st.floats(0, 1e6), st.floats(0, 20_000), st.floats(0, 1e4)), min_size=1, max_size=30))
def test_splitting_into_hours_preserves_the_total(rows):
    s = np.array([a for a, _, _ in rows])
    e = s + np.array([d for _, d, _ in rows])
    v = np.array([x for _, _, x in rows])
    hours, vals = build.split_hours(s, e, v)
    assert vals.sum() == pytest.approx(v.sum(), rel=1e-9, abs=1e-6)
    assert (hours % 3600 == 0).all()


@given(st.lists(st.tuples(st.integers(0, 1000), st.integers(0, 100)), max_size=30))
def test_union_spans_is_sorted_disjoint_and_covers_the_same_points(pairs):
    spans = [(a, a + d) for a, d in pairs]
    out = build.union_spans(spans)
    assert all(x[1] < y[0] for x, y in zip(out, out[1:]))
    for p in range(0, 1101, 7):
        assert any(a <= p <= b for a, b in spans) == any(a <= p <= b for a, b in out)


ZONES = ["America/Boise", "America/New_York", "Europe/Madrid", "Asia/Tokyo", "Australia/Sydney"]


@settings(max_examples=50, deadline=None)
@given(st.sampled_from(ZONES), st.integers(0, 3 * 365 * 24))
def test_hour_offsets_match_zoneinfo(zone, hour):
    first = datetime(2022, 1, 1)
    t = first + timedelta(hours=hour)
    h = build.hour_offsets([(first, datetime(2100, 1, 1), zone, 9)], first, t)
    want = t.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).utcoffset().total_seconds() / 60
    assert h.offset_min.iloc[-1] == want
