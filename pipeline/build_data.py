#!/usr/bin/env python3
"""
Builds the public site: docs/index.html plus docs/data/*.json
Input : watchlist.json (tickers and public company details only; no holdings anywhere)
Output: stocks.json (analysis, money flow, options, context, risk, news per ticker), market.json, calendar.json, meta.json

Run locally:  python3 pipeline/build_data.py
In CI:        .github/workflows/update-data.yml  (optional secret AV_KEY: Alpha Vantage, used for earnings dates
              and as a price fallback when Yahoo is unreachable)
"""
import csv, io, json, os, shutil, sys, time, urllib.parse
import datetime as dt
import traceback
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib, analysis, flow, context, analysts, yields, shorts
import macro as macro_mod
from lib import (yahoo_chart, av_chart, parse_rss, themes_of, cached_get, estimate_next_dividend,
                 MARKET_FEEDS, CENTRAL_BANK_FEEDS, MARKET, STATUS)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data")
SITE = os.path.join(ROOT, "site")
WL = json.load(open(os.path.join(ROOT, "watchlist.json"), encoding="utf-8"))
TICKERS = WL["tickers"]
AV_KEY = os.environ.get("AV_KEY", "").strip()

def now_utc(): return dt.datetime.now(dt.timezone.utc)
def today(): return now_utc().date()

def write(name, obj):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), default=str, allow_nan=False)
    print("wrote", name, os.path.getsize(path), "bytes")

def clean(o):
    """JSON has no NaN/Infinity; turn them into null so the browser can parse the files."""
    if isinstance(o, float): return o if o == o and o not in (float("inf"), float("-inf")) else None
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    return o

# ----------------------------------------------------------------------------- prices
def get_chart(t):
    d = yahoo_chart(t["yahoo"])
    if d: return d
    d = av_chart(t.get("av"), AV_KEY)
    if d: print("price fallback: Alpha Vantage for", t["ticker"])
    return d

# ----------------------------------------------------------------------------- news
def dedupe(items):
    seen, out = set(), []
    for i in items:
        k = "".join(ch for ch in i["title"].lower() if ch.isalnum())[:60]
        if k in seen: continue
        seen.add(k); out.append(i)
    return out

def stock_news(t, move_pct):
    q = urllib.parse.quote("%s stock when:7d" % t["news"])
    txt = cached_get("https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en" % q, 900, "news")
    items = dedupe(parse_rss(txt, 30) if txt else [])
    items.sort(key=lambda i: i["time"], reverse=True)
    items = items[:12]
    th = themes_of([i["title"] for i in items])
    pos = sum(i["tone"] == "pos" for i in items); neg = sum(i["tone"] == "neg" for i in items)
    mood = "Positive" if pos > neg + 1 else "Negative" if neg > pos + 1 else "Mixed"
    if not items:
        return {"mood": None, "summary": "No headlines available right now.", "themes": [], "items": []}
    mv = ("%s %.2f%% on the last session. " % ("Up" if move_pct >= 0 else "Down", abs(move_pct))) if move_pct is not None else ""
    summary = "%sCoverage is %s (%d positive, %d negative of %d headlines). Main themes: %s." % (
        mv, mood.lower(), pos, neg, len(items), ", ".join(x for x, _ in th[:3]) or "no dominant theme")
    return {"mood": mood, "summary": summary, "themes": th[:5], "items": items}

# ----------------------------------------------------------------------------- stocks
def prefetch_flow():
    """Shared downloads for the money-flow section: FINRA daily files and the FCA short register."""
    ctx = {"finra": [], "fca": None, "afm": None}
    if any(t.get("us") for t in TICKERS):
        ctx["finra"] = flow.finra_days(today())
        print("FINRA daily files:", len(ctx["finra"]))
    fca = next((t["fca"] for t in TICKERS if t.get("fca")), None)
    if fca: ctx["fca"] = flow.uk_shorts(fca)
    if any(t.get("afm") for t in TICKERS):
        ctx["afm"] = shorts.afm_rows(); print("AFM register rows:", len(ctx["afm"] or []))
    return ctx

