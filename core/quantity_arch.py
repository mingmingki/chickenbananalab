"""Architectural plan measures from CAD (no AI): walls by material and the windows and doors.

Runs inside the floor-views subprocess on each architectural plan frame (pure module, no Django):

* walls: the masonry (조적), ALC and dry (건식벽) lines of the frame are measured like the concrete
  walls of a structural plan (quantity_floor_takeoff.measure_walls): the long faces of the wall
  section give the length (faces / 2, the short end line of each drawn piece left out) and the
  mean thickness (2 × section / faces). A doorway or a window breaks the wall lines, so the length
  is the wall without its openings.
* windows and doors: block inserts on window/door layers (or named like one), each with its width
  (the longer side of the placed block) and whether it is a door (a swing arc, or a door-like
  name). Its type is the tag written beside it (AW1, SD-2: a text, or an attribute of a tag block
  or of the window block itself), the nearest within 1.5 m; untagged ones of a block whose tagged
  copies all carry one tag take that tag, the rest keep their block name. The tags are the codes
  of the window and door schedule (quantity_arch_schedule), which gives their sizes. Each is
  matched to the wall it sits in (the nearest wall line within 0.6 m), so the takeoff can add the
  wall left above a door and above/below a window.

Lengths in metres, frame-local (x right, y up), as quantity_floor_views._plan_model gives them.
"""
from __future__ import annotations

import math
import re

WALL_KINDS = ("masonry", "alc", "dry")
# thinner than this the lines are a lining, not a wall: a dry wall is at least 50 mm of studs and
# boards, a block or brick wall at least a 0.5B brick (90 mm)
MIN_WALL_M = {"masonry": 0.06, "alc": 0.05, "dry": 0.05}
OPENING_REACH_M = 0.6
# a window or door block known by what it draws: most of its own lines on window/door layers, and
# no bigger than an opening (a unit-plan block also holds many window lines, but among far more)
OPENING_SHARE = 0.6
OPENING_BLOCK_MM = (300.0, 6000.0)
DUPLICATE_M = 0.1  # two openings of one kind this close are one opening drawn twice
ANONYMOUS_BLOCK = re.compile(r"^(?:\*|[A-Z]\$C[0-9A-Fa-f]{6,}$)")
TAG_REACH_M = 1.5
MAX_TAGS = 20000
DOOR_NAME = re.compile(r"DOOR|DR|문|^(?:A|W|S|F|P|SS|H)?D\d|WD|AD|SD|FD|PD", re.I)
WINDOW_NAME = re.compile(r"WIN|WD?W|창|^(?:A|P|S|B|W)?W\d", re.I)
MAX_INSERTS = 20000


def _block_info(doc, name, cache):
    """(width, height of the block's own extents, has an arc) of a block, cached per document."""
    key = (id(doc), name)
    if key in cache:
        return cache[key]
    info = (0.0, 0.0, False)
    block = doc.blocks.get(name)
    if block is not None:
        try:
            from ezdxf import bbox
            box = bbox.extents(block, fast=True)
            has_arc = any(e.dxftype() in ("ARC", "CIRCLE", "ELLIPSE") for e in block)
            if box.has_data:
                info = (float(box.size.x), float(box.size.y), has_arc)
        except Exception:
            info = (0.0, 0.0, False)
    cache[key] = info
    return info


def _block_center(doc, name, cache):
    """(x, y) of the middle of a block's own extents (block units), cached; None when empty."""
    key = (id(doc), name, "center")
    if key not in cache:
        cache[key] = None
        block = doc.blocks.get(name)
        if block is not None:
            try:
                from ezdxf import bbox
                box = bbox.extents(block, fast=True)
                if box.has_data:
                    cache[key] = (float(box.center.x), float(box.center.y))
            except Exception:
                pass
    return cache[key]


LINE_KINDS = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC")
_DOOR_LAYER = re.compile(r"DOOR|DR($|[-_ ])|문", re.I)


