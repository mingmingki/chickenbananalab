"""Member lengths measured from CAD plan linework (beams and walls), matched to plan labels.

Beams are drawn as two parallel edge lines on beam/girder layers and walls as two parallel
concrete lines. Two parallel lines whose distance is a plausible member width and whose
projections overlap give one measured piece (length = overlap, width = distance). Beam
pieces touching end to end form one span; wall pieces on one line with the same
thickness and short gaps (junctions, door openings) form one wall run whose length is the
sum of its pieces, so openings are not counted.

A span or run takes the mark of, in this order: a name-layer extent line drawn along it
with the label at its end, a leader ending on it, or the nearest label of the same member
family beside it. When labels of different marks sit along one run, the run is split
between them. Anything else stays unlabelled and is reported, never guessed. Model units
are millimetres. Measured lengths are review evidence, not verified quantities.
"""
from __future__ import annotations

import bisect
import math
import re
from collections import Counter, defaultdict

MAX_SEGMENTS = 150000
MAX_BLOCK_DEPTH = 8
NAME_LAYER = re.compile(r"NAME|MARK|TAG", re.I)
FAMILY_LAYERS = (("beams", re.compile(r"GIRDER|BEAM|GDR", re.I)), ("walls", re.compile(r"CONC|CORE|WALL", re.I)))
NOT_STRUCTURAL = re.compile(r"DRY|BRICK|ALC|BLOCK|PARTITION|경량|조적", re.I)
LIMITS = {
    # width range (mm), shortest piece, longest gap bridged inside one span/run, label reach
    "beams": {"wmin": 150, "wmax": 1300, "min_piece": 300, "gap": 60, "reach": 3000},
    "walls": {"wmin": 100, "wmax": 1000, "min_piece": 400, "gap": 2500, "reach": 3000},
}
ANGLE_TOL = 0.6
SAME_LINE = 40.0


def family_of(layer):
    """("beams" | "walls", is_name_layer) for a layer, or (None, False)."""
    layer = str(layer or "")
    if NOT_STRUCTURAL.search(layer):
        return None, False
    family = next((name for name, pattern in FAMILY_LAYERS if pattern.search(layer)), None)
    if family is None:
        return None, False
    return family, bool(NAME_LAYER.search(layer))


def _block_items(doc, name, cache, depth=0):
    """Line items of a block in block coordinates, nested blocks included (cached by name)."""
    if name in cache:
        return cache[name]
    cache[name] = []
    block = doc.blocks.get(name)
    if block is None or block.block_record.is_xref or depth > MAX_BLOCK_DEPTH:
        return []
    rows = []
    for entity in block:
        rows.extend(_entity_items(entity))
        if entity.dxftype() == "INSERT":
            rows.extend(_placed(entity, _block_items(doc, entity.dxf.name, cache, depth + 1)))
        if len(rows) >= MAX_SEGMENTS:
            break
    cache[name] = rows
    return rows


def _entity_items(entity):
    """[(layer, [(x, y), ...])] polylines of a LINE or LWPOLYLINE in its own coordinates."""
    kind = entity.dxftype()
    layer = entity.dxf.get("layer", "")
    if kind == "LINE":
        a, b = entity.dxf.start, entity.dxf.end
        return [(layer, [(float(a.x), float(a.y)), (float(b.x), float(b.y))])]
    if kind == "LWPOLYLINE":
        points = [(float(x), float(y)) for x, y in entity.get_points("xy")]
        if entity.closed and points:
            points.append(points[0])
        return [(layer, points)] if len(points) >= 2 else []
    return []


def _placed(insert, rows):
    if not rows:
        return []
    matrix = insert.matrix44()
    insert_layer = insert.dxf.get("layer")
    placed = []
    for layer, points in rows:
        moved = [matrix.transform((x, y, 0)) for x, y in points]
        placed.append((insert_layer if layer in (None, "0") else layer, [(float(p.x), float(p.y)) for p in moved]))
    return placed


def collect_segments(doc, skip_blocks=()):
    """Member lines and name-layer paths of one drawing (model space and ordinary blocks).

    Returns {"segments": [(x1, y1, x2, y2, family)], "paths": [(family, [(x, y), ...])],
    "truncated": bool}. Nothing is collected when no layer names a member family.
    """
    layers = {layer.dxf.name for layer in doc.layers}
    layers.update(entity.dxf.get("layer", "") for entity in doc.modelspace())  # layers need not be declared
    if not any(family_of(name)[1] for name in layers):
        return {"segments": [], "paths": [], "truncated": False}
    segments, paths, cache = [], [], {}

    def keep(layer, points):
        family, is_name = family_of(layer)
        if family is None:
            return
        if is_name:
            paths.append((family, points))
            return
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            if math.hypot(bx - ax, by - ay) >= 50:
                segments.append((ax, ay, bx, by, family))

    for entity in doc.modelspace():
        if len(segments) >= MAX_SEGMENTS:
            break
        if entity.dxftype() == "INSERT":
            block = doc.blocks.get(entity.dxf.name)
            if block is None or block.block_record.is_xref or entity.dxf.name in skip_blocks:
                continue
            try:
                rows = _placed(entity, _block_items(doc, entity.dxf.name, cache))
            except Exception:
                rows = []
            for layer, points in rows:
                keep(layer, points)
        else:
            for layer, points in _entity_items(entity):
                keep(layer, points)
    return {"segments": segments[:MAX_SEGMENTS], "paths": paths, "truncated": len(segments) >= MAX_SEGMENTS}


