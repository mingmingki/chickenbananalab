import inspect
import trader

def test_stale_manual_close_cleanup_is_wired_to_all_position_actions():
    names = [
        "_execute_position_ai_add_locked",
        "_execute_position_ai_reduce_50_locked",
        "_run_core_fast_tick",
    ]
    for name in names:
        source = inspect.getsource(getattr(trader, name))
        assert "complete_if_superseded_by_position" in source, name
        assert "stale manual-close guard completed for newer position" in source, name
