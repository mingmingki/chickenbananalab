"""Floor-by-floor colored plans for the upload check: walls by material, from the CAD itself.

Every plan sheet (title-block frame whose title says 평면도) is drawn from its own lines plus
the xref drawings it inserts, and each line is colored by what its layer says it is:
concrete (골조), masonry (조적), ALC, dry walls (건식벽) and openings (창호). Text, hatches,
dimensions, center lines and everything else is left out, which keeps a sheet near 300 KB.

Pure module (no Django): the web job converts the DWGs and runs `main()` in a separate
low-priority process so the drawing walk never holds the web worker's memory.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import sys
import unicodedata
from collections import defaultdict

FLOOR_VIEWS_VERSION = 1
CATEGORIES = ("frame", "beam", "masonry", "alc", "dry", "opening", "other")
CATEGORY_LABELS = {"frame": "골조(콘크리트)", "beam": "보", "masonry": "조적", "alc": "ALC",
                   "dry": "건식벽", "opening": "창호", "other": "분류 안 된 선"}
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
MAX_OTHER_SUBPATHS = 5000  # unclassified lines shown faintly on structural plans, to trace a missed member
MIN_OTHER_MM = 150.0      # shorter unclassified lines (hatching, symbols) are left out
MIN_EXTENT_MM = 30.0


def _nfc(text):
    return unicodedata.normalize("NFC", str(text or ""))


def layer_category(layer):
    """Material category of a layer name (xref layers 'XREF|name' use the part after '|')."""
    rule = _layer_rule(layer)
    return None if rule == "skip" else rule


def _layer_rule(layer):
    base = _nfc(layer).split("|")[-1]
    for name, rule in _LAYER_RULES:
        if rule.search(base):
            return name
    return None


def plan_key(name, row):
    """Stable id of one plan frame of one file: links its picture, its 3D storey and people's fixes."""
    import hashlib
    box = ",".join(str(round(float(v))) for v in row.get("frame_bbox") or [])
    raw = f"{name}|{row.get('drawing_number') or ''}|{box}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def is_plan_title(title):
    text = re.sub(r"\s+", "", _nfc(title))
    return "평면도" in text and not _PLAN_EXCLUDE.search(text)


FACILITY_NAME = re.compile(r"관리사무소|관리동|경로당|커뮤니티|주민공동|주민운동|근린생활|근생|어린이집|보육|도서관|"
                           r"피트니스|휘트니스|독서실|경비실|상가|복리")


def _facility_name(raw):
    """'관리사무소 및 경로당 1층 구조평면도' → '관리사무소 및 경로당' (the floor and sheet words left out)."""
    name = re.sub(r"\(.*?\)|(구조)?평면도|구조도|지하\s*\d+\s*층|지상\s*\d+\s*층|\d+\s*층|PIT\s*층|피트\s*층|"
                  r"기초|지붕|옥탑|기준층|[~∼]", " ", raw)
    name = re.sub(r"\s+", " ", name).strip(" ,·-")
    return name or "부대시설"


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
    elif "부대" in text:
        building = "부대시설"
    elif FACILITY_NAME.search(text):
        building = _facility_name(raw)
    if building == "단위세대":
        unit = re.search(r"(\d{2,3}[A-Z0-9]*?)(형|타입|TYPE)", text, re.I)
        extra = re.search(r"\((기본형|확장형)\)", text)
        label = (unit.group(1) + "형" if unit else "단위세대") + (" " + extra.group(1) if extra else "")
        return {"building": building, "floor": label, "group": "unit", "order": 0, "discipline": discipline}
    # "4~23층" / "4-23층" / "지상4~지상23층": a range whose first number has no 층
    span = re.search(r"(지하|지상)?(\d+)층?[~∼\-](지하|지상)?(\d+)층", text)
    floors = re.findall(r"(지하|지상)?(\d+)층", text)
    if span and (span.group(1) or "") != "지하" and (span.group(3) or "") != "지하":
        floors = [(span.group(1) or "", span.group(2)), (span.group(3) or "", span.group(4))]
    if "기초" in text:
        return {"building": building or "기타", "floor": "기초", "group": "basement", "order": -99, "discipline": discipline}
    if building and building.endswith("동") and re.search(r"(지하)?주차장지붕", text) and not re.search(r"\d+층", text):
        # a tower's "parking deck roof" plan is its ground-floor slab: the tower's 1st floor
        return {"building": building, "floor": "1층", "group": "ground", "order": 1, "discipline": discipline}
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
_FORM_KEY = re.compile(r"form|시트|sheet|도곽|양식|title|border", re.I)


