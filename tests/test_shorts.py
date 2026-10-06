import datetime as dt, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import lib, shorts

CSV = ('"Positie houder";"Naam van de emittent";"ISIN";"Netto Shortpositie";"Positiedatum"\n'
       '"Fund A";"Basic-Fit N.V.";"NL0011872650";"0.60";"2026-09-29 00:00:00"\n'
       '"Fund B";"Basic-Fit N.V.";"NL0011872650";"0.76";"2026-09-28 00:00:00"\n'
       '"Fund C";"Pharming Group N.V.";"NL0010391025";"1.49";"2026-10-05 00:00:00"\n')

class Shorts(unittest.TestCase):
    def setUp(self):
        self.old = lib.CACHE; lib.CACHE = tempfile.mkdtemp()
    def tearDown(self): lib.CACHE = self.old

    def test_parse_and_register(self):
        rows = shorts.parse_afm(CSV)
        self.assertEqual(len(rows), 3)
        r = shorts.register(rows, "basic-fit")
        self.assertEqual(r["holders"], 2); self.assertAlmostEqual(r["total_pct"], 1.36); self.assertEqual(r["top"][0]["holder"], "Fund B"); self.assertEqual(r["date"], "2026-09-29")

    def test_no_positions(self):
        r = shorts.register(shorts.parse_afm(CSV), "ASML")
        self.assertEqual(r["holders"], 0); self.assertEqual(r["top"], [])

    def test_daily_change_from_snapshot(self):
        s = shorts.register(shorts.parse_afm(CSV), "basic-fit")
        d1, d2 = dt.date(2026, 10, 5), dt.date(2026, 10, 6)
        b1 = shorts.register_block("NL", "AFM", s, "k", d1, "")
        self.assertIsNone(b1["chg_pct"])
        s2 = dict(s, total_pct=s["total_pct"] + 0.5, holders=3)
        b2 = shorts.register_block("NL", "AFM", s2, "k", d2, "")
        self.assertAlmostEqual(b2["chg_pct"], 0.5); self.assertEqual(b2["tone"], "dn")

    def test_us_block_change_vs_previous_session(self):
        out = {"offexchange": {"asof": "2026-10-05", "short_ratio": 0.5, "short_ratio_avg20": 0.45, "tone": "mid", "series": [0.45, 0.5]}, "short": None}
        b = shorts.us_block(out)
        self.assertAlmostEqual(b["chg_pts"], 5.0)
        self.assertIsNone(shorts.us_block({"offexchange": None, "short": None}))

if __name__ == "__main__": unittest.main()