# ── measuring ───────────────────────────────────────────────────────────────

def _axis(angle):
    rad = math.radians(angle)
    return math.cos(rad), math.sin(rad), -math.sin(rad), math.cos(rad)


def _canonical(angle):
    """Direction in [-0.25, 179.75): lines a hair off horizontal keep one normal direction."""
    angle %= 180.0
    return angle - 180.0 if angle >= 179.75 else angle


def _line_items(segments):
    items = []
    for ax, ay, bx, by in segments:
        angle = _canonical(math.degrees(math.atan2(by - ay, bx - ax)))
        ux, uy, nx, ny = _axis(angle)
        t1, t2 = sorted((ax * ux + ay * uy, bx * ux + by * uy))
        items.append((angle, ax * nx + ay * ny, t1, t2, (ax, ay, bx, by)))
    return items


def _angle_diff(a, b):
    return abs(((a - b + 90.0) % 180.0) - 90.0)


def pieces(segments, family):
    """Pieces between pairs of parallel lines: {angle, offset, width, t1, t2} (local coordinates)."""
    limit = LIMITS[family]
    items = _line_items(segments)
    bins = defaultdict(list)
    for index, item in enumerate(items):
        bins[int(math.floor(item[0] * 2 + 0.5))].append(index)
    for key in bins:
        bins[key].sort(key=lambda i: items[i][1])
    offsets = {key: [items[i][1] for i in members] for key, members in bins.items()}
    margin = 2000.0  # neighbouring directions shift offsets; the exact test below decides
    out = []
    for index, (angle, offset, t1, t2, _raw) in enumerate(items):
        ux, uy, nx, ny = _axis(angle)
        key = int(math.floor(angle * 2 + 0.5))
        candidates = []
        for near in (key - 1, key, key + 1):
            members = bins.get(near)
            if not members:
                continue
            lo = bisect.bisect_left(offsets[near], offset + limit["wmin"] - margin)
            hi = bisect.bisect_right(offsets[near], offset + limit["wmax"] + margin)
            for j in members[lo:hi]:
                other = items[j]
                if j == index or _angle_diff(other[0], angle) > ANGLE_TOL:
                    continue
                ax, ay, bx, by = other[4]
                distance = ((ax + bx) / 2) * nx + ((ay + by) / 2) * ny - offset  # measured on this line's normal
                s1, s2 = sorted((ax * ux + ay * uy, bx * ux + by * uy))
                start, end = max(t1, s1), min(t2, s2)
                if limit["wmin"] <= distance <= limit["wmax"] and end - start >= limit["min_piece"]:
                    candidates.append((distance, start, end))
        if not candidates:
            continue
        nearest = min(c[0] for c in candidates)
        for distance, start, end in candidates:
            if distance - nearest <= 20:  # the facing edge may be drawn in several segments
                out.append({"angle": angle, "offset": offset + distance / 2, "width": distance, "t1": start, "t2": end})
    return out


def _closed_outline(run, ends):
    """True when both ends of a short single piece are closed by lines as long as its width."""
    if len(run["pieces"]) != 1:
        return False
    ux, uy, nx, ny = _axis(run["angle"])
    half = run["width"] / 2.0
    for t in (run["t1"], run["t2"]):
        a = (t * ux + (run["offset"] - half) * nx, t * uy + (run["offset"] - half) * ny)
        b = (t * ux + (run["offset"] + half) * nx, t * uy + (run["offset"] + half) * ny)
        cell = (round(a[0] / 100), round(a[1] / 100))
        near = [pair for dx in (-1, 0, 1) for dy in (-1, 0, 1) for pair in ends.get((cell[0] + dx, cell[1] + dy), ())]
        if not any(math.hypot(p[0] - a[0], p[1] - a[1]) <= 30 and math.hypot(q[0] - b[0], q[1] - b[1]) <= 30 for p, q in near):
            return False
    return True