def opening_by_content(doc, name, layer, layer_kind, cache):
    """"door" / "window" for a block drawn like an opening whatever its name ("PW-1500(D)",
    "SD-700", "단위세대_WD-1000" on layer 0): most of its own lines on window/door layers and the
    size of an opening; else None. Lines on layer 0 take the insert's layer."""
    key = (id(doc), name, layer, "content")
    if key in cache:
        return cache[key]
    found = None
    block = doc.blocks.get(name)
    # pasted groups ("A$C2CEE7391", "*U12") hold a door with its frame and wall bits: not one opening
    anonymous = bool(ANONYMOUS_BLOCK.match(name.split("$0$")[-1]))
    if block is not None and layer_kind is not None and not block.block_record.is_xref and not anonymous:
        lines = on_opening = on_door = 0
        for entity in block:
            if entity.dxftype() not in LINE_KINDS:
                continue
            own = entity.dxf.get("layer", "0")
            own = layer if own == "0" else own
            lines += 1
            if layer_kind(own) == "opening":
                on_opening += 1
                if _DOOR_LAYER.search(own.split("$0$")[-1].split("|")[-1]):
                    on_door += 1
        if lines >= 3 and on_opening >= OPENING_SHARE * lines:
            w, h, has_arc = _block_info(doc, name, cache)
            if OPENING_BLOCK_MM[0] <= max(w, h) <= OPENING_BLOCK_MM[1]:
                found = "door" if has_arc or on_door >= 0.5 * on_opening else "window"
    cache[key] = found
    return found


def _text(entity):
    try:
        return entity.plain_text() if entity.dxftype() == "MTEXT" else entity.dxf.get("text", "")
    except Exception:
        return ""