def is_form_key(key):
    """A title-block xref (its name says form, sheet, 시트, 도곽...): used to find sheets, never members."""
    return bool(_FORM_KEY.search(key or ""))


# ── drawing walk ──────────────────────────────

def _quick_corners(entity, kind):
    """A few points spanning a plain entity, in its own coordinates (no curve flattening)."""
    try:
        if kind == "LINE":
            a, b = entity.dxf.start, entity.dxf.end
            return [(a.x, a.y), (b.x, b.y)]
        if kind == "LWPOLYLINE":
            points = entity.get_points("xy")
            if not points:
                return None
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            return [(min(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys)), (max(xs), min(ys))]
        if kind in ("ARC", "CIRCLE"):
            c, r = entity.dxf.center, entity.dxf.radius
            return [(c.x - r, c.y - r), (c.x + r, c.y + r), (c.x - r, c.y + r), (c.x + r, c.y - r)]
    except Exception:
        return None
    return None


def _outside_limit(corners, matrix, limit):
    xs, ys = [], []
    for x, y in corners:
        p = matrix.transform((x, y, 0))
        xs.append(p.x)
        ys.append(p.y)
    return max(xs) < limit[0] or min(xs) > limit[2] or max(ys) < limit[1] or min(ys) > limit[3]


def _walk(doc, entities, matrix, inherit, xrefs, out, depth=0, classify=None, describe=None, kinds=None, limit=None):
    """Member paths in drawing coordinates. ``limit`` (a drawing-coordinate box, e.g. the plan
    frames or an xref's clip window) skips plain entities that cannot reach it before any curve is
    flattened, which is most of a site-wide xref inserted once per sheet."""
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
            inner = limit
            if clip and not clip[1]:
                xs, ys = [p[0] for p in clip[0]], [p[1] for p in clip[0]]
                box = (min(xs), min(ys), max(xs), max(ys))
                inner = box if limit is None else (max(box[0], limit[0]), max(box[1], limit[1]),
                                                   min(box[2], limit[2]), min(box[3], limit[3]))
            if block.block_record.is_xref:
                path = block.block.dxf.get("xref_path", "") or entity.dxf.name
                target = xrefs.get(_key(re.split(r"[\\/]", path)[-1])) or xrefs.get(_key(entity.dxf.name))
                if target is not None and not is_form_key(_key(path)):
                    _walk(target, target.modelspace(), placed, None, xrefs, target_out, depth + 1, classify, describe, kinds, inner)
            else:
                _walk(doc, block, placed, layer, xrefs, target_out, depth + 1, classify, describe, kinds, inner)
            if clip:
                polygon, inverted = clip
                out.extend(item for item in target_out if _kept_by_clip(item, polygon, inverted))
            continue
        if kind not in (kinds or ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE")):
            continue
        category = describe(entity, doc, layer) if describe else (classify or layer_category)(layer)
        if category is None:
            continue
        if limit is not None:
            corners = _quick_corners(entity, kind)
            if corners and _outside_limit(corners, matrix, limit):
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


def _frames_limit(rows):
    """The box around a drawing's plan frames (None when a frame is the whole drawing)."""
    boxes = [row["frame_bbox"] for row in rows if row.get("frame_bbox")]
    if not boxes or any(b[2] - b[0] > 1e11 for b in boxes):
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


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


def _svg(paths, box, other=True):
    """Plan picture of one frame (1 unit = 10 mm, y down). A path category may be (category, layer):
    the path then carries the layer, so a person picking a line in the editor learns its layer."""
    x0, y0, x1, y1 = box
    width, height = (x1 - x0) / 10.0, (y1 - y0) / 10.0
    groups, counts, total, others = defaultdict(list), defaultdict(int), 0, 0
    ordered = sorted(paths, key=lambda p: (p[0][0] if isinstance(p[0], tuple) else p[0]) == "other")
    for category, ax, ay, bx, by, points in ordered:
        if bx < x0 or ax > x1 or by < y0 or ay > y1:
            continue
        if total >= MAX_SUBPATHS_PER_VIEW:
            break
        kind = category[0] if isinstance(category, tuple) else category
        if kind == "other":
            if not other or others >= MAX_OTHER_SUBPATHS or max(bx - ax, by - ay) < MIN_OTHER_MM:
                continue
            others += 1
        coords, last = [], None
        for px, py in points:
            pair = (round((px - x0) / 10.0), round((y1 - py) / 10.0))
            if pair != last:
                coords.append(f"{pair[0]} {pair[1]}")
                last = pair
        if len(coords) < 2:
            continue
        groups[category].append("M" + "L".join(coords))
        counts[kind] += 1
        total += 1

    def attr(text):
        return html.escape(str(text), quote=True)
    rank = {c: i for i, c in enumerate(CATEGORIES)}
    body = "".join(
        f'<path class="fv-{c[0]}" data-layer="{attr(c[1])}" d="{"".join(groups[c])}"/>' if isinstance(c, tuple)
        else f'<path class="fv-{c}" d="{"".join(groups[c])}"/>'
        for c in sorted(groups, key=lambda c: (rank.get(c[0] if isinstance(c, tuple) else c, 99), str(c))))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
           f'preserveAspectRatio="xMidYMid meet">{body}</svg>')
    return svg, dict(counts)


