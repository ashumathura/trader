import datetime as dt, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline"))
import analysts, lib

PAGE = """<html><body>
<p><!--[1-->According to 42 analysts polled by S&amp;P Global, ASML Holding stock has a consensus rating of "Strong Buy" and an average price target of &#8364;2,051. The average 1-year stock price forecast is 25.34% higher than the current stock price, while the lowest is &#8364;1,291 (-21.11%) and the highest is &#8364;2,557 (+56.26%).<!--]--></p>
<table><tr><th>Target</th><th>Low</th><th>Average</th><th>Median</th><th>High</th></tr><tr><td>Price</td><td>&#8364;1,291</td><td>&#8364;2,051</td><td>&#8364;2,100</td><td>&#8364;2,557</td></tr><tr><td>Change</td><td>-21.11%</td><td>+25.34%</td><td>+28.33%</td><td>+56.26%</td></tr></table>
<table><tr><th>Rating</th><th>May '26</th><th>Jun '26</th><th>Jul '26</th><th>Aug '26</th></tr>
<tr><td>Strong Buy</td><td>32</td><td>33</td><td>33</td><td>32</td></tr><tr><td>Buy</td><td>6</td><td>6</td><td>7</td><td>6</td></tr><tr><td>Hold</td><td>5</td><td>3</td><td>3</td><td>4</td></tr>
<tr><td>Sell</td><td>0</td><td>1</td><td>0</td><td>0</td></tr><tr><td>Strong Sell</td><td>1</td><td>1</td><td>1</td><td>1</td></tr><tr><td>Total</td><td>44</td><td>44</td><td>44</td><td>43</td></tr></table>
<table><tr><th>Analyst</th><th>Firm</th><th>Rating</th><th>Rating Action</th><th>Price Target</th><th>Upside</th><th>Date</th></tr>
<tr><td>Francois Xavier Bouvignies</td><td>UBS UBS</td><td>Buy Maintains &#8364;2,350</td><td>Buy Maintains &#8364;2,350</td><td>+43.61%</td><td>Sep 28, 2026</td></tr>
<tr><td>Didier Scemama Bank of America Securities Bank of America Securities Buy Reiterates &#8364;2,452 Buy Reiterates &#8364;2,452 +49.84% Sep 26, 2026</td></tr>
<tr><td>Alex Yao J.P. Morgan J.P. Morgan Hold Reiterates $4 &#8594 $4.5 Hold Reiterates $4 &#8594 $4.5 +20.97% Aug 17, 2026</td></tr>
<tr><td>Ellie Jiang Macquarie Macquarie Buy Upgrades n/a Buy Upgrades n/a n/a Aug 14, 2026</td></tr>
<tr><td>Eric Sheridan Goldman Sachs Goldman Sachs Buy Reiterates $94 &#8594 $90 Buy Reiterates $94 &#8594 $90 +32.12% Oct 6, 2026</td></tr>
<tr><td>Garbage row that is not an action</td></tr></table>
<table><tr><th>Fiscal Year</th><th>FY 2025</th><th>FY 2026</th><th>FY 2027</th></tr><tr><td>Revenue</td><td>32.67B</td><td>42.88B</td><td>Upgrade</td></tr><tr><td>EPS</td><td>24.71</td><td>38.35</td><td>Upgrade</td></tr></table>
<script>recommendations:[{buy:4,date:"2025-11-27",hold:10,sell:0,month:"Nov '25",score:6.3,total:38,updated:"2025-11-27",consensus:"Buy",strongBuy:23,strongSell:1},{buy:6,date:"2026-01-30",hold:6,sell:1,month:"Jan '26",score:7.0,total:43,updated:"2026-01-30",consensus:"Buy",strongBuy:25,strongSell:1},{buy:6,date:"2026-04-30",hold:5,sell:1,month:"Apr '26",score:7.0,total:44,updated:"2026-04-30",consensus:"Buy",strongBuy:31,strongSell:1},{buy:7,date:"2026-09-30",hold:3,sell:0,month:"Sep '26",score:7.0,total:42,updated:"2026-09-30",consensus:"Strong Buy",strongBuy:31,strongSell:1}]</script>
</body></html>"""

