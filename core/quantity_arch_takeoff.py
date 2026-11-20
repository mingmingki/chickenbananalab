"""Architectural takeoff by floor from CAD (no AI): walls by material and the windows and doors.

Built from the measures of each architectural plan (quantity_arch.measure, kept on the floor-view)
and the storey heights of the structural model (quantity_floor_views.build_model):

* wall area of each material (조적, ALC, 건식벽) = wall length × clear height (the storey height less
  the slab above it), plus the wall left above each door and above/below each window that sits in
  that material (the plan's wall lines break at openings, so their length leaves them out);
* windows and doors counted by type on each storey (the tag beside each one, else its block name);
  a plan for "2~5층" counts for each of those storeys. A type found in the window and door
  schedule (창호일람표, quantity_arch_schedule) takes its width and height from there; the others
  keep the width of their block and an assumed height. Each type's area (width × height) adds up
  to the window and door area of the storey, and the counts are set against the schedule's.

Concrete walls belong to the structural takeoff. Unit plans (단위세대) are not floors of a building
and are left out. What is assumed (heights of doors and windows, the slab under the walls, default
storey heights) is listed and can be changed (quantity_assumed).
"""
from __future__ import annotations

from . import quantity_floor_views as fv
from .quantity_assumed import ARCH_KEYS, Assumed, clean_overrides, pick, said

ARCH_TAKEOFF_VERSION = 2
WALL_KINDS = ("masonry", "alc", "dry")
WALL_LABELS = {"masonry": "조적", "alc": "ALC", "dry": "건식벽"}
SLAB_MM = 210.0          # the slab above a wall: walls run from floor to slab soffit
DOOR_HEIGHT = 2.1        # the wall left above a door is the clear height less this
WINDOW_HEIGHT = 1.5      # the wall left below and above a window in a partition


def _label(key):
    if key.startswith("B") and key[1:].isdigit():
        return f"지하{key[1:]}층"
    if key.isdigit():
        return f"{key}층"
    return {"R": "지붕층", "PH": "옥탑층", "PHR": "옥탑지붕"}.get(key, key)


def _wall_length(view):
    return sum((w or {}).get("length") or 0 for w in ((view.get("arch") or {}).get("walls") or {}).values())


def _sized(opening, types, door_h, window_h):
    """The opening with the size it is counted at: the schedule's, else its block width and the
    assumed height."""
    listed = types.get(opening["name"]) or {}
    door = opening["kind"] == "door"
    width = listed.get("width") or opening["width"]
    height = listed.get("height") or (door_h if door else window_h)
    return dict(opening, width=round(width, 3), height=round(height, 3), block_width=opening["width"],
                width_from="schedule" if listed.get("width") else "block",
                height_from="schedule" if listed.get("height") else "assumed",
                area=round(width * height * opening["count"], 2))


