"""Money flow, volume structure, off-exchange activity, short interest and options analytics (stdlib only).

Everything here is derived from public data and is descriptive: volume-based flow measures are proxies for what
institutions might be doing, not observations of their orders. Each block says where its numbers come from.
"""
import datetime as dt
import math
import re
import statistics

import lib


# ----------------------------------------------------------------------------- volume-based flow (from daily bars)
def mfi(h, l, c, v, n=14):
    """Money Flow Index: a volume-weighted RSI on typical price."""
    if len(c) < n + 2: return None
    tp = [(a + b + d) / 3 for a, b, d in zip(h, l, c)]
    pos = neg = 0.0
    for i in range(len(c) - n, len(c)):
        flow = tp[i] * v[i]
        if tp[i] > tp[i - 1]: pos += flow
        elif tp[i] < tp[i - 1]: neg += flow
    return 100.0 if neg == 0 and pos > 0 else (100 - 100 / (1 + pos / neg)) if neg else None

def ad_line(h, l, c, v):
    out, tot = [], 0.0
    for i in range(len(c)):
        rng = h[i] - l[i]
        tot += ((2 * c[i] - h[i] - l[i]) / rng * v[i]) if rng else 0.0
        out.append(tot)
    return out

def up_down_volume(c, v, n=20):
    up = sum(v[i] for i in range(len(c) - n, len(c)) if c[i] > c[i - 1])
    dn = sum(v[i] for i in range(len(c) - n, len(c)) if c[i] < c[i - 1])
    return up, dn

def accumulation_days(c, v, n=25, min_move=0.002):
    """Counts of accumulation (up on higher volume) and distribution (down on higher volume) sessions."""
    acc = dist = 0
    for i in range(len(c) - n, len(c)):
        chg = c[i] / c[i - 1] - 1
        if v[i] > v[i - 1]:
            if chg >= min_move: acc += 1
            elif chg <= -min_move: dist += 1
    return acc, dist

def vwap(h, l, c, v, n):
    if len(c) < n: return None
    num = sum((h[i] + l[i] + c[i]) / 3 * v[i] for i in range(len(c) - n, len(c)))
    den = sum(v[i] for i in range(len(c) - n, len(c)))
    return num / den if den else None

def volume_profile(h, l, v, lookback=120, bins=30):
    """Spread each session's volume evenly across its high-low range, then bucket by price.
    Returns the point of control (busiest price), the 70% value area and the busiest nodes."""
    h, l, v = h[-lookback:], l[-lookback:], v[-lookback:]
    lo, hi = min(l), max(h)
    if hi <= lo: return None
    step = (hi - lo) / bins
    hist = [0.0] * bins
    for a, b, vol in zip(l, h, v):
        i0 = min(bins - 1, int((a - lo) / step)); i1 = min(bins - 1, int((b - lo) / step))
        share = vol / (i1 - i0 + 1)
        for i in range(i0, i1 + 1): hist[i] += share
    mid = lambda i: lo + (i + 0.5) * step
    poc = max(range(bins), key=lambda i: hist[i])
    total = sum(hist); area = {poc}; acc = hist[poc]
    a_i, b_i = poc, poc
    while acc < 0.7 * total and (a_i > 0 or b_i < bins - 1):
        down = hist[a_i - 1] if a_i > 0 else -1; up = hist[b_i + 1] if b_i < bins - 1 else -1
        if up >= down: b_i += 1; acc += hist[b_i]
        else: a_i -= 1; acc += hist[a_i]
    avg = total / bins
    nodes = [i for i in range(1, bins - 1) if hist[i] >= hist[i - 1] and hist[i] >= hist[i + 1] and hist[i] > 1.25 * avg]
    return {"poc": mid(poc), "va_low": lo + a_i * step, "va_high": lo + (b_i + 1) * step,
            "nodes": [{"price": mid(i), "share": hist[i] / total} for i in nodes], "lookback": len(h)}

