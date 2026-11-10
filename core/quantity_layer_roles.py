"""What each CAD layer of a structural plan draws, judged from its geometry with the name as a hint.

Offices name layers in their own ways ("0_STR_CONC", "A-콘크리트", "WAL", "COL", even "0"), so the
role of a layer is read from what is on it inside the structural plan frames:

* line type: solid lines are cut (walls, columns); dashed lines are hidden (walls below or above,
  beams under the slab); centre lines are grids;
* pairs: wall and beam faces come in parallel pairs 100-1300 mm apart; stair treads and hatches
  are ladders of many equally spaced lines;
* closed outlines 150-2500 mm wide are columns;
* hidden pairs that lie on the cut walls are the walls below; the others are beams.

Layer names only add weight or rule a layer out (dimensions, text, masonry, dry walls, furniture),
because geometry alone takes a hatch or a dry wall for concrete. Every decision keeps its reason so
the user can check it, and a confirmed answer can be saved for the next drawing of the same office.
"""
from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict

from . import quantity_geometry as geometry

ROLES = ("wall", "wall_below", "wall_above", "column", "beam", "outline", "opening", "stair")
ROLE_LABELS = {"wall": "벽(자른 면)", "wall_below": "아래층 벽(점선)", "wall_above": "위층 벽(점선)", "column": "기둥",
               "beam": "보", "outline": "슬래브 외곽", "opening": "개구부", "stair": "계단"}

_EXCLUDE = re.compile(
    r"DEF|DIM|치수|TEXT|TXT|문자|TIT|SYM|기호|BUBBLE|CEN|GRID|중심|HAT|해치|FORM|도곽|TITLE|CLOUD|REV|NOTE|주기|"
    r"LEAD|ELEV|(^|[^A-Z])EV([^A-Z]|$)|승강|FUR|가구|SANI|위생|WIN|DOOR|창호|PAPER|(^|[^A-Z])XC([^A-Z]|$)|AREA|면적|"
    r"CHK|체크|보조선|RAIL|난간|조경|배치|주차구획|SHEET|딱지|NAME|MARK|TAG|부호|명칭|SECTION|STIRRUP|배근|철근|"
    r"상부근|하부근|보강근|REBAR|BAR|LIST|표|CLOUD|사업경계|대지|경계선|신청|NO.?PRINT|VIEWPORT|VPORT",
    re.I)
_MASONRY = re.compile(r"BRICK|벽돌|조적|BLOCK|블록|블럭|DRY|건식|경량|ALC|석고|GYPS|PARTITION|칸막이|MASONRY", re.I)
# never members even when the name also says wall or column (a wall hatch, a punching-shear outline)
_NEVER = re.compile(r"HATCH|해치|위험\s*단면|PATTERN|패턴|기초|FOOTING|FTG|FDN", re.I)
# layers of a title-block xref ("칠성24지구 sheet$0$...", "XR-FORM$0$..."): never members
_SHEET_XREF = re.compile(r"SHEET|FORM|시트|도곽|양식|TITLE|BORDER", re.I)
_STAIR = re.compile(r"STAIR|계단|(^|[^A-Z])STA($|[^A-Z])", re.I)
_OPEN = re.compile(r"OPEN|개구|SHAFT|DUCT|덕트", re.I)
_OUTLINE = re.compile(r"OUTLINE|외곽|EDGE|단부|SLAB|슬래브|LEVEL|레벨", re.I)
_BELOW = re.compile(r"BELOW|하부|아래|LOWER|HID", re.I)
_ABOVE = re.compile(r"ABOVE|상부|위층|UPPER", re.I)
_BEAM = re.compile(r"BEAM|GIRDER|GDR|거더|(^|[^가-힣A-Z])보($|[^가-힣])|(^|[^A-Z])(TG|WG|BM)\d*($|[^A-Z])", re.I)
_WALL = re.compile(r"CONC|콘크리트|WALL|WAL|벽|CORE|코어|옹벽|(^|[^A-Z])RC($|[^A-Z])", re.I)
_COLUMN = re.compile(r"COL|기둥", re.I)

_DASHED = re.compile(r"HID|DASH|DOT|PHANTOM|숨은|파선|^D\d|^DASHED", re.I)
_CENTER = re.compile(r"CEN|CENTER|중심|DASHDOT|CHAIN", re.I)

MIN_LAYER_M = 5.0
MAX_SEGMENTS = 30000


def _nfc(text):
    return unicodedata.normalize("NFC", str(text or ""))


def from_sheet(layer):
    """A layer brought in by a title-block or sheet xref."""
    name = _nfc(layer).split("|")[-1]
    return "$0$" in name and bool(_SHEET_XREF.search(name.split("$0$")[0]))


