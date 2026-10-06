"""Macro drivers: how each stock's daily moves relate to oil, rates, the dollar and volatility (stdlib only).

Correlations of daily returns over the last 60 shared trading days. They describe what happened recently, not a
causal law, and with 60 observations anything between about -0.25 and +0.25 is statistically indistinguishable from noise.
"""
import datetime as dt

import context

NOISE = 0.25     # |corr| below this is not reported at all
NOTABLE = 0.35   # |corr| at or above this is called out as a link

# (key, label, phrase for "when ..."). Keys are Yahoo symbols, or yield series names from yields.py.
FACTORS = [
    ("CL=F", "Oil (WTI)", "oil rises"),
    ("BZ=F", "Brent oil", "Brent oil rises"),
    ("^TNX", "US 10Y yield", "US 10-year yields rise"),
    ("US 2Y", "US 2Y yield", "US 2-year yields rise"),
    ("US 10Y real", "US 10Y real yield", "US real yields rise"),
    ("Euro AAA 10Y", "Euro AAA 10Y", "euro-area yields rise"),
    ("UK 10Y", "UK 10Y gilt", "gilt yields rise"),
    ("DX-Y.NYB", "US dollar", "the dollar strengthens"),
    ("EURUSD=X", "EUR/USD", "the euro strengthens"),
    ("^VIX", "VIX", "volatility rises"),
    ("GC=F", "Gold", "gold rises"),
    ("^GSPC", "S&P 500", "the S&P 500 rises"),
]


def to_series(points):
    """[(date, value)] -> ([timestamps], [values]) in the form context.correlation expects."""
    return ([int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()) for d, _ in points], [v for _, v in points])

def sensitivity(series, factor_series, factors, n=60):
    """{key: correlation or None} for one stock against each available factor."""
    return {k: (context.correlation(series, factor_series[k], n) if series and k in factor_series else None) for k, _, _ in factors}

def narrative(name, corr, labels):
    """Sentences naming the strongest links, or saying there are none worth reporting."""
    skip = {"CL=F"} if corr.get("BZ=F") is not None else set()   # WTI and Brent move together: one sentence is enough
    items = sorted(((abs(c), k, c) for k, c in corr.items() if c is not None and abs(c) >= NOTABLE and k not in skip), reverse=True)[:3]
    if not items:
        return "%s shows no strong link to oil, yields, the dollar or volatility over the last 60 sessions." % name
    return "; ".join("%s tends to %s when %s (correlation %+.2f)" % (name, "rise" if c > 0 else "fall", labels[k][1], c) for _, k, c in items) + "."

def summarise(per_stock, factors):
    """Cross-stock lines: how many stocks lean the same way on a factor."""
    lines = []
    have_brent = any(k == "BZ=F" for k, _, _ in factors)
    for k, label, phrase in factors:
        if k == "^GSPC" or (k == "CL=F" and have_brent): continue   # market beta and duplicate oil add no information
        vals = [(t, c[k]) for t, c in per_stock.items() if c.get(k) is not None]
        if len(vals) < 5: continue
        neg = [t for t, c in vals if c <= -NOTABLE]; pos = [t for t, c in vals if c >= NOTABLE]
        avg = sum(c for _, c in vals) / len(vals)
        short = lambda ts: ", ".join(t.split(":")[-1] for t in ts)
        if len(neg) >= 3 and len(neg) > len(pos):
            lines.append("%d of %d stocks have tended to fall when %s: %s (average correlation %+.2f)." % (len(neg), len(vals), phrase, short(neg), avg))
        elif len(pos) >= 3 and len(pos) > len(neg):
            lines.append("%d of %d stocks have tended to rise when %s: %s (average correlation %+.2f)." % (len(pos), len(vals), phrase, short(pos), avg))
    return lines

def build(series_by_ticker, names, quotes, yield_raw):
    """Matrix of stock x factor correlations plus plain-language text.
    `quotes` maps Yahoo symbol -> context quote; `yield_raw` maps yield series name -> [(date, value)]."""
    fs, factors = {}, []
    for k, label, phrase in FACTORS:
        q = quotes.get(k)
        if q: fs[k] = (q["_t"], q["_c"])
        elif yield_raw.get(k) and len(yield_raw[k]) > 40: fs[k] = to_series(yield_raw[k])
        else: continue
        factors.append((k, label, phrase))
    per = {t: sensitivity(s, fs, factors) for t, s in series_by_ticker.items()}
    labels = {k: (label, phrase) for k, label, phrase in factors}
    return {"factors": [{"id": k, "label": label} for k, label, _ in factors],
            "matrix": [{"ticker": t, "corr": c} for t, c in per.items()],
            "summary": summarise(per, factors),
            "text": {t: narrative(names.get(t, t), c, labels) for t, c in per.items()},
            "window": 60}