def volume_flow(bars, tech):
    """Flow block built only from OHLCV bars. `tech` is the tech dict from analysis (obv, cmf, sma50...)."""
    t, h, l, c, v = bars["t"], bars["h"], bars["l"], bars["c"], bars["v"]
    n = len(c)
    if n < 40 or not any(v): return None
    last = c[-1]
    avg20 = sum(v[-21:-1]) / 20
    prev_vol = v[-1]
    up, dn = up_down_volume(c, v)
    acc, dist = accumulation_days(c, v)
    ad = ad_line(h, l, c, v)
    ad_trend = "Rising" if ad[-1] > ad[-21] and ad[-1] > sum(ad[-20:]) / 20 else "Falling" if ad[-1] < ad[-21] and ad[-1] < sum(ad[-20:]) / 20 else "Flat"
    big = [i for i in range(n - 25, n) if avg20 and v[i] > 2 * avg20]
    big_up = sum(1 for i in big if c[i] > c[i - 1]); big_dn = len(big) - big_up
    vw20, vw50 = vwap(h, l, c, v, 20), vwap(h, l, c, v, 50)
    prof = volume_profile(h, l, v)
    sup = res = None
    if prof:
        below = [x for x in prof["nodes"] if x["price"] < last]; above = [x for x in prof["nodes"] if x["price"] > last]
        sup = max(below, key=lambda x: x["price"]) if below else None
        res = min(above, key=lambda x: x["price"]) if above else None
    mf = mfi(h, l, c, v)
    return {
        "volume": {"last": prev_vol, "avg20": avg20, "ratio": prev_vol / avg20 if avg20 else None,
                   "asof": dt.datetime.fromtimestamp(t[-1], dt.timezone.utc).date().isoformat()},
        "obv": tech.get("obv"), "cmf": tech.get("cmf"), "mfi": mf, "ad_trend": ad_trend,
        "updown_ratio": up / dn if dn else None, "acc_days": acc, "dist_days": dist, "big_days": len(big), "big_up": big_up, "big_down": big_dn,
        "vwap20": vw20, "vwap50": vw50, "profile": prof, "support_node": sup, "resistance_node": res,
    }


# ----------------------------------------------------------------------------- verdict
def verdict(flow, price, sma50=None):
    """Plain-language read from the flow metrics. Returns tone (up/dn/mid) and bullet reasons."""
    pts, score = [], 0
    def add(text, s): pts.append(text); return s
    v = flow["volume"]
    if v["ratio"] is not None:
        if v["ratio"] >= 1.5: pts.append("Last session volume was %.1fx the 20-day average (heavy)." % v["ratio"])
        elif v["ratio"] <= 0.6: pts.append("Last session volume was only %.1fx the 20-day average (light, low conviction)." % v["ratio"])
    obv, cmf = flow.get("obv"), flow.get("cmf")
    if obv == "Rising": score += add("On-balance volume is rising: volume is flowing in on up days.", 1)
    elif obv == "Falling": score += add("On-balance volume is falling: volume is heavier on down days.", -1)
    if cmf is not None:
        if cmf > 0.05: score += add("Chaikin money flow is positive (%+.2f): closes tend to land in the upper part of the daily range." % cmf, 1)
        elif cmf < -0.05: score += add("Chaikin money flow is negative (%+.2f): closes tend to land in the lower part of the daily range." % cmf, -1)
    mf = flow.get("mfi")
    if mf is not None:
        if mf >= 80: pts.append("Money Flow Index %.0f: overbought, buying may be exhausted." % mf)
        elif mf <= 20: pts.append("Money Flow Index %.0f: oversold, selling may be exhausted." % mf)
    ud = flow.get("updown_ratio")
    if ud is not None:
        if ud >= 1.3: score += add("Up-day volume is %.1fx down-day volume over 20 sessions (accumulation)." % ud, 1)
        elif ud <= 0.77: score += add("Down-day volume is %.1fx up-day volume over 20 sessions (distribution)." % (1 / ud), -1)
    if flow["dist_days"] - flow["acc_days"] >= 3: score += add("%d distribution days vs %d accumulation days in the last 25 sessions." % (flow["dist_days"], flow["acc_days"]), -1)
    elif flow["acc_days"] - flow["dist_days"] >= 3: score += add("%d accumulation days vs %d distribution days in the last 25 sessions." % (flow["acc_days"], flow["dist_days"]), 1)
    if flow["big_days"]: pts.append("%d session(s) with more than 2x average volume recently (%d up, %d down)." % (flow["big_days"], flow["big_up"], flow["big_down"]))
    tone = "up" if score >= 2 else "dn" if score <= -2 else "mid"
    label = "Accumulation" if tone == "up" else "Distribution" if tone == "dn" else "Balanced / paused"
    sup = flow.get("support_node"); vw = flow.get("vwap50")
    tail = ""
    if tone == "up" and (sup or vw):
        lvl = sup["price"] if sup else vw
        tail = " Dip buyers have previously been active near %s (%s)." % (lvl_fmt(lvl), "high-volume price node" if sup else "50-day volume-weighted average price")
    elif tone == "dn" and flow.get("resistance_node"):
        tail = " Supply has previously been heavy near %s (high-volume price node)." % lvl_fmt(flow["resistance_node"]["price"])
    elif tone == "mid" and (sup or vw):
        lvl = sup["price"] if sup else vw
        tail = " Watch %s (%s) as the level where buyers have stepped in before." % (lvl_fmt(lvl), "high-volume node" if sup else "50-day VWAP")
    return {"tone": tone, "label": label, "score": score, "points": pts,
            "text": "%s: %s%s" % (label, "volume flow leans %s." % ("positive" if tone == "up" else "negative" if tone == "dn" else "neither way"), tail)}

