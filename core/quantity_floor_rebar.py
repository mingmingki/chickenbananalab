"""Rebar by storey from CAD: the schedules' bars laid on the lengths, counts and areas the floor
takeoff measured (no AI). Same rule as the concrete: storey N = walls and columns of plan N +
the slab and beams of plan N+1.

- walls, by mark: the schedule row whose floor range covers the storey gives the vertical and
  horizontal bars; two layers when the wall is 180 mm or thicker; vertical bars run the storey
  height plus a lap, horizontal bars run the wall plus a lap every 10 m (stock bar). Walls the
  plan does not mark take the storey's most used wall row (or the default).
- columns: main bars × (storey height + lap) + hoops round the section less cover.
- beams (plan above): top and bottom bars (rows for the ends and the middle each cover half the
  span) anchored at both ends + stirrups at their spacing.
- slab (plan above): the slab area × each bar layer at its spacing (default two layers, two ways).
Laps and anchorages come from quantity_calc (the general notes' tables when read, else the
KDS formula, marked as an estimate). Steel grade from the bar text (HD = SD400, SHD = SD500,
UHD = SD600, SD___), else the general notes' grade table, else SD400 (said so in the notes).
Weights by the KS unit weights; the total with the 3 % cutting and loss allowance is given too.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from . import quantity_calc as qc
from . import quantity_schedules as sch

REBAR_VERSION = 1
KINDS = ("foundation", "wall", "opening", "column", "beam", "slab", "stair")
KIND_LABELS = {"foundation": "기초", "wall": "벽", "opening": "개구부·단부 보강", "column": "기둥", "beam": "보", "slab": "슬래브",
               "stair": "계단"}
STAIR_KG_PER_M2 = 35.0  # stair flights and landings per m² of stair plan (an assumption, said in the notes)
FOUNDATION_BARS = "D16@300"  # top and bottom, each way, when the foundation plan gives none
COVER_M = 0.04
HOOK_M = 0.2               # two 135° hooks of a stirrup or hoop, roughly
DOUBLE_LAYER_MIN_M = 0.18  # walls this thick get bars at both faces
DEFAULTS = {
    "wall": {"vertical_bar": "D10@300", "horizontal_bar": "D10@300"},  # the usual unmarked wall (W0)
    "slab": ["D10@200", "D10@200", "D10@200", "D10@200"],  # top and bottom, both ways
    "beam": {"top_bar": "3-D19", "bottom_bar": "3-D19", "stirrup": "D10@200"},
    "column": {"main_bar": "8-D19", "hoop_mid": "D10@200"},
}
GRADE_COLORS = {"SD400": "#4ade80", "SD500": "#f59e0b", "SD600": "#f43f5e"}
# standard details around an opening in a wall: 2 bars each side (full storey height), 2 bars
# above and below running 0.6 m past it, and U-bars closing the two wall ends at the
# horizontal bar spacing
OPENING_BAR = "D16"
OPENING_BARS = 2
OPENING_EXTEND_M = 0.6
OPENING_WIDTH_M = 1.8      # average opening width when only the total is known (older results)
U_BAR_LEG_M = 0.3


def _key(text):
    return re.sub(r"\s+", "", str(text or "")).upper()


def grade_of(text, diameter, general_spec):
    """(grade, from the drawing?) of one bar text such as 'HD13@200' or '4-SHD19'."""
    key = _key(text)
    if "UHD" in key:
        return "SD600", True
    if "SHD" in key:
        return "SD500", True
    match = re.search(r"SD\s*(\d{3})", key)
    if match:
        return "SD" + match.group(1), True
    if re.search(r"(^|[^A-Z])HD\d", key):
        return "SD400", True
    spec = general_spec or {}
    if spec.get("rebar_grade_table") or spec.get("rebar_grade"):
        return "SD%d" % int(qc._rebar_fy_mpa(spec, f"D{diameter}")), True
    return "SD400", False


class _Tally:
    """kg by member kind and by (diameter, grade), with the notes of what was assumed."""

    def __init__(self, general_spec):
        self.spec = general_spec
        self.kg = dict.fromkeys(KINDS, 0.0)
        self.bars = defaultdict(float)
        self.notes = []
        self.guessed_grade = False
        self.estimated_laps = False

    def add(self, kind, text, diameter, length_m):
        if not diameter or length_m <= 0:
            return
        size = f"D{diameter}"
        weight, error = qc.rebar_weight(size, length_m)
        if error:
            self.note(f"{size}는 단위중량을 몰라 뺐어요")
            return
        grade, known = grade_of(text, diameter, self.spec)
        self.guessed_grade = self.guessed_grade or not known
        self.kg[kind] += weight
        self.bars[f"{size}|{grade}"] += weight

    def note(self, text):
        if text not in self.notes:
            self.notes.append(text)

    def lap(self, diameter, category, top=False):
        length, source = qc.get_splice_length(f"D{diameter}", self.spec, top_bar=top, category=category)
        if source == qc.EXCLUDED_SOURCE:
            self.note(f"D{diameter} 이음길이를 일반사항에서 못 찾아 뺐어요")
            return 0.0
        self.estimated_laps = self.estimated_laps or source.startswith("추정")
        return length

    def anchor(self, diameter, category, top=False, hook=True):
        length, source = qc.get_anchorage_length(f"D{diameter}", self.spec, top_bar=top, hook=hook, category=category)
        if source == qc.EXCLUDED_SOURCE:
            self.note(f"D{diameter} 정착길이를 일반사항에서 못 찾아 뺐어요")
            return 0.0
        self.estimated_laps = self.estimated_laps or source.startswith("추정")
        return length


# ── schedule rows ──

def _rows_of(links, category, mark):
    pool = (links or {}).get("schedule_rows") or []
    for item in (links or {}).get("marks") or []:
        if item.get("category") != category or item.get("mark") != mark:
            continue
        for spec in item.get("specs") or []:
            index = spec.get("rows")
            rows = pool[index] if isinstance(index, int) and 0 <= index < len(pool) else (spec.get("rows") or [])
            if isinstance(rows, list) and rows:
                return rows
    return []


def _row_for_level(rows, level_key):
    """The row whose floor range covers the storey, else the only/first row."""
    covering = [r for r in rows if level_key in (sch.parse_level_text(r.get("levels_text") or "") or [])]
    return (covering or rows or [None])[0]


def _spaced(text):
    found = sch.parse_spaced(text or "")
    return (found[0], found[1] / 1000.0) if found and found[1] else None


def _bars(text):
    return sch.parse_bars(text or "")


def _plans_of(links, building, sheet):
    plans = (links or {}).get("plans") or []
    return {i for i, p in enumerate(plans) if p.get("drawing_number") == sheet and p.get("kind") == "floor_plan"
            and (p.get("section") or "") in (building or "")}


def _wall_runs(links, building, sheet):
    """[(mark, length m, thickness m)] of marked walls on one plan sheet."""
    wanted = _plans_of(links, building, sheet)
    runs = []
    if not wanted:
        return runs
    for item in (links or {}).get("marks") or []:
        if item.get("category") != "walls":
            continue
        for place in item.get("typical_plans") or []:
            if place.get("plan") not in wanted:
                continue
            for width, length in ((place.get("measured") or {}).get("widths") or {}).items():
                try:
                    runs.append((item.get("mark"), float(length) / 1000.0, float(width) / 1000.0))
                except (TypeError, ValueError):
                    continue
    return runs


# ── members ──

def _wall(tally, values, length, thickness, height, top_storey):
    layers = 2 if thickness >= DOUBLE_LAYER_MIN_M else 1
    vertical, horizontal = values.get("vertical_bar"), values.get("horizontal_bar")
    v, h = _spaced(vertical), _spaced(horizontal)
    if v:
        dia, spacing = v
        each = height + (tally.anchor(dia, "전단벽") if top_storey else tally.lap(dia, "전단벽"))
        tally.add("wall", vertical, dia, qc._spacing_bar_count(length, spacing) * layers * each)
    if h:
        dia, spacing = h
        laps = max(0, math.ceil(length / qc.STOCK_BAR_LENGTH_M) - 1)
        each = length + laps * tally.lap(dia, "전단벽")
        tally.add("wall", horizontal, dia, qc._spacing_bar_count(height, spacing) * layers * each)
    return layers


def _column(tally, values, b, h, count, height):
    for bars_count, dia in _bars(values.get("main_bar")):
        tally.add("column", values.get("main_bar"), dia, count * bars_count * (height + tally.lap(dia, "기둥")))
    hoop_text = values.get("hoop_mid") or values.get("hoop_end") or values.get("hoop")
    hoop = _spaced(hoop_text)
    if hoop:
        dia, spacing = hoop
        perimeter = max(2 * (b + h) - 8 * COVER_M, 0.4) + HOOK_M
        tally.add("column", hoop_text, dia, count * qc._spacing_bar_count(height, spacing) * perimeter)


def _beam(tally, rows, length, b, h):
    ends = [r for r in rows if re.search(r"단부|양단|END", str(r.get("position") or ""), re.I)]
    middle = [r for r in rows if re.search(r"중앙|CENTER|MID", str(r.get("position") or ""), re.I)]
    zones = [(ends[0], 0.5), (middle[0], 0.5)] if ends and middle else [(rows[0], 1.0)]
    for row, share in zones:
        values = row.get("values") or row
        for key, top in (("top_bar", True), ("bottom_bar", False)):
            for count, dia in _bars(values.get(key)):
                anchors = 2 * tally.anchor(dia, "보", top=top) * share
                laps = max(0, math.ceil(length / qc.STOCK_BAR_LENGTH_M) - 1) * tally.lap(dia, "보", top=top) * share
                tally.add("beam", values.get(key), dia, count * (length * share + anchors + laps))
        stirrup = _spaced(values.get("stirrup"))
        if stirrup:
            dia, spacing = stirrup
            perimeter = max(2 * (b + h) - 8 * COVER_M, 0.4) + HOOK_M
            tally.add("beam", values.get("stirrup"), dia, qc._spacing_bar_count(length * share, spacing) * perimeter)


def _openings(tally, measure, height, horizontal, thickness):
    """Reinforcement round the outer wall openings the plan measured (windows, balcony sashes)."""
    total = float((measure or {}).get("window_gaps") or 0)
    if total <= 0:
        return 0
    count = int((measure or {}).get("window_gap_count") or max(1, round(total / OPENING_WIDTH_M)))
    dia = int(OPENING_BAR[1:])
    vertical = count * 2 * OPENING_BARS * (height + tally.lap(dia, "전단벽"))
    flat = 2 * OPENING_BARS * (total + count * 2 * OPENING_EXTEND_M)
    tally.add("opening", OPENING_BAR, dia, vertical + flat)
    h = _spaced(horizontal)
    if h:
        u_bar = 2 * U_BAR_LEG_M + thickness
        tally.add("opening", horizontal, h[0], count * 2 * qc._spacing_bar_count(height, h[1]) * u_bar)
    return count


# standard wall details, per straight wall piece (between corners, crossings and openings):
# U- or C-bars closing both ends at the horizontal bar spacing (back = wall thickness, legs = one
# anchorage each), and vertical bars at the ends; per opening (a gap between two pieces on one
# line, or a window in an outer wall): 2 bars on each side and 2 above and below, at both wall
# faces, running a lap length past it.
END_VERTICAL_BARS = 6
OPENING_HEIGHT_M = 2.1
OPENING_LAP_M = 0.5
OPENING_LAYERS = 2
OPENING_MIN_M, OPENING_MAX_M = 0.5, 5.0


def wall_pieces(walls):
    """(pieces [(length m, thickness m)], openings [width m]) of a plan's wall face lines: faces
    paired into straight pieces; a gap of 0.5–5 m between two pieces on one line is an opening."""
    from . import quantity_geometry as geometry
    segments = [(w[0] * 1000, w[1] * 1000, w[2] * 1000, w[3] * 1000) for w in walls or []]
    if not segments:
        return [], []
    found = [p for p in geometry.pieces(segments, "walls") if 100 <= p["width"] <= 600 and p["t2"] - p["t1"] >= 150]
    lines = defaultdict(list)
    for p in found:
        lines[(round(p["angle"]) % 180, round(p["offset"] / 60), round(p["width"] / 40))].append(p)
    openings = []
    for row in lines.values():
        row.sort(key=lambda p: p["t1"])
        for a, b in zip(row, row[1:]):
            gap = (b["t1"] - a["t2"]) / 1000.0
            if OPENING_MIN_M <= gap <= OPENING_MAX_M:
                openings.append(gap)
    return [((p["t2"] - p["t1"]) / 1000.0, p["width"] / 1000.0) for p in found], openings


def _wall_details(tally, walls, height, vertical, horizontal, measure=None):
    """Standard end and opening bars of one storey's walls; returns (pieces, openings) counted."""
    pieces, openings = wall_pieces(walls)
    windows = int((measure or {}).get("window_gap_count") or 0)
    if windows:
        width = float((measure or {}).get("window_gaps") or 0) / windows
        openings = openings + [width] * windows
    h, v = _spaced(horizontal), _spaced(vertical)
    if h:
        ends = 2 * tally.anchor(h[0], "전단벽", hook=False)
        rows = qc._spacing_bar_count(height, h[1])
        tally.add("opening", horizontal, h[0], sum(rows * (2 * t + 2 * ends) for _length, t in pieces))
    if v:
        tally.add("opening", vertical, v[0], len(pieces) * END_VERTICAL_BARS * (height + tally.lap(v[0], "전단벽")))
    if openings:
        dia = int(OPENING_BAR[1:])
        bars = OPENING_BARS * 2 * OPENING_LAYERS  # two sides (or above and below) at both faces
        tally.add("opening", OPENING_BAR, dia, sum(bars * (OPENING_HEIGHT_M + 2 * OPENING_LAP_M) + bars * (w + 2 * OPENING_LAP_M)
                                                   for w in openings))
    return len(pieces), len(openings)


