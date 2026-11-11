"""Putting a project's buildings where they stand, for the 3D review.

Each structural plan is read inside its own sheet frame, so every building comes out in its own
coordinates, and a frame often holds more than the plan (a legend sample, a key map, a border
drawn on a wall layer). This module, working only on the 3D display lines (never on the takeoff
measures):

1. cleans each plan: the main drawing and its big parts are kept; small pieces far from it and
   lone straight lines (borders, section marks) are dropped;
2. lines up the floors of one building when a sheet was drawn somewhere else in its frame;
3. puts the towers and facilities on the parking deck: the walls of a tower's basement or ground
   floor are usually drawn again (as an xref) on the parking plan of that level, so the tower is
   slid and turned (quarter turns) to where its walls coincide with the parking's. Only a clear
   fit is used; a building without one stays beside the site;
4. draws a wall once where a tower and the parking both draw it.

All by wall shapes on a 0.3 m grid (FFT cross-correlation), no names or office conventions.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

import numpy as np

CELL = 0.3
MAX_SIDE = 2400           # grid cells per side at most; coarser cells for very large plans
JOIN_GAP = 2.0            # m: lines closer than this belong to one drawing
KEEP_SHARE = 0.12         # a separate group this big (share of the main drawing's length) stays
MIN_MAIN = 5.0            # m: a plan whose main drawing is shorter than this shows nothing
PLACE_SURE = 0.8          # one level fitting this well places a building (above chance, see fits)
PLACE_MIN_M = 60.0        # m of wall lines at least in a drawing used to place a building
PLACE_AGREE = 0.6         # or two levels agreeing on the place, each at least this well
AGREE_M = 1.5             # m: two levels agree when their places are this close (same turn)
ALIGN_MIN = 0.5           # floors of one building: a better fit than this moves a sheet
ALIGN_GAIN = 0.15         # ... and only when it beats leaving it where it is by this much
FACILITY_WORDS = re.compile(r"관리사무소|관리동|경로당|커뮤니티|주민공동|주민운동|근린생활|근생|어린이집|보육|도서관|"
                            r"피트니스|휘트니스|독서실|경비실|상가|복리|부대")


def building_kind(name):
    if name == "지하주차장":
        return "parking"
    if re.fullmatch(r"\d{3,4}동", name or ""):
        return "tower"
    if FACILITY_WORDS.search(name or ""):
        return "facility"
    return "other"


# ── lines of a plan ──

def _wall_lines(plan):
    lines = [list(w[:4]) for w in plan.get("walls") or []]
    for poly in plan.get("cols") or []:
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            lines.append([a[0], a[1], b[0], b[1]])
    return lines


def _length(line):
    return math.hypot(line[2] - line[0], line[3] - line[1])


def _box(lines):
    xs = [c for l in lines for c in (l[0], l[2])]
    ys = [c for l in lines for c in (l[1], l[3])]
    return (min(xs), min(ys), max(xs), max(ys))


def _groups(lines, gap=JOIN_GAP):
    """Index lists of lines joined within ``gap`` m (bounding boxes), by a sweep along x."""
    parent = list(range(len(lines)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    boxes = [(min(l[0], l[2]) - gap, min(l[1], l[3]) - gap, max(l[0], l[2]) + gap, max(l[1], l[3]) + gap) for l in lines]
    active = []
    for i in sorted(range(len(lines)), key=lambda k: boxes[k][0]):
        active = [j for j in active if boxes[j][2] >= boxes[i][0]]
        for j in active:
            if boxes[j][1] <= boxes[i][3] and boxes[i][1] <= boxes[j][3]:
                parent[find(i)] = find(j)
        active.append(i)
    out = defaultdict(list)
    for i in range(len(lines)):
        out[find(i)].append(i)
    return list(out.values())


def _paired_share(lines):
    """Share of the length that has a parallel line 0.05–1.5 m away beside it (a wall's two faces)."""
    total = paired = 0.0
    for i, a in enumerate(lines):
        la = _length(a)
        total += la
        if la < 0.05:
            continue
        ux, uy = (a[2] - a[0]) / la, (a[3] - a[1]) / la
        for j, b in enumerate(lines):
            lb = _length(b)
            if i == j or lb < 0.05:
                continue
            if abs(ux * (b[3] - b[1]) / lb - uy * (b[2] - b[0]) / lb) > 0.05:
                continue  # not parallel
            mx, my = (b[0] + b[2]) / 2 - a[0], (b[1] + b[3]) / 2 - a[1]
            if not 0.05 <= abs(ux * my - uy * mx) <= 1.5:
                continue
            t0 = ux * (b[0] - a[0]) + uy * (b[1] - a[1])
            t1 = ux * (b[2] - a[0]) + uy * (b[3] - a[1])
            overlap = min(la, max(t0, t1)) - max(0.0, min(t0, t1))
            if overlap >= 0.3 * min(la, lb):
                paired += la
                break
    return paired / total if total else 0.0


# ── 1. cleaning a plan ──

def clean_plan(plan):
    """Drop the display lines that are not part of the drawing (see the module notes). Returns the
    number of wall/column lines dropped."""
    walls, cols = plan.get("walls") or [], plan.get("cols") or []
    lines = _wall_lines(plan)
    if not lines:
        return 0
    owner = [("w", i) for i in range(len(walls))]
    for c, poly in enumerate(cols):
        owner.extend(("c", c) for _ in poly)
    groups = []
    for members in _groups(lines):
        part = [lines[i] for i in members]
        length = sum(_length(l) for l in part)
        border = len(part) <= 12 and _paired_share(part) < 0.2  # lone straight lines: borders, marks
        groups.append({"members": members, "length": length, "box": _box(part), "border": border})
    candidates = [g for g in groups if not g["border"]]
    main = max(candidates, key=lambda g: g["length"]) if candidates else None
    if main is None or main["length"] < MIN_MAIN:
        kept = []
    else:
        big = [g for g in candidates if g is main or g["length"] >= KEEP_SHARE * main["length"]]
        # small pieces inside a kept drawing (free-standing columns, pits) stay with it
        kept = [g for g in candidates if g in big or any(
            g["box"][0] <= b["box"][2] and g["box"][2] >= b["box"][0] and g["box"][1] <= b["box"][3] and g["box"][3] >= b["box"][1]
            for b in big)]
    keep_walls, keep_cols = set(), set()
    for g in kept:
        for i in g["members"]:
            kind, index = owner[i]
            (keep_walls if kind == "w" else keep_cols).add(index)
    boxes = [(g["box"][0] - 1, g["box"][1] - 1, g["box"][2] + 1, g["box"][3] + 1) for g in kept]

    def inside(x, y):
        return any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)
    dropped = len(walls) - len(keep_walls) + sum(len(p) for c, p in enumerate(cols) if c not in keep_cols)
    plan["walls"] = [w for i, w in enumerate(walls) if i in keep_walls]
    plan["cols"] = [p for i, p in enumerate(cols) if i in keep_cols]
    plan["beams"] = [b for b in plan.get("beams") or [] if inside((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)]
    plan["opens"] = [o for o in plan.get("opens") or [] if inside((o[0] + o[2]) / 2, (o[1] + o[3]) / 2)]
    plan["slab"] = [r for r in plan.get("slab") or [] if inside((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)]
    if plan.get("slab_polygon") and not kept:
        plan["slab_polygon"] = []
    if dropped:
        plan["display_dropped"] = dropped
    return dropped


# ── grids and fitting ──

def _turn(lines, k):
    out = []
    for x0, y0, x1, y1 in lines:
        for _ in range(k % 4):
            x0, y0, x1, y1 = -y0, x0, -y1, x1
        out.append([x0, y0, x1, y1])
    return out


def _cell_for(*boxes):
    side = max(max(b[2] - b[0], b[3] - b[1]) for b in boxes)
    return max(CELL, side / MAX_SIDE)


def _raster(lines, cell, box=None):
    """0/1 grid of the lines and the grid's origin (x, y of cell [0, 0])."""
    x0, y0, x1, y1 = box or _box(lines)
    width, height = int((x1 - x0) / cell) + 3, int((y1 - y0) / cell) + 3
    grid = np.zeros((height, width), np.float32)
    for ax, ay, bx, by in lines:
        n = max(2, int(math.hypot(bx - ax, by - ay) / (cell * 0.5)) + 1)
        xs = np.clip(((np.linspace(ax, bx, n) - x0) / cell + 1).astype(np.int64), 0, width - 1)
        ys = np.clip(((np.linspace(ay, by, n) - y0) / cell + 1).astype(np.int64), 0, height - 1)
        grid[ys, xs] = 1.0
    return grid, (x0 - cell, y0 - cell)


def _grow(grid):
    out = grid.copy()
    out[1:, :] = np.maximum(out[1:, :], grid[:-1, :])
    out[:-1, :] = np.maximum(out[:-1, :], grid[1:, :])
    out[:, 1:] = np.maximum(out[:, 1:], grid[:, :-1])
    out[:, :-1] = np.maximum(out[:, :-1], grid[:, 1:])
    return out


def fits(moving, fixed, turns=(0, 1, 2, 3), peaks=3, spacing=6.0):
    """Places of the ``moving`` lines on the ``fixed`` lines: [(score, turn, dx, dy)], best first,
    each at least ``spacing`` m from a better one. score = share of the smaller drawing's grid
    cells that coincide (within one cell), above what the fixed drawing's density gives by chance
    (a dense parking plan meets a small drawing almost anywhere); (turn, dx, dy) maps a moving
    point p to rotate(p, turn × 90°) + (dx, dy)."""
    if not moving or not fixed:
        return []
    cell = _cell_for(_box(moving), _box(fixed))
    fixed_grid, fixed_origin = _raster(fixed, cell)
    fixed_grown = _grow(fixed_grid)
    fixed_count = float(fixed_grid.sum())
    chance = float(fixed_grown.mean())
    found = []
    for turn in turns:
        lines = _turn(moving, turn)
        grid, origin = _raster(lines, cell)
        count = float(grid.sum())
        if not count or not fixed_count:
            continue
        height, width = fixed_grown.shape[0] + grid.shape[0], fixed_grown.shape[1] + grid.shape[1]
        spectrum = np.multiply(np.fft.rfft2(fixed_grown, (height, width)), np.conj(np.fft.rfft2(grid, (height, width))))
        corr = np.fft.irfft2(spectrum, (height, width))
        radius = max(1, int(spacing / cell))
        for _ in range(peaks):
            iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
            value = float(corr[iy, ix])
            if value <= 0.5:
                break
            sy = iy - height if iy > height // 2 else iy
            sx = ix - width if ix > width // 2 else ix
            dx = fixed_origin[0] + sx * cell - origin[0]
            dy = fixed_origin[1] + sy * cell - origin[1]
            share = value / min(count, fixed_count)
            if count <= fixed_count:
                share = (share - chance) / (1.0 - chance) if chance < 1 else 0.0
            found.append((share, turn, dx, dy))
            for yy in range(iy - radius, iy + radius + 1):  # wrap-around neighbourhood
                lo, hi = (ix - radius) % width, (ix + radius) % width
                row = corr[yy % height]
                if lo <= hi:
                    row[lo:hi + 1] = 0
                else:
                    row[lo:] = 0
                    row[:hi + 1] = 0
    found.sort(key=lambda f: -f[0])
    out = []
    for f in found:
        if all(f[1] != o[1] or math.hypot(f[2] - o[2], f[3] - o[3]) >= spacing for o in out):
            out.append((round(f[0], 3), f[1], round(f[2], 2), round(f[3], 2)))
    return out


def _share_at(moving, fixed, turn, dx, dy):
    """Share of the moving drawing's cells on the fixed drawing at one given place."""
    if not moving or not fixed:
        return 0.0
    placed = [[x0 + dx, y0 + dy, x1 + dx, y1 + dy] for x0, y0, x1, y1 in _turn(moving, turn)]
    a, b = _box(placed), _box(fixed)
    if a[0] > b[2] or b[0] > a[2] or a[1] > b[3] or b[1] > a[3]:
        return 0.0
    box = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
    if max(box[2] - box[0], box[3] - box[1]) / CELL > 4 * MAX_SIDE:
        return 0.0
    cell = _cell_for(box)
    grid, _origin = _raster(placed, cell, box)
    fixed_grid, _origin = _raster(fixed, cell, box)
    count = min(float(grid.sum()), float(fixed_grid.sum()))
    return float(np.multiply(grid, _grow(fixed_grid)).sum()) / count if count else 0.0


# ── moving a plan's display lines ──

def move_plan(plan, turn, dx, dy):
    if not turn and not dx and not dy:
        return

    def point(x, y):
        for _ in range(turn % 4):
            x, y = -y, x
        return round(x + dx, 2), round(y + dy, 2)

    def segment(line):
        a, b = point(line[0], line[1]), point(line[2], line[3])
        return [a[0], a[1], b[0], b[1]] + list(line[4:])
    plan["walls"] = [segment(w) for w in plan.get("walls") or []]
    plan["beams"] = [segment(b) for b in plan.get("beams") or []]
    plan["opens"] = [segment(o) for o in plan.get("opens") or []]
    plan["cols"] = [[list(point(p[0], p[1])) for p in poly] for poly in plan.get("cols") or []]
    rects = []
    for r in plan.get("slab") or []:
        a, b = point(r[0], r[1]), point(r[2], r[3])
        rects.append([min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])])
    plan["slab"] = rects
    if plan.get("slab_polygon"):
        plan["slab_polygon"] = [list(point(p[0], p[1])) for p in plan["slab_polygon"]]


