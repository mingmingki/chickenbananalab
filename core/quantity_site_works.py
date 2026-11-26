"""Earthwork and the outside of the buildings (no AI), from the floor takeoff and the 3D model.

Earthwork (토공) — per excavation: a building on its own, or a parking deck with the towers
standing in it (their mats go deeper than the deck's):

* depth = the ground level (GL from the sections and elevations, else the ground floor's level,
  or a parking deck's roof) less the bottom: the lowest floor less the foundation's thickness
  and the lean concrete (60 mm); a building without a basement digs at least the frost depth
  (0.9 m);
* excavation = the footprint (the foundation's outline) widened by the working space each side
  (1.0 m), sloped 1 : 1.0 for an open cut or straight down behind shoring (흙막이), plus the
  deeper pits under the towers' mats;
* backfill (되메우기) = the excavation less what is built below the ground (the basement box and
  the foundation) and the lean concrete; surplus soil (잔토처리) = the rest, also loosened (×1.25);
* shoring (흙막이) = the excavation's outline × its depth (the toe below the bottom not included);
* lean concrete (버림콘크리트) = the footprint and 0.1 m round it × 60 mm.

The outside (외부):

* outer wall finish = each storey's outer wall faces × its storey height, less the openings in
  them (their length × the opening height the takeoff used);
* basement outer wall waterproofing = the basement storeys' outer wall faces × their height
  (the deck's, for towers standing in a deck);
* roof waterproofing = the roof slab (the slab over the top storey below the roof) and a 0.3 m
  rise round its edge; roof insulation = the roof slab. A parking deck's roof is waterproofed too.

Every value it assumes is listed (quantity_assumed); the method, the working space, the slope and
the outer finish can be changed in place.
"""
from __future__ import annotations

from .quantity_assumed import pick

LEAN_M = 0.06
LEAN_MARGIN = 0.1
FROST_M = 0.9
MARGIN_M = 1.0          # working space round the foundation
SLOPE = 1.0             # 1 : n for an open cut
LOOSE = 1.25            # 토량환산계수 L
ROOF_RISE = 0.3
ROOF_KEYS = ("R", "PH", "PHR")
METHODS = ("오픈컷", "흙막이 (수직 굴착)")
EXTERIOR_FINISHES = ("외부 수성페인트", "석재", "외단열 미장", "금속패널", "타일")


def _levels_and_ground(building):
    """(lowest floor's level, ground level, where the ground came from, has a basement) from the
    3D model: the ground is the drawn GL, else the ground floor (a deck's roof), else the lowest."""
    levels = building.get("levels") or []
    elev = {l.get("key"): float(l.get("elev") or 0.0) for l in levels}
    basement = [elev[k] for k in elev if (k or "").startswith("B")]
    stacked = sum(float(l.get("height") or 0) for l in levels if (l.get("key") or "").startswith("B"))
    floor = min(basement) if basement else elev.get("1", min(elev.values()) if elev else 0.0)
    if building.get("ground_elev") is not None:
        ground, source = float(building["ground_elev"]), "drawing"
    elif "1" in elev:
        ground, source = elev["1"], "first_floor"
    elif "R" in elev and basement:
        ground, source = elev["R"], "deck_roof"
    else:
        ground, source = floor + stacked, "first_floor"
    if basement and floor >= ground - 0.01:
        # levels without elevations: the basement storeys' heights stacked under the ground
        floor = ground - stacked
    return floor, ground, source, bool(basement)


def _method(overrides, excavation, deepest):
    chosen = (overrides or {}).get("excavation_method")
    if chosen in METHODS:
        return chosen, "person"
    text = str(excavation or "")
    if "오픈" in text:
        return METHODS[0], "answer"
    if "CIP" in text.upper() or "흙막이" in text:
        return METHODS[1], "answer"
    return (METHODS[1] if deepest > 5.0 else METHODS[0]), "assumed"