def build(views, model=None, overrides=None, learned=None, schedule=None):
    """{"buildings": [{"name", "rows": [per storey], "totals", "openings": [by type]}], "assumed",
    "schedule": the check against the window and door schedule, ...}."""
    types = (schedule or {}).get("types") or {}
    overrides = clean_overrides(overrides)
    slab_mm, slab_from = pick("slab_thickness", overrides, learned, SLAB_MM)
    door_h, door_from = pick("door_height", overrides, learned, DOOR_HEIGHT)
    window_h, window_from = pick("window_opening_height", overrides, learned, WINDOW_HEIGHT)
    slab = slab_mm / 1000.0
    levels = {(b.get("name"), l.get("key")): l for b in (model or {}).get("buildings") or [] for l in b.get("levels") or []}
    heights_drawn = bool((model or {}).get("heights_from_drawings"))
    chosen = {}
    for view in views or []:
        arch = view.get("arch") or {}
        if view.get("discipline") != "arch" or not arch or arch.get("error") or view.get("group") in ("unit", "other"):
            continue
        for key in fv._storey_keys(view):
            spot = (view.get("building"), key)
            if spot not in chosen or _wall_length(view) > _wall_length(chosen[spot]):
                chosen[spot] = view  # of two sheets for one floor, the one drawing more walls
    assumed = Assumed()
    buildings, default_heights = {}, []
    for (name, key), view in sorted(chosen.items(), key=lambda item: (item[0][0] or "", fv._level_order(item[0][1]))):
        level = levels.get((name, key))
        group = "basement" if key.startswith("B") else ("roof" if key in ("R", "PH", "PHR") else "ground")
        height = float(level.get("height")) if level and level.get("height") else fv.DEFAULT_STOREY_M[group]
        clear = max(height - slab, 0.0)
        arch = view["arch"]
        openings = [_sized(o, types, door_h, window_h) for o in arch.get("openings") or []]
        walls, notes = {}, []
        guessed = {"door": 0.0, "window": 0.0}  # wall area resting on an assumed opening height
        for kind in WALL_KINDS:
            wall = (arch.get("walls") or {}).get(kind)
            if not wall:
                continue
            area = wall["length"] * clear
            above = 0.0
            for opening in openings:
                on = (opening.get("on") or {}).get(kind) or 0
                if on:
                    part = on * opening["width"] * max(clear - opening["height"], 0.0)
                    above += part
                    if opening["height_from"] == "assumed":
                        guessed[opening["kind"]] += part
            walls[kind] = {"length": round(wall["length"], 2), "thickness": wall["thickness"],
                           "area": round(area + above, 2), "above_openings": round(above, 2)}
        doors = sum(o["count"] for o in openings if o["kind"] == "door")
        windows = sum(o["count"] for o in openings if o["kind"] == "window")
        storey = _label(key) if not level else (level.get("label") or _label(key))
        row = {"key": key, "label": storey, "height": round(height, 3), "clear": round(clear, 3), "walls": walls,
               "doors": doors, "windows": windows, "sheet": view.get("sheet"), "plan_id": view.get("plan_id"),
               "door_area": round(sum(o["area"] for o in openings if o["kind"] == "door"), 2),
               "window_area": round(sum(o["area"] for o in openings if o["kind"] == "window"), 2),
               "openings": openings, "notes": notes}
        if any(w["above_openings"] for w in walls.values()):
            notes.append("문 위 벽, 창 위·아래 벽 포함 (" + ("창호일람표 높이" if not any(guessed.values()) else
                         f"일람표에 없는 종류는 문 {door_h:g}m · 창 {window_h:g}m로 봤어요") + ")")
        buildings.setdefault(name, []).append(row)
        wall_area = sum(w["area"] for w in walls.values())
        if wall_area:
            assumed.add("slab_thickness", "벽 위 슬래브 두께 (벽 높이 = 층고 − 슬래브)", said(f"{slab_mm:.0f}mm", slab_from), name, storey,
                        sum(w["length"] for w in walls.values()) * slab, "㎡", "구조 슬래브 두께와 같은 값이에요",
                        raw=round(slab_mm), person=slab_from == "person")
        # the opening heights assumed for the types the schedule does not give (their own area rests on it too)
        door_area = guessed["door"] + sum(o["area"] for o in openings if o["kind"] == "door" and o["height_from"] == "assumed")
        window_area = guessed["window"] + sum(o["area"] for o in openings if o["kind"] == "window" and o["height_from"] == "assumed")
        if door_area > 0.001:
            assumed.add("door_height", "문 높이 (창호일람표에 없는 문)", said(f"{door_h:g}m", door_from), name, storey, door_area, "㎡",
                        "창호일람표 CAD를 올리면 종류별 높이를 써요", raw=door_h, person=door_from == "person")
        if window_area > 0.001:
            assumed.add("window_opening_height", "창 높이 (창호일람표에 없는 창)", said(f"{window_h:g}m", window_from), name, storey, window_area, "㎡",
                        "창호일람표 CAD를 올리면 종류별 높이를 써요", raw=window_h, person=window_from == "person")
        if not heights_drawn and wall_area:
            default_heights.append((name, storey, round(height, 3), wall_area))
    if default_heights:  # the heights used, storey by storey: "1층 3.25m · 2층~5층 2.85m"
        by_height = {}
        for _name, storey, height, _area in default_heights:
            floors = by_height.setdefault(height, [])
            if storey not in floors:
                floors.append(storey)
        text = " · ".join((f[0] if len(f) == 1 else f"{f[0]}~{f[-1]}") + f" {h:g}m" for h, f in by_height.items()) + " (기본값)"
        for name, storey, _height, area in default_heights:
            assumed.add("arch_storey_height", "층고", text, name, storey, area, "㎡", "단면도·입면도의 층 레벨(EL)을 올리면 그 높이를 써요")
    out = []
    for name, rows in buildings.items():
        totals = {kind: round(sum(r["walls"].get(kind, {}).get("area", 0) for r in rows), 1) for kind in WALL_KINDS}
        totals.update(doors=sum(r["doors"] for r in rows), windows=sum(r["windows"] for r in rows),
                      door_area=round(sum(r["door_area"] for r in rows), 2), window_area=round(sum(r["window_area"] for r in rows), 2))
        by_type = {}
        for r in rows:
            for o in r["openings"]:
                item = by_type.setdefault(o["name"], {
                    "name": o["name"], "kind": o["kind"], "count": 0, "width": o["width"], "height": o["height"], "area": 0.0,
                    "width_from": o["width_from"], "height_from": o["height_from"], "blocks": list(o.get("blocks") or []),
                    "schedule_count": (types.get(o["name"]) or {}).get("count"), "floors": []})
                item["count"] += o["count"]
                item["area"] = round(item["area"] + o["area"], 2)
                item["floors"].append(r["label"])
        out.append({"name": name, "rows": rows, "totals": totals,
                    "openings": sorted(by_type.values(), key=lambda t: (t["kind"], t["name"]))})
    assumed.keep_unused(overrides, ("door_height", "window_opening_height"))
    check = schedule_check(out, types)
    missing = ["콘크리트 벽(구조 산출에 포함)", "미장·도장·타일 등 마감"]
    if not types:
        missing.append("창호일람표(없어서 창·문 폭은 블록 크기, 높이는 가정값으로 봤어요)")
    elif check["not_listed"]:
        missing.append("창호일람표에 없는 종류의 크기(블록 폭·가정 높이로 봤어요)")
    return {"version": ARCH_TAKEOFF_VERSION, "buildings": out, "assumed": assumed.items(), "overrides": overrides,
            "wall_labels": WALL_LABELS, "schedule": check, "not_included": missing}


def schedule_check(buildings, types):
    """The plans against the window and door schedule: each type's count in the plans (all
    buildings) and in the schedule, the schedule's types the plans never show, and the plan types
    the schedule does not list."""
    counted = {}
    for building in buildings:
        for item in building["openings"]:
            counted[item["name"]] = counted.get(item["name"], 0) + item["count"]
    rows = []
    for key in sorted(set(counted) | set(types), key=lambda k: ((types.get(k) or {}).get("kind") or "", k)):
        listed = types.get(key) or {}
        rows.append({"code": key, "kind": listed.get("kind"), "plans": counted.get(key, 0), "schedule": listed.get("count"),
                     "width": listed.get("width"), "height": listed.get("height"), "listed": key in types})
    return {"read": bool(types), "types": rows,
            "matched": sum(1 for r in rows if r["listed"] and r["plans"]),
            "not_in_plans": [r["code"] for r in rows if r["listed"] and not r["plans"]],
            "not_listed": [r["code"] for r in rows if not r["listed"]],
            "differ": [r["code"] for r in rows if r["listed"] and r["plans"] and r["schedule"] is not None and r["schedule"] != r["plans"]]}
