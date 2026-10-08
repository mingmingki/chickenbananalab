"""Prepare Oct5 + revised entry kernel comparison, NOT a deployable full runtime."""
import ast
import shutil
from pathlib import Path
from load_entry_kernel import FUNCTIONS

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT.parent/'autotrader-rc-structure-risk-ai-20261005'
DEST=Path('/private/tmp/autotrader-gpt-recovery-20261008/available-candidate')


def main():
    old=(BASE/'trader.py').read_text();src=(ROOT/'patch/trader.py').read_text()
    # The actual v4 Gemini budget is unchanged by this patch. Keep Oct5's own
    # budget in this older-runtime comparison: v4's unchanged helper requires
    # unavailable core_entry_timing and cannot qualify an Oct5 run_cycle.
    changed=FUNCTIONS-{'_core_ai_call_gate','_core_ai_budget_signature',
                       '_core_ai_position_fingerprint','_core_ai_discrete_indicator_state'}
    updated={n.name:ast.get_source_segment(src,n) for n in ast.parse(src).body
             if isinstance(n,ast.FunctionDef) and n.name in changed}
    lines=old.splitlines(keepends=True);seen=set()
    for n in reversed(ast.parse(old).body):
        if isinstance(n,ast.FunctionDef) and n.name in updated:
            # ast.get_source_segment omits decorator lines: preserve legacy writer guard.
            lines[n.lineno-1:n.end_lineno]=[updated[n.name]+'\n'];seen.add(n.name)
    old=''.join(lines)+'\n\n'+'\n\n'.join(v for k,v in updated.items() if k not in seen)
    constants=[ast.get_source_segment(src,n) for n in ast.parse(src).body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id in ('GPT_ENTRY_TIMEOUT_SECONDS','GPT_ENTRY_MAX_RETRIES',
            'GPT_APPROVED_ENTRY_MAX_PRICE_DRIFT_RATIO','CORE_AI_PRICE_MOVE_ATR',
            'CORE_AI_HELD_FALLBACK_MINUTES','CORE_AI_FLAT_FALLBACK_MINUTES') for t in n.targets)]
    (DEST/'trader.py').write_text(old+'\n'+'\n'.join(constants)+'\n')
    for name in ('adaptive_exit_engine','config','openai_analyzer','telegram_notify','gpt_shadow_log',
                 'core_entry_orders','core_entry_events','core_entry_policy'):
        shutil.copy2(ROOT/'patch'/f'{name}.py',DEST/f'{name}.py')
    p=DEST/'okx_client.py';old=(BASE/'okx_client.py').read_text();src=(ROOT/'patch/okx_client.py').read_text()
    def method(text):
        cls=next(n for n in ast.parse(text).body if isinstance(n,ast.ClassDef) and n.name=='OkxClient')
        return next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='fetch_order_status_by_client_id')
    m=method(old);lines=old.splitlines(keepends=True)
    lines[m.lineno-1:m.end_lineno]=['    '+ast.get_source_segment(src,method(src))+'\n'];p.write_text(''.join(lines))
    print('Prepared scoped Oct5 runtime comparison. Not complete v4/current LIVE.')


if __name__=='__main__': main()