def base_name(layer):
    """The layer's own name: without an xref prefix ("XR-구조101동$0$A-CONC" → "A-CONC")."""
    name = _nfc(layer).split("|")[-1]
    return name.split("$0$")[-1] if "$0$" in name else name


def linetype_class(linetype):
    text = _nfc(linetype).split("$0$")[-1].upper()
    if _CENTER.search(text):
        return "center"
    if _DASHED.search(text):
        return "dashed"
    return "solid"


def entity_linetype(entity, doc):
    """The linetype an entity is drawn with (its own, else its layer's)."""
    linetype = (entity.dxf.get("linetype") or "BYLAYER").upper()
    if linetype in ("BYLAYER", "BYBLOCK"):
        name = entity.dxf.get("layer", "0")
        try:
            layer = doc.layers.get(name)
        except Exception:
            layer = None
        linetype = ((layer.dxf.get("linetype") if layer is not None else None) or "CONTINUOUS").upper()
    return linetype


def _segments(paths):
    out = []
    for points in paths:
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            if math.hypot(bx - ax, by - ay) >= 50:
                out.append((ax, ay, bx, by))
    return out[:MAX_SEGMENTS]


def surely_not_member(layer):
    """Layers whose name alone rules them out (text, dimensions, symbols), unless it also names a member."""
    name = base_name(layer)
    if _NEVER.search(name) or from_sheet(layer):
        return True
    if _STAIR.search(name) or _OPEN.search(name) or _OUTLINE.search(name):
        return False
    return bool(_EXCLUDE.search(name)) and not (_WALL.search(name) or _BEAM.search(name) or _COLUMN.search(name))


def layer_features(items):
    """Features per layer. items: [(layer, linetype, points in mm)] inside the structural frames."""
    grouped = defaultdict(list)
    for layer, linetype, points in items:
        grouped[layer].append((linetype, points))
    features = {}
    for layer, rows in grouped.items():
        total = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for _lt, pts in rows for p, q in zip(pts, pts[1:]))
        if total < MIN_LAYER_M * 1000:
            continue
        closed = 0
        for _lt, pts in rows:
            if len(pts) >= 4 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 5:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                if 150 <= max(xs) - min(xs) <= 2500 and 150 <= max(ys) - min(ys) <= 2500:
                    closed += 1
        segs = _segments([pts for _lt, pts in rows])
        pieces = geometry.pieces(segs, "beams") if segs else []
        paired = sum(p["t2"] - p["t1"] for p in pieces)
        partners = Counter()
        for piece in pieces:  # lines met from both sides at one spacing: ladders (treads, hatches)
            partners[(round(piece["angle"]), round(piece["offset"] - piece["width"] / 2))] += 1
            partners[(round(piece["angle"]), round(piece["offset"] + piece["width"] / 2))] += 1
        ladder = sum(1 for count in partners.values() if count >= 2) / len(partners) if partners else 0.0
        widths = [p["width"] for p in pieces for _ in range(max(1, int((p["t2"] - p["t1"]) / 500)))]
        kinds = Counter(linetype_class(lt) for lt, _pts in rows)
        features[layer] = {
            "m": round(total / 1000, 1), "count": len(rows), "closed": closed,
            "linetype": kinds.most_common(1)[0][0], "pair": round(min(1.0, 2 * paired / total), 2),
            "width": round(statistics.median(widths)) if widths else None, "ladder": round(ladder, 2),
        }
    return features


