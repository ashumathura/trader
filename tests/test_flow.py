import datetime as dt, io, json, math, os, random, sys, tempfile, unittest, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline"))
import flow, context, lib

def bars(n=160, seed=5, drift=0.0005, vol=1000):
    r = random.Random(seed); c = [50.0]
    for _ in range(n - 1): c.append(c[-1] * math.exp(r.gauss(drift, 0.01)))
    t0 = int(dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc).timestamp())
    return {"t": [t0 + 86400 * i for i in range(n)], "o": c, "h": [x * 1.005 for x in c], "l": [x * 0.995 for x in c], "c": c, "v": [vol + r.randint(0, 200) for _ in c]}

class VolumeFlow(unittest.TestCase):
    def test_mfi_extremes(self):
        up = [float(i + 10) for i in range(40)]
        self.assertEqual(flow.mfi(up, up, up, [100] * 40), 100.0)
        dn = up[::-1]; self.assertLess(flow.mfi(dn, dn, dn, [100] * 40), 1)

    def test_accumulation_vs_distribution_days(self):
        c = [100.0]; v = [1000.0]
        for i in range(30):
            c.append(c[-1] * (1.01 if i % 2 == 0 else 1.0)); v.append(v[-1] + 100)  # rising prices on rising volume
        acc, dist = flow.accumulation_days(c, v, n=25); self.assertGreater(acc, dist)

    def test_volume_profile_finds_busy_price(self):
        n = 130; h = [101.0] * n; l = [99.0] * n; v = [100.0] * n
        for i in range(60, 90): h[i], l[i], v[i] = 80.5, 79.5, 1000.0  # heavy trading near 80
        p = flow.volume_profile(h, l, v, lookback=n)
        self.assertLess(abs(p["poc"] - 80.0), 2.0); self.assertTrue(p["va_low"] <= p["poc"] <= p["va_high"])

    def test_vwap_weights_by_volume(self):
        h = [11, 11]; l = [9, 9]; c = [10, 10]; self.assertAlmostEqual(flow.vwap(h, l, c, [1, 3], 2), 10.0)

    def test_volume_flow_and_verdict_shapes(self):
        b = bars(); f = flow.volume_flow(b, {"obv": "Rising", "cmf": 0.1})
        self.assertIn("profile", f); vd = flow.verdict(f, b["c"][-1])
        self.assertIn(vd["tone"], ("up", "dn", "mid")); self.assertTrue(vd["text"])

    def test_verdict_accumulation_when_everything_positive(self):
        f = {"volume": {"ratio": 1.0}, "obv": "Rising", "cmf": 0.2, "mfi": 60, "updown_ratio": 1.6, "acc_days": 9, "dist_days": 2, "big_days": 0, "big_up": 0, "big_down": 0,
             "support_node": {"price": 48.0}, "vwap50": 47.0, "resistance_node": None}
        self.assertEqual(flow.verdict(f, 50)["tone"], "up")

class Finra(unittest.TestCase):
    def test_parse_and_offexchange(self):
        txt = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n20261005|NFLX|4000000.5|10|10000000.0|B,Q,N\n20261005"
        self.assertEqual(flow.parse_finra(txt)["NFLX"], (4000000.5, 10000000.0))
        b = bars(40); days = []
        d = dt.date(2026, 2, 9)
        for i in range(24):
            day = dt.date(2026, 1, 2) + dt.timedelta(days=15 + i); days.append((day, {"CNMS": {"NFLX": (400.0 + i, 1000.0)}}))
        days = sorted(days, reverse=True)
        cons = {dt.datetime.fromtimestamp(t, dt.timezone.utc).date(): v for t, v in zip(b["t"], b["v"])}
        out = flow.offexchange("NFLX", False, days, b)
        self.assertIsNotNone(out); self.assertTrue(0 <= out["short_ratio"] <= 1)

    def test_too_little_data_gives_none(self):
        self.assertIsNone(flow.offexchange("NFLX", False, [(dt.date(2026, 1, 5), {"CNMS": {"NFLX": (1.0, 2.0)}})], bars(40)))

