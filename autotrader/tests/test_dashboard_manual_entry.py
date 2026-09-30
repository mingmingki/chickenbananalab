from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"


def test_core_cards_have_manual_long_short_buttons_above_pause():
    text = HTML.read_text()
    assert 'id="manual-long-${id}"' in text
    assert 'id="manual-short-${id}"' in text
    assert "수동 LONG 진입" in text
    assert "수동 SHORT 진입" in text
    assert text.index('id="manual-entry-${id}"') < text.index('id="pause-${id}"')


def test_manual_entry_ui_confirms_current_core_sizing_and_skips_ai_only():
    text = HTML.read_text()
    assert "async function manualCoreEntry" in text
    assert "'/api/manual_entry'" in text
    assert "현재 CORE 설정의 포지션 크기·레버리지·SL/TP를 그대로 사용합니다." in text
    assert "Gemini/GPT 진입 판단은 건너뛰지만" in text


def test_manual_buttons_show_only_flat_running_enabled_symbols():
    text = HTML.read_text()
    assert "const canShowManualEntry = !pos && !!info.enabled;" in text
    assert "const manualEntryBlocked = !s.running" in text
    assert "manualLongBtn.disabled = !canShowManualEntry || manualEntryBlocked;" in text
    assert "manualShortBtn.disabled = !canShowManualEntry || manualEntryBlocked;" in text