def lvl_fmt(x):
    return ("%.2f" % x) if x < 100 else ("%.0f" % x)


# ----------------------------------------------------------------------------- FINRA: off-exchange and short-sale volume (US)
FINRA = "https://cdn.finra.org/equity/regsho/daily/%sshvol%s.txt"

def parse_finra(txt):
    """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market  ->  {symbol: (short, total)}"""
    out = {}
    for line in txt.splitlines()[1:]:
        p = line.split("|")
        if len(p) < 5: continue
        try: out[p[1]] = (float(p[2]), float(p[4]))
        except ValueError: continue
    return out

def finra_days(today, want=24):
    """Last `want` trading-day files (CNMS = exchange-listed, FORF = OTC). Published files never change, so they cache for good."""
    days, d = [], today
    for _ in range(45):
        d -= dt.timedelta(days=1)
        if d.weekday() >= 5: continue
        got = {}
        for kind in ("CNMS", "FORF"):
            txt = lib.cached_get(FINRA % (kind, d.strftime("%Y%m%d")), 30 * 86400, "finra", validate=lambda t: t.startswith("Date|Symbol"))
            if txt: got[kind] = parse_finra(txt)
        if got: days.append((d, got))
        if len(days) >= want: break
    return days

def offexchange(symbol, otc, days, bars):
    """Off-exchange (FINRA TRF) volume share and short-sale volume ratio versus their own 20-day averages."""
    kind = "FORF" if otc else "CNMS"
    cons = {dt.datetime.fromtimestamp(t, dt.timezone.utc).date(): v for t, v in zip(bars["t"], bars["v"])}
    rows = []
    for d, got in reversed(days):  # oldest first
        rec = got.get(kind, {}).get(symbol) or got.get("CNMS" if otc else "FORF", {}).get(symbol)
        if not rec or not rec[1]: continue
        sv, tv = rec
        share = (tv / cons[d]) if (not otc and cons.get(d)) else None
        if share is not None and share > 1.2: share = None  # mismatched volume basis (e.g. partial or ADR-adjusted bar): do not show nonsense
        rows.append({"date": d, "short_ratio": sv / tv, "off_vol": tv, "share": share})
    if len(rows) < 8: return None
    cur = rows[-1]; hist = rows[-21:-1] if len(rows) > 21 else rows[:-1]
    avg = lambda k: (sum(r[k] for r in hist if r[k] is not None) / max(1, sum(1 for r in hist if r[k] is not None))) if any(r[k] is not None for r in hist) else None
    sr_avg, sh_avg = avg("short_ratio"), avg("share")
    score = 0; notes = []
    if cur["share"] is not None and sh_avg:
        d = cur["share"] - sh_avg
        if d > 0.04: notes.append("Off-exchange share of volume is above its 20-day norm: more institutional-style (block / dark) execution than usual."); 
        elif d < -0.04: notes.append("Off-exchange share of volume is below its 20-day norm.")
    if sr_avg is not None:
        d = cur["short_ratio"] - sr_avg
        if d > 0.05: score -= 1; notes.append("Short-sale volume ratio is %.0f points above its 20-day average (more shorting than usual)." % (d * 100))
        elif d < -0.05: score += 1; notes.append("Short-sale volume ratio is %.0f points below its 20-day average (less shorting than usual)." % (-d * 100))
    return {"asof": cur["date"].isoformat(), "otc": otc, "off_volume": cur["off_vol"], "share": cur["share"], "share_avg20": sh_avg,
            "short_ratio": cur["short_ratio"], "short_ratio_avg20": sr_avg, "tone": "up" if score > 0 else "dn" if score < 0 else "mid", "notes": notes,
            "series": [round(r["short_ratio"], 3) for r in rows[-20:]]}


