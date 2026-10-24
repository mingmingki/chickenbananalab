from pathlib import Path
from types import SimpleNamespace
import subprocess
import web_app


def test_candidate_legacy_sl_only_requires_both_missing_target_and_adaptive_hash():
    assert hasattr(web_app, "_candidate_c_legacy_sl_only")
    f = web_app._candidate_c_legacy_sl_only
    assert f(SimpleNamespace(target_price=None, adaptive_exit_policy_hash=None)) is True
    assert f(SimpleNamespace(target_price=130.0, adaptive_exit_policy_hash=None)) is False
    assert f(SimpleNamespace(target_price=None, adaptive_exit_policy_hash="abc")) is False


def test_dashboard_distinguishes_legacy_missing_tp_from_new_missing_tp():
    html = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "TP 없음 (기존 포지션 · SL 보호 정상)" in html
    assert "legacy_sl_only" in html
    assert "TP 확인 안 됨" in html


def test_dashboard_separates_gpt_reduce_proposal_from_execution_gate():
    path = Path(__file__).resolve().parents[1] / "templates/dashboard.html"
    script = r'''const fs=require('node:fs'),vm=require('node:vm'),a=require('node:assert/strict');
const text=fs.readFileSync(process.argv[1],'utf8');
const helpers=text.slice(text.indexOf('function ccEscape('),text.indexOf('const CC_SETTING_FIELDS ='));
const begin=text.indexOf('const posAiEl = document.getElementById("position-ai-"');
const end=text.indexOf('\n  });',begin);
const body=text.slice(begin,end);
const el={style:{},innerHTML:'',querySelector:()=>null};
const context={document:{getElementById:()=>el},id:'BTC',pos:null,info:{
 position_ai_review:{action:'REDUCE_50',reduce_fraction:.4,confidence:.9,reasoning:'hold weaker'},
 reduce_v2:{initial_contracts:10,current_contracts:8,cumulative_reduced_ratio:.2,reduce_stage:1,last_block_reason:'stage1_5m_or_ai_conditions'},
 reduce_v2_diagnostics:{gemini_assessment:'thesis_intact',two_macd_weakening:false,two_rsi_adverse:false},
 live_position:{contracts:8}
}};
vm.createContext(context);vm.runInContext(helpers,context);vm.runInContext(body,context);
a.ok(el.innerHTML.includes('AI 관리 제안:'));
a.ok(el.innerHTML.includes('40.0%')); // Proposal amount.
a.ok(el.innerHTML.includes('실행 게이트: 차단'));
a.ok(el.innerHTML.includes('Gemini thesis_intact'));
a.ok(el.innerHTML.includes('누적 실제 감축 20%')); // Actual fill history remains separate.
a.ok(!el.innerHTML.includes('누적 실제 감축 40%'));
'''
    subprocess.run(["node", "-e", script, str(path)], check=True, capture_output=True, text=True)


def test_core_fixed_mode_summary_states_new_entry_margin_and_notional():
    html = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "신규진입 고정 증거금" in html
    assert "예상 명목" in html
