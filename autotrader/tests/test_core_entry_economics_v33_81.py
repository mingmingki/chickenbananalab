import importlib.util
import math
import pathlib
import unittest
module=pathlib.Path(__file__).with_name("core_entry_economics_v33_81.py")
if not module.exists():
 module=pathlib.Path(__file__).resolve().parent.parent/"core_entry_economics_v33_81.py"
spec=importlib.util.spec_from_file_location("mod",module)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
class FirstTargetEconomicTests(unittest.TestCase):
 def plan(self,side="long"):
  if side=="long":return dict(action="long",exit_plan=dict(stop_loss_price=95,take_profit_1_price=104,take_profit_2_price=111))
  return dict(action="short",exit_plan=dict(stop_loss_price=105,take_profit_1_price=96,take_profit_2_price=89))
 def context(self,fee=.001):
  return dict(entry_price=100,estimated_roundtrip_cost_rate=fee)
 def test_break_even_formula_base(self):
  e=mod.economics(self.plan(),self.context())
  self.assertEqual(e["status"],"known")
  self.assertAlmostEqual(e["base"]["tp1_breakeven_win_rate"],5.1/9)
  self.assertAlmostEqual(e["base"]["tp2_breakeven_win_rate"],5.1/16)
 def test_long_short_symmetric(self):
  l=mod.economics(self.plan(),self.context())
  s=mod.economics(self.plan("short"),self.context())
  self.assertEqual(l["base"]["tp1_breakeven_win_rate"],s["base"]["tp1_breakeven_win_rate"])
 def test_cost_stress_requires_more_wins(self):
  e=mod.economics(self.plan(),self.context())
  self.assertLess(e["base"]["tp1_breakeven_win_rate"],e["stress_15bps"]["tp1_breakeven_win_rate"])
 def test_stress_tp1_requires_more_wins_than_tp2(self):
  e=mod.economics(self.plan(),self.context())
  self.assertGreater(e["stress_15bps"]["tp1_breakeven_win_rate"],e["stress_15bps"]["tp2_breakeven_win_rate"])
 def test_unknown_fails_closed(self):
  e=mod.economics(dict(action="long"),self.context())
  self.assertEqual(e["status"],"unknown")
 def test_negative_expected_reward_requires_over_hundred_percent(self):
  e=mod.economics(self.plan(),self.context(fee=.06))
  self.assertGreater(e["base"]["tp1_breakeven_win_rate"],1)
 def test_prompt_includes_first_target_critical_warning(self):
  s=mod.prompt(symbol="ETH/USDT:USDT",frames=["5m"],position=None,
   decision=self.plan(),summary="verified",short_context="",contract=self.context())
  self.assertIn("tp1_breakeven_win_rate",s)
  self.assertIn("승률 추정치",s)
  self.assertNotIn("보장 수익",s)
if __name__=="__main__":unittest.main(verbosity=2)