def earthwork(buildings, model_buildings, host_name, match, overrides=None, learned=None, excavation=None, assumed=None):
    """{"method", "groups": [{"name", "parts", "area", "perimeter", "depth", "excavation", "backfill",
    "surplus", "surplus_loose", "lean", "shoring_length", "shoring_area"}], "totals"}."""
    margin, margin_from = pick("excavation_margin", overrides, learned, MARGIN_M)
    slope, slope_from = pick("excavation_slope", overrides, learned, SLOPE)
    levels = {b.get("name"): b for b in model_buildings or []}
    parts = {}
    for building in buildings:
        foundation = next((r.get("foundation") for r in building.get("rows") or [] if r.get("key") == "FT"), None)
        if not foundation or not foundation.get("area"):
            continue
        floor, ground, ground_from, has_basement = _levels_and_ground(levels.get(building["name"]) or {})
        thickness = float(foundation.get("thickness") or 0)
        bottom = floor - thickness - LEAN_M
        depth = ground - bottom if has_basement else max(ground - bottom, FROST_M)
        parts[building["name"]] = {"name": building["name"], "area": float(foundation["area"]),
                                   "perimeter": float(foundation.get("perimeter") or 0), "depth": round(depth, 3),
                                   "below": max(min(depth - LEAN_M, ground - (floor - thickness)), 0.0), "basement": has_basement,
                                   "ground": round(ground, 3), "ground_from": ground_from}
    groups = []
    inside = set(match or {}) if host_name in parts else set()
    for name, part in parts.items():
        if name in inside:
            continue
        members = [part] + [parts[t] for t in sorted(inside) if name == host_name and t in parts]
        groups.append({"name": name, "members": members})
    deepest = max((p["depth"] for p in parts.values()), default=0.0)
    method, method_from = _method(overrides, excavation, deepest)
    shored = method == METHODS[1]
    out, totals = [], dict.fromkeys(("excavation", "backfill", "surplus", "surplus_loose", "lean", "shoring_length", "shoring_area"), 0.0)
    for group in groups:
        base = group["members"][0]
        area = sum(m["area"] for m in group["members"])
        perimeter = base["perimeter"] or 4 * area ** 0.5
        depth = base["depth"]
        widened = area + perimeter * margin + 4 * margin * margin
        volume = depth * widened
        if not shored:
            volume += slope * depth * depth * (perimeter + 8 * margin) / 2 + 4 * slope * slope * depth ** 3 / 3
        pits = sum(m["area"] * max(m["depth"] - depth, 0.0) for m in group["members"][1:])  # the towers' deeper mats
        volume += pits
        built = sum(m["area"] * m["below"] for m in group["members"])
        lean = (area + perimeter * LEAN_MARGIN) * LEAN_M
        backfill = max(volume - built - lean, 0.0)
        surplus = volume - backfill
        shoring_length = perimeter + 8 * margin if shored else 0.0
        item = {"name": group["name"], "parts": [m["name"] for m in group["members"]], "area": round(area, 1),
                "ground_from": base["ground_from"], "ground": base["ground"],
                "perimeter": round(perimeter, 1), "depth": round(depth, 2), "pits": round(pits, 1),
                "excavation": round(volume, 1), "backfill": round(backfill, 1), "surplus": round(surplus, 1),
                "surplus_loose": round(surplus * LOOSE, 1), "lean": round(lean, 2),
                "shoring_length": round(shoring_length, 1), "shoring_area": round(shoring_length * depth, 1)}
        out.append(item)
        for key in totals:
            totals[key] += item[key]
        if assumed is not None:
            where = group["name"]
            if base["ground_from"] != "drawing":
                assumed.add("ground_level", "지반고 (GL)",
                            ("주차장 지붕 = 지반" if base["ground_from"] == "deck_roof" else "1층 바닥 = 지반") + " (굴착 깊이 = 지반 − 맨 아래층 바닥 + 기초 + 버림 60mm)",
                            where, "토공", volume, "㎥", "단면도·입면도에 GL 레벨(예: GL±0=EL+16.20)을 함께 올리면 그 높이를 써요")
            assumed.add("excavation_margin", "작업 여유폭", f"{margin:g}m" + {"person": " (입력값)", "learned": " (다른 현장 입력값)"}.get(margin_from, ""),
                        where, "토공", (perimeter + 4 * margin) * margin * depth, "㎥", "흙막이·기초 상세도의 여유폭으로 바꿔 주세요",
                        raw=margin, person=margin_from == "person")
            if not shored:
                assumed.add("excavation_slope", "굴착 경사 (1 : n)", f"1 : {slope:g}" + {"person": " (입력값)", "learned": " (다른 현장 입력값)"}.get(slope_from, ""),
                            where, "토공", slope * depth * depth * (perimeter + 8 * margin) / 2, "㎥",
                            "지반조사서·토공 계획의 경사로 바꿔 주세요", raw=slope, person=slope_from == "person")
            if not base["basement"]:
                assumed.add("frost_depth", "지하층 없는 동의 굴착 깊이", f"기초 두께 + 버림, 동결심도 {FROST_M:g}m 이상", where, "토공",
                            volume, "㎥", "기초 상세도의 기초 바닥 레벨로 바꿔야 해요 (다음 단계)")
    if assumed is not None and out:
        assumed.add("excavation_method", "굴착 방식",
                    method + {"person": " (입력값)", "answer": " (프로젝트 확인에서 고른 터파기 방식)", "assumed": " (지하 5m 넘으면 흙막이로 봤어요)"}[method_from],
                    out[0]["name"], "토공", totals["excavation"], "㎥", "오픈컷과 흙막이 중 실제 공법으로 골라 주세요",
                    raw=method, person=method_from == "person")
        assumed.add("loose_factor", "잔토 운반 (토량환산계수 L)", f"{LOOSE:g}", out[0]["name"], "토공", totals["surplus"], "㎥",
                    "흙의 종류에 따라 1.1~1.3이에요")
    return {"method": method, "margin": margin, "slope": slope if not shored else None, "groups": out,
            "totals": {k: round(v, 1) for k, v in totals.items()}}


