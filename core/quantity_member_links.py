"""Schedule definitions linked to plan label occurrences, from CAD only.

A mark is "defined" when it appears inside a schedule sheet (일람표) and "placed" when
it is labelled on a floor plan on a member-name layer (e.g. 0_STR_COL_NAME). Grid
bubbles and other layers never count. Unit-type plans (단위세대) are typical plans:
listed, never added to occurrences. A plan's section and levels come from its title;
totals multiply only by a level range read from that title. Labels are evidence for a
reviewer, not a verified member count; member counts are compared, never replaced.

Labels kept inside pasted blocks count like model-space labels. A plan note saying another
area's plans must be read ("주차장 부분은 주차장 구조평면도 참고") makes that area's marks on
this plan reference copies: listed, never counted. A mark's area comes from the schedules
defining it. Schedule tables give each mark's section and rebar values; a level-prefixed
schedule mark ("-1G1", "RG1") defines the plain label ("G1") on that level when only the
plain label is drawn on plans.
"""
from __future__ import annotations

import json
import re
import unicodedata

from . import quantity_schedules as schedules
from . import quantity_sheet_identity as sid

CATEGORIES = ("foundations", "columns", "beams", "slabs", "walls", "stairs")
LAYER_CATEGORIES = (("columns", r"COL"), ("walls", r"WALL"), ("beams", r"BEAM|GIRDER|GDR"),
                    ("foundations", r"FDN|FOOT|FTG|FOUND|PILE"), ("slabs", r"SLAB"), ("stairs", r"STAIR"))
NAME_LAYER = re.compile(r"NAME|MARK|TAG", re.I)
TITLE_CATEGORIES = (("walls", ("벽체", "외벽", "옹벽", "wall")), ("columns", ("기둥", "column")),
                    ("foundations", ("기초", "footing", "foundation")), ("slabs", ("슬래브", "slab")),
                    ("stairs", ("계단", "stair")), ("beams", ("보일람", "보배근", "girder", "beam")))
SCHEDULE_WORDS = ("일람표", "schedule")


def _compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).casefold()


def layer_category(layer):
    if not layer or not NAME_LAYER.search(layer):
        return None
    return next((category for category, pattern in LAYER_CATEGORIES if re.search(pattern, layer, re.I)), None)


def title_category(title):
    compact = _compact(title)
    if not compact:
        return None
    return next((category for category, words in TITLE_CATEGORIES if any(w in compact for w in words)), None)


def _levels(text):
    compact = _compact(text)
    if "기초" in compact and "일람" not in compact:
        return ["FDN"]  # a foundation plan is one level, whichever floor it sits on
    span = re.search(r"(지하|지상)?(\d+)층?~(지하|지상)?(\d+)층", compact)
    if span:
        low_basement = span.group(1) == "지하"
        high_basement = (span.group(3) or span.group(1)) == "지하"
        low, high = int(span.group(2)), int(span.group(4))
        if not low_basement and not high_basement and low <= high:
            return [str(n) for n in range(low, high + 1)]
        if low_basement and high_basement:
            return ["B" + str(n) for n in range(max(low, high), min(low, high) - 1, -1)]
        return None
    single = re.findall(r"(지하|지상)?(\d+)층", compact)
    if len(single) == 1:
        prefix, number = single[0]
        return [("B" if prefix == "지하" else "") + str(int(number))]
    if not single and "지붕" in compact and "옥탑" not in compact:
        return ["R"]
    return None


def parse_zone_levels(zone):
    """An AI zone ("지하1층", "1층", "기준층(2~15층)") as levels; unknown stays None."""
    return _levels(zone)


def parse_sheet_scope(title):
    compact = _compact(title)
    section = re.search(r"(\d{2,4})동", compact)
    if section:
        section = section.group(1) + "동"
    elif "지하주차장" in compact:
        section = "지하주차장"
    elif "주차장" in compact:
        section = "주차장"
    else:
        section = None
    if any(word in compact for word in SCHEDULE_WORDS):
        kind = "schedule"
    elif "단위세대" in compact:
        kind = "unit_type_plan"
    elif "평면도" in compact or "기초" in compact:
        kind = "floor_plan"
    else:
        kind = "other"
    return {"section": section, "levels": _levels(title) if kind == "floor_plan" else None, "kind": kind}