class ShortInterest(unittest.TestCase):
    def test_parse(self):
        js = json.dumps({"data": {"shortInterestTable": {"rows": [{"settlementDate": "09/15/2026", "interest": "94,187,131", "avgDailyShareVolume": "26,198,057", "daysToCover": 3.59}]}}})
        r = flow.parse_short_interest(js)[0]; self.assertEqual(r["shares"], 94187131.0); self.assertEqual(r["date"], "2026-09-15")
        self.assertIsNone(flow.parse_short_interest('{"data":null}'))

    def test_insiders(self):
        js = json.dumps({"data": {"transactionTable": {"table": {"rows": [
            {"lastDate": "09/30/2026", "relation": "CEO", "transactionType": "Sell", "sharesTraded": "10,000", "lastPrice": "$50"},
            {"lastDate": "01/01/2020", "relation": "CFO", "transactionType": "Buy", "sharesTraded": "1", "lastPrice": "$1"}]}}}})
        r = flow.parse_insiders(js, dt.date(2026, 10, 6)); self.assertEqual((r["buys"], r["sells"]), (0, 1)); self.assertEqual(r["sell_value"], 500000.0)

class UkShorts(unittest.TestCase):
    def make_xlsx(self, rows):
        shared = []; sheet = []
        for ri, row in enumerate(rows, 1):
            cells = []
            for ci, v in enumerate(row):
                ref = "ABCDE"[ci] + str(ri)
                if isinstance(v, str): shared.append(v); cells.append('<c r="%s" t="s"><v>%d</v></c>' % (ref, len(shared) - 1))
                else: cells.append('<c r="%s"><v>%s</v></c>' % (ref, v))
            sheet.append("<row r=\"%d\">%s</row>" % (ri, "".join(cells)))
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/sharedStrings.xml", "<sst %s>%s</sst>" % (ns, "".join("<si><t>%s</t></si>" % s for s in shared)))
            z.writestr("xl/worksheets/sheet1.xml", "<worksheet %s><sheetData>%s</sheetData></worksheet>" % (ns, "".join(sheet)))
        return buf.getvalue()

    def test_sums_positions_for_issuer(self):
        blob = self.make_xlsx([["Position Holder", "Name of Share Issuer", "ISIN", "Net Short Position (%)", "Position Date"],
                               ["Fund A", "LLOYDS BANKING GROUP PLC", "GB0008706128", 1.2, "2026-10-01"],
                               ["Fund B", "LLOYDS BANKING GROUP PLC", "GB0008706128", 0.6, "2026-09-30"], ["Fund C", "OTHER PLC", "X", 5.0, "2026-10-01"]])
        r = flow.fca_shorts(flow.read_xlsx(blob), "Lloyds Banking Group")
        self.assertEqual(r["holders"], 2); self.assertAlmostEqual(r["total_pct"], 1.8)

    def test_only_latest_position_per_holder_counts(self):
        blob = self.make_xlsx([["Position Holder", "Name of Share Issuer", "ISIN", "Net Short Position (%)", "Position Date"],
                               ["Fund A", "LLOYDS BANKING GROUP PLC", "G", 1.5, 46000], ["Fund A", "LLOYDS BANKING GROUP PLC", "G", 0.9, 46010],
                               ["Fund B", "LLOYDS BANKING GROUP PLC", "G", 0.7, 46000], ["Fund B", "LLOYDS BANKING GROUP PLC", "G", 0, 46012]])
        r = flow.fca_shorts(flow.read_xlsx(blob), "lloyds")
        self.assertEqual(r["holders"], 1); self.assertAlmostEqual(r["total_pct"], 0.9)
        self.assertRegex(r["date"], r"^\d{4}-\d\d-\d\d$")

    def test_positions_below_disclosure_line_are_ignored(self):
        blob = self.make_xlsx([["Position Holder", "Name of Share Issuer", "Net Short Position (%)"], ["A", "LLOYDS BANKING GROUP", 0.49]])
        self.assertEqual(flow.fca_shorts(flow.read_xlsx(blob), "lloyds")["holders"], 0)

    def test_unknown_layout_gives_none(self):
        self.assertIsNone(flow.fca_shorts([{"A": "foo"}], "x"))

