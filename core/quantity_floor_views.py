"""Floor-by-floor colored plans for the upload check: walls by material, from the CAD itself.

Every plan sheet (title-block frame whose title says 평면도) is drawn from its own lines plus
the xref drawings it inserts, and each line is colored by what its layer says it is:
concrete (골조), masonry (조적), ALC, dry walls (건식벽) and openings (창호). Text, hatches,
dimensions, center lines and everything else is left out, which keeps a sheet near 300 KB.

Pure module (no Django): the web job converts the DWGs and runs `main()` in a separate
low-priority process so the drawing walk never holds the web worker's memory.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import unicodedata
from collections import defaultdict

FLOOR_VIEWS_VERSION = 1
CATEGORIES = ("frame", "beam", "masonry", "alc", "dry", "opening")
CATEGORY_LABELS = {"frame": "골조(콘크리트)", "beam": "보", "masonry": "조적", "alc": "ALC",
                   "dry": "건식벽", "opening": "창호"}
# first match wins; anything that matches none is not drawn
_LAYER_RULES = (
    ("skip", re.compile(r"text|bubble|dim|defpoint|(^|[-_ ])cen($|[-_ ])|center|sym|title|form|keyplan|"
                        r"hat|해치|print|점형|_name$|name$|grid|axis|leader", re.I)),
    ("beam", re.compile(r"str_(beam|girder)", re.I)),
    ("frame", re.compile(r"conc|콘크리트|str_(col|wall|conc|fdn|found|slab_edge)|옹벽", re.I)),
    ("masonry", re.compile(r"brick|벽돌|조적|블록|블럭|block", re.I)),
    ("alc", re.compile(r"alc", re.I)),
    ("dry", re.compile(r"석고|gboard|gypsum|건식|경량|drywall|stud", re.I)),
    ("opening", re.compile(r"win|door|창호|문틀", re.I)),
)
_PLAN_EXCLUDE = re.compile(r"배근|일람|상세|단면|입면|창호|방화|동선|장애인|배치|구적|위치|결정|단열|방수|결로|코아|코어|"
                           r"확대|면적|성능|마감|안내|계획도|조경")
_SHEET_FILE = re.compile(r"^[A-Z]{1,2}[-_ ]?\d", re.I)
MAX_SUBPATHS_PER_VIEW = 60000
MIN_EXTENT_MM = 30.0


def _nfc(text):
    return unicodedata.normalize("NFC", str(text or ""))


def layer_category(layer):
    """Material category of a layer name (xref layers 'XREF|name' use the part after '|')."""
    base = _nfc(layer).split("|")[-1]
    for name, rule in _LAYER_RULES:
        if rule.search(base):
            return None if name == "skip" else name
    return None


def is_plan_title(title):
    text = re.sub(r"\s+", "", _nfc(title))
    return "평면도" in text and not _PLAN_EXCLUDE.search(text)


def floor_of(title):
    """{building, floor, group, order, discipline} from a sheet title such as
    '101동 지하 2층 평면도', '지하2층 지하주차장 평면도' or '66A형 단위세대 평면도 (확장형)'."""
    raw = _nfc(title)
    text = re.sub(r"\s+", "", raw)
    discipline = "struct" if "구조" in text else "arch"
    building = None
    match = re.search(r"(\d{3,4})동", text)
    if match:
        building = match.group(1) + "동"
    elif "지하주차장" in text or "주차장" in text:
        building = "지하주차장"
    elif "단위세대" in text:
        building = "단위세대"
    elif "부대" in text or "경비실" in text:
        building = "부대시설"
    if building == "단위세대":
        unit = re.search(r"(\d{2,3}[A-Z0-9]*?)(형|타입|TYPE)", text, re.I)
        extra = re.search(r"\((기본형|확장형)\)", text)
        label = (unit.group(1) + "형" if unit else "단위세대") + (" " + extra.group(1) if extra else "")
        return {"building": building, "floor": label, "group": "unit", "order": 0, "discipline": discipline}
    floors = re.findall(r"(지하|지상)?(\d+)층", text)
    if "기초" in text:
        return {"building": building or "기타", "floor": "기초", "group": "basement", "order": -99, "discipline": discipline}
    if floors:
        under, first = floors[0][0] == "지하", int(floors[0][1])
        if under:
            return {"building": building or "기타", "floor": f"지하{first}층", "group": "basement",
                    "order": -first, "discipline": discipline}
        if len(floors) > 1 and floors[-1][0] != "지하":
            label = f"{first}~{int(floors[-1][1])}층"
        else:
            label = f"{first}층"
        return {"building": building or "기타", "floor": label, "group": "ground", "order": first,
                "discipline": discipline}
    if "지붕" in text or "옥탑" in text:
        return {"building": building or "기타", "floor": "지붕·옥탑", "group": "roof", "order": 900,
                "discipline": discipline}
    if "기준층" in text:
        return {"building": building or "기타", "floor": "기준층", "group": "ground", "order": 50,
                "discipline": discipline}
    return {"building": building or "기타", "floor": raw.replace("평면도", "").strip() or "평면",
            "group": "other", "order": 950, "discipline": discipline}


def is_plan_file(name):
    """A sheet-set file worth converting for plans (by its name, before it is read)."""
    text = re.sub(r"\s+", "", _nfc(os.path.basename(name)))
    return bool(_SHEET_FILE.match(text)) and "평면도" in text and not _PLAN_EXCLUDE.search(text)


def is_reference_file(name):
    """Files that are probably xrefs or title-block forms (not numbered sheet sets)."""
    base = _nfc(os.path.basename(name))
    return not _SHEET_FILE.match(base) or bool(re.match(r"^xr[-_ ]", base, re.I))


def referenced_files(dxf_text):
    """Base names (no extension) of the .dwg files a DXF text references as xrefs."""
    names = set()
    for path in re.findall(r"\n\s*1\r?\n([^\r\n]{1,260}\.dwg)\r?\n", dxf_text, re.I):
        names.add(_key(re.split(r"[\\/]", path)[-1]))
    return names


def _key(name):
    base = _nfc(name).rsplit("/", 1)[-1]
    base = re.sub(r"\.(dwg|dxf)$", "", base, flags=re.I)
    return base.casefold().strip()


reference_key = _key


# ── drawing walk ──────────────────────────────

def _walk(doc, entities, matrix, inherit, xrefs, out, depth=0, classify=None):
    from ezdxf import path as ezpath
    from ezdxf.math import Matrix44
    for entity in entities:
        kind = entity.dxftype()
        layer = entity.dxf.get("layer", "0")
        if layer == "0" and inherit:
            layer = inherit
        if kind == "INSERT":
            block = doc.blocks.get(entity.dxf.name)
            if block is None or depth > 8:
                continue
            try:
                placed = Matrix44.chain(entity.matrix44(), matrix)
            except Exception:
                continue
            clip = _clip_polygon(entity, matrix)
            target_out = [] if clip else out
            if block.block_record.is_xref:
                path = block.block.dxf.get("xref_path", "") or entity.dxf.name
                target = xrefs.get(_key(re.split(r"[\\/]", path)[-1])) or xrefs.get(_key(entity.dxf.name))
                if target is not None and "form" not in _key(path):
                    _walk(target, target.modelspace(), placed, None, xrefs, target_out, depth + 1, classify)
            else:
                _walk(doc, block, placed, layer, xrefs, target_out, depth + 1, classify)
            if clip:
                polygon, inverted = clip
                out.extend(item for item in target_out if _kept_by_clip(item, polygon, inverted))
            continue
        if kind not in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
            continue
        category = (classify or layer_category)(layer)
        if category is None:
            continue
        try:
            path = ezpath.make_path(entity).transform(matrix)
            points = [(p.x, p.y) for p in path.flattening(40)]
        except Exception:
            continue
        if len(points) < 2:
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        if max(xs) - min(xs) < MIN_EXTENT_MM and max(ys) - min(ys) < MIN_EXTENT_MM:
            continue
        out.append((category, min(xs), min(ys), max(xs), max(ys), points))


def _clip_polygon(insert, matrix):
    """The XCLIP boundary of an INSERT as a polygon in drawing coordinates, or None.

    Sheets often insert one big xref several times and clip each copy to a small window;
    without this the whole xref would spill into the neighbouring sheets.
    """
    try:
        from ezdxf import xclip
        clip = xclip.XClip(insert)
        if not clip.has_clipping_path or not clip.is_clipping_enabled:
            return None
        path = clip.get_wcs_clipping_path()
        points = [matrix.transform((v.x, v.y, 0)) for v in path.vertices]
        if len(points) == 2:  # a rectangle given by two corners
            (ax, ay), (bx, by) = (points[0].x, points[0].y), (points[1].x, points[1].y)
            polygon = [(ax, ay), (bx, ay), (bx, by), (ax, by)]
        else:
            polygon = [(p.x, p.y) for p in points]
        inverted = bool(getattr(clip, "is_inverted_clip", False))
        return (polygon, inverted) if len(polygon) >= 3 else None
    except Exception:
        return None


def _inside(polygon, x, y):
    hit = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            hit = not hit
        j = i
    return hit


def _kept_by_clip(item, polygon, inverted):
    _category, ax, ay, bx, by, points = item
    inside = _inside(polygon, (ax + bx) / 2, (ay + by) / 2) or any(_inside(polygon, x, y) for x, y in points[:: max(1, len(points) // 8)])
    return inside != inverted


def _svg(paths, box):
    x0, y0, x1, y1 = box
    width, height = (x1 - x0) / 10.0, (y1 - y0) / 10.0
    groups, counts, total = defaultdict(list), defaultdict(int), 0
    for category, ax, ay, bx, by, points in paths:
        if bx < x0 or ax > x1 or by < y0 or ay > y1:
            continue
        if total >= MAX_SUBPATHS_PER_VIEW:
            break
        coords, last = [], None
        for px, py in points:
            pair = (round((px - x0) / 10.0), round((y1 - py) / 10.0))
            if pair != last:
                coords.append(f"{pair[0]} {pair[1]}")
                last = pair
        if len(coords) < 2:
            continue
        groups[category].append("M" + "L".join(coords))
        counts[category] += 1
        total += 1
    body = "".join(f'<path class="fv-{c}" d="{"".join(groups[c])}"/>' for c in CATEGORIES if groups[c])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
           f'preserveAspectRatio="xMidYMid meet">{body}</svg>')
    return svg, dict(counts)


def _plan_views(name, doc, rows, xrefs):
    """Views of one drawing's plan frames; `xrefs` maps reference keys to loaded documents."""
    from ezdxf.math import Matrix44
    plans = [row for row in rows if row.get("frame_bbox") and is_plan_title(row.get("drawing_title"))]
    if not plans:
        return []
    paths = []
    _walk(doc, doc.modelspace(), Matrix44(), None, xrefs, paths)
    views = []
    for row in plans:
        svg, counts = _svg(paths, row["frame_bbox"])
        if not counts:
            continue
        floor = floor_of(row["drawing_title"])
        variant = "구조평면" if floor["discipline"] == "struct" else "건축평면"
        if "보완" in _nfc(name):
            variant += " (보완)"
        views.append(dict(floor, file=name, sheet=row.get("drawing_number"), title=row.get("drawing_title"),
                          variant=variant, counts=counts, svg=svg))
    return views