def _project_wall_rows(links):
    """Every wall schedule row weighted by the wall length drawn with its mark (on any plan)."""
    weighted = []
    for item in (links or {}).get("marks") or []:
        if item.get("category") != "walls":
            continue
        length = sum(float((p.get("measured") or {}).get("length_mm") or 0) for p in item.get("typical_plans") or []) / 1000.0
        rows = _rows_of(links, "walls", item.get("mark"))
        if rows and length > 0:
            weighted.append((rows, length))
    return weighted


def _noted_wall_rows(links):
    """Rows of the wall mark the drawing's notes give to walls drawn without a mark."""
    for item in (links or {}).get("marks") or []:
        if item.get("category") == "walls" and item.get("unlabelled_default_plans"):
            rows = _rows_of(links, "walls", item.get("mark"))
            if rows:
                return item.get("mark"), rows
    return None, []


def _project_wall_spec(weighted, level_key):
    """(vertical, horizontal, thickness) most used for walls on the storey, by length."""
    used = Counter()
    for rows, length in weighted:
        values = (_row_for_level(rows, level_key) or {}).get("values") or {}
        if values.get("vertical_bar") and values.get("horizontal_bar"):
            thickness = (sch.parse_mm(values.get("thickness")) or 200) / 1000.0
            used[(values["vertical_bar"], values["horizontal_bar"], thickness)] += length
    return used.most_common(1)[0][0] if used else None


