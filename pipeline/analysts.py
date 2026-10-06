"""Analyst consensus, price targets and recent rating or target changes (stdlib only).

Source: the public forecast page on stockanalysis.com for each stock (analyst data from S&P Global, with recent actions
from TipRanks). It loads for US, Euronext, London, Hong Kong and OTC listings. The page is parsed defensively: anything that
does not match is left out rather than guessed, and the source is named next to the numbers on the site.
A daily snapshot of the consensus is recorded so changes in the mean target can be shown once history has built up.
"""
import datetime as dt
import html
import json
import os
import re
from html.parser import HTMLParser

import lib

BASE = "https://stockanalysis.com/%s/forecast/"
ACTIONS = ("Maintains", "Reiterates", "Upgrades", "Downgrades", "Initiates", "Assumes", "Resumes", "Reinstates", "Raises", "Lowers", "Cuts", "Adds", "Removes")
UP_WORDS = ("Buy", "Strong Buy", "Outperform", "Overweight", "Accumulate", "Add", "Positive", "Market Outperform")
DOWN_WORDS = ("Sell", "Strong Sell", "Underperform", "Underweight", "Reduce", "Negative")


class _Tables(HTMLParser):
    """Collects every <table> as a list of rows, each row a list of cell texts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._t, self._r, self._c, self._in = [], None, None, None, False
    def handle_starttag(self, tag, attrs):
        if tag == "table": self._t = []
        elif tag == "tr" and self._t is not None: self._r = []
        elif tag in ("td", "th") and self._r is not None: self._c = []
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._c is not None and self._r is not None:
            self._r.append(re.sub(r"\s+", " ", "".join(self._c)).strip()); self._c = None
        elif tag == "tr" and self._r is not None and self._t is not None:
            self._t.append(self._r); self._r = None
        elif tag == "table" and self._t is not None:
            self.tables.append(self._t); self._t = None
    def handle_data(self, data):
        if self._c is not None: self._c.append(data + " ")


def tables(page):
    p = _Tables(); p.feed(page); return p.tables

def plain(page):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->", "", page, flags=re.S)))).strip()

def num(s):
    """First number in a price string like '€1,291', '$4.5', 'HK$108.20' or '2,100.00'."""
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s or "")
    return float(m.group(0).replace(",", "")) if m else None

def money_symbol(s):
    m = re.match(r"\s*([^\d\s.,+\-]+)", s or "")
    return m.group(1) if m else ""


# ----------------------------------------------------------------------------- parsing
def parse_consensus(text):
    """Headline paragraph: analyst count, rating word, mean/low/high target with the page's own upside figures."""
    m = re.search(r"According to (\d+) analysts? polled by [^,]+, .+? stock has a consensus rating of \"([^\"]+)\" and an average price target of ([^\s]+)\. "
                  r"The average 1-year stock price forecast is ([\d.]+)% (higher|lower) than the current stock price, while the lowest is ([^\s]+) \(([+-]?[\d.]+)%\) "
                  r"and the highest is ([^\s]+) \(([+-]?[\d.]+)%\)", text)
    if not m: return None
    sign = 1 if m.group(5) == "higher" else -1
    return {"n": int(m.group(1)), "rating": m.group(2), "mean": num(m.group(3)), "low": num(m.group(6)), "high": num(m.group(8)), "symbol": money_symbol(m.group(3)),
            "upside_pct": sign * float(m.group(4)), "low_upside_pct": float(m.group(7)), "high_upside_pct": float(m.group(9))}

def parse_target_table(tbls):
    """Target / Low / Average / Median / High table -> median."""
    for t in tbls:
        if t and t[0][:2] == ["Target", "Low"] and len(t) >= 2:
            row = t[1] if t[1][0] == "Price" else None
            if row and len(row) >= 5: return {"median": num(row[3])}
    return {}

def parse_rating_counts(tbls):
    """Monthly rating-count table -> [{month, strong_buy, buy, hold, sell, strong_sell, total}] oldest first."""
    for t in tbls:
        if t and t[0] and t[0][0] == "Rating" and any(r and r[0] == "Strong Buy" for r in t):
            months = t[0][1:]; rows = {r[0]: r[1:] for r in t[1:] if r}
            out = []
            for i, m in enumerate(months):
                try:
                    out.append({"month": m, **{k: int(rows[lbl][i]) for k, lbl in (("strong_buy", "Strong Buy"), ("buy", "Buy"), ("hold", "Hold"), ("sell", "Sell"),
                                                                                   ("strong_sell", "Strong Sell"), ("total", "Total"))}})
                except (KeyError, ValueError, IndexError): continue
            return out
    return []

def parse_recommendations(page):
    """The page embeds about a year of monthly counts as a JS array; used for a longer trend than the table."""
    out = []
    for m in re.finditer(r'\{buy:(\d+),date:"([\d-]+)",hold:(\d+),sell:(\d+),month:"[^"]*",score:[\d.]+,total:(\d+),updated:"[^"]*",consensus:"([^"]*)",strongBuy:(\d+),strongSell:(\d+)\}', page):
        out.append({"date": m.group(2), "buy": int(m.group(1)) + int(m.group(7)), "hold": int(m.group(3)), "sell": int(m.group(4)) + int(m.group(8)), "total": int(m.group(5)), "consensus": m.group(6)})
    return out