def _finish(views, warnings):
    order = {"basement": 0, "ground": 1, "roof": 2, "unit": 3, "other": 4}

    def building_rank(name):
        match = re.match(r"(\d+)동$", name)
        if match:
            return (0, int(match.group(1)), name)
        return ({"지하주차장": 1, "부대시설": 2, "단위세대": 3}.get(name, 4), 0, name)

    views.sort(key=lambda v: (building_rank(v["building"]), order.get(v["group"], 9), v["order"],
                              v["floor"], v["discipline"] != "arch", v["variant"], v.get("sheet") or ""))
    seen = defaultdict(int)
    for view in views:
        seen[(view["building"], view["floor"], view["variant"])] += 1
    for view in views:
        if seen[(view["building"], view["floor"], view["variant"])] > 1 and view.get("sheet"):
            view["variant"] = f'{view["variant"]} {view["sheet"]}'
    return {"version": FLOOR_VIEWS_VERSION, "views": views, "warnings": warnings,
            "legend": [{"key": key, "label": CATEGORY_LABELS[key]} for key in CATEGORIES]}


def build_views(docs, evidence_by_name):
    """Views for every plan frame of documents already in memory (tests and small sets).

    docs: {file name: ezdxf document} (plans, their xrefs and title-block forms).
    evidence_by_name: {file name: sheet evidence} from quantity_sheet_identity.
    """
    from . import quantity_sheet_identity as identity
    sheets = identity.resolve_cad_sheets(evidence_by_name)
    xrefs = {_key(name): doc for name, doc in docs.items()}
    views, warnings = [], []
    for name, rows in sheets.items():
        if name not in docs:
            continue
        missing = sorted(ref for ref in _xref_names(docs[name]) if ref not in xrefs and "form" not in ref)
        if missing:
            warnings.append({"file": name, "missing_xrefs": missing})
        views.extend(_plan_views(name, docs[name], rows, xrefs))
    return _finish(views, warnings)


