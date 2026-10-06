"""Temporary probe 7: short position sources. Prints trimmed excerpts only."""
import re, sys, html, urllib.request, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
def fetch(url, headers=None, timeout=40):
    h = {"User-Agent": UA}; h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r: return r.read().decode("utf-8", "replace")
    except Exception as e: print("FAIL", url, e); return None
def text(h): return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)))).strip()
def links(h, pat): return sorted({m for m in re.findall(r'href="([^"]+)"', h) if re.search(pat, m, re.I)})[:25]
U = "https://finaristo.com/short-selling/netherlands/shorted-companies"
for label, url in (("finaristo direct", U), ("finaristo jina", "https://r.jina.ai/" + U)):
    h = fetch(url)
    if not h: continue
    t = text(h)
    print("=====", label, len(h), "text", len(t)); print("  HEAD:", t[:900])
    for kw in ("ASML", "Adyen", "Sligro", "Basic", "Magnum"):
        i = t.find(kw); print("  %s: %s" % (kw, t[max(0, i - 100):i + 260] if i >= 0 else "none"))
    print("  LINKS:", links(h, r"netherlands|company|short|asml|adyen")[:25])
    tbl = re.findall(r"<table.*?</table>", h, re.S); print("  tables:", len(tbl))
    if tbl: print("  T0:", text(tbl[0])[:800])
    break
print("robots:", (fetch("https://finaristo.com/robots.txt") or "")[:500].replace("\n", " | "))
h = fetch("https://www.hkex.com.hk/eng/stat/smstat/ssturnover/ncms/mshtmain.htm")
if h: print("===== HKEX", len(h)); print("  TEXT:", text(h)[:700]); print("  LINKS:", links(h, r"short|ssturn|csv|xls|\.htm"))
for u in ("https://www.sfc.hk/en/Regulatory-functions/Market/Short-position-reporting/Aggregated-reportable-short-positions-of-specified-shares",
          "https://www.sfc.hk/en/Regulatory-functions/Market/Short-position-reporting"):
    h = fetch(u)
    if h: print("===== SFC", u[-60:], len(h)); print("  LINKS:", links(h, r"csv|xls|short-position|aggregated")); print("  TEXT:", text(h)[:300])
h = fetch("https://www.afm.nl/en/sector/registers/meldingenregisters/netto-shortposities-actueel")
if h: print("===== AFM", len(h)); print("  LINKS:", links(h, r"csv|xls|export|json|api|short"))
import flow, lib
blob = lib.cached_bytes("https://www.fca.org.uk/publication/data/short-positions-daily-update.xlsx", 3600)
if blob:
    rows = flow.read_xlsx(blob); print("===== FCA rows", len(rows))
    for r in rows[:4]: print("  ", r)
    for r in [r for r in rows if "lloyds" in " ".join(str(v) for v in r.values()).lower()][:12]: print("  LLOYDS:", r)