def build_stock(t, data, fctx):
    out = {k: t[k] for k in ("ticker", "yahoo", "name", "sector", "currency", "exchange")}
    out["status"] = "no_data"; out["source"] = None
    a = None
    if data:
        try:
            a = analysis.analyse(data["bars"])
        except Exception:
            print("analysis failed for", t["ticker"]); traceback.print_exc(); a = None
        if a:
            out.update(a); out["status"] = "ok"; out["source"] = data["source"]; out["via"] = data.get("via")
            out["bars"] = len(data["bars"]["c"])
            if out["bars"] < 260: out["note"] = "Short price history (%d sessions): 200-day average and regime model need about a year." % out["bars"]
            try:
                out["flow"] = flow.build(t, data["bars"], a["tech"], fctx, today())
            except Exception:
                print("flow failed for", t["ticker"]); traceback.print_exc(); out["flow"] = None
        else:
            out["note"] = "Not enough price history for analysis."
    try:
        out["shorts"] = shorts.for_stock(t, out.get("flow") or {}, fctx, today())
    except Exception:
        print("shorts failed for", t["ticker"]); traceback.print_exc(); out["shorts"] = None
    try:
        out["analysts"] = analysts.analyse(t, today())
    except Exception:
        print("analysts failed for", t["ticker"]); traceback.print_exc(); out["analysts"] = None
    move = out.get("price", {}).get("chg_pct")
    out["news"] = stock_news(t, move)
    return out

# ----------------------------------------------------------------------------- calendar
def calendar(data):
    td = today(); ev = []
    # 1) dividends: estimated from each stock's own Yahoo history (when available)
    for t in TICKERS:
        d = data.get(t["ticker"]); dv = d["divs"] if d else []
        dates = [dt.datetime.fromtimestamp(x[0], dt.timezone.utc).date() for x in dv]
        est = estimate_next_dividend(dates, td)
        if not est: continue
        nxt, gap = est
        ev.append({"date": nxt.isoformat(), "kind": "dividend", "ticker": t["ticker"], "approx": True,
                   "title": "%s ex-dividend (est.)" % t["ticker"].split(":")[-1],
                   "detail": "Last %s: %.4g, cadence about every %d days" % (dates[-1].isoformat(), dv[-1][1], gap)})
    # 2) earnings: Alpha Vantage calendar when it lists the ticker, otherwise the dates pinned in watchlist.json
    av = {}
    if AV_KEY:
        txt = cached_get("https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&horizon=6month&apikey=" + AV_KEY, 20 * 3600,
                         "alphavantage_calendar", validate=lambda s: s.startswith("symbol"))
        if txt:
            want = {t["yahoo"].split(".")[0].upper(): t for t in TICKERS}
            for r in csv.DictReader(io.StringIO(txt)):
                t = want.get((r.get("symbol") or "").upper())
                if t and t["ticker"] not in av:
                    av[t["ticker"]] = r
    for t in TICKERS:
        tk = t["ticker"]; name = tk.split(":")[-1]; o = WL.get("earnings_overrides", {}).get(tk)
        if tk in av:
            r = av[tk]
            ev.append({"date": r["reportDate"], "kind": "earnings", "ticker": tk, "approx": False, "title": "%s earnings" % name,
                       "detail": "Alpha Vantage%s, EPS estimate %s" % (", " + r["timeOfTheDay"] if r.get("timeOfTheDay") else "", r.get("estimate") or "n/a")})
        elif o:
            ev.append({"date": o["date"], "kind": "earnings", "ticker": tk, "approx": bool(o.get("approx")), "title": "%s earnings" % name,
                       "detail": o.get("note") or "Company-announced date"})
    # 3) macro
    for e in WL.get("macro_events", []):
        ev.append({"date": e["date"], "kind": "macro", "ticker": None, "approx": False, "title": e["title"], "detail": e.get("detail", "")})
    ev = [e for e in ev if dt.date.fromisoformat(e["date"]) >= td - dt.timedelta(days=3)]
    for e in ev: e["days"] = (dt.date.fromisoformat(e["date"]) - td).days
    ev.sort(key=lambda e: (e["date"], e["kind"]))
    return ev

def calendar_notes(events, stocks):
    """Say why each event matters, using the options-implied move where we have one."""
    by = {s["ticker"]: s for s in stocks}
    for e in events:
        s = by.get(e["ticker"]) if e["ticker"] else None
        if e["kind"] == "earnings" and s:
            o = (s.get("flow") or {}).get("options")
            e["why"] = ("Options imply a move of about +/-%.1f%% by the %s expiry." % (o["expected_move_pct"], o["iv_expiry"][5:])) if o and o.get("expected_move_pct") else "Largest scheduled volatility event for the stock; regimes often break at earnings."
        elif e["kind"] == "macro": e["why"] = "Rate decisions reprice every stock; markets trade the surprise versus expectations."
        elif e["kind"] == "dividend": e["why"] = "Price typically drops by about the dividend on the ex-date."
    return events

