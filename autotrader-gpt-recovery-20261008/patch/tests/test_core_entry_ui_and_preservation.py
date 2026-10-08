import ast
import importlib.util
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT.parent/'autotrader-rc-structure-risk-ai-20261005'


def test_ui_integration_retains_legacy_recent_and_presents_phase_history():
    spec=importlib.util.spec_from_file_location('ui_integration',ROOT/'tools/ui_integration.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    api=module.api_source((BASE/'web_app.py').read_text())
    compile(api,'candidate_web_app.py','exec')
    assert '"events": __import__("core_entry_events").recent(ctx.dir, limit=100)' in api
    html=module.dashboard_source((BASE/'templates/dashboard.html').read_text())
    assert 'coreEntryStatus.describe(entryAttempt)' in html
    assert 'coreEntryStatus.render(document,r)' in html
    assert '/static/core_entry_status.js' in html
    assert 'data.events?.length' in html


def test_ui_does_not_confuse_approval_with_fill_or_interpret_html():
    script='''const s=require(process.argv[1]);
const assert=require('node:assert/strict');
const doc={createElement:(tag)=>({tag,children:[],appendChild(x){this.children.push(x)}})};
for(const status of Object.keys(s.labels)) {
 const row={status,decision_id:'d1',gpt_raw_result:'TIMEOUT',gate_processing:'TIMEOUT_BYPASS',reason:'<script>attack</script>'};
 const tr=s.render(doc,row);
 assert.equal(tr.children[4].innerHTML,undefined);
 assert.ok(tr.children[4].textContent.includes('<script>attack</script>'));
 assert.ok(s.describe(row).includes('decision_id=d1'));
}
assert.ok(s.describe({status:'GPT_APPROVED'}).includes('주문 전'));
assert.ok(s.describe({status:'TIMEOUT_BYPASS'}).includes('GPT 응답 없음 — 설정에 따른 예외 통과'));
'''
    subprocess.run(['node','-e',script,str(ROOT/'patch/static/core_entry_status.js')],check=True,capture_output=True)


def test_cost_patch_preserves_gemini_budget_and_held_gpt_function_bodies():
    old=ast.parse((ROOT.parent/'autotrader-handoff-20261008/patch/trader.py').read_text())
    new=ast.parse((ROOT/'patch/trader.py').read_text())
    def functions(tree):
        return {n.name:ast.dump(n,include_attributes=False) for n in tree.body if isinstance(n,ast.FunctionDef)}
    before,after=functions(old),functions(new)
    # No new cooldown suppression: these actual v4 predicates are bytecode-equivalent.
    for name in ('_core_ai_call_gate','_core_ai_budget_signature','_core_ai_position_fingerprint',
                 '_handle_position_ai_review'):
        assert after[name]==before[name]
    assert after['_core_ai_call_gate']==before['_core_ai_call_gate']


def test_account_migration_preserves_other_settings_and_reads_effective_policy(tmp_path):
    spec=importlib.util.spec_from_file_location('account_policy',ROOT/'tools/active_account_policy.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    original='OTHER_SETTING=kept\nLEVERAGE=5\nCORE_PAID_SHADOW_ENABLED=true\nCORE_GPT_ENTRY_TIMEOUT_BYPASS=false\n'
    (tmp_path/'.env').write_text(module.updated(original))
    import config
    cfg=config.UserConfig(str(tmp_path))
    assert cfg.GPT_ENTRY_GATE_ENABLED and cfg.CORE_GPT_ENTRY_TIMEOUT_BYPASS
    assert not cfg.CORE_PAID_SHADOW_ENABLED
    assert cfg._env['OTHER_SETTING']=='kept' and cfg.LEVERAGE==5


def test_new_early_setup_can_trigger_gemini_immediately_before_cooldown_end():
    import datetime
    import trader
    from test_core_entry_pipeline import State,SYMBOL
    now=datetime.datetime.now(datetime.timezone.utc)
    state=State()
    first=trader._core_ai_call_gate(state,SYMBOL,{}, {},{},None,now=now,
        entry_timing={'status':'ok','phase':'early','event_key':'setup-1'})
    state.symbols[SYMBOL].update(core_ai_budget=first['next_memory'],reentry_until=(now+datetime.timedelta(seconds=1)).timestamp())
    next_gate=trader._core_ai_call_gate(state,SYMBOL,{}, {},{},None,now=now,
        entry_timing={'status':'ok','phase':'early','event_key':'setup-2'})
    assert next_gate['call_ai'] and next_gate['reason']=='entry_setup_changed'
