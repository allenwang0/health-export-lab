"""Reusable statistics. Everything respects time order and is deterministic (fixed seeds).

Daily health series are autocorrelated, so naive p-values overstate certainty. These
functions use effective sample size, HAC standard errors and block resampling instead.
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats as sps

SEED = 20260101


def ar1(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 4 or np.std(x) == 0:
        return 0.0
    r = np.corrcoef(x[:-1], x[1:])[0, 1]
    return float(np.clip(r, -0.99, 0.99))


def effective_n(x, y):
    """Bretherton-style effective sample size for a correlation of two AR(1) series."""
    n = len(x)
    r1, r2 = ar1(x), ar1(y)
    return max(3.0, min(n, n * (1 - r1 * r2) / (1 + r1 * r2)))


def corr(x, y):
    """Pearson and Spearman with autocorrelation-adjusted p and Fisher CI."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = ~(np.isnan(x) | np.isnan(y))
    x, y = x[m], y[m]
    n = len(x)
    if n < 10 or np.std(x) == 0 or np.std(y) == 0:
        return dict(n=int(n), r=None, rho=None, p=None, neff=None, ci=[None, None])
    r = float(np.corrcoef(x, y)[0, 1])
    rho = float(sps.spearmanr(x, y)[0])
    neff = effective_n(x, y)
    t = abs(r) * np.sqrt(max(neff - 2, 1) / max(1 - r * r, 1e-9))
    p = float(2 * sps.t.sf(t, max(neff - 2, 1)))
    z, se = np.arctanh(np.clip(r, -0.999, 0.999)), 1 / np.sqrt(max(neff - 3, 1))
    return dict(n=int(n), r=r, rho=rho, p=p, neff=float(neff),
                ci=[float(np.tanh(z - 1.96 * se)), float(np.tanh(z + 1.96 * se))])


def bh_fdr(pvals):
    """Benjamini-Hochberg q-values; NaN p-values stay NaN and do not count as tests."""
    p = np.asarray(pvals, float)
    q = np.full(len(p), np.nan)
    idx = np.where(~np.isnan(p))[0]
    if len(idx) == 0:
        return q
    order = idx[np.argsort(p[idx])]
    m = len(order)
    adj = p[order] * m / np.arange(1, m + 1)
    q[order] = np.minimum.accumulate(adj[::-1])[::-1].clip(0, 1)
    return q