# ── 2. the floors of one building ──

def _level_rank(key):
    if key.startswith("B") and key[1:].isdigit():
        return -int(key[1:])
    if key.isdigit():
        return int(key)
    return {"R": 500, "PH": 501}.get(key, 900)


def align_floors(building):
    """Move a floor's sheet onto the floor below or above when it was drawn elsewhere in its frame.
    Returns [(plan index, dx, dy)] of the moved sheets."""
    plans, levels = building.get("plans") or [], building.get("levels") or []
    order = []
    for level in sorted(levels, key=lambda l: _level_rank(l.get("key") or "")):
        index = level.get("plan")
        if isinstance(index, int) and index < len(plans) and index not in order and _wall_lines(plans[index]):
            order.append(index)
    if len(order) < 2:
        return []
    ground = next((l.get("plan") for l in levels if l.get("key") == "1"), None)
    start = order.index(ground) if ground in order else next((i for i, p in enumerate(order)
                                                              if any(l.get("plan") == p and (l.get("key") or "").isdigit() for l in levels)), 0)
    moved = []
    sequence = [(order[i], order[i + 1]) for i in range(start - 1, -1, -1)] + \
               [(order[i], order[i - 1]) for i in range(start + 1, len(order))]
    for index, neighbour in sequence:
        moving, fixed = _wall_lines(plans[index]), _wall_lines(plans[neighbour])
        here = _share_at(moving, fixed, 0, 0.0, 0.0)
        best = fits(moving, fixed, turns=(0,), peaks=1)
        if best and best[0][0] >= ALIGN_MIN and best[0][0] >= here + ALIGN_GAIN and math.hypot(best[0][2], best[0][3]) > 0.6:
            move_plan(plans[index], 0, best[0][2], best[0][3])
            moved.append((index, best[0][2], best[0][3]))
        elif here < 0.05:
            # nothing in common where it is: at least bring it next to its neighbour
            a, b = _box(moving), _box(fixed)
            far = math.hypot((a[0] + a[2] - b[0] - b[2]) / 2, (a[1] + a[3] - b[1] - b[3]) / 2)
            if far > 200:
                dx, dy = round((b[0] + b[2] - a[0] - a[2]) / 2, 2), round((b[1] + b[3] - a[1] - a[3]) / 2, 2)
                move_plan(plans[index], 0, dx, dy)
                moved.append((index, dx, dy))
    return moved


