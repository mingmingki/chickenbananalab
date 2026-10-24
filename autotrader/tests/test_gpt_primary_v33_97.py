from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import json
import os
import pytest

import gpt_primary_v33_97 as primary
import ai_primary_provider_v33_97 as provider
from config import UserConfig

SCHEMA = {"type": "OBJECT", "properties": {
    "action": {"type": "STRING", "enum": ["long", "hold"]},
    "confidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
}, "required": ["action", "confidence"]}

def _response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=8),
        id="mocked_response",
    )

def _cfg(directory, key="mock-only"):
    return SimpleNamespace(user_dir=directory, OPENAI_API_KEY=key,
                           OPENAI_MODEL="gpt-5-mini")

def test_primary_calls_only_gpt_and_records_correct_provider():
    with TemporaryDirectory() as d, patch("openai.OpenAI") as sdk, \
         patch.object(primary.usage_log,"record_api_attempt") as attempt, \
         patch.object(primary.usage_log,"record_usage") as usage_log:
        sdk.return_value.chat.completions.create.return_value = _response(
            '{"action":"long","confidence":0.82}')
        result = primary._generate_content_observed(_cfg(d),"BTC","core_primary_decision",
            contents='Market: sample', config=primary.types.GenerateContentConfig(response_schema=SCHEMA))
        assert json.loads(result.text)["action"] == "long"
        sdk.assert_called_once()
        args=sdk.return_value.chat.completions.create.call_args.kwargs
        assert args["model"] == "gpt-5-mini"
        assert "json_object" == args["response_format"]["type"]
        assert args["messages"][-1]["content"] == "Market: sample"
        assert attempt.call_args.args[2] == "openai"
        assert not usage_log.called
        sdk.return_value.close.assert_called_once()

def test_malformed_response_fail_closed():
    with TemporaryDirectory() as d, patch("openai.OpenAI") as sdk, \
         patch.object(primary.usage_log,"record_api_attempt"):
        sdk.return_value.chat.completions.create.return_value = _response(
            '{"action":"long","confidence":"high"}')
        with pytest.raises(ValueError):
            primary._generate_content_observed(_cfg(d),"ETH","core_primary_decision",
                contents="test",config=primary.types.GenerateContentConfig(response_schema=SCHEMA))

def test_missing_key_fail_closed_without_sdk_call():
    with TemporaryDirectory() as d, patch("openai.OpenAI") as sdk, \
         patch.object(primary.usage_log,"record_api_attempt"):
        with pytest.raises(RuntimeError,match="OPENAI_API_KEY_required"):
            primary._generate_content_observed(_cfg(d,key=""),"XRP","core_primary_decision",
                contents="test",config=primary.types.GenerateContentConfig(response_schema=SCHEMA))
        sdk.assert_not_called()

def test_primary_facade_dispatch_per_account():
    cfg=SimpleNamespace(GPT_PRIMARY_ONLY=True)
    with patch.object(provider._gpt,"analyze",return_value={"action":"hold"}) as g, \
         patch.object(provider._legacy,"analyze",return_value={"action":"long"}) as old:
        assert provider.analyze(cfg,"ETH",[],None,None)["action"]=="hold"
        old.assert_not_called()
        g.assert_called_once()
        cfg.GPT_PRIMARY_ONLY=False
        assert provider.analyze(cfg,"ETH",[],None,None)["action"]=="long"

def test_gpt_only_config_enforces_no_timeout_bypass_and_key_requirement():
    with TemporaryDirectory() as d:
        with open(os.path.join(d,".env"),"w") as f:
            f.write("GPT_PRIMARY_ONLY=true\nCORE_AI_STRATEGY_AUTHORITY=true\n"
                    "GPT_ENTRY_GATE_ENABLED=false\nCORE_GPT_ENTRY_TIMEOUT_BYPASS=true\n"
                    "CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=false\n"
                    "OKX_API_KEY=dummy\nOKX_API_SECRET=dummy\nOKX_API_PASSPHRASE=dummy\n")
        cfg=UserConfig(d)
        assert cfg.GPT_PRIMARY_ONLY
        assert cfg.GPT_ENTRY_GATE_ENABLED
        assert cfg.CANDIDATE_C_GPT_ENTRY_GATE_ENABLED
        assert not cfg.CORE_GPT_ENTRY_TIMEOUT_BYPASS
        with pytest.raises(RuntimeError,match="OPENAI_API_KEY"):
            cfg.validate_core()
        cfg.OPENAI_API_KEY="mock-only"
        cfg.validate_core()

def test_no_google_sdk_import_in_primary_source():
    from pathlib import Path
    s=Path(primary.__file__).read_text()
    assert "from google import" not in s
    assert "from google.genai import" not in s
    assert "genai.Client" not in s