def _section_match(member_section, sheet_section):
    a, b = _compact(member_section), _compact(sheet_section)
    if not a or a in ("미상", "전체") or not b:
        return True
    return a == b or a in b or b in a


def _compact_row(row):
    """A schedule row as stored: values plus only the keys that say something."""
    kept = {"values": row.get("values") or {}}
    for key in ("position", "levels_text"):
        if row.get(key):
            kept[key] = row[key]
    if row.get("ditto"):
        kept["ditto"] = row["ditto"]
    return kept


def _place(ref, plans):
    """Section and levels of a placement; older results stored them on the placement itself."""
    plan = plans[ref["plan"]] if isinstance(ref.get("plan"), int) and 0 <= ref["plan"] < len(plans) else {}
    return dict(ref, section=ref.get("section", plan.get("section")), levels=ref.get("levels", plan.get("levels")))


def _plan_area(scope, title):
    return schedules.area_of(scope.get("section")) or schedules.area_of(title)


def build_member_links(cad_data):
    sheets_by_file = {f: d.get("sheets") or [] for f, d in (cad_data or {}).items() if isinstance(d, dict)}
    index_entries = [e for d in (cad_data or {}).values() if isinstance(d, dict) for e in d.get("index_entries") or []]
    registry = sid.build_sheet_registry(sheets_by_file, index_entries=index_entries)
    rows, plans, row_sets, row_set_index = {}, [], [], {}

    def row_set(table_rows):
        """Schedule rows are stored once; many marks share the same rows."""
        compact = [_compact_row(r) for r in table_rows]
        key = json.dumps(compact, ensure_ascii=False, sort_keys=True)
        if key not in row_set_index:
            row_set_index[key] = len(row_sets)
            row_sets.append(compact)
        return row_set_index[key]

    def plan_ref(slot, sheet, scope):
        """Sheet details live once in `plans`; placements carry its index and number."""
        title = sheet.get("drawing_title") or slot.get("index_title")
        plans.append({"drawing_number": sheet.get("drawing_number"), "drawing_title": sheet.get("drawing_title"),
                      "index_title": slot.get("index_title"), "file": sheet.get("file"),
                      "frame_handle": sheet.get("frame_handle"), "section": scope["section"],
                      "levels": scope["levels"], "kind": scope["kind"], "labelled_marks": 0,
                      "area": _plan_area(scope, slot.get("index_title") or title),
                      "notes": [dict(n) for n in sheet.get("notes") or []], "specs": list(sheet.get("specs") or []),
                      "reference_copy_labels": 0, "labels_in_blocks": 0})
        return len(plans) - 1

    def row(category, mark):
        return rows.setdefault((category, mark), {"category": category, "mark": mark, "definitions": [], "specs": [],
                                                   "occurrences": [], "typical_plans": [], "reference_copies": [],
                                                   "unlabelled_default_plans": [], "flags": []})

    def flag(target, name):
        if name not in target["flags"]:
            target["flags"].append(name)

    slots = [slot for sequence in registry["sequence"].values() for slot in sequence if slot.get("cad_sheet")]
    schedule_slots, plan_slots = [], []
    for slot in slots:
        sheet = slot["cad_sheet"]
        scope = parse_sheet_scope(sheet.get("drawing_title") or slot.get("index_title"))
        if any(word in _compact(slot.get("index_title")) for word in SCHEDULE_WORDS):
            scope["kind"] = "schedule"
        if scope["kind"] == "schedule":
            schedule_slots.append((plan_ref(slot, sheet, scope), sheet, slot, scope))
        elif scope["kind"] in ("floor_plan", "unit_type_plan"):
            plan_slots.append((plan_ref(slot, sheet, scope), sheet, slot, scope))

    # Plan labels per member-name layer, before deciding which of them are counted.
    labels, placed, placed_levels = [], {}, {}
    for plan, sheet, _slot, scope in plan_slots:
        for entry in sheet.get("marks") or []:
            layered = {}
            for layer, count in entry["layers"].items():
                category = layer_category(layer)
                if category:
                    layered[category] = layered.get(category, 0) + count
            if not layered:
                continue
            category = max(layered, key=layered.get)
            labels.append((plan, entry["mark"], category, layered[category], scope,
                           min(entry.get("in_blocks", 0), layered[category])))
            placed.setdefault(entry["mark"], set()).add(category)
            placed_levels.setdefault(entry["mark"], set()).update(scope["levels"] or [])

    # Schedule marks: a level-prefixed mark defines the plain label on that level.
    homes, defined = {}, []
    for plan, sheet, slot, scope in schedule_slots:
        area = schedules.area_of(slot.get("index_title")) or schedules.area_of(sheet.get("drawing_title"))
        tables = {}
        for table in sheet.get("tables") or []:
            tables.setdefault(table["mark"], []).append(table)
        marks = list(dict.fromkeys([entry["mark"] for entry in sheet.get("marks") or []] + list(tables)))
        plans[plan]["table_labels"] = {k: v for t in sheet.get("tables") or [] for k, v in (t.get("labels") or {}).items()}
        for mark in marks:
            target, levels = mark, None
            prefixed = schedules.level_prefix(mark)
            if mark not in placed and prefixed and prefixed[1] in placed and prefixed[0] in placed_levels.get(prefixed[1], ()):
                target, levels = prefixed[1], [prefixed[0]]
            homes.setdefault(target, set()).add(area)
            defined.append((plan, sheet, slot, scope, mark, target, levels, tables.get(mark) or []))

    for plan, mark, category, count, scope, in_blocks in labels:
        info = plans[plan]
        referenced = {n["area"] for n in info["notes"] if n["kind"] == "reference" and n.get("area")} - {info["area"]}
        home = homes.get(mark, set()) - {None}
        target = row(category, mark)
        ref = {"plan": plan, "drawing_number": info["drawing_number"], "count": count}
        if in_blocks:
            ref["in_blocks"] = in_blocks
            info["labels_in_blocks"] += in_blocks
        if referenced and home and home <= referenced:
            ref["note"] = next(n["text"] for n in info["notes"] if n["kind"] == "reference" and n.get("area") in home)
            target["reference_copies"].append(ref)
            info["reference_copy_labels"] += count
            continue
        info["labelled_marks"] += count
        if scope["kind"] == "floor_plan":
            target["occurrences"].append(ref)  # section and levels live on plans[ref["plan"]]
        else:
            target["typical_plans"].append(ref)

    for plan, sheet, slot, scope, mark, target_mark, levels, tables in defined:
        on_plans = placed.get(target_mark)
        table_category = next((t["category"] for t in tables if t.get("category")), None)
        if on_plans and len(on_plans) == 1:
            category = next(iter(on_plans))
        else:
            category = (table_category or title_category(slot.get("index_title"))
                        or title_category(sheet.get("drawing_title")) or "unknown")
        target = row(category, target_mark)
        definition = {"plan": plan, "drawing_number": sheet.get("drawing_number")}
        if target_mark != mark:
            definition.update(schedule_mark=mark, levels=levels)
        if definition not in target["definitions"]:
            target["definitions"].append(definition)
        for table in tables:
            spec = {"plan": plan, "drawing_number": sheet.get("drawing_number"), "schedule_mark": mark,
                    "category": table.get("category"), "layout": table.get("layout"),
                    "rows": row_set(table.get("rows") or [])}
            if levels:
                spec["levels"] = levels
            target["specs"].append(spec)
        title_categories = {c for c in (title_category(sheet.get("drawing_title")), title_category(slot.get("index_title"))) if c}
        if title_categories and (category not in title_categories or len(title_categories) > 1):
            flag(target, "schedule_category_differs")
        if table_category and table_category != category:
            flag(target, "table_category_differs")

    for plan, info in enumerate(plans):
        for note in info["notes"]:
            if note["kind"] != "unlabelled_default" or not note.get("mark"):
                continue
            categories = [note["category"]] if note.get("category") else sorted(placed.get(note["mark"], ())) or ["unknown"]
            for category in categories:
                target = row(category, note["mark"])
                target["unlabelled_default_plans"].append({"plan": plan, "drawing_number": info["drawing_number"],
                                                            "note": note["text"]})
                flag(target, "unlabelled_default_on_plans")
        if info["kind"] in ("floor_plan", "unit_type_plan"):
            info["status"] = ("labelled" if info["labelled_marks"] else "reference_copies_only" if info["reference_copy_labels"]
                              else "specs_only" if info["specs"] else "no_member_labels")
        else:
            info["status"] = "schedule" if info["kind"] == "schedule" else None

    for target in rows.values():
        known = [o for o in target["occurrences"] if plans[o["plan"]]["levels"]]
        target["label_total_known_part"] = sum(o["count"] * len(plans[o["plan"]]["levels"]) for o in known)
        target["label_total"] = target["label_total_known_part"] if len(known) == len(target["occurrences"]) else None
        if len(known) != len(target["occurrences"]):
            flag(target, "floor_count_unknown")
        if target["definitions"] and not target["occurrences"]:
            flag(target, "defined_not_on_plans")
        if (target["occurrences"] or target["typical_plans"]) and not target["definitions"]:
            flag(target, "on_plans_not_defined")
        if target["reference_copies"]:
            flag(target, "reference_copies_not_counted")
        areas = {plans[o["plan"]]["area"] for o in target["occurrences"]} - {None}
        if len(areas) > 1 and not (homes.get(target["mark"], set()) - {None}):
            flag(target, "placed_in_two_areas")
    ordered = sorted(rows.values(), key=lambda r: (CATEGORIES.index(r["category"]) if r["category"] in CATEGORIES else 99,
                                                    re.sub(r"\d+", lambda m: m.group().zfill(6), r["mark"])))
    return {"schema": "quantity_member_links_v2", "marks": ordered, "plans": plans, "schedule_rows": row_sets,
            "issues": registry["issues"], "quantity_verified": False}