# ── 3. buildings on the parking deck ──

def _plan_of(building, key):
    plans = building.get("plans") or []
    level = next((l for l in building.get("levels") or [] if l.get("key") == key), None)
    index = (level or {}).get("plan")
    return plans[index] if isinstance(index, int) and index < len(plans) else None


def _pairs(building, host):
    """(building level key, host level key) pairs whose walls are drawn on both."""
    host_keys = {l.get("key") for l in host.get("levels") or []}
    keys = [l.get("key") or "" for l in building.get("levels") or []]
    out = [(k, k) for k in keys if k.startswith("B") and k in host_keys]
    ground = "1" if "1" in keys else next((k for k in keys if k.isdigit()), None)
    deck = "R" if "R" in host_keys else None
    if ground and deck:
        out.append((ground, deck))
    return out


def _host(buildings):
    named = [b for b in buildings if b.get("name") == "지하주차장"]
    if named:
        return named[0]
    decks = [b for b in buildings if b.get("levels") and not any((l.get("key") or "").isdigit() for l in b["levels"])]
    return max(decks, key=lambda b: sum((p.get("slab_area") or 0) for p in b.get("plans") or []), default=None)


def place_buildings(buildings):
    """Place every building on the parking deck where its walls fit. Returns the site summary."""
    host = _host(buildings)
    if host is None:
        for building in buildings:
            building["site"] = {"placed": False, "lift": _lift(building, None, None)}
        return {"host": None, "placed": [], "beside": [b.get("name") for b in buildings]}
    host["site"] = {"placed": True, "host": True, "lift": 0.0}
    chosen = []
    for building in buildings:
        if building is host:
            continue
        hypotheses = []
        for key, host_key in _pairs(building, host):
            plan, deck = _plan_of(building, key), _plan_of(host, host_key)
            if not plan or not deck:
                continue
            moving, fixed = _wall_lines(plan), _wall_lines(deck)
            if sum(_length(l) for l in moving) < PLACE_MIN_M or len(fixed) < 12:
                continue
            for score, turn, dx, dy in fits(moving, fixed):
                hypotheses.append({"score": score, "turn": turn, "dx": dx, "dy": dy, "via": key})
        places = []
        for h in sorted(hypotheses, key=lambda h: -h["score"]):
            home = next((p for p in places if p["turn"] == h["turn"] and
                         math.hypot(p["dx"] - h["dx"], p["dy"] - h["dy"]) <= AGREE_M), None)
            if home is None:
                places.append(dict(h, levels=[h["via"]], scores=[h["score"]]))
            elif h["via"] not in home["levels"]:
                home["levels"].append(h["via"])
                home["scores"].append(h["score"])
        good = [p for p in places if p["scores"][0] >= PLACE_SURE or
                (len(p["scores"]) >= 2 and sorted(p["scores"])[-2] >= PLACE_AGREE)]
        good.sort(key=lambda p: (-p["scores"][0], -len(p["scores"])))
        if good:
            chosen.append((building, good))
    # strongest fits first; two buildings may not take the same place
    taken = []
    placed = []
    for building, good in sorted(chosen, key=lambda item: -item[1][0]["scores"][0]):
        footprint = _wall_lines(_plan_of(building, "1") or next((p for p in building.get("plans") or [] if p.get("walls")), {}))
        for place in good:
            box = _box(_turn(footprint, place["turn"])) if footprint else None
            if box:
                box = (box[0] + place["dx"], box[1] + place["dy"], box[2] + place["dx"], box[3] + place["dy"])
                if any(_overlap(box, other) > 0.4 for other in taken):
                    continue
                taken.append(box)
            for plan in building.get("plans") or []:
                move_plan(plan, place["turn"], place["dx"], place["dy"])
            building["site"] = {"placed": True, "on": host.get("name"), "score": place["scores"][0],
                                "turn": place["turn"] * 90, "levels": place["levels"], "lift": _lift(building, host, place)}
            placed.append(building)
            break
    reference = _site_ground(host, placed)
    for building in buildings:
        building.setdefault("site", {"placed": False, "lift": _lift(building, host, None, reference)})
    _once(host, placed)
    return {"host": host.get("name"), "placed": [b.get("name") for b in placed],
            "beside": [b.get("name") for b in buildings if not b["site"].get("placed")]}