def _most_used(counter, fallback):
    return counter.most_common(1)[0][0] if counter else fallback


def build_rebar(model, links=None, takeoff=None, general_spec=None, edits=None):
    """Rebar rows per building and storey, in step with ``takeoff`` (quantity_floor_takeoff)."""
    from . import quantity_edits
    from . import quantity_floor_takeoff as ft
    fixes_by_plan = quantity_edits.by_plan(edits)
    takeoff = takeoff or ft.build_takeoff(model, links, edits=edits)
    rows_by = {(b.get("name"), r.get("key")): r for b in takeoff.get("buildings") or [] for r in b.get("rows") or []}
    project_walls = _project_wall_rows(links)
    noted_mark, noted_rows = _noted_wall_rows(links)
    out_buildings, overall = [], defaultdict(float)
    for building in (model or {}).get("buildings") or []:
        name, plans, levels = building.get("name"), building.get("plans") or [], building.get("levels") or []
        rows = []
        ft_row = rows_by.get((name, "FT"))
        if ft_row and ft_row.get("foundation"):
            rows.append(_foundation_rebar(ft_row, links, name, general_spec, overall))
        for index, level in enumerate(levels):
            key = level.get("key") or ""
            row = rows_by.get((name, key))
            if not row:
                continue
            above = levels[index + 1] if index + 1 < len(levels) else None
            vplan = plans[level["plan"]] if isinstance(level.get("plan"), int) and level["plan"] < len(plans) else {}
            hplan = plans[above["plan"]] if above and isinstance(above.get("plan"), int) and above["plan"] < len(plans) else None
            tally = _Tally(general_spec)
            concrete = row.get("concrete") or {}
            slab_area = float((row.get("measures") or {}).get("slab_area") or 0)
            t = (float(concrete.get("slab") or 0) / slab_area) if slab_area else ft.SLAB_THICKNESS
            height = max(float(row.get("height") or 0) - t, 0.0)
            top_storey = above is None or ft._storey_kind(above.get("key") or "") == "roof" and index + 2 >= len(levels)
            draw = {}

            # walls: marked runs by their rows, the rest by the storey's most used row
            used = Counter()
            marked_area = 0.0
            for mark, length, thickness in (_wall_runs(links, name, level.get("sheet")) if links else []):
                values = (_row_for_level(_rows_of(links, "walls", mark), key) or {}).get("values") or {}
                if not values.get("vertical_bar") and not values.get("horizontal_bar"):
                    continue
                _wall(tally, values, length, thickness, height, top_storey)
                used[(values.get("vertical_bar"), values.get("horizontal_bar"), thickness)] += length
                marked_area += length * thickness
            wall_area = max(float(concrete.get("wall") or 0) / height if height else 0.0, 0.0)
            rest_area = max(wall_area - marked_area, 0.0)
            noted = (_row_for_level(noted_rows, key) or {}).get("values") or {}
            noted_spec = ((noted.get("vertical_bar"), noted.get("horizontal_bar"), (sch.parse_mm(noted.get("thickness")) or 200) / 1000.0)
                          if noted.get("vertical_bar") and noted.get("horizontal_bar") else None)
            # walls drawn without a mark are the plain wall type: the drawing's noted default, else
            # D10@300 each way (the marked rows belong to the special walls that carry the mark)
            project = noted_spec
            fallback = project or (DEFAULTS["wall"]["vertical_bar"], DEFAULTS["wall"]["horizontal_bar"], 0.2)
            vertical, horizontal, thickness = fallback
            if rest_area > 0.05:
                rest_length = rest_area / max(thickness, 0.1)
                _wall(tally, {"vertical_bar": vertical, "horizontal_bar": horizontal}, rest_length, thickness, height, top_storey)
                source = (f"도면 주기의 미표기 벽 {noted_mark} 배근" if noted_spec else "미표기 벽 기본 배근")
                tally.note(f"부호 없는 벽 {rest_length:.0f}m는 {source}({vertical}·{horizontal})으로 봤어요")
            pieces, opened = _wall_details(tally, vplan.get("walls"), height, vertical, horizontal,
                                           vplan.get("measure") if ft._storey_kind(key) != "basement" else None)
            if not pieces:
                opened = _openings(tally, vplan.get("measure"), height, horizontal, thickness) if ft._storey_kind(key) != "basement" else 0
            if pieces or opened:
                tally.note(f"벽 {pieces}토막 끝에 U·C형 바와 수직보강근 {END_VERTICAL_BARS}가닥, 개구부 {opened}곳에 보강근(양면 2-D16 둘레)을 넣었어요")
            # the 3D draws the bars of the walls that run longest on the storey
            rest_run = rest_area / max(thickness, 0.1) if rest_area > 0.05 else 0.0
            shown = _most_used(used, None) if used and sum(used.values()) >= rest_run else (vertical, horizontal, thickness)
            draw["wall"] = {"vertical": shown[0], "horizontal": shown[1], "layers": 2 if shown[2] >= DOUBLE_LAYER_MIN_M else 1}

            # columns on this storey's plan
            members = ft._link_members(links, name, level.get("sheet")) if links else None
            column_used = Counter()
            for column in (members or {}).get("columns") or []:
                values = (_row_for_level(_rows_of(links, "columns", column["mark"]), key) or {}).get("values") or {}
                if values.get("main_bar"):
                    _column(tally, values, column["b"], column["h"], column["count"], height)
                    column_used[values.get("main_bar")] += column["count"]
            if column_used:
                draw["column"] = {"main": _most_used(column_used, None)}

            # beams and the slab on top of the storey (plan above)
            if above is not None and hplan is not None:
                above_members = ft._link_members(links, name, above.get("sheet")) if links else None
                beam_used = Counter()
                for beam in (above_members or {}).get("beams") or []:
                    rows_ = _rows_of(links, "beams", beam["mark"]) if beam.get("mark") else []
                    depth = beam["h"] or ft.BEAM_DEPTH_DEFAULT
                    if rows_:
                        _beam(tally, rows_, beam["length"], beam["b"], depth)
                        beam_used[(rows_[0].get("values") or {}).get("stirrup")] += beam["length"]
                    else:
                        _beam(tally, [{"values": DEFAULTS["beam"]}], beam["length"], beam["b"], depth)
                        tally.note("부호 없는 보는 기본 배근(3-D19 상·하부, D10@200 늑근)으로 봤어요")
                if beam_used:
                    draw["beam"] = {"stirrup": _most_used(beam_used, DEFAULTS["beam"]["stirrup"])}
                if slab_area > 0:
                    layers = _slab_layers(links, name, above)
                    if not layers:
                        layers = DEFAULTS["slab"]
                        tally.note("슬래브 배근 일람표를 못 찾아 D10@200 상·하부 양방향으로 봤어요")
                    for text in layers:
                        bar = _spaced(text)
                        if bar:
                            tally.add("slab", text, bar[0], slab_area / bar[1] * 1.0)
                    draw["slab"] = {"bars": layers}

            stair_area = float(concrete.get("stair") or 0) / ft.STAIR_CONCRETE_PER_M2 if concrete.get("stair") else 0.0
            if stair_area:
                tally.kg["stair"] += stair_area * STAIR_KG_PER_M2
                tally.bars["D13|" + grade_of("D13", 13, general_spec)[0]] += stair_area * STAIR_KG_PER_M2
                tally.note(f"계단 {stair_area:.0f}㎡는 {STAIR_KG_PER_M2:.0f}kg/㎡(D13)로 봤어요")

            # people's fixes: added members take the storey's usual bars, taken-out ones give them back
            fixes = [e for e in fixes_by_plan.get(vplan.get("id"), []) if e["kind"] != "beam"]
            if hplan is not None and above is not None:
                fixes += [e for e in fixes_by_plan.get(hplan.get("id"), []) if e["kind"] == "beam"]
            for fix in fixes:
                sign = -1.0 if fix["op"] == "remove" else 1.0
                part = _Tally(general_spec)
                if fix["kind"] == "wall":
                    _wall(part, {"vertical_bar": vertical, "horizontal_bar": horizontal}, quantity_edits.length(fix), fix["t"], height, top_storey)
                elif fix["kind"] == "beam":
                    _beam(part, [{"values": DEFAULTS["beam"]}], quantity_edits.length(fix), fix["w"], fix["d"])
                else:
                    _column(part, {"main_bar": (draw.get("column") or {}).get("main") or DEFAULTS["column"]["main_bar"],
                                   "hoop_mid": DEFAULTS["column"]["hoop_mid"]}, fix["w"], fix["h"], 1, height)
                for kind in KINDS:
                    tally.kg[kind] += sign * part.kg[kind]
                for bar, kg in part.bars.items():
                    tally.bars[bar] += sign * kg
            if fixes:
                tally.note(f"사람이 고친 부재 {len(fixes)}개를 같은 층의 배근으로 더하거나 뺐어요")

            if tally.guessed_grade:
                tally.note("강종 표기가 없는 철근은 SD400으로 봤어요 (구조일반사항 확인 필요)")
            if tally.estimated_laps:
                tally.note("이음·정착길이는 KDS 공식 추정값이에요 (일반사항 표를 읽으면 그 값을 써요)")
            if row.get("approximate"):
                tally.note("콘크리트가 근사인 층이라 철근도 근사예요")
            kg = {k: round(max(v, 0.0), 1) for k, v in tally.kg.items()}
            kg["total"] = round(sum(kg[k] for k in KINDS), 1)
            bars = {b: round(max(v, 0.0), 1) for b, v in sorted(tally.bars.items(), key=_bar_order) if v > 0.05}
            if not row.get("approximate"):
                for b, v in bars.items():
                    overall[b] += v
            rows.append({"key": key, "label": row.get("label"), "kg": kg, "by_bar": bars, "notes": tally.notes,
                         "approximate": bool(row.get("approximate")), "draw": _draw_spec(draw, general_spec)})
        totals = dict.fromkeys(KINDS + ("total",), 0.0)
        by_bar = defaultdict(float)
        for r in rows:
            if r["approximate"]:
                continue
            for k in totals:
                totals[k] += r["kg"][k]
            for b, v in r["by_bar"].items():
                by_bar[b] += v
        out_buildings.append({"name": name, "rows": rows, "totals": {k: round(v, 1) for k, v in totals.items()},
                              "by_bar": {b: round(v, 1) for b, v in sorted(by_bar.items(), key=_bar_order)}})
    net = sum(overall.values())
    return {
        "version": REBAR_VERSION, "rule": "N층 = N층 벽·기둥 + N+1층 바닥 슬래브·보",
        "buildings": out_buildings,
        "by_bar": {b: round(v, 1) for b, v in sorted(overall.items(), key=_bar_order)},
        "net_kg": round(net, 1), "loss_factor": qc.REBAR_WASTE_FACTOR, "with_loss_kg": round(net * qc.REBAR_WASTE_FACTOR, 1),
        "grade_colors": GRADE_COLORS,
        "not_included": ["기초(매트)", "계단", "개구부·단부 보강근", "우마근"],
    }