def spec_rows(spec, matrix):
    """Rows of one schedule spec; `rows` is an index into matrix["schedule_rows"]."""
    rows = spec.get("rows")
    if isinstance(rows, int) and not isinstance(rows, bool):
        pool = (matrix or {}).get("schedule_rows") or []
        return pool[rows] if 0 <= rows < len(pool) else []
    return rows or []


_NOTES = {
    "count_matches": "CAD 평면 표기 {cad}개와 같습니다({plans}). 실제 개수 확인은 필요합니다.",
    "count_differs": "AI {ai}개, CAD 평면 표기 {cad}개({plans}) — 확인이 필요합니다.",
    "cad_counts_vary_by_floor": "층별 CAD 평면 표기 개수가 다릅니다({plans}) — 층별로 나눠 확인해 주세요.",
    "no_matching_cad_plan": "같은 동·층의 CAD 평면도에서 이 부호 표기를 찾지 못했습니다(표기된 도면: {plans}).",
    "mark_not_on_cad_plans": "CAD 평면도에서 이 부호 표기를 찾지 못했습니다.",
    "zone_unknown": "층 정보를 해석하지 못해 CAD 평면 표기와 비교하지 못했습니다.",
    "no_cad_plan_marks": "비교할 CAD 평면도 부호 근거가 없습니다.",
    "reference_copy_only": "같은 동·층 평면({copies})의 이 부호는 다른 영역 참고용 표기라 세지 않았습니다(주기: {note}). "
                           "해당 영역 평면도({plans})로 확인해 주세요.",
    "foundation_plan_specs": "기초 평면({specs_plans})에 부재 표기 대신 문구가 있습니다: {specs}.",
}


