import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
import usage_log
import gpt_primary_v33_97 as primary
from config import UserConfig

def test_chart_only_config_stays_no_ai(tmp_path):
    p=tmp_path/".env"
    p.write_text("GPT_PRIMARY_ONLY=true\nCANDIDATE_C_CHART_ONLY=true\nCANDIDATE_C_GPT_ENTRY_GATE_ENABLED=true\nCANDIDATE_C_AI_EXIT_PLAN_ENABLED=true\nEXECUTION_MODE=LIVE\n")
    cfg=UserConfig(str(tmp_path))
    assert cfg.GPT_PRIMARY_ONLY
    assert cfg.CANDIDATE_C_CHART_ONLY
    assert cfg.CANDIDATE_C_GPT_ENTRY_GATE_ENABLED is False
    assert cfg.CANDIDATE_C_AI_EXIT_PLAN_ENABLED is False
    assert cfg.POLL_INTERVAL_SECONDS == 900

def test_openai_cached_tokens_lower_estimate_only(tmp_path):
    usage_log.record_usage(str(tmp_path),"BTC",input_tokens=4000,output_tokens=100,
        provider="openai",purpose="test",model="gpt-5-mini",cached_input_tokens=3000)
    obj=json.loads((tmp_path/"token_usage.jsonl").read_text())
    regular=1000*.25/1e6
    cached=3000*.025/1e6
    outgoing=100*2/1e6
    assert obj["cost_usd"]==pytest.approx(regular+cached+outgoing)
    assert obj["cached_input_tokens"]==3000
    assert obj["estimate_only"] is True

def test_primary_gpt_cache_key_is_transport_only_and_has_no_order_calls(tmp_path):
    cfg=SimpleNamespace(OPENAI_API_KEY="fake",OPENAI_MODEL="gpt-5-mini",user_dir=str(tmp_path))
    schema={"type":"OBJECT","required":["action"],"properties":{"action":{"type":"STRING","enum":["hold"]}}}
    with patch("openai.OpenAI") as sdk,patch.object(primary.usage_log,"record_api_attempt"):
        instance=sdk.return_value
        instance.chat.completions.create.return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"action":"hold"}'))],
            usage=SimpleNamespace(prompt_tokens=10,completion_tokens=5,prompt_tokens_details=SimpleNamespace(cached_tokens=0)),id="mock")
        response=primary._generate_content_observed(cfg,"BTC","core_primary_decision",
            contents="No trade data; HOLD only",
            config=primary.types.GenerateContentConfig(response_schema=schema))
        assert json.loads(response.text)["action"]=="hold"
        kwargs=instance.chat.completions.create.call_args.kwargs
        assert kwargs["model"]=="gpt-5-mini"
        assert kwargs["extra_body"]["prompt_cache_key"].startswith("cb-gpt-primary-")
        assert kwargs["response_format"]["type"]=="json_object"
        assert "reasoning_effort" not in kwargs # unchanged decision depth
        assert "max_completion_tokens" not in kwargs # no truncation of valid decisions

def test_chart_only_never_attempts_gpt_on_entry():
    import inspect
    import candidate_c_hybrid_live_adapter as live
    source=inspect.getsource(live._execute_entry)
    assert "not strategy_authority.candidate_chart(cfg)" in source
    assert 'getattr(cfg, "GPT_PRIMARY_ONLY", False) or not strategy_authority.candidate_chart' not in source
