"""Macro drivers: how each stock's daily moves relate to oil, rates, the dollar and volatility (stdlib only).

Correlations of daily returns over the last 60 shared trading days. They describe what happened recently, not a
causal law, and with 60 observations anything between about -0.25 and +0.25 is statistically indistinguishable from noise.
"""
import datetime as dt
import math

import context

NOISE = 0.25     # |corr| below this is not mentioned in the text
NOTABLE = 0.35   # |corr| at or above this is called out as a link

# symbol -> (label, phrase when the factor RISES)
FACTORS = [
    ("CL=F", "Oil (WTI)", "oil rises"),
    ("BZ=F", "Brent oil", "Brent rises"),
    ("^TNX", "US 10Y yield", "US 10-year yields rise"),
    ("DX-Y.NYB", "US dollar", "the dollar strengthens"),
    ("EURUSD=X", "EUR/USD", "the euro strengthens"),
    ("^VIX", "VIX", "volatility rises"),
    ("GC=F", "Gold", "gold rises"),
    ("^GSPC", "S&P 500", "the S&P 500 rises"),
]

def sensitivity(series, factor_series, factors=None, n=60):
    """{symbol: correlation or None} for one stock against each factor. Series are (timestamps, closes)."""
    out = {}
    for sym, _, _ in (factors or FACTORS):
        fs = factor_series.get(sym)
        out[sym] = context.correlation(series, fs, n) if fs and series else None
    return out

def narrative(name, corr, labels):
    """One or two sentences naming the strongest links, or saying there are none worth reporting."""
    items = sorted(((abs(c), s, c) for s, c in corr.items() if c is not None and abs(c) >= NOISE), reverse=True)
    items = [i for i in items if i[0] >= NOTABLE][:3]
    if not items:
        return "%s shows no strong link to oil, yields, the dollar or volatility over the last 60 sessions." % name
    bits = []
    for _, s, c in items:
        label, phrase = labels[s]
        bits.append("%s tends to %s when %s (correlation %+.2f)" % (name, "rise" if c > 0 else "fall", phrase, c))
    return "; ".join(bits) + "."

def summarise(per_stock, labels):
    """Cross-stock summary lines: how many stocks lean the same way on each factor."""
    lines = []
    for sym, label, phrase in FACTORS:
        vals = [(t, c[sym]) for t, c in per_stock.items() if c.get(sym) is not None]
        if len(vals) < 5: continue
        neg = [t for t, c in vals if c <= -NOTABLE]; pos = [t for t, c in vals if c >= NOTABLE]
        avg = sum(c for _, c in vals) / len(vals)
        if len(neg) >= 3 and len(neg) > len(pos):
            lines.append("%d of %d stocks have tended to fall when %s: %s (average correlation %+.2f)." % (len(neg), len(vals), phrase, ", ".join(t.split(":")[-1] for t in neg), avg))
        elif len(pos) >= 3 and len(pos) > len(neg):
            lines.append("%d of %d stocks have tended to rise when %s: %s (average correlation %+.2f)." % (len(pos), len(vals), phrase, ", ".join(t.split(":")[-1] for t in pos), avg))
    return lines