VIEW_OF_ROLE = {"wall": "frame", "column": "frame", "beam": "beam"}


def _plan_views(name, doc, rows, xrefs, roles=None):
    """Views of one drawing's plan frames; `xrefs` maps reference keys to loaded documents.
    ``roles`` (layer → member role, read from the drawing) colours the structural members of an
    office whose layer names the name rules do not know."""
    from ezdxf.math import Matrix44
    plans = [row for row in rows if row.get("frame_bbox") and is_plan_title(row.get("drawing_title"))]
    if not plans:
        return []
    from . import quantity_layer_roles as layer_roles

    def classify(layer):
        base = _nfc(layer).split("|")[-1]
        role = (roles or {}).get(base)
        category = VIEW_OF_ROLE.get(role) if role else layer_category(layer)
        if category is None:
            # not a member as read: shown faintly (a missed beam can be traced), except sheet clutter
            if _layer_rule(layer) == "skip" or layer_roles.surely_not_member(base):
                return None
            category = "other"
        return (category, layer_roles.base_name(base))
    paths = []
    _walk(doc, doc.modelspace(), Matrix44(), None, xrefs, paths, classify=classify, limit=_frames_limit(plans))
    whole = [row for row in plans if row["frame_bbox"][2] - row["frame_bbox"][0] > 1e11]
    if whole and paths:
        # a one-sheet drawing read without a frame: the drawn extent, ignoring a few stray objects
        xs = sorted(v for p in paths for v in (p[1], p[3]))
        ys = sorted(v for p in paths for v in (p[2], p[4]))
        cut = lambda values, q: values[min(len(values) - 1, max(0, int(q * (len(values) - 1))))]
        box = [cut(xs, 0.01), cut(ys, 0.01), cut(xs, 0.99), cut(ys, 0.99)]
        for row in whole:
            row["frame_bbox"] = [round(v, 3) for v in box]
    views = []
    for row in plans:
        structural = floor_of(row["drawing_title"])["discipline"] == "struct"
        svg, counts = _svg(paths, row["frame_bbox"], other=structural)
        if not any(v for k, v in counts.items() if k != "other"):
            continue
        floor = floor_of(row["drawing_title"])
        variant = "구조평면" if floor["discipline"] == "struct" else "건축평면"
        if "보완" in _nfc(name):
            variant += " (보완)"
        views.append(dict(floor, file=name, sheet=row.get("drawing_number"), title=row.get("drawing_title"),
                          variant=variant, counts=counts, svg=svg, plan_id=plan_key(name, row)))
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
        missing = sorted(ref for ref in _xref_names(docs[name]) if ref not in xrefs and not is_form_key(ref))
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
    ("stair", re.compile(r"(^|[_-])a-stair$", re.I)),
    ("above", re.compile(r"str_conc_above$", re.I)),
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
    import numpy as np
    from .quantity_floor_takeoff import _and, _dil4, _erode4, _fill, _not, _raster
    if not segments:
        return [], (lambda x, y: True)
    xs = [v for seg in segments for v in (seg[0], seg[2])]
    ys = [v for seg in segments for v in (seg[1], seg[3])]
    minx, miny = min(xs) - 1.0, min(ys) - 1.0
    width, height = max(xs) + 1.0 - minx, max(ys) + 1.0 - miny
    cell = max(cell, width / 1200.0, height / 1200.0)
    nx, ny = int(width / cell) + 1, int(height / cell) + 1
    grid = _raster([tuple(seg[:4]) for seg in segments], minx, miny, cell, (ny, nx))
    for _ in range(close_cells):
        grid = _dil4(grid)
    open_cells = _not(grid)
    border = np.zeros((ny, nx), dtype=bool)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    outside = _fill(_and(border, open_cells), open_cells)
    # the closing grew the walls outward; shrink the floor back by the same amount
    floor = _not(outside)
    for _ in range(close_cells):
        floor = _erode4(floor)
    rects, open_runs = [], {}
    for gy in range(ny):
        row = floor[gy]
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
        runs = [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]
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
        """Outside the floor as finally shaped (after the gap-closing growth is taken back)."""
        gx, gy = int((x - minx) / cell), int((y - miny) / cell)
        if gx < 0 or gy < 0 or gx >= nx or gy >= ny:
            return True
        return not floor[gy, gx]

    def is_outside_grown(x, y):
        """Outside the grown lines (used with a probe longer than the growth)."""
        gx, gy = int((x - minx) / cell), int((y - miny) / cell)
        if gx < 0 or gy < 0 or gx >= nx or gy >= ny:
            return True
        return bool(outside[gy, gx])

    is_outside.grown = is_outside_grown
    is_outside.reach = close_cells * cell

    return ([[r(minx + a * cell), r(miny + y0 * cell), r(minx + b * cell), r(miny + y1 * cell)]
             for a, y0, b, y1 in rects if (b - a) * (y1 - y0) * cell * cell >= 0.1], is_outside)


