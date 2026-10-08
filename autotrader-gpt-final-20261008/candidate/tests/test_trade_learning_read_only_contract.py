from pathlib import Path

ANALYSIS_MODULES=('trade_learning_lifecycle.py','trade_learning_features.py','trade_pattern_analysis.py','strategy_learning.py','ai_strategy_review.py','ai_strategy_review_log.py','trade_learning_scheduler.py','trade_learning_cache.py')

def test_analysis_stack_has_no_execution_imports_or_mutation_calls():
    forbidden=('import okx_client','place_order','close_position_now','set_leverage','save_env(')
    for module in ANALYSIS_MODULES:
        text=Path(module).read_text()
        hits=[token for token in forbidden if token in text]
        assert not hits, f'{module}: {hits}'

def test_analysis_stack_does_not_reference_live_config_write_files():
    for module in ANALYSIS_MODULES:
        text=Path(module).read_text()
        assert '.env' not in text
        assert 'candidate_c_config_active.json' not in text
