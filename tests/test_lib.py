import datetime as dt, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline"))
import lib

def bars_from(closes, vol=1000):
    t0 = int(dt.datetime(2025, 1, 2, tzinfo=dt.timezone.utc).timestamp())
    return {"t": [t0 + 86400 * i for i in range(len(closes))], "o": list(closes),
            "h": [c * 1.01 for c in closes], "l": [c * 0.99 for c in closes], "c": list(closes), "v": [vol] * len(closes)}

class Indicators(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(lib.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])

    def test_rsi_extremes(self):
        self.assertEqual(lib.rsi(list(range(1, 40)))[-1], 100)
        self.assertLess(lib.rsi(list(range(40, 1, -1)))[-1], 1)

    def test_rsi_short_series(self):
        self.assertTrue(all(x is None for x in lib.rsi([1, 2, 3])))

    def test_uptrend_is_bullish(self):
        tech = lib.compute_tech(bars_from([100 + i * 0.5 for i in range(260)]))
        self.assertEqual(tech["rating"], "Bullish")
        self.assertIn("Uptrend", [s["label"] for s in tech["signals"]])

    def test_downtrend_is_bearish(self):
        tech = lib.compute_tech(bars_from([300 - i * 0.5 for i in range(260)]))
        self.assertEqual(tech["rating"], "Bearish")

    def test_too_little_history(self):
        self.assertIsNone(lib.compute_tech(bars_from([1.0] * 30)))

class Ytd(unittest.TestCase):
    def test_ytd_uses_prior_year_close(self):
        b = bars_from([100.0] * 400)  # runs from 2025-01-02 into 2026
        b["c"][-1] = 110.0
        year_end = max(i for i, ts in enumerate(b["t"]) if dt.datetime.fromtimestamp(ts, dt.timezone.utc).year == 2025)
        b["c"][year_end] = 100.0
        self.assertAlmostEqual(lib.ytd_return(b["t"], b["c"]), 10.0)

    def test_ytd_none_when_history_starts_this_year(self):
        b = bars_from([100.0] * 10)
        self.assertIsNone(lib.ytd_return(b["t"], b["c"]))

class Dividends(unittest.TestCase):
    D = dt.date

    def test_quarterly_irregular_gaps_snap(self):
        dates = [self.D(2026, 1, 10), self.D(2026, 4, 12), self.D(2026, 7, 9)]
        nxt, gap = lib.estimate_next_dividend(dates, self.D(2026, 8, 1))
        self.assertEqual(gap, 91)
        self.assertGreaterEqual(nxt, self.D(2026, 8, 1))

    def test_semi_annual(self):
        dates = [self.D(2025, 5, 1), self.D(2025, 10, 30)]
        self.assertEqual(lib.estimate_next_dividend(dates, self.D(2026, 1, 1))[1], 182)

    def test_single_dividend_gives_none(self):
        self.assertIsNone(lib.estimate_next_dividend([self.D(2026, 1, 1)], self.D(2026, 2, 1)))

    def test_odd_cadence_gives_none(self):
        self.assertIsNone(lib.estimate_next_dividend([self.D(2026, 1, 1), self.D(2026, 1, 5)], self.D(2026, 2, 1)))

class Session(unittest.TestCase):
    def test_market_open(self):
        meta = {"currentTradingPeriod": {"regular": {"start": 100, "end": 200}}}
        self.assertTrue(lib.market_open(meta, 150))
        self.assertFalse(lib.market_open(meta, 250))
        self.assertFalse(lib.market_open({}, 150))

class News(unittest.TestCase):
    def test_tone(self):
        self.assertEqual(lib.tone("Company beats estimates, shares surge"), "pos")
        self.assertEqual(lib.tone("Regulator probe, stock plunges"), "neg")

    def test_parse_rss(self):
        rss = "<rss><channel><item><title>Fed holds rates</title><link>http://x</link><pubDate>Mon, 05 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>"
        items = lib.parse_rss(rss)
        self.assertEqual(items[0]["title"], "Fed holds rates")
        self.assertEqual(items[0]["time"], "2026-10-05T12:00:00")
        self.assertEqual(lib.parse_rss("not xml"), [])

if __name__ == "__main__":
    unittest.main()
