import pytest
from adaptive_exit_engine import apply_monotonic_stop
@pytest.mark.parametrize('side,current,proposed,accepted,rejected',[
 ('long',95.0,94.0,95.0,True),('long',95.0,96.0,96.0,False),
 ('short',105.0,106.0,105.0,True),('short',105.0,104.0,104.0,False),
])
def test_active_stop_never_loosens(side,current,proposed,accepted,rejected):
    d=apply_monotonic_stop(side,current,proposed)
    assert d.effective_stop == accepted and d.rejected_loosen is rejected
