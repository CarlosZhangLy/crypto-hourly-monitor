"""Offline checks for hourly conditional plan calculations and email formatting."""
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from hourly_plans import CORE, build_plans, markdown
from risk_calc import plan as strict_plan
from email_templates import render_markdown

def snapshot():
    stamp = "2026-10-31T19:34:10+08:00"
    periods = {}
    for period in ("5m", "15m", "1h", "4h", "1d"):
        periods[period] = dict(
            closed_candle_close=100, ema20=99.5, ema50=99, atr14=0.5,
            volume_ratio_vs_prior20=1.2, recent_swing_high=101,
            recent_swing_low=99, structure_hint="HH / HL", bb_expanding=True,
            bar_time_bj=stamp,
        )
    return {
        "generated_beijing": stamp, "source": "OKX USDT SWAP", "errors": {},
        "symbols": {
            symbol+"USDT": {
                "last_price": 100, "ticker_time_bj": stamp,
                "intervals": deepcopy(periods),
                "five_minute_structure": {"confirmed": False, "side": "long"},
            } for symbol in CORE
        }
    }

def specs():
    return {
        symbol+"-USDT-SWAP": {
            "instId": symbol+"-USDT-SWAP", "state": "live",
            "ctType": "linear", "settleCcy": "USDT",
            "tickSz": "0.01", "ctVal": "0.01",
            "lotSz": "0.01", "minSz": "0.01",
        } for symbol in CORE
    }

class TestHourlyPlans(unittest.TestCase):
    def test_plans_have_verified_numbers(self):
        data = snapshot()
        plans = build_plans(data, specs())
        self.assertEqual(len(plans), 2)
        for p in plans:
            self.assertEqual(p["status"], "待触发：当前不可直接委托")
            self.assertGreater(float(p["entry"]), 100)
            self.assertLess(float(p["stop"]), float(p["entry"]))
            self.assertLess(float(p["entry"]), float(p["tp1"]))
            self.assertLess(float(p["tp1"]), float(p["tp2"]))
            self.assertLessEqual(p["risk"], 0.85 + 0.0001)
            self.assertLessEqual(p["margin"], 25 + 0.0001)
            self.assertLessEqual(p["notional"], 75 + 0.0001)
            self.assertIn(p["leverage"], [2, 3, 5, 7, 10])
            self.assertGreaterEqual(p["rr1"], 1.8)
            self.assertTrue(0 < p["score"] < 100)

    def test_no_invented_contract_if_specs_missing(self):
        self.assertEqual(build_plans(snapshot(), {}), [])

    def test_dynamic_ten_x_uses_small_margin_not_big_position(self):
        data = snapshot()
        for sym in CORE:
            x = data["symbols"][sym + "USDT"]
            x["five_minute_structure"] = {
                "confirmed": True, "side": "long", "regained_level": False,
                "bars_since_break": 2, "break_level": 99.95
            }
            x["intervals"]["5m"]["atr14"] = 0.05
            x["intervals"]["5m"]["recent_swing_high"] = 100.2
            x["intervals"]["5m"]["recent_swing_low"] = 100.05
            x["intervals"]["5m"]["volume_ratio_vs_prior20"] = 2.0
            x["intervals"]["15m"]["volume_ratio_vs_prior20"] = 2.0
            x["intervals"]["15m"]["recent_swing_high"] = 103.0
            x["intervals"]["1h"]["recent_swing_high"] = 103.0
        plans = build_plans(data, specs())
        self.assertEqual(len(plans), 2)
        self.assertEqual(plans[0]["leverage"], 10)
        self.assertLessEqual(plans[0]["notional"], 75.0001)
        self.assertLessEqual(plans[0]["margin"], 8.0001)
        self.assertLessEqual(plans[0]["risk"], 0.85001)
        self.assertLessEqual(sum(p["margin"] for p in plans), 50.0001)
        self.assertLessEqual(sum(p["risk"] for p in plans), 1.70001)
        self.assertIn("逐仓10倍", markdown(plans, datetime.fromisoformat(data["generated_beijing"])))
        strict = strict_plan(data["symbols"]["BTCUSDT"], specs()["BTC-USDT-SWAP"])
        self.assertIsNotNone(strict)
        self.assertEqual(strict["leverage"], 10)
        self.assertLessEqual(strict["notional"], 75.0001)
        self.assertLessEqual(strict["margin"], 8.0001)
        self.assertLessEqual(strict["risk"], 0.85001)

    def test_bigger_stop_forces_reduced_exposure(self):
        data = snapshot()
        proposals = build_plans(data, specs())
        self.assertTrue(proposals)
        for p in proposals:
            self.assertLessEqual(p["margin"], 25)
            self.assertLessEqual(p["risk"], 0.85)
        self.assertIn("2—10倍", markdown(proposals, datetime.fromisoformat(data["generated_beijing"])))

    def test_bearish_case_and_html(self):
        data = snapshot()
        for s in CORE:
            x = data["symbols"][s+"USDT"]
            for k in ("5m","15m","1h","4h","1d"):
                v = x["intervals"][k]
                v["ema20"], v["ema50"] = 100.5, 101
                v["structure_hint"] = "LH / LL"
        plans = build_plans(data, specs())
        self.assertGreaterEqual(len(plans), 1)
        self.assertEqual(plans[0]["direction"], "空")
        self.assertLess(float(plans[0]["entry"]), 100)
        self.assertLess(float(plans[0]["tp1"]), float(plans[0]["entry"]))
        html = render_markdown(markdown(plans, datetime.fromisoformat(data["generated_beijing"])))
        self.assertIn("<table", html)
        self.assertIn("止损", html)
        self.assertIn("推荐度", html)

if __name__ == "__main__":
    unittest.main()
