"""Temporary probe 5: analyst ratings and price target sources. Prints status, sizes and small excerpts only."""
import os, re, urllib.request, urllib.parse, json
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
KEY = os.environ.get("AV_KEY", "")
def get(label, url, n=300, grep=None, headers=None, timeout=40):
    h = {"User-Agent": UA}; h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        print("OK  ", label, r.status, len(body))
        if grep:
            hits = [l.strip()[:260] for l in body.splitlines() if re.search(grep, l, re.I)][:8]
            for l in hits: print("      >", l)
            if not hits: print("      (no match)", body[:150].replace("\n", " "))
        else: print("    ", body[:n].replace("\n", " // "))
        return body
    except Exception as e:
        print("FAIL", label, str(e).replace(KEY, "***") if KEY else e); return None
H = {"Accept": "application/json"}
for ep in ("ratings", "targetprice", "earnings-forecast", "estimates"):
    get("nasdaq analyst " + ep, "https://api.nasdaq.com/api/analyst/NFLX/" + ep, 700, headers=H)
for sym in ("NFLX", "ASML.AMS"):
    b = get("AV OVERVIEW " + sym, "https://www.alphavantage.co/query?function=OVERVIEW&symbol=%s&apikey=%s" % (sym, KEY), 0, grep=r"Analyst|MarketCapitalization|PERatio|Name\"")
for label, u in (("stockanalysis NFLX direct", "https://stockanalysis.com/stocks/nflx/forecast/"),
                 ("stockanalysis ASML ams direct", "https://stockanalysis.com/quote/ams/ASML/forecast/"),
                 ("stockanalysis LLOY lon direct", "https://stockanalysis.com/quote/lon/LLOY/forecast/"),
                 ("stockanalysis 9988 hkg direct", "https://stockanalysis.com/quote/hkg/9988/forecast/")):
    get(label, u, grep=r"price target|analyst|consensus|upside")
for label, u in (("stockanalysis NFLX jina", "https://r.jina.ai/https://stockanalysis.com/stocks/nflx/forecast/"),
                 ("stockanalysis ASML jina", "https://r.jina.ai/https://stockanalysis.com/quote/ams/ASML/forecast/")):
    get(label, u, grep=r"price target|consensus|upgrade|downgrade|maintains|reiterate|raises|lowers|\| *20\d\d")
get("finviz NFLX direct", "https://finviz.com/quote.ashx?t=NFLX", grep=r"Target Price|Recom|Upgrade|Downgrade")
get("finviz NFLX jina", "https://r.jina.ai/https://finviz.com/quote.ashx?t=NFLX", grep=r"Target Price|Recom|Upgrade|Downgrade")
get("yahoo quoteSummary via jina", "https://r.jina.ai/https://query1.finance.yahoo.com/v10/finance/quoteSummary/NFLX?modules=financialData,recommendationTrend,upgradeDowngradeHistory", 220)
b = get("google news analyst ASML", "https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en" % urllib.parse.quote("ASML analyst price target upgrade OR downgrade OR raises OR lowers when:30d"), 0, grep=r"<title>")
b = get("google news analyst Adyen", "https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en" % urllib.parse.quote("Adyen analyst price target upgrade OR downgrade OR raises OR lowers when:30d"), 0, grep=r"<title>")
