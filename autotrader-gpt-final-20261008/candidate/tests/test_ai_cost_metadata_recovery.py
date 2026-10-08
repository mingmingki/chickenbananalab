"""Offline usage evidence: absence of metadata must never invent measured costs/retries."""
import datetime as dt
import json
import logging
from types import SimpleNamespace

import pytest

import operating_costs
import usage_log

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 8, 12, tzinfo=KST)


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_usage_preserves_legacy_totals_and_records_optional_real_metadata(tmp_path):
    usage_log.record_usage(str(tmp_path), 'ETH', 1000, 200, provider='openai', purpose='entry_gate',
                           model='fixture-model', request_id='req-real', response_id='resp-real',
                           input_fingerprint='hash', retry_limit=2)
    row = read_rows(tmp_path / 'token_usage.jsonl')[0]
    assert row['purpose'] == 'entry_gate' and row['model'] == 'fixture-model'
    assert row['request_id'] == 'req-real' and row['response_id'] == 'resp-real'
    assert row['total_tokens'] == 1200
    assert row['cost_usd'] == pytest.approx(.00065)
    assert row['retry_count'] is None and row['retry_limit'] == 2
    assert row['estimate_only'] is True
    assert row['pricing_basis'] == 'legacy_provider_estimate'
    usage_log.record_usage(str(tmp_path), 'ETH', 1, 2)
    legacy = read_rows(tmp_path / 'token_usage.jsonl')[1]
    assert legacy['request_id'] is None and legacy['retry_count'] is None
    assert usage_log.summary(str(tmp_path))['total_tokens'] == 1203


def test_cost_summary_separates_repeat_inputs_log_duplicates_and_unknown_retries(tmp_path):
    base = dict(time='2026-10-08T11:00:00+09:00', cost_usd=.1, provider='openai',
                purpose='entry_gate', symbol='ETH', model='fixture-model', input_tokens=10,
                output_tokens=5, total_tokens=15, input_fingerprint='same-input', retry_limit=2)
    rows = [dict(base, request_id='req-1', retry_count=1),
            dict(base, request_id='req-2', retry_count=None),
            dict(base, request_id='req-2', retry_count=None),
            dict(time='2026-10-08T11:01:00+09:00', cost_usd=.2, total_tokens=8),
            dict(base, time='2026-10-07T11:59:59+09:00'),
            dict(base, time='2026-10-08T12:00:01+09:00')]
    (tmp_path / 'token_usage.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    before = (tmp_path / 'token_usage.jsonl').read_bytes()
    result = operating_costs.build_operating_cost_summary(str(tmp_path), now=NOW, assumptions={})
    assert result['rolling_24h_cost_usd'] == .5
    assert result['by_model_24h']['fixture-model']['calls'] == 3
    assert result['by_model_24h']['fixture-model']['total_tokens'] == 45
    assert result['by_purpose_24h']['unknown']['total_tokens'] == 8
    metrics = result['call_metadata_24h']
    assert metrics['logged_calls'] == 4
    assert metrics['duplicate_request_records'] == 1
    assert metrics['repeated_input_calls'] == 1  # duplicate log record excluded
    assert metrics['measured_retry_count'] == 1
    assert metrics['unknown_retry_calls'] == 3
    assert metrics['request_identity_unknown_calls'] == 1
    assert (tmp_path / 'token_usage.jsonl').read_bytes() == before


@pytest.mark.parametrize('retry_limit,expected', [(0, 0), (2, None), (None, None)])
def test_entry_usage_and_latency_record_purpose_model_and_retry_uncertainty(tmp_path, monkeypatch, retry_limit, expected):
    import openai_analyzer
    response = SimpleNamespace(model='actual-openai-model', _request_id='req-offline', id='resp-offline',
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"decision":"wait","confidence":0.8,"reasoning":"offline"}'))])
    class OfflineClient:
        max_retries = retry_limit
        def with_options(self, **options): return self
        def create(self, **kwargs): return response
    client = OfflineClient()
    client.chat = SimpleNamespace(completions=client)
    monkeypatch.setattr(openai_analyzer, '_get_client', lambda _: client)
    monkeypatch.setattr(openai_analyzer, '_ensure_response_models_warmed_up', lambda: None)
    cfg = SimpleNamespace(OPENAI_API_KEY='offline', OPENAI_MODEL='configured-model',
                          user_dir=str(tmp_path), logger=logging.getLogger('offline'))
    openai_analyzer.verify(cfg, 'ETH', [], 'private prompt', None,
                          {'action':'long', 'confidence':.8}, purpose='entry_gate', max_retries=retry_limit)
    row = read_rows(tmp_path / 'token_usage.jsonl')[0]
    assert row['purpose'] == 'entry_gate' and row['model'] == 'actual-openai-model'
    assert row['request_id'] == 'req-offline' and row['response_id'] == 'resp-offline'
    assert row['retry_count'] == expected and row['retry_limit'] == retry_limit
    assert len(row['input_fingerprint']) == 64
    assert 'private prompt' not in (tmp_path / 'token_usage.jsonl').read_text()
    latency = read_rows(tmp_path / 'gpt_latency_log.jsonl')[0]
    assert latency['retry_count'] == expected and latency['retry_limit'] == retry_limit


def test_gemini_review_records_real_model_response_identity_and_unknown_retries(tmp_path, monkeypatch):
    import gemini_analyzer
    response = SimpleNamespace(model_version='actual-gemini-model', response_id='resp-gemini', text='{}',
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=3))
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_: response))
    monkeypatch.setattr(gemini_analyzer, '_get_client', lambda _: client)
    cfg = SimpleNamespace(GEMINI_MODEL='configured-gemini', user_dir=str(tmp_path), logger=None)
    gemini_analyzer.review_strategy_report(cfg, {'offline': True})
    row = read_rows(tmp_path / 'token_usage.jsonl')[0]
    assert row['purpose'] == 'strategy_review' and row['model'] == 'actual-gemini-model'
    assert row['response_id'] == 'resp-gemini' and row['request_id'] is None
    assert row['output_tokens'] == 8
    assert row['retry_count'] is None and row['retry_limit'] is None


def test_latency_legacy_retry_ceiling_is_never_relabelled_as_measured(tmp_path):
    import gpt_latency_log
    gpt_latency_log.record_call(str(tmp_path), 'ETH', 'entry_gate', 'fixture',
                               '2026-10-08T11:00:00', 100, False, 2)
    row = read_rows(tmp_path / 'gpt_latency_log.jsonl')[0]
    assert row['retry_limit'] == 2 and row['retry_count'] is None
