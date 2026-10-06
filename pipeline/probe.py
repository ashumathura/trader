"""Temporary probe 3: money-flow and options data sources. Prints status/sizes only."""
import datetime as dt, json, urllib.request
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
def get(label, url, n=220, headers=None):
    h = {"User-Agent": UA}; h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
        print("OK  ", label, r.status, len(body), "|", body[:n].replace("\n", " ")); return body
    except Exception as e:
        print("FAIL", label, e); return None
for s in ("NFLX", "ASML", "BABA", "LYG", "GRAB", "SONO"):
    b = get("cboe options " + s, "https://cdn.cboe.com/api/global/delayed_quotes/options/%s.json" % s, 160)
    if b:
        try:
            d = json.loads(b)["data"]; o = d["options"]
            print("     keys", list(d.keys())[:14], "n_options", len(o), "sample", json.dumps(o[0])[:420])
        except Exception as e: print("     parse", e)
get("cboe quote NFLX", "https://cdn.cboe.com/api/global/delayed_quotes/quotes/NFLX.json", 200)
d = dt.date.today()
found = 0
for i in range(1, 9):
    x = d - dt.timedelta(days=i)
    if x.weekday() >= 5: continue
    b = get("finra regsho CNMS " + x.isoformat(), "https://cdn.finra.org/equity/regsho/daily/CNMSshvol%s.txt" % x.strftime("%Y%m%d"), 120)
    if b and found == 0:
        for line in b.splitlines():
            if line.split("|")[1:2] and line.split("|")[1] in ("NFLX", "GRAB", "SONO", "ASML", "DIDIY"): print("     ", line)
        found = 1
get("finra regsho OTC FORF", "https://cdn.finra.org/equity/regsho/daily/FORFshvol%s.txt" % (d - dt.timedelta(days=1 if d.weekday() not in (0, 6) else 3)).strftime("%Y%m%d"), 120)
get("nasdaq short interest NFLX", "https://api.nasdaq.com/api/quote/NFLX/short-interest?assetClass=stocks", 500, {"Accept": "application/json"})
get("nasdaq short interest DIDIY", "https://api.nasdaq.com/api/quote/DIDIY/short-interest?assetClass=stocks", 300, {"Accept": "application/json"})
get("nasdaq info NFLX", "https://api.nasdaq.com/api/quote/NFLX/summary?assetclass=stocks", 600, {"Accept": "application/json"})
get("AFM shorts", "https://www.afm.nl/en/sector/registers/meldingenregisters/netto-shortposities-actueel", 150)
get("AFM csv", "https://www.afm.nl/export.aspx?type=0ee836dc-5520-459c-9f09-d8d3d5a8b5dd&format=csv", 150)
get("FCA shorts xlsx", "https://www.fca.org.uk/publication/data/short-positions-daily-update.xlsx", 60)
get("HKEX short turnover", "https://www.hkex.com.hk/eng/stat/smstat/ssturnover/ncms/mshtmain.htm", 100)
get("yahoo options via jina", "https://r.jina.ai/https://query2.finance.yahoo.com/v7/finance/options/NFLX", 200)