def _plan_model(paths, box):
    """Members of one structural frame in metres, frame-local (x right, y up), plus the takeoff
    measures (wall section area, faces, openings) taken on the unrounded lines."""
    from . import quantity_floor_takeoff as takeoff
    x0, y0, x1, y1 = box
    r = lambda v: round(v, 2)
    walls, beams, edges, cols, opens = [], [], [], [], []
    raw = {"wall": [], "col": [], "open": [], "stair": [], "above": []}
    for category, ax, ay, bx, by, points in paths:
        if bx < x0 or ax > x1 or by < y0 or ay > y1:
            continue
        local = [((px - x0) / 1000.0, (py - y0) / 1000.0) for px, py in points]
        closed = len(local) >= 4 and abs(local[0][0] - local[-1][0]) < 0.005 and abs(local[0][1] - local[-1][1]) < 0.005
        if category == "col" and closed and _wall_outline(local):
            category = "wall"  # some offices draw each wall piece as a closed outline
        if category == "col" and closed:
            cols.append([[r(x), r(y)] for x, y in local[:-1]])
            raw["col"].append(local[:-1])
            continue
        if category in ("stair", "above"):
            raw[category].append(local)
            continue
        if category in ("wall", "col"):
            raw["wall"].append(local)
        elif category == "open":
            raw["open"].append(local)
        target = {"wall": walls, "col": walls, "beam": beams, "outline": edges, "open": opens}[category]
        for (ax_, ay_), (bx_, by_) in zip(local, local[1:]):
            if abs(ax_ - bx_) + abs(ay_ - by_) < 0.05:
                continue
            target.append(((ax_, ay_), (bx_, by_)) if category == "outline" else [r(ax_), r(ay_), r(bx_), r(by_)])
    outline = [[a[0], a[1], b[0], b[1]] for a, b in edges]
    edge_lines = walls + outline + [[c[i][0], c[i][1], c[(i + 1) % len(c)][0], c[(i + 1) % len(c)][1]]
                                    for c in cols for i in range(len(c))]
    slab, is_outside = _footprint(edge_lines)
    _flag_outer_faces(walls, is_outside, append=True)
    area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in slab)
    hull = _hull([(w[0], w[1]) for w in edge_lines] + [(w[2], w[3]) for w in edge_lines])
    hull_area = abs(_area(hull)) if len(hull) >= 3 else 0.0
    model = {"walls": walls, "beams": beams, "cols": cols, "slab": slab, "opens": opens,
             "slab_area": round(area, 1), "slab_approximate": False}
    sub_slabs = []
    if hull_area and area < 0.35 * hull_area:
        # windows break the outer wall lines and some offices draw no slab edge: bridge gaps up to
        # about 3 m around the main group of lines (a key map or a stray line is left out)
        main = _main_cluster([line[:4] for line in edge_lines])
        main_hull = _hull([(w[0], w[1]) for w in main] + [(w[2], w[3]) for w in main])
        main_hull_area = abs(_area(main_hull)) if len(main_hull) >= 3 else 0.0
        bridged, bridged_outside = _footprint(main, close_cells=10)
        bridged_area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in bridged)
        if main_hull_area and bridged_area >= 0.5 * main_hull_area:
            slab, is_outside, area = bridged, bridged_outside, bridged_area
            hull, hull_area = main_hull, main_hull_area
            _flag_outer_faces(walls, is_outside, append=False)  # again, against the bridged outline
            model.update(slab=slab, slab_area=round(area, 1), slab_bridged=True)
    if hull_area and area < 0.35 * hull_area:
        # one frame may hold several small plans side by side (roof, penthouse, penthouse roof)
        sub_slabs = _separate_slabs(edge_lines)
        # an open outline (basements joined to a parking deck): the outer hull stands in
        model.update(slab=[], slab_polygon=[[r(x), r(y)] for x, y in hull], slab_area=round(hull_area, 1),
                     slab_approximate=True)
    if len(sub_slabs) >= 2:
        model["sub_slabs"] = sub_slabs
    try:
        model["measure"] = takeoff.measure_plan(raw["wall"], raw["col"], raw["open"], raw["stair"],
                                                None if model["slab_approximate"] else is_outside,
                                                above=raw["above"], slab_rects=slab)
        model["hull_area"] = round(hull_area, 1)
        model["measure"]["outline"] = round(sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in edges), 1)
    except Exception as exc:  # the colored plans and the 3D model must not depend on the takeoff
        model["measure"] = {"error": str(exc)[:200]}
    return model


