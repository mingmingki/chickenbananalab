from types import SimpleNamespace
import trader


def test_funding_refresh_is_independently_staggered_from_fee_refresh(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(trader.exchange_fee_ledger,"refresh",lambda *_a,**_k:calls.append("fee"))
    monkeypatch.setattr(trader.okx_margin_return,"refresh",lambda *_a,**_k:calls.append("margin"))
    monkeypatch.setattr(trader.exchange_funding_ledger,"refresh",lambda *_a,**_k:calls.append("funding"))
    cfg=SimpleNamespace(user_dir=str(tmp_path))
    exchange=object()
    fee_next,funding_next=trader._refresh_accounting_ledgers_if_due(
        cfg,exchange,now_mono=100.0,next_fee_refresh=0.0,next_funding_refresh=160.0)
    assert calls == ["fee","margin"]
    assert fee_next == 100.0 + trader.EXCHANGE_FEE_REFRESH_SECONDS
    assert funding_next == 160.0
    calls.clear()
    fee_next,funding_next=trader._refresh_accounting_ledgers_if_due(
        cfg,exchange,now_mono=161.0,next_fee_refresh=fee_next,next_funding_refresh=funding_next)
    assert calls == ["funding"]
    assert funding_next == 161.0 + trader.EXCHANGE_FUNDING_REFRESH_SECONDS

