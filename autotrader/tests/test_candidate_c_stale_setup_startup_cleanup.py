import logging
from types import SimpleNamespace

import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import web_app


SYMBOL = "DOGE/USDT:USDT"


class Exchange:
    def __init__(self, client):
        self.client = client

    def fetch_open_orders(self, symbol):
        self.client.order_reads += 1
        return []


class Client:
    def __init__(self, symbol, cfg):
        self.symbol = symbol
        self.position_reads = 0
        self.order_reads = 0
        self.algo_reads = 0
        self.exchange = Exchange(self)

    def fetch_position(self):
        self.position_reads += 1
        return None

    def fetch_pending_protection_algo_ids(self):
        self.algo_reads += 1
        return []


def cfg(tmp_path):
    return SimpleNamespace(
        user_dir=str(tmp_path),
        logger=logging.getLogger("test.candidate.startup.cleanup"),
    )


def test_stale_tracker_edges_are_reconciled_once_per_symbol(tmp_path, monkeypatch):
    tracker = st.SetupTracker.load(str(tmp_path / "candidate_c_setup_tracker.jsonl"))
    sid1 = tracker.observe(SYMBOL, "long", 1, True)
    tracker.observe(SYMBOL, "long", 2, False)
    sid2 = tracker.observe(SYMBOL, "long", 3, True)

    made = []
    def factory(symbol, cfg_obj):
        client = Client(symbol, cfg_obj)
        made.append(client)
        return client

    monkeypatch.setattr(web_app.okx_client, "OkxClient", factory)
    count = web_app._reconcile_candidate_c_stale_setups_before_start(cfg(tmp_path), [SYMBOL])

    assert count == 2
    reloaded = st.SetupTracker.load(str(tmp_path / "candidate_c_setup_tracker.jsonl"))
    assert reloaded.pending_setup_ids() == []
    assert reloaded.is_attempted(sid1)
    assert reloaded.is_attempted(sid2)
    assert len(made) == 1
    assert made[0].position_reads == 1
    assert made[0].order_reads == 1
    assert made[0].algo_reads == 1


def test_safe_halt_is_never_auto_reconciled(tmp_path, monkeypatch):
    tracker = st.SetupTracker.load(str(tmp_path / "candidate_c_setup_tracker.jsonl"))
    sid = tracker.observe(SYMBOL, "long", 1, True)

    store = rsm.ReversalStateStore.load(str(tmp_path / "candidate_c_reversal_store.jsonl"))
    store.get(SYMBOL).enter_safe_halt()
    store.persist(SYMBOL, 1)

    monkeypatch.setattr(web_app.okx_client, "OkxClient", Client)
    count = web_app._reconcile_candidate_c_stale_setups_before_start(cfg(tmp_path), [SYMBOL])

    assert count == 0
    reloaded = st.SetupTracker.load(str(tmp_path / "candidate_c_setup_tracker.jsonl"))
    assert reloaded.pending_setup_ids() == [sid]