def _repeat_suffix(tokens, max_k):
    """Largest k <= max_k such that the last k tokens repeat the k before them (pages duplicate content for mobile layouts)."""
    for k in range(min(max_k, len(tokens) // 2), 0, -1):
        if tokens[-k:] == tokens[-2 * k:-k]: return k
    return 0

def parse_action_row(cells):
    """One row of the analyst-actions table -> dict, or None when it does not look like one."""
    txt = " ".join(cells).replace("&#8594", "→")
    tok = txt.split()
    if len(tok) < 8: return None
    dm = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})$", " ".join(tok[-3:]))
    if not dm: return None
    date = dt.datetime.strptime("%s %s %s" % dm.groups(), "%b %d %Y").date()
    tok = tok[:-3]
    upside = tok.pop() if tok and re.fullmatch(r"[+-]?\d+(?:\.\d+)?%|n/a", tok[-1]) else None
    k = _repeat_suffix(tok, 9)
    if k < 2: return None
    x, head = tok[-k:], tok[:-2 * k]
    ai = next((i for i, w in enumerate(x) if w in ACTIONS), None)
    if ai is None or ai == 0: return None
    kf = _repeat_suffix(head, 5)
    firm = " ".join(head[-kf:]) if kf else (head[-1] if head else "")
    name = " ".join(head[:-2 * kf]) if kf else " ".join(head[:-1])
    pt = " ".join(x[ai + 1:])
    nums = [num(p) for p in re.split(r"→", pt) if num(p) is not None]
    return {"date": date.isoformat(), "analyst": name, "firm": firm, "rating": " ".join(x[:ai]), "action": x[ai], "pt_text": pt,
            "pt_from": nums[0] if len(nums) == 2 else None, "pt_to": nums[-1] if nums else None, "symbol": money_symbol(pt),
            "upside": upside if upside and upside != "n/a" else None}

def parse_actions(tbls):
    for t in tbls:
        head = " ".join(t[0]) if t else ""
        if "Analyst" in head and "Firm" in head and "Date" in head:
            out = [a for a in (parse_action_row(r) for r in t[1:]) if a]
            for a in out:
                a["target_change_pct"] = (a["pt_to"] / a["pt_from"] - 1) * 100 if a["pt_from"] and a["pt_to"] else None
                a["kind"] = ("upgrade" if a["action"] == "Upgrades" else "downgrade" if a["action"] == "Downgrades" else "initiate" if a["action"] in ("Initiates", "Assumes", "Resumes", "Reinstates")
                             else "target_raise" if a["target_change_pct"] and a["target_change_pct"] > 0 else "target_cut" if a["target_change_pct"] and a["target_change_pct"] < 0 else "reiterate")
            return out
    return []

def parse_estimates(tbls):
    """Next fiscal-year revenue and EPS consensus from the estimates table (values behind a paywall are skipped)."""
    for t in tbls:
        if t and t[0] and t[0][0] == "Fiscal Year":
            years = t[0][1:]
            def row(label):
                r = next((x for x in t if x and x[0] == label), None)
                if not r: return {}
                return {y: v for y, v in zip(years, r[1:]) if re.fullmatch(r"-?[\d.,]+[KMBT]?%?", v or "")}
            return {"years": years, "revenue": row("Revenue"), "eps": row("EPS"), "eps_growth": row("EPS Growth"), "revenue_growth": row("Revenue Growth")}
    return {}

def parse_page(page):
    """Everything we can read from one forecast page, or None when the consensus paragraph is missing."""
    text = plain(page)
    cons = parse_consensus(text)
    if not cons: return None
    tb = tables(page)
    cons.update(parse_target_table(tb))
    return {"consensus": cons, "counts": parse_rating_counts(tb), "history": parse_recommendations(page), "actions": parse_actions(tb), "estimates": parse_estimates(tb)}


# ----------------------------------------------------------------------------- derived metrics
def revisions(actions, today, days):
    cut = today - dt.timedelta(days=days)
    rec = [a for a in actions if dt.date.fromisoformat(a["date"]) >= cut]
    k = lambda name: sum(1 for a in rec if a["kind"] == name)
    return {"days": days, "upgrades": k("upgrade"), "downgrades": k("downgrade"), "initiations": k("initiate"), "target_raises": k("target_raise"),
            "target_cuts": k("target_cut"), "actions": len(rec)}

