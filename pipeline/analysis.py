"""Stock analysis engine (stdlib only): extended technicals, confluence score, trade levels and a
3-state Gaussian hidden Markov model for bull / sideways / bear regimes, with a walk-forward check.

Methodology follows quant-signals-guide.md: regimes are fitted on standardised daily log-returns and
rolling volatility (Baum-Welch), the tradeable signal is the *filtered* forward probability (never Viterbi),
scalers are fitted on training data only, and the walk-forward uses expanding windows.
Descriptive analytics, not financial advice.
"""
import datetime as dt
import math
import statistics

import lib

STATES = ("bear", "sideways", "bull")  # ordered by fitted mean return, lowest first


# ----------------------------------------------------------------------------- extra indicators
def adx(h, l, c, n=14):
    """Wilder ADX with +DI and -DI. Returns (adx, plus_di, minus_di) for the last bar, or Nones."""
    if len(c) < 2 * n + 1: return None, None, None
    tr, pdm, mdm = [], [], []
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    def wilder(x):
        s = sum(x[:n]); out = [s]
        for v in x[n:]:
            s = s - s / n + v; out.append(s)
        return out
    atr_, p_, m_ = wilder(tr), wilder(pdm), wilder(mdm)
    pdi = [100 * p / a if a else 0 for p, a in zip(p_, atr_)]
    mdi = [100 * m / a if a else 0 for m, a in zip(m_, atr_)]
    dx = [100 * abs(p - m) / (p + m) if (p + m) else 0 for p, m in zip(pdi, mdi)]
    a = sum(dx[:n]) / n
    for v in dx[n:]: a = (a * (n - 1) + v) / n
    return a, pdi[-1], mdi[-1]

def aroon_osc(h, l, n=25):
    if len(h) < n + 1: return None
    wh, wl = h[-(n + 1):], l[-(n + 1):]
    up = (n - wh[::-1].index(max(wh))) / n * 100
    dn = (n - wl[::-1].index(min(wl))) / n * 100
    return up - dn

def wma(v, n):
    if len(v) < n: return None
    w = v[-n:]; return sum((i + 1) * x for i, x in enumerate(w)) / (n * (n + 1) / 2)

def hma(v, n=20):
    """Hull moving average for the last bar."""
    half, root = n // 2, int(math.sqrt(n))
    if len(v) < n + root: return None
    raw = []
    for i in range(len(v) - root, len(v)):
        a, b = wma(v[:i + 1], half), wma(v[:i + 1], n)
        raw.append(2 * a - b)
    return wma(raw, root)

def obv_trend(c, v, n=20):
    """OBV and whether it is above its own n-day average (rising)."""
    obv, tot = [], 0.0
    for i in range(len(c)):
        if i: tot += v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0
        obv.append(tot)
    if len(obv) < n + 1: return None
    return "Rising" if obv[-1] > sum(obv[-n:]) / n and obv[-1] > obv[-n] else "Falling" if obv[-1] < sum(obv[-n:]) / n and obv[-1] < obv[-n] else "Flat"

def cmf(h, l, c, v, n=20):
    num = den = 0.0
    for i in range(len(c) - n, len(c)):
        rng = h[i] - l[i]
        num += ((2 * c[i] - h[i] - l[i]) / rng * v[i]) if rng else 0
        den += v[i]
    return num / den if den else None

def pivots(h, l, c):
    """Classic pivot points from the previous completed session."""
    H, L, C = h[-1], l[-1], c[-1]
    p = (H + L + C) / 3
    return {"p": p, "r1": 2 * p - L, "s1": 2 * p - H, "r2": p + (H - L), "s2": p - (H - L),
            "r3": H + 2 * (p - L), "s3": L - 2 * (H - p)}

def keltner(c, h, l, n=20, mult=2.0):
    e = lib.ema(c, n)[-1]; a = lib.atr(h, l, c, n)[-1]
    return (e + mult * a, e - mult * a) if e is not None and a is not None else (None, None)

def weekly(t, c):
    """Collapse daily closes to weekly closes (last close of each ISO week)."""
    out = {}
    for ts, x in zip(t, c):
        d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).isocalendar()
        out[(d[0], d[1])] = x
    return [out[k] for k in sorted(out)]

def weekly_overlay(t, c):
    w = weekly(t, c)
    if len(w) < 30: return None
    r = lib.rsi(w)[-1]; e = lib.ema(w, 20)
    trend = "Uptrend" if w[-1] > e[-1] and e[-1] > e[-4] else "Downtrend" if w[-1] < e[-1] and e[-1] < e[-4] else "Sideways"
    return {"rsi": r, "ema20": e[-1], "trend": trend}

