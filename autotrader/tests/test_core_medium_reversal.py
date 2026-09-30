import trader


def _rows():
    return [
        {'macd': 3.0, 'rsi_14': 48.0, 'close': 101.0, 'ema_20': 102.0},
        {'macd': 2.0, 'rsi_14': 46.0, 'close': 100.0, 'ema_20': 102.0},
        {'macd': 1.0, 'rsi_14': 44.0, 'close': 99.0, 'ema_20': 101.0},
    ]


def test_medium_reversal_accepts_confirmed_long_to_short():
    ok, reason = trader._medium_reversal_eligible(
        {'side': 'long'},
        {'action': 'short', 'confidence': 0.72},
        {'decision': 'approve_now', 'confidence': 0.75},
        _rows(),
    )
    assert ok is True
    assert reason == 'approved'


def test_medium_reversal_rejects_low_gemini_confidence():
    ok, reason = trader._medium_reversal_eligible(
        {'side': 'long'},
        {'action': 'short', 'confidence': 0.69},
        {'decision': 'approve_now', 'confidence': 0.90},
        _rows(),
    )
    assert (ok, reason) == (False, 'gemini_confidence')


def test_medium_reversal_rejects_low_gpt_confidence():
    ok, reason = trader._medium_reversal_eligible(
        {'side': 'long'},
        {'action': 'short', 'confidence': 0.90},
        {'decision': 'approve_now', 'confidence': 0.69},
        _rows(),
    )
    assert (ok, reason) == (False, 'gpt_confidence')



def test_medium_reversal_rejects_unconfirmed_5m_weakness():
    rows = _rows()
    rows[-1]['macd'] = 2.5
    ok, reason = trader._medium_reversal_eligible(
        {'side': 'long'},
        {'action': 'short', 'confidence': 0.90},
        {'decision': 'approve_now', 'confidence': 0.90},
        rows,
    )
    assert (ok, reason) == (False, '5m_not_bearish')


def test_medium_reversal_is_only_long_to_short():
    ok, reason = trader._medium_reversal_eligible(
        {'side': 'short'},
        {'action': 'long', 'confidence': 0.90},
        {'decision': 'approve_now', 'confidence': 0.90},
        _rows(),
    )
    assert (ok, reason) == (False, 'not_long_to_short')


def test_reversal_flat_confirmation_rejects_any_open_position():
    old = {'side': 'long', 'position_id': 'old'}
    assert trader._reversal_flat_confirmed(None, old) is True
    assert trader._reversal_flat_confirmed({'side': 'long', 'position_id': 'old'}, old) is False
    assert trader._reversal_flat_confirmed({'side': 'short', 'position_id': 'new'}, old) is False
