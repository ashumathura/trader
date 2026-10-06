"""Government bond yields (stdlib only).

Sources, all public and reachable from GitHub's servers:
  US Treasury   daily par yield curve and real yield curve (CSV)        2Y 5Y 10Y 30Y, 10Y real yield
  Bank of England  daily nominal par yields (IADB CSV, a few days lag)  UK 5Y 10Y
  ECB           euro-area AAA government yield curve (SDMX CSV)         2Y 10Y
  worldgovernmentbonds.com (through the Jina proxy)                     current 10Y for DE FR IT ES NL
For the last group there is no previous close to download, so the change versus yesterday comes from the last snapshot this build
recorded on the previous day; it appears from the second day onwards.
"""
import csv
import datetime as dt
import io
import json
import os
import re

import lib

TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/%d/all"
            "?type=%s&field_tdr_date_value=%d&page&_format=csv")
BOE = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=%s&Dateto=now"
       "&SeriesCodes=%s&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
ECB = "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_%s?startPeriod=%s&format=csvdata"
WGB = "https://r.jina.ai/https://www.worldgovernmentbonds.com/country/%s/"
COUNTRIES = [("germany", "DE", "Germany"), ("france", "FR", "France"), ("italy", "IT", "Italy"), ("spain", "ES", "Spain"), ("netherlands", "NL", "Netherlands")]


def parse_treasury(txt):
    """Treasury CSV -> {column label: [(date, value), ...]} oldest first."""
    out = {}
    rd = csv.reader(io.StringIO(txt.lstrip("﻿")))
    head = next(rd, None)
    if not head: return out
    for row in rd:
        if len(row) < 2: continue
        try: d = dt.datetime.strptime(row[0], "%m/%d/%Y").date()
        except ValueError: continue
        for h, v in zip(head[1:], row[1:]):
            try: x = float(v)
            except ValueError: continue
            out.setdefault(h.strip(), []).append((d, x))
    return {k: sorted(v) for k, v in out.items()}

def treasury(kind, years):
    """Daily curve for each calendar year in `years` (finished years never change, so they are cached for a month)."""
    series = {}
    this_year = dt.date.today().year
    for y in years:
        ttl = 6 * 3600 if y >= this_year else 30 * 86400
        txt = lib.cached_get(TREASURY % (y, kind, y), ttl, "treasury", validate=lambda t: t.lstrip("\ufeff").startswith("Date"))
        if not txt: continue
        for k, v in parse_treasury(txt).items(): series.setdefault(k, []).extend(v)
    return {k: sorted(set(v)) for k, v in series.items()}

def parse_boe(txt):
    """BoE CSV 'DATE,CODE1,CODE2' with dates like '02 Oct 2026' -> {code: [(date, value)]}."""
    out = {}
    rd = csv.reader(io.StringIO(txt))
    head = next(rd, None)
    if not head: return out
    for row in rd:
        if len(row) < 2: continue
        try: d = dt.datetime.strptime(row[0].strip(), "%d %b %Y").date()
        except ValueError: continue
        for h, v in zip(head[1:], row[1:]):
            try: x = float(v)
            except ValueError: continue
            out.setdefault(h.strip(), []).append((d, x))
    return {k: sorted(v) for k, v in out.items()}

def boe(codes, since):
    txt = lib.cached_get(BOE % (since.strftime("%d/%b/%Y"), ",".join(codes)), 6 * 3600, "boe", validate=lambda t: t.startswith("DATE"))
    return parse_boe(txt) if txt else {}

def parse_ecb(txt):
    rd = csv.DictReader(io.StringIO(txt))
    out = []
    for r in rd:
        try: out.append((dt.date.fromisoformat(r["TIME_PERIOD"]), float(r["OBS_VALUE"])))
        except (KeyError, ValueError): continue
    return sorted(out)