WALL_OUTLINE_MAX_T = 0.35  # m: a closed outline this thin ...
WALL_OUTLINE_MIN_RATIO = 4.0  # ... and this many times longer than thick is a wall piece, not a column


def _wall_outline(points):
    """A closed outline shaped like a wall piece: thin (2·area/perimeter, the thickness of a
    long strip) and long for its thickness. Columns are squat."""
    pts = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    if len(pts) < 4:
        return False
    area = abs(_area(pts))
    perimeter = sum(math.hypot(pts[(i + 1) % len(pts)][0] - pts[i][0], pts[(i + 1) % len(pts)][1] - pts[i][1]) for i in range(len(pts)))
    if area <= 0 or perimeter <= 0:
        return False
    thickness = 2 * area / perimeter
    length = perimeter / 2 - thickness
    return thickness <= WALL_OUTLINE_MAX_T and length >= WALL_OUTLINE_MIN_RATIO * thickness


def _flag_outer_faces(walls, is_outside, append):
    """5th value 1: an exterior face (one side of it is outside the building). The probe reaches
    past the gap-closing growth, so only an outer face has open air on one side."""
    outside = getattr(is_outside, "grown", is_outside)
    reach = max(0.5, getattr(is_outside, "reach", 0.3) + 0.1)
    for wall in walls:
        ax, ay, bx, by = wall[:4]
        length = math.hypot(bx - ax, by - ay) or 1.0
        nx_, ny_ = (by - ay) / length * reach, -(bx - ax) / length * reach
        mx, my = (ax + bx) / 2, (ay + by) / 2
        flag = 1 if outside(mx + nx_, my + ny_) != outside(mx - nx_, my - ny_) else 0
        if append:
            wall.append(flag)
        else:
            wall[4] = flag


