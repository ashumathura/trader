"""Temporary probe 8: AFM short register export."""
import re, sys, os, urllib.request
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
def fetch(url, timeout=60):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), r.headers.get("Content-Type")
    except Exception as e: print("FAIL", url, e); return None, None
B = "https://www.afm.nl/export.aspx?type=%s&format=%s"
for t in ("8a46a4ef-f196-4467-a7ab-1ae1cb58f0e7",):
    for f in ("csv", "xml"):
        h, ct = fetch(B % (t, f))
        if not h: continue
        print("=====", f, ct, len(h)); print(h[:900].replace("\n", " | "))
        for kw in ("ASML", "Adyen", "Sligro", "Magnum", "Basic-Fit", "Basic Fit", "Lloyds"):
            idx = [m.start() for m in re.finditer(kw, h, re.I)][:2]
            for i in idx: print("  ", kw, ":", h[max(0, i - 150):i + 250].replace("\n", " | "))
            if not idx: print("  ", kw, ": none")
h, ct = fetch("https://www.afm.nl/en/sector/registers/meldingenregisters/netto-shortposities-historie")
if h: print("HIST page", len(h)); print(sorted({m for m in re.findall(r'href="([^"]*export[^"]*)"', h)}))
