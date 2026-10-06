"""Government bond yields (stdlib only).

Sources, all public and reachable from GitHub's servers:
  US Treasury   daily par yield curve and real yield curve (CSV)        2Y 5Y 10Y 30Y, 10Y real yield
  Bank of England  daily nominal par yields (IADB CSV, a few days lag)  UK 5Y 10Y
  ECB           euro-area AAA government yield curve (SDMX CSV)         2Y 10Y
  worldgovernmentbonds.com (through the Jina proxy)                     current 10Y for DE FR IT ES NL
For the last group there is no history to download, so day-to-day changes come from a snapshot this build
records once per day; they fill in after the first day.
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


# ----------------------------------------------------------------------------- statistics
def stats(series, label, name, source):
    """Level, changes in basis points, 1-year range and an 'extreme' note for one (date, yield) series."""
    if len(series) < 3: return None
    d, v = [x[0] for x in series], [x[1] for x in series]
    last = v[-1]
    chg = lambda n: (last - v[-1 - n]) * 100 if len(v) > n else None
    row = {"id": label, "name": name, "source": source, "asof": d[-1].isoformat(), "last": last, "chg_1d_bp": chg(1), "chg_1w_bp": chg(5),
           "chg_1m_bp": chg(21), "chg_3m_bp": chg(63), "spark": v[-60:]}
    yr = [x for dd, x in series if (d[-1] - dd).days <= 365]
    if len(yr) > 20: row["hi_1y"], row["lo_1y"] = max(yr), min(yr)
    row["extreme"] = extreme(series)
    return row

def human_span(days):
    return "%.0f years" % (days / 365.25) if days >= 700 else "%d months" % max(1, round(days / 30.4))

def extreme(series, min_span_days=60):
    """'highest close since <date> (N months ago)' when today's value is the extreme of at least 60 days; if nothing in the
    data window beats it, say so with the window length."""
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

def spread(a, b):
    """Spread a - b in basis points on shared dates: (latest, change 1m, date) or None."""
    da, db = dict(a), dict(b)
    days = sorted(set(da) & set(db))
    if len(days) < 3: return None
    s = [(d, (da[d] - db[d]) * 100) for d in days]
    return {"bp": s[-1][1], "chg_1m_bp": s[-1][1] - s[-22][1] if len(s) > 22 else None, "asof": days[-1].isoformat(), "series": s}


# ----------------------------------------------------------------------------- snapshot history for country yields
def snapshots(today, current):
    """Record today's country yields, return {code: [(date, y10)]} including earlier days."""
    path = os.path.join(lib.CACHE, "yield_history.json")
    try:
        with open(path) as fh: hist = json.load(fh)
    except Exception: hist = {}
    for code, y in current.items(): hist.setdefault(code, {})[today.isoformat()] = y
    for code in hist:
        for k in sorted(hist[code])[:-400]: hist[code].pop(k)
    try:
        with open(path, "w") as fh: json.dump(hist, fh)
    except Exception: pass
    return {c: sorted((dt.date.fromisoformat(k), v) for k, v in h.items()) for c, h in hist.items()}


# ----------------------------------------------------------------------------- assemble
def build(today):
    """Everything the page needs for the rates section, plus raw series for the macro sensitivity matrix."""
    since = today - dt.timedelta(days=500)
    rows, raw, notes = [], {}, []
    # US Treasury nominal and real
    nom = treasury("daily_treasury_yield_curve", (today.year - 1, today.year))
    for key, label in (("2 Yr", "US 2Y"), ("5 Yr", "US 5Y"), ("10 Yr", "US 10Y"), ("30 Yr", "US 30Y")):
        s = nom.get(key)
        r = s and stats(s, label, label, "US Treasury")
        if r: rows.append(r); raw[label] = s
    real = treasury("daily_treasury_real_yield_curve", (today.year - 1, today.year))
    r10 = real.get("10 YR") or real.get("10 Yr")
    if r10:
        r = stats(r10, "US 10Y real", "US 10Y real (TIPS)", "US Treasury")
        if r: rows.append(r); raw["US 10Y real"] = r10
        if nom.get("10 Yr"):
            be = spread(nom["10 Yr"], r10)
            if be: notes.append({"label": "US 10Y breakeven inflation", "value": be["bp"] / 100, "unit": "%", "chg_1m_bp": be["chg_1m_bp"], "asof": be["asof"]})
    # UK gilts
    b = boe(("IUDSNPY", "IUDMNPY"), since)
    for code, label in (("IUDSNPY", "UK 5Y"), ("IUDMNPY", "UK 10Y")):
        r = b.get(code) and stats(b[code], label, label + " gilt", "Bank of England")
        if r: rows.append(r); raw[label] = b[code]
    # Euro area AAA curve
    for tenor, label in (("2Y", "Euro AAA 2Y"), ("10Y", "Euro AAA 10Y")):
        s = ecb(tenor, since)
        r = s and stats(s, label, label.replace("Euro AAA", "Euro area AAA") + " (ECB)", "ECB")
        if r: rows.append(r); raw[label] = s
    # country 10-year yields (current value only, history built from daily snapshots)
    cur = {}
    for slug, code, _ in COUNTRIES:
        c = country(slug)
        if c: cur[code] = c
    hist = snapshots(today, {k: v["y10"] for k, v in cur.items()}) if cur else {}
    for slug, code, name in COUNTRIES:
        if code not in cur: continue
        h = hist.get(code, [])
        row = {"id": code + " 10Y", "name": name + " 10Y", "source": "worldgovernmentbonds.com", "asof": today.isoformat(), "last": cur[code]["y10"],
               "chg_1d_bp": None, "chg_1w_bp": None, "chg_1m_bp": None, "chg_3m_bp": None, "spark": [x[1] for x in h][-60:], "extreme": None,
               "policy": cur[code].get("policy"), "history_days": len(h)}
        prev = [x for x in h if x[0] < today]
        if prev: row["chg_1d_bp"] = (cur[code]["y10"] - prev[-1][1]) * 100
        old = [x for x in h if (today - x[0]).days >= 28]
        if old: row["chg_1m_bp"] = (cur[code]["y10"] - old[-1][1]) * 100
        rows.append(row)
    by = {r["id"]: r for r in rows}
    spreads = []
    def add(label, a, bb, note):
        if a in by and bb in by:
            spreads.append({"label": label, "bp": (by[a]["last"] - by[bb]["last"]) * 100, "note": note})
    add("US 10Y minus 2Y (curve)", "US 10Y", "US 2Y", "Negative means inverted: markets pricing cuts or a downturn")
    add("US 30Y minus 10Y", "US 30Y", "US 10Y", "Long-end term premium")
    add("UK 10Y minus 5Y", "UK 10Y", "UK 5Y", "Gilt curve slope")
    add("France minus Germany 10Y", "FR 10Y", "DE 10Y", "Political and fiscal stress gauge for France")
    add("Italy minus Germany 10Y", "IT 10Y", "DE 10Y", "Periphery stress gauge")
    add("Spain minus Germany 10Y", "ES 10Y", "DE 10Y", "Periphery stress gauge")
    add("Netherlands minus Germany 10Y", "NL 10Y", "DE 10Y", "Core spread; usually small")
    add("UK 10Y minus Germany 10Y", "UK 10Y", "DE 10Y", "Gilts versus Bunds")
    add("US 10Y minus Germany 10Y", "US 10Y", "DE 10Y", "Transatlantic yield gap; supports the dollar when wide")
    return {"rows": rows, "spreads": spreads, "inflation": notes}, raw