def sentiment(cons, counts, rev90):
    """Plain scoring: how bullish the street is, which way revisions lean, and the resulting tone."""
    score, why = 0, []
    last = counts[-1] if counts else None
    if last and last["total"]:
        buy_pct = (last["strong_buy"] + last["buy"]) / last["total"] * 100
        sell_pct = (last["sell"] + last["strong_sell"]) / last["total"] * 100
        if buy_pct >= 70: score += 1; why.append("%.0f%% of analysts rate it Buy." % buy_pct)
        if buy_pct >= 85 and last["total"] >= 8: score += 1
        elif buy_pct <= 35 or sell_pct >= 30: score -= 1; why.append("Only %.0f%% of analysts rate it Buy, %.0f%% Sell." % (buy_pct, sell_pct))
    net = rev90["upgrades"] - rev90["downgrades"]
    if net >= 2: score += 1; why.append("%d upgrades vs %d downgrades in 90 days." % (rev90["upgrades"], rev90["downgrades"]))
    elif net <= -2: score -= 1; why.append("%d downgrades vs %d upgrades in 90 days." % (rev90["downgrades"], rev90["upgrades"]))
    tr = rev90["target_raises"] - rev90["target_cuts"]
    if tr >= 3: score += 1; why.append("%d target raises vs %d cuts in 90 days." % (rev90["target_raises"], rev90["target_cuts"]))
    elif tr <= -3: score -= 1; why.append("%d target cuts vs %d raises in 90 days." % (rev90["target_cuts"], rev90["target_raises"]))
    if cons.get("upside_pct") is not None and cons["upside_pct"] >= 20 and cons.get("n", 0) >= 5: score += 1; why.append("Mean target is %.0f%% above the price." % cons["upside_pct"])
    if cons.get("upside_pct") is not None and cons["upside_pct"] < -5: score -= 1; why.append("Price is %.0f%% above the mean target." % abs(cons["upside_pct"]))
    tone = "up" if score >= 2 else "dn" if score <= -2 else "mid"
    return {"score": score, "tone": tone, "label": {"up": "Constructive", "dn": "Cautious", "mid": "Mixed"}[tone], "why": why}

def trend(counts_hist):
    """Change in Buy share over the monthly history that came with the page (oldest vs newest)."""
    h = [c for c in counts_hist if c["total"]]
    if len(h) < 4: return None
    first, last = h[0], h[-1]
    pct = lambda c: c["buy"] / c["total"] * 100
    return {"from": first["date"], "to": last["date"], "buy_pct_from": pct(first), "buy_pct_to": pct(last), "change_pts": pct(last) - pct(first),
            "n_from": first["total"], "n_to": last["total"]}

def snapshot(ticker, today, cons):
    """Record today's mean target and analyst count, return the change against the oldest record at least 28 days back."""
    path = os.path.join(lib.CACHE, "analyst_history.json")
    try:
        with open(path) as fh: hist = json.load(fh)
    except Exception: hist = {}
    h = hist.setdefault(ticker, {})
    if cons.get("mean") is not None: h[today.isoformat()] = {"mean": cons["mean"], "n": cons["n"], "low": cons.get("low"), "high": cons.get("high")}
    for k in sorted(h)[:-400]: h.pop(k)
    try:
        with open(path, "w") as fh: json.dump(hist, fh)
    except Exception: pass
    old = [(k, v) for k, v in sorted(h.items()) if (today - dt.date.fromisoformat(k)).days >= 28]
    out = {"days_recorded": len(h)}
    if old and cons.get("mean"):
        k, v = old[-1]
        out.update({"since": k, "mean_then": v["mean"], "mean_change_pct": (cons["mean"] / v["mean"] - 1) * 100 if v["mean"] else None})
    return out


# ----------------------------------------------------------------------------- fetch and assemble
def analyse(t, today, price=None):
    """The `analysts` block for one stock, or None when the page cannot be read."""
    slug = t.get("sa")
    if not slug: return None
    page = lib.cached_get(BASE % slug, 6 * 3600, "analysts", validate=lambda p: "analysts polled by" in p, timeout=45)
    if not page: return None
    try:
        d = parse_page(page)
    except Exception as e:
        print("analyst parse failed", t["ticker"], e, file=__import__("sys").stderr); return None
    if not d: return None
    cons, counts, acts = d["consensus"], d["counts"], d["actions"]
    r30, r90 = revisions(acts, today, 30), revisions(acts, today, 90)
    # the page prints Hong Kong dollars as "$"; show the stock's own currency so targets sit next to the price they refer to
    cons["symbol"] = {"EUR": "\u20ac", "USD": "$", "HKD": "HK$", "GBp": ""}.get(t.get("currency"), cons.get("symbol"))
    cons["currency_note"] = "targets in %s as shown by the source" % (t.get("currency") or "local currency")
    out = {"source": "stockanalysis.com (S&P Global, TipRanks)", "url": BASE % slug, "asof": today.isoformat(), "consensus": cons, "counts": counts,
           "trend": trend(d["history"]), "history": d["history"][-13:], "actions": acts[:15], "rev30": r30, "rev90": r90, "estimates": d["estimates"],
           "snapshot": snapshot(t["ticker"], today, cons), "sentiment": sentiment(cons, counts, r90)}
    return out
