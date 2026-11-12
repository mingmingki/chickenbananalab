"""People's fixes to how a plan was read: members added where the reading missed them (a concrete
beam, a wall, a column) and members taken out where it read something that is not one; and
openings people cut into a wall (a door or a window the plan did not show as a gap).

A fix is drawn on one plan frame (``plan`` = the frame's id) in that sheet's own coordinates
(metres, x right, y up from the frame's lower left), the same coordinates the plan picture and the
3D model start from. The takeoff adds each fix to the storeys that use the plan, by the same rule
as everything else ("N층 = N층 벽·기둥 + N+1층 바닥 슬래브·보"): a wall or a column on the plan of
storey N, a beam on the plan above it. Each fix is shown as its own line of the storey's notes, so
the reasoning stays visible.
"""
from __future__ import annotations

import math
import uuid

KINDS = ("beam", "wall", "column", "opening")
OPS = ("add", "remove")
MAX_EDITS = 3000
MAX_LENGTH_M = 400.0
SIZE_M = (0.05, 3.0)
KIND_LABEL = {"beam": "보", "wall": "벽", "column": "기둥", "opening": "개구부"}
OPENING_H_M = (0.3, 6.0)
OPENING_H_DEFAULT = 2.1


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)) or abs(x) > 1e6 or abs(y) > 1e6:
        return None
    return [round(x, 3), round(y, 3)]


def _size(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return round(min(max(number, SIZE_M[0]), SIZE_M[1]), 3)


def clean(edits):
    """Valid fixes only, numbers bounded; unknown fields dropped."""
    out = []
    for raw in (edits or [])[:MAX_EDITS]:
        if not isinstance(raw, dict):
            continue
        kind, op, plan = raw.get("kind"), raw.get("op"), raw.get("plan")
        if kind not in KINDS or op not in OPS or not isinstance(plan, str) or not plan.strip():
            continue
        if kind == "opening" and op != "add":
            continue  # an opening is taken back by deleting the fix
        edit = {"id": str(raw.get("id") or uuid.uuid4().hex[:10])[:40], "plan": plan.strip()[:40], "op": op, "kind": kind}
        if kind == "column":
            centre = _point(raw.get("c"))
            if centre is None:
                continue
            edit.update(c=centre, w=_size(raw.get("w"), 0.5), h=_size(raw.get("h"), 0.5))
        else:
            a, b = _point(raw.get("a")), _point(raw.get("b"))
            if a is None or b is None:
                continue
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if not 0.05 <= length <= MAX_LENGTH_M:
                continue
            edit.update(a=a, b=b)
            if kind == "beam":
                edit.update(w=_size(raw.get("w"), 0.4), d=_size(raw.get("d"), 0.6))
            elif kind == "opening":
                try:
                    height = float(raw.get("h"))
                except (TypeError, ValueError):
                    height = OPENING_H_DEFAULT
                if not math.isfinite(height):
                    height = OPENING_H_DEFAULT
                edit.update(t=_size(raw.get("t"), 0.2), h=round(min(max(height, OPENING_H_M[0]), OPENING_H_M[1]), 3))
            else:
                edit.update(t=_size(raw.get("t"), 0.2))
        layer = raw.get("layer")
        if isinstance(layer, str) and layer.strip():
            edit["layer"] = layer.strip()[:200]  # the drawn line it was taken from, for learning later
        out.append(edit)
    return out


def by_plan(edits):
    out = {}
    for edit in edits or []:
        out.setdefault(edit["plan"], []).append(edit)
    return out


def length(edit):
    if edit["kind"] == "column":
        return 0.0
    return math.hypot(edit["b"][0] - edit["a"][0], edit["b"][1] - edit["a"][1])


def effect(edit, storey_kind, wall_height, slab_thickness):
    """(concrete {key: m³}, formwork {key: m²}) one fix adds (or takes out) on one storey."""
    sign = -1.0 if edit["op"] == "remove" else 1.0
    face_key = "euro" if storey_kind in ("basement", "roof") else "al_vertical"
    if edit["kind"] == "wall":
        run = length(edit)
        return ({"wall": sign * run * edit["t"] * wall_height}, {face_key: sign * 2 * run * wall_height})
    if edit["kind"] == "column":
        return ({"column": sign * edit["w"] * edit["h"] * wall_height},
                {face_key: sign * 2 * (edit["w"] + edit["h"]) * wall_height})
    if edit["kind"] == "opening":
        # the wall where the opening is, out; the opening's sides and head take formwork instead
        run, high = length(edit), min(edit["h"], wall_height)
        return ({"wall": -run * edit["t"] * high},
                {face_key: -2 * run * high + edit["t"] * (run + 2 * high)})
    drop = max(edit["d"] - slab_thickness, 0.0)
    run = length(edit)
    beam_key = "plywood" if storey_kind == "basement" else "al_vertical"
    return ({"beam": sign * edit["w"] * drop * run}, {beam_key: sign * (2 * drop + edit["w"]) * run})


def note(edits, concrete, formwork):
    added = [e for e in edits if e["op"] == "add"]
    removed = [e for e in edits if e["op"] == "remove"]

    def count(items):
        kinds = {}
        for e in items:
            kinds[KIND_LABEL[e["kind"]]] = kinds.get(KIND_LABEL[e["kind"]], 0) + 1
        return ", ".join(f"{k} {n}개" for k, n in kinds.items())
    parts = []
    if added:
        parts.append(count(added) + " 추가")
    if removed:
        parts.append(count(removed) + " 지움")
    return f"사람이 고친 것: {' · '.join(parts)} (콘크리트 {concrete:+.2f}㎥, 거푸집 {formwork:+.1f}㎡)"