def _foundation_rebar(row, links, name, general_spec, overall):
    """Mat bars (layers × both ways at the spacing over the area, laps every stock length) and the
    isolated footings' bars, for the foundation storey."""
    from . import quantity_floor_takeoff as ft
    info = row["foundation"]
    tally = _Tally(general_spec)
    _thickness, text = ft.foundation_specs(links, name)
    bar = _spaced(text) if text else None
    if not bar:
        text, bar = FOUNDATION_BARS + "(T/B, EW)", _spaced(FOUNDATION_BARS)
        tally.note(f"기초 배근 표기를 못 찾아 {FOUNDATION_BARS} 상·하부 양방향으로 봤어요")
    key = _key(text)
    layers = (2 if re.search(r"T/?B|TOP.*BOT", key) else 1) * 2  # each way unless told otherwise
    area, side = info["area"], math.sqrt(max(info["area"], 0.0))
    laps = max(0, math.ceil(side / qc.STOCK_BAR_LENGTH_M) - 1)
    run = area / bar[1] * (1 + laps * tally.lap(bar[0], "기초") / max(side, 1.0))
    tally.add("foundation", text, bar[0], layers * run)
    # chair bars holding the top mat: one per 1.5 m square, legs the mat depth less the covers,
    # plus half as many fixing bars (the SK manual's rule, D16)
    height = max(info["thickness"] - 0.1, 0.2)
    chairs = math.ceil(area / (1.5 * 1.5))
    tally.add("foundation", "D16", 16, chairs * (2 * height + 0.8) + math.ceil(chairs * 0.5) * 2 * height)
    tally.note(f"고임철근(우마) D16 {chairs}개를 1.5m 간격으로 넣었어요")
    for foot in info.get("footings") or []:
        spaced = _spaced(str(foot.get("bars") or "")) or (16, 0.3)
        per = 2 * (qc._spacing_bar_count(foot["x"], spaced[1]) * foot["y"] + qc._spacing_bar_count(foot["y"], spaced[1]) * foot["x"]) / 2
        tally.add("foundation", f"D{spaced[0]}", spaced[0], foot["count"] * per)
    if info.get("footings"):
        tally.note("독립기초는 하부 양방향 D16@300으로 봤어요 (기초 일람표 배근을 읽으면 그 값을 써요)")
    if row.get("approximate"):
        tally.note("기초 두께가 가정값이라 철근도 근사예요")
    kg = {k: round(max(v, 0.0), 1) for k, v in tally.kg.items()}
    kg["total"] = round(sum(kg[k] for k in KINDS), 1)
    bars = {b: round(v, 1) for b, v in sorted(tally.bars.items(), key=_bar_order) if v > 0.05}
    if not row.get("approximate"):
        for b, v in bars.items():
            overall[b] += v
    return {"key": "FT", "label": "기초", "kg": kg, "by_bar": bars, "notes": tally.notes,
            "approximate": bool(row.get("approximate")), "draw": {}}