def exterior(buildings, match, overrides=None, assumed=None):
    """{"finish", "buildings": [{"name", "wall", "openings", "basement_waterproof", "roof", "rows"}], "totals"}."""
    chosen = (overrides or {}).get("exterior_finish")
    finish = chosen if chosen in EXTERIOR_FINISHES else EXTERIOR_FINISHES[0]
    inside = set(match or {})
    out = []
    totals = dict.fromkeys(("wall", "openings", "basement_waterproof", "roof_waterproof", "roof_insulation"), 0.0)
    for building in buildings:
        name = building.get("name")
        rows = [r for r in building.get("rows") or [] if r.get("key") != "FT"]
        lines, wall_sum, openings_sum, basement_sum = [], 0.0, 0.0, 0.0
        for row in rows:
            measures = row.get("measures") or {}
            faces, height = float(measures.get("outside_faces") or 0), float(row.get("height") or 0)
            if not faces or not height:
                continue
            if (row.get("key") or "").startswith("B"):
                if name in inside:
                    continue  # a tower's basement is the parking deck's
                proof = faces * height
                basement_sum += proof
                lines.append({"key": row["key"], "label": row.get("label"), "wall": 0.0, "openings": 0.0, "basement_waterproof": round(proof, 1)})
                continue
            gross = faces * height
            openings = min(float(measures.get("window_gaps") or 0) * float(measures.get("opening_height") or 0), gross)
            wall_sum += gross - openings
            openings_sum += openings
            lines.append({"key": row["key"], "label": row.get("label"), "wall": round(gross - openings, 1), "openings": round(openings, 1),
                          "basement_waterproof": 0.0})
        roof_row = next((r for r, nxt in zip(rows, rows[1:]) if nxt.get("key") in ROOF_KEYS and r.get("key") not in ROOF_KEYS), None)
        roof_row = roof_row or (rows[-1] if rows else None)
        roof_area = float(((roof_row or {}).get("measures") or {}).get("slab_area") or 0)
        roof_edge = float(((roof_row or {}).get("measures") or {}).get("outside_faces") or 0) or 4 * roof_area ** 0.5
        roof = {"area": round(roof_area, 1), "perimeter": round(roof_edge, 1), "storey": (roof_row or {}).get("label"),
                "waterproof": round(roof_area + roof_edge * ROOF_RISE, 1) if roof_area else 0.0, "insulation": round(roof_area, 1)}
        out.append({"name": name, "wall": round(wall_sum, 1), "openings": round(openings_sum, 1),
                    "basement_waterproof": round(basement_sum, 1), "roof": roof, "rows": lines})
        totals["wall"] += wall_sum
        totals["openings"] += openings_sum
        totals["basement_waterproof"] += basement_sum
        totals["roof_waterproof"] += roof["waterproof"]
        totals["roof_insulation"] += roof["insulation"]
        if assumed is not None:
            if wall_sum:
                assumed.add("exterior_finish", "외벽 마감재", finish + (" (입력값)" if chosen else " (가정)"), name, "외부", wall_sum, "㎡",
                            "입면도·외부마감표의 외벽 마감으로 골라 주세요", raw=finish, person=bool(chosen))
            if roof["area"]:
                assumed.add("roof_spec", "지붕 방수·단열", f"슬래브 면적 + 치켜올림 {ROOF_RISE:g}m (방수), 슬래브 면적 (단열)", name, "외부",
                            roof["waterproof"], "㎡", "지붕 상세도의 방수 공법·치켜올림 높이와 다르면 엑셀에서 바꿔 주세요")
    return {"finish": finish, "buildings": out, "totals": {k: round(v, 1) for k, v in totals.items()}}