def runs(found, family):
    """Spans (beams) or wall runs: pieces on one line with the same width and short gaps.

    Pieces are grouped on a shared axis (the first piece's direction), so lines drawn a hair
    apart in angle still fall on one line; offsets and positions are re-projected onto it.
    """
    gap = LIMITS[family]["gap"]
    ordered = sorted(found, key=lambda p: (int(math.floor(p["angle"] * 2 + 0.5)), p["offset"]))
    groups, by_bin = [], defaultdict(list)
    for piece in ordered:
        ux, uy, nx, ny = _axis(piece["angle"])
        mid = (piece["t1"] + piece["t2"]) / 2.0
        centre = (mid * ux + piece["offset"] * nx, mid * uy + piece["offset"] * ny)
        placed = False
        for group in by_bin.get(int(math.floor(piece["angle"] * 2 + 0.5)), ()):
            if _angle_diff(piece["angle"], group["angle"]) > ANGLE_TOL:
                continue
            gx, gy, gnx, gny = _axis(group["angle"])
            if abs(centre[0] * gnx + centre[1] * gny - group["offset"]) <= SAME_LINE:
                group["members"].append((piece, centre))
                placed = True
                break
        if not placed:
            gx, gy, gnx, gny = _axis(piece["angle"])
            group = {"angle": piece["angle"], "offset": centre[0] * gnx + centre[1] * gny, "members": [(piece, centre)]}
            groups.append(group)
            key = int(math.floor(piece["angle"] * 2 + 0.5))
            for near in (key - 1, key, key + 1):
                by_bin[near].append(group)
    result = []
    for group in groups:
        gx, gy, gnx, gny = _axis(group["angle"])
        projected = []
        for piece, (cx, cy) in group["members"]:
            mid = cx * gx + cy * gy
            half = (piece["t2"] - piece["t1"]) / 2.0
            projected.append({"angle": group["angle"], "offset": group["offset"], "width": piece["width"],
                              "t1": mid - half, "t2": mid + half})
        widths = []
        for piece in sorted(projected, key=lambda p: p["width"]):
            if widths and abs(piece["width"] - widths[-1][-1]["width"]) <= 20:
                widths[-1].append(piece)
            else:
                widths.append([piece])
        for same in widths:
            current = None
            for piece in sorted(same, key=lambda p: p["t1"]):
                if current is not None and piece["t1"] - current["t2"] <= gap:
                    current["t2"] = max(current["t2"], piece["t2"])
                    current["pieces"].append((piece["t1"], piece["t2"]))
                    current["solid"] = sum(end - start for start, end in _union(current["pieces"]))
                else:
                    current = dict(piece, solid=piece["t2"] - piece["t1"], pieces=[(piece["t1"], piece["t2"])])
                    result.append(current)
    return result


def _label_points(labels, paths):
    """Each label with its anchor point and the far ends of leaders that start at it."""
    rows = []
    for label in labels:
        h = float(label.get("h") or 300.0)
        anchor = (label["x"] + 0.4 * h * len(label["text"]), label["y"] + 0.5 * h)
        reach = max(4 * h, 800.0)
        targets, extents = [], []
        for points in paths:
            for end, other in ((points[0], points[-1]), (points[-1], points[0])):
                if math.hypot(end[0] - anchor[0], end[1] - anchor[1]) <= reach:
                    targets.append(other)
                    if len(points) == 2 and math.hypot(other[0] - end[0], other[1] - end[1]) >= 1500:
                        extents.append((points[0], points[1]))
        rows.append({"mark": label["mark"], "anchor": anchor, "targets": targets, "extents": extents})
    return rows


def _claims(run, points, reach):
    """[(priority, position along the run, mark)] for labels that point at or sit beside a run."""
    ux, uy, nx, ny = _axis(run["angle"])
    half = run["width"] / 2.0
    found = []
    for point in points:
        for (ax, ay), (bx, by) in point["extents"]:
            angle = math.degrees(math.atan2(by - ay, bx - ax)) % 180.0
            if _angle_diff(angle, run["angle"]) > 1.0:
                continue
            e1, e2 = sorted((ax * ux + ay * uy, bx * ux + by * uy))
            overlap = min(e2, run["t2"]) - max(e1, run["t1"])
            if abs(ax * nx + ay * ny - run["offset"]) <= 3000 and overlap >= 0.5 * (run["t2"] - run["t1"]):
                found.append((0, (max(e1, run["t1"]) + min(e2, run["t2"])) / 2, point["mark"]))
        for tx, ty in point["targets"]:
            along = tx * ux + ty * uy
            if run["t1"] - 300 <= along <= run["t2"] + 300 and abs(tx * nx + ty * ny - run["offset"]) <= half + 300:
                found.append((1, along, point["mark"]))
        ax, ay = point["anchor"]
        along = ax * ux + ay * uy
        distance = abs(ax * nx + ay * ny - run["offset"])
        if run["t1"] - 300 <= along <= run["t2"] + 300 and distance <= reach:
            found.append((2 + distance / reach, along, point["mark"]))
    return found