def _coincidence(segments, wall_segments, tolerance=40.0):
    """Share of a layer's line length lying on the cut wall lines (walls below sit under walls)."""
    if not segments or not wall_segments:
        return 0.0
    grid = defaultdict(list)
    for index, (ax, ay, bx, by) in enumerate(wall_segments):
        for gx in range(int(min(ax, bx) // 2000), int(max(ax, bx) // 2000) + 1):
            for gy in range(int(min(ay, by) // 2000), int(max(ay, by) // 2000) + 1):
                grid[(gx, gy)].append(index)
    on, total = 0.0, 0.0
    for ax, ay, bx, by in segments[:5000]:
        length = math.hypot(bx - ax, by - ay)
        total += length
        mx, my = (ax + bx) / 2, (ay + by) / 2
        for index in grid.get((int(mx // 2000), int(my // 2000)), ()):
            cx, cy, dx, dy = wall_segments[index]
            vx, vy = dx - cx, dy - cy
            seg_len2 = vx * vx + vy * vy or 1.0
            t = max(0.0, min(1.0, ((mx - cx) * vx + (my - cy) * vy) / seg_len2))
            if math.hypot(mx - (cx + t * vx), my - (cy + t * vy)) <= tolerance:
                on += length
                break
    return on / total if total else 0.0


def assign_roles(features, segments_by_layer=None, known=None):
    """{layer: {"role": role or None, "reason": text, "by": "name"|"geometry"|"both"|"profile"}}.

    ``known`` maps layer names to roles confirmed before (a saved office profile); they win."""
    roles = {}
    for layer, f in features.items():
        name = base_name(layer)
        if known and layer in known:
            roles[layer] = {"role": known[layer], "reason": "저장된 도면 프로필", "by": "profile"}
            continue
        if known and name in known:
            roles[layer] = {"role": known[name], "reason": "저장된 도면 프로필", "by": "profile"}
            continue
        role, reason, by = None, "", "geometry"
        paired, width, ladder = f["pair"], f["width"] or 0, f["ladder"]
        named_wall = bool(_WALL.search(name) or _COLUMN.search(name))
        # many equally spaced lines are treads or hatching, unless the name says wall (cores are dense)
        wall_like = paired >= 0.5 and 100 <= width <= 1000 and (named_wall or ladder < 0.75)
        if from_sheet(layer):
            role, reason, by = None, "도곽(시트) 외부참조의 레이어", "name"
        elif _NEVER.search(name):
            role, reason, by = None, "이름이 해치·표시선·기초", "name"
        elif _STAIR.search(name):
            role, reason, by = "stair", "이름이 계단", "name"
        elif _MASONRY.search(name):
            role, reason, by = None, "이름이 조적·건식벽", "name"
        elif _OPEN.search(name) and not wall_like:
            role, reason, by = "opening", "이름이 개구부", "name"
        elif _EXCLUDE.search(name) and not (_WALL.search(name) or _BEAM.search(name) or _COLUMN.search(name)):
            role, reason, by = None, "이름이 표기·치수·기타", "name"
        elif f["linetype"] == "center":
            role, reason = None, "중심선 선종류"
        elif f["linetype"] == "dashed":
            if paired < 0.4:
                role, reason = None, "점선이지만 짝선이 아님"
            elif _ABOVE.search(name):
                role, reason, by = "wall_above", "점선 짝선 + 이름이 위층", "both"
            elif _BEAM.search(name):
                role, reason, by = "beam", "점선 짝선 + 이름이 보", "both"
            elif _BELOW.search(name) and (_WALL.search(name) or wall_like):
                role, reason, by = "wall_below", "점선 짝선 + 이름이 아래층 벽", "both"
            else:
                on_walls = (segments_by_layer or {}).get("_coincidence", {}).get(layer, 0.0)
                if on_walls >= 0.35:
                    role, reason = "wall_below", f"점선 짝선이 벽 선과 {on_walls:.0%} 겹침"
                elif 150 <= width <= 1300:
                    role, reason = "beam", f"점선 짝선 {width}mm, 벽과 겹치지 않음"
                else:
                    role, reason = None, "점선 짝선이지만 폭이 보·벽 범위 밖"
        else:
            # mostly closed outlines are columns (a rectangle's sides also pair up like a wall)
            column_like = f["closed"] >= 3 and (f["closed"] >= 0.6 * f["count"]
                                                 or (_COLUMN.search(name) and f["closed"] >= 0.35 * f["count"]))
            if column_like:
                role, reason, by = "column", f"닫힌 사각형 {f['closed']}개" + (" + 이름이 기둥" if _COLUMN.search(name) else ""), \
                    ("both" if _COLUMN.search(name) else "geometry")
            elif _BEAM.search(name) and paired >= 0.5 and not _WALL.search(name):
                role, reason, by = "beam", "실선 짝선 + 이름이 보", "both"
            elif wall_like and width >= 120:
                role, reason, by = "wall", f"실선 짝선 {paired:.0%}, 두께 {width}mm" + (" + 이름이 벽" if named_wall else ""), \
                    ("both" if named_wall else "geometry")
            elif _OUTLINE.search(name) and paired < 0.5:
                role, reason, by = "outline", "이름이 슬래브 외곽", "name"
            else:
                role, reason = None, f"실선이지만 벽 모양 아님 (짝선 {paired:.0%})"
        roles[layer] = {"role": role, "reason": reason, "by": by}
    return roles


def judge(items, known=None):
    """Roles for the layers of one drawing's structural frames. items: [(layer, linetype, points mm)]."""
    features = layer_features(items)
    first = assign_roles(features, known=known)
    walls = {layer for layer, r in first.items() if r["role"] == "wall"}
    by_layer = defaultdict(list)
    for layer, _lt, points in items:
        by_layer[layer].append(points)
    wall_segments = _segments([p for layer in walls for p in by_layer[layer]])
    coincidence = {layer: _coincidence(_segments(by_layer[layer]), wall_segments)
                   for layer, f in features.items() if f["linetype"] == "dashed" and f["pair"] >= 0.4}
    roles = assign_roles(features, {"_coincidence": coincidence}, known=known)
    for layer, info in roles.items():
        info.update(features[layer])
    return roles