def opening_inserts(doc, xrefs, limit=None, is_opening=None, key_of=None, tags=None, layer_kind=None, names=None):
    """[(name, kind "door"/"window", x, y, width, tag)] of the window and door inserts of a drawing,
    in drawing coordinates (tag: a type code carried by the insert's own attributes, else None).
    ``is_opening(layer, block name)`` says whether an insert is one; ``layer_kind(layer)`` (the
    layer's material category) also finds the ones only their lines show (opening_by_content);
    other blocks and xrefs are walked into (a unit plan block holds its doors). ``key_of(path)``
    finds an xref's document in ``xrefs``. ``tags``, a list, gets the type tags written on the
    drawing: [(code, x, y)]; ``names``, a list, gets the room names written on it: [(name, x, y)].
    An insert clipped with XCLIP gives only what lies inside its boundary."""
    from ezdxf.math import Matrix44, Vec3
    from .quantity_arch_rooms import is_room_name
    from .quantity_arch_schedule import code_key
    out, cache = [], {}

    def inside(point):
        return limit is None or (limit[0] <= point.x <= limit[2] and limit[1] <= point.y <= limit[3])

    def tag_at(key, entity, matrix):
        if key and tags is not None and len(tags) < MAX_TAGS:
            point = matrix.transform(Vec3(entity.dxf.insert))
            if inside(point):
                tags.append((key, float(point.x), float(point.y)))

    def name_at(text, entity, matrix):
        if names is not None and len(names) < MAX_TAGS and is_room_name(text):
            point = matrix.transform(Vec3(entity.dxf.insert))
            if inside(point):
                names.append((re.sub(r"\s+", "", text), float(point.x), float(point.y)))

    def walk(document, entities, matrix, inherit, depth):
        for entity in entities:
            kind_of_entity = entity.dxftype()
            if kind_of_entity in ("TEXT", "MTEXT"):
                if tags is not None or names is not None:
                    text = _text(entity)
                    if len(text) <= 24:
                        tag_at(code_key(text), entity, matrix)
                        name_at(text, entity, matrix)
                continue
            if len(out) >= MAX_INSERTS or kind_of_entity != "INSERT":
                continue
            layer = entity.dxf.get("layer", "0")
            if layer == "0" and inherit:
                layer = inherit
            name = entity.dxf.name
            try:
                placed = Matrix44.chain(entity.matrix44(), matrix)
            except Exception:
                continue
            block = document.blocks.get(name)
            if block is None or depth > 6:
                continue
            if block.block_record.is_xref:
                path = block.block.dxf.get("xref_path", "") or name
                target = xrefs.get(key_of(path)) if key_of else None
                if target is not None and not is_key_map(name):
                    clipped(entity, matrix, lambda: walk(target, target.modelspace(), placed, None, depth + 1))
                continue
            attrib_keys = [(code_key(_text(a)), a) for a in entity.attribs]
            by_content = None if is_opening(layer, name) else opening_by_content(document, name, layer, layer_kind, cache)
            if by_content or is_opening(layer, name):
                origin = placed.transform(Vec3(0, 0, 0))
                center = _block_center(document, name, cache) if ANONYMOUS_BLOCK.match(name.split("$0$")[-1]) else None
                if center is not None:
                    # a pasted group (A$C…) sits at the point it was pasted from, often far from the
                    # door it draws: it stands where its lines are (its swing is then no second door)
                    origin = placed.transform(Vec3(center[0], center[1], 0))
                if not inside(origin):
                    continue
                w, h, has_arc = _block_info(document, name, cache)
                ux = placed.transform_direction(Vec3(1, 0, 0)).magnitude
                uy = placed.transform_direction(Vec3(0, 1, 0)).magnitude
                width = max(w * ux, h * uy)
                if by_content:
                    kind = by_content
                else:
                    kind = "door" if has_arc or (DOOR_NAME.search(name) and not WINDOW_NAME.search(name)) else "window"
                own = next((key for key, _a in attrib_keys if key), None)
                out.append((name, kind, float(origin.x), float(origin.y), float(width), own, bool(has_arc)))
                continue
            for key, attrib in attrib_keys:  # a tag block: its attribute is the code (or a room name)
                tag_at(key, attrib, matrix)
                name_at(_text(attrib), attrib, matrix)
            clipped(entity, matrix, lambda: walk(document, block, placed, layer, depth + 1))

    def clipped(entity, matrix, go):
        """Walk into an insert; with an XCLIP boundary keep only what it finds inside the boundary.
        A sheet often inserts the whole building's xref (every floor side by side) in each frame,
        clipped to the one floor it shows: the other floors' doors are no openings of this one."""
        clip = _clip_polygon(entity, matrix)
        marks = (len(out), len(tags) if tags is not None else 0, len(names) if names is not None else 0)
        go()
        if not clip:
            return
        polygon, inverted = clip

        def kept(x, y):
            return _point_inside(polygon, x, y) != inverted
        out[marks[0]:] = [item for item in out[marks[0]:] if kept(item[2], item[3])]
        if tags is not None:
            tags[marks[1]:] = [item for item in tags[marks[1]:] if kept(item[1], item[2])]
        if names is not None:
            names[marks[2]:] = [item for item in names[marks[2]:] if kept(item[1], item[2])]

    from .quantity_floor_views import _clip_polygon, is_key_map
    from .quantity_floor_views import _inside as _point_inside
    walk(doc, doc.modelspace(), Matrix44(), None, 0)
    return out


def _distance(px, py, seg):
    ax, ay, bx, by = seg
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


LINE_DOOR = "문(블록 없음)"