def _plan_names(occurrences):
    return ", ".join(sorted({o["drawing_number"] or "?" for o in occurrences})) or "-"


def _overlaps(levels, other):
    return bool(levels and other and set(levels) & set(other))


def _foundation_specs(item, plans, section):
    """Spec texts of label-less foundation plans in the member's section, and the AI rebar check."""
    found = [(index, plan) for index, plan in enumerate(plans) if plan.get("kind") == "floor_plan"
             and "FDN" in (plan.get("levels") or []) and plan.get("specs") and _section_match(section, plan.get("section"))]
    if not found:
        return None
    specs = [spec for _index, plan in found for spec in plan["specs"]]
    shown = ", ".join(((spec["label"] + " ") if spec.get("label") else "") + spec["text"]
                      + (f" ×{spec['count']}" if spec["count"] > 1 else "") for spec in specs[:6])
    extra = ""
    ai = schedules.parse_spaced(f"{item.get('rebar_size') or ''}@{schedules._mm(item.get('rebar_spacing_m')) or ''}")
    if ai:
        same = any(schedules.parse_spaced(spec["text"]) == ai for spec in specs if spec["kind"] == "rebar")
        extra = (f" AI 배근 D{ai[0]}@{ai[1]}과 같은 문구가 있습니다." if same
                 else f" AI 배근 D{ai[0]}@{ai[1]}은 이 문구에 없습니다.")
    return {"plans": [{"plan": index, "drawing_number": plan["drawing_number"]} for index, plan in found],
            "text": shown, "extra": extra}


