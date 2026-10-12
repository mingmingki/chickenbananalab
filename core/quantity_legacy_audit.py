"""Read-only diagnosis of legacy geometry; never mutates or approves a takeoff."""
from collections import Counter


def diagnose_workspace(workspace):
    scene = workspace.get("scene") or {}
    objects = scene.get("objects") or []
    groups = scene.get("review_groups") or []
    rows = workspace.get("takeoff_rows") or []
    missing_groups = sum(not row.get("review_group_id") for row in objects)
    unclassified = sum(row.get("classification_status") in (None, "", "unclassified") for row in rows)
    missing_evidence = sum(row.get("source_evidence_status") in (None, "", "missing") for row in rows)
    technical = sum(group.get("blocker") not in (None, "", "review_required") for group in groups)
    technical_marked = sum(bool(group.get("technical_only")) for group in groups)
    return {
        "schema": "quantity_legacy_diagnostic_v1", "read_only": True,
        "scene_objects": len(objects), "review_groups": len(groups),
        "groups_by_blocker": dict(sorted(Counter(str(g.get("blocker") or "unspecified") for g in groups).items())),
        "technical_groups": technical, "technical_marked_groups": technical_marked,
        "human_review_groups": len(groups) - technical,
        "objects_by_layer": dict(sorted(Counter(str(o.get("layer") or "unknown") for o in objects).items())),
        "objects_without_group": missing_groups,
        "unclassified_objects": unclassified, "source_evidence_missing": missing_evidence,
        "supported_rows": sum(bool(row.get("quantity_eligibility")) for row in rows),
        "ready_for_quantity": bool(objects) and len(rows) == len(objects) and
            not missing_groups and not unclassified and not missing_evidence and
            all(row.get("quantity_eligibility") for row in rows),
        "next_actions": [
            "Recover original drawing/source positions before classifying diagnostic faces.",
            "Resolve the technical blockers separately from human interpretation.",
            "Read explicit plan occurrence/count evidence and schedule definitions; do not count table faces.",
            "Compare supported quantities with an authoritative manual takeoff before production use."],
    }