def _bar_order(item):
    size, _sep, grade = item[0].partition("|")
    return (int(size[1:]) if size[1:].isdigit() else 99, grade)


def _slab_layers(links, building, above):
    """Bar texts of the slab marks drawn on the plan above (their schedule rows), most used first."""
    found = Counter()
    wanted = _plans_of(links, building, above.get("sheet"))
    if not wanted:
        return []
    for item in (links or {}).get("marks") or []:
        if item.get("category") != "slabs":
            continue
        count = sum(o.get("count") or 1 for o in item.get("typical_plans") or [] if o.get("plan") in wanted)
        if not count:
            continue
        row = _row_for_level(_rows_of(links, "slabs", item.get("mark")), above.get("key") or "")
        texts = tuple(v for v in ((row or {}).get("values") or {}).values() if isinstance(v, str) and sch.parse_spaced(v))
        if texts:
            found[texts] += count
    return list(found.most_common(1)[0][0]) if found else []


def _draw_spec(draw, general_spec):
    """What the 3D draws for a storey: bar spacing (m) and the grade colour of walls, slab, beams."""
    out = {}
    wall = draw.get("wall") or {}
    for part in ("vertical", "horizontal"):
        bar = _spaced(wall.get(part))
        if bar:
            grade, _known = grade_of(wall.get(part), bar[0], general_spec)
            out["wall_" + part] = {"d": bar[0], "s": bar[1], "grade": grade}
    if wall:
        out["wall_layers"] = wall.get("layers", 2)
    slab = (draw.get("slab") or {}).get("bars") or []
    bar = _spaced(slab[0]) if slab else None
    if bar:
        grade, _known = grade_of(slab[0], bar[0], general_spec)
        out["slab"] = {"d": bar[0], "s": bar[1], "grade": grade}
    stirrup = (draw.get("beam") or {}).get("stirrup")
    bar = _spaced(stirrup)
    if bar:
        grade, _known = grade_of(stirrup, bar[0], general_spec)
        out["beam"] = {"d": bar[0], "s": bar[1], "grade": grade}
    return out
