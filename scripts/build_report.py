"""Render report/index.html (and optionally site/card.json and the README highlights) from the
committed result files. Python 3.10+ standard library only, plus the vendored report-kit/.

    python3 scripts/build_report.py [--results results] [--out report/index.html]
        [--card site/card.json] [--readme README.md]

Inputs, all written by the repo's own scripts:
    results/report.json       scripts/report.py            one SYNTHETIC seed, full detail
    results/seeds.json        scripts/report.py --seeds 20 key numbers per seed
    results/mutants.json      scripts/mutation_check.py    which test kills each mutant
    results/parse_bench.json  scripts/bench_parse.py       parser timing, machine, load average

Every number on the page is computed here from those files; none is typed by hand. The output
is deterministic, so CI rebuilds it and fails on any difference from the committed page.
"""
import argparse
import json
import math
import pathlib
import re
import statistics
import subprocess
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "report-kit"))
import report_kit as rk  # noqa: E402
from report_kit import Raw, esc  # noqa: E402

REPO = "allenwang0/health-export-lab"
REPO_URL = f"https://github.com/{REPO}"
PAGE_URL = "https://allenwang0.github.io/health-export-lab/"
HOME_URL = "https://allenwang0.github.io/"
README_START, README_END = "<!-- results:start -->", "<!-- results:end -->"
THIN = " "
T, f = rk.svg_text, rk.coord
WRAP = "white-space:normal;overflow-wrap:anywhere"  # the kit ellipsizes inline code; test names must stay whole
EXERCISE_NAMES = {"run_mi": "run distance", "workout_min": "workout minutes", "active_kcal_worn": "active energy (worn days)",
                  "run_min": "run minutes", "runs": "runs", "workout_kcal": "workout energy", "active_kcal": "active energy"}
METRIC_NAMES = {"rhr": "resting heart rate", **EXERCISE_NAMES}


# ---------------------------------------------------------------- inputs

def load(results_dir):
    d = pathlib.Path(results_dir)
    out = {}
    for key, name in (("run", "report.json"), ("seeds", "seeds.json"), ("mutants", "mutants.json"), ("bench", "parse_bench.json")):
        path = d / name
        if not path.exists():
            sys.exit(f"{path} is missing: run the script named in scripts/build_report.py's docstring")
        out[key] = json.loads(path.read_text(encoding="utf-8"))
    return out