# ── 3D model data (structural plans) ───────────
_MODEL_RULES = (
    ("wall", re.compile(r"str_(conc|core|wall)$", re.I)),
    ("col", re.compile(r"str_col$", re.I)),
    ("beam", re.compile(r"str_(beam|girder)(-[a-z]+)?$", re.I)),
    ("outline", re.compile(r"str_outline$", re.I)),
    ("open", re.compile(r"str_open$", re.I)),
)
DEFAULT_STOREY_M = {"basement": 3.5, "1": 3.25, "ground": 2.85, "roof": 3.0}
LEVEL_FILE = re.compile(r"단면도|입면도")
LEVEL_FILE_EXCLUDE = re.compile(r"단위세대|코아|코어|상세|계단|경사로|창호|부대|경비")


def model_category(layer):
    """Structural member family of a layer for the 3D model (cut walls, columns, beams, slab edges)."""
    base = _nfc(layer).split("|")[-1]
    for name, rule in _MODEL_RULES:
        if rule.search(base):
            return name
    return None


def is_level_file(name):
    """Section / elevation sheets whose EL marks give the storey heights."""
    text = re.sub(r"\s+", "", _nfc(os.path.basename(name)))
    return bool(_SHEET_FILE.match(text)) and bool(LEVEL_FILE.search(text)) and not LEVEL_FILE_EXCLUDE.search(text)


