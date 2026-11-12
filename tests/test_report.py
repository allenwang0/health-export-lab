"""The demo report and card.json, built from a short SYNTHETIC run."""
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_report_and_card_are_built_from_a_run(tmp_path):
    page, card = ROOT / "report" / "index.html", ROOT / "site" / "card.json"
    keep = {p: p.read_bytes() for p in (page, card) if p.exists()}
    try:
        subprocess.run([sys.executable, "scripts/report.py", "--days", "400", "--seed", "3"], cwd=ROOT, check=True, capture_output=True)
        html, raw = page.read_text(), card.read_bytes()
        c = json.loads(raw)
    finally:
        for p in (page, card):
            p.write_bytes(keep[p]) if p in keep else p.unlink(missing_ok=True)
    assert "SYNTHETIC" in html and "NaN" not in html
    assert "Mean daily error (kcal)" in html and "Week" in html
    assert set(c) == {"schema_version", "repo", "sha", "generated_at", "command", "data_note", "metric", "takeaway", "chart"}
    assert c["schema_version"] == 1 and re.fullmatch(r"[0-9a-f]{40}", c["sha"])
    datetime.strptime(c["generated_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert "SYNTHETIC" in c["data_note"] and "seed 3" in c["data_note"]
    assert len(c["takeaway"]) <= 140 and 0 <= c["metric"]["value"] <= 100
    ch = c["chart"]
    assert ch["type"] == "line" and ch["x_scale"] == "time" and 1 <= len(ch["series"]) <= 4
    for s in ch["series"]:
        assert s["style"] in ("solid", "dashed", "dots") and 0 < len(s["points"]) <= 500
        for x, y in s["points"]:
            datetime.strptime(x, "%Y-%m-%d")
            assert y == y and abs(y) != float("inf")
    assert len(raw) <= 200_000
