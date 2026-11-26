"""Rooms (실) of an architectural plan from CAD (no AI): their areas, wall lengths and names.

The wall lines of every kind (concrete, masonry, ALC, dry wall) are drawn on a grid, the openings
are closed where a window or door block stands in a gap between two wall pieces on one line, and
the spaces the walls enclose are the rooms: empty cells not reached from the edge of the frame,
wider than a wall section (about 0.9 m). An opening without a block (an open-plan living room and
kitchen, a corridor) stays open, so those spaces come out as one room.

Each room gets the names written inside it (거실, 욕실, 발코니 ...), its floor area, its perimeter,
the length of that perimeter against each wall kind (plaster goes on masonry and ALC), and the
windows and doors in its walls (for the wall finish to leave out and the baseboard to stop at).

Lengths in metres, frame-local, as quantity_arch.measure gives them.
"""
from __future__ import annotations

import math
import re

import numpy as np

from .quantity_floor_takeoff import GROW, THIN_HALF_WIDTH, _and, _dil8, _erode4, _fill, _not, _raster, _runs, _shift, end_lines, wall_caps

KINDS = ("frame", "masonry", "alc", "dry")
MIN_CELL = 0.025
MAX_CELLS = 4_000_000
MIN_ROOM_M2 = 0.8
BRIDGE_MIN, BRIDGE_MAX = 0.3, 4.5   # an opening between two wall pieces on one line
ALIGN = 0.03                        # how far off one line two wall ends may be
MAX_ROOMS = 600

# room names by what they are finished with (the first match decides; order matters)
ROOM_CLASSES = (
    ("shaft", re.compile(r"^(?:E?PS|DA|TPS|AD|PIT|피트|덕트|샤프트|DUCT)\d*$|덕트|샤프트", re.I)),
    ("wet", re.compile(r"욕실|화장실|샤워|세탁실|다용도실|파우더룸?|TOILET|BATH|WC", re.I)),
    ("balcony", re.compile(r"발코니|베란다|실외기|대피\s*공간|테라스|BALCONY", re.I)),
    ("entrance", re.compile(r"현관|ENTRANCE", re.I)),
    ("service", re.compile(r"기계실|전기실|발전기|펌프|창고|주차|저수조|물탱크|MDF|통신실|쓰레기|분리수거|방재실|보일러실|휀룸|팬룸|부속실", re.I)),
    ("common", re.compile(r"계단실?|홀$|E/?V|엘리베이터|승강기|로비|LOBBY|공용|관리|경비|주민|커뮤니티|복도", re.I)),
    ("room", re.compile(r"거실|침실|안방|^방\s*\d*$|주방|식당|드레스|알파룸|서재|팬트리|가족실|다목적|ROOM|KITCHEN|LIVING|DINING", re.I)),
)
ROOM_NAME = re.compile("|".join(p.pattern for _c, p in ROOM_CLASSES), re.I)
TIE_ORDER = ("room", "wet", "balcony", "entrance", "service", "common")  # a space several names share, on a tie


def room_class(names):
    """The finish class of a room from its names: shaft, wet, balcony, entrance, service, common,
    room; None when no name says. Each name has its class; a space several names share (rooms a
    missing door left open into one: 거실/침실1/발코니/PS) takes the class most of its names have,
    the rooms (거실·침실) on a tie, and a shaft's label (PS, AD) never decides when another name is
    there (its shaft not walled off on the plan)."""
    classes = [next((kind for kind, pattern in ROOM_CLASSES if pattern.search(n)), None) for n in names]
    classes = [kind for kind in classes if kind]
    rest = [kind for kind in classes if kind != "shaft"]
    if not rest:
        return classes[0] if classes else None
    return max(TIE_ORDER, key=lambda kind: (rest.count(kind), -TIE_ORDER.index(kind)))


def is_room_name(text):
    text = re.sub(r"\s+", "", str(text or ""))
    return bool(text) and len(text) <= 16 and not re.search(r"\d{3,}|[=@]", text) and bool(ROOM_NAME.search(text))


