"""Schedule definitions linked to plan label occurrences, from CAD only.

A mark is "defined" when it appears inside a schedule sheet (일람표) and "placed" when
it is labelled on a floor plan on a member-name layer (e.g. 0_STR_COL_NAME). Grid
bubbles and other layers never count. Unit-type plans (단위세대) are typical plans:
listed, never added to occurrences. A plan's section and levels come from its title;
totals multiply only by a level range read from that title. Labels are evidence for a
reviewer, not a verified member count; member counts are compared, never replaced.
"""
from __future__ import annotations

import re
import unicodedata

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
    if "기초" in compact and "일람" not in compact:
        return ["FDN"]
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




def build_member_links(cad_data):
    sheets_by_file = {f: d.get("sheets") or [] for f, d in (cad_data or {}).items() if isinstance(d, dict)}
    index_entries = [e for d in (cad_data or {}).values() if isinstance(d, dict) for e in d.get("index_entries") or []]
    registry = sid.build_sheet_registry(sheets_by_file, index_entries=index_entries)
    rows, plans, placed = {}, [], {}

    def plan_ref(slot, sheet, scope):
        """Sheet details live once in `plans`; placements carry its index and number."""
        plans.append({"drawing_number": sheet.get("drawing_number"), "drawing_title": sheet.get("drawing_title"),
                      "index_title": slot.get("index_title"), "file": sheet.get("file"),
                      "frame_handle": sheet.get("frame_handle"), "section": scope["section"],
                      "levels": scope["levels"], "kind": scope["kind"], "labelled_marks": 0})
        return len(plans) - 1

    def row(category, mark):
        return rows.setdefault((category, mark), {"category": category, "mark": mark, "definitions": [],
                                                   "occurrences": [], "typical_plans": [], "flags": []})

    slots = [slot for sequence in registry["sequence"].values() for slot in sequence if slot.get("cad_sheet")]
    schedules = []
    for slot in slots:
        sheet = slot["cad_sheet"]
        scope = parse_sheet_scope(sheet.get("drawing_title") or slot.get("index_title"))
        if any(word in _compact(slot.get("index_title")) for word in SCHEDULE_WORDS):
            scope["kind"] = "schedule"
        marks = sheet.get("marks") or []
        if scope["kind"] == "schedule":
            schedules.append((plan_ref(slot, sheet, scope), sheet, marks, slot))
            continue
        if scope["kind"] not in ("floor_plan", "unit_type_plan"):
            continue
        plan = plan_ref(slot, sheet, scope)
        labelled = 0
        for entry in marks:
            layered = {}
            for layer, count in entry["layers"].items():
                category = layer_category(layer)
                if category:
                    layered[category] = layered.get(category, 0) + count
            if not layered:
                continue
            category = max(layered, key=layered.get)
            labelled += layered[category]
            placed.setdefault(entry["mark"], set()).add(category)
            target = row(category, entry["mark"])
            ref = {"plan": plan, "drawing_number": sheet.get("drawing_number"), "count": layered[category]}
            if scope["kind"] == "floor_plan":
                ref.update(section=scope["section"], levels=scope["levels"])
                target["occurrences"].append(ref)
            else:
                target["typical_plans"].append(ref)
        plans[plan]["labelled_marks"] = labelled
    for plan, sheet, marks, slot in schedules:
        title_categories = {c for c in (title_category(sheet.get("drawing_title")), title_category(slot.get("index_title"))) if c}
        for entry in marks:
            on_plans = placed.get(entry["mark"])
            if on_plans and len(on_plans) == 1:
                category = next(iter(on_plans))
            else:
                category = title_category(slot.get("index_title")) or title_category(sheet.get("drawing_title")) or "unknown"
            target = row(category, entry["mark"])
            target["definitions"].append({"plan": plan, "drawing_number": sheet.get("drawing_number")})
            if title_categories and (category not in title_categories or len(title_categories) > 1):
                if "schedule_category_differs" not in target["flags"]:
                    target["flags"].append("schedule_category_differs")
    for target in rows.values():
        known = [o for o in target["occurrences"] if o["levels"]]
        target["label_total_known_part"] = sum(o["count"] * len(o["levels"]) for o in known)
        target["label_total"] = target["label_total_known_part"] if len(known) == len(target["occurrences"]) else None
        if len(known) != len(target["occurrences"]):
            target["flags"].append("floor_count_unknown")
        if target["definitions"] and not target["occurrences"]:
            target["flags"].append("defined_not_on_plans")
        if (target["occurrences"] or target["typical_plans"]) and not target["definitions"]:
            target["flags"].append("on_plans_not_defined")
    ordered = sorted(rows.values(), key=lambda r: (CATEGORIES.index(r["category"]) if r["category"] in CATEGORIES else 99,
                                                    re.sub(r"\d+", lambda m: m.group().zfill(6), r["mark"])))
    return {"schema": "quantity_member_links_v1", "marks": ordered, "plans": plans,
            "issues": registry["issues"], "quantity_verified": False}