def momentum_12_1(c):
    """Return over the past 12 months excluding the most recent month (skip-month momentum)."""
    return (c[-22] / c[-252] - 1) * 100 if len(c) > 252 else None


# ----------------------------------------------------------------------------- hidden Markov model
def features(c, train_end=None):
    """Standardised [log return, 20d realised vol]. Scaler uses c[:train_end] only (no look-ahead)."""
    r = [0.0] + [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    vol = [None] * len(c)
    for i in range(20, len(c)):
        vol[i] = statistics.pstdev(r[i - 19:i + 1])
    start = 20
    rows = [(r[i], vol[i]) for i in range(start, len(c))]
    k = len(rows) if train_end is None else max(2, train_end - start)
    tr = rows[:k]
    mu = [sum(x[j] for x in tr) / len(tr) for j in (0, 1)]
    sd = [statistics.pstdev([x[j] for x in tr]) or 1e-9 for j in (0, 1)]
    return [[(x[j] - mu[j]) / sd[j] for j in (0, 1)] for x in rows], r[start:]

def _logpdf(x, m, v):
    return sum(-0.5 * (math.log(2 * math.pi * v[j]) + (x[j] - m[j]) ** 2 / v[j]) for j in range(len(x)))

def _emis(X, m, v):
    """Per-time emission likelihoods, scaled per row to avoid underflow."""
    B = []
    for x in X:
        lp = [_logpdf(x, m[s], v[s]) for s in range(3)]
        mx = max(lp); B.append([math.exp(a - mx) for a in lp])
    return B

def _forward(B, A, pi):
    alpha, scale = [], []
    prev = None
    for t, b in enumerate(B):
        a = [(pi[s] if t == 0 else sum(prev[q] * A[q][s] for q in range(3))) * b[s] for s in range(3)]
        z = sum(a) or 1e-300
        a = [x / z for x in a]; alpha.append(a); scale.append(z); prev = a
    return alpha, scale

def fit_hmm(X, iters=30):
    """Baum-Welch for a 3-state diagonal-Gaussian HMM. Returns parameters with states ordered bear < sideways < bull."""
    T, D = len(X), len(X[0])
    order = sorted(range(T), key=lambda i: X[i][0])
    m = [X[order[int(T * q)]][:] for q in (0.15, 0.5, 0.85)]
    gv = [max(statistics.pvariance([x[j] for x in X]), 1e-3) for j in range(D)]
    v = [gv[:] for _ in range(3)]
    A = [[0.9 if i == j else 0.05 for j in range(3)] for i in range(3)]
    pi = [1 / 3] * 3
    for _ in range(iters):
        B = _emis(X, m, v)
        alpha, scale = _forward(B, A, pi)
        beta = [[1.0] * 3 for _ in range(T)]
        for t in range(T - 2, -1, -1):
            for i in range(3):
                beta[t][i] = sum(A[i][j] * B[t + 1][j] * beta[t + 1][j] for j in range(3)) / scale[t + 1]
        gamma = []
        for t in range(T):
            g = [alpha[t][i] * beta[t][i] for i in range(3)]; z = sum(g) or 1e-300
            gamma.append([x / z for x in g])
        xi = [[0.0] * 3 for _ in range(3)]
        for t in range(T - 1):
            tot = 0.0; c = [[0.0] * 3 for _ in range(3)]
            for i in range(3):
                for j in range(3):
                    c[i][j] = alpha[t][i] * A[i][j] * B[t + 1][j] * beta[t + 1][j]; tot += c[i][j]
            if tot:
                for i in range(3):
                    for j in range(3): xi[i][j] += c[i][j] / tot
        A = []
        for i in range(3):
            row = [xi[i][j] + 1e-3 for j in range(3)]; z = sum(row); A.append([x / z for x in row])
        pi = gamma[0]
        for s in range(3):
            w = sum(g[s] for g in gamma) or 1e-300
            m[s] = [sum(gamma[t][s] * X[t][j] for t in range(T)) / w for j in range(D)]
            v[s] = [max(sum(gamma[t][s] * (X[t][j] - m[s][j]) ** 2 for t in range(T)) / w, 0.02) for j in range(D)]
    idx = sorted(range(3), key=lambda s: m[s][0])  # bear, sideways, bull by mean return
    return {"m": [m[s] for s in idx], "v": [v[s] for s in idx],
            "A": [[A[i][j] for j in idx] for i in idx], "pi": [pi[s] for s in idx]}

def filtered(model, X):
    """Filtered state probabilities for every step (uses only data up to each step)."""
    alpha, _ = _forward(_emis(X, model["m"], model["v"]), model["A"], model["pi"])
    return alpha

def moments(rets, weights):
    w = sum(weights) or 1e-300
    mean = sum(r * g for r, g in zip(rets, weights)) / w
    var = sum(g * (r - mean) ** 2 for r, g in zip(rets, weights)) / w
    sd = math.sqrt(var) or 1e-12
    sk = sum(g * ((r - mean) / sd) ** 3 for r, g in zip(rets, weights)) / w
    ku = sum(g * ((r - mean) / sd) ** 4 for r, g in zip(rets, weights)) / w - 3
    return {"mean_pct": mean * 100, "vol_ann_pct": sd * math.sqrt(252) * 100, "skew": sk, "ex_kurtosis": ku}

def walk_forward(c, min_train=252, step=21):
    """Expanding-window walk-forward: refit every `step` days on data up to t only, classify the next
    `step` days with the frozen model, and record the following-day return by predicted regime."""
    n = len(c)
    buckets = {s: [] for s in STATES}
    refits = 0
    t = min_train
    while t + 2 < n:
        X, _ = features(c[:t + 1])
        if len(X) < 120: t += step; continue
        model = fit_hmm(X, iters=15); refits += 1
        end = min(t + step, n - 1)
        Xall, rets_all = features(c[:end + 1], train_end=t + 1)  # scaler frozen on training data
        alpha = filtered(model, Xall)
        off = len(c[:end + 1]) - len(Xall)
        for d in range(t, end):
            a = alpha[d - off]
            k = max(range(3), key=lambda s: a[s])
            nxt = math.log(c[d + 1] / c[d])
            buckets[STATES[k]].append(nxt)
        t += step
    res = {"refits": refits}
    for s in STATES:
        b = buckets[s]
        res[s] = {"days": len(b), "mean_next_day_pct": (sum(b) / len(b) * 100) if b else None}
    bull, bear = res["bull"]["mean_next_day_pct"], res["bear"]["mean_next_day_pct"]
    res["separates"] = bool(bull is not None and bear is not None and bull > bear)
    return res

def regime(c):
    """Full regime read for one ticker, or None when there is too little history."""
    if len(c) < 260: return None
    X, rets = features(c)
    model = fit_hmm(X)
    alpha = filtered(model, X)
    probs = alpha[-1]
    A = model["A"]
    nxt = [sum(probs[i] * A[i][j] for i in range(3)) for j in range(3)]
    def project(k):
        p = probs[:]
        for _ in range(k): p = [sum(p[i] * A[i][j] for i in range(3)) for j in range(3)]
        return p
    cur = max(range(3), key=lambda s: probs[s])
    stay = A[cur][cur]
    mom = {STATES[s]: moments(rets, [a[s] for a in alpha]) for s in range(3)}
    # duration of the current streak (days the filtered argmax has been the same)
    run = 0
    for a in reversed(alpha):
        if max(range(3), key=lambda s: a[s]) == cur: run += 1
        else: break
    return {
        "current": STATES[cur], "probs": dict(zip(STATES, probs)),
        "stickiness": stay, "expected_days_remaining": 1 / (1 - stay) if stay < 1 else None, "days_in_regime": run,
        "transition": {STATES[i]: dict(zip(STATES, A[i])) for i in range(3)},
        "next_day": dict(zip(STATES, nxt)), "in_5d": dict(zip(STATES, project(5))), "in_20d": dict(zip(STATES, project(20))),
        "moments": mom, "history": [STATES[max(range(3), key=lambda s: a[s])] for a in alpha[-120:]],
    }


# ----------------------------------------------------------------------------- confluence and levels
def confluence(d):
    """Ten yes/no bullish checks. Score = number passed. Also returns the checks for display."""
    checks = [
        ("Price above 50-day average", d["last"] > d["sma50"] if d.get("sma50") else None),
        ("Price above 200-day average", d["last"] > d["sma200"] if d.get("sma200") else None),
        ("50-day above 200-day", d["sma50"] > d["sma200"] if d.get("sma50") and d.get("sma200") else None),
        ("RSI between 50 and 70", 50 <= d["rsi"] <= 70 if d.get("rsi") is not None else None),
        ("MACD above signal", d["macd"] > d["macd_signal"] if d.get("macd") is not None and d.get("macd_signal") is not None else None),
        ("Trend strength with buyers in control (ADX > 20, +DI > -DI)", d["adx"] > 20 and d["pdi"] > d["mdi"] if d.get("adx") is not None else None),
        ("Aroon oscillator positive", d["aroon"] > 0 if d.get("aroon") is not None else None),
        ("On-balance volume rising", d["obv"] == "Rising" if d.get("obv") else None),
        ("Money flow (CMF) positive", d["cmf"] > 0 if d.get("cmf") is not None else None),
        ("Bull regime most likely", d["regime_bull"] > d["regime_bear"] and d["regime_bull"] >= 0.4 if d.get("regime_bull") is not None else None),
    ]
    avail = [x for _, x in checks if x is not None]
    score = sum(1 for x in avail if x)
    scaled = round(score / len(avail) * 10) if avail else None  # keeps /10 meaning when a check is unavailable
    return {"score": scaled, "passed": score, "available": len(avail), "checks": [{"label": a, "pass": b} for a, b in checks]}

def levels(last, atr_, piv, bias):
    """Volatility-based trade levels. Entry zone is the pullback band under the last close; target and stop
    are ATR multiples, nudged to the nearest pivot level when one sits inside the move."""
    if not atr_: return None
    if bias == "Bullish":
        entry_hi, entry_lo = last, last - 0.5 * atr_
        target = last + 2.0 * atr_
        stop = last - 1.5 * atr_
        for r in (piv["r1"], piv["r2"]):
            if last + 1.0 * atr_ < r < target: target = r; break
    elif bias == "Bearish":
        entry_lo, entry_hi = last, last + 0.5 * atr_
        target = last - 2.0 * atr_
        stop = last + 1.5 * atr_
    else:
        return {"support": piv["s1"], "resistance": piv["r1"], "bias": bias}
    mid = (entry_lo + entry_hi) / 2
    risk = abs(mid - stop)
    return {"bias": bias, "entry_lo": entry_lo, "entry_hi": entry_hi, "target": target, "stop": stop,
            "rr": abs(target - mid) / risk if risk else None, "support": piv["s1"], "resistance": piv["r1"]}

def analyse(bars):
    """Everything the page shows for one ticker, computed from daily bars. Returns None if history is too short."""
    t, o, h, l, c, v = (bars[k] for k in ("t", "o", "h", "l", "c", "v"))
    base = lib.compute_tech(bars)
    if not base: return None
    n = len(c)
    ad, pdi, mdi = adx(h, l, c)
    kup, klo = keltner(c, h, l)
    piv = pivots(h, l, c)
    reg = regime(c)
    d = dict(base, adx=ad, pdi=pdi, mdi=mdi, aroon=aroon_osc(h, l), hma20=hma(c, 20), obv=obv_trend(c, v), cmf=cmf(h, l, c, v),
             regime_bull=reg["probs"]["bull"] if reg else None, regime_bear=reg["probs"]["bear"] if reg else None)
    conf = confluence(d)
    s = conf["score"]
    bias = "Bullish" if s is not None and s >= 7 else "Bearish" if s is not None and s <= 3 else "Neutral"
    wk = weekly_overlay(t, c)
    daily_dir = "Uptrend" if base["sma50"] and base["last"] > base["sma50"] else "Downtrend"
    wf = walk_forward(c) if reg else None
    prev = c[-2]
    conf_level = "High" if (s is not None and (s >= 8 or s <= 2)) and (not reg or reg["probs"][reg["current"]] > 0.7) else "Medium" if s is not None and (s >= 6 or s <= 4) else "Low"
    return {
        "price": {"last": base["last"], "prev": prev, "chg_pct": (base["last"] / prev - 1) * 100, "day_high": h[-1], "day_low": l[-1],
                  "hi52": base["hi52"], "lo52": base["lo52"], "volume": v[-1], "vol_ratio": base["vol_ratio"],
                  "from_hi52_pct": (base["last"] / base["hi52"] - 1) * 100, "asof": t[-1]},
        "returns": {"1w": base["ret_1w"], "1m": base["ret_1m"], "3m": base["ret_3m"], "ytd": base["ret_ytd"], "1y": base["ret_1y"], "mom_12_1": momentum_12_1(c)},
        "tech": {"rsi": base["rsi"], "adx": ad, "plus_di": pdi, "minus_di": mdi, "macd": base["macd"], "macd_signal": base["macd_signal"],
                 "aroon": d["aroon"], "hma20": d["hma20"], "sma20": base["sma20"], "sma50": base["sma50"], "sma200": base["sma200"],
                 "bb_up": base["bb_up"], "bb_lo": base["bb_lo"], "keltner_up": kup, "keltner_lo": klo, "atr": base["atr"], "atr_pct": base["atr_pct"],
                 "vol_ann": base["vol_ann"], "obv": d["obv"], "cmf": d["cmf"], "hi20": base["hi20"], "lo20": base["lo20"]},
        "pivots": piv, "signals": base["signals"], "breakout": base["breakout"],
        "weekly": dict(wk, aligned=(wk["trend"] == daily_dir)) if wk else None,
        "confluence": conf, "bias": bias, "confidence": conf_level,
        "regime": reg, "walk_forward": wf,
        "levels": levels(base["last"], base["atr"], piv, bias),
        "chart": base["chart"],
    }