def _elev(building, key):
    level = next((l for l in building.get("levels") or [] if l.get("key") == key), None)
    return float(level["elev"]) if level and level.get("elev") is not None else None


def _ground(building):
    """Height of a building's ground floor in its own levels (a parking deck: its roof)."""
    if building.get("kind") == "parking" or not any((l.get("key") or "").isdigit() for l in building.get("levels") or []):
        return _elev(building, "R")
    if _elev(building, "1") is not None:
        return _elev(building, "1")
    first = min((l for l in building["levels"] if (l.get("key") or "").isdigit()), key=lambda l: int(l["key"]))
    return float(first.get("elev") or 0) - 3.25 - 2.85 * (int(first["key"]) - 2)


def _lift(building, host, place, reference=0.0):
    """How far a building is raised so its floors meet the parking's (placed) or so every ground
    floor is at one height (beside the site). Heights read from one office's sections may count
    from sea level and another building's from its own lowest floor."""
    if place:
        for key in place["levels"]:
            pair = next((h for k, h in _pairs(building, host) if k == key), None)
            mine, theirs = _elev(building, key), _elev(host, pair) if pair else None
            if mine is not None and theirs is not None:
                return round(theirs - mine, 2)
    ground = _ground(building)
    return round(reference - ground, 2) if ground is not None else 0.0


