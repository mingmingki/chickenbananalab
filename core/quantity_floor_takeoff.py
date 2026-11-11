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

import numpy as np

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
    faces = outside_faces = 0.0
    outer = []
    reach = (GROW + 2) * cell
    for ax, ay, bx, by in lines:
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
            "caps": len(caps), "cell": round(cell, 3),
            "window_gaps": round(sum(g[0] for g in gaps), 2),
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
WINDOW_HEIGHT = 1.8        # assumed opening height; the wall above and below an outer opening is concrete
# stairs per m² of stair hall in plan: 150 mm waist slab and landings plus 178 x 260 mm steps
STAIR_CONCRETE_PER_M2 = 0.2
STAIR_FORM_PER_M2 = 1.4
FORM_KEYS = ("al_vertical", "al_horizontal", "gang", "euro", "plywood", "stair")
FORM_LABELS = {"al_vertical": "AL폼 수직", "al_horizontal": "AL폼 수평", "gang": "갱폼", "euro": "유로폼", "plywood": "합판",
               "stair": "계단"}
CONCRETE_KEYS = ("wall", "column", "beam", "slab", "stair")
CONCRETE_LABELS = {"wall": "벽", "column": "기둥", "beam": "보", "slab": "슬래브", "stair": "계단"}


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
    beams, columns = [], []
    for item in links.get("marks") or []:
        category, mark = item.get("category"), item.get("mark")
        for occurrence in item.get("occurrences") or []:
            if occurrence.get("plan") not in wanted:
                continue
            if category == "beams":
                measured = occurrence.get("measured") or {}
                b, h = sizes.get((category, mark), (None, None))
                for width, length in (measured.get("widths") or {}).items():
                    beams.append({"mark": mark, "length": length / 1000.0, "b": b or int(width) / 1000.0, "h": h})
            elif category == "columns":
                b, h = sizes.get((category, mark), (None, None))
                if b and h:
                    columns.append({"mark": mark, "count": occurrence.get("count") or 0, "b": b, "h": h})
    for index in wanted:
        unlabelled = (((plans[index].get("geometry") or {}).get("beams") or {}).get("unlabelled") or {})
        for width, length in (unlabelled.get("widths") or {}).items():
            beams.append({"mark": None, "length": length / 1000.0, "b": int(width) / 1000.0, "h": None})
    return {"beams": beams, "columns": columns}


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


def build_takeoff(model, links=None, slab_thickness=SLAB_THICKNESS, edits=None):
    """Rows per building and storey: walls/columns of the storey + slab/beams on top of it.
    ``edits``: people's fixes (quantity_edits.clean), added to the storeys using their plans."""
    from . import quantity_edits
    fixes_by_plan = quantity_edits.by_plan(edits)
    buildings = []
    all_buildings = (model or {}).get("buildings") or []
    host_name, match = tower_regions(all_buildings)
    host = next((b for b in all_buildings if b.get("name") == host_name), None)
    host_levels = {l.get("key"): l for l in (host or {}).get("levels") or []}
    matched = sorted(match.items(), key=lambda item: item[1])
    for building in all_buildings:
        plans, levels = building.get("plans") or [], building.get("levels") or []
        name = building.get("name")
        rows = []
        for index, level in enumerate(levels):
            key, kind = level.get("key") or "", _storey_kind(level.get("key") or "")
            vplan = plans[level["plan"]] if isinstance(level.get("plan"), int) and level["plan"] < len(plans) else {}
            above = levels[index + 1] if index + 1 < len(levels) else None
            hplan = plans[above["plan"]] if above and isinstance(above.get("plan"), int) and above["plan"] < len(plans) else None
            hplan_id = (hplan or {}).get("id")
            measure = vplan.get("measure") or {}
            hmeasure = (hplan or {}).get("measure") or {}
            t = slab_thickness
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
            wall_h = max(height - t, 0.0)
            notes, approximate = ([thickness_note] if thickness_note else []), kind == "basement"
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
            concrete = {"wall": (vertical_area - column_area) * wall_h, "column": column_area * wall_h, "beam": 0.0,
                        "slab": 0.0, "stair": 0.0}
            form = dict.fromkeys(FORM_KEYS, 0.0)
            if gaps and kind != "basement":
                # the wall left above and below the openings in the outer walls
                left = max(wall_h - WINDOW_HEIGHT, 0.0)
                concrete["wall"] += gap_area * left
                form["gang"] += gaps * left
                form["euro" if kind == "roof" else "al_vertical"] += gaps * left
                notes.append(f"창·발코니 개구부 {gaps:.0f}m 위·아래 벽 포함 (개구부 높이 {WINDOW_HEIGHT}m 가정)")
            stair_area = sum(measure.get("stairs") or [])
            if stair_area and kind != "basement":
                concrete["stair"] = stair_area * STAIR_CONCRETE_PER_M2
                form["stair"] = stair_area * STAIR_FORM_PER_M2
                notes.append(f"계단은 평면의 계단 {stair_area:.0f}㎡로 추정")
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
                if upper is None and own is not None:
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
                if above_members and beam_share < 1.0:
                    above_members = {"beams": [dict(b, length=b["length"] * beam_share) for b in above_members["beams"]],
                                     "columns": above_members["columns"]}
                    notes.append(f"보는 주차장 평면의 보를 슬래브 면적 비율({beam_share:.0%})로 나눴어요")
                if above_members is None:
                    if links:
                        notes.append("보는 일람표와 연결된 평면을 찾지 못해 빠졌어요")
                else:
                    guessed = 0.0
                    for beam in above_members["beams"]:
                        depth = beam["h"]
                        if depth is None:
                            depth = BEAM_DEPTH_DEFAULT
                            guessed += beam["length"]
                        drop = max(depth - t, 0.0)
                        concrete["beam"] += beam["b"] * drop * beam["length"]
                        area = (2 * drop + beam["b"]) * beam["length"]
                        form["plywood" if kind == "basement" else "al_vertical"] += area
                    if guessed:
                        notes.append(f"보 {guessed:.0f}m는 춤이 도면에 없어 {BEAM_DEPTH_DEFAULT * 1000:.0f}mm로 봤어요")
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
            rows.append({
                "key": key, "label": level.get("label") or key, "height": round(height, 3),
                "sheets": {"vertical": level.get("sheet"), "horizontal": above.get("sheet") if above else None},
                "concrete": _round(concrete, 2), "formwork": _round(form, 2),
                "measures": {"vertical_area": round(vertical_area, 2), "faces": round(faces, 1),
                             "outside_faces": round(outside, 1), "slab_area": round(slab_area, 1)},
                "approximate": approximate, "notes": notes, "fixed": fixed,
            })
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
    return {
        "version": TAKEOFF_VERSION, "rule": "N층 = N층 벽·기둥 + N+1층 바닥 슬래브·보",
        "assumptions": {"slab_thickness": slab_thickness, "beam_depth_default": BEAM_DEPTH_DEFAULT,
                        "window_height": WINDOW_HEIGHT},
        "uses_schedules": bool(links), "buildings": buildings,
        "not_included": ["기초(매트)", "발코니 쪽 개구부 위·아래 벽 일부", "철근(AI 판독 층별 수량산출 참고)"],
    }
