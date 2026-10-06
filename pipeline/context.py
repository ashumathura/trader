"""Cross-asset context, risk assessment, session range and universe-level risk (stdlib only).

Mirrors the sections of the pre-market report format: sector and peer context, risk table, expected range,
time horizon, and a dashboard across the whole watchlist (beta, correlation, currency mix).
"""
import datetime as dt
import math
import statistics

import lib


# ----------------------------------------------------------------------------- quotes and relative strength
_Q = {}

def quote(sym, name=None, rng="1y", ttl=3600):
    """Daily bars for a context symbol, reduced to what the page needs (memoised per build). None when unavailable."""
    if sym in _Q: return _Q[sym]
    d = lib.yahoo_chart(sym, rng, "1d", ttl=ttl, drop_partial=False)
    if not d or len(d["bars"]["c"]) < 25: _Q[sym] = None; return None
    b = d["bars"]; c = b["c"]
    ret = lambda n: (c[-1] / c[-1 - n] - 1) * 100 if len(c) > n else None
    _Q[sym] = {"symbol": sym, "name": name or sym, "price": c[-1], "chg_pct": (c[-1] / c[-2] - 1) * 100, "ret_1m": ret(21), "ret_3m": ret(63),
               "asof": dt.datetime.fromtimestamp(b["t"][-1], dt.timezone.utc).date().isoformat(), "_t": b["t"], "_c": c, "_bars": b}
    return _Q[sym]

def strip(q):
    return {k: v for k, v in q.items() if not k.startswith("_")} if q else None

def _rets_by_date(t, c):
    out = {}
    for i in range(1, len(c)):
        out[dt.datetime.fromtimestamp(t[i], dt.timezone.utc).date()] = math.log(c[i] / c[i - 1])
    return out

def correlation(a, b, n=60):
    """Correlation of daily log returns on the dates both series share (last n common dates)."""
    ra, rb = _rets_by_date(*a), _rets_by_date(*b)
    days = sorted(set(ra) & set(rb))[-n:]
    if len(days) < 20: return None
    x, y = [ra[d] for d in days], [rb[d] for d in days]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sx = math.sqrt(sum((v - mx) ** 2 for v in x)); sy = math.sqrt(sum((v - my) ** 2 for v in y))
    return sum((p - mx) * (q - my) for p, q in zip(x, y)) / (sx * sy) if sx and sy else None

def beta(stock, bench, n=250):
    ra, rb = _rets_by_date(*stock), _rets_by_date(*bench)
    days = sorted(set(ra) & set(rb))[-n:]
    if len(days) < 60: return None
    x, y = [ra[d] for d in days], [rb[d] for d in days]
    my = sum(y) / len(y); var = sum((v - my) ** 2 for v in y)
    mx = sum(x) / len(x)
    return sum((p - mx) * (q - my) for p, q in zip(x, y)) / var if var else None

def sector_context(t, own_ret_1m, own_series, cache):
    """ETF, peers, local index and FX for one stock, with relative strength. `cache` maps symbol -> quote."""
    get = lambda s: cache.get(s)
    etf = get(t.get("etf")) if t.get("etf") else None
    idx = get(t.get("index")) if t.get("index") else None
    fx = get(t.get("fx")) if t.get("fx") else None
    peers = [get(p) for p in t.get("peers", []) if get(p)]
    out = {"etf": strip(etf), "index": strip(idx), "fx": strip(fx), "peers": [strip(p) for p in peers]}
    if etf:
        out["etf"]["rel_1m"] = own_ret_1m - etf["ret_1m"] if own_ret_1m is not None and etf["ret_1m"] is not None else None
        out["etf"]["corr_60d"] = correlation(own_series, (etf["_t"], etf["_c"])) if own_series else None
    if idx and own_ret_1m is not None and idx["ret_1m"] is not None: out["index"]["rel_1m"] = own_ret_1m - idx["ret_1m"]
    pr = [p["ret_1m"] for p in peers if p["ret_1m"] is not None]
    out["peer_avg_1m"] = sum(pr) / len(pr) if pr else None
    bits = []
    if etf and out["etf"]["rel_1m"] is not None:
        r = out["etf"]["rel_1m"]; bits.append("%s %s its sector ETF %s by %.1f points over 1 month" % (t["name"], "is beating" if r >= 0 else "is lagging", etf["symbol"], abs(r)))
    if out["peer_avg_1m"] is not None and own_ret_1m is not None:
        r = own_ret_1m - out["peer_avg_1m"]; bits.append("%s the average of its peers (%+.1f%% vs %+.1f%%)" % ("ahead of" if r >= 0 else "behind", own_ret_1m, out["peer_avg_1m"]))
    out["text"] = "; ".join(bits) + "." if bits else None
    return out


