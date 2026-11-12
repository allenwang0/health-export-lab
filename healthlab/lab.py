"""Hypothesis engine shared by the Lab, Cohorts and Ask views.

A condition is {"metric", "op", "value"}. A spec is explicit about exposure, comparison,
outcome and lag, so every result can be read back as the query that produced it.
"""
import re

import numpy as np
import pandas as pd

from .catalog import METRICS
from .data import json_safe
from . import stats as st

OPS = {">": lambda s, v: s > v, ">=": lambda s, v: s >= v, "<": lambda s, v: s < v, "<=": lambda s, v: s <= v,
       "==": lambda s, v: s == v, "!=": lambda s, v: s != v}

SYNONYMS = [("resting heart rate", "rhr"), ("resting hr", "rhr"), ("rhr", "rhr"), ("hrv", "hrv_sdnn"), ("heart rate variability", "hrv_sdnn"),
            ("vo2", "vo2max"), ("sleep efficiency", "sleep_eff"), ("deep sleep", "deep_h"), ("rem", "rem_h"), ("bedtime", "bed_clock_adj"), ("sleep onset", "bed_clock_adj"),
            ("wake time", "wake_clock"), ("sleep", "asleep_watch_h"), ("steps", "steps"), ("step count", "steps"), ("distance", "distance_mi"),
            ("active energy", "active_kcal_worn"), ("flights", "flights"), ("stairs", "flights"), ("running distance", "run_mi"), ("workout minutes", "workout_min"),
            ("walking heart rate", "walking_hr"), ("respiratory rate", "resp_rate"), ("walking speed", "walk_speed_mph"), ("weight", "weight_lb"), ("water", "water_ml")]
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12}


def mask(df, conds):
    """Rows satisfying every condition. A NULL metric never satisfies one: unknown is not false."""
    m = pd.Series(True, index=df.index)
    for c in conds:
        if c["metric"] == "weekend":
            m &= df.weekend == bool(c["value"])
        elif c["metric"] == "dow":
            m &= df.dow == c["value"]
        elif c["metric"] == "date":
            m &= OPS[c["op"]](pd.Series(df.index, index=df.index), pd.Timestamp(c["value"]))
        elif c["metric"] == "year":
            m &= OPS[c.get("op", "==")](df.yr, c["value"])
        else:
            s = df[c["metric"]]
            m &= s.notna() & OPS[c["op"]](s, c["value"])
    return m


def describe(conds):
    def one(c):
        if c["metric"] == "weekend":
            return "weekend" if c["value"] else "weekday"
        name = METRICS.get(c["metric"], {}).get("label", c["metric"])
        return f"{name} {c.get('op', '=')} {c['value']}"
    return " and ".join(one(c) for c in conds) or "all days"


def run_experiment(d, spec):
    """spec: outcome, exposure conds, control conds, lag, covariates, exclude conds, start, end."""
    out, lag = spec["outcome"], int(spec.get("lag", 0))
    if out not in d.columns:
        return dict(error=f"Unknown outcome {out}.")
    f = d.copy()
    if spec.get("start"):
        f = f[f.index >= spec["start"]]
    if spec.get("end"):
        f = f[f.index <= spec["end"]]
    exp, ctl = mask(f, spec.get("exposure", [])), mask(f, spec.get("control", []))
    both = exp & ctl
    g = pd.Series(np.where(exp & ~both, 1.0, np.where(ctl & ~both, 0.0, np.nan)), index=f.index)
    if spec.get("exclude"):
        g[mask(f, spec["exclude"])] = np.nan
    t = pd.DataFrame({"y": f[out].shift(-lag), "g": g}, index=f.index)
    covs = [c for c in spec.get("covariates", []) if c in f.columns]
    for c in covs:
        t[c] = f[c]
    use = t.dropna(subset=["y", "g"])
    res = st.compare(use, "y", "g", covs)
    a, b = use[use.g == 1].y, use[use.g == 0].y
    # alternative specifications, so a result that depends on one choice is visible
    alts = []
    for name, sub in [("Unadjusted, last 3 years", use[use.index >= use.index.max() - pd.Timedelta(days=1095)] if len(use) else use),
                      ("Worn days only", use[f.worn_day.reindex(use.index).fillna(False)]),
                      ("Excluding travel days", use[f.tz.reindex(use.index) == f.tz.mode().iloc[0]] if f.tz.notna().any() else use.iloc[:0])]:
        r = st.compare(sub, "y", "g", covs)
        alts.append(dict(name=name, n_a=r["n_a"], n_b=r["n_b"], diff=r["diff"], adjusted=r.get("adjusted")))
    warn = []
    if len(a) < 30 or len(b) < 30:
        warn.append("Small sample in at least one group. Treat the estimate as tentative.")
    if res.get("n_a") and f[out].notna().sum() < .5 * len(f):
        warn.append(f"{METRICS.get(out, {}).get('label', out)} is missing on {1 - f[out].notna().mean():.0%} of days in range.")
    q = (f"Compare {METRICS.get(out, {}).get('label', out)}{f' {lag} day(s) later' if lag else ''} "
         f"when [{describe(spec.get('exposure', []))}] against [{describe(spec.get('control', []))}]. "
         f"Adjusted for weekend, annual season, linear time{', ' + ', '.join(covs) if covs else ''}, with Newey-West errors.")
    r = dict(query=q, spec=spec, result=res, alternatives=alts, warnings=warn, a=_hist(a), b=_hist(b),
             dist=dict(a=_quant(a), b=_quant(b)), confounding=[
        "These days were not randomly assigned; you chose when to do these things.",
        "Illness, travel, stress and alcohol are not recorded and could affect both groups.",
        "Time trend and season are adjusted for, but only approximately."])
    r["plain"] = _plain(r, out, lag)
    return json_safe(r)