def _site_ground(host, placed):
    """Height of the ground floor on the site: the parking deck's roof, else a placed tower's
    ground floor, else the top of the parking."""
    ground = _ground(host)
    if ground is not None:
        return ground
    for building in placed:
        mine = _ground(building)
        if mine is not None:
            return mine + building["site"].get("lift", 0.0)
    tops = [float(l.get("elev") or 0) + float(l.get("height") or 0) for l in host.get("levels") or []]
    return max(tops) if tops else 0.0


def _overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1.0
    return w * h / smaller


# ── 4. a wall drawn once ──

def _on_grid(lines, reference):
    """For each line: does its middle lie on the reference drawing (within one grid cell)?"""
    if not lines or not reference:
        return [False] * len(lines)
    box = _box(reference)
    cell = _cell_for(box)
    grid = _grow(_raster(reference, cell, box)[0])
    origin = (box[0] - cell, box[1] - cell)
    out = []
    for line in lines:
        hits = 0
        for t in (0.25, 0.5, 0.75):
            x, y = line[0] + (line[2] - line[0]) * t, line[1] + (line[3] - line[1]) * t
            i, j = int((y - origin[1]) / cell), int((x - origin[0]) / cell)
            hits += 1 if 0 <= i < grid.shape[0] and 0 <= j < grid.shape[1] and grid[i, j] else 0
        out.append(hits >= 2)
    return out


