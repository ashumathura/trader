"""Temporary connectivity probe, run from GitHub Actions. Prints status codes and sizes only, never the key."""
import os, urllib.request, urllib.parse, json, time, sys
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
KEY = os.environ.get("AV_KEY", "").strip()
print("AV_KEY set:", bool(KEY), "length:", len(KEY))
def get(label, url, n=300, headers=None):
    h = {"User-Agent": UA}; h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
        print("OK  ", label, r.status, len(body), "|", body[:n].replace("\n", " ").replace(KEY, "***") if KEY else body[:n].replace("\n", " "))
        return body
    except Exception as e:
        print("FAIL", label, e); return None
y = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=5d&interval=1d"
for s in ("ASML.AS", "NFLX", "1211.HK", "LLOY.L"): get("yahoo " + s, y % s, 80)
get("yahoo query2 ASML.AS", y.replace("query1", "query2") % "ASML.AS", 80)
for s in ("asml.nl", "nflx.us", "lloy.uk", "1211.hk", "9988.hk", "^spx", "^hsi", "^aex"): get("stooq " + s, "https://stooq.com/q/d/l/?s=%s&i=d" % urllib.parse.quote(s), 120)
for s in ("ASML.AMS", "ADYEN.AMS", "SLIGR.AMS", "LLOY.LON", "9988.HKG", "1211.HKG", "NFLX", "GRAB", "SONO", "DIDIY"):
    b = get("AV daily full " + s, "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&outputsize=full&symbol=%s&apikey=%s" % (s, KEY), 160)
    if b:
        try:
            ts = json.loads(b).get("Time Series (Daily)"); print("     bars:", len(ts) if ts else 0)
        except Exception: pass
    time.sleep(1.2)
get("AV earnings calendar", "https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&horizon=6month&apikey=" + KEY, 200)
for label, u in (("gnews", "https://news.google.com/rss/search?q=ASML+stock&hl=en-US&gl=US&ceid=US:en"),
                 ("cnbc", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
                 ("marketwatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
                 ("investing", "https://www.investing.com/rss/news_25.rss"),
                 ("yahoo news", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NFLX&region=US&lang=en-US"),
                 ("ecb rss", "https://www.ecb.europa.eu/rss/press.html"),
                 ("fed rss", "https://www.federalreserve.gov/feeds/press_all.xml")): get(label, u, 100)