def git_last_commit(rel):
    """Commit that last changed a committed file, so evidence links point at a SHA."""
    try:
        sha = subprocess.run(["git", "log", "-1", "--format=%H", "--", rel], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return sha or None


def line_of(rel, needle, end=None):
    lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
    first = next(i for i, line in enumerate(lines, 1) if needle in line)
    last = first if end is None else next(i for i, line in enumerate(lines, 1) if i >= first and end in line)
    return first, last


# ---------------------------------------------------------------- numbers

def derive(data):
    run, seeds, mut, bench = data["run"], data["seeds"]["seeds"], data["mutants"], data["bench"]
    st = run["steps"]
    over = lambda s: (s["plain_sum"] - s["truth"]) / s["truth"]  # noqa: E731
    sl = run["sleep"]
    sleep_over = lambda s: (s["longest_source_h"] - s["truth_h"]) / s["truth_h"]  # noqa: E731
    sleep_err = lambda s: abs(s["analyzer_h"] - s["truth_h"]) / s["truth_h"]  # noqa: E731
    rng = lambda xs: (min(xs), max(xs))  # noqa: E731
    return dict(
        plain=st["plain_sum"], recovered=st["recovered"], truth=st["truth"], over=over(st),
        step_err=abs(st["recovered"] - st["truth"]) / st["truth"], exact=abs(st["recovered"] - st["truth"]) < .5,
        naive5=run["time"]["within5_naive"], rebuilt5=run["time"]["within5_rebuilt"],
        sleep_over=sleep_over(sl), sleep_err=sleep_err(sl),
        n_seeds=len(seeds), seeds_exact=sum(abs(s["steps"]["recovered"] - s["steps"]["truth"]) < .5 for s in seeds),
        over_rng=rng([over(s["steps"]) for s in seeds]),
        naive5_rng=rng([s["time"]["within5_naive"] for s in seeds]), rebuilt5_rng=rng([s["time"]["within5_rebuilt"] for s in seeds]),
        rebuilt_better=sum(s["time"]["within5_rebuilt"] > s["time"]["within5_naive"] for s in seeds),
        sleep_over_rng=rng([sleep_over(s["sleep"]) for s in seeds]), sleep_exact=sum(sleep_err(s["sleep"]) < 1e-9 for s in seeds),
        surv_rng=rng([s["family"]["surviving"] for s in seeds]), surv_planted=sum(s["family"]["survivors_planted"] for s in seeds),
        tested_rng=rng([s["family"]["tested"] for s in seeds]),
        planted_rng=rng([sum(x["planted"] for x in s["family"]["survivors"]) for s in seeds]),
        extra_seeds=sum(any(not x["planted"] for x in s["family"]["survivors"]) for s in seeds),
        extra_rng=rng([sum(not x["planted"] for x in s["family"]["survivors"]) for s in seeds
                       if any(not x["planted"] for x in s["family"]["survivors"])] or [0]),
        extra_max_r=max([abs(x["r"]) for s in seeds for x in s["family"]["survivors"] if not x["planted"]] or [0]),
        killed=mut["killed"], mutants=mut["total"], kill_tests=len({m["test"] for m in mut["mutants"]}),
        parse_s=bench["median_s"], parse_reps=bench["reps"], records=bench["records"],
    )


def pct(v, digits=1):
    return f"{v * 100:.{digits}f}%"


def pct_g(v):
    """Spec G: one decimal under 10%, integer above."""
    if v == 0:
        return "0%"
    return f"{v * 100:.1f}%" if abs(v) < .1 else f"{v * 100:.0f}%"


def n0(v):
    return f"{v:,.0f}"


def span(lo, hi, fmt):
    return fmt(lo) if fmt(lo) == fmt(hi) else f"{fmt(lo)}–{fmt(hi)}"


def load_text(xs):
    return " / ".join(f"{v:.2f}" for v in xs)


def responsive_table(caption, head, rows, phone_rows, *, numeric=(), wrap_cols=(), tablet_list=False):
    """Table on desktop (and tablet); a definition list where the columns would scroll sideways."""
    table = rk.table(caption, head, rows, numeric=numeric, wrap_cols=wrap_cols)
    listed = rk.tag("p", caption, class_="card-label") + rk.methodology(phone_rows)
    return rk.variants(table, listed if tablet_list else table, listed)


# ---------------------------------------------------------------- charts

def legend(items, x, y):
    """items: (kind, label) where kind is 'muted' | 'accent' | 'line-muted' | 'line-accent' | 'floor' | 'whisker'."""
    parts = []
    for kind, label in items:
        if kind in ("muted", "accent"):
            parts.append(f'<circle cx="{f(x + 5)}" cy="{f(y - 4)}" r="5" class="c-{kind}"/>')
        elif kind == "whisker":
            parts.append(f'<path class="c-whisker" d="M{f(x)} {f(y - 4)}H{f(x + 14)}M{f(x)} {f(y - 8)}V{f(y)}M{f(x + 14)} {f(y - 8)}V{f(y)}"/>')
        else:
            cls = {"line-muted": "c-stroke-muted", "line-accent": "c-line-accent", "floor": "c-floor"}[kind]
            parts.append(f'<path class="{cls}" stroke-width="2" d="M{f(x)} {f(y - 4)}H{f(x + 16)}"/>')
        parts.append(T(x + 22, y, label, "c-title"))
        x += 22 + rk.text_width(label, 12) + 18
    return parts


def dumbbell_chart(rows, *, uid, width, title, desc, axis_title):
    """One row per trap: plain reading (grey, with min-max over seeds) and healthlab (blue), x from 0 = planted truth.
    rows: dicts label, naive, fixed, naive_lo, naive_hi, text. Below 520 px the label sits above its track."""
    stacked, two_line = width < 520, width < 800
    label_w = 0 if stacked else max(rk.text_width(r["label"]) for r in rows) + 20
    text_w = 0 if stacked else max(rk.text_width(r["text"]) for r in rows) + 20
    top = 64 if two_line else 44
    row_h = 46 if not stacked else 58
    plot_l, plot_r = (14, width - 18) if stacked else (label_w + 8, width - text_w - 10)
    hi = max(max(r["naive_hi"], r["naive"]) for r in rows)
    ticks = rk.nice_ticks(0, hi * 100, 5)
    vmax = ticks[-1] / 100
    x = lambda v: plot_l + (plot_r - plot_l) * v / vmax  # noqa: E731
    bottom = top + row_h * len(rows) - 8
    parts = []
    if two_line:
        parts += legend([("muted", "plain reading"), ("accent", "healthlab")], 0 if stacked else plot_l, 14)
        parts += legend([("whisker", f"plain reading, range over {rows[0]['n']} seeds")], 0 if stacked else plot_l, 36)
    else:
        parts += legend([("muted", "plain reading"), ("accent", "healthlab"), ("whisker", f"plain reading, range over {rows[0]['n']} seeds")], plot_l, 14)
    spans = [(top - 10, bottom)] if not stacked else [(top + i * row_h + 10, top + i * row_h + row_h - 4) for i in range(len(rows))]
    for t in ticks:
        for a, b in spans:
            parts.append(f'<path class="c-grid" d="M{f(x(t / 100))} {f(a)}V{f(b)}"/>')
        parts.append(T(x(t / 100), bottom + 16, f"{t:g}%", "c-tick", "middle"))
    for a, b in spans:
        parts.append(f'<path class="c-axis" d="M{f(plot_l)} {f(a)}V{f(b)}"/>')
    for i, r in enumerate(rows):
        y0 = top + i * row_h
        cy = y0 + (row_h - 8) / 2 + (8 if stacked else 0)
        if stacked:
            parts.append(T(0, y0 + 2, r["label"], "c-label"))
            parts.append(T(width, y0 + 2, r["text"], "c-value", "end"))
        else:
            parts.append(T(label_w - 4, cy + 4.5, r["label"], "c-label", "end"))
            parts.append(T(width, cy + 4.5, r["text"], "c-value", "end"))
        lo, hi_ = x(r["naive_lo"]), x(r["naive_hi"])
        parts.append(f'<path class="c-whisker" d="M{f(lo)} {f(cy - 9)}H{f(hi_)}M{f(lo)} {f(cy - 13)}V{f(cy - 5)}M{f(hi_)} {f(cy - 13)}V{f(cy - 5)}"/>')
        parts.append(f'<path class="c-line" stroke-width="2" d="M{f(x(r["fixed"]))} {f(cy)}H{f(x(r["naive"]))}"/>')
        parts.append(f'<circle cx="{f(x(r["naive"]))}" cy="{f(cy)}" r="6" class="c-muted"/>')
        parts.append(f'<circle cx="{f(x(r["fixed"]))}" cy="{f(cy)}" r="6" class="c-accent"/>')
    parts.append(T(plot_l, bottom + 36, axis_title, "c-title"))
    return rk.svg(uid, width, bottom + 44, title, desc, "".join(parts))


def weekly_chart(weeks, changes, *, uid, width, height, title, desc):
    """Weekly mean daily error, export offset (grey) vs rebuilt local time (blue); dashed verticals at zone changes."""
    narrow = width < 520
    ml, mr, mt, mb = 44, 12, 52 if not narrow else 96, 40
    days = [date.fromisoformat(w[0]).toordinal() for w in weeks]
    x0, x1 = min(days), max(days)
    ymax = max(max(w[1], w[2]) for w in weeks)
    yt = rk.nice_ticks(0, ymax, 4 if narrow else 5)
    px = lambda d: ml + (width - ml - mr) * (d - x0) / (x1 - x0)  # noqa: E731
    py = lambda v: height - mb - (height - mt - mb) * v / yt[-1]  # noqa: E731
    parts = []
    if narrow:
        parts += legend([("line-muted", "-0700 export offset"), ("line-accent", "rebuilt local time")][:1], 0, 14)
        parts += legend([("line-accent", "rebuilt local time")], 0, 34)
        parts += legend([("floor", "zone change")], 0, 54)
    else:
        parts += legend([("line-muted", "-0700 export offset"), ("line-accent", "rebuilt local time"), ("floor", "zone change")], ml, 14)
    for t in yt:
        parts.append(f'<path class="c-grid" d="M{ml} {f(py(t))}H{f(width - mr)}"/>')
        parts.append(T(ml - 8, py(t) + 4, f"{t:g}", "c-tick", "end"))
    first = date.fromordinal(x0)
    for yr in range(first.year + (first.month > 1 or first.day > 1), date.fromordinal(x1).year + 1):
        d = date(yr, 1, 1).toordinal()
        parts.append(T(px(d), height - mb + 18, str(yr), "c-tick", "middle"))
    for c, _zone in changes:
        d = date.fromisoformat(c).toordinal()
        if x0 <= d <= x1:
            parts.append(f'<path class="c-floor" d="M{f(px(d))} {mt - 6}V{f(height - mb)}"/>')
    parts.append(f'<path class="c-axis" d="M{ml} {f(height - mb)}H{f(width - mr)}"/>')
    for k, cls in ((1, "c-stroke-muted"), (2, "c-line-accent")):
        d = "".join(("M" if i == 0 else "L") + f"{f(px(dd))} {f(py(w[k]))}" for i, (dd, w) in enumerate(zip(days, weeks)))
        parts.append(f'<path class="{cls}" stroke-width="{1.5 if k == 1 else 2}" stroke-linejoin="round" d="{d}"/>')
    parts.append(T(ml, mt - 14, "Mean daily error (kcal)", "c-title"))
    parts.append(T(width - mr, height - 6, "Week (synthetic dates)", "c-title", "end"))
    return rk.svg(uid, width, height, title, desc, "".join(parts))


def strip_chart(rows, *, uid, width, title, desc, axis_title):
    """One row per method; one dot per seed; x truncated to the data range (dots, not bars)."""
    stacked = width < 520
    label_w = 0 if stacked else max(rk.text_width(r["label"]) for r in rows) + 20
    text_w = max(rk.text_width(r["text"]) for r in rows) + 16
    allv = [v for r in rows for v in r["values"]]
    lo = math.floor(min(allv) * 100 - .5)
    ticks = rk.nice_ticks(lo, 100, 5)
    ticks = [t for t in ticks if t <= 100]
    a, b = ticks[0], 100
    top, row_h = 14 if not stacked else 24, 50 if not stacked else 64
    plot_l, plot_r = (16, width - 20) if stacked else (label_w + 8, width - text_w - 10)
    x = lambda v: plot_l + (plot_r - plot_l) * (v * 100 - a) / (b - a)  # noqa: E731
    bottom = top + row_h * len(rows)
    parts = []
    for t in ticks:
        parts.append(f'<path class="c-grid" d="M{f(x(t / 100))} {top - 6}V{f(bottom)}"/>')
        parts.append(T(x(t / 100), bottom + 16, f"{t:g}%", "c-tick", "middle"))
    for i, r in enumerate(rows):
        y0 = top + i * row_h
        cy = y0 + row_h / 2 + (8 if stacked else 0)
        if stacked:
            parts.append(T(0, y0 + 10, r["label"], "c-label c-hi" if r["accent"] else "c-label"))
            parts.append(T(width, y0 + 10, r["text"], "c-value", "end"))
        else:
            parts.append(T(label_w - 4, cy + 4.5, r["label"], "c-label c-hi" if r["accent"] else "c-label", "end"))
            parts.append(T(width, cy + 4.5, r["text"], "c-value", "end"))
        for k, v in enumerate(r["values"]):
            parts.append(f'<circle cx="{f(x(v))}" cy="{f(cy + ((k % 5) - 2) * 3.5)}" r="4" class="{"c-accent" if r["accent"] else "c-muted"}" fill-opacity=".75"/>')
    parts.append(T(0 if stacked else plot_l, bottom + 36, f"{'Days within 5%' if stacked else axis_title}, axis from {a:g}%", "c-title"))
    return rk.svg(uid, width, bottom + 44, title, desc, "".join(parts))


PIPELINE = [
    ("Stream the zip", "lxml iterparse, raw text kept"),
    ("Rebuild local time", "HKTimeZone tags, DST"),
    ("Count each step once", "Watch > iPhone > apps"),
    ("Build daily rows", "no data stays unknown"),
    ("Run pre-listed tests", "BH correction, q ≤ 0.10"),
]


def pipeline_svg(uid, width):
    vertical = width < 700
    parts = []
    if vertical:
        bh, gap = 54, 22
        for i, (a, b) in enumerate(PIPELINE):
            y = i * (bh + gap)
            parts.append(f'<rect class="{"c-node" if i != 2 else "c-node"}" x="0.5" y="{f(y + .5)}" width="{f(width - 1)}" height="{bh}" rx="12"/>')
            parts.append(T(16, y + 22, f"{i + 1}  {a}", "c-value c-hi" if i == 2 else "c-value"))
            parts.append(T(16, y + 41, b, "c-tick"))
            if i < len(PIPELINE) - 1:
                parts.append(f'<path class="c-line" d="M{f(width / 2)} {f(y + bh)}V{f(y + bh + gap)}M{f(width / 2 - 4)} {f(y + bh + gap - 6)}L{f(width / 2)} {f(y + bh + gap)}L{f(width / 2 + 4)} {f(y + bh + gap - 6)}"/>')
        height = len(PIPELINE) * (bh + gap) - gap + 1
    else:
        gap = 20
        bw = (width - gap * (len(PIPELINE) - 1)) / len(PIPELINE)
        for i, (a, b) in enumerate(PIPELINE):
            x0 = i * (bw + gap)
            parts.append(f'<rect class="c-node" x="{f(x0 + .5)}" y="0.5" width="{f(bw - 1)}" height="74" rx="12"/>')
            parts.append(T(x0 + 14, 22, str(i + 1), "c-tick"))
            parts.append(T(x0 + 14, 44, a, "c-value c-hi" if i == 2 else "c-value"))
            parts.append(T(x0 + 14, 62, b, "c-tick"))
            if i < len(PIPELINE) - 1:
                ax = x0 + bw
                parts.append(f'<path class="c-line" d="M{f(ax + 3)} 37H{f(ax + gap - 3)}M{f(ax + gap - 8)} 33L{f(ax + gap - 3)} 37L{f(ax + gap - 8)} 41"/>')
        height = 76
    desc = "; ".join(f"{i + 1}. {a}: {b}" for i, (a, b) in enumerate(PIPELINE))
    return rk.svg(uid, width, height, "healthlab pipeline, from export zip to tests", desc, "".join(parts))


# ---------------------------------------------------------------- card and README

def build_card(data, d):
    run = data["run"]
    takeaway = (f"A plain sum overstates steps by {pct_g(d['over'])}; healthlab recovers the planted total exactly "
                f"(synthetic, seed {run['seed']}).")
    weeks = run["time"]["weeks"]
    return {
        "schema_version": 1,
        "repo": "health-export-lab",
        "sha": run["run"]["code_sha"] if "run" in run else run["code_sha"],
        "generated_at": run["run"]["date"] if "run" in run else run["date"],
        "command": "python scripts/report.py && python3 scripts/build_report.py",
        "data_note": (f"SYNTHETIC Apple Health export from healthlab/synth.py, seed {run['seed']}, {run['days']} days "
                      f"({run['start']} to {run['end']}). No real health data."),
        "metric": {"label": "Days within 5% of Apple's daily active energy, rebuilt local time",
                   "value": round(d["rebuilt5"] * 100, 1), "unit": "%", "higher_is_better": True},
        "takeaway": takeaway[:140],
        "chart": {"type": "line", "x_label": "Week", "x_unit": "date", "x_scale": "time",
                  "y_label": "Mean daily error against Apple's total", "y_unit": "kcal", "y_scale": "linear",
                  "series": [{"name": "Export offset (-0700)", "style": "dashed", "points": [[w[0], round(w[1], 2)] for w in weeks]},
                             {"name": "Rebuilt local time", "style": "solid", "points": [[w[0], round(w[2], 2)] for w in weeks]}]},
    }


def readme_block(data, d):
    run, bench = data["run"], data["bench"]
    return "\n".join([
        f"- Steps counted once: a plain sum gives {n0(d['plain'])} steps, {pct_g(d['over'])} over the planted "
        f"{n0(d['truth'])}; removing iPhone and Watch overlap recovers that total exactly, on {d['seeds_exact']} of "
        f"{d['n_seeds']} seeds.",
        f"- Local time rebuilt hour by hour: days within 5% of Apple's own daily totals go from {pct(d['naive5'])} to "
        f"{pct(d['rebuilt5'])} (seed {run['seed']}; {span(*d['rebuilt5_rng'], pct)} over {d['n_seeds']} seeds).",
        f"- Every fix is guarded: reverting each of {d['mutants']} fixes, one at a time, makes a test fail "
        f"({d['killed']} of {d['mutants']}).",
        f"- Streams the export zip without extracting it: {d['records']:,} synthetic records parse in "
        f"{d['parse_s']:.1f}{THIN}s (median of {d['parse_reps']}, {bench['machine']['cpu']}).",
        f"- Statistics corrected for many tests: on seed {run['seed']}, {run['family']['surviving']} of "
        f"{run['family']['tested']} pre-listed tests survive, all the one effect the generator planted; over "
        f"{d['n_seeds']} seeds that effect always survives, with weak extras on {d['extra_seeds']}.",
    ])


def update_readme(path, data, d):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    if README_START not in text or README_END not in text:
        sys.exit(f"{path} has no {README_START} / {README_END} markers")
    head, rest = text.split(README_START, 1)
    _, tail = rest.split(README_END, 1)
    pathlib.Path(path).write_text(f"{head}{README_START}\n{readme_block(data, d)}\n{README_END}{tail}", encoding="utf-8")


# ---------------------------------------------------------------- page

def build_page(data, d):
    run, seeds, mut, bench = data["run"], data["seeds"], data["mutants"], data["bench"]
    info = run["run"]
    R = rk.ref
    seed = run["seed"]
    sha = {rel: git_last_commit(rel) or info["code_sha"] for rel in
           ("results/report.json", "results/seeds.json", "results/mutants.json", "results/parse_bench.json",
            "healthlab/build.py", "healthlab/insights.py", "tests/test_traps.py", "scripts/mutation_check.py", "healthlab/synth.py")}

    def blob(rel, lines=None):
        anchor = f"#L{lines[0]}" + (f"-L{lines[1]}" if lines[1] != lines[0] else "") if lines else ""
        return f"{REPO_URL}/blob/{sha[rel]}/{rel}{anchor}"

    dedupe = line_of("healthlab/build.py", "def dedupe_cumulative", "return")
    prio = line_of("healthlab/build.py", "SOURCE_PRIORITY =")
    t_steps = line_of("tests/test_traps.py", "def test_iphone_and_watch_steps_are_not_double_counted", "raw_step_sum")
    t_fdr = line_of("tests/test_traps.py", "def test_only_the_planted_effect_survives_fdr", "survivors")
    planted = line_of("healthlab/synth.py", "RestingHeartRate")
    src = (ROOT / "healthlab/synth.py").read_text(encoding="utf-8")
    after, base = (float(v) for v in re.search(r"gauss\((\d+) if ran else (\d+)", src).groups())
    window = len(re.search(r"for k in \(([\d, ]+)\)\) if truth\[.workouts.\]", src).group(1).split(","))
    evidence = [
        ("E1", f"Plain sum {n0(d['plain'])} steps; healthlab {n0(d['recovered'])}; planted truth {n0(d['truth'])} (seed {seed})",
         "results/report.json → steps", blob("results/report.json"), "Verified"),
        ("E2", f"Plain sum overstates steps by {pct(d['over'])} of the planted total", "(plain − truth) ÷ truth from E1",
         blob("results/report.json"), "Derived"),
        ("E3", "A test fails if any day's steps differ from the planted value", f"tests/test_traps.py:{t_steps[0]}–{t_steps[1]}",
         blob("tests/test_traps.py", t_steps), "Verified"),
        ("E4", f"Days within 5% of Apple's total: {pct(d['naive5'])} with the export offset, {pct(d['rebuilt5'])} rebuilt",
         "results/report.json → time", blob("results/report.json"), "Verified"),
        ("E5", f"Across {d['n_seeds']} seeds: steps exact on {d['seeds_exact']}; rebuilt time {span(*d['rebuilt5_rng'], pct)}",
         "results/seeds.json", blob("results/seeds.json"), "Verified"),
        ("E6", f"{d['killed']} of {d['mutants']} mutants killed by the trap tests", "results/mutants.json; CI runs make mutants",
         blob("results/mutants.json"), "Verified"),
        ("E7", f"{run['family']['surviving']} of {run['family']['tested']} pre-listed tests survive q ≤ 0.10, all exercise → resting heart rate",
         f"results/report.json; tests/test_traps.py:{t_fdr[0]}–{t_fdr[1]}", blob("tests/test_traps.py", t_fdr), "Verified"),
        ("E8", f"{d['records']:,} records parsed in {d['parse_s']:.2f} s, median of {d['parse_reps']}",
         "results/parse_bench.json", blob("results/parse_bench.json"), "Verified"),
        ("E9", "Overlap removed interval by interval in source priority order", f"healthlab/build.py:{dedupe[0]}–{dedupe[1]}",
         blob("healthlab/build.py", dedupe), "Verified"),
    ]
    if d["planted_rng"][0] == 0:
        sys.exit("the planted effect failed to survive on some seed: rewrite the statistics copy before publishing")
    ev_ids = {e[0] for e in evidence}
    assert all(i in ev_ids for i in ("E1", "E2", "E4", "E5", "E6", "E7", "E8")), "unresolved evidence id"

    # hero
    hero_bars = lambda uid, w: rk.bar_chart(  # noqa: E731
        [{"label": "Plain sum", "value": d["plain"] / 1e6, "accent": False},
         {"label": "healthlab", "value": d["recovered"] / 1e6, "accent": True},
         {"label": "Planted truth", "value": d["truth"] / 1e6, "accent": False}],
        uid=uid, width=w, title="Total steps: plain sum, healthlab, planted truth",
        desc=f"Plain sum {n0(d['plain'])} steps; healthlab {n0(d['recovered'])}; planted truth {n0(d['truth'])}. Synthetic export, seed {seed}.",
        axis_title="Total steps (millions), bars from zero", fmt=lambda v: f"{v:.2f}{THIN}M")
    hero = rk.hero(
        eyebrow="Data engineering · Python · Verification",
        title="Health export lab",
        subtitle="A local Apple Health analyzer that corrects what a raw export gets wrong, and checks each correction "
                 "against an export whose right answers are known.",
        value=pct_g(d["over"]),
        label=Raw(f"overcount in a plain step sum, removed exactly{R('E2')}"),
        support=Raw(f"{n0(d['plain'])} steps summed; healthlab recovers the planted {n0(d['truth'])}, and does so on "
                    f"{d['seeds_exact']} of {d['n_seeds']} seeds{R('E1')}{R('E5')}"),
        context=f"Synthetic {run['days']:,}-day export with planted errors, seed {seed}; no real health data.",
        actions=[("Source", REPO_URL, True), ("Reproduce", "#reproduce", False)],
        figure=rk.tag("p", f"Total steps, seed {seed} (synthetic)", class_="card-label")
        + rk.variants(hero_bars("hero-d", 398), hero_bars("hero-t", 640), hero_bars("hero-p", 280)),
    )
    nav = rk.report_nav([("findings", "Findings"), ("traps", "Errors"), ("steps", "Steps"), ("time", "Local time"),
                         ("seeds", "Seeds"), ("mutants", "Mutants"), ("tests", "Statistics"), ("how", "How it works"),
                         ("methodology", "Method"), ("limitations", "Limits"), ("reproduce", "Reproduce"), ("evidence", "Evidence")])

    findings = rk.bento([
        (8, 1, rk.join([
            rk.tag("h3", "Steps counted once, against a known total", class_="card-heading"),
            rk.comparison([("Plain sum", d["plain"], f"{d['plain'] / d['truth'] * 100:.0f}% of the planted total", False),
                           ("healthlab", d["recovered"], f"{d['recovered'] / d['truth'] * 100:.0f}% of the planted total", True)], d["plain"]),
            rk.tag("p", Raw(f"iPhone and Watch log the same walking. Keeping the higher-priority device for each "
                            f"stretch of time lands on the planted total to the step.{R('E1')} "
                            f'<a href="#steps">How overlap is removed</a>')),
        ])),
        (4, 2, rk.join([
            rk.tag("h3", "Each fix is guarded by a test", class_="card-heading"),
            rk.metric("Mutants killed", f"{d['killed']} of {d['mutants']}"),
            rk.tag("p", Raw(f"Reverting any one fix to its naive version makes a named test fail.{R('E6')}")),
        ])),
        (4, 3, rk.join([
            rk.tag("h3", "Local time rebuilt", class_="card-heading"),
            rk.metric("Days within 5% of Apple's total", pct(d["rebuilt5"])),
            rk.tag("p", Raw(f"Against {pct(d['naive5'])} when every timestamp is read at the export's −0700 offset.{R('E4')}")),
        ])),
        (4, 4, rk.join([
            rk.tag("h3", f"Holds on {d['n_seeds']} seeds", class_="card-heading"),
            rk.metric("Seeds with an exact step total", f"{d['seeds_exact']} of {d['n_seeds']}"),
            rk.tag("p", Raw(f"Rebuilt time beats the export offset on {d['rebuilt_better']} of {d['n_seeds']}.{R('E5')}")),
        ])),
        (4, 5, rk.join([
            rk.tag("h3", "Parses large exports quickly", class_="card-heading"),
            rk.metric(f"{d['records'] / 1e6:g} M records", f"{d['parse_s']:.1f}", "s"),
            rk.tag("p", Raw(f"Median of {d['parse_reps']} runs on an {esc(bench['machine']['cpu'])}, synthetic heart-rate "
                            f"records in a zip.{R('E8')}")),
        ])),
    ])
    findings_sec = rk.section("findings", "Every correction is scored against planted truth", findings, eyebrow="Findings")

    # traps
    S = seeds["seeds"]
    n = d["n_seeds"]
    t = run["time"]
    drows = [
        dict(label="Step total", naive=d["over"], fixed=d["step_err"], naive_lo=d["over_rng"][0], naive_hi=d["over_rng"][1], n=n,
             text=f"{pct_g(d['over'])} → {pct_g(d['step_err'])}"),
        dict(label="Sleep per Watch night", naive=d["sleep_over"], fixed=d["sleep_err"], naive_lo=d["sleep_over_rng"][0],
             naive_hi=d["sleep_over_rng"][1], n=n, text=f"{pct_g(d['sleep_over'])} → {pct_g(d['sleep_err'])}"),
        dict(label="Days off Apple's total by >5%", naive=1 - d["naive5"], fixed=1 - d["rebuilt5"], naive_lo=1 - d["naive5_rng"][1],
             naive_hi=1 - d["naive5_rng"][0], n=n, text=f"{pct_g(1 - d['naive5'])} → {pct_g(1 - d['rebuilt5'])}"),
    ]
    ddesc = "; ".join(f"{r['label']}: plain reading {pct(r['naive'])} (range {pct(r['naive_lo'])} to {pct(r['naive_hi'])} over {n} seeds), "
                      f"healthlab {pct(r['fixed'])}" for r in drows) + "."
    dtitle = "Error against planted truth, plain reading versus healthlab"
    dumb = rk.variants(*(dumbbell_chart(drows, uid=u, width=w, title=dtitle, desc=ddesc, axis_title="Error against planted truth (%), 0 = exact")
                         for u, w in (("db-d", 1000), ("db-t", 640), ("db-p", 300))))
    sl, gp, sp, pw, zn = run["sleep"], run["gaps"], run["sparse_runs"], run["partial_wear"], run["zones"]
    trap_rows = [
        ["iPhone and Watch count the same steps", f"{n0(d['plain'])} steps", f"{n0(d['recovered'])}", f"{n0(d['truth'])}"],
        ["Every timestamp carries the −0700 export offset", f"{pct(d['naive5'])} of days within 5%", pct(d["rebuilt5"]), "100%"],
        ["Three sources score each Watch night", f"{sl['longest_source_h']:.2f} h (longest source)", f"{sl['analyzer_h']:.2f} h", f"{sl['truth_h']:.2f} h"],
        ["Days with no data at all", f"{gp['days']} days of 0 steps", f"{gp['analyzer_null']} days unknown", f"{gp['days']} unknown days"],
        ["Older runs keep a few heart-rate samples", f"{sp['runs']} runs averaged from a median {sp['median_samples']:.0f} samples",
         f"{sp['analyzer_blank']} left blank", f"{sp['runs']} with no reliable average"],
        ["Zones change with a move, two trips and an alias", "1 zone (−0700 throughout)", f"{zn['segments']} zone segments",
         f"{zn['tagged_eras']} tagged eras + 1 untagged"],
        ["Watch worn only part of the day", f"{pw['days']} partial days counted as worn", f"{pw['excluded']} excluded", f"{pw['days']} partial days"],
    ]
    traps_fig = rk.figure(
        "fig-errors", f"Each correction lands on, or within {max(r['fixed'] for r in drows) * 100:.1f} pp of, the planted answer",
        dumb,
        rk.tag("p", Raw(f"Seed {seed}, {run['days']:,} synthetic days. Dots: error of the plain reading (grey) and of healthlab "
                        f"(blue) against the value the generator planted. Whiskers: the plain reading's range over {n} seeds; "
                        + (f"healthlab's step and sleep errors are 0 on all {n} seeds. " if d["seeds_exact"] == d["sleep_exact"] == n
                           else f"healthlab's step and sleep errors are 0 on {d['seeds_exact']} and {d['sleep_exact']} of {n} seeds. ")
                        + "The three rows measure different things — a total, a mean per night, a share of days — so compare "
                        f"within a row.{R('E1')}{R('E4')}{R('E5')}")),
        question="How far from the planted answer is each reading?",
        table=rk.data_table("Error against planted truth (%)", ["Measure", "Plain reading", f"Range over {n} seeds", "healthlab"],
                            [[r["label"], pct(r["naive"]), f"{pct(r['naive_lo'])}–{pct(r['naive_hi'])}", pct(r["fixed"])] for r in drows],
                            numeric={1, 2, 3}),
        data_href=blob("results/report.json"),
    )
    trap_table = responsive_table(f"Seven planted errors, seed {seed}: plain reading, healthlab, planted truth",
                                  ["Planted error", "Plain reading", "healthlab", "Planted truth"], trap_rows,
                                  [(r[0], f"Plain reading: {r[1]}. healthlab: {r[2]}. Truth: {r[3]}.") for r in trap_rows],
                                  wrap_cols={0, 1, 3}, tablet_list=True)
    traps_sec = rk.section(
        "traps", "A plain reading of the export gets every planted error wrong",
        rk.join([traps_fig, Raw('<h3 class="sub">All seven planted errors</h3>'), trap_table]), eyebrow="Result",
        intro=Raw("The test export comes from <code>healthlab/synth.py</code>, which writes an Apple Health zip in Apple's own "
                  "element shape and records the right answer for every error it plants. That makes each correction "
                  "scoreable: the plain reading, healthlab's reading and the truth sit side by side."))

    # steps mechanism
    ex = run["steps"]["example"]
    plain_hour = sum(s["value"] for s in ex["samples"])
    listing = "\n".join(f"{s['start']}–{s['end']} UTC  {s['source']:<22} {s['value']:>5.0f} steps" for s in ex["samples"])
    listing += f"\n\nplain sum  {plain_hour:>5.0f}\nhealthlab  {ex['kept']:>5.0f}"
    watch = sum(s["value"] for s in ex["samples"] if "Watch" in s["source"])
    phone = sum(s["value"] for s in ex["samples"] if "iPhone" in s["source"])
    steps_body = rk.join([
        Raw('<div class="bento"><div class="span-8" style="--p:1">'),
        rk.note("One hour, two devices", rk.join([
            rk.tag("p", Raw(f"The first hour in the seed-{seed} export where both devices logged steps, {esc(ex['hour_utc'])} UTC. "
                            f"The iPhone wrote one hourly sample; the Watch wrote two half-hours covering the same walk:")),
            rk.code(listing, "Step samples for one hour"),
            rk.tag("p", Raw(f"healthlab cuts the hour at every sample boundary and keeps the Watch for each piece it covers, "
                            f"because the Watch ranks first in <code>SOURCE_PRIORITY</code>; the iPhone only fills time the Watch "
                            f"left empty. Here the Watch covers the whole hour, so {phone:.0f} iPhone steps drop out and "
                            f"{watch:.0f} remain.{R('E9')}")),
        ])),
        Raw('</div><div class="span-4" style="--p:2">'),
        rk.tag("p", Raw(f"A sample that is only partly covered keeps its uncovered share, assuming steps fall evenly across "
                        f"the sample. Summed over {run['days']:,} days, that rule lands exactly on the planted step total.{R('E1')}"),
               class_="prose"),
        rk.tag("p", Raw(f'Priority order: <a class="src" href="{esc(blob("healthlab/build.py", prio))}">healthlab/build.py:{prio[0]}</a>. '
                        f"A test compares every stable day's steps with the planted value.{R('E3')}"), class_="muted"),
        Raw("</div></div>"),
    ])
    steps_sec = rk.section("steps", "Overlapping devices are counted once, in priority order", steps_body, eyebrow="Mechanism")

    # time
    weeks = t["weeks"]
    worst = max(weeks, key=lambda w: w[1])
    wdesc = (f"{len(weeks)} weeks. Export offset: weekly mean error up to {worst[1]:.0f} kcal (week ending {worst[0]}). "
             f"Rebuilt local time: up to {max(w[2] for w in weeks):.0f} kcal; zero in {sum(w[2] == 0 for w in weeks)} of {len(weeks)} weeks.")
    wtitle = "Weekly mean daily error against Apple's total, export offset versus rebuilt local time"
    wk = rk.variants(weekly_chart(weeks, t["zone_changes"], uid="wk-d", width=1000, height=380, title=wtitle, desc=wdesc),
                     weekly_chart(weeks, t["zone_changes"], uid="wk-t", width=640, height=320, title=wtitle, desc=wdesc),
                     weekly_chart(weeks, t["zone_changes"], uid="wk-p", width=320, height=340, title=wtitle, desc=wdesc))
    boise = [w for w in weeks if w[1] == 0 and w[2] == 0]
    zone_at = lambda day: next((z for c, z in reversed(t["zone_changes"]) if c <= day), None)  # noqa: E731
    if not all(zone_at(w[0]) == "America/Boise" and int(w[0][5:7]) in (11, 12, 1, 2, 3) for w in boise):
        sys.exit("a week with zero error on both methods is not a Boise winter week: rewrite the time-zone copy")
    change_days = [date.fromisoformat(c).toordinal() for c, _ in t["zone_changes"]]
    near_weeks = sum(w[2] > 0 and any(abs(date.fromisoformat(w[0]).toordinal() - c) <= 10 for c in change_days) for w in weeks)
    time_fig = rk.figure(
        "fig-time", f"The export offset is off in {sum(w[1] > 0 for w in weeks)} of {len(weeks)} weeks; rebuilt local time "
                    f"in {sum(w[2] > 0 for w in weeks)}",
        wk,
        rk.tag("p", Raw(f"Weekly mean of the absolute daily difference between Apple's ring total (keyed by the true local "
                        f"date) and the day's hourly active energy, on {t['days_compared']:,} days with a ring total; seed "
                        f"{seed}, synthetic. Dashed lines: the {len(t['zone_changes'])} zone changes; {near_weeks} of the "
                        f"{sum(w[2] > 0 for w in weeks)} weeks with any rebuilt-time error end within 10 days of one. {t['misses_near_change']} of "
                        f"the {t['misses_rebuilt']} days rebuilt time misses by more than 5% fall within 2 days of a zone change, "
                        f"which tags place only to within about a day.{R('E4')}")),
        question="Does cutting days at the rebuilt local time match Apple's own daily totals?",
        table=rk.data_table("Weekly mean daily error (kcal)", ["Week ending", "Export offset", "Rebuilt"],
                            [[w[0], f"{w[1]:.1f}", f"{w[2]:.1f}"] for w in weeks], numeric={1, 2}),
        data_href=blob("results/report.json"),
    )
    time_body = rk.join([
        time_fig,
        Raw('<div class="prose" style="margin-top:32px">'),
        rk.tag("p", Raw("Every record in an export carries the offset in force on the day of export, here −0700, whatever "
                        "zone it was recorded in. Reading it literally shifts each day's cut-off by the difference, so evening "
                        "activity lands on the next day. healthlab rebuilds the zone from <code>HKTimeZone</code> metadata, "
                        "merges aliases such as <code>US/Eastern</code>, and applies each hour's own DST offset.")),
        rk.tag("p", Raw(f"The flat stretches where both lines sit at zero ({len(boise)} weeks) are winters in America/Boise, "
                        f"where standard time is −0700 and the literal reading happens to be right.")),
        Raw("</div>"),
    ])
    time_sec = rk.section("time", f"Rebuilt local time matches Apple's daily totals on {pct(d['rebuilt5'])} of days",
                          time_body, eyebrow="Time zones")

    # seeds
    strip_rows = [
        dict(label="−0700 export offset", values=[s["time"]["within5_naive"] for s in S], accent=False,
             text=span(*d["naive5_rng"], pct)),
        dict(label="Rebuilt local time", values=[s["time"]["within5_rebuilt"] for s in S], accent=True,
             text=span(*d["rebuilt5_rng"], pct)),
    ]
    sdesc = (f"{n} seeds. Export offset: {span(*d['naive5_rng'], pct)} of days within 5%. Rebuilt local time: "
             f"{span(*d['rebuilt5_rng'], pct)}.")
    stitle = "Share of days within 5% of Apple's total, per seed"
    strip = rk.variants(*(strip_chart(strip_rows, uid=u, width=w, title=stitle, desc=sdesc,
                                      axis_title="Days within 5% of Apple's total (%)")
                          for u, w in (("sd-d", 1000), ("sd-t", 640), ("sd-p", 300))))
    seeds_fig = rk.figure(
        "fig-seeds", f"Rebuilt time beats the export offset on {d['rebuilt_better']} of {n} seeds",
        strip,
        rk.tag("p", Raw(f"One dot per seed (0–{n - 1}), {seeds['days']:,} synthetic days each; dots are spread vertically "
                        f"only to keep them visible. The axis is truncated because these are dots, not bars.{R('E5')}")),
        question="Do the results depend on the one seed shown above?",
        data_href=blob("results/seeds.json"),
    )
    med = lambda key: statistics.median(key(s) for s in S)  # noqa: E731
    ovr = lambda s: (s["steps"]["plain_sum"] - s["steps"]["truth"]) / s["steps"]["truth"]  # noqa: E731
    slo = lambda s: (s["sleep"]["longest_source_h"] - s["sleep"]["truth_h"]) / s["sleep"]["truth_h"]  # noqa: E731
    seed_rows = [
        ["Plain-sum step overcount", pct(med(ovr)), span(*d["over_rng"], pct)],
        ["Days within 5%, export offset", pct(med(lambda s: s["time"]["within5_naive"])), span(*d["naive5_rng"], pct)],
        ["Days within 5%, rebuilt local time", pct(med(lambda s: s["time"]["within5_rebuilt"])), span(*d["rebuilt5_rng"], pct)],
        ["Sleep overstatement, longest source", pct(med(slo)), span(*d["sleep_over_rng"], pct)],
        ["Pre-listed tests surviving q ≤ 0.10", f"{med(lambda s: s['family']['surviving']):g}", span(*d["surv_rng"], str)],
    ]
    seed_table = Raw('<div style="margin-top:32px">') + responsive_table(
        f"Key numbers over {n} synthetic seeds", ["Measure", "Median", "Range"], seed_rows,
        [(r[0], f"median {r[1]}, range {r[2]}") for r in seed_rows], numeric={1, 2}) + Raw("</div>")
    seeds_sec = rk.section("seeds", f"The corrections hold on {n} synthetic seeds", rk.join([seeds_fig, seed_table]),
                           eyebrow="Robustness")

    # mutants
    by_test = {}
    for m in mut["mutants"]:
        by_test.setdefault(m["test"], []).append(m)
    mut_rows = [[m["mutant"], Raw(f"<code>{esc(m['file'])}</code>"), Raw(f'<code style="{WRAP}">{esc(m["test"])}</code>'),
                 "killed" if m["killed"] else "SURVIVED"] for m in mut["mutants"]]
    shared = [k for k, v in by_test.items() if len(v) > 1]
    mut_body = rk.join([
        rk.tag("p", Raw(f"<code>scripts/mutation_check.py</code> patches one fix back to its naive version in a scratch copy of "
                        f"the repo, runs the named test from <code>tests/test_traps.py</code>, and records whether it failed. "
                        f"{d['mutants']} fixes, {d['kill_tests']} tests"
                        + (f" (one test guards {len(by_test[shared[0]])} fixes)" if shared else "")
                        + f"; CI runs it on every push.{R('E6')}"), class_="prose"),
        responsive_table(f"{d['killed']} of {d['mutants']} mutants killed", ["Fix reverted", "File", "Test that fails", "Result"],
                         mut_rows, [(m["mutant"], Raw(f'{"Killed" if m["killed"] else "SURVIVED"} by <code style="{WRAP}">{esc(m["test"])}</code>'))
                                    for m in mut["mutants"]], wrap_cols={0, 2}, tablet_list=True),
    ])
    mut_sec = rk.section("mutants", f"Reverting any one of {d['mutants']} fixes makes its test fail", mut_body,
                         eyebrow="Test strength")

    # statistics
    fam = run["family"]
    surv_rows = [[METRIC_NAMES.get(s["x"], s["x"]), METRIC_NAMES.get(s["y"], s["y"]), f"{s['lag']} day{'s' if s['lag'] > 1 else ''}",
                  f"{s['r']:.2f}", f"{s['q']:.1e}"] for s in fam["survivors"]]
    stats_body = rk.join([
        rk.tag("p", Raw(f"The discovery step runs a fixed list of {fam['tested']} cross-metric tests on detrended, "
                        f"deseasonalised series and corrects all of them together with Benjamini–Hochberg, nulls included. "
                        f"The generator plants one effect: resting heart rate {base - after:g} bpm lower (mean) in the {window} days after a run "
                        f'(<a class="src" href="{esc(blob("healthlab/synth.py", planted))}">healthlab/synth.py:{planted[0]}</a>).'),
                class_="prose"),
        responsive_table(f"Tests surviving q ≤ 0.10, seed {seed}", ["Exposure", "Outcome", "Lag", "r", "q"], surv_rows,
                         [(f"{r[0]} → {r[1]}", f"lag {r[2]}, r = {r[3]}, q = {r[4]}") for r in surv_rows], numeric={2, 3, 4}),
        rk.tag("p", Raw(f"All {fam['surviving']} are exercise followed by lower resting heart rate. Over {n} seeds, "
                        f"{span(*d['planted_rng'], str)} planted-effect tests survive on every seed. On {d['extra_seeds']} of "
                        f"{n} seeds, {span(*d['extra_rng'], str)} other tests also pass, all weak (|r| ≤ {d['extra_max_r']:.2f}); "
                        f"a 10% false-discovery threshold allows that. A test now fails if the seed-{seed} family "
                        f"changes.{R('E7')}"),
                class_="prose", style="margin-top:24px"),
    ])
    stats_sec = rk.section(
        "tests", f"On seed {seed}, {fam['surviving']} of {fam['tested']} pre-listed tests survive, all the planted effect",
        stats_body, eyebrow="Statistics")

    # how it works
    shots = rk.join([
        rk.figure("fig-ui-quality", "The Data quality view shows each correction on the user's own export",
                  Raw('<img src="img/ui-quality.png" width="1360" height="1000" decoding="async" style="width:100%;height:auto;border-radius:12px" '
                      'alt="Health Lab Data quality view: record counts, a double-counting table with raw and deduplicated '
                      'totals per metric, devices and apps, and the rebuilt time-zone timeline. Synthetic data.">'),
                  rk.tag("p", "Screenshot of the local web app (FastAPI + static JS) on the synthetic demo export. Its "
                              "“Removed” column is the share of the plain sum dropped, not the overcount relative to truth. Its "
                              "“Days match Apple” tile scores a slightly different set of days than scripts/report.py, so it "
                              "can differ from the figures above in the last digit.")),
        rk.figure("fig-ui-overview", "The overview reads only corrected tables",
                  Raw('<img src="img/ui-overview.png" width="1360" height="1000" decoding="async" style="width:100%;height:auto;border-radius:12px" '
                      'alt="Health Lab overview: record step total, 30-day averages for sleep, steps, resting heart rate and HRV, '
                      'and routine eras. Synthetic data.">'),
                  rk.tag("p", "Same app and synthetic export. Served on 127.0.0.1 only; the original export is never modified.")),
    ])
    how_body = rk.join([
        rk.figure("fig-pipeline", "Five stages from export zip to tested findings",
                  rk.variants(pipeline_svg("pl-d", 1000), pipeline_svg("pl-t", 640), pipeline_svg("pl-p", 300)),
                  rk.tag("p", Raw(f"<code>healthlab/parse.py</code> streams <code>export.xml</code> out of the zip with "
                                  f"<code>lxml</code>'s <code>iterparse</code> and writes Parquet; <code>build.py</code> loads "
                                  f"it into DuckDB and does stages 2–4; <code>insights.py</code> stage 5. The parse stage handled "
                                  f"{d['records']:,} records in {d['parse_s']:.1f}{THIN}s here.{R('E8')}"))),
        shots,
    ])
    how_sec = rk.section("how", "The export is parsed once, corrected in three stages, then tested", how_body, eyebrow="How it works")

    # methodology
    m = bench["machine"]
    method = rk.methodology([
        ("Data", f"Synthetic Apple Health export from healthlab/synth.py: {run['days']:,} days ({run['start']} to {run['end']}), "
                 f"seed {seed} in detail, seeds 0–{n - 1} for ranges. No real health data."),
        ("Truth", "Written by the generator beside each export (truth.json): planted step total, sleep per night, zones, gaps, wear."),
        ("Pipeline runs", f"python {info['python']}, DuckDB {info['duckdb']}, pandas {info['pandas']}, {info['platform']} {info['machine']}; "
                          f"run {info['date'][:10]} at code {info['code_sha'][:7]}"),
        ("Time-zone score", f"{t['days_compared']:,} days with an Apple ring total; a day matches when |hourly sum − ring total| ≤ 5% of the ring total"),
        ("Mutation check", f"{d['mutants']} one-line patches, each run against its named test (results/mutants.json)"),
        ("Parse timing", f"{d['records']:,} synthetic heart-rate records from tests/synthetic.bulk, zipped "
                         f"({bench['zip_bytes'] / 2**20:.0f} MiB zip, {bench['xml_bytes'] / 2**20:.0f} MiB XML); "
                         f"{bench['timer']}; {d['parse_reps']} repetitions, median {bench['median_s']:.2f} s, "
                         f"range {bench['min_s']:.2f}–{bench['max_s']:.2f} s"),
        ("Timing hardware", f"{m['cpu']}, {m['cores']} cores, {m['memory_gib']} GiB, {m['os']}; python {bench['python']}"),
        ("Load average", f"{load_text(bench['loadavg_before'])} before, {load_text(bench['loadavg_after'])} after (1 / 5 / 15 min); "
                         f"shared machine, timing runs serialised with a lock"),
        ("Timing run date", bench["date"][:10]),
    ])
    method_sec = rk.section("methodology", "Methodology", method, eyebrow="Conditions")

    limits = rk.limitations([
        ("Synthetic data only.", "Every number here comes from generated exports; real exports carry device and app quirks the "
                                 "generator does not plant, so exactness on real data is not measured."),
        ("The generator and the fixes share an author.", "Each trap is planted in the form the fix expects; a different "
                                                         "overlap pattern (for example a third step source) is untested."),
        ("Zone changes are placed to within about a day.", f"{t['misses_near_change']} of {t['misses_rebuilt']} rebuilt-time "
                                                           f"misses sit within 2 days of a zone change."),
        ("Even-rate assumption.", "A partly covered sample keeps a share proportional to time, which is exact for the "
                                  "generator's uniform samples and approximate on real ones."),
        ("One machine for timing.", f"The parse time is from one {m['cpu']} under load {load_text(bench['loadavg_before'])}; "
                                    f"it is shown as a scale, not a benchmark against other tools."),
        ("Rule-based questions.", "The app's question box matches a fixed set of question shapes; it is not a language model."),
    ])
    limits_sec = rk.section("limitations", "Results cover synthetic exports from one generator", limits, eyebrow="Limitations")

    steps_html = rk.reproduce([
        ("Python 3.11 virtualenv with pinned dependencies, then the test suite (includes the trap tests).", "make setup\nmake test"),
        ("Mutation check: rewrites results/mutants.json and exits non-zero if any mutant survives.", "make mutants"),
        (f"Rerun the synthetic experiment: seed {seed} in detail, then seeds 0–{n - 1}; rewrites results/report.json and results/seeds.json.",
         ".venv/bin/python scripts/report.py\n.venv/bin/python scripts/report.py --seeds " + str(n)),
        (f"Time the parser on {d['records']:,} records ({d['parse_reps']} repetitions).", ".venv/bin/python scripts/bench_parse.py"),
        ("Rebuild this page, site/card.json and the README highlights from results/ (standard library only).", "make report"),
        ("Try the app on the synthetic export, or on your own export from the Health app.",
         "make demo                        # http://127.0.0.1:8877\n./run.sh ~/Downloads/export.zip"),
    ])
    repro_sec = rk.section("reproduce", "Reproduce it from a clone", steps_html, eyebrow="Reproduce",
                           intro=Raw(f'Commands run from a clone of <a class="src" href="{REPO_URL}">{REPO}</a>.'))

    ev_table = rk.table("Evidence ledger", ["ID", "Claim", "Source", "Class"],
                        [[Raw(f'<span id="evidence-{eid.lower()}">{eid}</span>'), claim,
                          Raw(f'<a class="src" href="{esc(href)}">{esc(src)}</a>'), cls] for eid, claim, src, href, cls in evidence],
                        wrap_cols={1, 2})
    ev_sec = rk.section("evidence", "Every number traces to a committed file", ev_table, eyebrow="Sources")

    main = rk.join([hero, nav, findings_sec, traps_sec, steps_sec, time_sec, seeds_sec, mut_sec, stats_sec, how_sec,
                    method_sec, limits_sec, repro_sec, ev_sec])
    footer = rk.footer(
        Raw(f"Generated by <code>scripts/build_report.py</code> from <code>results/</code> · pipeline code "
            f"{esc(info['code_sha'][:7])} · runs {esc(info['date'][:10])} · synthetic data"),
        Raw(f'<a href="{HOME_URL}">Allen Wang</a> · <a href="{REPO_URL}">Source</a>'))
    return rk.page(
        title="Health export lab · Allen Wang",
        description=(f"A local Apple Health analyzer that removes a {pct_g(d['over'])} step overcount exactly and rebuilds "
                     f"local time, scored against planted truth in synthetic exports."),
        canonical=PAGE_URL,
        header=rk.report_header(HOME_URL, "← Allen Wang", [("Source", REPO_URL)]),
        main=main, footer=footer)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=str(ROOT / "results"))
    ap.add_argument("--out", default=str(ROOT / "report" / "index.html"))
    ap.add_argument("--card", help="also write the portfolio card JSON")
    ap.add_argument("--readme", help="rewrite the highlights block between the README markers")
    a = ap.parse_args()
    data = load(a.results)
    d = derive(data)
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_page(data, d), encoding="utf-8")
    if a.card:
        pathlib.Path(a.card).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.card).write_text(json.dumps(build_card(data, d), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.readme:
        update_readme(a.readme, data, d)


if __name__ == "__main__":
    main()