# ----------------------------------------------------------------------------- short interest
def parse_short_interest(txt):
    """Nasdaq short-interest JSON -> list of {date, shares, avg_volume, days_to_cover}, newest first."""
    try: rows = lib._json_after(txt)["data"]["shortInterestTable"]["rows"]
    except Exception: return None
    out = []
    for r in rows:
        try:
            out.append({"date": dt.datetime.strptime(r["settlementDate"], "%m/%d/%Y").date().isoformat(), "shares": float(r["interest"].replace(",", "")),
                        "avg_volume": float(r["avgDailyShareVolume"].replace(",", "")), "days_to_cover": float(r["daysToCover"])})
        except Exception: continue
    return out or None

def short_interest(sym, price):
    H = {"Accept": "application/json"}
    txt = lib.cached_get("https://api.nasdaq.com/api/quote/%s/short-interest?assetClass=stocks" % sym, 12 * 3600, "short_interest",
                         validate=lambda t: '"shortInterestTable"' in t, headers=H)
    rows = parse_short_interest(txt) if txt else None
    if not rows: return None
    cur = rows[0]; prev = rows[1] if len(rows) > 1 else None
    chg = (cur["shares"] / prev["shares"] - 1) * 100 if prev and prev["shares"] else None
    pct = None; mcap = None
    sm = lib.cached_get("https://api.nasdaq.com/api/quote/%s/summary?assetclass=stocks" % sym, 12 * 3600, "short_interest", headers=H)
    try:
        mc = lib._json_after(sm)["data"]["summaryData"]["MarketCap"]["value"]
        mcap = float(re.sub(r"[^0-9.]", "", mc))
        if mcap and price: pct = cur["shares"] / (mcap / price) * 100
    except Exception: pass
    tone = "dn" if (cur["days_to_cover"] >= 5 or (chg is not None and chg > 8)) else "up" if (chg is not None and chg < -8) else "mid"
    return {"date": cur["date"], "shares": cur["shares"], "days_to_cover": cur["days_to_cover"], "change_pct": chg,
            "pct_of_shares": pct, "market_cap": mcap, "history": [{"date": r["date"], "shares": r["shares"]} for r in rows[:8]][::-1], "tone": tone}


# ----------------------------------------------------------------------------- insider trades (Nasdaq, US only)
def _money(x):
    try: return float(re.sub(r"[^0-9.]", "", x or "")) if x else 0.0
    except ValueError: return 0.0