def _main_cluster(lines, gap=2.0):
    """The group of lines (joined within ``gap`` m) holding most of the drawn length."""
    if not lines:
        return []
    parent = list(range(len(lines)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    boxes = [(min(l[0], l[2]) - gap, min(l[1], l[3]) - gap, max(l[0], l[2]) + gap, max(l[1], l[3]) + gap) for l in lines]
    order = sorted(range(len(lines)), key=lambda i: boxes[i][0])
    active = []
    for i in order:
        active = [j for j in active if boxes[j][2] >= boxes[i][0]]
        for j in active:
            if boxes[j][1] <= boxes[i][3] and boxes[i][1] <= boxes[j][3]:
                parent[find(i)] = find(j)
        active.append(i)
    weight = defaultdict(float)
    for i, l in enumerate(lines):
        weight[find(i)] += math.hypot(l[2] - l[0], l[3] - l[1])
    root = max(weight, key=weight.get)
    return [l for i, l in enumerate(lines) if find(i) == root]


def _separate_slabs(lines, gap=3.0, min_lines=12):
    """Closed slab areas of plans drawn apart inside one frame, largest first (m²)."""
    parent = list(range(len(lines)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    boxes = [(min(l[0], l[2]) - gap, min(l[1], l[3]) - gap, max(l[0], l[2]) + gap, max(l[1], l[3]) + gap) for l in lines]
    order = sorted(range(len(lines)), key=lambda i: boxes[i][0])
    active = []
    for i in order:
        active = [j for j in active if boxes[j][2] >= boxes[i][0]]
        for j in active:
            if boxes[j][1] <= boxes[i][3] and boxes[i][1] <= boxes[j][3]:
                parent[find(i)] = find(j)
        active.append(i)
    groups = defaultdict(list)
    for i, line in enumerate(lines):
        groups[find(i)].append(line)
    areas = []
    for group in groups.values():
        if len(group) < min_lines:
            continue
        rects, _outside = _footprint([line[:4] for line in group])
        area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in rects)
        hull = _hull([(w[0], w[1]) for w in group] + [(w[2], w[3]) for w in group])
        hull_area = abs(_area(hull)) if len(hull) >= 3 else 0.0
        if area >= 5 and hull_area and area >= 0.35 * hull_area:
            areas.append(round(area, 1))
    return sorted(areas, reverse=True)


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
    if "1" in known and "1" not in keys and "R" in keys and not any(k.isdigit() for k in keys):
        known.setdefault("R", known["1"])  # a parking deck: its roof is the ground floor of the towers
    ordered = sorted(set(keys), key=_level_order)
    first = next((i for i, key in enumerate(ordered) if key in known), None)
    result = {}
    if first is not None:
        # below the first known level: each storey's default height downward from it
        result[ordered[first]] = known[ordered[first]]
        for i in range(first - 1, -1, -1):
            key = ordered[i]
            group = "basement" if key.startswith("B") else ("1" if key == "1" else "ground")
            result[key] = round(result[ordered[i + 1]] - DEFAULT_STOREY_M[group], 3)
    last = None
    for i, key in enumerate(ordered):
        if key in result and (first is None or i <= first):
            last = (key, result[key])
            continue
        if key in known:
            result[key] = known[key]
        elif last is not None:
            prev_key, prev_elev = last
            group = "basement" if prev_key.startswith("B") else ("1" if prev_key == "1" else "ground")
            result[key] = round(prev_elev + DEFAULT_STOREY_M[group], 3)
        else:
            result[key] = 0.0
        last = (key, result[key])
    return result


def build_model(plans, table, site=True):
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
    model = {"version": 1, "buildings": buildings, "heights_from_drawings": bool((table or {}).get("sections"))}
    if site:
        from . import quantity_site
        try:
            model["site"] = quantity_site.assemble(buildings)
        except Exception as exc:  # the 3D layout must never cost the takeoff
            model["site"] = {"error": str(exc)[:200]}
    return model


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


ROLE_CATEGORY = {"wall": "wall", "column": "col", "beam": "beam", "outline": "outline", "opening": "open",
                 "stair": "stair", "wall_above": "above"}


def _describe(entity, doc, layer):
    from . import quantity_layer_roles as roles
    name = _nfc(layer).split("|")[-1]
    if roles.surely_not_member(name):
        return None  # dimensions, text, symbols: not worth turning into paths
    return (name, roles.linetype_class(roles.entity_linetype(entity, doc)))


def structural_members(doc, xrefs, frames, profiles=None):
    """Member lines of a drawing's structural frames by layer role (geometry first, names as a hint).

    Returns (members for _plan_model, role report). Falls back to the layer-name rules when no
    layer reads as a cut wall. A saved office profile (``profiles``, from people's corrections)
    whose layer names match this drawing's gives its roles first."""
    from ezdxf.math import Matrix44
    from . import quantity_layer_roles as roles
    limit = None if any(x1 - x0 > 1e11 for x0, y0, x1, y1 in frames) else (
        min(f[0] for f in frames), min(f[1] for f in frames), max(f[2] for f in frames), max(f[3] for f in frames))
    raw = []  # first look: straight lines only, enough to tell walls from beams and hatches
    _walk(doc, doc.modelspace(), Matrix44(), None, xrefs, raw, describe=_describe,
          kinds=("LINE", "LWPOLYLINE", "POLYLINE"), limit=limit)

    def in_frames(ax, ay, bx, by):
        return any(not (bx < x0 or ax > x1 or by < y0 or ay > y1) for x0, y0, x1, y1 in frames)
    inside = [(key[0], key[1], points) for key, ax, ay, bx, by, points in raw if in_frames(ax, ay, bx, by)]
    del raw
    from . import quantity_brain as brain
    names = {roles.base_name(layer) for layer, _lt, _points in inside}
    profile = brain.match(profiles, names) if profiles else None
    known = {name: role for name, role in (profile or {}).get("layers", {}).items() if name in names} if profile else None
    judged = roles.judge(inside, known=known)
    del inside
    category_of = {layer: ROLE_CATEGORY[info["role"]] for layer, info in judged.items() if info["role"] in ROLE_CATEGORY}
    method = "geometry"
    if "wall" not in category_of.values():
        category_of, method = None, "layer_names"
    members = []  # second look: the member layers only, arcs included
    _walk(doc, doc.modelspace(), Matrix44(), None, xrefs, members,
          classify=(lambda layer: category_of.get(_nfc(layer).split("|")[-1])) if category_of is not None else model_category,
          limit=limit)
    report = [{"layer": layer, "role": info["role"], "reason": info["reason"], "by": info["by"], "m": info["m"],
               "linetype": info["linetype"], "pair": info["pair"], "width": info["width"]}
              for layer, info in sorted(judged.items(), key=lambda kv: -kv[1]["m"])
              if info["role"] or info["m"] >= 50][:40]
    used = {"id": profile["id"], "label": profile.get("label") or "", "shared": len(known or {})} if profile else None
    member_roles = {layer: info["role"] for layer, info in judged.items() if info["role"]} if category_of is not None else {}
    return members, {"method": method, "layers": report, "profile": used}, member_roles


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
    # structural plans first: the 3D model and the takeoff need them, the colours can wait
    plans.sort(key=lambda name: (0 if _is_structural_file(name) else 1, _nfc(name)))
    others = {_key(name): item for name, item in items.items() if name not in plans}
    forms, failed = {}, []
    for key, item in others.items():
        if not is_form_key(key):
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
    views, warnings, model_plans, layer_roles = [], [], [], {}
    xref_cache = {}  # the next sheet set of a series usually inserts the same xrefs
    for name in plans:
        try:
            doc = _read(items[name])
        except Exception:
            failed.append(name)
            continue
        evidence = dict(forms)
        evidence[name] = identity.extract_sheet_evidence(doc)
        rows = identity.resolve_cad_sheets(evidence).get(name, [])
        wanted = {ref for ref in _xref_names(doc) if ref in others and not is_form_key(ref)}
        for ref in [ref for ref in xref_cache if ref not in wanted]:
            del xref_cache[ref]  # only the xrefs of this drawing stay loaded
        gc.collect()
        for ref in sorted(wanted - set(xref_cache)):
            try:
                xref_cache[ref] = _read(others[ref])
            except Exception:
                failed.append(others[ref]["name"])
        xrefs = {ref: xref_cache[ref] for ref in wanted if ref in xref_cache}
        missing = sorted(ref for ref in _xref_names(doc) if ref not in xrefs and not is_form_key(ref))
        if missing:
            warnings.append({"file": name, "missing_xrefs": missing})
        structural = [row for row in rows if row.get("frame_bbox") and is_plan_title(row.get("drawing_title"))
                      and floor_of(row["drawing_title"])["discipline"] == "struct"]
        members, member_roles = [], {}
        if structural:
            members, role_report, member_roles = structural_members(
                doc, xrefs, [row["frame_bbox"] for row in structural], manifest.get("profiles"))
            layer_roles[name] = role_report
        views.extend(_plan_views(name, doc, rows, xrefs, member_roles))  # also settles one-sheet frames
        for row in structural:
            model = _plan_model(members, row["frame_bbox"])
            model["id"] = plan_key(name, row)
            model_plans.append({"floor": floor_of(row["drawing_title"]), "sheet": row.get("drawing_number"), "model": model})
        del members
        del doc, xrefs, evidence
        gc.collect()
        _write(out_path + ".partial", _assemble(views, warnings, model_plans, table, failed, layer_roles, partial=True))
    _write(out_path, _assemble(views, warnings, model_plans, table, failed, layer_roles))
    try:
        os.remove(out_path + ".partial")
    except OSError:
        pass


def _is_structural_file(name):
    text = re.sub(r"\s+", "", _nfc(os.path.basename(name)))
    return "구조" in text or bool(re.match(r"^S\d*[-_ ]", text, re.I))


def _assemble(views, warnings, model_plans, table, failed, layer_roles, partial=False):
    result = _finish(list(views), list(warnings))
    result["model"] = build_model(model_plans, table, site=not partial)
    result["levels"] = table
    result["unreadable"] = sorted(set(failed))
    result["layer_roles"] = layer_roles
    if partial:
        result["partial"] = True
    return result


def _write(path, result):
    """Atomic write, so a reader never sees half a file (the web job may read the partial one)."""
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    os.replace(temporary, path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
