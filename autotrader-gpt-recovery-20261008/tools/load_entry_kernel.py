"""Offline harness: archived v4 functions with available Oct5 real dependencies.

Does not fabricate missing current production modules. This is scoped kernel
integration, not qualification of the complete current LIVE runtime.
"""
import ast
from pathlib import Path

FUNCTIONS = {
    '_set_core_entry_outcome', '_gpt_entry_gate', '_handle_new_entry', '_core_final_entry_validation',
    '_recover_unprotected_core_entry', '_reconcile_pending_core_entry', '_finalize_core_entry_result', '_execute_entry', '_execute_approved_entry_with_optional_reversal',
    '_approved_entry_still_valid_after_gpt', '_record_entry_gate_result',
    '_record_core_entry_attempt', '_fire_shadow_verification_async',
    '_run_shadow_verification', '_core_entry_risk_adjustment',
    '_core_entry_market_condition_blocks', '_core_adaptive_live_entry_decision',
    '_core_ai_call_gate', '_core_ai_budget_signature', '_core_ai_position_fingerprint',
    '_core_ai_discrete_indicator_state',
}

def load(trader, source):
    tree = ast.parse(Path(source).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name in FUNCTIONS]
    nodes += [n for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id in (
                  'GPT_ENTRY_TIMEOUT_SECONDS', 'GPT_ENTRY_MAX_RETRIES',
                  'GPT_APPROVED_ENTRY_MAX_PRICE_DRIFT_RATIO','CORE_AI_PRICE_MOVE_ATR',
                  'CORE_AI_FLAT_FALLBACK_MINUTES','CORE_AI_HELD_FALLBACK_MINUTES') for t in n.targets)]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), trader.__dict__)
    return trader