def parse_insiders(txt, today, days=90):
    try: rows = lib._json_after(txt)["data"]["transactionTable"]["table"]["rows"]
    except Exception: return None
    buys = sells = 0; bsh = ssh = 0.0; bval = sval = 0.0; recent = []
    for r in rows or []:
        try: d = dt.datetime.strptime(r["lastDate"], "%m/%d/%Y").date()
        except Exception: continue
        if (today - d).days > days: continue
        kind = (r.get("transactionType") or "").lower()
        sh = _money(r.get("sharesTraded")); val = sh * _money(r.get("lastPrice"))
        if "buy" in kind or "purchase" in kind: buys += 1; bsh += sh; bval += val
        elif "sell" in kind or "sale" in kind: sells += 1; ssh += sh; sval += val
        else: continue
        if len(recent) < 4: recent.append({"date": d.isoformat(), "role": r.get("relation") or "Insider", "type": "Buy" if "buy" in kind or "purchase" in kind else "Sell", "shares": sh, "value": val})
    tone = "up" if bval > sval * 1.5 and buys else "dn" if sval > bval * 3 and sells >= 3 else "mid"
    return {"days": days, "buys": buys, "sells": sells, "buy_value": bval, "sell_value": sval, "recent": recent, "tone": tone}

def insiders(sym, today):
    txt = lib.cached_get("https://api.nasdaq.com/api/company/%s/insider-trades?limit=40&type=ALL&sortColumn=lastDate&sortOrder=DESC" % sym, 12 * 3600,
                         "insiders", validate=lambda t: '"transactionTable"' in t, headers={"Accept": "application/json"})
    return parse_insiders(txt, today) if txt else None


# ----------------------------------------------------------------------------- UK short positions (FCA register)
def read_xlsx(blob):
    """Minimal .xlsx reader (first worksheet) returning rows as {column letter: text}."""
    import io, zipfile
    import xml.etree.ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(io.BytesIO(blob))
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si"):
            ss.append("".join(t.text or "" for t in si.iter(ns + "t")))
    sheet = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
    rows = []
    for row in ET.fromstring(z.read(sheet)).iter(ns + "row"):
        r = {}
        for c in row.iter(ns + "c"):
            col = re.match(r"[A-Z]+", c.get("r") or "")
            v = c.find(ns + "v")
            if c.get("t") == "s" and v is not None: val = ss[int(v.text)]
            elif c.get("t") == "inlineStr": val = "".join(t.text or "" for t in c.iter(ns + "t"))
            else: val = v.text if v is not None else None
            if col and val is not None: r[col.group()] = val
        rows.append(r)
    return rows

def fca_shorts(rows, issuer_match):
    """Sum the disclosed net short positions (>= 0.5%) in one issuer from the FCA register rows."""
    hdr = None
    for i, r in enumerate(rows[:30]):
        txt = {k: v.lower() for k, v in r.items()}
        if any("issuer" in v for v in txt.values()) and any("net short" in v for v in txt.values()):
            hdr = (i, txt); break
    if not hdr: return None
    i, h = hdr
    c_iss = next(k for k, v in h.items() if "issuer" in v); c_pos = next(k for k, v in h.items() if "net short" in v)
    c_date = next((k for k, v in h.items() if "date" in v), None); c_hold = next((k for k, v in h.items() if "holder" in v), None)
    pos = []
    for r in rows[i + 1:]:
        if issuer_match.lower() in (r.get(c_iss) or "").lower():
            try: pos.append((float(r[c_pos]), r.get(c_hold), r.get(c_date)))
            except Exception: continue
    if not pos: return {"holders": 0, "total_pct": 0.0, "top": [], "date": None}
    pos.sort(reverse=True)
    # values may be fractions (0.012) or percents (1.2); disclosure threshold is 0.5%, so anything below 0.5 as fraction means percent already
    scale = 100 if max(p[0] for p in pos) < 0.5 else 1
    return {"holders": len(pos), "total_pct": sum(p[0] for p in pos) * scale, "top": [{"holder": p[1], "pct": p[0] * scale} for p in pos[:3]],
            "date": next((p[2] for p in pos if p[2]), None)}

def uk_shorts(issuer_match):
    blob = lib.cached_bytes("https://www.fca.org.uk/publication/data/short-positions-daily-update.xlsx", 12 * 3600, "fca_shorts")
    if not blob: return None
    try: return fca_shorts(read_xlsx(blob), issuer_match)
    except Exception as e:
        print("FCA parse failed:", e, file=__import__("sys").stderr); return None


