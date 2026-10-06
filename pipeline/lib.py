"""Shared helpers for the public data pipeline (stdlib only). No personal data is handled here."""
import hashlib, json, math, os, re, statistics, sys, threading, time
import datetime as dt
import urllib.parse, urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, ".cache")
os.makedirs(CACHE, exist_ok=True)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
FRESH = threading.local()
STATUS = {}

def _path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".txt")

_throttle_lock = threading.Lock()
_last_call = {}
MIN_GAP = {"r.jina.ai": 3.3, "stockanalysis.com": 1.2}  # free tier allows about 20 requests per minute

def _throttle(url):
    host = urllib.parse.urlparse(url).netloc
    gap = MIN_GAP.get(host)
    if not gap: return
    with _throttle_lock:
        wait = _last_call.get(host, 0) + gap - time.time()
        if wait > 0: time.sleep(wait)
        _last_call[host] = time.time()

def cached_get(url, ttl, source=None, timeout=20, validate=None, headers=None):
    """GET with an on-disk cache. A response that fails `validate` is never cached (so a rate-limit message
    cannot replace good data) and is treated as a failure."""
    p = _path(url)
    fresh = getattr(FRESH, "on", False)
    if os.path.exists(p) and not fresh and time.time() - os.path.getmtime(p) < ttl:
        if source: STATUS.setdefault(source, "cached")
        return open(p, encoding="utf-8").read()
    try:
        _throttle(url)
        h = {"User-Agent": UA, "Accept": "*/*"}; h.update(headers or {})
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace")
        if validate and not validate(txt): raise ValueError("response failed validation: " + txt[:80].replace("\n", " "))
        open(p, "w", encoding="utf-8").write(txt)
        if source: STATUS[source] = "live"
        return txt
    except Exception as e:
        print("fetch failed [%s] %s: %s" % (source or "-", url.split("?")[0], e), file=sys.stderr)
        if os.path.exists(p):
            if source: STATUS.setdefault(source, "cached")
            return open(p, encoding="utf-8").read()
        if source: STATUS.setdefault(source, "unavailable")
        return None

def cached_bytes(url, ttl, source=None, timeout=60):
    """Like cached_get but for binary files (xlsx). Falls back to a stale copy on failure."""
    p = _path(url) + ".bin"
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < ttl:
        if source: STATUS.setdefault(source, "cached")
        return open(p, "rb").read()
    try:
        _throttle(url)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            blob = r.read()
        open(p, "wb").write(blob)
        if source: STATUS[source] = "live"
        return blob
    except Exception as e:
        print("fetch failed [%s] %s: %s" % (source or "-", url.split("?")[0], e), file=sys.stderr)
        if os.path.exists(p):
            if source: STATUS.setdefault(source, "cached")
            return open(p, "rb").read()
        if source: STATUS.setdefault(source, "unavailable")
        return None

_mem = {}

# ----------------------------------------------------------------------------- yahoo prices
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s&events=div"
DIRECT_DEAD = threading.Event()  # set once Yahoo rate limits this machine, so later calls skip straight to the proxy

def _json_after(txt, marker="{"):
    """Pull the first JSON object out of text that may have a wrapper (the Jina Reader adds a header)."""
    i = txt.find(marker)
    if i < 0: return None
    try: return json.JSONDecoder().raw_decode(txt[i:])[0]
    except Exception: return None

def _valid_chart(txt):
    j = _json_after(txt)
    return bool(j and j.get("chart", {}).get("result"))

