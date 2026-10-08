from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MODULES=['learning_policy.py','learning_state.py','learning_shadow.py','learning_adapter.py','learning_control.py']
FORBIDDEN=['create_order','_execute_entry','_execute_close','set_leverage','risk_manager','candidate_c_','.env']

def test_learning_modules_have_no_execution_or_risk_authority():
    for name in MODULES:
        text=(ROOT/name).read_text()
        for token in FORBIDDEN:
            assert token not in text, f'{name} contains forbidden authority token {token}'