def attach_plan_evidence(members, matrix):
    """Add CAD label evidence to each member; counts are compared, never replaced."""
    index = {(row["category"], row["mark"]): row for row in (matrix or {}).get("marks", [])}
    plans = (matrix or {}).get("plans", [])
    has_marks = any(plan.get("labelled_marks") or plan.get("reference_copy_labels") for plan in plans)
    for category in CATEGORIES:
        for item in members.get(category) or []:
            if not isinstance(item, dict):
                continue
            mark = sid.normalize_mark(item.get("mark")) or re.sub(r"\s+", "", str(item.get("mark") or "")).upper()
            evidence = {"status": None, "ai_count": item.get("count"), "cad_count": None, "cad_floors": None,
                        "plans": [], "flags": [], "quantity_verified": False}
            row = index.get((category, mark))
            occurrences = [_place(o, plans) for o in (row or {}).get("occurrences") or []]
            levels = ["FDN"] if category == "foundations" else parse_zone_levels(item.get("zone"))
            note_values = {}
            if not has_marks:
                evidence["status"] = "no_cad_plan_marks"
            elif not occurrences:
                evidence["status"] = "mark_not_on_cad_plans"
                if row and row.get("typical_plans"):
                    evidence["flags"].append("on_typical_plans_only")
            else:
                candidates = [o for o in occurrences if _section_match(item.get("section"), o["section"])]
                if levels is None:
                    evidence["status"] = "zone_unknown"
                    evidence["plans"] = candidates
                else:
                    overlapping = [o for o in candidates if _overlaps(o["levels"], levels)]
                    if not overlapping:
                        evidence["status"] = "no_matching_cad_plan"
                        evidence["plans"] = candidates
                    else:
                        evidence["plans"] = overlapping
                        counts = {o["count"] for o in overlapping}
                        covered = set().union(*(set(o["levels"]) for o in overlapping)) & set(levels)
                        evidence["cad_floors"] = len(covered)
                        if len(counts) > 1:
                            evidence["status"] = "cad_counts_vary_by_floor"
                        else:
                            evidence["cad_count"] = counts.pop()
                            evidence["status"] = ("count_matches" if item.get("count") == evidence["cad_count"]
                                                  else "count_differs")
                        repeat = item.get("floor_repeat_count")
                        if isinstance(repeat, int) and not isinstance(repeat, bool) and repeat != evidence["cad_floors"]:
                            evidence["flags"].append("floor_repeat_differs")
            if evidence["status"] in ("mark_not_on_cad_plans", "no_matching_cad_plan") and row and levels is not None:
                copies = [c for c in map(lambda c: _place(c, plans), row.get("reference_copies") or [])
                          if _section_match(item.get("section"), c["section"]) and _overlaps(c["levels"], levels)]
                if copies:
                    evidence["status"] = "reference_copy_only"
                    evidence["reference_copies"] = [{k: c[k] for k in ("plan", "drawing_number", "count")} for c in copies]
                    evidence["plans"] = [o for o in occurrences if _overlaps(o["levels"], levels)] or occurrences
                    note_values.update(copies=_plan_names(copies), note=copies[0].get("note") or "-")
            if evidence["status"] in ("mark_not_on_cad_plans", "no_matching_cad_plan", "no_cad_plan_marks") \
                    and category == "foundations":
                found = _foundation_specs(item, plans, item.get("section"))
                if found:
                    evidence["status"] = "foundation_plan_specs"
                    evidence["spec_plans"] = found["plans"]
                    note_values.update(specs_plans=_plan_names(found["plans"]), specs=found["text"])
                    note_values["extra"] = found["extra"]
            note = _NOTES[evidence["status"]].format(ai=evidence["ai_count"], cad=evidence["cad_count"],
                                                     plans=_plan_names(evidence["plans"]),
                                                     **{k: v for k, v in note_values.items() if k != "extra"})
            note += note_values.get("extra", "")
            if "floor_repeat_differs" in evidence["flags"]:
                note += f" 반복 층수: AI {item.get('floor_repeat_count')}개 층, 도면명 기준 {evidence['cad_floors']}개 층."
            defaults = [d for d in map(lambda d: _place(d, plans), (row or {}).get("unlabelled_default_plans") or [])
                        if _section_match(item.get("section"), d["section"]) and (levels is None or _overlaps(d["levels"], levels))]
            if defaults and evidence["status"] not in ("no_cad_plan_marks",):
                evidence["flags"].append("unlabelled_default")
                note += (f" 평면 주기({_plan_names(defaults)})에 따르면 표기가 없는 부재도 이 부호입니다"
                         f"(\"{defaults[0]['note']}\") — 표기 개수보다 실제 개수가 많을 수 있습니다.")
            evidence["plans"] = [{k: o[k] for k in ("plan", "drawing_number", "count", "section", "levels") if k in o}
                                 for o in evidence["plans"]]
            evidence["note"] = note
            item["plan_evidence"] = evidence
    return members


