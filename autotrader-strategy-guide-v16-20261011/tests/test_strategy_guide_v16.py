import importlib.util
import json
from types import SimpleNamespace

import pytest
from config import UserConfig


def guide(cfg):
    assert importlib.util.find_spec('strategy_guide') is not None, 'current strategy guide missing'
    import strategy_guide
    import trader
    return strategy_guide.build(cfg, add_size_fraction=trader.CORE_ADD_POSITION_SIZE_FRACTION)


def configured(tmp_path):
    (tmp_path/'.env').write_text('CORE_AI_STRATEGY_AUTHORITY=true\nCORE_GEMINI_MANAGEMENT_ONLY=true\n'
        'CANDIDATE_C_CHART_ONLY=true\nRISK_ADAPTIVE_PARTIAL_ENABLED=true\n'
        'CORE_ORDER_MODE=FIXED_MARGIN_AUTO_EXIT\nPOSITION_FIXED_USDT=200\n'
        'CANDIDATE_C_ORDER_MODE=FIXED_MARGIN_AUTO_EXIT\nCANDIDATE_C_FIXED_MARGIN_USDT=150\n'
        'CANDIDATE_C_SYMBOLS=DOGE/USDT:USDT,SOL/USDT:USDT\n')
    return UserConfig(str(tmp_path))


def row(card,label):
    return next(x['value'] for x in card['rows'] if x['label']==label)


def test_current_roles_distinguish_entry_gpt_and_gemini_management(tmp_path):
    data=guide(configured(tmp_path));core,cc=data['cards']
    assert 'GPT' in row(core,'진입') and 'Gemini' in row(core,'진입')
    assert 'Gemini' in row(core,'보유 관리') and 'GPT' not in row(core,'보유 관리')
    assert 'AI 사용 없음' in cc['summary']
    assert '중단' in data['costs'] and '6시간' in data['costs']


def test_saved_margin_change_updates_explanation_without_doc_edit(tmp_path):
    cfg=configured(tmp_path)
    assert '200 USDT' in row(guide(cfg)['cards'][0],'주문 크기')
    cfg.POSITION_FIXED_USDT=350;cfg.CANDIDATE_C_FIXED_MARGIN_USDT=225
    core,cc=guide(cfg)['cards']
    assert '350 USDT' in row(core,'주문 크기') and '200 USDT' not in row(core,'주문 크기')
    assert '225 USDT' in row(cc,'주문 크기')


def test_legacy_flags_never_claim_gpt_entry_only(tmp_path):
    cfg=configured(tmp_path);cfg.CORE_GEMINI_MANAGEMENT_ONLY=False
    data=guide(cfg)
    assert 'GPT' in row(data['cards'][0],'보유 관리')
    assert '수동 유료' in data['costs'] and '가능' in data['costs']


def test_manual_order_modes_do_not_claim_automatic_exit_prices(tmp_path):
    cfg=configured(tmp_path);cfg.CORE_ORDER_MODE='MANUAL_ALL';cfg.CANDIDATE_C_ORDER_MODE='MANUAL_ALL'
    for card in guide(cfg)['cards']:
        assert '직접 지정' in row(card,'SL/TP')


def test_guide_follows_code_reduction_policy_bounds(tmp_path,monkeypatch):
    import core_management_policy
    monkeypatch.setitem(core_management_policy.POLICY,'min',.1)
    monkeypatch.setitem(core_management_policy.POLICY,'max',.4)
    assert '10~40%' in row(guide(configured(tmp_path))['cards'][0],'부분감축')


def test_unknown_values_do_not_invent_live_margin():
    cfg=SimpleNamespace(CORE_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT')
    for card in guide(cfg)['cards']:
        assert '미설정' in row(card,'주문 크기')


def test_dashboard_footer_and_state_share_read_only_current_guide(tmp_path,monkeypatch):
    import web_app
    from state import TraderState
    cfg=configured(tmp_path);ctx=SimpleNamespace(cfg=cfg,username='guide-user',dir=str(tmp_path),state=TraderState(),
        gemini_validated=False,okx_validated=False,openai_validated=False)
    monkeypatch.setattr(web_app,'get_context',lambda _:ctx)
    monkeypatch.setattr(web_app.accounts,'is_admin',lambda _:False)
    monkeypatch.setattr(web_app.accounts,'is_approved',lambda _:True)
    with web_app.app.test_client() as client:
        with client.session_transaction() as session:
            session['username']='guide-user';session['authenticated']=True
        page=client.get('/');assert page.status_code==200
        html=page.get_data(as_text=True)
        assert 'id="strategy-guide-panel"' in html
        assert html.index('id="strategy-guide-panel"')>html.index('id="log-box"')
        assert '현재 전략과 운영 방법' in html and '200 USDT' in html
        payload=client.get('/api/state').get_json()
        assert payload['strategy_guide']['cards']==guide(cfg)['cards']
        cfg.POSITION_FIXED_USDT=350
        changed=client.get('/api/state').get_json()['strategy_guide']
        assert '350 USDT' in row(changed['cards'][0],'주문 크기')
