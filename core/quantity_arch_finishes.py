"""Finish quantities by room (no AI): floor, wall and ceiling finishes, baseboard, waterproofing and
plaster, from the rooms of the architectural plans (quantity_arch_rooms).

What each room is finished with follows its name, by the usual choice for Korean housing until a
finish schedule (실내재료마감표) is read:

=================  ===========  ==========  ============  ========  ===========
room               floor        wall        ceiling       baseboard waterproofing
=================  ===========  ==========  ============  ========  ===========
거실·침실·주방      강마루        벽지         천장지         yes       -
욕실·세탁실         바닥타일      벽타일       욕실 천장재     -         floor + rise (1.2 m)
발코니·실외기실      바닥타일      수성페인트    수성페인트      -         floor + 0.3 m
현관               바닥타일      벽지         천장지         -         -
계단실·홀·복도       바닥타일      수성페인트    수성페인트      yes       -
기계·전기실·창고      에폭시        수성페인트    수성페인트      -         -
=================  ===========  ==========  ============  ========  ===========

Wall finish = perimeter × the finish height (the ceiling height, at most the clear storey
height) less the windows and doors in that room's walls; plaster (미장) goes on the masonry and
ALC faces of the room (concrete walls are finished directly). Rooms without a name are kept
out of the finishes and listed, so a person sees what is missing; shafts get none.
"""
from __future__ import annotations

RULES = {
    "room": {"floor": "강마루", "wall": "벽지", "ceiling": "천장지", "base": True, "waterproof": None},
    "wet": {"floor": "바닥타일", "wall": "벽타일", "ceiling": "욕실 천장재", "base": False, "waterproof": "wet"},
    "balcony": {"floor": "바닥타일", "wall": "수성페인트", "ceiling": "수성페인트", "base": False, "waterproof": "balcony"},
    "entrance": {"floor": "바닥타일", "wall": "벽지", "ceiling": "천장지", "base": False, "waterproof": None},
    "common": {"floor": "바닥타일", "wall": "수성페인트", "ceiling": "수성페인트", "base": True, "waterproof": None},
    "service": {"floor": "에폭시", "wall": "수성페인트", "ceiling": "수성페인트", "base": False, "waterproof": None},
}
CLASS_LABELS = {"room": "거실·침실·주방", "wet": "욕실·세탁실", "balcony": "발코니", "entrance": "현관",
                "common": "계단실·홀·복도", "service": "기계·전기실·창고", "shaft": "샤프트", None: "이름 없는 실"}
CEILING_HEIGHT = 2.3       # the finish height of the walls (반자 높이)
WATERPROOF_RISE = 1.2      # waterproofing up the walls of a wet room
BALCONY_RISE = 0.3         # ... and of a balcony
PLASTER_KINDS = ("masonry", "alc")


def _add(bucket, key, value):
    if value > 0:
        bucket[key] = round(bucket.get(key, 0.0) + value, 2)


def storey_finishes(rooms, clear, sizes, ceiling_h=CEILING_HEIGHT, rise=WATERPROOF_RISE):
    """{"floor"/"wall"/"ceiling": {material: m²}, "base": m, "waterproof": m², "plaster": m²,
    "rooms": [per room], "unnamed": {"count", "area"}, "wall_by_height": m² resting on the
    ceiling height, "rise_area": m² resting on the rise} of one storey.

    rooms: quantity_arch_rooms.find_rooms(...); clear: the clear storey height (m);
    sizes(name, kind, width) → (width, height) of a window or door as counted."""
    height = min(ceiling_h, clear) if clear else ceiling_h
    out = {"floor": {}, "wall": {}, "ceiling": {}, "base": 0.0, "waterproof": 0.0, "plaster": 0.0,
           "rooms": [], "unnamed": {"count": 0, "area": 0.0}, "wall_by_height": 0.0, "rise_area": 0.0}
    for room in rooms or []:
        kind = room.get("class")
        rule = RULES.get(kind)
        area, perimeter = float(room.get("area") or 0), float(room.get("perimeter") or 0)
        if kind == "shaft":
            continue
        if rule is None:
            out["unnamed"]["count"] += 1
            out["unnamed"]["area"] = round(out["unnamed"]["area"] + area, 2)
            out["rooms"].append({"names": room.get("names") or [], "class": None, "area": area, "perimeter": perimeter})
            continue
        gross = perimeter * height
        openings = 0.0
        doors = 0.0
        for opening in room.get("openings") or []:
            width, tall = sizes(opening.get("name"), opening.get("kind"), float(opening.get("width") or 0))
            openings += width * min(tall, height)
            if opening.get("kind") == "door":
                doors += width
        wall = max(gross - openings, 0.0)
        faces = room.get("faces") or {}
        plaster_faces = sum(float(faces.get(k) or 0) for k in PLASTER_KINDS)
        plaster = plaster_faces * height * (wall / gross if gross else 0.0)
        base = max(perimeter - doors, 0.0) if rule["base"] else 0.0
        waterproof = 0.0
        if rule["waterproof"] == "wet":
            waterproof = area + perimeter * rise
            out["rise_area"] += perimeter * rise
        elif rule["waterproof"] == "balcony":
            waterproof = area + perimeter * BALCONY_RISE
        _add(out["floor"], rule["floor"], area)
        _add(out["wall"], rule["wall"], wall)
        _add(out["ceiling"], rule["ceiling"], area)
        out["base"] += base
        out["waterproof"] += waterproof
        out["plaster"] += plaster
        out["wall_by_height"] += wall
        out["rooms"].append({"names": room.get("names") or [], "class": kind, "area": round(area, 2), "perimeter": round(perimeter, 2),
                             "floor": rule["floor"], "wall_finish": rule["wall"], "ceiling": rule["ceiling"],
                             "wall": round(wall, 2), "openings": round(openings, 2), "base": round(base, 2),
                             "waterproof": round(waterproof, 2), "plaster": round(plaster, 2)})
    for key in ("base", "waterproof", "plaster", "wall_by_height", "rise_area"):
        out[key] = round(out[key], 2)
    out["height"] = round(height, 3)
    return out


def add_up(parts):
    """The finishes of several storeys added (materials by kind of surface, the rest summed)."""
    total = {"floor": {}, "wall": {}, "ceiling": {}, "base": 0.0, "waterproof": 0.0, "plaster": 0.0,
             "unnamed": {"count": 0, "area": 0.0}, "rooms": 0}
    for part in parts:
        if not part:
            continue
        for surface in ("floor", "wall", "ceiling"):
            for material, value in (part.get(surface) or {}).items():
                _add(total[surface], material, value)
        for key in ("base", "waterproof", "plaster"):
            total[key] = round(total[key] + float(part.get(key) or 0), 2)
        total["unnamed"]["count"] += (part.get("unnamed") or {}).get("count") or 0
        total["unnamed"]["area"] = round(total["unnamed"]["area"] + ((part.get("unnamed") or {}).get("area") or 0), 2)
        total["rooms"] += len([r for r in part.get("rooms") or [] if r.get("class")])
    return total
