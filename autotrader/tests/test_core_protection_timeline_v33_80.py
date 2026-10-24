from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import tempfile, unittest, json, os
F=Path(__file__).with_name("core_protection_timeline_v33_80.py")
if not F.exists():
 F=Path(__file__).resolve().parent.parent / "core_protection_timeline_v33_80.py"
spec=spec_from_file_location("timeline",F)
m=module_from_spec(spec); spec.loader.exec_module(m)
pos={"side":"long","position_id":"synthetic","entry_price":100.0,"contracts":3.0}
oco={"status":"VERIFIED","algo_id":"test-order-1","sl_price":95.0,"tp_price":108.0,"sz":3.0}
class Checks(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  m._LAST.clear()
  self.root=Path(self.tmp.name)
 def contents(self):
  return [json.loads(v) for v in (self.root/"core_protection_timeline_v33_80.jsonl").read_text().splitlines()]
 def test_first_verified(self):
  self.assertTrue(m.record_observation(self.root,"BTC",pos,oco))
  self.assertEqual(self.contents()[0]["live_sl"],95)
 def test_deduplicate(self):
  m.record_observation(self.root,"BTC",pos,oco)
  self.assertFalse(m.record_observation(self.root,"BTC",pos,oco))
  self.assertEqual(len(self.contents()),1)
 def test_stop_change(self):
  m.record_observation(self.root,"BTC",pos,oco)
  m.record_observation(self.root,"BTC",pos,{**oco,"sl_price":96})
  self.assertEqual([v["live_sl"] for v in self.contents()],[95,96])
 def test_partial(self):
  m.record_observation(self.root,"BTC",pos,oco)
  m.record_observation(self.root,"BTC",{**pos,"contracts":1.5},{**oco,"sz":1.5})
  self.assertEqual(self.contents()[-1]["live_contracts"],1.5)
 def test_unknown_if_invalid_quantity(self):
  self.assertEqual(m.normalize("BTC",pos,{**oco,"sz":9})["status"],"UNKNOWN")
 def test_flat_transition(self):
  m.record_observation(self.root,"BTC",pos,oco)
  m.record_observation(self.root,"BTC",None,{"status":"NOT_REQUIRED_FLAT"})
  self.assertEqual(self.contents()[-1]["status"],"NOT_REQUIRED_FLAT")
 def test_missing_transition(self):
  m.record_observation(self.root,"BTC",pos,oco)
  m.record_observation(self.root,"BTC",pos,{"status":"MISSING_CONFIRMED"})
  self.assertIsNone(self.contents()[-1]["live_sl"])
 def test_permissions(self):
  m.record_observation(self.root,"BTC",pos,oco)
  self.assertEqual(os.stat(self.root/"core_protection_timeline_v33_80.jsonl").st_mode & 0o777,0o600)
 def test_symbol_isolated(self):
  m.record_observation(self.root,"BTC",pos,oco)
  m.record_observation(self.root,"ETH",pos,oco)
  self.assertEqual(len(self.contents()),2)
 def test_no_extra_observation_data(self):
  m.record_observation(self.root,"BTC",pos,{**oco,"extra_field":"SHOULD_NOT_COPY"})
  self.assertNotIn("SHOULD_NOT_COPY",(self.root/"core_protection_timeline_v33_80.jsonl").read_text())
 def test_no_symlink(self):
  other=self.root/"destination.txt"; other.write_text("unchanged")
  (self.root/"core_protection_timeline_v33_80.jsonl").symlink_to(other)
  with self.assertRaises(ValueError):m.record_observation(self.root,"BTC",pos,oco)
  self.assertEqual(other.read_text(),"unchanged")
 def test_nan_invalid(self):
  self.assertEqual(m.normalize("BTC",pos,{**oco,"sl_price":float('nan')})["status"],"UNKNOWN")
if __name__=="__main__": unittest.main(verbosity=2)
