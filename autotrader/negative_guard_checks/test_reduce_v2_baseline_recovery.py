import math
import tempfile
import unittest

import reduce_v2_state


SYMBOL = "ETH/USDT:USDT"


def position(**updates):
    value = {
        "side": "long",
        "position_id": "pos-1",
        "entry_timestamp_ms": 1_700_000_000_000,
        "entry_price": 100.0,
        "contracts": 10.0,
    }
    value.update(updates)
    return value


def open_record(**updates):
    value = {
        "type": "open",
        "symbol": SYMBOL,
        "side": "long",
        "price": 100.0,
        "amount": 1.0,
        "dry_run": False,
        "time": "2026-09-20T12:00:00",
    }
    value.update(updates)
    return value


class BaselineEvidenceTests(unittest.TestCase):
    def test_live_open_record_restores_contract_baseline_after_unit_conversion(self):
        recovered = reduce_v2_state.initial_contracts_from_open_record(
            SYMBOL, position(), open_record(), contract_size=0.1,
        )

        self.assertEqual(recovered, 10.0)

    def test_mismatched_or_ambiguous_open_record_never_becomes_a_baseline(self):
        cases = [
            open_record(side="short"),
            open_record(price=101.0),
            open_record(amount=0.9),
            open_record(symbol="BTC/USDT:USDT"),
            open_record(dry_run=True),
            open_record(amount=float("inf")),
            open_record(type="reduce"),
        ]

        for record in cases:
            with self.subTest(record=record):
                self.assertIsNone(
                    reduce_v2_state.initial_contracts_from_open_record(
                        SYMBOL, position(), record, contract_size=0.1,
                    )
                )

    def test_existing_unknown_lifecycle_can_be_upgraded_only_with_valid_quantity(self):
        with tempfile.TemporaryDirectory() as user_dir:
            first = reduce_v2_state.ensure_position(user_dir, SYMBOL, position())
            self.assertFalse(first["baseline_known"])

            invalid = reduce_v2_state.ensure_position(
                user_dir, SYMBOL, position(), initial_contracts=math.inf,
            )
            self.assertFalse(invalid["baseline_known"])

            recovered = reduce_v2_state.ensure_position(
                user_dir, SYMBOL, position(), initial_contracts=10.0,
                entry_time="2026-09-20T12:00:00",
            )

            self.assertTrue(recovered["baseline_known"])
            self.assertEqual(recovered["initial_contracts"], 10.0)
            self.assertEqual(recovered["entry_time"], "2026-09-20T12:00:00")

    def test_ensure_position_recovers_from_valid_last_unclosed_open(self):
        with tempfile.TemporaryDirectory() as user_dir:
            reduce_v2_state.ensure_position(user_dir, SYMBOL, position())

            recovered = reduce_v2_state.ensure_position(
                user_dir, SYMBOL, position(), open_record=open_record(),
                contract_size=0.1,
            )

            self.assertTrue(recovered["baseline_known"])
            self.assertEqual(recovered["initial_contracts"], 10.0)

    def test_legacy_unreduced_state_is_upgraded_in_the_same_call(self):
        with tempfile.TemporaryDirectory() as user_dir:
            legacy = reduce_v2_state.load_or_init(
                user_dir, SYMBOL, "long", 100.0,
                "2026-09-20T12:00:00", 10.0,
            )
            self.assertNotIn("lifecycle_id", legacy)

            recovered = reduce_v2_state.ensure_position(
                user_dir, SYMBOL, position(), open_record=open_record(),
                contract_size=0.1,
            )

            self.assertTrue(recovered["baseline_known"])
            self.assertEqual(recovered["initial_contracts"], 10.0)
            self.assertEqual(
                recovered["lifecycle_id"],
                reduce_v2_state.position_identity(position()),
            )


if __name__ == "__main__":
    unittest.main()