def residualize(s: pd.Series, trend_days=91):
    """Remove slow trend and calendar structure so shared drift cannot fake a correlation."""
    s = s.astype(float)
    if s.notna().sum() < 30:
        return s * np.nan
    trend = s.rolling(trend_days, center=True, min_periods=max(14, trend_days // 4)).median()
    r = s - trend
    r = r - r.groupby(r.index.dayofweek).transform("mean")
    return r - r.groupby(r.index.month).transform("mean")


def lag_curve(x: pd.Series, y: pd.Series, max_lag=7):
    """corr(x[t], y[t+lag]) on residualized series. Positive lag means x leads y."""
    rx, ry = residualize(x), residualize(y)
    return [dict(lag=k, **corr(rx.values, ry.shift(-k).values)) for k in range(-max_lag, max_lag + 1)]


def rolling_corr(x: pd.Series, y: pd.Series, window=180):
    r = residualize(x).rolling(window, min_periods=window // 2).corr(residualize(y))
    return r.dropna().iloc[::14]


def circular_shift_p(x, y, n=999):
    """Permutation p that keeps each series' autocorrelation by rotating one against the other."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = ~(np.isnan(x) | np.isnan(y))
    x, y = x[m], y[m]
    if len(x) < 40:
        return None
    rng = np.random.default_rng(SEED)
    x_, y_ = (x - x.mean()) / (x.std() or 1), (y - y.mean()) / (y.std() or 1)
    obs = abs(np.mean(x_ * y_))
    shifts = rng.integers(14, len(x) - 14, n)
    null = np.array([abs(np.mean(np.roll(x_, s) * y_)) for s in shifts])
    return float((1 + (null >= obs).sum()) / (n + 1))


def block_bootstrap(fn, arrays, block=14, n=1000):
    """Moving-block bootstrap CI of fn(*arrays). Arrays are resampled together."""
    length = len(arrays[0])
    if length < block * 2:
        return [None, None]
    rng = np.random.default_rng(SEED)
    k = int(np.ceil(length / block))
    out = []
    for _ in range(n):
        starts = rng.integers(0, length - block + 1, k)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:length]
        v = fn(*[a[idx] for a in arrays])
        if v is not None and not np.isnan(v):
            out.append(v)
    return [float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))] if out else [None, None]


def ols_hac(df: pd.DataFrame, outcome, terms, lags=14):
    """OLS with Newey-West errors. Returns per-term estimates, CI and p."""
    d = df[[outcome] + terms].astype(float).dropna()
    if len(d) < len(terms) + 10:
        return None
    X = sm.add_constant(d[terms], has_constant="add")
    fit = sm.OLS(d[outcome], X).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    ci = fit.conf_int()
    return dict(n=len(d), r2=float(fit.rsquared), terms={
        t: dict(b=float(fit.params[t]), lo=float(ci.loc[t, 0]), hi=float(ci.loc[t, 1]), p=float(fit.pvalues[t]))
        for t in terms})


def calendar_terms(df: pd.DataFrame):
    """Weekend, annual harmonics and linear time: the default adjustment set."""
    idx = pd.DatetimeIndex(df.index)
    out = df.copy()
    doy = idx.dayofyear.values
    out["_weekend"] = (idx.dayofweek >= 5).astype(float)
    out["_sin"], out["_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    out["_t"] = (idx - idx[0]).days / 365.25
    return out, ["_weekend", "_sin", "_cos", "_t"]


def hedges_g(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    sp = np.sqrt(((n1 - 1) * a.var(ddof=1) + (n2 - 1) * b.var(ddof=1)) / (n1 + n2 - 2))
    if sp == 0:
        return None
    return float((a.mean() - b.mean()) / sp * (1 - 3 / (4 * (n1 + n2) - 9)))


def compare(df: pd.DataFrame, outcome, group_col, covariates=None, adjust=True):
    """Group 1 vs group 0 on outcome. df is daily-indexed; group_col is 1/0/NaN."""
    d = df[[outcome, group_col] + (covariates or [])].dropna(subset=[outcome, group_col])
    a, b = d[d[group_col] == 1][outcome], d[d[group_col] == 0][outcome]
    res = dict(n_a=len(a), n_b=len(b), mean_a=_f(a.mean()), mean_b=_f(b.mean()), median_a=_f(a.median()),
               median_b=_f(b.median()), diff=_f(a.mean() - b.mean()), g=hedges_g(a, b))
    if len(a) < 5 or len(b) < 5:
        res["warning"] = "Fewer than 5 observations in a group; no test was run."
        return res
    un = ols_hac(d, outcome, [group_col])
    res["unadjusted"] = un["terms"][group_col] if un else None
    if adjust:
        dd, cal = calendar_terms(d)
        # a term identical to the group (weekend vs weekday) would make the model singular
        cal = [c for c in cal if abs(np.corrcoef(dd[c].astype(float), dd[group_col].astype(float))[0, 1]) < .999]
        fit = ols_hac(dd, outcome, [group_col] + cal + (covariates or []))
        if fit:
            res["adjusted"] = fit["terms"][group_col]
            res["adjusted_terms"] = {k: v for k, v in fit["terms"].items() if k != group_col}
    return res


def _f(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def theil_sen(s: pd.Series):
    """Robust trend on a monthly-median series; slope per year, with Kendall p corrected for AR(1)."""
    m = s.dropna().resample("MS").median().dropna()
    if len(m) < 12:
        return None
    t = (m.index - m.index[0]).days.values / 365.25
    slope, icpt, lo, hi = sps.theilslopes(m.values, t, 0.95)
    tau, p = sps.kendalltau(t, m.values)
    rho = ar1(m.values - (icpt + slope * t))
    neff_ratio = (1 - rho) / (1 + rho)
    z = sps.norm.isf(p / 2) * np.sqrt(max(neff_ratio, 0.05)) if p > 0 else 8
    return dict(slope=float(slope), lo=float(lo), hi=float(hi), tau=float(tau), p=float(2 * sps.norm.sf(z)),
                months=len(m), first=str(m.index[0].date()), last=str(m.index[-1].date()))


def change_points(s: pd.Series, min_weeks=10, max_cp=5):
    """Binary segmentation of weekly medians with a BIC-style penalty.

    Returns [{date, before, after, delta, g}] oldest first. The penalty scales with
    variance after AR(1) inflation, so ordinary drift does not register as a shift.
    """
    w = s.dropna().resample("W-MON").median().dropna()
    if len(w) < min_weeks * 2:
        return []
    x = w.values
    rho = max(ar1(x - pd.Series(x).rolling(13, center=True, min_periods=4).median().values), 0)
    sigma2 = np.median(np.abs(np.diff(x))) ** 2 / (2 * 0.4549)
    penalty = 3 * np.log(len(x)) * max(sigma2, 1e-9) * (1 + rho) / (1 - min(rho, 0.9))

    def best(a, b):
        cs = np.concatenate([[0], np.cumsum(x[a:b])])
        cs2 = np.concatenate([[0], np.cumsum(x[a:b] ** 2)])

        def sse(i, j):
            return cs2[j] - cs2[i] - (cs[j] - cs[i]) ** 2 / (j - i)
        n, whole, top = b - a, sse(0, b - a), (None, 0)
        for k in range(min_weeks, n - min_weeks + 1):
            gain = whole - sse(0, k) - sse(k, n)
            if gain > top[1]:
                top = (a + k, gain)
        return top

    cuts, segs = [], [(0, len(x))]
    while len(cuts) < max_cp:
        cands = [(best(a, b), (a, b)) for a, b in segs if b - a >= 2 * min_weeks]
        cands = [c for c in cands if c[0][0] is not None and c[0][1] > penalty]
        if not cands:
            break
        (k, _), (a, b) = max(cands, key=lambda c: c[0][1])
        cuts.append(k)
        segs.remove((a, b))
        segs += [(a, k), (k, b)]
    cuts = sorted(cuts)
    bounds = [0] + cuts + [len(x)]
    out = []
    for i, k in enumerate(cuts):
        before, after = x[bounds[i]:k], x[k:bounds[i + 2]]
        out.append(dict(date=str(w.index[k].date()), before=float(np.median(before)), after=float(np.median(after)),
                        delta=float(np.median(after) - np.median(before)), g=hedges_g(after, before),
                        n_before=len(before), n_after=len(after)))
    return out


def rolling_robust_z(s: pd.Series, window=90, min_periods=30):
    """Deviation from the trailing median in MAD units. The current day is excluded."""
    p = s.shift(1)
    med = p.rolling(window, min_periods=min_periods).median()
    mad = (p - med).abs().rolling(window, min_periods=min_periods).median() * 1.4826
    return (s - med) / mad.where(mad > 0)


def stl_parts(s: pd.Series, period=7):
    from statsmodels.tsa.seasonal import STL
    x = s.interpolate(limit=7).dropna()
    if len(x) < period * 4:
        return None
    r = STL(x, period=period, robust=True).fit()
    return dict(trend=r.trend, seasonal=r.seasonal, resid=r.resid)


def cluster_days(df: pd.DataFrame, cols, k_range=(3, 7), runs=20):
    """K-means day archetypes with silhouette-picked k and subsampling stability (ARI)."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_rand_score, silhouette_score
    d = df[cols].dropna()
    if len(d) < 100:
        return None
    z = ((d - d.median()) / (d.quantile(.75) - d.quantile(.25)).replace(0, 1)).clip(-4, 4).values
    best = None
    for k in range(k_range[0], k_range[1] + 1):
        lab = KMeans(k, n_init=10, random_state=SEED).fit_predict(z)
        s = silhouette_score(z, lab, sample_size=min(1500, len(z)), random_state=SEED)
        if best is None or s > best[1]:
            best = (k, s, lab)
    k, sil, lab = best
    rng = np.random.default_rng(SEED)
    aris = []
    for _ in range(runs):
        i = rng.choice(len(z), int(.8 * len(z)), replace=False)
        aris.append(adjusted_rand_score(lab[i], KMeans(k, n_init=5, random_state=SEED).fit_predict(z[i])))
    return dict(k=k, silhouette=float(sil), stability_ari=float(np.mean(aris)),
                labels=pd.Series(lab, index=d.index), centers=pd.DataFrame(z, columns=cols).groupby(lab).median())
