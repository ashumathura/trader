"""Temporary connectivity probe 2. Prints status codes and sizes only, never the key."""
import os, urllib.request, urllib.parse, json, time
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
KEY = os.environ.get("AV_KEY", "").strip()
def get(label, url, n=200, headers=None):
    h = {"User-Agent": UA}; h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
        print("OK  ", label, r.status, len(body), "|", body[:n].replace("\n", " ").replace(KEY, "***"))
        return body
    except Exception as e:
        print("FAIL", label, str(e).replace(KEY, "***")); return None
av = "https://www.alphavantage.co/query?function=%s&symbol=%s&apikey=" + KEY
def bars(label, fn, sym, extra=""):
    b = get(label, (av % (fn, sym)) + extra, 150)
    if b:
        try:
            j = json.loads(b); k = [x for x in j if "Time Series" in x]
            ts = j[k[0]] if k else {}; ds = sorted(ts)
            print("     bars:", len(ts), ds[:1], ds[-1:], list(ts[ds[-1]].items())[:5] if ds else "")
        except Exception as e: print("     parse", e)
    time.sleep(1.5)
for s in ("ASML.AMS", "LLOY.LON", "9988.HKG", "NFLX", "DIDIY"): bars("AV daily compact " + s, "TIME_SERIES_DAILY", s, "&outputsize=compact")
for s in ("ASML.AMS", "9988.HKG"): bars("AV weekly " + s, "TIME_SERIES_WEEKLY", s)
# keyless alternatives
y = "https://query1.finance.yahoo.com/v8/finance/chart/NFLX?range=2y&interval=1d"
get("jina->yahoo", "https://r.jina.ai/" + y, 200)
get("allorigins->yahoo", "https://api.allorigins.win/raw?url=" + urllib.parse.quote(y), 200)
get("yahoo cookie fc", "https://fc.yahoo.com", 60)
get("google finance page", "https://www.google.com/finance/quote/NFLX:NASDAQ?hl=en", 100)
get("nasdaq api hist", "https://api.nasdaq.com/api/quote/NFLX/historical?assetclass=stocks&fromdate=2026-09-01&limit=5&todate=2026-10-05", 200, {"Accept": "application/json"})
get("stooq with ua+accept", "https://stooq.com/q/d/l/?s=nflx.us&i=d", 300, {"Accept": "text/csv"})
get("hkex? sina hk", "https://hq.sinajs.cn/list=rt_hk09988", 100, {"Referer": "https://finance.sina.com.cn"})
get("euronext live", "https://live.euronext.com/en/product/equities/NL0010273215-XAMS", 60)
get("google news ASML", "https://news.google.com/rss/search?q=ASML+when:2d&hl=en-US&gl=US&ceid=US:en", 100)
