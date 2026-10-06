"""Short positions and short-selling activity per stock (stdlib only).

  Netherlands  AFM register of net short positions (>= 0.5% of issued shares), CSV export, refreshed each business day
  UK           FCA register of net short positions (>= 0.5%)
  US           FINRA daily short-sale volume ratio (share of the day's volume sold short) and Nasdaq short interest
  Hong Kong    no machine-readable daily source is available, so nothing is shown (the card says so)
Positions are only public above 0.5%, so "none disclosed" means no holder is above that line, not that nobody is short.
Day-on-day change for the registers comes from a snapshot recorded by each build (first change appears on the second day).
"""
import csv
import datetime as dt
import io
import json
import os

import lib

AFM_URL = "https://www.afm.nl/export.aspx?type=8a46a4ef-f196-4467-a7ab-1ae1cb58f0e7&format=csv"


def parse_afm(txt):
    """AFM CSV (semicolon separated: holder, issuer, ISIN, net short %, position date) -> list of dicts."""
    out = []
    for r in csv.reader(io.StringIO(txt.lstrip("﻿")), delimiter=";"):
        if len(r) < 5: continue
        try: pct = float(r[3].replace(",", "."))
        except ValueError: continue
        out.append({"holder": r[0].strip(), "issuer": r[1].strip(), "isin": r[2].strip(), "pct": pct, "date": r[4].strip()[:10]})
    return out

def afm_rows():
    txt = lib.cached_get(AFM_URL, 3 * 3600, "afm", validate=lambda t: "Netto" in t[:300])
    return parse_afm(txt) if txt else None

def register(rows, match, scale=1.0):
    """Summary of the disclosed positions in one issuer: total, holder count, largest holders and who reported most recently."""
    mine = [r for r in rows if match.lower() in r["issuer"].lower() and r["pct"] > 0]
    mine.sort(key=lambda r: -r["pct"])
    last = max((r["date"] for r in mine), default=None)
    return {"holders": len(mine), "total_pct": sum(r["pct"] for r in mine) * scale, "date": last,
            "top": [{"holder": r["holder"], "pct": r["pct"] * scale, "date": r["date"]} for r in mine[:4]]}


# ----------------------------------------------------------------------------- day-on-day snapshots
def _path(): return os.path.join(lib.CACHE, "shorts_history.json")

def snapshot(key, today, value):
    """Store today's value for `key` (last build of the day wins) and return the most recent value from an earlier day, or None."""
    try:
        with open(_path()) as fh: hist = json.load(fh)
    except Exception: hist = {}
    h = hist.setdefault(key, {}); h[today.isoformat()] = value
    prev = [(d, v) for d, v in sorted(h.items()) if d < today.isoformat()]
    for d in sorted(h)[:-30]: h.pop(d)
    try:
        os.makedirs(lib.CACHE, exist_ok=True)
        with open(_path(), "w") as fh: json.dump(hist, fh)
    except Exception: pass
    return prev[-1] if prev else None


def movement(total, prev_total, holders, prev_holders):
    """(tone, sentence) describing the change in disclosed short interest since the previous snapshot."""
    if prev_total is None: return "mid", "Day-on-day change starts once the site has recorded two days."
    d = total - prev_total
    if abs(d) < 0.005 and holders == prev_holders: return "mid", "Unchanged since the previous day."
    if d > 0: return "dn", "Disclosed short positions rose by %.2f points since the previous day." % d
    if d < 0: return "up", "Disclosed short positions fell by %.2f points since the previous day (covering)." % -d
    return "mid", "The number of disclosed holders changed; the total did not."


def register_block(region, source, summary, key, today, scope):
    prev = snapshot(key, today, {"total": summary["total_pct"], "holders": summary["holders"]})
    pv = prev[1] if prev else {}
    tone, text = movement(summary["total_pct"], pv.get("total"), summary["holders"], pv.get("holders"))
    level = "No holder is above the 0.5% disclosure line." if not summary["holders"] else \
        "%d holder(s) disclose %.2f%% in total; largest %s at %.2f%%." % (summary["holders"], summary["total_pct"], summary["top"][0]["holder"], summary["top"][0]["pct"])
    return {"kind": "register", "region": region, "source": source, "scope": scope, "asof": summary["date"],
            "total_pct": summary["total_pct"], "holders": summary["holders"], "top": summary["top"],
            "prev_pct": pv.get("total"), "prev_date": prev[0] if prev else None,
            "chg_pct": (summary["total_pct"] - pv["total"]) if pv.get("total") is not None else None,
            "tone": tone, "text": level + " " + text}


def us_block(flow_out):
    """FINRA daily short-sale volume ratio with change versus the previous session, plus the latest short interest."""
    o, si = flow_out.get("offexchange"), flow_out.get("short")
    if not o and not si: return None
    b = {"kind": "volume", "region": "US", "source": "FINRA daily short-sale volume and Nasdaq short interest",
         "scope": "Share of each day's volume sold short, published daily; short interest is published twice a month", "tone": "mid"}
    series = (o or {}).get("series") or []
    if o:
        b.update({"asof": o["asof"], "ratio": o["short_ratio"], "ratio_avg20": o["short_ratio_avg20"],
                  "prev_ratio": series[-2] if len(series) >= 2 else None, "series": series})
        if b["prev_ratio"] is not None: b["chg_pts"] = (o["short_ratio"] - b["prev_ratio"]) * 100
        b["tone"] = o["tone"]
    if si: b["interest"] = {"shares": si["shares"], "date": si["date"], "days_to_cover": si["days_to_cover"], "change_pct": si["change_pct"],
                            "pct_of_shares": si.get("pct_of_shares"), "adr": si.get("adr")}
    parts = []
    if o: parts.append("%.0f%% of the latest session's volume was sold short%s." % (o["short_ratio"] * 100,
                       (" (%+.1f points vs the previous session)" % b["chg_pts"]) if "chg_pts" in b else ""))
    if si and si.get("change_pct") is not None: parts.append("Short interest moved %+.1f%% at the latest settlement." % si["change_pct"])
    b["text"] = " ".join(parts)
    return b


def unavailable(region, why):
    return {"kind": "none", "region": region, "tone": "mid", "text": why}


def for_stock(t, flow_out, ctx, today):
    """The unified `shorts` block shown on the stock card."""
    if t.get("afm"):
        rows = ctx.get("afm")
        if rows is None: return unavailable("NL", "The AFM short-position register could not be downloaded in this build.")
        return register_block("NL", "AFM register of net short positions", register(rows, t["afm"]), "AFM:" + t["afm"], today,
                              "Positions of 0.5% of issued shares or more, updated each business day; smaller positions are not public")
    if t.get("fca"):
        s = ctx.get("fca")
        if s is None: return unavailable("UK", "The FCA short-position register could not be read in this build.")
        return register_block("UK", "FCA register of net short positions", {k: s[k] for k in ("holders", "total_pct", "date", "top")}, "FCA:" + t["fca"], today,
                              "Positions of 0.5% or more, updated each business day; smaller positions are not public")
    if t["ticker"].startswith("HKG:"):
        return unavailable("HK", "Hong Kong publishes daily short turnover as web pages and weekly aggregated positions as files; neither has a stable machine-readable feed, so no short data is shown for this stock.")
    return us_block(flow_out) or unavailable("US", "No short-sale volume or short interest is published for this symbol.")
