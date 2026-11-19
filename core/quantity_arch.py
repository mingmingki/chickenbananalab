"""Architectural plan measures from CAD (no AI): walls by material and the windows and doors.

Runs inside the floor-views subprocess on each architectural plan frame (pure module, no Django):

* walls: the masonry (조적), ALC and dry (건식벽) lines of the frame are measured like the concrete
  walls of a structural plan (quantity_floor_takeoff.measure_walls): the long faces of the wall
  section give the length (faces / 2, the short end line of each drawn piece left out) and the
  mean thickness (2 × section / faces). A doorway or a window breaks the wall lines, so the length
  is the wall without its openings.
* windows and doors: block inserts on window/door layers (or named like one), each with its block
  name (the type), its width (the longer side of the placed block) and whether it is a door (a
  swing arc, or a door-like name). Each is matched to the wall it sits in (the nearest wall line
  within 0.6 m), so the takeoff can add the wall left above a door and above/below a window.

Lengths in metres, frame-local (x right, y up), as quantity_floor_views._plan_model gives them.
"""
from __future__ import annotations

import math
import re

WALL_KINDS = ("masonry", "alc", "dry")
OPENING_REACH_M = 0.6
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


def opening_inserts(doc, xrefs, limit=None, is_opening=None, key_of=None):
    """[(name, kind "door"/"window", x, y, width)] of the window and door inserts of a drawing, in
    drawing coordinates. ``is_opening(layer, block name)`` says whether an insert is one; other
    blocks and xrefs are walked into (a unit plan block holds its doors). ``key_of(path)`` finds an
    xref's document in ``xrefs``."""
    from ezdxf.math import Matrix44, Vec3
    out, cache = [], {}

    def walk(document, entities, matrix, inherit, depth):
        for entity in entities:
            if len(out) >= MAX_INSERTS or entity.dxftype() != "INSERT":
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
                if target is not None:
                    walk(target, target.modelspace(), placed, None, depth + 1)
                continue
            if is_opening(layer, name):
                origin = placed.transform(Vec3(0, 0, 0))
                if limit is not None and not (limit[0] <= origin.x <= limit[2] and limit[1] <= origin.y <= limit[3]):
                    continue
                w, h, has_arc = _block_info(document, name, cache)
                ux = placed.transform_direction(Vec3(1, 0, 0)).magnitude
                uy = placed.transform_direction(Vec3(0, 1, 0)).magnitude
                width = max(w * ux, h * uy)
                kind = "door" if has_arc or (DOOR_NAME.search(name) and not WINDOW_NAME.search(name)) else "window"
                out.append((name, kind, float(origin.x), float(origin.y), float(width)))
                continue
            walk(document, block, placed, layer, depth + 1)

    walk(doc, doc.modelspace(), Matrix44(), None, 0)
    return out


def _distance(px, py, seg):
    ax, ay, bx, by = seg
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def measure(paths, box, inserts=()):
    """Walls by material and the openings of one architectural frame.

    paths: quantity_floor_views paths [((category, layer) or category, x0, y0, x1, y1, points)];
    box: the frame in drawing units (mm); inserts: opening_inserts(...) of the drawing."""
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
        walls[kind] = {"length": round(faces / 2.0, 2), "thickness": round(2 * section / faces, 3), "section": round(section, 3)}
    grid = {}
    for kind, segs in segments.items():
        for seg in segs:
            for gx in range(int(math.floor(min(seg[0], seg[2]))), int(math.floor(max(seg[0], seg[2]))) + 1):
                for gy in range(int(math.floor(min(seg[1], seg[3]))), int(math.floor(max(seg[1], seg[3]))) + 1):
                    grid.setdefault((gx, gy), []).append((kind, seg))
    openings = {}
    for name, kind, px, py, width in inserts:
        if not (x0 <= px <= x1 and y0 <= py <= y1) or width <= 0:
            continue
        lx, ly = (px - x0) / 1000.0, (py - y0) / 1000.0
        best = None
        for gx in (int(math.floor(lx)) - 1, int(math.floor(lx)), int(math.floor(lx)) + 1):
            for gy in (int(math.floor(ly)) - 1, int(math.floor(ly)), int(math.floor(ly)) + 1):
                for wall_kind, seg in grid.get((gx, gy), ()):
                    d = _distance(lx, ly, seg)
                    if d <= OPENING_REACH_M and (best is None or d < best[0]):
                        best = (d, wall_kind)
        item = openings.setdefault(name, {"name": name, "kind": kind, "count": 0, "width": 0.0, "on": {}})
        item["count"] += 1
        item["width"] += width / 1000.0
        wall = best[1] if best else "none"
        item["on"][wall] = item["on"].get(wall, 0) + 1
    out = []
    for item in sorted(openings.values(), key=lambda i: (i["kind"], i["name"])):
        out.append(dict(item, width=round(item["width"] / item["count"], 3)))
    return {"walls": walls, "openings": out}
