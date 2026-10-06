"""Temporary probe 6: structure of analyst pages and coverage. Prints trimmed excerpts only."""
import re, urllib.request
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
def fetch(url, headers=None):
    h = {"User-Agent": UA}; h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r: return r.read().decode("utf-8", "replace")
    except Exception as e: print("FAIL", url, e); return None
def text(h): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()
for label, url in (("ASML ams", "https://stockanalysis.com/quote/ams/ASML/forecast/"), ("DIDIY otc", "https://stockanalysis.com/quote/otc/DIDIY/forecast/"), ("NFLX", "https://stockanalysis.com/stocks/nflx/forecast/")):
    html = fetch(url)
    if not html: continue
    print("=====", label, len(html))
    tables = re.findall(r"<table.*?</table>", html, re.S)
    print("tables:", len(tables))
    for i, t in enumerate(tables[:4]): print("  T%d: %s" % (i, text(t)[:700]))
    for kw in ("priceTarget", "targetPrice", "consensus", "recommendation"):
        i = html.find(kw)
        if i >= 0: print("  KW %s @%d: %s" % (kw, i, html[max(0, i - 150):i + 450].replace("\n", " ")))
        else: print("  KW %s: none" % kw)
    m = re.search(r"Based on (\d+) analyst", html)
    print("  based-on:", m and m.group(0))
html = fetch("https://finviz.com/quote.ashx?t=NFLX")
if html:
    rows = re.findall(r'<tr class="styled-row[^>]*>.*?</tr>', html, re.S)
    print("===== finviz rows", len(rows))
    for r in rows[:4]: print("  ", text(r)[:200])
    for kw in ("Target Price", "Recom"):
        i = html.find(">" + kw + "<")
        print("  ", kw, text(html[i:i + 700])[:120] if i >= 0 else "none")
H = {"Accept": "application/json"}
for sym in ("ASML", "BABA", "LYG", "GRAB", "SONO", "DIDIY"):
    for ep in ("targetprice", "ratings"):
        b = fetch("https://api.nasdaq.com/api/analyst/%s/%s" % (sym, ep), H)
        print("nasdaq %s %s: %s" % (sym, ep, (b or "FAIL")[:230].replace("\n", " ")))
b = fetch("https://api.nasdaq.com/api/analyst/NFLX/ratings", H)
print("NFLX ratings full:", (b or "")[:1500])