def _bridges(segments, openings):
    """Lines across the gaps where a window or door stands at the end of a wall: from the end of a
    wall's face line (not its short end line) straight on to the first wall line it meets
    0.3–4.5 m ahead (the other side of the opening, on the same line or across it), when an
    opening of about that width stands in the gap (its insert point within 0.5 m of the line, the
    gap no wider than 1.3 × the opening + 0.3 m). A wall the ray meets sooner (a T-junction) leaves
    no gap to close. openings: [(x, y, width)]."""
    if not openings:
        return []
    cells = {}
    for index, (ax, ay, bx, by) in enumerate(segments):
        for gx in range(int(math.floor(min(ax, bx) / BRIDGE_MAX)), int(math.floor(max(ax, bx) / BRIDGE_MAX)) + 1):
            for gy in range(int(math.floor(min(ay, by) / BRIDGE_MAX)), int(math.floor(max(ay, by) / BRIDGE_MAX)) + 1):
                cells.setdefault((gx, gy), []).append(index)
    spots = {}
    for ox, oy, ow in openings:
        spots.setdefault((int(math.floor(ox)), int(math.floor(oy))), []).append((ox, oy, ow))

    def opening_fits(ax, ay, bx, by):
        length = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / length, (by - ay) / length
        for gx in range(int(math.floor(min(ax, bx) - 1)), int(math.floor(max(ax, bx) + 1)) + 1):
            for gy in range(int(math.floor(min(ay, by) - 1)), int(math.floor(max(ay, by) + 1)) + 1):
                for px, py, width in spots.get((gx, gy), ()):
                    along = (px - ax) * ux + (py - ay) * uy
                    off = abs((px - ax) * uy - (py - ay) * ux)
                    if off <= 0.5 and -0.3 <= along <= length + 0.3 and length <= 1.3 * width + 0.3:
                        return True
        return False

    def first_hit(x, y, ux, uy, own):
        """Distance along (ux, uy) from (x, y) to the first wall line ahead (not one touching the start)."""
        best = None
        seen = set()
        for gx in range(int(math.floor((min(x, x + ux * BRIDGE_MAX)) / BRIDGE_MAX)), int(math.floor(max(x, x + ux * BRIDGE_MAX) / BRIDGE_MAX)) + 1):
            for gy in range(int(math.floor((min(y, y + uy * BRIDGE_MAX)) / BRIDGE_MAX)), int(math.floor(max(y, y + uy * BRIDGE_MAX) / BRIDGE_MAX)) + 1):
                for index in cells.get((gx, gy), ()):
                    if index == own or index in seen:
                        continue
                    seen.add(index)
                    ax, ay, bx, by = segments[index]
                    dx, dy = bx - ax, by - ay
                    denom = ux * dy - uy * dx
                    if abs(denom) < 1e-9:  # parallel: a line on the same line ahead (an opening between two pieces)
                        if abs((ax - x) * uy - (ay - y) * ux) > ALIGN:
                            continue
                        ahead = [(px - x) * ux + (py - y) * uy for px, py in ((ax, ay), (bx, by))]
                        t = min((a for a in ahead if a > 0.02), default=None)
                        if t is None and min(ahead) < 0 < max(ahead):
                            t = 0.0  # the ray runs inside another line: it is part of the same wall
                    else:
                        t = ((ax - x) * dy - (ay - y) * dx) / denom
                        u = ((ax - x) * uy - (ay - y) * ux) / denom
                        if not (-1e-6 <= u <= 1 + 1e-6):
                            continue
                    if t is not None and t > 0.02 and (best is None or t < best):
                        best = t
                    elif t is not None and 0.0 <= t <= 0.02 and denom != 0 and abs(denom) >= 1e-9:
                        continue  # a line the end touches (the wall it stands on)
        return best

    out, seen = [], set()
    caps = end_lines(segments)
    for index, (ax, ay, bx, by) in enumerate(segments):
        length = math.hypot(bx - ax, by - ay)
        if length < 0.4 or index in caps:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        for x, y, dx, dy in ((bx, by, ux, uy), (ax, ay, -ux, -uy)):
            t = first_hit(x, y, dx, dy, index)
            if t is None or not BRIDGE_MIN <= t <= BRIDGE_MAX:
                continue
            fx, fy = x + dx * t, y + dy * t
            key = (round(min(x, fx), 2), round(min(y, fy), 2), round(max(x, fx), 2), round(max(y, fy), 2))
            if key in seen or not opening_fits(x, y, fx, fy):
                continue
            seen.add(key)
            out.append((x, y, fx, fy))
    return out