def chain(spot=100.0, call_vol=1000, put_vol=500, iv=0.30):
    today = dt.date(2026, 10, 6); e = today + dt.timedelta(days=30); out = []
    for k in range(-5, 6):
        strike = spot + k * 5
        for ty in "CP":
            out.append({"exp": e, "type": ty, "strike": strike, "mid": max(0.5, abs(spot - strike) * 0.3 + 3), "iv": iv + (0.02 if ty == "P" else 0), "oi": 1000 + (500 if strike == 100 else 0),
                        "vol": call_vol // 11 if ty == "C" else put_vol // 11, "delta": (0.5 - k * 0.08) if ty == "C" else (-0.5 - k * 0.08)})
    return today, out

class Options(unittest.TestCase):
    def test_parse_occ(self):
        js = {"data": {"current_price": 100.0, "options": [{"option": "NFLX261016C00067500", "bid": 1.0, "ask": 1.2, "iv": 0.4, "open_interest": 10, "volume": 5, "delta": 0.5}]}}
        spot, ch = flow.parse_chain(js); self.assertEqual(ch[0]["strike"], 67.5); self.assertEqual(ch[0]["type"], "C"); self.assertEqual(ch[0]["exp"], dt.date(2026, 10, 16))

    def test_bullish_when_call_heavy(self):
        today, ch = chain(call_vol=4000, put_vol=1000); r = flow.options_summary(100.0, ch, today, hv=0.25)
        self.assertEqual(r["label"], "Bullish"); self.assertLess(r["pc_volume"], 0.7)

    def test_bearish_when_put_heavy(self):
        today, ch = chain(call_vol=1000, put_vol=4000); self.assertEqual(flow.options_summary(100.0, ch, today)["label"], "Bearish")

    def test_thin_activity_is_not_called_directional(self):
        today, ch = chain(call_vol=40, put_vol=20); self.assertEqual(flow.options_summary(100.0, ch, today)["label"], "Thin activity")

    def test_iv_expected_move_and_ratio(self):
        today, ch = chain(iv=0.30); r = flow.options_summary(100.0, ch, today, hv=0.20)
        self.assertAlmostEqual(r["iv"], 0.31, delta=0.02); self.assertAlmostEqual(r["expected_move_pct"], r["iv"] * math.sqrt(30 / 365) * 100, places=6); self.assertGreater(r["iv_vs_hv"], 1.3)

    def test_wide_spread_quotes_do_not_set_iv(self):
        today, ch = chain(iv=0.30)
        for c in ch: c["spread"] = 0.8  # illiquid quotes
        r = flow.options_summary(100.0, ch, today)
        self.assertNotIn("iv", r); self.assertNotIn("expected_move_pct", r)

    def test_max_pain_is_a_listed_strike_with_heaviest_oi(self):
        today, ch = chain(); r = flow.options_summary(100.0, ch, today); self.assertEqual(r["max_pain"], 100.0)

    def test_unusual_activity_needs_volume_over_oi(self):
        today, ch = chain(); ch[0]["vol"] = 5000; ch[0]["oi"] = 100
        self.assertEqual(len(flow.options_summary(100.0, ch, today)["unusual"]), 1)

    def test_iv_rank_builds_then_ranks(self):
        old = lib.CACHE
        with tempfile.TemporaryDirectory() as d:
            lib.CACHE = d
            try:
                for i in range(19): flow.iv_rank("XX", 0.2 + i * 0.01, dt.date(2026, 9, 1) + dt.timedelta(days=i))
                self.assertEqual(flow.iv_rank("XXB", 0.3, dt.date(2026, 10, 1))["status"], "building")
                r = flow.iv_rank("XX", 0.5, dt.date(2026, 9, 25)); self.assertEqual(r["status"], "ok"); self.assertEqual(r["rank"], 100.0)
            finally: lib.CACHE = old

class Context(unittest.TestCase):
    def test_correlation_and_beta(self):
        a = bars(150, seed=1); b = {"t": a["t"], "c": [x * 2 for x in a["c"]]}
        self.assertAlmostEqual(context.correlation((a["t"], a["c"]), (b["t"], b["c"])), 1.0, places=6)
        self.assertAlmostEqual(context.beta((a["t"], a["c"]), (b["t"], b["c"])), 1.0, places=6)

    def test_lean_labels(self):
        self.assertEqual(context.lean(8), "Bullish"); self.assertEqual(context.lean(5), "Neutral, leaning bullish"); self.assertEqual(context.lean(2), "Bearish")

    def test_risk_levels(self):
        s = {"price": {"last": 10.0, "from_hi52_pct": -50}, "tech": {"atr_pct": 5.0}, "currency": "USD", "exchange": "OTC Markets",
             "flow": {"volume_flow": {"volume": {"avg20": 1000}}}, "regime": {"probs": {"bear": 0.7}, "current": "bear"}}
        ev = [{"kind": "earnings", "days": 3, "date": "2026-10-09", "approx": False}]
        lv = {r["factor"]: r["level"] for r in context.risk_table(s, ev, [], None)}
        self.assertEqual(lv["Earnings proximity"], "red"); self.assertEqual(lv["Liquidity / gap risk"], "red"); self.assertEqual(lv["Volatility"], "red"); self.assertEqual(lv["Regime risk"], "red")

    def test_session_range_contains_last(self):
        s = {"price": {"last": 100.0}, "tech": {"atr": 2.0}, "pivots": {"s1": 98.0, "r1": 102.0}}
        r = context.session_range(s); self.assertTrue(r["atr_lo"] < 100 < r["atr_hi"])

if __name__ == "__main__":
    unittest.main()
