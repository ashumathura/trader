#!/usr/bin/env python3
"""
Builds the public site: docs/index.html plus docs/data/*.json
Input : watchlist.json (tickers and public company details only; no holdings anywhere)
Output: stocks.json (analysis + news per ticker), market.json, calendar.json, meta.json

Run locally:  python3 pipeline/build_data.py
In CI:        .github/workflows/update-data.yml  (optional secret AV_KEY: Alpha Vantage, used for earnings dates
              and as a price fallback when Yahoo is unreachable)
"""
import csv, io, json, os, shutil, statistics, sys, time, urllib.parse
import datetime as dt
import traceback
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib, analysis
from lib import (yahoo_chart, av_chart, parse_rss, themes_of, cached_get, estimate_next_dividend,
                 MARKET_FEEDS, CENTRAL_BANK_FEEDS, INDICES, STATUS)

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
def build_stock(t, data):
    out = {k: t[k] for k in ("ticker", "yahoo", "name", "sector", "currency", "exchange")}
    out["status"] = "no_data"; out["source"] = None
    if data:
        try:
            a = analysis.analyse(data["bars"])
        except Exception:
            print("analysis failed for", t["ticker"]); traceback.print_exc(); a = None
        if a:
            out.update(a); out["status"] = "ok"; out["source"] = data["source"]; out["via"] = data.get("via")
            out["bars"] = len(data["bars"]["c"])
            if out["bars"] < 260: out["note"] = "Short price history (%d sessions): 200-day average and regime model need about a year." % out["bars"]
        else:
            out["note"] = "Not enough price history for analysis."
    move = out.get("price", {}).get("chg_pct")
    out["news"] = stock_news(t, move)
    return out

# ----------------------------------------------------------------------------- calendar
def calendar(stocks, data):
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
    write("calendar.json", {"events": ev, "today": td.isoformat()})
    return ev

# ----------------------------------------------------------------------------- market
def index_quote(item):
    sym, name = item
    d = yahoo_chart(sym, "1y", "1d", ttl=900, drop_partial=False)
    if not d or len(d["bars"]["c"]) < 2: return {"symbol": sym, "name": name, "price": None}
    c = d["bars"]["c"]
    tech = lib.compute_tech(d["bars"]) if len(c) >= 60 else None
    return {"symbol": sym, "name": name, "price": c[-1], "chg_pct": (c[-1] / c[-2] - 1) * 100, "spark": c[-60:],
            "ret_1m": tech and tech["ret_1m"], "ret_3m": tech and tech["ret_3m"], "ret_ytd": tech and tech["ret_ytd"],
            "rsi": tech and tech["rsi"], "trend": tech and next((s["label"] for s in tech["signals"] if s["label"] in ("Uptrend", "Downtrend", "Mixed trend")), None),
            "above_200": bool(tech and tech["sma200"] and c[-1] > tech["sma200"])}

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
    return {"n": len(ok), "bullish": sum(s["bias"] == "Bullish" for s in ok), "neutral": sum(s["bias"] == "Neutral" for s in ok),
            "bearish": sum(s["bias"] == "Bearish" for s in ok), "regimes": reg,
            "avg_confluence": sum(scores) / len(scores) if scores else None,
            "above_50": sum(1 for s in ok if s["tech"]["sma50"] and s["price"]["last"] > s["tech"]["sma50"]),
            "above_200": sum(1 for s in ok if s["tech"]["sma200"] and s["price"]["last"] > s["tech"]["sma200"]),
            "have_200": sum(1 for s in ok if s["tech"]["sma200"])}

def market_read(quotes, heads, br, events):
    """A few plain-language bullets assembled from the numbers. Descriptive only."""
    q = {x["symbol"]: x for x in quotes if x.get("price") is not None}
    out = []
    eq = [q[s] for s in ("^GSPC", "^IXIC", "^AEX", "^STOXX50E", "^HSI") if s in q]
    if eq:
        up = [x for x in eq if x["chg_pct"] >= 0]
        best = max(eq, key=lambda x: x["chg_pct"]); worst = min(eq, key=lambda x: x["chg_pct"])
        out.append("%d of %d tracked equity indices rose on the last session. Strongest: %s (%+.2f%%), weakest: %s (%+.2f%%)." % (
            len(up), len(eq), best["name"], best["chg_pct"], worst["name"], worst["chg_pct"]))
        tr = [x for x in eq if x.get("trend")]
        if tr: out.append("Index trends: " + ", ".join("%s %s" % (x["name"], x["trend"].lower()) for x in tr) + ".")
    if "^VIX" in q:
        v = q["^VIX"]["price"]
        out.append("VIX at %.1f (%s): %s." % (v, "%+.1f%% on the day" % q["^VIX"]["chg_pct"],
                   "calm" if v < 16 else "normal" if v < 22 else "elevated, markets are pricing in stress" if v < 30 else "very high, risk-off"))
    if "^TNX" in q: out.append("US 10-year yield %.2f%% (%+.1f%% on the day)." % (q["^TNX"]["price"], q["^TNX"]["chg_pct"]))
    if "EURUSD=X" in q: out.append("EUR/USD %.4f (%+.2f%%): moves in the euro change the translated value of dollar earnings for the European names." % (q["EURUSD=X"]["price"], q["EURUSD=X"]["chg_pct"]))
    if heads:
        pos = sum(h["tone"] == "pos" for h in heads); neg = sum(h["tone"] == "neg" for h in heads)
        out.append("Headline tone across %d recent market stories: %d positive, %d negative, %d neutral (keyword based)." % (len(heads), pos, neg, len(heads) - pos - neg))
    if br["n"]:
        out.append("Your 10 stocks: %d bullish, %d neutral, %d bearish on the confluence score; %d of %d with a 200-day average trade above it." % (
            br["bullish"], br["neutral"], br["bearish"], br["above_200"], br["have_200"]) if br["have_200"] else
            "Your 10 stocks: %d bullish, %d neutral, %d bearish on the confluence score." % (br["bullish"], br["neutral"], br["bearish"]))
    soon = [e for e in events if e["kind"] in ("macro", "earnings") and 0 <= e["days"] <= 14]
    if soon: out.append("Next 14 days: " + "; ".join("%s (%s)" % (e["title"], e["date"][5:]) for e in soon[:6]) + ".")
    return out

def market(stocks, events):
    with ThreadPoolExecutor(4) as ex:
        quotes = list(ex.map(index_quote, INDICES))
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
    write("market.json", clean({"quotes": quotes, "headlines": heads, "central_banks": cb, "breadth": br,
                                "drivers": [{"theme": t, "count": c, "headlines": sample.get(t, [])} for t, c in th[:6]],
                                "read": market_read(quotes, heads, br, events)}))

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
    stocks = [build_stock(t, charts.get(t["ticker"])) for t in TICKERS]
    events = calendar(stocks, charts)
    by = {}
    for e in events:
        if e["ticker"]: by.setdefault(e["ticker"], []).append(e)
    for s in stocks: s["events"] = [e for e in by.get(s["ticker"], []) if e["days"] >= 0][:4]
    write("stocks.json", clean({"updated": now_utc().isoformat(timespec="seconds"), "stocks": stocks}))
    market(stocks, events)
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
