import pandas as pd
import core_tactical_short
import core_short_level


def _df(rows):
    return pd.DataFrame(rows)


def test_pi_like_1h_bearish_accepts_bearish_ema_stack_when_rebound_histogram_turns_down():
    one_h=_df([
        {'close':0.08687,'ema_20':0.08692,'ema_50':0.08777,'macd':-0.000450,'macd_signal':-0.000602},
        {'close':0.08600,'ema_20':0.08683,'ema_50':0.08770,'macd':-0.000472,'macd_signal':-0.000576},
    ])
    # MACD is still above signal, but positive histogram shrank: rebound is losing steam.
    assert one_h.iloc[-1].macd > one_h.iloc[-1].macd_signal
    assert core_tactical_short._bearish_1h(one_h) is True


def test_pi_like_1h_bearish_rejects_when_histogram_is_still_improving():
    one_h=_df([
        {'close':0.08736,'ema_20':0.08693,'ema_50':0.08781,'macd':-0.000505,'macd_signal':-0.000640},
        {'close':0.08687,'ema_20':0.08692,'ema_50':0.08777,'macd':-0.000450,'macd_signal':-0.000602},
    ])
    assert core_tactical_short._bearish_1h(one_h) is False


def test_1h_bearish_requires_bearish_ema_stack_for_histogram_turn_down_path():
    one_h=_df([
        {'close':100.0,'ema_20':99.0,'ema_50':101.0,'macd':-0.1,'macd_signal':-0.2},
        {'close':98.5,'ema_20':99.0,'ema_50':98.0,'macd':-0.15,'macd_signal':-0.2},
    ])
    assert core_tactical_short._bearish_1h(one_h) is False


def test_pi_like_full_classifier_no_longer_none_once_lower_tfs_are_bearish():
    one_h=_df([
        {'close':0.08687,'ema_20':0.08692,'ema_50':0.08777,'macd':-0.000450,'macd_signal':-0.000602},
        {'close':0.08600,'ema_20':0.08683,'ema_50':0.08770,'macd':-0.000472,'macd_signal':-0.000576},
    ])
    four_h=_df([
        {'close':0.0868,'ema_20':0.0878,'ema_50':0.0882,'macd':-0.00020,'macd_signal':0.00020},
        {'close':0.08646,'ema_20':0.08787,'ema_50':0.08820,'macd':-0.00021,'macd_signal':0.00027},
        {'close':0.0860,'ema_20':0.0877,'ema_50':0.0881,'macd':-0.00025,'macd_signal':0.00020},
    ])
    one_d=_df([{'close':0.08646,'ema_20':0.0896,'ema_50':0.0919,'macd':-0.00139,'macd_signal':-0.0010}])
    lower=_df([{'close':1,'ema_20':2,'ema_50':3,'macd':-1,'macd_signal':0}])
    bearish={'high_structure':'HH','low_structure':'LL'}
    result=core_short_level.classify('short',0.72,'bearish',one_d,four_h,one_h,lower,lower,bearish,bearish)
    assert result['level'] in ('TACTICAL','STRONG','FULL_BEARISH')
    assert result['reasons']['1h_bearish'] is True