# ----------------------------------------------------------------------------- market
QUOTE_TTL = {"equity": 900, "vol": 900, "rates": 900}   # everything else refreshes about every other build

def index_quote(item):
    group, sym, name = item
    q = context.quote(sym, name)
    if not q: return {"group": group, "symbol": sym, "name": name, "price": None}
    c = q["_c"]; b = q["_bars"]
    tech = lib.compute_tech(b) if len(c) >= 60 else None
    series = [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date(), v) for t, v in zip(b["t"], c)]
    return {"group": group, "symbol": sym, "name": name, "price": c[-1], "chg_pct": q["chg_pct"], "asof": q["asof"], "spark": c[-60:],
            "ret_1m": q["ret_1m"], "ret_3m": q["ret_3m"], "ret_ytd": tech and tech["ret_ytd"],
            "rsi": tech and tech["rsi"], "trend": tech and next((s["label"] for s in tech["signals"] if s["label"] in ("Uptrend", "Downtrend", "Mixed trend")), None),
            "above_200": bool(tech and tech["sma200"] and c[-1] > tech["sma200"]),
            "support": tech and tech["lo20"], "resistance": tech and tech["hi20"], "from_hi52_pct": tech and (c[-1] / tech["hi52"] - 1) * 100,
            "extreme": yields.extreme(series) if len(series) > 80 else None}

def feed_items(url, name, ttl=900, limit=12):
    txt = cached_get(url, ttl, "market_news")
    items = parse_rss(txt, limit) if txt else []
    for i in items: i["source"] = i["source"] or name
    return items

def breadth(stocks):
    ok = [s for s in stocks if s["status"] == "ok"]
    reg = {"bull": 0, "sideways": 0, "bear": 0}
    for s in ok:
        r = s.get("regime")
        if r: reg[r["current"]] += 1
    scores = [s["confluence"]["score"] for s in ok if s["confluence"]["score"] is not None]
    fl = {"up": 0, "mid": 0, "dn": 0}
    for s in ok:
        o = (s.get("flow") or {}).get("overall")
        if o: fl[o["tone"]] += 1
    return {"n": len(ok), "bullish": sum(s["bias"] == "Bullish" for s in ok), "neutral": sum(s["bias"] == "Neutral" for s in ok),
            "bearish": sum(s["bias"] == "Bearish" for s in ok), "regimes": reg, "flow": fl,
            "avg_confluence": sum(scores) / len(scores) if scores else None,
            "above_50": sum(1 for s in ok if s["tech"]["sma50"] and s["price"]["last"] > s["tech"]["sma50"]),
            "above_200": sum(1 for s in ok if s["tech"]["sma200"] and s["price"]["last"] > s["tech"]["sma200"]),
            "have_200": sum(1 for s in ok if s["tech"]["sma200"])}

