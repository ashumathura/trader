import datetime as dt, math, os, random, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline"))
import analysis, lib

def make_bars(n=420, drift=0.0006, sd=0.012, seed=3):
    rnd = random.Random(seed); c = [100.0]
    for _ in range(n - 1): c.append(c[-1] * math.exp(rnd.gauss(drift, sd)))
    t0 = int(dt.datetime(2025, 1, 2, tzinfo=dt.timezone.utc).timestamp())
    return {"t": [t0 + 86400 * i for i in range(n)], "o": c, "h": [x * 1.006 for x in c], "l": [x * 0.994 for x in c], "c": c,
            "v": [1000 + rnd.randint(0, 400) for _ in c]}

class Indicators(unittest.TestCase):
    def test_adx_in_range_and_di(self):
        b = make_bars(); a, p, m = analysis.adx(b["h"], b["l"], b["c"])
        self.assertTrue(0 <= a <= 100 and p >= 0 and m >= 0)

    def test_adx_strong_trend_is_high(self):
        c = [100 + i for i in range(120)]
        a, p, m = analysis.adx([x + 0.5 for x in c], [x - 0.5 for x in c], c)
        self.assertGreater(a, 40); self.assertGreater(p, m)

    def test_aroon_extremes(self):
        up = [float(i) for i in range(40)]
        self.assertGreater(analysis.aroon_osc(up, up), 90)
        dn = up[::-1]; self.assertLess(analysis.aroon_osc(dn, dn), -90)

    def test_hma_tracks_linear_series(self):
        v = [float(i) for i in range(80)]
        self.assertLess(abs(analysis.hma(v, 20) - 79.0), 1.0)  # lags a straight line by well under a bar; an SMA(20) would lag 9.5

    def test_pivots_order(self):
        p = analysis.pivots([10, 12], [8, 9], [9, 11])
        self.assertTrue(p["s3"] < p["s2"] < p["s1"] < p["p"] < p["r1"] < p["r2"] < p["r3"])

    def test_weekly_collapses_by_week(self):
        b = make_bars(70); w = analysis.weekly(b["t"], b["c"])
        self.assertTrue(9 <= len(w) <= 12)

class Hmm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = make_bars(); cls.reg = analysis.regime(cls.bars["c"])

    def test_probabilities_sum_to_one(self):
        for k in ("probs", "next_day", "in_5d", "in_20d"): self.assertAlmostEqual(sum(self.reg[k].values()), 1.0, places=6)
        for row in self.reg["transition"].values(): self.assertAlmostEqual(sum(row.values()), 1.0, places=6)

    def test_states_ordered_by_mean_return(self):
        m = self.reg["moments"]
        self.assertLessEqual(m["bear"]["mean_pct"], m["sideways"]["mean_pct"]); self.assertLessEqual(m["sideways"]["mean_pct"], m["bull"]["mean_pct"])

    def test_stickiness_gives_duration(self):
        r = self.reg; self.assertAlmostEqual(r["expected_days_remaining"], 1 / (1 - r["stickiness"]))

    def test_short_history_gives_none(self):
        self.assertIsNone(analysis.regime(make_bars(100)["c"]))

    def test_no_lookahead_in_scaler(self):
        c = make_bars()["c"]
        full, _ = analysis.features(c); part, _ = analysis.features(c, train_end=300)
        self.assertEqual(len(full), len(part)); self.assertNotEqual(full[10], part[10])  # scaler fitted on different windows

    def test_walk_forward_shape(self):
        w = analysis.walk_forward(make_bars(330)["c"], min_train=252, step=21)
        self.assertGreaterEqual(w["refits"], 2); self.assertIn("separates", w)
        self.assertEqual(sum(w[s]["days"] for s in analysis.STATES), 330 - 1 - 252)  # every out-of-sample day classified once

class Scoring(unittest.TestCase):
    def test_confluence_scales_when_checks_missing(self):
        d = {"last": 10, "sma50": 9, "sma200": None, "rsi": 60, "macd": 1, "macd_signal": 0, "adx": None, "obv": None, "cmf": None, "aroon": None,
             "regime_bull": None, "regime_bear": None}
        c = analysis.confluence(d)
        self.assertEqual(c["available"], 3); self.assertEqual(c["passed"], 3); self.assertEqual(c["score"], 10)

    def test_levels_bullish_geometry(self):
        piv = analysis.pivots([11], [9], [10]); l = analysis.levels(10.0, 0.4, piv, "Bullish")
        self.assertLess(l["stop"], l["entry_lo"]); self.assertGreater(l["target"], l["entry_hi"]); self.assertGreater(l["rr"], 1)

    def test_levels_neutral_has_no_trade(self):
        l = analysis.levels(10.0, 0.4, analysis.pivots([11], [9], [10]), "Neutral")
        self.assertNotIn("target", l)

    def test_analyse_end_to_end_is_json_safe(self):
        import json
        a = analysis.analyse(make_bars())
        json.dumps(a, allow_nan=False, default=str)  # build_data cleans NaN, but this should already be serialisable
        self.assertIn(a["bias"], ("Bullish", "Neutral", "Bearish")); self.assertTrue(0 <= a["confluence"]["score"] <= 10)

    def test_analyse_short_history_still_works(self):
        a = analysis.analyse(make_bars(120)); self.assertIsNone(a["regime"]); self.assertIsNone(a["tech"]["sma200"])

class Sources(unittest.TestCase):
    def test_json_after_handles_jina_wrapper(self):
        txt = 'Title: \nURL Source: https://x\n\nMarkdown Content:\n{"chart":{"result":[{"a":1}]}} trailing'
        self.assertEqual(lib._json_after(txt)["chart"]["result"][0]["a"], 1)
        self.assertTrue(lib._valid_chart(txt)); self.assertFalse(lib._valid_chart("Too Many Requests"))

    def test_parse_chart_skips_null_closes(self):
        j = {"chart": {"result": [{"timestamp": [1, 2, 3], "indicators": {"quote": [{"open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3], "close": [1, None, 3], "volume": [5, 5, 5]}]}, "meta": {}}]}}
        bars, divs, meta = lib._parse_chart(j); self.assertEqual(bars["t"], [1, 3])

    def test_parse_rss_strips_bom_and_outlet_suffix(self):
        rss = '﻿<?xml version="1.0"?><rss><channel><item><title>Big news - Reuters</title><source>Reuters</source><pubDate>Mon, 05 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'
        i = lib.parse_rss(rss)[0]; self.assertEqual(i["title"], "Big news"); self.assertEqual(i["source"], "Reuters")

if __name__ == "__main__":
    unittest.main()