def _member_specs(row_index, category, mark, levels):
    row = row_index.get((category, mark))
    if row and row.get("specs"):
        return row["specs"], levels
    prefixed = schedules.level_prefix(mark)
    if prefixed:
        base = row_index.get((category, prefixed[1]))
        if base:
            return [spec for spec in base.get("specs") or [] if spec.get("levels") and prefixed[0] in spec["levels"]], [prefixed[0]]
    return (row or {}).get("specs") or [], levels


def attach_schedule_evidence(members, matrix):
    """Compare extracted section and rebar values with CAD schedule rows of the same mark."""
    row_index = {(row["category"], row["mark"]): row for row in (matrix or {}).get("marks", [])}
    plans = (matrix or {}).get("plans", [])
    has_specs = any(row.get("specs") for row in row_index.values())
    for category in CATEGORIES:
        for item in members.get(category) or []:
            if not isinstance(item, dict):
                continue
            if not has_specs:
                evidence = {"status": "no_schedule_tables", "sheets": [], "fields": [], "flags": [], "quantity_verified": False}
            else:
                mark = schedules.schedule_mark(item.get("mark")) or re.sub(r"\s+", "", str(item.get("mark") or "")).upper()
                levels = None if category == "foundations" else parse_zone_levels(item.get("zone"))
                specs, levels = _member_specs(row_index, category, mark, levels)
                specs = [dict(spec, section=_place(spec, plans)["section"], rows=spec_rows(spec, matrix)) for spec in specs]
                evidence = schedules.schedule_evidence(category, item, specs, levels, _section_match)
            evidence["note"] = schedules.note_for(evidence)
            item["schedule_evidence"] = evidence
    return members