_NOTES = {
    "count_matches": "CAD 평면 표기 {cad}개와 같습니다({plans}). 실제 개수 확인은 필요합니다.",
    "count_differs": "AI {ai}개, CAD 평면 표기 {cad}개({plans}) — 확인이 필요합니다.",
    "cad_counts_vary_by_floor": "층별 CAD 평면 표기 개수가 다릅니다({plans}) — 층별로 나눠 확인해 주세요.",
    "no_matching_cad_plan": "같은 동·층의 CAD 평면도에서 이 부호 표기를 찾지 못했습니다(표기된 도면: {plans}).",
    "mark_not_on_cad_plans": "CAD 평면도에서 이 부호 표기를 찾지 못했습니다.",
    "zone_unknown": "층 정보를 해석하지 못해 CAD 평면 표기와 비교하지 못했습니다.",
    "no_cad_plan_marks": "비교할 CAD 평면도 부호 근거가 없습니다.",
}


def _plan_names(occurrences):
    return ", ".join(sorted({o["drawing_number"] or "?" for o in occurrences})) or "-"


def attach_plan_evidence(members, matrix):
    """Add CAD label evidence to each member; counts are compared, never replaced."""
    index = {(row["category"], row["mark"]): row for row in (matrix or {}).get("marks", [])}
    has_marks = any(plan.get("labelled_marks") for plan in (matrix or {}).get("plans", []))
    for category in CATEGORIES:
        for item in members.get(category) or []:
            if not isinstance(item, dict):
                continue
            mark = sid.normalize_mark(item.get("mark")) or re.sub(r"\s+", "", str(item.get("mark") or "")).upper()
            evidence = {"status": None, "ai_count": item.get("count"), "cad_count": None, "cad_floors": None,
                        "plans": [], "flags": [], "quantity_verified": False}
            row = index.get((category, mark))
            if not has_marks:
                evidence["status"] = "no_cad_plan_marks"
            elif not row or not row["occurrences"]:
                evidence["status"] = "mark_not_on_cad_plans"
                if row and row["typical_plans"]:
                    evidence["flags"].append("on_typical_plans_only")
            else:
                levels = parse_zone_levels(item.get("zone"))
                candidates = [o for o in row["occurrences"] if _section_match(item.get("section"), o["section"])]
                if levels is None:
                    evidence["status"] = "zone_unknown"
                    evidence["plans"] = candidates
                else:
                    overlapping = [o for o in candidates if o["levels"] and set(o["levels"]) & set(levels)]
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
            note = _NOTES[evidence["status"]].format(ai=evidence["ai_count"], cad=evidence["cad_count"],
                                                     plans=_plan_names(evidence["plans"]))
            if "floor_repeat_differs" in evidence["flags"]:
                note += f" 반복 층수: AI {item.get('floor_repeat_count')}개 층, 도면명 기준 {evidence['cad_floors']}개 층."
            evidence["note"] = note
            item["plan_evidence"] = evidence
    return members
