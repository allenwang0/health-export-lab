"""The published report, card and README highlights must say what the committed results say.

Expected values are recomputed here straight from results/*.json, independently of
scripts/build_report.py's helpers, so a generator bug that shifts a number (wrong field,
rounding before dividing, one seed instead of all) fails here instead of reaching the page.
"""
import html
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"


def build(tmp, results=RESULTS):
    page, card, readme = tmp / "index.html", tmp / "card.json", tmp / "README.md"
    shutil.copy(ROOT / "README.md", readme)
    subprocess.run([sys.executable, "scripts/build_report.py", "--results", str(results), "--out", str(page),
                    "--card", str(card), "--readme", str(readme)], cwd=ROOT, check=True)
    return page.read_text(), json.loads(card.read_text()), readme.read_text()


def text_of(page):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def test_hero_is_the_step_overcount_recomputed_from_results(tmp_path):
    # The hero is the one number a 5-second reader keeps: the plain sum's overcount against the
    # planted total, removed exactly. It must be recomputed from the result file, not typed.
    page, _, _ = build(tmp_path)
    r = json.loads((RESULTS / "report.json").read_text())
    seeds = json.loads((RESULTS / "seeds.json").read_text())["seeds"]
    s = r["steps"]
    over = (s["plain_sum"] - s["truth"]) / s["truth"]
    assert over > 0, "owner rule: the hero never states a zero or negative result"
    assert s["recovered"] == s["truth"], "the hero says 'removed exactly'"
    hero = re.search(r'<p class="hero-metric"><span class="value">([^<]+)</span>', page).group(1)
    assert hero == f"{over * 100:.0f}%"
    text = text_of(page)
    exact = sum(x["steps"]["recovered"] == x["steps"]["truth"] for x in seeds)
    assert f"{s['plain_sum']:,.0f} steps summed; healthlab recovers the planted {s['truth']:,.0f}" in text
    assert f"on {exact} of {len(seeds)} seeds" in text
    context = re.search(r'<p class="hero-context">([^<]+)</p>', page).group(1)
    assert "Synthetic" in context and f"seed {r['seed']}" in context, "synthetic data must be labelled in the hero"


def test_key_numbers_match_results(tmp_path):
    page, card, readme = build(tmp_path)
    text = text_of(page)
    r = json.loads((RESULTS / "report.json").read_text())
    m = json.loads((RESULTS / "mutants.json").read_text())
    b = json.loads((RESULTS / "parse_bench.json").read_text())
    naive, rebuilt = r["time"]["within5_naive"], r["time"]["within5_rebuilt"]
    assert f"matches Apple's daily totals on {rebuilt * 100:.1f}% of days" in text
    assert f"{naive * 100:.1f}%" in text
    assert f"Reverting any one of {m['total']} fixes makes its test fail" in text and m["killed"] == m["total"]
    assert f"{b['median_s']:.1f}" in text and b["reps"] >= 5
    fam = r["family"]
    assert f"On seed {r['seed']}, {fam['surviving']} of {fam['tested']} pre-listed tests survive" in text
    assert card["metric"]["value"] == round(rebuilt * 100, 1)
    assert f"{naive * 100:.1f}% to {rebuilt * 100:.1f}%" in readme


def test_committed_page_card_and_readme_are_current(tmp_path):
    # Pages publishes report/ as committed; a stale page would show numbers no longer in results/.
    page, card, readme = build(tmp_path)
    hint = "stale: run make report and commit"
    assert page == (ROOT / "report" / "index.html").read_text(), "report/index.html " + hint
    assert card == json.loads((ROOT / "site" / "card.json").read_text()), "site/card.json " + hint
    assert readme == (ROOT / "README.md").read_text(), "README.md highlights " + hint


def test_unverified_claims_stay_out(tmp_path):
    # "about 10 s" was never measured, and "all 4 that survive" holds on seed 0 only.
    page, _, readme = build(tmp_path)
    for name, text in (("page", text_of(page)), ("README", readme)):
        for phrase in ("about 10 s", "all 4 that survive", "proves", "production-ready", "guarantees"):
            assert phrase not in text, f"{name} contains {phrase!r}"


def test_runner_writes_results_the_page_can_render(tmp_path):
    """A short SYNTHETIC run of scripts/report.py renders into a valid page and card."""
    res = tmp_path / "results"
    res.mkdir()
    subprocess.run([sys.executable, "scripts/report.py", "--days", "400", "--seed", "3", "--out", str(res / "report.json")],
                   cwd=ROOT, check=True, capture_output=True)
    for name in ("seeds.json", "mutants.json", "parse_bench.json"):
        shutil.copy(RESULTS / name, res / name)
    r = json.loads((res / "report.json").read_text())
    assert r["synthetic"] and r["seed"] == 3 and r["steps"]["recovered"] == r["steps"]["truth"]
    page, c, _ = build(tmp_path, res)
    assert "Synthetic" in page and "NaN" not in page
    assert set(c) == {"schema_version", "repo", "sha", "generated_at", "command", "data_note", "metric", "takeaway", "chart"}
    assert c["schema_version"] == 1 and re.fullmatch(r"[0-9a-f]{40}", c["sha"])
    datetime.strptime(c["generated_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert "SYNTHETIC" in c["data_note"] and "seed 3" in c["data_note"]
    assert len(c["takeaway"]) <= 140 and 0 <= c["metric"]["value"] <= 100
    for s in c["chart"]["series"]:
        assert s["style"] in ("solid", "dashed", "dots") and 0 < len(s["points"]) <= 500
        for x, y in s["points"]:
            datetime.strptime(x, "%Y-%m-%d")
            assert y == y and abs(y) != float("inf")
