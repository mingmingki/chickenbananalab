from pathlib import Path
from types import SimpleNamespace

import pytest
import risk_manager


def _cfg(tmp_path):
    return SimpleNamespace(
        user_dir=str(tmp_path), logger=None,
        ACCOUNT_HARD_DAILY_LOSS_PCT=30.0,
        MAX_DAILY_LOSS_PCT=5.0,
        CANDIDATE_C_MAX_DAILY_LOSS_PCT=10.0,
        TELEGRAM_BOT_TOKEN='token', TELEGRAM_CHAT_ID='chat',
    )


def test_core_daily_loss_is_exposed_and_saved_by_dashboard():
    html = Path('templates/dashboard.html').read_text(encoding='utf-8')
    app = Path('web_app.py').read_text(encoding='utf-8')
    assert 'CORE 일일 손실 (%)' in html
    assert 'id="core-max-daily-loss-pct"' in html
    assert 'max_daily_loss_pct: parseFloat(document.getElementById("core-max-daily-loss-pct").value)' in html
    assert 'document.getElementById("core-max-daily-loss-pct").value = s.settings.max_daily_loss_pct' in html
    assert 'CORE 일일손실 ${fmt(s.settings.max_daily_loss_pct)}%' in html
    assert '"max_daily_loss_pct": cfg.MAX_DAILY_LOSS_PCT' in app
    assert '"MAX_DAILY_LOSS_PCT": str(max_daily_loss_pct)' in app


def test_core_guard_telegram_fires_once_recovers_once_and_survives_restart(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    realized = {'value': 0.0}
    monkeypatch.setattr(risk_manager.pnl_reconciliation, 'realized_pnl_for_kst_date', lambda *_a, **_k: realized['value'])
    sent = []
    monkeypatch.setattr(risk_manager.telegram_notify, 'send', lambda _cfg, text: sent.append(text))
    guard = risk_manager.DailyLossGuard(cfg, limit_attr='MAX_DAILY_LOSS_PCT', group='core')
    assert guard.allow_new_entry(1000.0) is True
    realized['value'] = -60.0
    assert guard.allow_new_entry(1000.0) is False
    assert guard.allow_new_entry(1000.0) is False
    assert len(sent) == 1 and '[CORE]' in sent[0] and '발동' in sent[0]
    restarted = risk_manager.DailyLossGuard(cfg, limit_attr='MAX_DAILY_LOSS_PCT', group='core')
    assert restarted.allow_new_entry(1000.0) is False
    assert len(sent) == 1
    realized['value'] = -10.0
    assert restarted.allow_new_entry(1000.0) is True
    assert len(sent) == 2 and '[CORE]' in sent[1] and '해제' in sent[1]


def test_account_guard_telegram_is_deduped_across_group_guards(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(risk_manager.pnl_reconciliation, 'realized_pnl_for_kst_date', lambda *_a, **_k: 0.0)
    sent = []
    monkeypatch.setattr(risk_manager.telegram_notify, 'send', lambda _cfg, text: sent.append(text))
    core = risk_manager.DailyLossGuard(cfg, limit_attr='MAX_DAILY_LOSS_PCT', group='core')
    candidate = risk_manager.DailyLossGuard(cfg, limit_attr='CANDIDATE_C_MAX_DAILY_LOSS_PCT', group='candidate_c')
    assert core.allow_new_entry(1000.0) is True
    assert core.allow_new_entry(690.0) is False
    assert candidate.allow_new_entry(690.0) is False
    account_alerts = [x for x in sent if '[ACCOUNT]' in x and '발동' in x]
    assert len(account_alerts) == 1
    assert candidate.allow_new_entry(710.0) is True
    recoveries = [x for x in sent if '[ACCOUNT]' in x and '해제' in x]
    assert len(recoveries) == 1


def test_telegram_failure_never_changes_guard_decision(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(risk_manager.pnl_reconciliation, 'realized_pnl_for_kst_date', lambda *_a, **_k: -60.0)
    def boom(*_a, **_k):
        raise RuntimeError('telegram down')
    monkeypatch.setattr(risk_manager.telegram_notify, 'send', boom)
    guard = risk_manager.DailyLossGuard(cfg, limit_attr='MAX_DAILY_LOSS_PCT', group='core')
    assert guard.allow_new_entry(1000.0) is False