# ----------------------------------------------------------------------------- per-stock risk table, range, horizon
def risk_table(s, events, macro, fx_q):
    """Colour-coded risk factors like the report's section 6. Levels: green, amber, red."""
    rows = []
    def add(f, lvl, detail): rows.append({"factor": f, "level": lvl, "detail": detail})
    ern = next((e for e in events if e["kind"] == "earnings" and e["days"] >= 0), None)
    if ern:
        mv = ""
        o = (s.get("flow") or {}).get("options")
        if o and o.get("expected_move_pct"): mv = " Options imply a move of about +/-%.1f%% by the %s expiry." % (o["expected_move_pct"], o["iv_expiry"][5:])
        add("Earnings proximity", "red" if ern["days"] <= 7 else "amber" if ern["days"] <= 21 else "green",
            "%s in %d day(s)%s.%s" % (ern["date"], ern["days"], " (approximate)" if ern["approx"] else "", mv))
    else:
        add("Earnings proximity", "green", "No earnings date in the next months.")
    soon = [m for m in macro if 0 <= m["days"] <= 14]
    add("Upcoming macro events", "red" if any(m["days"] <= 2 for m in soon) else "amber" if soon else "green",
        "; ".join("%s on %s" % (m["title"], m["date"][5:]) for m in soon[:3]) if soon else "No FOMC or ECB decision in the next 14 days.")
    px = s["price"]; atr_pct = s["tech"].get("atr_pct")
    vol = (s.get("flow") or {}).get("volume_flow", {}).get("volume", {})
    turnover = (vol.get("avg20") or 0) * px["last"] * (0.01 if s["currency"] == "GBp" else 1)
    otc = "OTC" in s["exchange"]
    thin = otc or turnover < 2_000_000
    add("Liquidity / gap risk", "red" if otc else "amber" if thin else "green",
        ("OTC security: wide spreads and gaps are likely. " if otc else "") + ("Average daily turnover about %s %s: thinly traded, indicators less reliable." % (_short(turnover), s["currency"] if s["currency"] != "GBp" else "GBP")
         if thin and not otc else "Average daily turnover about %s %s." % (_short(turnover), s["currency"] if s["currency"] != "GBp" else "GBP")))
    if atr_pct:
        add("Volatility", "red" if atr_pct >= 4 else "amber" if atr_pct >= 2.5 else "green", "Typical daily range %.1f%% of price (14-day ATR)." % atr_pct)
    if s["currency"] not in ("EUR",):
        ccy = "GBP" if s["currency"] == "GBp" else s["currency"]
        d = "Quoted in %s; a euro-based investor also carries the %s/EUR exchange rate" % (ccy, ccy)
        if fx_q and fx_q.get("ret_1m") is not None: d += " (%+.1f%% over 1 month)" % fx_q["ret_1m"]
        add("Currency risk", "amber", d + ".")
    else:
        add("Currency risk", "green", "Euro-denominated.")
    r = s.get("regime")
    if r:
        bear = r["probs"]["bear"]
        add("Regime risk", "red" if bear >= 0.5 else "amber" if bear >= 0.25 else "green", "Bear regime probability %.0f%%; model says %s." % (bear * 100, r["current"]))
    if px.get("from_hi52_pct") is not None and px["from_hi52_pct"] <= -35:
        add("Drawdown", "amber", "%.0f%% below the 52-week high." % abs(px["from_hi52_pct"]))
    sh = (s.get("flow") or {}).get("short")
    if sh and sh.get("days_to_cover") and sh["days_to_cover"] >= 5:
        add("Short interest", "amber", "%.1f days to cover: crowded shorts can squeeze rallies and add to selling." % sh["days_to_cover"])
    return rows

def _short(x):
    return "%.1fB" % (x / 1e9) if x >= 1e9 else "%.1fM" % (x / 1e6) if x >= 1e6 else "%.0fk" % (x / 1e3)

