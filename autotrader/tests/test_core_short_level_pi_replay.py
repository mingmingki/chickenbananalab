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


def test_pi_live_4h_break_allows_early_without_waiting_for_4h_close():
    one_h=_df([
        {'close':0.08523,'ema_20':0.08714,'ema_50':0.08732,'macd':-0.00018,'macd_signal':-0.00010},
        {'close':0.08384,'ema_20':0.08683,'ema_50':0.08719,'macd':-0.00042,'macd_signal':-0.00020},
    ])
    closed_four_h=_df([
        {'close':0.08710,'ema_20':0.08759,'ema_50':0.08846,'macd':-0.00048,'macd_signal':-0.00055},
    ])
    live_four_h=_df([
        {'close':0.08710,'ema_20':0.08759,'ema_50':0.08846,'macd':-0.00048,'macd_signal':-0.00055},
        {'close':0.08345,'ema_20':0.08720,'ema_50':0.08826,'macd':-0.00076,'macd_signal':-0.00050},
    ])
    one_d=_df([{'close':0.0871,'ema_20':0.0892,'ema_50':0.0910,'macd':-0.00082,'macd_signal':-0.00070}])
    lower=_df([
        {'close':0.0841,'ema_20':0.0842,'ema_50':0.0850,'macd':-0.00038,'macd_signal':-0.00030},
        {'close':0.0835,'ema_20':0.0840,'ema_50':0.0849,'macd':-0.00040,'macd_signal':-0.00030},
    ])
    not_bearish={'high_structure':'HH','low_structure':'HL'}
    bearish={'high_structure':'LH','low_structure':'LL'}
    result=core_short_level.classify(
        'short',0.76,'bearish',one_d,closed_four_h,one_h,lower,lower,
        not_bearish,bearish,live_4h_df=live_four_h,
    )
    assert result['level'] == 'EARLY'
    assert result['reasons']['live_4h_bearish'] is True
    assert result['reasons']['live_4h_early'] is True


def test_confirmed_4h_1h_plus_both_lower_tf_weakening_is_early_not_none():
    one_h=_df([
        {'close':1.00,'ema_20':1.10,'ema_50':1.20,'macd':-0.20,'macd_signal':-0.10},
        {'close':0.95,'ema_20':1.05,'ema_50':1.15,'macd':-0.25,'macd_signal':-0.12},
    ])
    four_h=_df([
        {'close':1.00,'ema_20':1.10,'ema_50':1.20,'macd':-0.20,'macd_signal':-0.10},
        {'close':0.95,'ema_20':1.05,'ema_50':1.15,'macd':-0.25,'macd_signal':-0.12},
        {'close':0.90,'ema_20':1.00,'ema_50':1.10,'macd':-0.30,'macd_signal':-0.15},
    ])
    one_d=_df([{'close':0.90,'ema_20':1.05,'ema_50':1.15,'macd':-0.2,'macd_signal':-0.1}])
    lower=_df([
        {'close':1.00,'ema_20':1.05,'ema_50':1.10,'macd':-0.10,'macd_signal':-0.08},
        {'close':0.98,'ema_20':1.04,'ema_50':1.09,'macd':-0.11,'macd_signal':-0.08},
    ])
    non_bearish={'high_structure':'HH','low_structure':'HL'}
    result=core_short_level.classify(
        'short',0.72,'bearish',one_d,four_h,one_h,lower,lower,
        non_bearish,non_bearish,
    )
    assert result['level'] == 'EARLY'
    assert result['reasons']['3m_weakening'] is True
    assert result['reasons']['5m_weakening'] is True
