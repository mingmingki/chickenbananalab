"""Offline synthetic tests for the v33-78 profitability research gate."""
import importlib.util
import pathlib
import unittest

SPEC = importlib.util.spec_from_file_location(
    "profitability_gate", pathlib.Path(__file__).with_name("chickenbanana_v33_78_profitability_gate.py")
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class ProfitGateTests(unittest.TestCase):
    def test_stats_calculates_closed_pnl(self):
        s = MOD.stats([{"net": 10}, {"net": -5}, {"net": -2}])
        self.assertEqual(s["n"], 3)
        self.assertEqual(s["net_usdt"], 3)
        self.assertEqual(s["pf"], round(10 / 7, 6))
        self.assertEqual(s["wins"], 1)

    def test_all_three_cost_stress_periods_required(self):
        positive = {p: {"n": 24, "net": 1.5, "pf": 1.05}
                    for p in ("early", "middle", "late")}
        self.assertTrue(MOD.passes_three_periods_and_stress(positive))
        for p in positive:
            copy = {k: dict(v) for k, v in positive.items()}
            copy[p]["net"] = -0.1
            self.assertFalse(MOD.passes_three_periods_and_stress(copy))

    def test_missing_period_and_small_sample_fail(self):
        positive = {p: {"n": 30, "net": 1, "pf": 1.1}
                    for p in ("early", "middle", "late")}
        del positive["middle"]
        self.assertFalse(MOD.passes_three_periods_and_stress(positive))
        positive["middle"] = {"n": 3, "net": 1, "pf": 1.2}
        self.assertFalse(MOD.passes_three_periods_and_stress(positive))

    def test_breakeven_is_not_profit(self):
        rows = {p: {"n": 30, "net": 0.0, "pf": 1}
                for p in ("early", "middle", "late")}
        self.assertFalse(MOD.passes_three_periods_and_stress(rows))

    def test_missing_or_null_pf_fails(self):
        rows = {p: {"n": 30, "net": 1, "pf": 1.2}
                for p in ("early", "middle", "late")}
        rows["late"]["pf"] = None
        self.assertFalse(MOD.passes_three_periods_and_stress(rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
