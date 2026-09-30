import trader


class Client:
    def __init__(self, protection=None, error=False):
        self.protection = protection
        self.error = error

    def fetch_current_protection(self, close_side):
        if self.error:
            raise RuntimeError("boom")
        return self.protection


def position(side="short", contracts=2.97):
    return {"side": side, "contracts": contracts}


def test_verified_exchange_protection_is_exposed_exactly():
    client = Client({"algo_id": "a1", "sl_price": 2782.9, "tp_price": 2619.2, "sz": 2.97})
    snap = trader._live_protection_snapshot(client, position())
    assert snap == {
        "status": "VERIFIED",
        "algo_id": "a1",
        "sl_price": 2782.9,
        "tp_price": 2619.2,
        "sz": 2.97,
    }


def test_mismatched_or_unknown_protection_is_not_synthesized():
    mismatch = Client({"algo_id": "a1", "sl_price": 2782.9, "tp_price": 2619.2, "sz": 3.95})
    assert trader._live_protection_snapshot(mismatch, position())["status"] == "UNKNOWN"
    assert trader._live_protection_snapshot(Client(error=True), position())["status"] == "UNKNOWN"


def test_flat_position_needs_no_protection():
    snap = trader._live_protection_snapshot(Client(error=True), None)
    assert snap["status"] == "NOT_REQUIRED_FLAT"