def session_range(s):
    """Expected next-session range from average true range, with the pivot band for reference. A statistical estimate, not a forecast."""
    atr_, last = s["tech"].get("atr"), s["price"]["last"]
    if not atr_: return None
    piv = s["pivots"]
    out = {"atr_lo": last - 0.6 * atr_, "atr_hi": last + 0.6 * atr_, "pivot_lo": piv["s1"], "pivot_hi": piv["r1"]}
    o = (s.get("flow") or {}).get("options")
    if o and o.get("iv"):
        m = last * o["iv"] * math.sqrt(1 / 365)
        out["iv_lo"], out["iv_hi"] = last - m, last + m
    return out

def time_horizon(s, events):
    ern = next((e for e in events if e["kind"] == "earnings" and 0 <= e["days"] <= 5), None)
    adx_ = s["tech"].get("adx") or 0
    if ern: return "Event-driven: earnings in %d day(s), decide before the report (binary risk)" % ern["days"]
    if s["bias"] == "Bullish": return "Swing (2-5 sessions)" if adx_ >= 20 else "Swing (5-10 sessions), trend is not yet strong"
    if s["bias"] == "Bearish": return "Avoid or monitor until a base forms"
    r = s.get("regime")
    return "Hold / monitor" + (" (regime has about %d sessions left on average)" % r["expected_days_remaining"] if r and r["expected_days_remaining"] else "")

def lean(score):
    if score is None: return None
    return "Bullish" if score >= 7 else "Neutral, leaning bullish" if score >= 5 else "Neutral, leaning bearish" if score == 4 else "Bearish"

def identity(s, mcap=None):
    otc = "OTC" in s["exchange"]
    return {"exchange": s["exchange"], "sector": s["sector"], "currency": "GBp (pence)" if s["currency"] == "GBp" else s["currency"],
            "market_cap": mcap, "status": "Active" + (" (OTC)" if otc else "")}


# ----------------------------------------------------------------------------- universe dashboard
def universe(stocks, series, bench, events, vix, macro, heads):
    """Risk view across all tracked stocks (equal weight; nothing about real holdings)."""
    ok = [s for s in stocks if s["status"] == "ok"]
    betas = {}
    if bench:
        for s in ok:
            sr = series.get(s["ticker"])
            b = beta(sr, bench) if sr else None
            if b is not None: betas[s["ticker"]] = b
    pairs = []
    for i, a in enumerate(ok):
        for b in ok[i + 1:]:
            if series.get(a["ticker"]) and series.get(b["ticker"]):
                c = correlation(series[a["ticker"]], series[b["ticker"]], 90)
                if c is not None: pairs.append((c, a["ticker"].split(":")[-1], b["ticker"].split(":")[-1]))
    pairs.sort(reverse=True)
    ccy = {}
    for s in ok: ccy[s["currency"] if s["currency"] != "GBp" else "GBP"] = ccy.get(s["currency"] if s["currency"] != "GBp" else "GBP", 0) + 1
    ern = [e for e in events if e["kind"] == "earnings" and 0 <= e["days"] <= 7]
    soon = [m for m in macro if 0 <= m["days"] <= 7]
    neg = sum(1 for h in heads if h["tone"] == "neg"); pos = sum(1 for h in heads if h["tone"] == "pos")
    score = 0
    if vix is not None: score += 2 if vix >= 25 else 1 if vix >= 20 else 0
    score += 1 if soon else 0
    score += 1 if heads and neg > pos + 3 else 0
    level = "red" if score >= 3 else "amber" if score >= 1 else "green"
    why = []
    if vix is not None: why.append("VIX %.1f" % vix)
    if soon: why.append("; ".join("%s %s" % (m["title"], m["date"][5:]) for m in soon))
    if heads: why.append("headline tone %d positive / %d negative" % (pos, neg))
    return {"avg_beta": sum(betas.values()) / len(betas) if betas else None, "betas": betas, "bench": "S&P 500",
            "earnings_7d": [{"ticker": e["ticker"], "date": e["date"], "days": e["days"]} for e in ern],
            "currency_mix": ccy, "high_corr": [{"a": a, "b": b, "corr": c} for c, a, b in pairs if c >= 0.6][:6],
            "lowest_corr": [{"a": a, "b": b, "corr": c} for c, a, b in pairs[-3:]][::-1] if len(pairs) >= 3 else [],
            "macro_risk": {"level": level, "detail": ", ".join(why)}}