def _union(intervals):
    """Overlapping intervals merged (lines drawn twice must not count twice)."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _split(run, claims):
    """Solid length of a run per mark; distinct marks along one run split it at midpoints."""
    marks = {mark for _p, _a, mark in claims}
    if len(marks) == 1:
        return {marks.pop(): run["solid"]}
    anchors = sorted({(along, mark) for _p, along, mark in claims})
    bounds = [(a1 + a2) / 2 for (a1, _m1), (a2, _m2) in zip(anchors, anchors[1:])]
    shares = Counter()
    for start, end in _union(run["pieces"]):
        edges = [start] + [b for b in bounds if start < b < end] + [end]
        for lo, hi in zip(edges, edges[1:]):
            mid = (lo + hi) / 2
            owner = min(anchors, key=lambda pair: abs(pair[0] - mid))[1]
            shares[owner] += hi - lo
    return dict(shares)


def measure(segments, paths, labels, family):
    """Per-mark measurement of one family on one plan frame (coordinates already local)."""
    own = [s[:4] for s in segments if s[4] == family]
    found = runs(pieces(own, family), family)
    if family == "walls":
        ends = defaultdict(list)
        for ax, ay, bx, by in own:
            for p, q in (((ax, ay), (bx, by)), ((bx, by), (ax, ay))):
                ends[(round(p[0] / 100), round(p[1] / 100))].append((p, q))
        short = [r for r in found if r["solid"] < max(600.0, 1.2 * r["width"])]
        boxes = [r for r in found if r["solid"] < 1500 and _closed_outline(r, ends)]
        found = [r for r in found if r not in short and r not in boxes]  # column outlines and stubs are not walls
    points = _label_points([label for label in labels if label["family"] == family],
                           [points for path_family, points in paths if path_family == family])
    reach = LIMITS[family]["reach"]
    marks = defaultdict(lambda: {"runs": 0, "length_mm": 0.0, "widths": Counter()})  # widths: width -> length
    unlabelled = {"runs": 0, "length_mm": 0.0, "widths": Counter()}
    measured = 0.0
    for run in found:
        measured += run["solid"]
        claims = _claims(run, points, reach)
        strong = [c for c in claims if c[0] < 2]  # an extent line or a leader names the run
        if strong:
            claims = strong
        elif claims:
            best = min(c[0] for c in claims)
            claims = [c for c in claims if c[0] - best < 0.15]
        width = int(round(run["width"] / 10.0) * 10)
        if not claims:
            unlabelled["runs"] += 1
            unlabelled["length_mm"] += run["solid"]
            unlabelled["widths"][width] += run["solid"]
            continue
        for mark, length in _split(run, claims).items():
            row = marks[mark]
            row["runs"] += 1
            row["length_mm"] += length
            row["widths"][width] += length
    labelled = sum(row["length_mm"] for row in marks.values())
    compact = lambda row: {"runs": row["runs"], "length_mm": int(round(row["length_mm"])),
                           "widths": {str(k): int(round(v)) for k, v in row["widths"].most_common(4)}}
    return {"marks": {mark: compact(row) for mark, row in sorted(marks.items())},
            "measured_mm": int(round(measured)), "labelled_mm": int(round(labelled)),
            "unlabelled": compact(unlabelled)}


def measure_sheet(sheet, geometry, texts, normalize_mark):
    """Beam and wall measurements of one identified frame; None without member labels."""
    if not sheet.get("frame_bbox") or not geometry or not geometry.get("segments"):
        return None
    x0, y0, x1, y1 = sheet["frame_bbox"]
    ox, oy = (x0 + x1) / 2.0, (y0 + y1) / 2.0  # local origin keeps offsets small and exact
    labels = []
    for text in texts:
        family, is_name = family_of(text.get("layer"))
        mark = normalize_mark(text.get("text"))
        if family and is_name and mark and x0 <= text["x"] <= x1 and y0 <= text["y"] <= y1:
            labels.append(dict(text, x=text["x"] - ox, y=text["y"] - oy, family=family, mark=mark))
    if not labels:
        return None
    inside = lambda x, y: x0 <= x <= x1 and y0 <= y <= y1
    segments = [(s[0] - ox, s[1] - oy, s[2] - ox, s[3] - oy, s[4]) for s in geometry["segments"]
                if inside((s[0] + s[2]) / 2, (s[1] + s[3]) / 2)]
    paths = [(family, [(x - ox, y - oy) for x, y in points]) for family, points in geometry.get("paths", [])
             if any(inside(x, y) for x, y in points)]
    result = {"version": 1, "units": "mm"}
    for family in ("beams", "walls"):
        if any(label["family"] == family for label in labels) and any(s[4] == family for s in segments):
            result[family] = measure(segments, paths, labels, family)
    return result if len(result) > 2 else None
