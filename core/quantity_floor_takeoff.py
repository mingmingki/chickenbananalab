"""Floor-by-floor structural takeoff from CAD plans: "N층 = N층 수직재 + N+1층 바닥 수평재".

Two halves:

* ``measure_plan`` runs inside the floor-views subprocess on the raw member lines of one
  structural plan frame (metres, frame-local). It rasterises the cut concrete walls, finds the
  thin enclosed regions between wall faces (the wall cross-sections), and measures face lengths
  (inside / outside), column outlines, slab openings and stair halls.
* ``build_takeoff`` combines those measures with the storey heights of the floor-views model
  and, when the AI reading has run, the CAD schedules (``member_links``: beam sizes, column
  sizes, measured beam lengths per plan) into rows per building and storey.

A structural plan titled "N층 구조평면도" shows the walls standing on floor N (layer CONC; the
walls below are drawn on CONC_BELOW and are not used) and the slab and beams of floor N. So
row N takes the walls of plan N and the slab and beams of plan N+1. All values are estimates
from drawings; the notes say what was assumed.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from .quantity_assumed import CONCRETE, Assumed, clean_overrides, pick, said

TAKEOFF_VERSION = 1

# ── plan measures ──────────────────────────────────────────────────────────

BASE_CELL = 0.02          # 20 mm grid: a 180 mm wall is nine cells across
MAX_CELLS = 6_000_000     # bigger plans (parking decks) use a coarser grid
THIN_HALF_WIDTH = 0.45    # enclosed regions thinner than about 0.9 m are wall sections
GROW = 1                  # line thickening that closes hairline gaps at corners
CAP_MIN, CAP_MAX = 0.08, 1.0
JOIN_TOL = 0.012


def _segments_of(paths):
    out = []
    for points in paths:
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            if abs(ax - bx) + abs(ay - by) > 0.005:
                out.append((ax, ay, bx, by))
    return out


def _point_segment_distance(px, py, seg):
    ax, ay, bx, by = seg
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _bucket(index, x0, y0, x1, y1, size=1.0):
    for gx in range(int(math.floor(x0 / size)), int(math.floor(x1 / size)) + 1):
        for gy in range(int(math.floor(y0 / size)), int(math.floor(y1 / size)) + 1):
            yield gx, gy


def wall_caps(segments):
    """Closing lines for open wall ends: two parallel faces that stop side by side 80-1000 mm
    apart with nothing joining them (the end line is often missing where a wall meets an
    opening or a different layer)."""
    grid = {}
    for i, (ax, ay, bx, by) in enumerate(segments):
        for cell in _bucket(i, min(ax, bx) - JOIN_TOL, min(ay, by) - JOIN_TOL, max(ax, bx) + JOIN_TOL, max(ay, by) + JOIN_TOL):
            grid.setdefault(cell, []).append(i)
    ends = []
    for i, (ax, ay, bx, by) in enumerate(segments):
        length = math.hypot(bx - ax, by - ay)
        if length < 0.05:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        for px, py, dx, dy in ((ax, ay, -ux, -uy), (bx, by, ux, uy)):
            joined = False
            for j in grid.get((int(math.floor(px)), int(math.floor(py))), ()):
                if j != i and _point_segment_distance(px, py, segments[j]) <= JOIN_TOL:
                    joined = True
                    break
            if not joined:
                ends.append((px, py, dx, dy))
    near = {}
    for k, (px, py, _dx, _dy) in enumerate(ends):
        near.setdefault((int(math.floor(px)), int(math.floor(py))), []).append(k)
    candidates = []
    for k, (px, py, dx, dy) in enumerate(ends):
        cx, cy = int(math.floor(px)), int(math.floor(py))
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for m in near.get((gx, gy), ()):
                    if m <= k:
                        continue
                    qx, qy, ex, ey = ends[m]
                    gap = math.hypot(qx - px, qy - py)
                    if not CAP_MIN <= gap <= CAP_MAX:
                        continue
                    if dx * ex + dy * ey < 0.98:          # both faces end the same way
                        continue
                    if abs((qx - px) * dx + (qy - py) * dy) > 0.02 + 0.05 * gap:  # side by side
                        continue
                    candidates.append((gap, k, m))
    candidates.sort()
    used, caps = set(), []
    for gap, k, m in candidates:
        if k in used or m in used:
            continue
        used.update((k, m))
        caps.append((ends[k][0], ends[k][1], ends[m][0], ends[m][1]))
    return caps


def _shift(a, dx, dy):
    out = np.zeros_like(a)
    h, w = a.shape
    out[max(dy, 0):h + min(dy, 0), max(dx, 0):w + min(dx, 0)] = a[max(-dy, 0):h + min(-dy, 0), max(-dx, 0):w + min(-dx, 0)]
    return out


# Boolean grid algebra goes through explicit ufuncs, never the & | ~ operators: numpy 2.2 on
# Python 3.14 reuses a named local array as a "temporary" for operators and overwrites it.
def _or(*arrays):
    out = arrays[0].copy()
    for other in arrays[1:]:
        np.logical_or(out, other, out=out)
    return out


def _and(*arrays):
    out = arrays[0].copy()
    for other in arrays[1:]:
        np.logical_and(out, other, out=out)
    return out


def _not(a):
    return np.logical_not(a)


def _dil4(a):
    return _or(a, _shift(a, 1, 0), _shift(a, -1, 0), _shift(a, 0, 1), _shift(a, 0, -1))


def _dil8(a):
    return _or(_dil4(a), _shift(a, 1, 1), _shift(a, -1, 1), _shift(a, 1, -1), _shift(a, -1, -1))


def _erode4(a):
    return _and(a, _shift(a, 1, 0), _shift(a, -1, 0), _shift(a, 0, 1), _shift(a, 0, -1))


def _runs(row):
    """[start, end) column runs of True cells in one grid row."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def _fill(seed, mask):
    """Cells of ``mask`` 4-connected to a ``seed`` cell inside it (a flood fill, done on row runs:
    a run touches the runs of the next row it overlaps)."""
    height = mask.shape[0]
    rows = [_runs(mask[y]) for y in range(height)]
    parent = []
    first = []
    for y, runs in enumerate(rows):
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
    picked = set()
    seeded = _and(seed, mask)
    for y in np.flatnonzero(seeded.any(axis=1)).tolist():
        for k, (a, b) in enumerate(rows[y]):
            if seeded[y, a:b].any():
                picked.add(find(first[y] + k))
    out = np.zeros_like(mask)
    if picked:
        for y, runs in enumerate(rows):
            for k, (a, b) in enumerate(runs):
                if find(first[y] + k) in picked:
                    out[y, a:b] = True
    return out


def _raster(segments, minx, miny, cell, shape):
    grid = np.zeros(shape, dtype=bool)
    for ax, ay, bx, by in segments:
        n = int(max(abs(bx - ax), abs(by - ay)) / (cell * 0.5)) + 2
        t = np.linspace(0.0, 1.0, n)
        gx = np.clip(((ax + (bx - ax) * t - minx) / cell).astype(int), 0, shape[1] - 1)
        gy = np.clip(((ay + (by - ay) * t - miny) / cell).astype(int), 0, shape[0] - 1)
        grid[gy, gx] = True
    return grid


def _polygon_area(points):
    return abs(sum(points[i][0] * points[i - 1][1] - points[i - 1][0] * points[i][1] for i in range(len(points)))) / 2.0


