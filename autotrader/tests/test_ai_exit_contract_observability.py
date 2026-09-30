import json
import ai_exit_plan_audit
import analysis_report


def _write(user_dir, rows):
    p=user_dir/'ai_exit_plan_audit.jsonl'
    p.write_text(''.join(json.dumps(r)+'\n' for r in rows))


def test_summary_separates_legacy_from_contract_validated(tmp_path):
    _write(tmp_path,[
      {'engine':'CORE','symbol':'PI/USDT:USDT','side':'long','result':'ai_price_direction_invalid','gpt_exit_plan_decision':'approve'},
      {'engine':'CORE','symbol':'BTC/USDT:USDT','side':'long','result':'ai_exit_plan_applied','gpt_exit_plan_decision':'revise','contract_enforced':True,'exit_plan_contract_reason':'gpt_revision_contract_ok'},
    ])
    s=ai_exit_plan_audit.summarize(str(tmp_path),limit=10)
    assert s['legacy_pre_contract_count']==1
    assert s['contract_validated_count']==1
    assert s['contract_current']['ai_applied']==1
    assert s['contract_current']['adaptive_fallback']==0
    assert s['contract_current']['gpt']['revise']==1

def test_report_labels_legacy_and_current_contract_samples(tmp_path):
    _write(tmp_path,[
      {'engine':'CORE','symbol':'PI/USDT:USDT','side':'long','result':'ai_price_direction_invalid','gpt_exit_plan_decision':'approve'},
    ])
    data=ai_exit_plan_audit.summarize(str(tmp_path),limit=10)
    snap={'release':'r','period':'all','coverage':{},'overall':{},'symbols':[],'sides':[],
          'top_positive':[],'top_negative':[],'tf':[],'confidence':[],'self_learning':{},
          'review':{},'settings':{},'positions':[],'system_issues':{},'ai_exit_observability':data}
    text=analysis_report.build_report(snap)['text']
    assert 'contract 검증 표본 0건' in text
    assert 'legacy pre-contract 1건' in text
    assert '현재 contract: AI 적용 0건' in text
