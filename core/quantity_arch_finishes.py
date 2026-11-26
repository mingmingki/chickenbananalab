"""Finish quantities by room (no AI): floor, wall and ceiling finishes, baseboard, waterproofing and
plaster, from the rooms of the architectural plans (quantity_arch_rooms).

What each room is finished with comes from the finish schedule (실내재료마감표,
quantity_arch_finish_schedule) when its row names the room: its floor, baseboard, wall and
ceiling finish, its waterproofing and its ceiling height (a column the schedule does not have
falls back as below; a cell that says "-" means none). Rooms the schedule does not name follow
their name, by the usual choice for Korean housing:

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


def storey_finishes(rooms, clear, sizes, ceiling_h=CEILING_HEIGHT, rise=WATERPROOF_RISE, schedule=None):
    """{"floor"/"wall"/"ceiling": {material: m²}, "base": m, "waterproof": m², "plaster": m²,
    "rooms": [per room], "unnamed": {"count", "area"}, "wall_by_height": m² resting on the
    ceiling height, "rise_area": m² resting on the rise} of one storey.

    rooms: quantity_arch_rooms.find_rooms(...); clear: the clear storey height (m);
    sizes(name, kind, width) → (width, height) of a window or door as counted; schedule:
    quantity_arch_finish_schedule.read_finish_schedule(...) or None."""
    from .quantity_arch_finish_schedule import match
    from .quantity_arch_rooms import room_class
    default_height = min(ceiling_h, clear) if clear else ceiling_h
    out = {"floor": {}, "wall": {}, "ceiling": {}, "base": 0.0, "waterproof": 0.0, "plaster": 0.0,
           "rooms": [], "unnamed": {"count": 0, "area": 0.0}, "wall_by_height": 0.0, "rise_area": 0.0,
           "rule_area": 0.0, "from_schedule": 0}
    for room in rooms or []:
        kind = room_class(room["names"]) if room.get("names") else room.get("class")  # (rooms read before the class rules changed)
        entry = match(schedule, room.get("names")) if schedule else None
        rule = RULES.get(kind) or (RULES["room"] if entry else None)
        area, perimeter = float(room.get("area") or 0), float(room.get("perimeter") or 0)
        if kind == "shaft" and not entry:
            continue
        if rule is None:
            out["unnamed"]["count"] += 1
            out["unnamed"]["area"] = round(out["unnamed"]["area"] + area, 2)
            out["rooms"].append({"names": room.get("names") or [], "class": None, "area": area, "perimeter": perimeter})
            continue
        cells = (entry or {}).get("cells") or {}

        def finish(column):
            return entry.get(column) if entry and column in cells else rule[column]
        floor_m, wall_m, ceiling_m = finish("floor"), finish("wall"), finish("ceiling")
        listed_height = entry.get("ceiling_height") if entry else None
        height = min(listed_height, clear) if listed_height and clear else (listed_height or default_height)
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
        has_base = entry.get("has_base") if entry and "base" in cells else rule["base"]
        base = max(perimeter - doors, 0.0) if has_base else 0.0
        proof = ("wet" if kind == "wet" else "balcony") if (entry or {}).get("waterproof") else (None if entry else rule["waterproof"])
        waterproof = 0.0
        if proof == "wet":
            waterproof = area + perimeter * rise
            out["rise_area"] += perimeter * rise
        elif proof == "balcony":
            waterproof = area + perimeter * BALCONY_RISE
        if floor_m:
            _add(out["floor"], floor_m, area)
        if wall_m:
            _add(out["wall"], wall_m, wall)
        if ceiling_m:
            _add(out["ceiling"], ceiling_m, area)
        out["base"] += base
        out["waterproof"] += waterproof
        out["plaster"] += plaster
        if not listed_height:
            out["wall_by_height"] += wall  # rests on the assumed ceiling height
        if entry:
            out["from_schedule"] += 1
        else:
            out["rule_area"] += area
        out["rooms"].append({"names": room.get("names") or [], "class": kind, "area": round(area, 2), "perimeter": round(perimeter, 2),
                             "floor": floor_m, "wall_finish": wall_m, "ceiling": ceiling_m, "height": round(height, 3),
                             "source": "schedule" if entry else "rule", "schedule_row": (entry or {}).get("label"),
                             "wall": round(wall, 2), "openings": round(openings, 2), "base": round(base, 2),
                             "waterproof": round(waterproof, 2), "plaster": round(plaster, 2)})
    for key in ("base", "waterproof", "plaster", "wall_by_height", "rise_area", "rule_area"):
        out[key] = round(out[key], 2)
    out["height"] = round(default_height, 3)
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