# ----------------------------------------------------------------------------- options (CBOE delayed chains)
OCC = re.compile(r"^(.+?)(\d{6})([CP])(\d{8})$")

def parse_chain(js):
    """CBOE JSON -> (spot, [contract dicts]). Contracts carry expiry date, type, strike, bid/ask, iv, oi, volume, delta."""
    d = js["data"]; spot = d.get("current_price"); out = []
    for o in d.get("options", []):
        m = OCC.match(o.get("option", ""))
        if not m: continue
        try: exp = dt.datetime.strptime(m.group(2), "%y%m%d").date()
        except ValueError: continue
        bid, ask, last = o.get("bid") or 0, o.get("ask") or 0, o.get("last_trade_price") or 0
        mid = (bid + ask) / 2 if bid and ask else last
        out.append({"exp": exp, "type": m.group(3), "strike": int(m.group(4)) / 1000, "mid": mid, "iv": o.get("iv") or 0, "oi": o.get("open_interest") or 0,
                    "vol": o.get("volume") or 0, "delta": o.get("delta") or 0})
    return spot, out

def hv20(closes):
    r = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - 20, len(closes))]
    return statistics.pstdev(r) * math.sqrt(252)

def max_pain(contracts):
    strikes = sorted({c["strike"] for c in contracts})
    best = None
    for k in strikes:
        pay = sum(c["oi"] * max(0.0, k - c["strike"]) if c["type"] == "C" else c["oi"] * max(0.0, c["strike"] - k) for c in contracts)
        if best is None or pay < best[1]: best = (k, pay)
    return best[0] if best else None