def _hist(s):
    return [round(float(v), 3) for v in s.dropna().values]


def _quant(s):
    s = s.dropna()
    return None if s.empty else [float(s.min()), *map(float, s.quantile([.25, .5, .75])), float(s.max())]


def _plain(r, out, lag):
    res, unit = r["result"], METRICS.get(out, {}).get("unit", "")
    a = res.get("adjusted")
    if not a:
        return res.get("warning", "Not enough observations to compare.")
    clear = a["lo"] > 0 or a["hi"] < 0
    verdict = "The interval excludes zero." if clear else "The interval includes zero, so the data do not establish a difference."
    return (f"Exposed days averaged {res['mean_a']:.2f} and comparison days {res['mean_b']:.2f} {unit}. After adjustment the difference is "
            f"{a['b']:+.2f} {unit} (95% CI {a['lo']:+.2f} to {a['hi']:+.2f}). {verdict} This is an observational comparison, not an experiment.")


def find_metric(text):
    t = text.lower()
    hits = [(t.find(k), -len(k), v) for k, v in SYNONYMS if k in t]
    return min(hits)[2] if hits else None


def parse_hypothesis(text):
    """Translate an ordinary-language comparison into a spec. Returns spec or None.

    Deliberately rule-based: no text leaves the machine and no model can invent a result.
    """
    t = text.lower().replace("-", " ")
    for w, n in WORDS.items():
        t = re.sub(rf"\b{w}\b", str(n), t)
    spec = dict(lag=0, exposure=[], control=[])
    parts = re.split(r"\b(?:against|versus|vs\.?|compared (?:to|with))\b", t, maxsplit=1)
    head = parts[0]
    if re.search(r"\b(days? )?(following|after)\b", head):
        spec["lag"] = 1
    pre = re.split(r"\b(?:on days|on|days|after|following|when)\b", head, maxsplit=1)[0]
    spec["outcome"] = find_metric(pre) or find_metric(head) or "rhr"
    m = re.search(r"(\d+) days? (?:after|later|following)", head)
    if m:
        spec["lag"] = int(m.group(1))
    if re.search(r"\b(run|runs|running)\b", head):
        mi = re.search(r"(?:longer than|over|more than|above|at least)\s*(\d+(?:\.\d+)?)\s*(?:mile|mi)", head)
        spec["exposure"] = [dict(metric="run_mi", op=">=", value=float(mi.group(1)) if mi else .01)]
        spec["control"] = [dict(metric="run_mi", op="==", value=0)]
    else:
        ctx = re.split(r"\b(?:on days|following|after|when)\b", head, maxsplit=1)[-1]
        name = find_metric(ctx)
        num = re.search(r"(?:than|over|above|under|below|least|most)\s*([\d,]+(?:\.\d+)?)", ctx)
        hrs = re.search(r"([\d.]+)\s*(?:h\b|hours?)", ctx)
        if name and (num or hrs):
            v = float((num or hrs).group(1).replace(",", ""))
            op = "<" if re.search(r"under|below|less|fewer|shorter", ctx) else ">="
            spec["exposure"] = [dict(metric=name, op=op, value=v)]
            spec["control"] = [dict(metric=name, op=">=" if op == "<" else "<", value=v)]
        elif "short sleep" in ctx:
            spec["exposure"] = [dict(metric="asleep_watch_h", op="<", value=6)]
            spec["control"] = [dict(metric="asleep_watch_h", op=">=", value=7)]
        elif "weekend" in head:
            spec["exposure"], spec["control"] = [dict(metric="weekend", value=True)], [dict(metric="weekend", value=False)]
        else:
            return None
    return spec


def compare_cohorts(d, a, b, metrics):
    ma, mb = mask(d, a), mask(d, b)
    rows = []
    for m in metrics:
        t = pd.DataFrame({"y": d[m], "g": np.where(ma, 1.0, np.where(mb, 0.0, np.nan))}, index=d.index)
        r = st.compare(t.dropna(subset=["y", "g"]), "y", "g")
        rows.append(dict(metric=m, label=METRICS[m]["label"], unit=METRICS[m]["unit"], **{k: r.get(k) for k in ("n_a", "n_b", "mean_a", "mean_b", "g", "adjusted")}))
    rows = [r for r in rows if r["n_a"] and r["n_b"]]
    ps = [r["adjusted"]["p"] if r.get("adjusted") else np.nan for r in rows]
    for r, q in zip(rows, st.bh_fdr(ps)):
        r["q"] = q
    return json_safe(dict(n_a=int(ma.sum()), n_b=int(mb.sum()), a=describe(a), b=describe(b), rows=rows, warning="Cohorts chosen after seeing the data are subject to post-hoc selection bias. q-values correct only for the metrics listed here."))
