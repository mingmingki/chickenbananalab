from pathlib import Path
import subprocess

def test_partial_tp_reason_has_human_readable_label():
    path = Path(__file__).resolve().parents[1] / "templates/dashboard.html"
    script = r'''const fs=require('node:fs'),vm=require('node:vm'),a=require('node:assert/strict');
const text=fs.readFileSync(process.argv[1],'utf8');
const start=text.indexOf('const CC_ACTION_REASON_LABELS =');
const end=text.indexOf('function candidateModeText(',start);
const context={};vm.createContext(context);vm.runInContext(text.slice(start,end),context);
const label=context.ccActionReasonText('partial_take_profit_2r');
a.ok(label.includes('+2R'));a.ok(label.includes('부분익절'));
a.ok(!label.includes('25%')); // Risk-based fractions vary; the event must not invent a fill fraction.
a.ok(context.ccActionReasonText('PARTIAL_TP_2R_DUST_SAFE_FULL_EXIT').includes('전량정리'));
a.equal(context.ccActionReasonText(''), '');
'''
    subprocess.run(["node", "-e", script, str(path)], check=True, capture_output=True, text=True)
