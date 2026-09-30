from pathlib import Path
from google.genai import types
import config

ROOT = Path(__file__).resolve().parents[1]

def test_default_model_is_gemini_38_flash(tmp_path):
    (tmp_path / ".env").write_text("")
    cfg = config.UserConfig(str(tmp_path))
    assert cfg.GEMINI_MODEL == "gemini-3.8-flash"

def test_all_core_gemini_calls_use_low_thinking_level():
    for name in ("gemini_analyzer.py", "core_unified_adapters.py"):
        text = (ROOT / name).read_text()
        assert "thinking_budget=0" not in text
        assert 'thinking_level="low"' in text

def test_sdk_accepts_low_thinking_level():
    cfg = types.ThinkingConfig(thinking_level="low")
    assert str(cfg.thinking_level).lower().endswith("low")