def options_summary(spot, contracts, today, hv=None):
    if not spot or not contracts: return None
    calls = [c for c in contracts if c["type"] == "C"]; puts = [c for c in contracts if c["type"] == "P"]
    cv, pv = sum(c["vol"] for c in calls), sum(c["vol"] for c in puts)
    coi, poi = sum(c["oi"] for c in calls), sum(c["oi"] for c in puts)
    cprem = sum(c["vol"] * c["mid"] * 100 for c in calls); pprem = sum(c["vol"] * c["mid"] * 100 for c in puts)
    exps = sorted({c["exp"] for c in contracts if (c["exp"] - today).days >= 0})
    res = {"call_volume": cv, "put_volume": pv, "pc_volume": pv / cv if cv else None, "call_oi": coi, "put_oi": poi, "pc_oi": poi / coi if coi else None,
           "call_premium": cprem, "put_premium": pprem, "call_premium_share": cprem / (cprem + pprem) if cprem + pprem else None, "expiries": len(exps)}
    # ATM implied volatility near 30 days
    cand = [e for e in exps if 14 <= (e - today).days <= 75] or [e for e in exps if (e - today).days >= 5]
    if cand:
        e30 = min(cand, key=lambda e: abs((e - today).days - 30)); dte = (e30 - today).days
        ch = [c for c in contracts if c["exp"] == e30 and c["iv"] > 0.01]
        atm = sorted(ch, key=lambda c: abs(c["strike"] - spot))[:4]
        ivs = [c["iv"] for c in atm if abs(c["strike"] - spot) / spot < 0.08]
        if ivs:
            iv = sum(ivs) / len(ivs)
            res.update({"iv": iv, "iv_expiry": e30.isoformat(), "iv_dte": dte, "expected_move_pct": iv * math.sqrt(dte / 365) * 100,
                        "iv_vs_hv": iv / hv if hv else None, "hv20": hv})
        pc = [c for c in ch if c["type"] == "P" and c["delta"] and -0.35 < c["delta"] < -0.15]; cc = [c for c in ch if c["type"] == "C" and 0.15 < c["delta"] < 0.35]
        if pc and cc:
            p = min(pc, key=lambda c: abs(c["delta"] + 0.25)); q = min(cc, key=lambda c: abs(c["delta"] - 0.25))
            res["skew_pts"] = (p["iv"] - q["iv"]) * 100
    # front-month structure: max pain and open-interest walls for the expiry with the most open interest in the next 45 days
    near = [e for e in exps if 2 <= (e - today).days <= 45]
    if near:
        e = max(near, key=lambda x: sum(c["oi"] for c in contracts if c["exp"] == x))
        ch = [c for c in contracts if c["exp"] == e]
        up = [c for c in ch if c["type"] == "C" and c["strike"] >= spot and c["oi"]]; dn = [c for c in ch if c["type"] == "P" and c["strike"] <= spot and c["oi"]]
        res.update({"pain_expiry": e.isoformat(), "max_pain": max_pain([c for c in ch if c["oi"]]),
                    "call_wall": max(up, key=lambda c: c["oi"])["strike"] if up else None, "put_wall": max(dn, key=lambda c: c["oi"])["strike"] if dn else None})
    big = [c for c in contracts if c["vol"] >= 500 and c["vol"] >= 2 * max(c["oi"], 1)]
    big.sort(key=lambda c: c["vol"] * c["mid"], reverse=True)
    res["unusual"] = [{"exp": c["exp"].isoformat(), "type": "Call" if c["type"] == "C" else "Put", "strike": c["strike"], "volume": c["vol"], "oi": c["oi"],
                       "premium": c["vol"] * c["mid"] * 100} for c in big[:5]]
    # sentiment
    pts, why = 0, []
    if cv + pv >= 200:
        if res["pc_volume"] is not None:
            if res["pc_volume"] < 0.7: pts += 1; why.append("Put/call volume ratio %.2f is call-heavy." % res["pc_volume"])
            elif res["pc_volume"] > 1.0: pts -= 1; why.append("Put/call volume ratio %.2f is put-heavy." % res["pc_volume"])
        s = res["call_premium_share"]
        if s is not None:
            if s > 0.6: pts += 1; why.append("%.0f%% of option premium traded is in calls." % (s * 100))
            elif s < 0.4: pts -= 1; why.append("Only %.0f%% of option premium traded is in calls." % (s * 100))
        res["tone"] = "up" if pts >= 2 else "dn" if pts <= -2 else "mid"
        res["label"] = {"up": "Bullish", "dn": "Bearish", "mid": "Neutral"}[res["tone"]]
    else:
        res["tone"] = "mid"; res["label"] = "Thin activity"; why.append("Fewer than 200 contracts traded: ratios are not meaningful yet.")
    res["notes"] = why
    return res

IV_HISTORY = "iv_history.json"

def iv_rank(sym, iv, today):
    """Record today's ATM IV and rank it against what has been recorded so far (needs 20 distinct days)."""
    import json, os
    path = os.path.join(lib.CACHE, IV_HISTORY)
    try:
        with open(path) as fh: hist = json.load(fh)
    except Exception: hist = {}
    h = hist.setdefault(sym, {}); h[today.isoformat()] = iv
    for k in sorted(h)[:-400]: h.pop(k)
    try:
        with open(path, "w") as fh: json.dump(hist, fh)
    except Exception: pass
    vals = list(h.values())
    if len(vals) < 20: return {"status": "building", "days": len(vals), "need": 20}
    lo, hi = min(vals), max(vals)
    return {"status": "ok", "days": len(vals), "rank": (iv - lo) / (hi - lo) * 100 if hi > lo else 50.0, "percentile": sum(1 for x in vals if x <= iv) / len(vals) * 100}

def options_for(sym, today, closes):
    txt = lib.cached_get("https://cdn.cboe.com/api/global/delayed_quotes/options/%s.json" % sym, 600, "options",
                         validate=lambda t: '"options"' in t, timeout=40)
    if not txt: return None
    try:
        js = lib._json_after(txt)
        spot, ch = parse_chain(js)
        res = options_summary(spot, ch, today, hv20(closes) if closes and len(closes) > 25 else None)
    except Exception as e:
        print("options parse failed", sym, e, file=__import__("sys").stderr); return None
    if not res: return None
    res.update({"symbol": sym, "spot": spot, "as_of": js.get("timestamp")})
    if res.get("iv"): res["iv_rank"] = iv_rank(sym, res["iv"], today)
    return res