def ecb(tenor, since):
    txt = lib.cached_get(ECB % (tenor, since.isoformat()), 6 * 3600, "ecb", validate=lambda t: "TIME_PERIOD" in t)
    return parse_ecb(txt) if txt else []

def parse_wgb(txt):
    """Current 10Y yield and 2Y spread from the page text, or None."""
    m = re.search(r"10-Year Gov\.?\s*Bond Yield:\s*(-?\d+(?:\.\d+)?)\s*%", txt)
    if not m: return None
    s = re.search(r"Spread vs 2-Year Bond:\s*(-?\d+(?:\.\d+)?)\s*bp", txt)
    cb = re.search(r"Central Bank Rate:\s*(-?\d+(?:\.\d+)?)\s*%", txt)
    return {"y10": float(m.group(1)), "spread_2y_bp": float(s.group(1)) if s else None, "policy": float(cb.group(1)) if cb else None}

def country(slug):
    txt = lib.cached_get(WGB % slug, 1800, "wgb", validate=lambda t: bool(parse_wgb(t)), timeout=45, headers={"x-no-cache": "true", "Accept": "text/plain"})
    return parse_wgb(txt) if txt else None


# ----------------------------------------------------------------------------- rows
def level(series, label, name, source):
    """Latest yield, previous close and the change in basis points for one (date, yield) series."""
    if len(series) < 2: return None
    d, v = [x[0] for x in series], [x[1] for x in series]
    return {"id": label, "name": name, "source": source, "asof": d[-1].isoformat(), "last": v[-1], "prev": v[-2], "chg_1d_bp": (v[-1] - v[-2]) * 100}

def extreme(series, min_span_days=60):
    """'highest close since <date> (N months ago)' when today's value is the extreme of at least 60 days; if nothing in the
    data window beats it, say so with the window length. Used for market tiles, not for yields."""
    d, v = [x[0] for x in series], [x[1] for x in series]
    last = v[-1]
    for kind, test in (("highest", lambda x: x >= last), ("lowest", lambda x: x <= last)):
        j = None
        for i in range(len(v) - 2, -1, -1):
            if test(v[i]): j = i; break
        span = (d[-1] - (d[j] if j is not None else d[0])).days
        if span >= min_span_days:
            if j is None: return "%s close in at least %s" % (kind, human_span(span))
            return "%s close since %s (%s ago)" % (kind, d[j].isoformat(), human_span(span))
    return None

def human_span(days):
    return "%.0f years" % (days / 365.25) if days >= 700 else "%d months" % max(1, round(days / 30.4))

def spread_row(rows, label, a, b, note):
    """a minus b in basis points, and how that spread moved since yesterday (difference of the two legs' changes)."""
    ra, rb = rows.get(a), rows.get(b)
    if not ra or not rb: return None
    chg = ra["chg_1d_bp"] - rb["chg_1d_bp"] if ra.get("chg_1d_bp") is not None and rb.get("chg_1d_bp") is not None else None
    return {"label": label, "bp": (ra["last"] - rb["last"]) * 100, "chg_1d_bp": chg, "note": note}


# ----------------------------------------------------------------------------- snapshots for country yields
def snapshots(today, current):
    """Record today's country yields (the last build of each day wins), return {code: [(date, y10)]} including earlier days."""
    path = os.path.join(lib.CACHE, "yield_history.json")
    try:
        with open(path) as fh: hist = json.load(fh)
    except Exception: hist = {}
    for code, y in current.items(): hist.setdefault(code, {})[today.isoformat()] = y
    for code in hist:
        for k in sorted(hist[code])[:-30]: hist[code].pop(k)
    try:
        with open(path, "w") as fh: json.dump(hist, fh)
    except Exception: pass
    return {c: sorted((dt.date.fromisoformat(k), v) for k, v in h.items()) for c, h in hist.items()}