def _hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _clusters(segments, margin):
    """Point lists of groups of lines whose boxes come within ``margin`` of each other."""
    parent = list(range(len(segments)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    grid = {}
    for i, (ax, ay, bx, by) in enumerate(segments):
        for cell in _bucket(i, min(ax, bx) - margin, min(ay, by) - margin, max(ax, bx) + margin, max(ay, by) + margin):
            for j in grid.get(cell, ()):
                cx0, cy0, cx1, cy1 = segments[j]
                if (min(ax, bx) - margin <= max(cx0, cx1) and min(cx0, cx1) <= max(ax, bx) + margin
                        and min(ay, by) - margin <= max(cy0, cy1) and min(cy0, cy1) <= max(ay, by) + margin):
                    parent[find(i)] = find(j)
            grid.setdefault(cell, []).append(i)
    groups = {}
    for i, (ax, ay, bx, by) in enumerate(segments):
        groups.setdefault(find(i), []).extend(((ax, ay), (bx, by)))
    return list(groups.values())


def outline_regions(segments, margin=3.0, min_area=100.0, limit=6):
    """Convex outlines of the buildings drawn above a podium plan (walls of the floor above),
    largest first: the parts of a parking deck that belong to each tower."""
    hulls = []
    for points in _clusters(segments, margin):
        hull = _hull(points)
        if len(hull) >= 3 and _polygon_area(hull) >= min_area:
            hulls.append(hull)
    hulls.sort(key=lambda h: -_polygon_area(h))
    return hulls[:limit]


def _clip(polygon, x0, y0, x1, y1):
    """A convex polygon clipped to a rectangle (Sutherland-Hodgman)."""
    def cut(points, inside, cross):
        out = []
        for i, current in enumerate(points):
            previous = points[i - 1]
            if inside(current):
                if not inside(previous):
                    out.append(cross(previous, current))
                out.append(current)
            elif inside(previous):
                out.append(cross(previous, current))
        return out

    def at_x(x):
        return lambda p, q: (x, p[1] + (q[1] - p[1]) * (x - p[0]) / ((q[0] - p[0]) or 1e-12))

    def at_y(y):
        return lambda p, q: (p[0] + (q[0] - p[0]) * (y - p[1]) / ((q[1] - p[1]) or 1e-12), y)
    points = list(polygon)
    for inside, cross in ((lambda p: p[0] >= x0, at_x(x0)), (lambda p: p[0] <= x1, at_x(x1)),
                          (lambda p: p[1] >= y0, at_y(y0)), (lambda p: p[1] <= y1, at_y(y1))):
        if not points:
            break
        points = cut(points, inside, cross)
    return points


def area_within(rects, polygon):
    """Area of floor rectangles [x0, y0, x1, y1] inside a convex polygon."""
    total = 0.0
    for x0, y0, x1, y1 in rects:
        clipped = _clip(polygon, x0, y0, x1, y1)
        if len(clipped) >= 3:
            total += _polygon_area(clipped)
    return total


def cluster_areas(segments, margin=0.3, min_area=0.5, max_area=80.0):
    """Hull areas of groups of lines that lie together (an opening's cross, a stair's treads)."""
    areas = []
    for points in _clusters(segments, margin):
        hull = _hull(points)
        area = _polygon_area(hull) if len(hull) >= 3 else 0.0
        if min_area <= area <= max_area:
            areas.append(round(area, 2))
    return sorted(areas, reverse=True)


def _inside_convex(polygon, x, y):
    sign = 0
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if cross:
            if sign and (cross > 0) != (sign > 0):
                return False
            sign = cross
    return True


def _region_grid(regions, minx, miny, cell, shape):
    """int8 grid: 1 + index of the convex region a cell centre lies in, 0 outside all of them."""
    grid = np.zeros(shape, dtype=np.int8)
    for index, polygon in enumerate(regions or []):
        if len(polygon) < 3:
            continue
        ys = [p[1] for p in polygon]
        row0 = max(0, int((min(ys) - miny) / cell))
        row1 = min(shape[0] - 1, int((max(ys) - miny) / cell) + 1)
        edges = list(zip(polygon, polygon[1:] + polygon[:1]))
        for row in range(row0, row1 + 1):
            y = miny + (row + 0.5) * cell
            xs = []
            for (x1, y1), (x2, y2) in edges:
                if (y1 <= y < y2) or (y2 <= y < y1):
                    xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
            if len(xs) >= 2:
                c0 = max(0, int((min(xs) - minx) / cell + 0.5))
                c1 = min(shape[1], int((max(xs) - minx) / cell + 0.5))
                if c1 > c0:
                    segment = grid[row, c0:c1]
                    segment[segment == 0] = index + 1
    return grid


END_LINE_MAX = 0.35       # a wall's end line: no longer than this, between two longer lines it meets square


def end_lines(segments):
    """Indices of the wall end lines: short lines whose two ends each meet a longer line at
    about a right angle (the closing line of a drawn wall rectangle, a door jamb's return)."""
    grid, out = {}, set()
    for i, (ax, ay, bx, by) in enumerate(segments):
        for cell in _bucket(i, min(ax, bx) - JOIN_TOL, min(ay, by) - JOIN_TOL, max(ax, bx) + JOIN_TOL, max(ay, by) + JOIN_TOL):
            grid.setdefault(cell, []).append(i)
    for i, (ax, ay, bx, by) in enumerate(segments):
        length = math.hypot(bx - ax, by - ay)
        if not 0.03 <= length <= END_LINE_MAX:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        met = 0
        for px, py in ((ax, ay), (bx, by)):
            for j in grid.get((int(math.floor(px)), int(math.floor(py))), ()):
                cx, cy, dx, dy = segments[j]
                other = math.hypot(dx - cx, dy - cy)
                if j == i or other <= length + 1e-6 or _point_segment_distance(px, py, segments[j]) > JOIN_TOL:
                    continue
                if abs(ux * (dx - cx) / other + uy * (dy - cy) / other) < 0.2:
                    met += 1
                    break
        if met == 2:
            out.add(i)
    return out


def measure_walls(segments, is_outside=None, probe=0.5, regions=None):
    """Plan area of the cut concrete walls and the length of their faces (all / outside).

    The wall lines are drawn on a fine grid; open wall ends are closed first. Regions enclosed
    by lines that are thinner than about 0.9 m are wall cross-sections; wider ones are rooms or
    the outside. A face is a wall line with wall on one side; it is an outside face when the
    other side is open to the outside.
    """
    if not segments:
        return {"area": 0.0, "faces": 0.0, "outside_faces": 0.0, "caps": 0, "cell": BASE_CELL}
    caps = wall_caps(segments)
    lines = list(segments) + caps
    xs = [v for s in lines for v in (s[0], s[2])]
    ys = [v for s in lines for v in (s[1], s[3])]
    minx, miny = min(xs) - 0.6, min(ys) - 0.6
    width, height = max(xs) + 0.6 - minx, max(ys) + 0.6 - miny
    cell = max(BASE_CELL, math.sqrt(width * height / MAX_CELLS))
    shape = (int(height / cell) + 1, int(width / cell) + 1)
    drawn = _raster(lines, minx, miny, cell, shape)
    solid = drawn
    for _ in range(GROW):
        solid = _dil8(solid)
    empty = _not(solid)
    border = np.zeros(shape, dtype=bool)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    outside = _fill(_and(border, empty), empty)
    inner = _and(empty, _not(outside))
    seed = inner
    for _ in range(max(1, int(THIN_HALF_WIDTH / cell))):
        seed = _erode4(seed)
    rooms = _fill(seed, inner)
    thin = _and(inner, _not(rooms))
    band = thin
    for _ in range(GROW):
        band = _and(_dil8(band), _or(solid, band))
    centre = _and(drawn, _dil8(band), _not(band))
    area = (int(band.sum()) + 0.5 * int(centre.sum())) * cell * cell
    labels = _region_grid(regions, minx, miny, cell, shape) if regions else None
    by_region = [{"area": 0.0, "faces": 0.0, "outside_faces": 0.0} for _ in (regions or [])]
    for index, part in enumerate(by_region):
        inside = np.equal(labels, index + 1)
        part["area"] = (int(np.logical_and(band, inside).sum()) + 0.5 * int(np.logical_and(centre, inside).sum())) * cell * cell

    # faces: sample each line, look a little to both sides
    faces = outside_faces = end_faces = 0.0
    ends = end_lines(segments) | set(range(len(segments), len(lines)))
    outer = []
    reach = (GROW + 2) * cell
    for index_line, (ax, ay, bx, by) in enumerate(lines):
        length = math.hypot(bx - ax, by - ay)
        if length < 0.03:
            continue
        n = max(2, int(length / 0.1))
        t = (np.arange(n) + 0.5) / n
        px, py = ax + (bx - ax) * t, ay + (by - ay) * t
        nx, ny = -(by - ay) / length * reach, (bx - ax) / length * reach
        sides = []
        for sign in (1, -1):
            gx = np.clip(((px + sign * nx - minx) / cell).astype(int), 0, shape[1] - 1)
            gy = np.clip(((py + sign * ny - miny) / cell).astype(int), 0, shape[0] - 1)
            sides.append(band[gy, gx])
        wall_a, wall_b = sides
        is_face = np.not_equal(wall_a, wall_b)
        hits = int(is_face.sum())
        if not hits:
            continue
        faces += length * hits / n
        if index_line in ends:
            end_faces += length * hits / n
        if labels is not None:
            gx = np.clip(((px - minx) / cell).astype(int), 0, shape[1] - 1)
            gy = np.clip(((py - miny) / cell).astype(int), 0, shape[0] - 1)
            owner = labels[gy, gx]
            for index, part in enumerate(by_region):
                part["faces"] += length * int(np.logical_and(is_face, np.equal(owner, index + 1)).sum()) / n
        if is_outside is None:
            continue
        scale = probe / reach
        out = 0
        for k in np.nonzero(is_face)[0]:
            sign = -1.0 if wall_a[k] else 1.0  # step towards the open side
            if is_outside(float(px[k] + sign * nx * scale), float(py[k] + sign * ny * scale)):
                out += 1
        outside_faces += length * out / n
        if out >= 0.6 * n and length >= 0.15:
            wall_side = 1.0 if wall_a.sum() >= wall_b.sum() else -1.0
            if bx < ax - 1e-9 or (abs(bx - ax) <= 1e-9 and by < ay):  # one direction per line, so each gap is met once
                outer.append((bx, by, ax, ay, -wall_side))
            else:
                outer.append((ax, ay, bx, by, wall_side))
    gaps = _window_gaps(outer, band, minx, miny, cell, is_outside, probe) if is_outside else []
    return {"area": round(area, 3), "faces": round(faces, 2), "outside_faces": round(outside_faces, 2),
            "end_faces": round(end_faces, 2), "caps": len(caps), "cell": round(cell, 3),
            "window_gaps": round(sum(g[0] for g in gaps), 2), "window_gap_count": len(gaps),
            "window_gap_area": round(sum(g[0] * g[1] for g in gaps), 3),
            **({"regions": [{k: round(v, 3) for k, v in part.items()} for part in by_region]} if regions else {})}


GAP_MIN, GAP_MAX = 0.3, 4.0
WALL_THICKNESS_DEFAULT = 0.2


def _wall_depth(band, minx, miny, cell, x, y, ux, uy):
    """Thickness of the wall behind an outer face point, walking inward through wall cells."""
    shape = band.shape
    depth = 0
    for step in range(1, int(1.0 / cell)):
        gx, gy = int((x + ux * step * cell - minx) / cell), int((y + uy * step * cell - miny) / cell)
        if not (0 <= gx < shape[1] and 0 <= gy < shape[0]):
            break
        if band[gy, gx]:
            depth = step
        elif depth:
            break
    return (depth + 1) * cell if depth else WALL_THICKNESS_DEFAULT  # the face line cell is half wall


def _window_gaps(outer, band, minx, miny, cell, is_outside, probe):
    """Openings in the outer walls (windows, balcony sashes): two outer faces on one line whose
    ends stop 0.3-4 m apart with no wall between, the gap lying on the building edge.
    Returns [(length, wall thickness)]."""
    found, seen = [], set()
    for i, (ax, ay, bx, by, side) in enumerate(outer):
        length = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / length, (by - ay) / length
        nx, ny = -uy * side, ux * side  # towards the wall
        t1 = 0.0
        best = None
        for j, (cx, cy, dx, dy, _side) in enumerate(outer):
            if j == i:
                continue
            if abs((cx - ax) * -uy + (cy - ay) * ux) > 0.06 or abs((dx - ax) * -uy + (dy - ay) * ux) > 0.06:
                continue  # not on the same line
            s1, s2 = sorted(((cx - ax) * ux + (cy - ay) * uy, (dx - ax) * ux + (dy - ay) * uy))
            gap = s1 - length
            if GAP_MIN <= gap <= GAP_MAX and (best is None or gap < best[0]):
                best = (gap, j, s1)
        if best is None:
            continue
        gap, j, s1 = best
        key = (min(i, j), max(i, j))
        if key in seen:
            continue
        mx, my = ax + ux * (length + gap / 2), ay + uy * (length + gap / 2)
        if not is_outside(mx - nx * probe, my - ny * probe) or is_outside(mx + nx * probe, my + ny * probe):
            continue  # not on the building edge
        n = max(2, int(gap / cell))
        blocked = 0
        for k in range(1, n):
            px, py = ax + ux * (length + gap * k / n), ay + uy * (length + gap * k / n)
            gx, gy = int((px + nx * 2 * cell - minx) / cell), int((py + ny * 2 * cell - miny) / cell)
            if 0 <= gx < band.shape[1] and 0 <= gy < band.shape[0] and band[gy, gx]:
                blocked += 1
        if blocked > 0.2 * n:
            continue  # a wall runs across: not an opening
        seen.add(key)
        depth_a = _wall_depth(band, minx, miny, cell, bx - ux * 0.1, by - uy * 0.1, nx, ny)
        cx, cy = ax + ux * (s1 + 0.1), ay + uy * (s1 + 0.1)
        depth_b = _wall_depth(band, minx, miny, cell, cx, cy, nx, ny)
        found.append((gap, (depth_a + depth_b) / 2))
    return found


def measure_plan(walls, cols, opens, stairs, is_outside=None, above=(), slab_rects=()):
    """Takeoff measures of one plan frame. walls/opens/stairs/above: polylines; cols: closed outlines.

    ``above`` are the walls of the floor above drawn on a podium plan; their outlines split the
    plan into tower parts (``regions``: wall section, faces, columns and slab inside each)."""
    regions = outline_regions(_segments_of(above)) if above else []
    measured = measure_walls(_segments_of(walls), is_outside, regions=regions or None)
    if regions:
        for polygon, part in zip(regions, measured["regions"]):
            inside = [c for c in cols if len(c) >= 3 and _inside_convex(polygon, sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c))]
            part.update(polygon=[[round(x, 2), round(y, 2)] for x, y in polygon], hull_area=round(_polygon_area(polygon), 1),
                        col_area=round(sum(_polygon_area(c) for c in inside), 3),
                        col_perimeter=round(sum(math.hypot(c[i][0] - c[i - 1][0], c[i][1] - c[i - 1][1]) for c in inside for i in range(len(c))), 2),
                        slab_area=round(area_within(slab_rects, polygon), 1))
    col_area = sum(_polygon_area(c) for c in cols if len(c) >= 3)
    col_perimeter = sum(math.hypot(c[i][0] - c[i - 1][0], c[i][1] - c[i - 1][1]) for c in cols for i in range(len(c)))
    measured.update(
        cols=len(cols), col_area=round(col_area, 3), col_perimeter=round(col_perimeter, 2),
        openings=cluster_areas(_segments_of(opens), margin=0.05, min_area=0.3, max_area=60.0),
        stairs=cluster_areas(_segments_of(stairs), margin=0.4, min_area=4.0, max_area=80.0),
    )
    return measured


# ── rows by the floor rule ─────────────────────────────────────────────────

SLAB_THICKNESS = 0.21      # assumed; slab schedules are rarely readable from the plans
BEAM_DEPTH_DEFAULT = 0.6   # beams whose schedule gives no depth ("180 X 건축치수", unlabelled runs)
# a beam-framed plan labels nearly all its beam runs (unmarked ≤ about 20 % on the check projects);
# past half unmarked, the "beam" lines are a wall-type floor's dashed walls below and slab edges
UNLABELLED_BEAM_SHARE_MAX = 0.5
WINDOW_HEIGHT = 1.8        # assumed opening height; the wall above and below an outer opening is concrete
# stairs per m² of stair hall in plan: 150 mm waist slab and landings plus 178 x 260 mm steps
STAIR_CONCRETE_PER_M2 = 0.2
STAIR_FORM_PER_M2 = 1.4
FORM_KEYS = ("al_vertical", "al_horizontal", "gang", "euro", "plywood", "stair")
FORM_LABELS = {"al_vertical": "AL폼 수직", "al_horizontal": "AL폼 수평", "gang": "갱폼", "euro": "유로폼", "plywood": "합판",
               "stair": "계단"}
CONCRETE_KEYS = ("foundation", "wall", "column", "beam", "slab", "stair")
CONCRETE_LABELS = {"foundation": "기초", "wall": "벽", "column": "기둥", "beam": "보", "slab": "슬래브", "stair": "계단"}
FOUNDATION_DEFAULT_M = {"tower": 1.0, "parking": 0.6}  # assumed until the drawing or a person gives it


def _size(text):
    """(b, h) in metres from a schedule size "500 X 1,300"; h is None for "180 X 건축치수"."""
    numbers = [int(v.replace(",", "")) for v in re.findall(r"\d[\d,]*", str(text or ""))]
    numbers = [v for v in numbers if 50 <= v <= 5000]
    if len(numbers) >= 2:
        b, h = sorted(numbers[:2])
        return b / 1000.0, h / 1000.0
    if len(numbers) == 1:
        return numbers[0] / 1000.0, None
    return None, None


def _schedule_sizes(links):
    """{(category, mark): (b, h)} from the CAD schedule rows linked to each mark."""
    sizes = {}
    rows = (links or {}).get("schedule_rows") or []
    for item in (links or {}).get("marks") or []:
        for spec in item.get("specs") or []:
            index = spec.get("rows")
            table = rows[index] if isinstance(index, int) and 0 <= index < len(rows) else []
            for row in table:
                b, h = _size((row.get("values") or {}).get("size"))
                if b:
                    sizes[(item.get("category"), item.get("mark"))] = (b, h)
                    break
            if (item.get("category"), item.get("mark")) in sizes:
                break
    return sizes


def _link_members(links, building, sheet):
    """Beams and columns placed on one plan sheet of one building, from member_links."""
    plans = (links or {}).get("plans") or []
    wanted = {i for i, p in enumerate(plans) if p.get("drawing_number") == sheet and p.get("kind") == "floor_plan"
              and (p.get("section") or "") in (building or "")}
    if not wanted:
        return None
    sizes = _schedule_sizes(links)
    # a plan whose "beam" lines are mostly unmarked is a wall-type floor: its dashed walls below and
    # slab edges are no beams, and a marked run there without a schedule depth (a lintel or link
    # beam label, "LB0") is measured along those same lines (a beam-framed floor marks nearly
    # every run)
    wall_type = set()
    for index in wanted:
        family = (plans[index].get("geometry") or {}).get("beams") or {}
        measured = float(family.get("measured_mm") or 0)
        if measured and float((family.get("unlabelled") or {}).get("length_mm") or 0) > UNLABELLED_BEAM_SHARE_MAX * measured:
            wall_type.add(index)
    beams, columns = [], []
    left_out = 0.0
    for item in links.get("marks") or []:
        category, mark = item.get("category"), item.get("mark")
        for occurrence in item.get("occurrences") or []:
            if occurrence.get("plan") not in wanted:
                continue
            if category == "beams":
                measured = occurrence.get("measured") or {}
                b, h = sizes.get((category, mark), (None, None))
                for width, length in (measured.get("widths") or {}).items():
                    if h is None and occurrence.get("plan") in wall_type:
                        left_out += length / 1000.0
                        continue
                    beams.append({"mark": mark, "length": length / 1000.0, "b": b or int(width) / 1000.0, "h": h})
            elif category == "columns":
                b, h = sizes.get((category, mark), (None, None))
                if b and h:
                    columns.append({"mark": mark, "count": occurrence.get("count") or 0, "b": b, "h": h})
    for index in wanted:
        unlabelled = ((plans[index].get("geometry") or {}).get("beams") or {}).get("unlabelled") or {}
        if index in wall_type:
            left_out += float(unlabelled.get("length_mm") or 0) / 1000.0
            continue
        for width, length in (unlabelled.get("widths") or {}).items():
            beams.append({"mark": None, "length": length / 1000.0, "b": int(width) / 1000.0, "h": None})
    return {"beams": beams, "columns": columns, "left_out_unlabelled_m": round(left_out, 1)}


def _net_slab(plan):
    """Closed slab area of a plan less its openings and stair halls, or None when the outline is open."""
    if not plan or plan.get("slab_approximate") or not plan.get("slab_area"):
        return None
    measure = plan.get("measure") or {}
    return max(float(plan["slab_area"]) - sum(measure.get("openings") or []) - sum(measure.get("stairs") or []), 0.0)


def _round(values, digits):
    return {k: round(v, digits) for k, v in values.items()}


def _storey_kind(key):
    if key.startswith("B"):
        return "basement"
    if key in ("R", "PH", "PHR"):
        return "roof"
    return "ground"


def _plan_at(building, level):
    plans = building.get("plans") or []
    index = (level or {}).get("plan")
    return plans[index] if isinstance(index, int) and 0 <= index < len(plans) else None


def tower_regions(buildings):
    """(podium building, {tower name: region index}): the tower outlines drawn on the podium's
    basement plans matched to the towers by outline area (each tower's ground-floor plan)."""
    host = sample = None
    for building in buildings:
        if any((l.get("key") or "").isdigit() for l in building.get("levels") or []):
            continue  # a podium has basement levels and a roof only
        for plan in building.get("plans") or []:
            regions = (plan.get("measure") or {}).get("regions")
            if regions:
                host, sample = building, regions
                break
        if host:
            break
    if not host:
        return None, {}
    towers = []
    for building in buildings:
        levels = building.get("levels") or []
        if building is host or not any((l.get("key") or "").startswith("B") for l in levels):
            continue
        ground = next((l for l in levels if l.get("key") == "1"), None) or next((l for l in levels if (l.get("key") or "").isdigit()), None)
        plan = _plan_at(building, ground)
        if plan and plan.get("hull_area"):
            towers.append((building.get("name"), float(plan["hull_area"])))
    pairs = sorted((abs(math.log(float(region["hull_area"]) / hull)), name, i)
                   for name, hull in towers for i, region in enumerate(sample) if region.get("hull_area"))
    match, used = {}, set()
    for score, name, i in pairs:
        if score <= math.log(1.6) and name not in match and i not in used:
            match[name] = i
            used.add(i)
    return host.get("name"), match


def _ground_slab(building):
    """Closed slab area of a tower's lowest above-ground plan: caps the slab of its podium part."""
    levels = building.get("levels") or []
    ground = next((l for l in levels if l.get("key") == "1"), None) or next((l for l in levels if (l.get("key") or "").isdigit()), None)
    plan = _plan_at(building, ground)
    return _net_slab(plan) if plan else None


def _level_prefix(key):
    """Mark prefix of a level in slab schedules: B1 → "-1", R → "R", 3 → "3"."""
    if key.startswith("B") and key[1:].isdigit():
        return "-" + key[1:]
    return key if key in ("R", "PH") or key.isdigit() else ""


def slab_thickness_for(links, building, sheet, level_key):
    """(thickness m, note) of the slab drawn on one plan: the slab marks placed on it looked up in
    the slab schedules (plain or with the level prefix, "S2" on B1 → "-1S2"), weighted by count."""
    table = ((links or {}).get("slab_thickness") or {}).get("marks") or {}
    if not table:
        return None
    plans = links.get("plans") or []
    wanted = {i for i, p in enumerate(plans) if p.get("drawing_number") == sheet and p.get("kind") == "floor_plan"
              and (p.get("section") or "") in (building or "")}
    prefix = _level_prefix(level_key or "")
    found = []
    for item in links.get("marks") or []:
        if item.get("category") != "slabs":
            continue
        mark = str(item.get("mark") or "").upper()
        mm = table.get(mark) or (table.get(prefix + mark) if prefix else None)
        count = sum(o.get("count") or 0 for o in item.get("occurrences") or [] if o.get("plan") in wanted)
        if mm and count:
            found.append((mark if mark in table else prefix + mark, mm, count))
    if not found:
        return None
    total = sum(c for _m, _mm, c in found)
    thickness = sum(mm * c for _m, mm, c in found) / total / 1000.0
    return round(thickness, 3), "슬래브 두께 {:.0f}mm (일람표 {})".format(thickness * 1000, "·".join(sorted({m for m, _mm, _c in found})))


def _region(plan, index):
    regions = ((plan or {}).get("measure") or {}).get("regions") or []
    return regions[index] if index is not None and 0 <= index < len(regions) else None


def foundation_specs(links, building):
    """(thickness m or None, rebar text or None) read from the foundation plan of a building."""
    thickness = rebar = None
    for plan in (links or {}).get("plans") or []:
        if "FDN" not in (plan.get("levels") or []) or not ((plan.get("section") or "") in (building or "") or
                                                            (building or "") in (plan.get("section") or "")):
            continue
        for spec in plan.get("specs") or []:
            text = str(spec.get("text") or "")
            if spec.get("kind") == "thickness" and thickness is None:
                found = re.search(r"THK\.?\s*(\d{3,4})|T\s*=\s*(\d{3,4})", text, re.I)
                if found:
                    thickness = int(found.group(1) or found.group(2)) / 1000.0
                else:  # a mat mark carrying its thickness: "101MAT_1.0"
                    found = re.search(r"MAT[_\-\s]?(\d\.\d{1,2})(?![\d.])", text, re.I)
                    if found and 0.3 <= float(found.group(1)) <= 5.0:
                        thickness = float(found.group(1))
            if spec.get("kind") == "rebar" and rebar is None and re.search(r"T\s*/\s*B|EW", text, re.I):
                rebar = text
    if rebar is None:
        rebar = _top_and_bottom(links, building)
    return thickness, rebar


_SPACED_BAR = re.compile(r"[A-Z]{0,3}D\d{2}\s*@\s*\d{2,4}", re.I)


def _top_and_bottom(links, building):
    """A mat's bars noted apart on its plan ("UHD22@300(상부근)", "UHD22@300(하부근)"): the most
    noted of each, as one text "UHD22@300(상부근) + UHD22@300(하부근)"; local strengthening
    (보강근) is not the mat's run."""
    tops, bottoms = Counter(), Counter()
    for plan in (links or {}).get("plans") or []:
        if "FDN" not in (plan.get("levels") or []) or not ((plan.get("section") or "") in (building or "") or
                                                            (building or "") in (plan.get("section") or "")):
            continue
        for spec in plan.get("specs") or []:
            text = str(spec.get("text") or "")
            found = _SPACED_BAR.search(text)
            if spec.get("kind") != "rebar" or not found or re.search(r"보강|추가|ADD", text, re.I):
                continue
            bar = re.sub(r"\s+", "", found.group(0)).upper()
            if re.search(r"상부|상단|TOP", text, re.I):
                tops[bar] += int(spec.get("count") or 1)
            elif re.search(r"하부|하단|BOT", text, re.I):
                bottoms[bar] += int(spec.get("count") or 1)
    if not tops or not bottoms:
        return None
    return f"{tops.most_common(1)[0][0]}(상부근) + {bottoms.most_common(1)[0][0]}(하부근)"


# ── foundation schedule rows: isolated footings, mats, wall footings ──

BAR_KEYS = ("top_bar", "bottom_bar", "extra_bar", "dist_bar", "rebar")
WALL_T_DEFAULT = 0.3  # m, the wall standing on a wall footing when the plan gives no thickness


def _value_m(values, *keys):
    """The first millimetre value among ``keys`` of a schedule row ("2,000", "B=1200"), in metres."""
    for key in keys:
        found = re.search(r"\d+(?:\.\d+)?", str((values or {}).get(key) or "").replace(",", ""))
        if found and 50 <= float(found.group()) <= 10000:
            return float(found.group()) / 1000.0
    return None


def bar_texts(values):
    """{column key: text} of the bar columns of a foundation schedule row that name a bar."""
    return {k: str(v) for k, v in (values or {}).items()
            if isinstance(k, str) and k.startswith(BAR_KEYS) and re.search(r"D\d{2}", str(v or ""), re.I)}


def footing_kind(mark, values):
    """"isolated" (Lx × Ly), "wall" (a WF mark with a width and a depth), "mat" (MAT / MF / FS:
    a mat or a foundation slab) or None."""
    if _value_m(values, "length_x") and _value_m(values, "length_y"):
        return "isolated"
    key = str(mark or "").upper()
    if key.startswith("WF"):
        b, h = _size((values or {}).get("size"))
        width = _value_m(values, "width", "length_x") or b
        depth = _value_m(values, "depth", "thickness") or h
        return "wall" if width and depth else None
    if "MAT" in key or key.startswith(("MF", "FS")):
        return "mat"
    return None


def _foundation_marks(links):
    """[(mark item, first schedule row values, schedule area)] of the foundation marks with a schedule row."""
    pool = (links or {}).get("schedule_rows") or []
    plans = (links or {}).get("plans") or []
    out, seen = [], set()
    for item in (links or {}).get("marks") or []:
        if item.get("category") != "foundations" or item.get("mark") in seen:
            continue
        for spec in item.get("specs") or []:
            index = spec.get("rows")
            rows = pool[index] if isinstance(index, int) and 0 <= index < len(pool) else []
            if rows:
                where = spec.get("plan")
                area = plans[where].get("area") if isinstance(where, int) and 0 <= where < len(plans) else None
                out.append((item, rows[0].get("values") or {}, area))
                seen.add(item.get("mark"))
                break
    return out


def _foundation_plans(links, building):
    plans = (links or {}).get("plans") or []
    return {i for i, p in enumerate(plans) if "FDN" in (p.get("levels") or []) and
            ((p.get("section") or "") in (building or "") or (building or "") in (p.get("section") or ""))}


def _placed(item, wanted):
    return sum(o.get("count") or 0 for o in item.get("occurrences") or [] if o.get("plan") in wanted)


def _natural(mark):
    return re.sub(r"\d+", lambda m: m.group().zfill(6), str(mark or ""))


def footings(links, building):
    """[(mark, count, length x m, length y m, depth m, values)] of the isolated footings marked on the
    building's foundation plans, sized by their schedule rows."""
    wanted = _foundation_plans(links, building)
    out = []
    for item, values, _area in _foundation_marks(links):
        count = _placed(item, wanted)
        lx, ly, depth = _value_m(values, "length_x"), _value_m(values, "length_y"), _value_m(values, "depth", "thickness")
        if count and lx and ly and depth:
            out.append((item.get("mark"), count, lx, ly, depth, values))
    return out


def mat_parts(links, building, kind):
    """(mats, placed, note) of a building's foundation from the foundation schedule: the mat marks
    labelled on its foundation plans (shared by label count), else those naming its number
    ("101MF1"), else the only mat of its kind (tower or parking) in the schedules. Each mat:
    {"mark", "share", "thickness" m or None, "bars" {column: text}}."""
    marks = [(item, values, area) for item, values, area in _foundation_marks(links)
             if footing_kind(item.get("mark"), values) == "mat"]
    if not marks:
        return [], False, None
    wanted = _foundation_plans(links, building)
    chosen = [(item, values, _placed(item, wanted)) for item, values, _area in marks]
    chosen = [c for c in chosen if c[2]]
    placed, note = bool(chosen), None
    if not chosen:
        number = re.search(r"\d{2,4}", building or "")
        chosen = [(item, values, 1) for item, values, _area in marks if number and number.group() in str(item.get("mark"))]
    if not chosen:
        home = "parking" if kind == "parking" else "building"
        pool = [(item, values, 1) for item, values, area in marks
                if (area or ("parking" if str(item.get("mark")).upper().startswith("FS") else "building")) == home]
        if len(pool) == 1:
            chosen = pool
        elif pool:
            note = ("기초 일람표의 매트 부호(" + ", ".join(sorted((i.get("mark") for i, _v, _w in pool), key=_natural))
                    + ")가 이 동 기초 평면에 표기되지 않아 어느 것인지 몰라 쓰지 않았어요")
    total = sum(w for _i, _v, w in chosen) or 1
    mats = [{"mark": item.get("mark"), "share": round(w / total, 4), "thickness": _value_m(values, "thickness", "depth"),
             "bars": bar_texts(values)} for item, values, w in sorted(chosen, key=lambda c: _natural(c[0].get("mark")))]
    return mats, placed, note


def wall_footing_parts(links, building, kind):
    """(wall footings, note) under a building's outer walls: the WF marks labelled on its foundation
    plans (shared by label count), else, for a parking deck when no plan labels any WF, the first
    WF of the schedules (WF marks are often left off the plan). Each: {"mark", "share", "width",
    "depth", "bars", "placed"}."""
    marks = [(item, values, area) for item, values, area in _foundation_marks(links)
             if footing_kind(item.get("mark"), values) == "wall"]
    if not marks:
        return [], None
    wanted = _foundation_plans(links, building)
    chosen = [(item, values, _placed(item, wanted)) for item, values, _area in marks]
    chosen = [c for c in chosen if c[2]]
    note = None
    if not chosen:
        labelled_anywhere = any(o.get("count") for item, _v, _a in marks for o in item.get("occurrences") or [])
        if kind != "parking" or labelled_anywhere:
            return [], None
        pool = sorted([(item, values) for item, values, area in marks if area != "building"], key=lambda c: _natural(c[0].get("mark")))
        if not pool:
            return [], None
        chosen = [(pool[0][0], pool[0][1], 0)]
        if len(pool) > 1:
            note = (f"줄기초 위치가 평면에 없어 {pool[0][0].get('mark')}로 봤어요 (일람표의 "
                    + ", ".join(i.get("mark") for i, _v in pool[1:]) + "도 있어요)")
    total = sum(c[2] for c in chosen) or 1
    parts = []
    for item, values, count in chosen:
        b, h = _size(values.get("size"))
        parts.append({"mark": item.get("mark"), "share": round((count / total) if count else 1.0, 4),
                      "width": _value_m(values, "width", "length_x") or b, "depth": _value_m(values, "depth", "thickness") or h,
                      "bars": bar_texts(values), "placed": count})
    return parts, note


def _outer_walls(plan, area):
    """(length m of the outer wall faces of a plan, approximated?) — the outline when the faces are unread."""
    measure = (plan or {}).get("measure") or {}
    outside = float(measure.get("outside_faces") or 0)
    if outside > 0:
        return outside, False
    polygon = (plan or {}).get("slab_polygon") or []
    if len(polygon) >= 3:
        return sum(math.hypot(polygon[i][0] - polygon[i - 1][0], polygon[i][1] - polygon[i - 1][1]) for i in range(len(polygon))), True
    return float(measure.get("outline") or 0) or 4 * math.sqrt(max(area, 0.0)), True


def _foundation_row(building, plans, levels, host_name, host, match, links, given, learned=None, assumed=None, overrides=None):
    """The foundation storey: the lowest floor's outline × the mat thickness (the drawing's, a
    person's, else an assumption kept out of the totals)."""
    if not levels:
        return None
    name = building.get("name")
    lowest = levels[0]
    plan = plans[lowest["plan"]] if isinstance(lowest.get("plan"), int) and lowest["plan"] < len(plans) else {}
    notes = []
    area = None
    if match and name in match and host is not None:
        host_lowest = next((l for l in host.get("levels") or [] if l.get("key") == lowest.get("key")), None)
        region = _region(_plan_at(host, host_lowest), match[name]) if host_lowest else None
        if region and region.get("slab_area"):
            area = float(region["slab_area"])
            notes.append(f"{host_name} 평면에서 이 동 영역을 잘라 기초 면적으로 봤어요")
    if area is None:
        area = _net_slab(plan)
        if area is None:
            area = float(plan.get("hull_area") or plan.get("slab_area") or 0)
            if area:
                notes.append("맨 아래층 외곽이 열려 있어 바깥 둘레로 근사했어요")
        if match and name == host_name:
            for tower, i in match.items():
                part = _region(plan, i)
                area -= float((part or {}).get("slab_area") or 0)
            area = max(area, 0.0)
    if area <= 0:
        return None
    drawn, _rebar = foundation_specs(links, name)
    kind = "parking" if name == "지하주차장" or not any((l.get("key") or "").isdigit() for l in levels) else "tower"
    usual = (learned or {}).get(f"foundation:{kind}")  # the median of what people entered on other jobs
    mats, mats_placed, mat_note = mat_parts(links, name, kind) if links else ([], False, None)
    weighed = [(m["share"], m["thickness"]) for m in mats if m["thickness"] and 0.1 <= m["thickness"] <= 5.0]
    listed = (sum(w * t for w, t in weighed) / sum(w for w, _t in weighed)) if weighed else None
    # a mat labelled on this building's plan beats the plan's own text; a schedule mat found
    # otherwise (by its number, or the only one) comes after it
    candidates = [(given, "person"), (listed if mats_placed else None, "schedule"), (drawn, "drawing"),
                  (None if mats_placed else listed, "schedule"), (usual, "learned"), (FOUNDATION_DEFAULT_M[kind], "assumed")]
    thickness, source = next((value, why) for value, why in candidates if value)
    thickness = round(thickness, 3)
    perimeter = float((plan.get("measure") or {}).get("outline") or 0) or 4 * math.sqrt(area)
    concrete = dict.fromkeys(CONCRETE_KEYS, 0.0)
    concrete["foundation"] = area * thickness
    form = dict.fromkeys(FORM_KEYS, 0.0)
    standalone = not match or (name not in match and name != host_name)
    if standalone:
        form["plywood"] = perimeter * thickness  # the mat edge; mats inside a parking deck run on into it
    else:
        notes.append("주차장 기초와 이어진 매트라 옆 거푸집은 넣지 않았어요")
    if mats:
        notes.append("기초 일람표 " + ", ".join(m["mark"] + (f" {m['thickness'] * 1000:,.0f}mm" if m["thickness"] else "")
                                           + (f" ({m['share']:.0%})" if len(mats) > 1 else "") for m in mats)
                     + (" — 기초 평면 표기 수로 나눴어요" if len(mats) > 1 and mats_placed else "")
                     + ("" if mats_placed else " (평면에 부호가 없어 일람표에서 골랐어요)"))
    elif mat_note:
        notes.append(mat_note)
    feet = footings(links, name)
    for mark, count, lx, ly, depth, _values in feet:
        concrete["foundation"] += count * lx * ly * depth  # footings are cast against the ground
    if feet:
        notes.append("독립기초 " + ", ".join(f"{m} {c}개({lx:.1f}×{ly:.1f}×{d:.2f}m)" for m, c, lx, ly, d, _v in feet) + " 포함")
    walls, wall_note = wall_footing_parts(links, name, kind) if links else ([], None)
    strips = []
    unlabelled = [part for part in walls if not part["placed"]]
    left_out_strips = unlabelled if (overrides or {}).get("wall_footing_place") is False else []
    if left_out_strips:  # a person said the unlabelled wall footing is not there
        walls = [part for part in walls if part["placed"]]
        wall_note = None
    if walls:
        length, rough = _outer_walls(plan, area)
        measure = plan.get("measure") or {}
        faces = float(measure.get("faces") or 0)
        wall_t = min(max(2 * float(measure.get("area") or 0) / faces, 0.15), 0.6) if faces else WALL_T_DEFAULT
        for part in walls:
            run = length * part["share"]
            inside = min(part["width"], (part["width"] + wall_t) / 2)  # under the mat outline, already counted
            concrete["foundation"] += max(run * (part["width"] * part["depth"] - inside * thickness), 0.0)
            strips.append(dict(part, length=round(run, 1)))
        notes.append("줄기초 " + ", ".join(f"{s['mark']} {s['width']:.2f}×{s['depth']:.2f}m × {s['length']:.0f}m" for s in strips)
                     + (" — 외벽 면 길이" if not rough else " — 외벽을 못 읽어 바깥 둘레로 근사")
                     + (", 평면 표기 수로 나눔" if len(strips) > 1 else "")
                     + ("" if any(s["placed"] for s in strips) else " (평면에 부호가 없어 외벽 아래로 봤어요 — 확인해 주세요)")
                     + ". 기초판과 겹치는 부분은 뺐고 옆 거푸집은 넣지 않았어요")
        if wall_note:
            notes.append(wall_note)
    if assumed is not None:
        if source in ("assumed", "learned"):
            assumed.add("foundation_thickness", "기초 두께", f"{thickness:.2f}m" + (" (가정)" if source == "assumed" else " (다른 현장 입력값 중앙값)"),
                        name, "기초", area * thickness, CONCRETE, "산출 화면의 기초 두께 칸에 넣으면 합계에 들어가요", counted=False)
        if mats and not mats_placed:
            assumed.add("mat_pick", "평면에 부호 없는 매트", "일람표 " + ", ".join(m["mark"] for m in mats), name, "기초",
                        change="기초 평면에 매트 부호가 있으면 그 부호를 써요")
        for strip in strips:
            if not strip["placed"]:
                piece = max(strip["length"] * (strip["width"] * strip["depth"] - min(strip["width"], (strip["width"] + wall_t) / 2) * thickness), 0.0)
                assumed.add("wall_footing_place", "줄기초 위치", f"{strip['mark']} {strip['width']:.2f}×{strip['depth']:.2f}m를 외벽 아래 "
                            f"{strip['length']:.0f}m" + (" (외벽을 못 읽어 바깥 둘레)" if rough else ""), name, "기초", piece, CONCRETE,
                            "평면에 줄기초 부호가 있으면 그 동에 넣어요. 위치가 다르면 2D 고치기로 바로잡아 주세요", raw=True)
        for part in left_out_strips:
            assumed.add("wall_footing_place", "줄기초 위치", f"{part['mark']} 뺌 (입력값)", name, "기초",
                        change="평면에 줄기초 부호가 있으면 그 동에는 그대로 넣어요", raw=False, person=True)
    notes.insert(0, f"기초 {area:.0f}㎡ × 두께 {thickness:.2f}m" + {"person": " (입력값)", "drawing": " (기초 평면 표기)",
                                                                  "schedule": " (기초 일람표)",
                                                                  "learned": " (다른 현장 입력값의 중앙값 — 확인해 주세요)",
                                                                  "assumed": " (가정값 — 두께를 확인해 주세요)"}[source])
    concrete["total"] = sum(concrete[k] for k in CONCRETE_KEYS)
    form["total"] = sum(form[k] for k in FORM_KEYS)
    return {"key": "FT", "label": "기초", "height": round(thickness, 3), "sheets": {"vertical": None, "horizontal": None},
            "concrete": _round(concrete, 2), "formwork": _round(form, 2),
            "measures": {"vertical_area": 0.0, "faces": 0.0, "outside_faces": 0.0, "slab_area": round(area, 1)},
            "approximate": source in ("assumed", "learned"), "notes": notes, "fixed": None,
            "foundation": {"area": round(area, 1), "thickness": thickness, "source": source, "perimeter": round(perimeter, 1),
                           "footings": [{"mark": m, "count": c, "x": lx, "y": ly, "depth": d, "bars": bar_texts(v)}
                                        for m, c, lx, ly, d, v in feet],
                           "mats": mats, "wall_footings": strips}}


SPARSE_WALL_SHARE = 0.02   # a tower floor whose wall section is under 2 % of its slab draws almost no walls
SPARSE_WALL_OF_USUAL = 0.3  # ... or under 30 % of what the building's other plans draw


def _flag_sparse_walls(name, levels, rows):
    """Mark the above-ground storeys of a tower whose plan draws almost no walls: some offices draw
    the walls of the typical floors only on the unit plans (단위세대). The rows keep their values;
    the note asks for the walls to be traced in the 2D fixing."""
    if not re.fullmatch(r"\d{2,4}동", name or ""):
        return
    by_key = {r["key"]: r for r in rows}
    shares = {}
    for level in levels:
        row = by_key.get(level.get("key"))
        if not row or _storey_kind(level.get("key") or "") != "ground" or not level.get("key", "").isdigit():
            continue
        measures = row.get("measures") or {}
        slab = float(measures.get("slab_area") or 0)
        if slab >= 100:
            shares.setdefault(level.get("plan"), (float(measures.get("vertical_area") or 0) / slab, []))[1].append(row)
    if not shares:
        return
    ratios = sorted(share for share, _rows in shares.values())
    usual = ratios[len(ratios) // 2] if len(ratios) >= 3 else None
    for share, plan_rows in shares.values():
        if share < SPARSE_WALL_SHARE or (usual and share < SPARSE_WALL_OF_USUAL * usual):
            for row in plan_rows:
                row["sparse_walls"] = True
                row["notes"].append(f"이 층 평면에는 벽 단면이 슬래브의 {share:.1%}뿐이에요 — 벽이 단위세대 도면에만 그려진 "
                                    "현장일 수 있어요. 빠진 벽은 2D 고치기로 따라 그려 주세요")


def _assumed_items(assumed, overrides):
    from .quantity_assumed import TAKEOFF_KEYS
    assumed.keep_unused(overrides, TAKEOFF_KEYS)
    return assumed.items()


def build_takeoff(model, links=None, slab_thickness=SLAB_THICKNESS, edits=None, foundations=None, learned=None, overrides=None,
                  opening_heights=None, excavation=None):
    """Rows per building and storey: walls/columns of the storey + slab/beams on top of it.
    ``edits``: people's fixes (quantity_edits.clean), added to the storeys using their plans.
    ``overrides``: values a person set in place of an assumption (quantity_assumed.EDITABLE);
    ``learned``: other jobs' values ({"override:slab_thickness": mm, "foundation:tower": m, ...});
    ``opening_heights``: {(building, storey key or None): m} the height of the outer openings from
    the window and door schedule (quantity_arch_takeoff.outer_opening_heights), used where a
    person has not set one, instead of the assumed height; ``excavation``: the project's
    excavation method as answered (오픈컷 / CIP등 흙막이공사), for the earthwork
    (quantity_site_works, with the outside of the buildings)."""
    opening_heights = opening_heights or {}
    overrides = clean_overrides(overrides)
    slab_mm, slab_from = pick("slab_thickness", overrides, learned, slab_thickness * 1000)
    beam_mm, beam_from = pick("beam_depth", overrides, learned, BEAM_DEPTH_DEFAULT * 1000)
    window_m, window_from = pick("window_height", overrides, learned, WINDOW_HEIGHT)
    parapet_m, parapet_from = pick("parapet", overrides, learned, None)
    beam_depth = beam_mm / 1000.0
    from . import quantity_edits
    fixes_by_plan = quantity_edits.by_plan(edits)
    buildings = []
    all_buildings = (model or {}).get("buildings") or []
    host_name, match = tower_regions(all_buildings)
    assumed = Assumed()
    host = next((b for b in all_buildings if b.get("name") == host_name), None)
    host_levels = {l.get("key"): l for l in (host or {}).get("levels") or []}
    matched = sorted(match.items(), key=lambda item: item[1])
    for building in all_buildings:
        plans, levels = building.get("plans") or [], building.get("levels") or []
        name = building.get("name")
        rows = []
        given = (foundations or {}).get(name)
        try:
            given = float(given) if given not in (None, "") else None
        except (TypeError, ValueError):
            given = None
        if given is not None and not 0.1 <= given <= 5.0:
            given = None
        ft_row = _foundation_row(building, plans, levels, host_name, host, match, links, given, learned, assumed, overrides)
        if ft_row:
            rows.append(ft_row)
        for index, level in enumerate(levels):
            key, kind = level.get("key") or "", _storey_kind(level.get("key") or "")
            vplan = plans[level["plan"]] if isinstance(level.get("plan"), int) and level["plan"] < len(plans) else {}
            above = levels[index + 1] if index + 1 < len(levels) else None
            hplan = plans[above["plan"]] if above and isinstance(above.get("plan"), int) and above["plan"] < len(plans) else None
            hplan_id = (hplan or {}).get("id")
            measure = vplan.get("measure") or {}
            hmeasure = (hplan or {}).get("measure") or {}
            t = slab_mm / 1000.0
            thickness_note = None
            if above is not None and links:
                source = (name, above.get("sheet"))
                if (kind == "basement" and name in match and _storey_kind(above.get("key") or "") == "basement"
                        and host_levels.get(above.get("key"))):
                    source = (host_name, host_levels[above["key"]].get("sheet"))
                found = slab_thickness_for(links, source[0], source[1], above.get("key"))
                default = ((links.get("slab_thickness") or {}).get("default"))
                if found:
                    t, thickness_note = found
                elif default and kind == "ground":
                    t, thickness_note = default / 1000.0, f"슬래브 두께 {default}mm (슬래브 배근도 주기)"
            height = float(level.get("height") or 0)
            if key == "PHR" and above is None and parapet_m:
                height = float(parapet_m)  # the penthouse roof's parapets, as a person (or other jobs) set them
            wall_h = height if key == "PHR" and above is None else max(height - t, 0.0)  # parapets: no slab above
            notes, approximate = ([thickness_note] if thickness_note else []), kind == "basement"
            used = []  # (key, label, value, m³, how to change, edit box value, set by a person) assumed on this storey
            vertical_area = float(measure.get("area") or 0) + float(measure.get("col_area") or 0)
            faces = float(measure.get("faces") or 0) + float(measure.get("col_perimeter") or 0)
            outside = min(float(measure.get("outside_faces") or 0), faces)
            # podium basements: each tower takes its outline out of the podium plan of the same level
            region = None
            podium_split = kind == "basement" and match and (name in match or name == host_name)
            if podium_split and name in match:
                region = _region(_plan_at(host, host_levels.get(key)), match[name])
                if region:
                    vertical_area = float(region.get("area") or 0) + float(region.get("col_area") or 0)
                    faces = float(region.get("faces") or 0) + float(region.get("col_perimeter") or 0)
                    outside = 0.0
                    approximate = False
                    notes.append(f"지하는 {host_name} 평면에서 이 동 영역을 잘라 계산했어요")
            elif podium_split:
                taken = [_region(vplan, i) for _n, i in matched]
                taken = [r for r in taken if r]
                if taken:
                    vertical_area -= sum(float(r.get("area") or 0) + float(r.get("col_area") or 0) for r in taken)
                    faces -= sum(float(r.get("faces") or 0) + float(r.get("col_perimeter") or 0) for r in taken)
                    vertical_area, faces = max(vertical_area, 0.0), max(faces, 0.0)
                    outside = min(outside, faces)
                    approximate = False
                    notes.append(f"동 지하 영역({', '.join(n for n, _i in matched)})은 각 동 행에서 계산했어요")
            if kind == "basement" and approximate:
                notes.append("지하층은 동 평면과 주차장 평면이 겹쳐 그려져 있어 근사값이에요")

            gaps, gap_area = float(measure.get("window_gaps") or 0), float(measure.get("window_gap_area") or 0)
            members = _link_members(links, building.get("name"), level.get("sheet")) if links else None
            column_area = sum(c["count"] * c["b"] * c["h"] for c in (members or {}).get("columns") or [])
            column_area = min(column_area, vertical_area)
            concrete = {"foundation": 0.0, "wall": (vertical_area - column_area) * wall_h, "column": column_area * wall_h, "beam": 0.0,
                        "slab": 0.0, "stair": 0.0}
            form = dict.fromkeys(FORM_KEYS, 0.0)
            opening_used = None
            if gaps and kind != "basement":
                # the wall left above and below the openings in the outer walls: their height from the
                # window and door schedule where the architectural plan shows them, else assumed
                listed = None if window_from == "person" else (
                    opening_heights.get((building.get("name"), level.get("key"))) or opening_heights.get((building.get("name"), None)))
                height_m = listed or window_m
                opening_used = height_m
                left = max(wall_h - height_m, 0.0)
                concrete["wall"] += gap_area * left
                form["gang"] += gaps * left
                form["euro" if kind == "roof" else "al_vertical"] += gaps * left
                notes.append(f"창·발코니 개구부 {gaps:.0f}m 위·아래 벽 포함 (개구부 높이 {height_m:g}m" +
                             (" 창호일람표" if listed else ("" if window_from == "person" else " 가정")) + ")")
                if not listed:
                    used.append(("window_height", "외벽 개구부 높이", said(f"{window_m:g}m", window_from) + " — 위·아래는 벽", gap_area * left, "",
                                 window_m, window_from == "person"))
            stair_area = sum(measure.get("stairs") or [])
            if stair_area and kind != "basement":
                concrete["stair"] = stair_area * STAIR_CONCRETE_PER_M2
                form["stair"] = stair_area * STAIR_FORM_PER_M2
                notes.append(f"계단은 평면의 계단 {stair_area:.0f}㎡로 추정")
                used.append(("stair_rate", "계단 콘크리트", f"계단 평면 1㎡당 {STAIR_CONCRETE_PER_M2}㎥", concrete["stair"], "", None, False))
            if kind == "basement":
                form["euro"] += faces * wall_h
            elif kind == "roof":
                form["gang"] += outside * wall_h
                form["euro"] += (faces - outside) * wall_h
            else:
                form["gang"] += outside * wall_h
                form["al_vertical"] += (faces - outside) * wall_h

            slab_area = 0.0
            beam_source, beam_share = (name, above.get("sheet")) if above else (None, None), 1.0
            next_is_basement = bool(above) and _storey_kind(above.get("key") or "") == "basement"
            podium_next = _plan_at(host, host_levels.get(above.get("key"))) if (above and podium_split) else None
            if region is not None and next_is_basement and podium_next is not None:
                upper_region = _region(podium_next, match[name])
                total = float(podium_next.get("slab_area") or 0)
                slab_area = float((upper_region or {}).get("slab_area") or 0)
                cap = _ground_slab(building)
                if cap:
                    slab_area = min(slab_area, cap)  # the outline hull also spans the notches of an L-shaped tower
                beam_source = (host_name, host_levels[above["key"]].get("sheet"))
                beam_share = slab_area / total if total else 0.0
                hplan = None  # handled here
            elif podium_split and name == host_name and hplan is not None and (hmeasure.get("regions") or []):
                upper = _net_slab(hplan) or 0.0
                taken = 0.0
                for tower, i in matched:
                    part = float((_region(hplan, i) or {}).get("slab_area") or 0)
                    cap = _ground_slab(next((b for b in all_buildings if b.get("name") == tower), {}))
                    taken += min(part, cap) if cap else part
                slab_area = max(upper - taken, 0.0)
                beam_share = slab_area / upper if upper else 0.0
                hplan = None
            elif podium_split and name == host_name and hplan is not None and (measure.get("regions") or []):
                # the podium roof: its sheet often leaves the outline open; the storey below less the
                # tower parts is the roof unless the roof sheet itself shows more
                own = _net_slab(vplan) or 0.0
                for tower, i in matched:
                    part = float((_region(vplan, i) or {}).get("slab_area") or 0)
                    cap = _ground_slab(next((b for b in all_buildings if b.get("name") == tower), {}))
                    own -= min(part, cap) if cap else part
                upper = _net_slab(hplan)
                if upper is None or upper < 0.9 * own:
                    slab_area = max(own, 0.0)
                    notes.append("주차장 지붕은 아래층 평면에서 동 영역을 뺀 크기로 봤어요")
                else:
                    slab_area = upper
            if slab_area > 0 or (podium_split and region is not None and next_is_basement):
                pass
            elif hplan is None and len(vplan.get("sub_slabs") or []) >= 2:
                # the roof sheet also draws the penthouse roof: the smallest plan on it
                slab_area = float(vplan["sub_slabs"][-1])
                notes.append("옥탑 지붕은 지붕 도면 안의 가장 작은 평면으로 봤어요")
            elif hplan is not None:
                own = _net_slab(vplan)
                upper = _net_slab(hplan)
                if kind == "roof":
                    # the penthouse on the roof: its own floor or roof plan, never the size of the roof
                    slab_area = upper if upper is not None else float(hplan.get("slab_area") or 0)
                    notes.append("이 층 위 슬래브는 " + (above.get("label") or above.get("key")) + " 평면 크기로 봤어요"
                                 + ("" if upper is not None else " (외곽이 열려 있어 바깥 둘레로 근사)"))
                elif upper is None and own is not None:
                    # a roof sheet draws the walls below as hidden lines, so its outline is open:
                    # the roof slab covers the storey under it
                    slab_area = own
                    notes.append("지붕 슬래브는 바로 아래층 평면 크기로 봤어요")
                elif upper is not None and own is not None and upper < 0.9 * own and kind != "basement":
                    # the floor above is set back: the slab still covers this storey (terraces)
                    slab_area = own
                    notes.append("위층이 들어가 있어 테라스까지 이 층 평면 크기로 슬래브를 잡았어요")
                elif upper is not None:
                    slab_area = upper
                else:
                    slab_area = float(hplan.get("slab_area") or 0)
                    approximate = True
                    notes.append("슬래브 외곽이 열려 있어 바깥 둘레로 근사했어요")
            if slab_area > 0:
                slab_area = max(slab_area, 0.0)
                concrete["slab"] = slab_area * t
                soffit = max(slab_area - vertical_area, 0.0)
                form["plywood" if kind in ("basement", "roof") else "al_horizontal"] += soffit
                if kind == "ground":  # slab edge along the outer walls and the open slab outline
                    edge = (outside + float(hmeasure.get("outline") or measure.get("outline") or 0)) * t
                    form["gang"] += edge
            if above is not None and (hplan is not None or slab_area > 0):
                above_members = _link_members(links, beam_source[0], beam_source[1]) if links else None
                if above_members and above_members.get("left_out_unlabelled_m"):
                    notes.append(f"보 선 {above_members['left_out_unlabelled_m']:.0f}m는 벽식 평면의 부호 없는 선(아래층 벽 점선 등)이거나 "
                                 "일람표에 춤이 없는 부호라 뺐어요")
                if above_members and beam_share < 1.0:
                    above_members = {"beams": [dict(b, length=b["length"] * beam_share) for b in above_members["beams"]],
                                     "columns": above_members["columns"]}
                    notes.append(f"보는 주차장 평면의 보를 슬래브 면적 비율({beam_share:.0%})로 나눴어요")
                if above_members is None:
                    if links:
                        notes.append("보는 일람표와 연결된 평면을 찾지 못해 빠졌어요")
                else:
                    guessed = guessed_concrete = 0.0
                    for beam in above_members["beams"]:
                        depth = beam["h"]
                        if depth is None:
                            depth = beam_depth
                            guessed += beam["length"]
                            guessed_concrete += beam["b"] * max(depth - t, 0.0) * beam["length"]
                        drop = max(depth - t, 0.0)
                        concrete["beam"] += beam["b"] * drop * beam["length"]
                        area = (2 * drop + beam["b"]) * beam["length"]
                        form["plywood" if kind == "basement" else "al_vertical"] += area
                    if guessed:
                        notes.append(f"보 {guessed:.0f}m는 춤이 도면에 없어 {beam_mm:.0f}mm로 봤어요")
                        used.append(("beam_depth", "일람표에 춤이 없는 보", said(f"춤 {beam_mm:.0f}mm", beam_from), guessed_concrete,
                                     "보 일람표에 단면이 있으면 그 값을 써요", beam_mm, beam_from == "person"))
            elif slab_area <= 0 and key == "PHR":
                notes.append(f"옥탑 지붕 위 벽은 난간벽으로 보고 높이 {height:.1f}m로 봤어요")
            elif slab_area <= 0:
                notes.append("위층 평면이 없어 이 층 위 슬래브·보는 빠졌어요")
            if not links:
                notes.append("보와 기둥 구분은 AI 판독 뒤 일람표로 채워져요")
            # people's fixes: walls and columns drawn on this storey's plan, beams on the plan above
            fixes = [e for e in fixes_by_plan.get(vplan.get("id"), []) if e["kind"] != "beam"]
            if above is not None and hplan_id:
                fixes += [e for e in fixes_by_plan.get(hplan_id, []) if e["kind"] == "beam"]
            fixed = None
            if fixes:
                added_concrete = added_form = 0.0
                for fix in fixes:
                    concrete_part, form_part = quantity_edits.effect(fix, kind, wall_h, t)
                    for k, v in concrete_part.items():
                        concrete[k] = max(concrete.get(k, 0.0) + v, 0.0)
                        added_concrete += v
                    for k, v in form_part.items():
                        form[k] = max(form.get(k, 0.0) + v, 0.0)
                        added_form += v
                notes.append(quantity_edits.note(fixes, added_concrete, added_form))
                fixed = {"count": len(fixes), "concrete": round(added_concrete, 2), "formwork": round(added_form, 2)}
            concrete["total"] = sum(concrete[k] for k in CONCRETE_KEYS)
            form["total"] = sum(form[k] for k in FORM_KEYS)
            if slab_area > 0 and thickness_note is None:
                used.append(("slab_thickness", "슬래브 두께", said(f"{t * 1000:.0f}mm", slab_from), concrete["slab"],
                             "슬래브 배근도·일람표가 연결되면 그 두께를 써요", round(t * 1000), slab_from == "person"))
            if key == "PHR" and above is None and concrete["wall"] > 0:
                used.append(("parapet", "옥탑 지붕 위 난간벽 높이", said(f"{height:.1f}m", parapet_from if parapet_m else "assumed"),
                             concrete["wall"], "", round(height, 2), parapet_from == "person"))
            for item_key, label, value, amount, change, raw, person in used:
                assumed.add(item_key, label, value, name, level.get("label") or key, amount, CONCRETE, change, counted=not approximate,
                            raw=raw, person=person)
            rows.append({
                "key": key, "label": level.get("label") or key, "height": round(height, 3),
                "elevation": round(float(level["elev"]), 3) if level.get("elev") is not None else None,
                "height_from": level.get("height_from") or "drawing",
                "sheets": {"vertical": level.get("sheet"), "horizontal": above.get("sheet") if above else None},
                "concrete": _round(concrete, 2), "formwork": _round(form, 2),
                "measures": {"vertical_area": round(vertical_area, 2), "faces": round(faces, 1),
                             "outside_faces": round(outside, 1), "slab_area": round(slab_area, 1),
                             "window_gaps": round(gaps, 1), "opening_height": opening_used},
                "approximate": approximate, "notes": notes, "fixed": fixed,
            })
        _flag_sparse_walls(name, levels, rows)
        totals = {"concrete": dict.fromkeys(CONCRETE_KEYS + ("total",), 0.0), "formwork": dict.fromkeys(FORM_KEYS + ("total",), 0.0)}
        approx = {"concrete": dict.fromkeys(CONCRETE_KEYS + ("total",), 0.0), "formwork": dict.fromkeys(FORM_KEYS + ("total",), 0.0)}
        for row in rows:
            target = approx if row["approximate"] else totals
            for group in ("concrete", "formwork"):
                for k, v in row[group].items():
                    target[group][k] += v
        buildings.append({"name": building.get("name"), "rows": rows,
                          "totals": {g: _round(v, 1) for g, v in totals.items()},
                          "approximate_totals": {g: _round(v, 1) for g, v in approx.items()}})
    from . import quantity_site_works as works
    try:  # the earthwork and the outside never cost the takeoff its rows
        earth = works.earthwork(buildings, all_buildings, host_name, match, overrides, learned, excavation, assumed)
        outside_works = works.exterior(buildings, match, overrides, assumed)
    except Exception as exc:
        earth, outside_works = {"error": str(exc)[:200], "groups": []}, {"error": str(exc)[:200], "buildings": []}
    return {
        "version": TAKEOFF_VERSION, "rule": "N층 = N층 벽·기둥 + N+1층 바닥 슬래브·보",
        "earthwork": earth, "exterior": outside_works,
        "assumptions": {"slab_thickness": slab_mm / 1000.0, "beam_depth_default": beam_depth,
                        "window_height": window_m},
        "overrides": overrides,
        "uses_schedules": bool(links), "buildings": buildings, "assumed": _assumed_items(assumed, overrides),
        "not_included": ["기초 피트·단차", "발코니 쪽 개구부 위·아래 벽 일부"],
    }
