from types import SimpleNamespace
import pytest
from google.genai.errors import ServerError
import gemini_analyzer as g


@pytest.mark.parametrize('status', [503, 504])
def test_typed_transient_retry_once_and_account_both_attempts(monkeypatch, tmp_path, status):
    calls = []
    records = []
    def fake(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ServerError(status, {'message': 'overloaded'})
        return 'ok'
    cfg = SimpleNamespace(user_dir=str(tmp_path), GEMINI_MODEL='offline')
    monkeypatch.setattr(g, '_get_client', lambda cfg: SimpleNamespace(models=SimpleNamespace(generate_content=fake)))
    monkeypatch.setattr(g.time, 'sleep', lambda s: None)
    monkeypatch.setattr(g.usage_log, 'record_api_attempt', lambda *a, **kw: records.append(kw))
    assert g._generate_content_observed(cfg, 'BTC/USDT:USDT', 'entry_gate', model='offline') == 'ok'
    assert len(calls) == 2
    assert len(records) == 2
    assert records[0]['error_type'] == 'ServerError'
    assert records[1]['error_type'] is None


@pytest.mark.parametrize('status', [500, 429])
def test_non_transient_or_non_server_error_does_not_retry(monkeypatch, tmp_path, status):
    from google.genai.errors import ClientError
    calls = []
    errtype = ServerError if status >= 500 else ClientError
    def fake(**kwargs):
        calls.append(kwargs)
        raise errtype(status, {'message': 'fail'})
    cfg = SimpleNamespace(user_dir=str(tmp_path), GEMINI_MODEL='offline')
    monkeypatch.setattr(g, '_get_client', lambda cfg: SimpleNamespace(models=SimpleNamespace(generate_content=fake)))
    monkeypatch.setattr(g.usage_log, 'record_api_attempt', lambda *a, **kw: None)
    with pytest.raises(errtype):
        g._generate_content_observed(cfg, 'BTC/USDT:USDT', 'entry_gate', model='offline')
    assert len(calls) == 1