def _swing(points):
    """(hinge x, y, leaf width) of a door swing drawn as plain lines: a quarter arc (60–120°) of
    0.5–1.3 m radius, the points (metres) on one circle; None for anything else."""
    if len(points) < 3:
        return None
    (ax, ay), (bx, by), (cx, cy) = points[0], points[len(points) // 2], points[-1]
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    if not 0.5 <= r <= 1.3 or any(abs(math.hypot(px - ux, py - uy) - r) > 0.03 + 0.05 * r for px, py in points):
        return None
    cos = ((ax - ux) * (cx - ux) + (ay - uy) * (cy - uy)) / (r * r)
    sweep = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
    return (ux, uy, r) if 60 <= sweep <= 120 else None


def _type_tags(placed, tags):
    """The type of each opening: its own attribute, else the nearest tag within 1.5 m (a window tag
    never goes to a swing door), else the tag its block's other copies all carry."""
    from .quantity_arch_schedule import kind_of, nearest_tags
    names = [own for (_n, _k, _x, _y, _w, own, _arc) in placed]
    loose = [i for i, own in enumerate(names) if not own]
    found = nearest_tags([(placed[i][2], placed[i][3]) for i in loose], tags, TAG_REACH_M,
                         allowed=lambda oi, key: not (placed[loose[oi]][6] and kind_of(key) == "window"))
    for oi, key in found.items():
        names[loose[oi]] = key
    by_block = {}
    for i, key in enumerate(names):
        if key:
            by_block.setdefault(placed[i][0], set()).add(key)
    for i, key in enumerate(names):
        if not key and len(by_block.get(placed[i][0], ())) == 1:
            names[i] = next(iter(by_block[placed[i][0]]))
    return names


def _without_copies(placed):
    """One opening per spot: a block inserted twice in the same place, or a door drawn as two
    blocks (leaf and frame) on one point, counts once (the widest kept)."""
    kept, grid = [], {}
    for item in sorted(placed, key=lambda i: -i[4]):
        _name, kind, lx, ly = item[:4]
        cell = (round(lx / DUPLICATE_M), round(ly / DUPLICATE_M))
        near = [k for gx in (cell[0] - 1, cell[0], cell[0] + 1) for gy in (cell[1] - 1, cell[1], cell[1] + 1)
                for k in grid.get((gx, gy), ())]
        if any(k[1] == kind and math.hypot(k[2] - lx, k[3] - ly) <= DUPLICATE_M for k in near):
            continue
        kept.append(item)
        grid.setdefault(cell, []).append(item)
    return kept


LINING_REACH_M = 0.15      # a board line this close along another wall is its lining (furring, insulation and board)
BUNDLE_ACROSS_M = 0.2      # board lines this close across, side by side, are one partition
BUNDLE_ANGLE = math.radians(5)


def _lining(seg, grid, cell):
    """Whether a dry-wall line runs along a wall of another kind (most of it within LINING_REACH_M
    of a parallel line of a concrete, block or ALC wall): a board lining, not a partition."""
    ax, ay, bx, by = seg
    length = math.hypot(bx - ax, by - ay)
    angle = math.atan2(by - ay, bx - ax)
    n = max(2, int(length / 0.1))
    near = 0
    for i in range(n):
        t = (i + 0.5) / n
        px, py = ax + (bx - ax) * t, ay + (by - ay) * t
        hit = False
        for other in grid.get((int(math.floor(px / cell)), int(math.floor(py / cell))), ()):
            ox0, oy0, ox1, oy1 = other
            turn = abs((math.atan2(oy1 - oy0, ox1 - ox0) - angle + math.pi / 2) % math.pi - math.pi / 2)
            if turn <= math.radians(10) and _distance(px, py, other) <= LINING_REACH_M:
                hit = True
                break
        near += hit
    return near >= 0.6 * n


def board_partitions(dry, others):
    """{"length", "thickness", "section", "drawn_as": "boards"} of dry partitions drawn by their
    boards (lines 1-4 cm apart, the studs between not drawn): the lines along a concrete, block or
    ALC wall are linings and left out; the rest, parallel and side by side within
    BUNDLE_ACROSS_M (one board pair, or both boards of a partition), make one partition each,
    as long as its lines run together. A lone line is no partition."""
    cell = 1.0
    grid = {}
    for seg in others:
        ax, ay, bx, by = seg
        for gx in range(int(math.floor(min(ax, bx) / cell - LINING_REACH_M)), int(math.floor(max(ax, bx) / cell + LINING_REACH_M)) + 1):
            for gy in range(int(math.floor(min(ay, by) / cell - LINING_REACH_M)), int(math.floor(max(ay, by) / cell + LINING_REACH_M)) + 1):
                grid.setdefault((gx, gy), []).append(seg)
    free = [seg for seg in dry if math.hypot(seg[2] - seg[0], seg[3] - seg[1]) >= 0.15 and not _lining(seg, grid, cell)]
    lines = []
    for ax, ay, bx, by in free:
        angle = math.atan2(by - ay, bx - ax) % math.pi
        ux, uy = math.cos(angle), math.sin(angle)
        s0, s1 = sorted((ax * ux + ay * uy, bx * ux + by * uy))
        lines.append((angle, -ax * uy + ay * ux, s0, s1))  # direction, offset across, extent along
    parent = list(range(len(lines)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    order = sorted(range(len(lines)), key=lambda i: (round(lines[i][0], 2), lines[i][1]))
    for a, i in enumerate(order):
        ai, di, si0, si1 = lines[i]
        for j in order[a + 1:a + 40]:
            aj, dj, sj0, sj1 = lines[j]
            turn = abs((aj - ai + math.pi / 2) % math.pi - math.pi / 2)
            if turn > BUNDLE_ANGLE:
                continue
            shared = min(si1, sj1) - max(si0, sj0)
            if abs(dj - di) <= BUNDLE_ACROSS_M and shared >= 0.5 * min(si1 - si0, sj1 - sj0):
                parent[root(j)] = root(i)
    bundles = {}
    for i in range(len(lines)):
        bundles.setdefault(root(i), []).append(lines[i])
    length = spread = 0.0
    for members in bundles.values():
        if len(members) < 2:
            continue
        along = max(m[3] for m in members) - min(m[2] for m in members)
        length += along
        spread += along * (max(m[1] for m in members) - min(m[1] for m in members))
    thickness = spread / length if length else 0.0
    return {"length": round(length, 2), "thickness": round(thickness, 3), "section": round(spread, 3), "drawn_as": "boards"}


def measure(paths, box, inserts=(), tags=(), names=()):
    """Walls by material and the openings of one architectural frame.

    paths: quantity_floor_views paths [((category, layer) or category, x0, y0, x1, y1, points)];
    box: the frame in drawing units (mm); inserts / tags / names: opening_inserts(...) of the drawing.
    The rooms the walls enclose come with it (quantity_arch_rooms)."""
    from . import quantity_floor_takeoff as takeoff
    x0, y0, x1, y1 = box
    segments = {kind: [] for kind in WALL_KINDS + ("frame",)}
    for category, ax, ay, bx, by, points in paths:
        kind = category[0] if isinstance(category, tuple) else category
        if kind not in segments or bx < x0 or ax > x1 or by < y0 or ay > y1:
            continue
        local = [((px - x0) / 1000.0, (py - y0) / 1000.0) for px, py in points]
        for (pa, qa), (pb, qb) in zip(local, local[1:]):
            if abs(pa - pb) + abs(qa - qb) > 0.005:
                segments[kind].append((pa, qa, pb, qb))
    walls = {}
    for kind in WALL_KINDS:
        if not segments[kind]:
            continue
        measured = takeoff.measure_walls(segments[kind])
        # the wall's two long faces; its end lines (the short closing line of each drawn piece) are not length
        faces = float(measured.get("faces") or 0) - float(measured.get("end_faces") or 0)
        section = float(measured.get("area") or 0)
        if faces <= 0 or section <= 0:
            continue
        thickness = 2 * section / faces
        if thickness < MIN_WALL_M[kind]:
            # board lines 1-4 cm apart: linings on the walls (석고보드 on concrete) and, standing free,
            # dry partitions drawn by their boards
            if kind == "dry":
                found = board_partitions(segments["dry"], segments["frame"] + segments["masonry"] + segments["alc"])
                if found["length"] > 0:
                    walls[kind] = found
            continue
        walls[kind] = {"length": round(faces / 2.0, 2), "thickness": round(thickness, 3), "section": round(section, 3)}
    grid = {}
    for kind, segs in segments.items():
        for seg in segs:
            for gx in range(int(math.floor(min(seg[0], seg[2]))), int(math.floor(max(seg[0], seg[2]))) + 1):
                for gy in range(int(math.floor(min(seg[1], seg[3]))), int(math.floor(max(seg[1], seg[3]))) + 1):
                    grid.setdefault((gx, gy), []).append((kind, seg))
    from .quantity_arch_schedule import kind_of
    placed = []
    for item in inserts:
        name, kind, px, py, width = item[:5]
        own, arc = (item[5], item[6]) if len(item) > 6 else (None, kind == "door")
        if (x0 <= px <= x1 and y0 <= py <= y1) and width > 0:
            placed.append((name, kind, (px - x0) / 1000.0, (py - y0) / 1000.0, width, own, arc))
    placed = _without_copies(placed)
    # doors drawn as plain lines on a door layer (no block): their swing arcs, where no door block stands
    for _category, ax, ay, bx, by, points in paths:
        kind = _category[0] if isinstance(_category, tuple) else _category
        if kind != "opening" or bx < x0 or ax > x1 or by < y0 or ay > y1:
            continue
        swing = _swing([((px - x0) / 1000.0, (py - y0) / 1000.0) for px, py in points])
        if swing is None:
            continue
        hx, hy, leaf = swing
        if any(math.hypot(hx - item[2], hy - item[3]) <= max(leaf, 0.6) for item in placed):
            continue  # the arc of a door block (or of a line door already taken)
        placed.append((LINE_DOOR, "door", hx, hy, leaf * 1000.0, None, True))
    local_tags = [(key, (tx - x0) / 1000.0, (ty - y0) / 1000.0) for key, tx, ty in tags or () if x0 <= tx <= x1 and y0 <= ty <= y1]
    types = _type_tags(placed, local_tags)
    openings = {}
    for (block, kind, lx, ly, width, _own, _arc), tag in zip(placed, types):
        name = tag or block
        if tag:
            kind = kind_of(tag)
        best = None
        for gx in (int(math.floor(lx)) - 1, int(math.floor(lx)), int(math.floor(lx)) + 1):
            for gy in (int(math.floor(ly)) - 1, int(math.floor(ly)), int(math.floor(ly)) + 1):
                for wall_kind, seg in grid.get((gx, gy), ()):
                    d = _distance(lx, ly, seg)
                    if d <= OPENING_REACH_M and (best is None or d < best[0]):
                        best = (d, wall_kind)
        item = openings.setdefault(name, {"name": name, "kind": kind, "count": 0, "width": 0.0, "on": {}, "tagged": bool(tag), "blocks": []})
        if block not in item["blocks"]:
            item["blocks"].append(block)
        item["count"] += 1
        item["width"] += width / 1000.0
        wall = best[1] if best else "none"
        item["on"][wall] = item["on"].get(wall, 0) + 1
    out = []
    for item in sorted(openings.values(), key=lambda i: (i["kind"], i["name"])):
        out.append(dict(item, width=round(item["width"] / item["count"], 3)))
    result = {"walls": walls, "openings": out}
    try:  # the rooms never cost the walls and openings their measure
        from .quantity_arch_rooms import find_rooms
        local_names = [(text, (nx - x0) / 1000.0, (ny - y0) / 1000.0) for text, nx, ny in names or ()
                       if x0 <= nx <= x1 and y0 <= ny <= y1]
        placed_openings = [(tag or block, kind_of(tag) if tag else kind, lx, ly, width / 1000.0)
                           for (block, kind, lx, ly, width, _own, _arc), tag in zip(placed, types)]
        result["rooms"] = find_rooms({k: v for k, v in segments.items() if v}, placed_openings, local_names)
    except Exception as exc:
        result["rooms_error"] = str(exc)[:200]
    return result