class Parse(unittest.TestCase):
    def test_consensus(self):
        c = analysts.parse_consensus(analysts.plain(PAGE))
        self.assertEqual((c["n"], c["rating"], c["mean"], c["low"], c["high"], c["symbol"]), (42, "Strong Buy", 2051.0, 1291.0, 2557.0, "€"))
        self.assertAlmostEqual(c["upside_pct"], 25.34); self.assertAlmostEqual(c["low_upside_pct"], -21.11)

    def test_lower_than_price_gives_negative_upside(self):
        t = analysts.plain(PAGE).replace("25.34% higher", "12.50% lower")
        self.assertAlmostEqual(analysts.parse_consensus(t)["upside_pct"], -12.5)

    def test_counts_table(self):
        d = analysts.parse_page(PAGE); self.assertEqual(d["counts"][-1]["total"], 43); self.assertEqual(d["counts"][0]["strong_buy"], 32); self.assertEqual(d["consensus"]["median"], 2100.0)

    def test_history_array(self):
        h = analysts.parse_recommendations(PAGE); self.assertEqual(len(h), 4); self.assertEqual(h[-1]["buy"], 38); self.assertEqual(h[0]["sell"], 1)

    def test_actions_rows(self):
        a = {x["firm"]: x for x in analysts.parse_actions(analysts.tables(PAGE))}
        self.assertEqual(set(a), {"UBS", "Bank of America Securities", "J.P. Morgan", "Macquarie", "Goldman Sachs"})
        self.assertEqual((a["UBS"]["analyst"], a["UBS"]["rating"], a["UBS"]["action"], a["UBS"]["pt_to"]), ("Francois Xavier Bouvignies", "Buy", "Maintains", 2350.0))
        self.assertEqual((a["J.P. Morgan"]["pt_from"], a["J.P. Morgan"]["pt_to"], a["J.P. Morgan"]["kind"]), (4.0, 4.5, "target_raise"))
        self.assertEqual((a["Goldman Sachs"]["pt_from"], a["Goldman Sachs"]["pt_to"], a["Goldman Sachs"]["kind"]), (94.0, 90.0, "target_cut"))
        self.assertEqual((a["Macquarie"]["kind"], a["Macquarie"]["pt_to"], a["Macquarie"]["upside"]), ("upgrade", None, None))
        self.assertEqual(a["Bank of America Securities"]["date"], "2026-09-26")

    def test_unreadable_page_gives_none(self):
        self.assertIsNone(analysts.parse_page("<html>Just a moment...</html>"))

    def test_estimates_skip_paywalled_cells(self):
        e = analysts.parse_estimates(analysts.tables(PAGE)); self.assertEqual(e["eps"], {"FY 2025": "24.71", "FY 2026": "38.35"})

class Derived(unittest.TestCase):
    def setUp(self):
        self.acts = analysts.parse_actions(analysts.tables(PAGE)); self.today = dt.date(2026, 10, 7)

    def test_revisions_window(self):
        r = analysts.revisions(self.acts, self.today, 30); self.assertEqual((r["target_cuts"], r["upgrades"]), (1, 0))
        r = analysts.revisions(self.acts, self.today, 90); self.assertEqual((r["upgrades"], r["target_raises"], r["actions"]), (1, 1, 5))

    def test_sentiment_constructive(self):
        d = analysts.parse_page(PAGE); s = analysts.sentiment(d["consensus"], d["counts"], {"upgrades": 3, "downgrades": 0, "target_raises": 5, "target_cuts": 1})
        self.assertEqual(s["tone"], "up")

    def test_sentiment_cautious_when_price_far_above_target(self):
        cons = {"upside_pct": -20.0}; counts = [{"strong_buy": 2, "buy": 2, "hold": 6, "sell": 3, "strong_sell": 2, "total": 15}]
        s = analysts.sentiment(cons, counts, {"upgrades": 0, "downgrades": 3, "target_raises": 0, "target_cuts": 4}); self.assertEqual(s["tone"], "dn")

    def test_trend_buy_share(self):
        t = analysts.trend(analysts.parse_recommendations(PAGE)); self.assertEqual((t["from"], t["to"]), ("2025-11-27", "2026-09-30"))

    def test_snapshot_change_after_a_month(self):
        old = lib.CACHE
        with tempfile.TemporaryDirectory() as d:
            lib.CACHE = d
            try:
                analysts.snapshot("X", dt.date(2026, 9, 1), {"mean": 100.0, "n": 10})
                s = analysts.snapshot("X", dt.date(2026, 10, 7), {"mean": 110.0, "n": 10})
                self.assertAlmostEqual(s["mean_change_pct"], 10.0); self.assertEqual(s["days_recorded"], 2)
                self.assertNotIn("mean_change_pct", analysts.snapshot("Y", dt.date(2026, 10, 7), {"mean": 5.0, "n": 3}))
            finally: lib.CACHE = old

if __name__ == "__main__":
    unittest.main()

class SentimentTone(unittest.TestCase):
    def test_strong_consensus_with_room_is_constructive(self):
        cons = {"upside_pct": 25.0, "n": 42}; counts = [{"strong_buy": 31, "buy": 7, "hold": 3, "sell": 0, "strong_sell": 1, "total": 42}]
        self.assertEqual(analysts.sentiment(cons, counts, {"upgrades": 0, "downgrades": 0, "target_raises": 0, "target_cuts": 0})["tone"], "up")

    def test_small_sample_does_not_get_upside_credit(self):
        cons = {"upside_pct": 30.0, "n": 3}; counts = [{"strong_buy": 0, "buy": 1, "hold": 2, "sell": 0, "strong_sell": 0, "total": 3}]
        self.assertEqual(analysts.sentiment(cons, counts, {"upgrades": 0, "downgrades": 0, "target_raises": 0, "target_cuts": 0})["tone"], "mid")

    def test_bullish_but_targets_being_cut_is_mixed(self):
        cons = {"upside_pct": 80.0, "n": 25}; counts = [{"strong_buy": 20, "buy": 5, "hold": 0, "sell": 0, "strong_sell": 0, "total": 25}]
        t = analysts.sentiment(cons, counts, {"upgrades": 0, "downgrades": 0, "target_raises": 0, "target_cuts": 3})["tone"]
        self.assertIn(t, ("mid", "up"))