def _once(host, placed):
    """A placed tower's basement walls already on the parking plan of that level are left to the
    parking; the parking deck's walls standing where a tower's ground floor stands are left to the
    tower."""
    for building in placed:
        for key, host_key in _pairs(building, host):
            plan, deck = _plan_of(building, key), _plan_of(host, host_key)
            if not plan or not deck:
                continue
            if key.startswith("B"):
                walls = plan.get("walls") or []
                hit = _on_grid([w[:4] for w in walls], _wall_lines(deck))
                plan["walls"] = [w for w, h in zip(walls, hit) if not h]
                cols = plan.get("cols") or []
                if cols:
                    centres = [[sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c)] * 2 for c in cols]
                    hit_cols = _on_grid(centres, _wall_lines(deck))
                    plan["cols"] = [c for c, h in zip(cols, hit_cols) if not h]
                if walls and sum(hit) >= 0.7 * len(walls):
                    # the parking draws this basement: its slab and beams are the parking's too
                    plan.update(slab=[], slab_polygon=[], beams=[], opens=[], shared_with=host.get("name"))
                clean_plan(plan)  # what is left may now be loose pieces
            else:
                walls = deck.get("walls") or []
                hit = _on_grid([w[:4] for w in walls], _wall_lines(plan))
                deck["walls"] = [w for w, h in zip(walls, hit) if not h]


def assemble(buildings):
    """Clean, line up and place the buildings (in place). Returns the site summary for the model."""
    dropped = 0
    for building in buildings:
        building["kind"] = building_kind(building.get("name"))
        for plan in building.get("plans") or []:
            dropped += clean_plan(plan)
    moved = 0
    for building in buildings:
        moved += len(align_floors(building))
    site = place_buildings(buildings)
    site.update(dropped_lines=dropped, moved_sheets=moved)
    return site