def preamble(quotes, heads, events, rates):
    """Global macro preamble in the report's order: US, Asia, Europe, rates, FX, volatility, commodities, crypto, key risks, events."""
    q = {x["symbol"]: x for x in quotes if x.get("price") is not None}
    mv = lambda s: "%s %+.2f%%" % (q[s]["name"], q[s]["chg_pct"]) if s in q else None
    row = lambda label, syms: {"label": label, "text": ", ".join(x for x in (mv(s) for s in syms) if x)} if any(s in q for s in syms) else None
    out = [row("US cash and futures", ["^GSPC", "^NDX", "^DJI", "^RUT", "ES=F", "NQ=F"]),
           row("Asia", ["^N225", "000001.SS", "^HSI", "^KS11", "^AXJO"]),
           row("Europe", ["^STOXX", "^STOXX50E", "^GDAXI", "^FCHI", "^AEX", "^FTSE"])]
    ry = {r["id"]: r for r in (rates or {}).get("rows", [])}
    bits = []
    for k in ("US 2Y", "US 10Y", "US 30Y", "UK 10Y", "DE 10Y"):
        r = ry.get(k)
        if r: bits.append("%s %.2f%%%s" % (k, r["last"], " (%+.0f bp)" % r["chg_1d_bp"] if r.get("chg_1d_bp") is not None else ""))
    sp = {x["label"]: x for x in (rates or {}).get("spreads", [])}
    for lab, short in (("US 10Y minus 2Y (curve)", "2s10s"), ("France minus Germany 10Y", "OAT-Bund"), ("Italy minus Germany 10Y", "BTP-Bund")):
        if lab in sp: bits.append("%s %+.0f bp" % (short, sp[lab]["bp"]))
    if bits: out.append({"label": "Government bonds", "text": ", ".join(bits)})
    fx = [mv(s) for s in ("DX-Y.NYB", "EURUSD=X", "USDJPY=X", "EURGBP=X", "EURCHF=X") if s in q]
    if fx: out.append({"label": "Currencies", "text": ", ".join(fx)})
    vol = []
    if "^VIX" in q:
        v = q["^VIX"]["price"]
        vol.append("VIX %.1f (%+.1f%%), %s" % (v, q["^VIX"]["chg_pct"], "calm" if v < 16 else "normal" if v < 22 else "elevated: markets are pricing in stress" if v < 30 else "very high: risk-off"))
    if "^MOVE" in q: vol.append("MOVE (rates volatility) %.1f (%+.1f%%)" % (q["^MOVE"]["price"], q["^MOVE"]["chg_pct"]))
    if vol: out.append({"label": "Volatility", "text": ", ".join(vol)})
    com = ["%s %.2f (%+.2f%%)" % (q[s]["name"], q[s]["price"], q[s]["chg_pct"]) for s in ("BZ=F", "CL=F", "GC=F", "HG=F") if s in q]
    if com: out.append({"label": "Commodities", "text": ", ".join(com)})
    cr = ["%s \u20ac%s (%+.2f%%)" % (q[sy]["name"].split(" ")[0], "{:,.0f}".format(q[sy]["price"]), q[sy]["chg_pct"]) for sy in ("BTC-EUR", "ETH-EUR") if sy in q]
    if cr: out.append({"label": "Crypto (24/7, in EUR)", "text": ", ".join(cr)})
    risks = [h for h in heads if h["tone"] == "neg"][:2]
    if risks: out.append({"label": "Key risks in the news", "text": " | ".join(h["title"] for h in risks)})
    soon = [e for e in events if e["kind"] in ("macro", "earnings") and 0 <= e["days"] <= 14]
    out.append({"label": "Next 14 days", "text": "; ".join("%s (%s)" % (e["title"], e["date"][5:]) for e in soon[:8]) if soon else "No scheduled FOMC, ECB or tracked earnings."})
    return [x for x in out if x]

def market_read(quotes, heads, br):
    q = {x["symbol"]: x for x in quotes if x.get("price") is not None}
    out = []
    eq = [q[s] for s in ("^GSPC", "^IXIC", "^AEX", "^STOXX50E", "^HSI", "^N225") if s in q]
    if eq:
        up = [x for x in eq if x["chg_pct"] >= 0]
        out.append("%d of %d tracked equity indices rose on the last session." % (len(up), len(eq)))
        tr = [x for x in eq if x.get("trend")]
        if tr: out.append("Index trends: " + ", ".join("%s %s" % (x["name"], x["trend"].lower()) for x in tr) + ".")
    if heads:
        pos = sum(h["tone"] == "pos" for h in heads); neg = sum(h["tone"] == "neg" for h in heads)
        out.append("Headline tone across %d recent market stories: %d positive, %d negative, %d neutral (keyword based)." % (len(heads), pos, neg, len(heads) - pos - neg))
    if br["n"]:
        out.append(("The %d stocks tracked: %d bullish, %d neutral, %d bearish on the confluence score; %d of %d with a 200-day average trade above it." % (
            br["n"], br["bullish"], br["neutral"], br["bearish"], br["above_200"], br["have_200"])) if br["have_200"] else
            "The %d stocks tracked: %d bullish, %d neutral, %d bearish on the confluence score." % (br["n"], br["bullish"], br["neutral"], br["bearish"]))
        f = br["flow"]; out.append("Money flow across them: %d net inflow, %d mixed, %d net outflow." % (f["up"], f["mid"], f["dn"]))
    return out