# ----------------------------------------------------------------------------- assemble per stock
def overall(parts):
    """Combine component tones into one flow verdict."""
    tones = [p for p in parts if p]
    s = sum(1 if t == "up" else -1 if t == "dn" else 0 for t in tones)
    tone = "up" if s >= 2 else "dn" if s <= -2 else "mid"
    return tone, {"up": "Net inflow / accumulation", "dn": "Net outflow / distribution", "mid": "Mixed or paused"}[tone]

def build(t, bars, tech, ctx, today):
    """The `flow` block for one stock. `ctx` holds prefetched shared data (FINRA days, UK shorts)."""
    base = volume_flow(bars, tech)
    if not base: return None
    price = bars["c"][-1]
    vd = verdict(base, price)
    out = {"volume_flow": base, "verdict": vd, "offexchange": None, "short": None, "uk_short": None, "options": None, "insiders": None, "gaps": []}
    us = t.get("us"); adr = t.get("adr")
    if us:
        out["offexchange"] = offexchange(us, bool(t.get("otc")), ctx["finra"], bars)
        if not out["offexchange"]: out["gaps"].append("Off-exchange data not available for this symbol.")
        if not t.get("otc"):
            out["insiders"] = insiders(us, today)
            out["short"] = short_interest(us, price)
            if not out["short"]: out["gaps"].append("Short interest not available from Nasdaq for this symbol.")
        else: out["gaps"].append("OTC security: exchange short interest is not published.")
    elif adr:
        si = short_interest(adr, None)
        if si: si["pct_of_shares"] = None; si["adr"] = adr
        out["short"] = si
        out["gaps"].append("Off-exchange (dark pool) data is only published for US-listed symbols.")
    else:
        out["gaps"].append("Off-exchange (dark pool) data is only published for US-listed symbols.")
    if t.get("fca"):
        out["uk_short"] = ctx.get("fca")
        if not out["uk_short"]: out["gaps"].append("FCA short-position register could not be read.")
    elif t["exchange"].startswith("Euronext Amsterdam"):
        out["gaps"].append("Short positions: the Dutch AFM register has no machine-readable feed; see afm.nl (net short positions above 0.5% are public).")
    elif t["ticker"].startswith("HKG:"):
        out["gaps"].append("Short selling: HKEX publishes daily short turnover as web pages only; not included.")
    opt = us if (us and not t.get("otc")) else adr
    if opt:
        out["options"] = options_for(opt, today, bars["c"])
        if out["options"]: out["options"]["proxy"] = bool(adr and not us)
        else: out["gaps"].append("Options data could not be fetched.")
    else:
        out["gaps"].append("No listed options available for this security.")
    parts = [vd["tone"], out["offexchange"] and out["offexchange"]["tone"], out["options"] and out["options"]["tone"], out["short"] and out["short"]["tone"]]
    tone, label = overall(parts)
    out["overall"] = {"tone": tone, "label": label}
    out["text"] = verdict_text(out, vd)
    return out

def verdict_text(f, vd):
    bits = [vd["text"]]
    o = f.get("options")
    if o and o.get("label") not in (None, "Thin activity"):
        bits.append("Options flow is %s%s." % (o["label"].lower(), " (via the %s ADR)" % o["symbol"] if o.get("proxy") else ""))
    off = f.get("offexchange")
    if off and off["notes"]: bits.append(off["notes"][-1])
    s = f.get("short")
    if s and s["tone"] == "dn": bits.append("Short interest is elevated (%.1f days to cover%s)." % (s["days_to_cover"], ", up %.0f%% since the previous settlement" % s["change_pct"] if s["change_pct"] and s["change_pct"] > 8 else ""))
    elif s and s["tone"] == "up": bits.append("Short interest is falling (%.0f%% since the previous settlement): shorts are covering." % s["change_pct"])
    return " ".join(bits)