# ----------------------------------------------------------------------------- assemble
def build(today):
    """Rows (level and change vs yesterday), key spreads and breakeven inflation, plus the raw series that feed the
    stock-sensitivity matrix (not shown as history)."""
    since = today - dt.timedelta(days=500)
    rows, raw = [], {}
    nom = treasury("daily_treasury_yield_curve", (today.year - 1, today.year))
    for key, label in (("2 Yr", "US 2Y"), ("5 Yr", "US 5Y"), ("10 Yr", "US 10Y"), ("30 Yr", "US 30Y")):
        s = nom.get(key); r = s and level(s, label, label, "US Treasury")
        if r: rows.append(r); raw[label] = s
    real = treasury("daily_treasury_real_yield_curve", (today.year - 1, today.year))
    r10 = real.get("10 YR") or real.get("10 Yr")
    inflation = []
    if r10:
        r = level(r10, "US 10Y real", "US 10Y real (TIPS)", "US Treasury")
        if r: rows.append(r); raw["US 10Y real"] = r10
        if nom.get("10 Yr"):
            dn, dr = dict(nom["10 Yr"]), dict(r10)
            days = sorted(set(dn) & set(dr))
            if len(days) >= 2:
                now_, prev = (dn[days[-1]] - dr[days[-1]]), (dn[days[-2]] - dr[days[-2]])
                inflation.append({"label": "US 10Y breakeven inflation", "value": now_, "chg_1d_bp": (now_ - prev) * 100, "asof": days[-1].isoformat()})
    b = boe(("IUDSNPY", "IUDMNPY"), since)
    for code, label in (("IUDSNPY", "UK 5Y"), ("IUDMNPY", "UK 10Y")):
        r = b.get(code) and level(b[code], label, label + " gilt", "Bank of England")
        if r: rows.append(r); raw[label] = b[code]
    for tenor, label in (("2Y", "Euro AAA 2Y"), ("10Y", "Euro AAA 10Y")):
        s = ecb(tenor, since)
        r = s and level(s, label, label.replace("Euro AAA", "Euro area AAA") + " (ECB)", "ECB")
        if r: rows.append(r); raw[label] = s
    cur = {}
    for slug, code, _ in COUNTRIES:
        c = country(slug)
        if c: cur[code] = c
    hist = snapshots(today, {k: v["y10"] for k, v in cur.items()}) if cur else {}
    for slug, code, name in COUNTRIES:
        if code not in cur: continue
        prev = [x for x in hist.get(code, []) if x[0] < today]
        rows.append({"id": code + " 10Y", "name": name + " 10Y", "source": "worldgovernmentbonds.com", "asof": today.isoformat(), "last": cur[code]["y10"],
                     "prev": prev[-1][1] if prev else None, "chg_1d_bp": (cur[code]["y10"] - prev[-1][1]) * 100 if prev else None})
    by = {r["id"]: r for r in rows}
    spreads = [x for x in (
        spread_row(by, "US 10Y minus 2Y (curve)", "US 10Y", "US 2Y", "Negative means inverted: markets pricing cuts or a downturn"),
        spread_row(by, "France minus Germany 10Y", "FR 10Y", "DE 10Y", "Political and fiscal stress gauge for France"),
        spread_row(by, "Italy minus Germany 10Y", "IT 10Y", "DE 10Y", "Periphery stress gauge"),
        spread_row(by, "Spain minus Germany 10Y", "ES 10Y", "DE 10Y", "Periphery stress gauge"),
        spread_row(by, "Netherlands minus Germany 10Y", "NL 10Y", "DE 10Y", "Core spread; usually small"),
        spread_row(by, "UK 10Y minus Germany 10Y", "UK 10Y", "DE 10Y", "Gilts versus Bunds"),
        spread_row(by, "US 10Y minus Germany 10Y", "US 10Y", "DE 10Y", "Transatlantic yield gap; supports the dollar when wide"),
    ) if x]
    return {"rows": rows, "spreads": spreads, "inflation": inflation}, raw