def vol_panel(quotes):
    """VIX, its term structure, SKEW and MOVE with a plain regime label, plus the options-implied S&P 500 move."""
    q = {x["symbol"]: x for x in quotes if x.get("price") is not None}
    g = lambda s: q[s]["price"] if s in q else None
    vix, v9, v3m = g("^VIX"), g("^VIX9D"), g("^VIX3M")
    term = None
    if vix and v3m:
        if v9 and v9 > vix > v3m: term = "Backwardation (stress: near-term fear above longer-term)"
        elif v9 and v9 < vix < v3m or (not v9 and vix < v3m): term = "Contango (normal: calm near term, more uncertainty further out)"
        elif vix > v3m: term = "Inverted (near-term volatility above 3-month)"
        else: term = "Flat / mixed"
    spx = q.get("^GSPC") or {}
    bull = bool(spx.get("above_200"))
    regime = None
    if vix is not None:
        regime = ("Low-volatility bull market" if vix < 16 and bull else "Low volatility, weak trend" if vix < 16 else
                  "Normal volatility, uptrend" if vix < 22 and bull else "Normal volatility, no clear uptrend" if vix < 22 else
                  "Elevated stress" if vix < 30 else "High-volatility crisis regime")
    out = {"vix": vix, "vix_chg_pct": q["^VIX"]["chg_pct"] if "^VIX" in q else None, "skew": g("^SKEW"), "move": g("^MOVE"), "term": term, "regime": regime}
    em = flow.expected_moves("SPY", today())
    if em and em["moves"]:
        spot = spx.get("price"); mv = em["moves"]
        pick = [mv[0]] + ([next((m for m in mv if m["dte"] >= 5), mv[-1])] if len(mv) > 1 else [])
        out["expected"] = [{"exp": m["exp"], "dte": m["dte"], "move_pct": m["move_pct"], "points": spot * m["move_pct"] / 100 if spot else None} for m in pick if m is not None]
        out["expected_source"] = "SPY option straddles applied to the S&P 500 level"
    return out

def drivers_box(quotes, rates, vol, macro_summary):
    """'Market drivers and catalysts': one line per asset class, built from the numbers. Descriptive only."""
    q = {x["symbol"]: x for x in quotes if x.get("price") is not None}
    ry = {r["id"]: r for r in (rates or {}).get("rows", [])}
    out = []
    eq = [q[k] for k in ("^GSPC", "^IXIC", "^STOXX", "^N225", "^HSI") if k in q]
    if eq:
        up = sum(1 for x in eq if x["chg_pct"] >= 0)
        hi = [x["name"] for x in eq if x.get("extreme", "") and "highest" in (x.get("extreme") or "") ]
        out.append({"label": "Equities", "text": "%d of %d major indices rose%s." % (up, len(eq), "; at their highest close in 12 months: " + ", ".join(hi) if hi else "")})
    r10 = ry.get("US 10Y")
    if r10:
        t = "US 10-year yield %.2f%% (%s bp on the day)" % (r10["last"], "%+.0f" % r10["chg_1d_bp"] if r10.get("chg_1d_bp") is not None else "n/a")
        sp = {x["label"]: x["bp"] for x in (rates or {}).get("spreads", [])}
        if "France minus Germany 10Y" in sp: t += "; France-Germany spread %.0f bp" % sp["France minus Germany 10Y"]
        out.append({"label": "Fixed income", "text": t + "."})
    if vol and vol.get("vix") is not None:
        out.append({"label": "Volatility", "text": "VIX %.1f%s%s." % (vol["vix"], ", " + vol["regime"].lower() if vol.get("regime") else "", "; term structure " + vol["term"].split(" (")[0].lower() if vol.get("term") else "")})
    cm = [q[k] for k in ("BZ=F", "CL=F", "GC=F", "HG=F") if k in q]
    if cm:
        top = max(cm, key=lambda x: abs(x["chg_pct"]))
        t = "%s %.2f (%+.2f%%)" % (q["BZ=F"]["name"], q["BZ=F"]["price"], q["BZ=F"]["chg_pct"]) if "BZ=F" in q else ""
        t += "; biggest mover %s %+.2f%%" % (top["name"], top["chg_pct"])
        g = q.get("GC=F")
        if g and g.get("support"): t += "; gold support near %.0f, resistance near %.0f" % (g["support"], g["resistance"])
        out.append({"label": "Commodities", "text": t + "."})
    fx = [q[k] for k in ("DX-Y.NYB", "EURUSD=X", "USDJPY=X") if k in q]
    if fx:
        t = ", ".join("%s %+.2f%%" % (x["name"], x["chg_pct"]) for x in fx)
        ex = [x["name"] + ": " + x["extreme"] for x in fx if x.get("extreme")]
        out.append({"label": "Currencies", "text": t + ("; " + "; ".join(ex) if ex else "") + "."})
    cr = [q[k] for k in ("BTC-EUR", "ETH-EUR") if k in q]
    if cr: out.append({"label": "Digital assets", "text": ", ".join("%s %+.2f%%" % (x["name"], x["chg_pct"]) for x in cr) + "."})
    for line in (macro_summary or [])[:2]: out.append({"label": "Link to your stocks", "text": line})
    return out