def _label(mask):
    """(int32 grid of component numbers 1..n, n): 4-connected components of ``mask`` (row runs
    joined like quantity_floor_takeoff._fill)."""
    height = mask.shape[0]
    rows = [_runs(mask[y]) for y in range(height)]
    parent, first = [], []
    for runs in rows:
        first.append(len(parent))
        parent.extend(range(len(parent), len(parent) + len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for y in range(1, height):
        above, below = rows[y - 1], rows[y]
        i = j = 0
        while i < len(above) and j < len(below):
            a0, a1 = above[i]
            b0, b1 = below[j]
            if max(a0, b0) < min(a1, b1):
                ra, rb = find(first[y - 1] + i), find(first[y] + j)
                if ra != rb:
                    parent[ra] = rb
            if a1 < b1:
                i += 1
            else:
                j += 1
    labels = np.zeros(mask.shape, dtype=np.int32)
    number = {}
    for y, runs in enumerate(rows):
        for k, (a, b) in enumerate(runs):
            root = find(first[y] + k)
            labels[y, a:b] = number.setdefault(root, len(number) + 1)
    return labels, len(number)


def find_rooms(segments, openings=(), names=()):
    """[{"names", "class", "area", "perimeter", "faces": {kind: m}, "openings": [...], "x", "y"}].

    segments: {kind: [(ax, ay, bx, by)]} of the frame's wall face lines (kinds of ``KINDS``);
    openings: [(type, "door"/"window", x, y, width)]; names: [(text, x, y)]."""
    every = [seg for kind in KINDS for seg in segments.get(kind) or ()]
    if not every:
        return []
    closing = _bridges(every, [(o[2], o[3], o[4]) for o in openings])
    lines = every + wall_caps(every) + closing
    xs = [v for s in lines for v in (s[0], s[2])]
    ys = [v for s in lines for v in (s[1], s[3])]
    minx, miny = min(xs) - 0.6, min(ys) - 0.6
    width, height = max(xs) + 0.6 - minx, max(ys) + 0.6 - miny
    cell = max(MIN_CELL, math.sqrt(width * height / MAX_CELLS))
    shape = (int(height / cell) + 1, int(width / cell) + 1)
    solid = _raster(lines, minx, miny, cell, shape)
    for _ in range(GROW):
        solid = _dil8(solid)
    empty = _not(solid)
    border = np.zeros(shape, dtype=bool)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    inner = _and(empty, _not(_fill(_and(border, empty), empty)))
    seed = inner
    for _ in range(max(1, int(THIN_HALF_WIDTH / cell))):
        seed = _erode4(seed)
    rooms_mask = _fill(seed, inner)
    labels, count = _label(rooms_mask)
    if not count:
        return []
    # the wall kind beside each boundary edge: a grid of kind numbers (0 = none), drawn thick
    kind_grid = np.zeros(shape, dtype=np.int8)
    for number, kind in enumerate(KINDS, 1):
        drawn = _raster(list(segments.get(kind) or ()) + ([] if kind != "frame" else closing), minx, miny, cell, shape)
        for _ in range(GROW + 1):
            drawn = _dil8(drawn)
        kind_grid[np.logical_and(drawn, kind_grid == 0)] = number
    edges = np.zeros(count + 1)
    faces = np.zeros((count + 1, len(KINDS) + 1))
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        boundary = _and(rooms_mask, _not(_shift(rooms_mask, dy, dx)))
        edges += np.bincount(labels[boundary], minlength=count + 1)
        found = np.zeros(shape, dtype=np.int8)
        for k in range(1, 5):
            ahead = _shift(kind_grid, k * dy, k * dx)
            fill_in = np.logical_and(found == 0, ahead > 0)
            found[fill_in] = ahead[fill_in]
        picked = boundary
        np.add.at(faces, (labels[picked], found[picked]), 1)
    cells = np.bincount(labels.ravel(), minlength=count + 1)
    ys_idx, xs_idx = np.nonzero(labels)
    cx = np.bincount(labels[ys_idx, xs_idx], weights=xs_idx, minlength=count + 1)
    cy = np.bincount(labels[ys_idx, xs_idx], weights=ys_idx, minlength=count + 1)
    # the line band drawn (GROW cells each side of the line) sits inside the room: half of it
    # and the line's own cell belong to the room up to the wall face
    band = (GROW + 0.5) * cell
    rooms = {}
    for n in range(1, count + 1):
        area = cells[n] * cell * cell + edges[n] * cell * band
        if area < MIN_ROOM_M2:
            continue
        perimeter = edges[n] * cell
        rooms[n] = {"names": [], "area": round(float(area), 2), "perimeter": round(float(perimeter), 2),
                    "faces": {kind: round(float(faces[n, i + 1] * cell), 2) for i, kind in enumerate(KINDS) if faces[n, i + 1]},
                    "openings": [], "x": round(float(minx + (cx[n] / cells[n] + 0.5) * cell), 2),
                    "y": round(float(miny + (cy[n] / cells[n] + 0.5) * cell), 2)}

    def nearest(x, y, reach):
        gx, gy = int((x - minx) / cell), int((y - miny) / cell)
        r = int(reach / cell) + 1
        y0, y1, x0, x1 = max(gy - r, 0), min(gy + r + 1, shape[0]), max(gx - r, 0), min(gx + r + 1, shape[1])
        if y0 >= y1 or x0 >= x1:
            return []
        window = labels[y0:y1, x0:x1]
        found = {}
        for n in np.unique(window[window > 0]).tolist():
            if n not in rooms:
                continue
            py, px = np.nonzero(window == n)
            d = float(np.min(np.hypot((px + x0 - gx) * cell, (py + y0 - gy) * cell)))
            if d <= reach:
                found[n] = d
        return sorted(found, key=found.get)

    for text, x, y in names:
        hit = nearest(x, y, 0.8)
        if hit and text not in rooms[hit[0]]["names"]:
            rooms[hit[0]]["names"].append(text)
    for name, kind, x, y, width in openings:
        near = nearest(x, y, max(width, 0.6) + 0.6)
        for n in near[:2 if kind == "door" else 1]:
            rooms[n]["openings"].append({"name": name, "kind": kind, "width": round(width, 3)})
    out = []
    for n in sorted(rooms, key=lambda k: -rooms[k]["area"])[:MAX_ROOMS]:
        room = rooms[n]
        room["class"] = room_class(room["names"])
        out.append(room)
    return out
