import ai_exit_plan_audit as audit


def test_legacy_requested_copy_is_not_counted_as_exchange_verified(tmp_path):
    audit.append_record(str(tmp_path), {
        "engine":"CORE","symbol":"PI/USDT:USDT","side":"long",
        "result":"ai_exit_plan_applied","exchange_verified":True,
        "actual_sl":90.0,"actual_tp":120.0,"final_sl":90.0,"final_tp":120.0,
    })
    out=audit.summarize(str(tmp_path),limit=10,lifecycles=[])
    row=out["recent"][0]
    assert row["exchange_verified"] is False
    assert row["exchange_verification_reason"] == "legacy_requested_copy_unverified"
