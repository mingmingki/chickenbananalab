import trader


def test_confirmed_manual_close_average_fills_missing_exit_price():
    resolved={'exit_price':None,'gross_pnl':1.0,'fee':0.1,'net_pnl':0.9}
    order={'status':'closed','filled':5.0,'average':1.2345}
    out=trader._prefer_confirmed_manual_close_price(resolved,order)
    assert out['exit_price']==1.2345
    assert out['exit_price_source']=='manual_close_order_average'
    assert out['gross_pnl']==1.0 and out['net_pnl']==0.9


def test_manual_close_price_does_not_guess_from_unconfirmed_order():
    resolved={'exit_price':None,'gross_pnl':-2.0,'fee':0.2,'net_pnl':-2.2}
    for order in ({'status':'open','filled':5.0,'average':1.2},
                  {'status':'closed','filled':0.0,'average':1.2},
                  {'status':'closed','filled':5.0,'average':None}):
        assert trader._prefer_confirmed_manual_close_price(resolved,order)['exit_price'] is None