def _loops(segments, tolerance=0.02):
    """Closed polygons chained from loose edge segments (slab edges are drawn as LINEs)."""
    def key(point):
        return (round(point[0] / tolerance), round(point[1] / tolerance))
    adjacency, points = defaultdict(list), {}
    for index, (a, b) in enumerate(segments):
        ka, kb = key(a), key(b)
        if ka == kb:
            continue
        points[ka], points[kb] = a, b
        adjacency[ka].append((kb, index))
        adjacency[kb].append((ka, index))
    used, loops = set(), []
    for start in list(adjacency):
        for nxt, edge in adjacency[start]:
            if edge in used:
                continue
            path, current, prev_edge = [start], nxt, edge
            used.add(edge)
            while current != start and len(path) < 5000:
                path.append(current)
                options = [(n, e) for n, e in adjacency[current] if e not in used]
                if not options:
                    break
                current, prev_edge = options[0]
                used.add(prev_edge)
            if current == start and len(path) >= 3:
                polygon = [points[k] for k in path]
                if abs(_area(polygon)) >= 4.0:
                    loops.append(polygon)
    return loops


def _area(polygon):
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1])) / 2.0


def _hull(points):
    points = sorted(set(points))
    if len(points) < 3:
        return points
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _footprint(segments, cell=0.15, close_cells=2):
    """Floor area enclosed by member lines, as rectangles [x0, y0, x1, y1] in metres.

    Slab edges are drawn as loose lines broken at every wall, so the enclosed area is found on
    a grid instead: lines are drawn, small gaps (window and door gaps up to about 2*close_cells
    cells) are closed, the outside is flood-filled and what is left is the floor.
    """
    from collections import deque
    if not segments:
        return [], (lambda x, y: True)
    xs = [v for seg in segments for v in (seg[0], seg[2])]
    ys = [v for seg in segments for v in (seg[1], seg[3])]
    minx, miny = min(xs) - 1.0, min(ys) - 1.0
    width, height = max(xs) + 1.0 - minx, max(ys) + 1.0 - miny
    cell = max(cell, width / 1200.0, height / 1200.0)
    nx, ny = int(width / cell) + 1, int(height / cell) + 1
    grid = bytearray(nx * ny)
    for ax, ay, bx, by in segments:
        steps = int(max(abs(bx - ax), abs(by - ay)) / (cell / 2)) + 1
        for k in range(steps + 1):
            t = k / steps
            gx, gy = int((ax + (bx - ax) * t - minx) / cell), int((ay + (by - ay) * t - miny) / cell)
            grid[gy * nx + gx] = 1
    for _ in range(close_cells):
        grown = bytearray(grid)
        for index, value in enumerate(grid):
            if value:
                gx, gy = index % nx, index // nx
                if gx > 0: grown[index - 1] = 1
                if gx < nx - 1: grown[index + 1] = 1
                if gy > 0: grown[index - nx] = 1
                if gy < ny - 1: grown[index + nx] = 1
        grid = grown
    outside = bytearray(nx * ny)
    queue = deque(i for i in range(nx * ny) if (i % nx in (0, nx - 1) or i // nx in (0, ny - 1)) and not grid[i])
    for i in queue:
        outside[i] = 1
    while queue:
        index = queue.popleft()
        gx, gy = index % nx, index // nx
        for n, ok in ((index - 1, gx > 0), (index + 1, gx < nx - 1), (index - nx, gy > 0), (index + nx, gy < ny - 1)):
            if ok and not outside[n] and not grid[n]:
                outside[n] = 1
                queue.append(n)
    # the closing grew the walls outward; shrink the floor back by the same amount
    floor = bytearray(0 if outside[i] else 1 for i in range(nx * ny))
    for _ in range(close_cells):
        shrunk = bytearray(floor)
        for index, value in enumerate(floor):
            if value:
                gx, gy = index % nx, index // nx
                if (gx == 0 or not floor[index - 1] or gx == nx - 1 or not floor[index + 1]
                        or gy == 0 or not floor[index - nx] or gy == ny - 1 or not floor[index + nx]):
                    shrunk[index] = 0
        floor = shrunk
    rects, open_runs = [], {}
    for gy in range(ny):
        runs, gx = [], 0
        while gx < nx:
            if floor[gy * nx + gx]:
                start = gx
                while gx < nx and floor[gy * nx + gx]:
                    gx += 1
                runs.append((start, gx))
            gx += 1
        next_runs = {}
        for run in runs:
            if run in open_runs:
                next_runs[run] = open_runs.pop(run)
            else:
                next_runs[run] = gy
        for (a, b), start_row in open_runs.items():
            rects.append([a, start_row, b, gy])
        open_runs = next_runs
    for (a, b), start_row in open_runs.items():
        rects.append([a, start_row, b, ny])
    r = lambda v: round(v, 2)

    def is_outside(x, y):
        gx, gy = int((x - minx) / cell), int((y - miny) / cell)
        if gx < 0 or gy < 0 or gx >= nx or gy >= ny:
            return True
        return bool(outside[gy * nx + gx])

    return ([[r(minx + a * cell), r(miny + y0 * cell), r(minx + b * cell), r(miny + y1 * cell)]
             for a, y0, b, y1 in rects if (b - a) * (y1 - y0) * cell * cell >= 0.1], is_outside)


def _plan_model(paths, box):
    """Members of one structural frame in metres, frame-local (x right, y up)."""
    x0, y0, x1, y1 = box
    r = lambda v: round(v, 2)
    walls, beams, edges, cols, opens = [], [], [], [], []
    for category, ax, ay, bx, by, points in paths:
        if bx < x0 or ax > x1 or by < y0 or ay > y1:
            continue
        local = [((px - x0) / 1000.0, (py - y0) / 1000.0) for px, py in points]
        closed = len(local) >= 4 and abs(local[0][0] - local[-1][0]) < 0.005 and abs(local[0][1] - local[-1][1]) < 0.005
        if category == "col" and closed:
            cols.append([[r(x), r(y)] for x, y in local[:-1]])
            continue
        target = {"wall": walls, "col": walls, "beam": beams, "outline": edges, "open": opens}[category]
        for (ax_, ay_), (bx_, by_) in zip(local, local[1:]):
            if abs(ax_ - bx_) + abs(ay_ - by_) < 0.05:
                continue
            target.append(((ax_, ay_), (bx_, by_)) if category == "outline" else [r(ax_), r(ay_), r(bx_), r(by_)])
    outline = [[a[0], a[1], b[0], b[1]] for a, b in edges]
    edge_lines = walls + outline + [[c[i][0], c[i][1], c[(i + 1) % len(c)][0], c[(i + 1) % len(c)][1]]
                                    for c in cols for i in range(len(c))]
    slab, is_outside = _footprint(edge_lines)
    for wall in walls:  # 5th value 1: an exterior face (one side of it is outside the building)
        ax, ay, bx, by = wall[:4]
        length = math.hypot(bx - ax, by - ay) or 1.0
        # probe past the gap-closing growth (2 cells): only an outer face has open air on one side
        nx_, ny_ = (by - ay) / length * 0.5, -(bx - ax) / length * 0.5
        mx, my = (ax + bx) / 2, (ay + by) / 2
        wall.append(1 if is_outside(mx + nx_, my + ny_) != is_outside(mx - nx_, my - ny_) else 0)
    area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in slab)
    hull = _hull([(w[0], w[1]) for w in edge_lines] + [(w[2], w[3]) for w in edge_lines])
    hull_area = abs(_area(hull)) if len(hull) >= 3 else 0.0
    model = {"walls": walls, "beams": beams, "cols": cols, "slab": slab, "opens": opens,
             "slab_area": round(area, 1), "slab_approximate": False}
    if hull_area and area < 0.35 * hull_area:
        # an open outline (basements joined to a parking deck): the outer hull stands in
        model.update(slab=[], slab_polygon=[[r(x), r(y)] for x, y in hull], slab_area=round(hull_area, 1),
                     slab_approximate=True)
    return model


def _storey_keys(floor):
    """Level keys a plan covers: '지하2층' → ['B2'], '3~13층' → ['3', ..., '13'], '지붕·옥탑' → ['R']."""
    label = floor["floor"]
    if floor["group"] == "basement":
        match = re.search(r"지하(\d+)층", label)
        return ["B" + match.group(1)] if match else ["FT"]
    if floor["group"] == "roof":
        return ["R"]
    match = re.match(r"(\d+)~(\d+)층", label)
    if match:
        return [str(n) for n in range(int(match.group(1)), int(match.group(2)) + 1)]
    match = re.match(r"(\d+)층", label)
    return [match.group(1)] if match else []


def _level_order(key):
    if key == "FT":
        return -100
    if key.startswith("B"):
        return -int(key[1:])
    if key.isdigit():
        return int(key)
    return {"R": 500, "PH": 501, "PHR": 502}.get(key, 999)


def _elevations(keys, table, section):
    """Floor elevation (m) of each level key; read from the level table, gaps filled with defaults."""
    sections = (table or {}).get("sections") or {}
    known = {}
    for name in (section, "주동", "공통"):
        info = sections.get(name) or {}
        for level, value in (info.get("elevations") or {}).items():
            known.setdefault(level, value)
    if "1" not in known and "GL" in known:
        known["1"] = known["GL"]
    ordered = sorted(set(keys) | {k for k in known if k in keys}, key=_level_order)
    result, last = {}, None
    for key in ordered:
        if key in known:
            result[key] = known[key]
        elif last is not None:
            prev_key, prev_elev = last
            group = "basement" if prev_key.startswith("B") else ("1" if prev_key == "1" else "ground")
            result[key] = round(prev_elev + DEFAULT_STOREY_M[group], 3)
        else:
            result[key] = 0.0
        last = (key, result[key])
    # a basement level read below its upper neighbour only by default stays consistent upward
    return result


def build_model(plans, table):
    """Buildings as stacks of storeys from structural plans.

    plans: [{"floor": floor_of(...), "sheet", "model": _plan_model(...)}].
    A plan's walls stand on its own level up to the next one; its slab, beams and openings sit at
    the top of that storey (the takeoff rule "N층 = N층 수직재 + N+1층 바닥 수평재").
    """
    by_building = defaultdict(list)
    for plan in plans:
        by_building[plan["floor"]["building"]].append(plan)
    buildings = []
    for name, rows in by_building.items():
        storeys, foundation = {}, None
        for index, plan in enumerate(rows):
            if plan["floor"]["floor"] == "기초":
                foundation = index
                continue
            for key in _storey_keys(plan["floor"]):
                storeys.setdefault(key, index)
        keys = sorted(storeys, key=_level_order)
        if not keys:
            continue
        top = keys[-1]
        upper = {"R": "PH"}.get(top) or (str(int(top) + 1) if top.isdigit() else ("1" if top == "B1" else None))
        elevations = _elevations(keys + ([upper] if upper else []), table, name)
        levels = []
        for position, key in enumerate(keys):
            nxt = keys[position + 1] if position + 1 < len(keys) else upper
            base = elevations.get(key, 0.0)
            height = round((elevations.get(nxt) - base) if nxt in elevations else DEFAULT_STOREY_M["ground"], 3)
            if not 1.5 <= height <= 12:
                height = DEFAULT_STOREY_M["basement" if key.startswith("B") else "ground"]
            levels.append({"key": key, "label": ("지하" + key[1:] + "층") if key.startswith("B") else
                           ("지붕·옥탑" if key == "R" else key + "층"), "elev": base, "height": height,
                           "plan": storeys[key], "sheet": rows[storeys[key]].get("sheet")})
        buildings.append({"name": name, "levels": levels, "foundation": foundation,
                          "plans": [plan["model"] for plan in rows]})
    return {"version": 1, "buildings": buildings,
            "heights_from_drawings": bool((table or {}).get("sections"))}


def level_table(evidence_by_name):
    """quantity_levels table from the sheet evidence of section / elevation drawings (+ forms)."""
    from . import quantity_levels as levels
    from . import quantity_sheet_identity as identity
    cad = {}
    for name, rows in identity.resolve_cad_sheets(evidence_by_name).items():
        sheets = []
        for row in rows:
            found = levels.read_sheet_levels(identity.frame_texts(row, evidence_by_name[name]))
            if found:
                sheets.append(dict(row, levels=found))
        cad[name] = {"sheets": sheets}
    return levels.build_level_table(cad)


def _xref_names(doc):
    names = set()
    for block in doc.blocks:
        try:
            if block.block_record.is_xref:
                names.add(_key(re.split(r"[\\/]", block.block.dxf.get("xref_path", "") or block.name)[-1]))
        except Exception:
            continue
    return names


def _read(item):
    import ezdxf
    if item.get("raw"):
        return ezdxf.readfile(item["dxf"])
    with open(item["dxf"], encoding="utf-8", errors="replace") as handle:  # converter output: decoded text
        return ezdxf.read(handle)


def main(manifest_path, out_path):
    """Run from the web job: manifest = {"files": [{"name", "dxf", "raw"}], "plans": [names]}.

    One plan drawing at a time, with only the xrefs it inserts, so memory stays near the
    largest single plan (about 300-500 MB) instead of the whole upload.
    """
    import gc
    from . import quantity_sheet_identity as identity
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    items = {item["name"]: item for item in manifest.get("files", [])}
    plans = [name for name in manifest.get("plans") or [] if name in items]
    others = {_key(name): item for name, item in items.items() if name not in plans}
    forms, failed = {}, []
    for key, item in others.items():
        if "form" not in key:
            continue
        try:
            doc = _read(item)
        except Exception:
            failed.append(item["name"])
            continue
        evidence = identity.extract_sheet_evidence(doc)
        evidence["inserts"] = []  # only its title-block template is used
        forms[item["name"]] = evidence
        del doc
    level_evidence = dict(forms)
    for name in manifest.get("levels") or []:
        if name not in items:
            continue
        try:
            doc = _read(items[name])
            level_evidence[name] = identity.extract_sheet_evidence(doc)
        except Exception:
            failed.append(name)
            continue
        del doc
    try:
        table = level_table(level_evidence)
    except Exception:
        table = {}
    views, warnings, model_plans = [], [], []
    for name in plans:
        try:
            doc = _read(items[name])
        except Exception:
            failed.append(name)
            continue
        evidence = dict(forms)
        evidence[name] = identity.extract_sheet_evidence(doc)
        rows = identity.resolve_cad_sheets(evidence).get(name, [])
        xrefs = {}
        for ref in _xref_names(doc):
            if ref in others and "form" not in ref:
                try:
                    xrefs[ref] = _read(others[ref])
                except Exception:
                    failed.append(others[ref]["name"])
        missing = sorted(ref for ref in _xref_names(doc) if ref not in xrefs and "form" not in ref)
        if missing:
            warnings.append({"file": name, "missing_xrefs": missing})
        views.extend(_plan_views(name, doc, rows, xrefs))
        structural = [row for row in rows if row.get("frame_bbox") and is_plan_title(row.get("drawing_title"))
                      and floor_of(row["drawing_title"])["discipline"] == "struct"]
        if structural:
            from ezdxf.math import Matrix44
            members = []
            _walk(doc, doc.modelspace(), Matrix44(), None, xrefs, members, classify=model_category)
            for row in structural:
                model_plans.append({"floor": floor_of(row["drawing_title"]), "sheet": row.get("drawing_number"),
                                    "model": _plan_model(members, row["frame_bbox"])})
            del members
        del doc, xrefs, evidence
        gc.collect()
    result = _finish(views, warnings)
    result["model"] = build_model(model_plans, table)
    result["levels"] = table
    result["unreadable"] = sorted(set(failed))
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