def market(stocks, events, rates):
    with ThreadPoolExecutor(4) as ex:
        quotes = list(ex.map(index_quote, MARKET))
    heads = []
    for name, url in MARKET_FEEDS: heads += feed_items(url, name)
    heads = [h for h in dedupe(heads)]
    heads.sort(key=lambda i: i["time"], reverse=True)
    heads = heads[:40]
    th = themes_of([h["title"] for h in heads]); sample = {}
    for h in heads:
        tl = " " + h["title"].lower() + " "
        for tname, kws in lib.THEMES.items():
            if any(k in tl for k in kws) and len(sample.setdefault(tname, [])) < 3: sample[tname].append(h)
    cb = []
    for name, url in CENTRAL_BANK_FEEDS:
        for i in feed_items(url, name, ttl=3600, limit=5): i["bank"] = name; cb.append(i)
    br = breadth(stocks)
    vol = vol_panel(quotes)
    return quotes, heads, {"quotes": quotes, "headlines": heads, "central_banks": cb, "breadth": br, "preamble": preamble(quotes, heads, events, rates), "rates": rates, "vol": vol,
                           "drivers": [{"theme": t, "count": c, "headlines": sample.get(t, [])} for t, c in th[:6]], "read": market_read(quotes, heads, br)}

# ----------------------------------------------------------------------------- per-stock extras that need events, quotes and flow
def top_catalyst(s):
    ev = next((e for e in s.get("events", []) if e["kind"] == "earnings" and e["days"] <= 30), None)
    if ev: return {"kind": "earnings", "text": "Earnings %s (in %d d)" % (ev["date"][5:], ev["days"])}
    an = s.get("analysts")
    if an:
        recent = [a for a in an["actions"] if a["kind"] in ("upgrade", "downgrade") and (today() - dt.date.fromisoformat(a["date"])).days <= 14]
        if recent: a = recent[0]; return {"kind": "analyst", "text": "%s %s by %s (%s)" % (a["rating"], "upgrade" if a["kind"] == "upgrade" else "downgrade", a["firm"], a["date"][5:])}
    o = (s.get("flow") or {}).get("options")
    if o and o.get("unusual"): return {"kind": "options", "text": "Unusual options activity"}
    it = next((i for i in s["news"]["items"] if i["tone"] != "neu"), None) or (s["news"]["items"][0] if s["news"]["items"] else None)
    return {"kind": "news", "text": it["title"][:70]} if it else {"kind": "none", "text": "No near-term catalyst"}

def market_cap(s):
    """Market cap is only known for US-listed names (from the Nasdaq summary); an ADR's short-interest record is not the local listing's."""
    sh = (s.get("flow") or {}).get("short") or {}
    return None if sh.get("adr") else sh.get("market_cap")

def enrich(stocks, charts, events, macro):
    for s in stocks:
        if s["status"] != "ok": continue
        t = next(x for x in TICKERS if x["ticker"] == s["ticker"])
        series = (charts[s["ticker"]]["bars"]["t"], charts[s["ticker"]]["bars"]["c"])
        s["lean"] = context.lean(s["confluence"]["score"])
        s["identity"] = context.identity(s, market_cap(s))
        s["context"] = context.sector_context(t, s["returns"]["1m"], series, context._Q)
        s["risk"] = context.risk_table(s, s["events"], macro, s["context"].get("fx"))
        s["range"] = context.session_range(s)
        s["horizon"] = context.time_horizon(s, s["events"])
        s["catalyst"] = top_catalyst(s)

