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

def _walk(doc, entities, matrix, inherit, xrefs, out, depth=0):
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
                    _walk(target, target.modelspace(), placed, None, xrefs, target_out, depth + 1)
            else:
                _walk(doc, block, placed, layer, xrefs, target_out, depth + 1)
            if clip:
                polygon, inverted = clip
                out.extend(item for item in target_out if _kept_by_clip(item, polygon, inverted))
            continue
        if kind not in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
            continue
        category = layer_category(layer)
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
    views, warnings = [], []
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
        del doc, xrefs, evidence
        gc.collect()
    result = _finish(views, warnings)
    result["unreadable"] = sorted(set(failed))
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