def _parse_chart(j):
    res = j["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    ts = res["timestamp"]
    bars = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    for i, t in enumerate(ts):
        if q["close"][i] is None: continue
        bars["t"].append(t)
        for k, kk in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume")):
            bars[k].append(q[kk][i] if q[kk][i] is not None else q["close"][i])
    divs = sorted((d["date"], d["amount"]) for d in (res.get("events", {}).get("dividends", {}) or {}).values())
    return bars, divs, res.get("meta", {})

def yahoo_chart(sym, rng="2y", interval="1d", ttl=1500, drop_partial=True):
    """Daily bars from Yahoo. Direct first; if Yahoo throttles this machine, go through the Jina Reader proxy
    (public price data only). Returns None when both fail."""
    url = YAHOO % (urllib.parse.quote(sym), rng, interval)
    txt, via = None, None
    if not DIRECT_DEAD.is_set():
        txt, via = cached_get(url, ttl, "yahoo", validate=_valid_chart), "direct"
        if not txt and not os.path.exists(_path(url)): DIRECT_DEAD.set()
    if not txt:
        txt, via = cached_get("https://r.jina.ai/" + url, ttl, "yahoo_proxy", validate=_valid_chart, timeout=40,
                              headers={"x-no-cache": "true", "Accept": "text/plain"}), "proxy"
    if not txt: return None
    try:
        bars, divs, meta = _parse_chart(_json_after(txt))
        if drop_partial and market_open(meta, time.time()) and len(bars["t"]) > 1:
            for k in bars: bars[k].pop()
        return {"bars": bars, "divs": divs, "meta": meta, "source": "yahoo", "via": via}
    except Exception as e:
        print("parse failed", sym, e, file=sys.stderr)
        return None

def av_chart(sym, key, ttl=20 * 3600):
    """Alpha Vantage daily bars (free tier: last 100 sessions, 25 calls a day). Used only when Yahoo fails."""
    if not key or not sym: return None
    url = "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&outputsize=compact&symbol=%s&apikey=%s" % (urllib.parse.quote(sym), key)
    txt = cached_get(url, ttl, "alphavantage", validate=lambda t: "Time Series (Daily)" in t)
    if not txt: return None
    try:
        ts = json.loads(txt)["Time Series (Daily)"]
        bars = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
        for d in sorted(ts):
            r = ts[d]
            bars["t"].append(int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
            for k, kk in (("o", "1. open"), ("h", "2. high"), ("l", "3. low"), ("c", "4. close"), ("v", "5. volume")):
                bars[k].append(float(r[kk]))
        return {"bars": bars, "divs": [], "meta": {}, "source": "alphavantage", "via": "direct"}
    except Exception as e:
        print("AV parse failed", sym, e, file=sys.stderr)
        return None

def market_open(meta, now):
    """True while the regular session is running, i.e. Yahoo's newest daily bar is still forming."""
    try:
        reg = meta["currentTradingPeriod"]["regular"]
        return reg["start"] <= now < reg["end"]
    except Exception:
        return False

def ytd_return(t, c):
    """Return since the last close of the previous calendar year (None if history starts this year)."""
    year = dt.datetime.fromtimestamp(t[-1], dt.timezone.utc).year
    prev = [x for ts, x in zip(t, c) if dt.datetime.fromtimestamp(ts, dt.timezone.utc).year < year]
    return (c[-1] / prev[-1] - 1) * 100 if prev else None

CADENCES = (30, 91, 182, 365)

def estimate_next_dividend(dates, today):
    """Project the next ex-dividend date from past ones. Snaps the typical gap to monthly,
    quarterly, semi-annual or annual so irregular real-world gaps still give a sensible date."""
    if len(dates) < 2: return None
    gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    gap = statistics.median(gaps[-4:])
    snapped = min(CADENCES, key=lambda x: abs(x - gap))
    if abs(snapped - gap) > snapped * 0.25: return None
    nxt = dates[-1] + dt.timedelta(days=snapped)
    while nxt < today: nxt += dt.timedelta(days=snapped)
    return nxt, snapped

# ----------------------------------------------------------------------------- technicals
def sma(v, n):
    out, s = [None] * len(v), 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n: s -= v[i - n]
        if i >= n - 1: out[i] = s / n
    return out

def ema(v, n):
    out, k, prev = [None] * len(v), 2 / (n + 1), None
    for i, x in enumerate(v):
        prev = x if prev is None else x * k + prev * (1 - k)
        if i >= n - 1: out[i] = prev
    return out

def rsi(c, n=14):
    out = [None] * len(c)
    if len(c) <= n: return out
    gains = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    losses = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100 - 100 / (1 + ag / al) if al else 100
    for i in range(n, len(gains)):
        ag, al = (ag * (n - 1) + gains[i]) / n, (al * (n - 1) + losses[i]) / n
        out[i + 1] = 100 - 100 / (1 + ag / al) if al else 100
    return out

def atr(h, l, c, n=14):
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    return sma(tr, n)

def compute_tech(bars):
    c, h, l, v, t = bars["c"], bars["h"], bars["l"], bars["v"], bars["t"]
    n = len(c)
    if n < 60: return None
    s20, s50, s200 = sma(c, 20), sma(c, 50), sma(c, 200)
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [(a - b) if a is not None and b is not None else None for a, b in zip(e12, e26)]
    mv = [m for m in macd if m is not None]
    sig_tail = ema(mv, 9)
    sig = [None] * (n - len(mv)) + sig_tail
    r = rsi(c); a = atr(h, l, c)
    last = c[-1]
    sd = statistics.pstdev(c[-20:])
    bb_mid, bb_up, bb_lo = s20[-1], s20[-1] + 2 * sd, s20[-1] - 2 * sd
    bw = (bb_up - bb_lo) / bb_mid if bb_mid else 0
    bws = []
    for i in range(max(19, n - 120), n):
        w = c[i - 19:i + 1]; m = sum(w) / 20
        bws.append((4 * statistics.pstdev(w)) / m if m else 0)
    squeeze = bool(bws) and bw <= sorted(bws)[max(0, len(bws) // 5)]
    yr = slice(max(0, n - 252), n)
    hi52, lo52 = max(h[yr]), min(l[yr])
    hi20, lo20 = max(h[-21:-1]), min(l[-21:-1])
    v20 = sum(v[-21:-1]) / 20 if sum(v[-21:-1]) else 0
    vol_ratio = v[-1] / v20 if v20 else None
    ret = lambda d: (last / c[-1 - d] - 1) * 100 if n > d else None
    sigs, score = [], 0
    def add(label, tone, detail):
        nonlocal score
        sigs.append({"label": label, "tone": tone, "detail": detail})
        score += {"bull": 1, "bear": -1}.get(tone, 0)
    if s50[-1] and s200[-1]:
        if last > s50[-1] > s200[-1]: add("Uptrend", "bull", "Price above 50 and 200 day averages, 50 above 200")
        elif last < s50[-1] < s200[-1]: add("Downtrend", "bear", "Price below 50 and 200 day averages, 50 below 200")
        else: add("Mixed trend", "neutral", "Price and moving averages not aligned")
        for i in range(n - 10, n):
            if s50[i - 1] and s200[i - 1] and s50[i] and s200[i]:
                if s50[i - 1] <= s200[i - 1] and s50[i] > s200[i]: add("Golden cross", "bull", "50 day crossed above 200 day in the last 10 sessions")
                if s50[i - 1] >= s200[i - 1] and s50[i] < s200[i]: add("Death cross", "bear", "50 day crossed below 200 day in the last 10 sessions")
    elif s50[-1]:
        add("Above 50 day" if last > s50[-1] else "Below 50 day", "bull" if last > s50[-1] else "bear", "Short history, 200 day not available")
    if r[-1] is not None:
        if r[-1] >= 70: add("Overbought", "bear", "RSI %.0f is above 70" % r[-1])
        elif r[-1] <= 30: add("Oversold", "bull", "RSI %.0f is below 30, bounce candidate" % r[-1])
        else: sigs.append({"label": "RSI %.0f" % r[-1], "tone": "neutral", "detail": "Neutral momentum"})
    if macd[-1] is not None and sig[-1] is not None:
        for i in range(n - 5, n):
            if None in (macd[i], sig[i], macd[i - 1], sig[i - 1]): continue
            if macd[i - 1] <= sig[i - 1] and macd[i] > sig[i]: add("MACD bullish cross", "bull", "MACD crossed above signal in the last 5 sessions")
            if macd[i - 1] >= sig[i - 1] and macd[i] < sig[i]: add("MACD bearish cross", "bear", "MACD crossed below signal in the last 5 sessions")
        if not any(s["label"].startswith("MACD") for s in sigs):
            sigs.append({"label": "MACD above signal" if macd[-1] > sig[-1] else "MACD below signal",
                         "tone": "bull" if macd[-1] > sig[-1] else "bear", "detail": "Histogram %.2f" % (macd[-1] - sig[-1])})
            score += 1 if macd[-1] > sig[-1] else -1
    breakout = None
    if last > hi20:
        confirmed = vol_ratio and vol_ratio >= 1.3
        breakout = "up"; add("Breakout", "bull", "Close above 20 day high %.2f%s" % (hi20, " on %.1fx volume" % vol_ratio if confirmed else " (volume not confirming)"))
    elif last < lo20:
        breakout = "down"; add("Breakdown", "bear", "Close below 20 day low %.2f" % lo20)
    if last >= hi52 * 0.98: add("Near 52 week high", "bull", "Within 2%% of %.2f" % hi52)
    if last <= lo52 * 1.02: add("Near 52 week low", "bear", "Within 2%% of %.2f" % lo52)
    if last > bb_up: sigs.append({"label": "Above upper Bollinger", "tone": "neutral", "detail": "Stretched, watch for pullback"})
    if last < bb_lo: sigs.append({"label": "Below lower Bollinger", "tone": "neutral", "detail": "Stretched, watch for bounce"})
    if squeeze: sigs.append({"label": "Volatility squeeze", "tone": "neutral", "detail": "Bollinger width at a 6 month low, move may be near"})
    rating = "Bullish" if score >= 2 else "Bearish" if score <= -2 else "Neutral"
    # simple volatility-based regime proxy
    rets = [math.log(c[i] / c[i - 1]) for i in range(max(1, n - 60), n)]
    vol_ann = statistics.pstdev(rets) * math.sqrt(252) * 100 if len(rets) > 5 else None
    k = max(0, n - 130)
    return {
        "last": last, "score": score, "rating": rating, "signals": sigs, "breakout": breakout,
        "rsi": r[-1], "macd": macd[-1], "macd_signal": sig[-1], "atr": a[-1], "atr_pct": a[-1] / last * 100 if a[-1] else None,
        "sma20": s20[-1], "sma50": s50[-1], "sma200": s200[-1], "bb_up": bb_up, "bb_lo": bb_lo,
        "hi52": hi52, "lo52": lo52, "hi20": hi20, "lo20": lo20, "vol_ratio": vol_ratio, "vol_ann": vol_ann,
        "ret_1w": ret(5), "ret_1m": ret(21), "ret_3m": ret(63), "ret_ytd": ytd_return(t, c), "ret_1y": ret(251),
        "support": lo20, "resistance": hi20,
        "chart": {"t": t[k:], "c": c[k:], "s50": s50[k:], "s200": s200[k:], "v": v[k:]},
    }


POS = ("beat", "beats", "surge", "soar", "jump", "rally", "upgrade", "raises", "record", "strong", "growth", "gain",
       "outperform", "buy", "bullish", "boost", "wins", "profit", "rebound", "climb", "rise", "higher", "optimism")
NEG = ("miss", "misses", "plunge", "fall", "drop", "slump", "downgrade", "cut", "cuts", "weak", "loss", "lawsuit", "probe",
       "fine", "sell-off", "selloff", "bearish", "warn", "warning", "decline", "lower", "fear", "tariff", "ban", "layoff",
       "investigation", "recall", "slide", "tumble")
THEMES = {
    "Earnings and guidance": ("earnings", "results", "guidance", "quarter", "revenue", "profit", "outlook", "eps"),
    "Analyst views": ("upgrade", "downgrade", "price target", "analyst", "rating", "outperform", "overweight"),
    "Rates and central banks": ("fed", "ecb", "rate cut", "rate hike", "interest rate", "powell", "lagarde", "fomc", "yield", "treasury"),
    "Inflation and economy": ("inflation", "cpi", "gdp", "jobs", "payrolls", "unemployment", "recession", "pmi", "consumer"),
    "Trade and geopolitics": ("tariff", "trade war", "sanction", "china", "export", "war", "ukraine", "middle east", "election", "taiwan"),
    "Oil and commodities": ("oil", "opec", "crude", "gold", "copper", "gas "),
    "AI and tech": ("ai ", "artificial intelligence", "chip", "semiconductor", "nvidia", "cloud", "data center"),
    "Deals and corporate": ("acquisition", "merger", "buyback", "takeover", "stake", "ipo", "dividend", "spin-off", "restructur"),
    "Regulation and legal": ("regulator", "lawsuit", "probe", "antitrust", "fine", "investigation", "court", "eu "),
}

def tone(title):
    t = title.lower()
    p = sum(w in t for w in POS); n = sum(w in t for w in NEG)
    return "pos" if p > n else "neg" if n > p else "neu"

def themes_of(titles):
    cnt = {}
    for t in titles:
        tl = " " + t.lower() + " "
        for th, kws in THEMES.items():
            if any(k in tl for k in kws): cnt[th] = cnt.get(th, 0) + 1
    return sorted(cnt.items(), key=lambda x: -x[1])

def parse_rss(txt, limit=25):
    items = []
    try:
        root = ET.fromstring(txt.lstrip("\ufeff \r\n\t"))
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            if not title: continue
            pd = it.findtext("pubDate") or ""
            try: ts = dt.datetime.strptime(pd[:25], "%a, %d %b %Y %H:%M:%S").isoformat()
            except Exception: ts = ""
            src = (it.findtext("source") or "").strip()
            if src and title.endswith(" - " + src): title = title[:-len(" - " + src)]  # Google News appends the outlet
            items.append({"title": title, "link": (it.findtext("link") or "").strip(), "time": ts, "source": src, "tone": tone(title)})
            if len(items) >= limit: break
    except Exception: pass
    return items

MARKET_FEEDS = [
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Investing.com", "https://www.investing.com/rss/news_25.rss"),
]
CENTRAL_BANK_FEEDS = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.html"),
]
MARKET = [
    ("equity", "^GSPC", "S&P 500"), ("equity", "^IXIC", "Nasdaq"), ("equity", "^NDX", "Nasdaq 100"), ("equity", "^DJI", "Dow Jones"), ("equity", "^RUT", "Russell 2000"),
    ("equity", "ES=F", "S&P 500 futures"), ("equity", "NQ=F", "Nasdaq futures"),
    ("equity", "^AEX", "AEX"), ("equity", "^STOXX50E", "Euro Stoxx 50"), ("equity", "^STOXX", "STOXX 600"), ("equity", "^GDAXI", "DAX"), ("equity", "^FCHI", "CAC 40"),
    ("equity", "^FTSE", "FTSE 100"), ("equity", "^IBEX", "IBEX 35"),
    ("equity", "^N225", "Nikkei 225"), ("equity", "000001.SS", "Shanghai Composite"), ("equity", "^HSI", "Hang Seng"), ("equity", "^KS11", "Kospi"), ("equity", "^AXJO", "ASX 200"),
    ("vol", "^VIX", "VIX"), ("vol", "^VIX9D", "VIX 9-day"), ("vol", "^VIX3M", "VIX 3-month"), ("vol", "^VVIX", "VVIX"), ("vol", "^SKEW", "SKEW"),
    ("vol", "^MOVE", "MOVE (rates volatility)"), ("vol", "^VXN", "VXN (Nasdaq volatility)"), ("vol", "^OVX", "OVX (oil volatility)"),
    ("rates", "^TNX", "US 10Y yield (Yahoo)"),
    ("fx", "DX-Y.NYB", "US dollar index"), ("fx", "EURUSD=X", "EUR/USD"), ("fx", "USDJPY=X", "USD/JPY"), ("fx", "GBPUSD=X", "GBP/USD"), ("fx", "EURGBP=X", "EUR/GBP"), ("fx", "EURCHF=X", "EUR/CHF"),
    ("commodity", "CL=F", "Oil (WTI)"), ("commodity", "BZ=F", "Oil (Brent)"), ("commodity", "NG=F", "Natural gas"), ("commodity", "GC=F", "Gold"), ("commodity", "SI=F", "Silver"),
    ("commodity", "HG=F", "Copper"), ("commodity", "SB=F", "Sugar"), ("commodity", "CC=F", "Cocoa"),
    ("crypto", "BTC-EUR", "Bitcoin (EUR)"), ("crypto", "ETH-EUR", "Ethereum (EUR)"), ("crypto", "BTC-USD", "Bitcoin (USD)"), ("crypto", "ETH-USD", "Ethereum (USD)"),
    ("crypto", "SOL-USD", "Solana (USD)"), ("crypto", "XRP-USD", "XRP (USD)"), ("crypto", "IBIT", "iShares Bitcoin Trust"), ("crypto", "COIN", "Coinbase"), ("crypto", "MSTR", "Strategy"),
    ("sector", "XLK", "Technology"), ("sector", "XLF", "Financials"), ("sector", "XLE", "Energy"), ("sector", "XLV", "Health care"), ("sector", "XLY", "Consumer discretionary"),
    ("sector", "XLP", "Consumer staples"), ("sector", "XLI", "Industrials"), ("sector", "XLU", "Utilities"), ("sector", "XLB", "Materials"), ("sector", "XLRE", "Real estate"),
    ("sector", "XLC", "Communication services"),
]
INDICES = [(s, n) for _, s, n in MARKET]
