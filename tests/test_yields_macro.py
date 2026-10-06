import datetime as dt, json, math, os, random, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline"))
import yields, macro, context, lib

D = dt.date

class Parsers(unittest.TestCase):
    def test_treasury(self):
        t = yields.parse_treasury('Date,"2 Yr","10 Yr"\n10/05/2026,4.84,5.31\n10/02/2026,4.80,5.20\n')
        self.assertEqual(t["10 Yr"], [(D(2026, 10, 2), 5.2), (D(2026, 10, 5), 5.31)])

    def test_treasury_bom_and_blank_cells(self):
        t = yields.parse_treasury('﻿Date,"2 Yr","30 Yr"\n01/02/2026,4.0,\n')
        self.assertEqual(t["2 Yr"], [(D(2026, 1, 2), 4.0)]); self.assertNotIn("30 Yr", t)

    def test_boe(self):
        t = yields.parse_boe("DATE,IUDMNPY,IUDSNPY\n01 Sep 2026,5.1333,4.6616\n02 Sep 2026,5.171,4.6975\n")
        self.assertEqual(t["IUDMNPY"][-1], (D(2026, 9, 2), 5.171))

    def test_ecb(self):
        self.assertEqual(yields.parse_ecb("KEY,FREQ,TIME_PERIOD,OBS_VALUE\nX,B,2026-10-01,3.59\nX,B,2026-09-30,3.6\n"), [(D(2026, 9, 30), 3.6), (D(2026, 10, 1), 3.59)])

    def test_wgb(self):
        r = yields.parse_wgb("**Main Indicators**\n10-Year Gov.Bond Yield: 3.490%\nSpread vs 2-Year Bond: 38.8 bp\nCentral Bank Rate: 2.65%")
        self.assertEqual((r["y10"], r["spread_2y_bp"], r["policy"]), (3.49, 38.8, 2.65)); self.assertIsNone(yields.parse_wgb("Just a moment..."))

class Stats(unittest.TestCase):
    def series(self, vals, start=D(2026, 1, 1)): return [(start + dt.timedelta(days=i), v) for i, v in enumerate(vals)]

    def test_changes_in_basis_points(self):
        s = self.series([4.0 + 0.01 * i for i in range(100)]); r = yields.stats(s, "X", "X", "t")
        self.assertAlmostEqual(r["chg_1d_bp"], 1.0); self.assertAlmostEqual(r["chg_1m_bp"], 21.0)

    def test_extreme_highest_since(self):
        s = self.series([5.0] + [4.0] * 200 + [4.5])
        self.assertIn("highest close since 2026-01-01", yields.extreme(s))

    def test_extreme_window_high(self):
        self.assertIn("highest close in at least", yields.extreme(self.series([4.0 + 0.001 * i for i in range(200)])))

    def test_no_extreme_in_the_middle(self):
        self.assertIsNone(yields.extreme(self.series([4, 5, 3, 4.5, 4.0, 4.2, 4.1] * 20)))

    def test_spread_on_shared_dates(self):
        a = self.series([5.0] * 30); b = self.series([4.0] * 30)[2:]
        sp = yields.spread(a, b); self.assertAlmostEqual(sp["bp"], 100.0)

    def test_snapshots_accumulate(self):
        old = lib.CACHE
        with tempfile.TemporaryDirectory() as d:
            lib.CACHE = d
            try:
                yields.snapshots(D(2026, 10, 1), {"DE": 3.4}); h = yields.snapshots(D(2026, 10, 6), {"DE": 3.5})
                self.assertEqual(h["DE"], [(D(2026, 10, 1), 3.4), (D(2026, 10, 6), 3.5)])
            finally: lib.CACHE = old

def prices(n, seed, base=100.0, beta=None, driver=None):
    r = random.Random(seed); c = [base]
    for i in range(1, n):
        shock = r.gauss(0, 0.01) + (beta * math.log(driver[i] / driver[i - 1]) if beta is not None else 0)
        c.append(c[-1] * math.exp(shock))
    t0 = int(dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc).timestamp())
    return [t0 + 86400 * i for i in range(n)], c

class Macro(unittest.TestCase):
    def test_positive_link_is_found_and_described(self):
        t, oil = prices(150, 1)
        _, stock = prices(150, 2, beta=1.0, driver=oil)
        q = {"CL=F": {"_t": t, "_c": oil}}
        res = macro.build({"X:AAA": (t, stock)}, {"X:AAA": "Alpha"}, q, {})
        self.assertGreater(res["matrix"][0]["corr"]["CL=F"], 0.5)
        self.assertIn("tends to rise when oil rises", res["text"]["X:AAA"])

    def test_negative_link_to_yields_from_series(self):
        t, _ = prices(150, 3)
        yl = [(dt.datetime.fromtimestamp(x, dt.timezone.utc).date(), 4.0 + 0.5 * math.sin(i / 7) + 0.01 * i) for i, x in enumerate(t)]
        ts, yv = macro.to_series(yl)
        _, stock = prices(150, 4, beta=-1.5, driver=yv)
        res = macro.build({"X:BBB": (t, stock)}, {"X:BBB": "Beta"}, {}, {"US 2Y": yl})
        self.assertLess(res["matrix"][0]["corr"]["US 2Y"], -0.3)
        self.assertIn("fall when US 2-year yields rise", res["text"]["X:BBB"])

    def test_no_link_text(self):
        t, oil = prices(150, 5); _, stock = prices(150, 6)
        res = macro.build({"X:CCC": (t, stock)}, {"X:CCC": "Gamma"}, {"CL=F": {"_t": t, "_c": oil}}, {})
        self.assertIn("no strong link", res["text"]["X:CCC"])

    def test_summary_needs_three_stocks(self):
        t, oil = prices(150, 7); q = {"CL=F": {"_t": t, "_c": oil}}
        series = {"S:%d" % i: (t, prices(150, 20 + i, beta=1.2, driver=oil)[1]) for i in range(5)}
        res = macro.build(series, {k: k for k in series}, q, {})
        self.assertTrue(any("tended to rise when oil rises" in x for x in res["summary"]))

if __name__ == "__main__":
    unittest.main()