# ----------------------------------------------------------------------------- main
def site():
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    for f in os.listdir(SITE):
        shutil.copy(os.path.join(SITE, f), os.path.join(ROOT, "docs", f))
    open(os.path.join(ROOT, "docs", ".nojekyll"), "w").close()

def main():
    t0 = time.time()
    with ThreadPoolExecutor(4) as ex:
        charts = dict(zip([t["ticker"] for t in TICKERS], ex.map(get_chart, TICKERS)))
    ok = sum(1 for d in charts.values() if d)
    print("prices fetched for %d of %d tickers in %.0fs" % (ok, len(TICKERS), time.time() - t0))
    # context symbols (sector ETFs, peers, FX, local indices, plus the market table) are fetched once and shared
    syms = []
    for t in TICKERS:
        for k in ("etf", "index", "fx"):
            if t.get(k): syms.append(t[k])
        syms += t.get("peers", [])
    ttl = {}
    for g, sym, nm in MARKET: ttl[sym] = QUOTE_TTL.get(g, 2400)
    for sym in syms: ttl.setdefault(sym, 3600)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda sy: context.quote(sy, ttl=ttl[sy]), sorted(ttl)))
    syms = list(ttl)
    print("context quotes: %d of %d in %.0fs" % (sum(1 for s in set(syms) if context._Q.get(s)), len(set(syms)), time.time() - t0))
    fctx = prefetch_flow()
    stocks = [build_stock(t, charts.get(t["ticker"]), fctx) for t in TICKERS]
    events = calendar(charts)
    by = {}
    for e in events:
        if e["ticker"]: by.setdefault(e["ticker"], []).append(e)
    for s in stocks: s["events"] = [e for e in by.get(s["ticker"], []) if e["days"] >= 0][:5]
    macro = [e for e in events if e["kind"] == "macro"]
    enrich(stocks, charts, events, macro)
    events = calendar_notes(events, stocks)
    for s in stocks: s["events"] = [e for e in events if e["ticker"] == s["ticker"] and e["days"] >= 0][:5]
    try:
        rates, yraw = yields.build(today())
    except Exception:
        print("yields failed"); traceback.print_exc(); rates, yraw = {"rows": [], "spreads": [], "inflation": []}, {}
    quotes, heads, mk = market(stocks, events, rates)
    vix = next((q["price"] for q in quotes if q["symbol"] == "^VIX" and q.get("price")), None)
    series = {s["ticker"]: (charts[s["ticker"]]["bars"]["t"], charts[s["ticker"]]["bars"]["c"]) for s in stocks if s["status"] == "ok"}
    spx = context._Q.get("^GSPC")
    mk["universe"] = context.universe(stocks, series, (spx["_t"], spx["_c"]) if spx else None, events, vix, macro, heads)
    try:
        mac = macro_mod.build(series, {s["ticker"]: s["name"] for s in stocks}, {k: v for k, v in context._Q.items() if v}, yraw)
    except Exception:
        print("macro failed"); traceback.print_exc(); mac = None
    mk["macro"] = mac
    if mac:
        for s in stocks:
            if s["ticker"] in mac["text"]:
                s["macro"] = {"text": mac["text"][s["ticker"]], "corr": next(m["corr"] for m in mac["matrix"] if m["ticker"] == s["ticker"])}
    mk["drivers_box"] = drivers_box(quotes, rates, mk.get("vol"), mac and mac["summary"])
    write("calendar.json", {"events": events, "today": today().isoformat()})
    write("stocks.json", clean({"updated": now_utc().isoformat(timespec="seconds"), "stocks": stocks}))
    write("market.json", clean(mk))
    srcs = {}
    for s in stocks: srcs[s["source"] or "none"] = srcs.get(s["source"] or "none", 0) + 1
    write("meta.json", {"updated": now_utc().replace(tzinfo=None).isoformat(timespec="seconds") + "Z", "tickers_ok": ok,
                        "tickers_total": len(TICKERS), "price_sources": srcs, "status": dict(STATUS), "build_seconds": round(time.time() - t0)})
    site()
    if ok == 0:
        # Still exit 0: headlines, calendar and meta status are useful, and a red run would skip the cache save and deploy.
        print("WARNING: no price data fetched from any source")

if __name__ == "__main__":
    main()
