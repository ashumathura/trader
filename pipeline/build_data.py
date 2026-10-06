#!/usr/bin/env python3
"""
Builds the PUBLIC data layer: docs/data/*.json
Input : watchlist.json (tickers only)
Output: technicals, news, market, calendar, prices, meta  (market data only, nothing personal)

Run locally:  python3 pipeline/build_data.py
In CI:        .github/workflows/update-data.yml (set secret AV_KEY for Alpha Vantage earnings dates, optional)
"""
import csv, io, json, os, statistics, sys, time
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
from lib import yahoo_chart, compute_tech, stock_news, themes_of, parse_rss, cached_get, MARKET_FEEDS, INDICES, STATUS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data")
os.makedirs(OUT, exist_ok=True)
WL = json.load(open(os.path.join(ROOT, "watchlist.json")))
TICKERS = WL["tickers"]

def write(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), default=str)
    print("wrote", name, os.path.getsize(os.path.join(OUT, name)), "bytes")

def charts():
    syms = [t["yahoo"] for t in TICKERS]
    with ThreadPoolExecutor(6) as ex:
        return dict(zip(syms, ex.map(lambda s: yahoo_chart(s), syms)))

def technicals(data):
    items = []
    for t in TICKERS:
        d = data.get(t["yahoo"])
        items.append({"ticker": t["ticker"], "yahoo": t["yahoo"], "tech": compute_tech(d["bars"]) if d else None})
    write("technicals.json", {"items": items})

def prices(data):
    out = {}
    for t in TICKERS:
        d = data.get(t["yahoo"])
        if d:
            b = d["bars"]; k = max(0, len(b["c"]) - 130)
            out[t["ticker"]] = {"t": b["t"][k:], "c": [round(x, 4) for x in b["c"][k:]]}
    write("prices.json", out)

def news(data):
    with ThreadPoolExecutor(6) as ex:
        feeds = list(ex.map(lambda t: stock_news(t["yahoo"]), TICKERS))
    stocks = []
    for t, items in zip(TICKERS, feeds):
        d = data.get(t["yahoo"]); c = d["bars"]["c"] if d else []
        move = (c[-1] / c[-2] - 1) * 100 if len(c) > 1 else 0
        th = themes_of([i["title"] for i in items])
        pos = sum(i["tone"] == "pos" for i in items); neg = sum(i["tone"] == "neg" for i in items)
        mood = "Positive" if pos > neg + 1 else "Negative" if neg > pos + 1 else "Mixed"
        name = t["ticker"].split(":")[-1]
        summary = ("%s is %s %.2f%% on the last session. Coverage is %s (%d positive, %d negative of %d headlines). Main themes: %s." % (
            name, "up" if move >= 0 else "down", abs(move), mood.lower(), pos, neg, len(items),
            ", ".join(x for x, _ in th[:3]) or "no dominant theme")) if items else "No headlines available right now."
        stocks.append({"ticker": t["ticker"], "yahoo": t["yahoo"], "move_pct": move, "mood": mood,
                       "themes": th[:5], "summary": summary, "items": items})
    write("news.json", {"stocks": stocks})

def market():
    def quote(item):
        sym, name = item
        d = yahoo_chart(sym, "1mo", "1d", ttl=600)
        if not d or len(d["bars"]["c"]) < 2: return {"symbol": sym, "name": name, "price": None}
        c = d["bars"]["c"]
        return {"symbol": sym, "name": name, "price": c[-1], "chg_pct": (c[-1] / c[-2] - 1) * 100, "spark": c[-22:]}
    with ThreadPoolExecutor(6) as ex:
        quotes = list(ex.map(quote, INDICES))
    heads = []
    for name, url in MARKET_FEEDS:
        txt = cached_get(url, 900, "market_news")
        for i in (parse_rss(txt, 12) if txt else []):
            i["source"] = i["source"] or name; heads.append(i)
    heads.sort(key=lambda i: i["time"], reverse=True)
    th = themes_of([h["title"] for h in heads]); sample = {}
    for h in heads:
        tl = " " + h["title"].lower() + " "
        for t, kws in lib.THEMES.items():
            if any(k in tl for k in kws) and len(sample.setdefault(t, [])) < 3: sample[t].append(h)
    write("market.json", {"quotes": quotes, "headlines": heads[:40],
                          "drivers": [{"theme": t, "count": c, "headlines": sample.get(t, [])} for t, c in th[:6]]})

def calendar(data):
    today = dt.date.today(); ev = []
    # 1) dividends: estimated from each stock's own history
    for t in TICKERS:
        d = data.get(t["yahoo"]); dv = d["divs"] if d else []
        if len(dv) < 2: continue
        dates = [dt.datetime.utcfromtimestamp(x[0]).date() for x in dv]
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        gap = statistics.median(gaps[-4:])
        if not 20 <= gap <= 400: continue
        nxt = dates[-1] + dt.timedelta(days=int(gap))
        while nxt < today: nxt += dt.timedelta(days=int(gap))
        ev.append({"date": nxt.isoformat(), "kind": "dividend", "ticker": t["ticker"], "approx": True,
                   "title": "%s ex-dividend (est.)" % t["ticker"].split(":")[-1],
                   "detail": "Last %s: %.4g, cadence about every %d days" % (dates[-1].isoformat(), dv[-1][1], gap)})
    # 2) earnings: manual overrides first, then Alpha Vantage calendar (optional, one call per day)
    have = set()
    for tk, date in WL.get("earnings_overrides", {}).items():
        ev.append({"date": date, "kind": "earnings", "ticker": tk, "approx": False, "title": "%s earnings" % tk.split(":")[-1], "detail": "manual"}); have.add(tk)
    key = os.environ.get("AV_KEY", "").strip()
    if key:
        txt = cached_get("https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&horizon=6month&apikey=" + key, 20 * 3600, "alphavantage")
        if txt and txt.startswith("symbol"):
            want = {t["yahoo"].split(".")[0].upper(): t for t in TICKERS}
            for r in csv.DictReader(io.StringIO(txt)):
                t = want.get((r.get("symbol") or "").upper())
                if t and t["ticker"] not in have:
                    have.add(t["ticker"])
                    ev.append({"date": r["reportDate"], "kind": "earnings", "ticker": t["ticker"], "approx": False,
                               "title": "%s earnings" % t["ticker"].split(":")[-1], "detail": "Alpha Vantage, EPS est. %s" % (r.get("estimate") or "n/a")})
    # 3) macro
    for e in WL.get("macro_events", []):
        ev.append({"date": e["date"], "kind": "macro", "ticker": None, "approx": False, "title": e["title"], "detail": e.get("detail", "")})
    ev = [e for e in ev if dt.date.fromisoformat(e["date"]) >= today - dt.timedelta(days=7)]
    ev.sort(key=lambda e: e["date"])
    write("calendar.json", {"events": ev, "today": today.isoformat()})

if __name__ == "__main__":
    data = charts()
    technicals(data); prices(data); news(data); market(); calendar(data)
    ok = sum(1 for d in data.values() if d)
    write("meta.json", {"updated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "tickers_ok": ok,
                        "tickers_total": len(TICKERS), "status": dict(STATUS)})
    if ok == 0:
        print("WARNING: no price data fetched (Yahoo may be blocking this network)"); sys.exit(1)
