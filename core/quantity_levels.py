"""Floor levels and storey heights read from CAD, and height evidence for members.

Two kinds of level marks are read from each identified frame:
  elevation marks  a floor name paired with an elevation, "3층 바닥" beside "(EL+22.30)"
                   (or one text such as "GL±0=EL+16.20"), as on building elevations and sections;
  section marks    floor names on level lines of a section detail ("지하1층 SL", "주차장 지붕 SL",
                   "GL."); storey heights are the drawn distances between neighbouring lines.
The sheet title gives the section (101동, 주차장); a detail that names 주동 applies to the
buildings. Storey height of a level = elevation of the next level up minus its own. When
both kinds exist for one level and differ, the difference is reported. Heights are
floor-to-floor values from the drawings, not verified quantities.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict

EL_VALUE = re.compile(r"EL\.?\s*(?P<sign>[+＋\-−])?\s*(?P<value>\d+(?:,\d{3})*(?:\.\d+)?)", re.I)
SECTION_MARK = re.compile(r"\bS\.?L\b|^\s*G\.?\s*L\.?\s*$|\bF\.?L\b", re.I)
TOLERANCE_M = 0.02


def _norm(text):
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def level_key(text):
    """B2 / 1 / R (roof) / PH (penthouse) / PHR (penthouse roof) / GL, or None."""
    text = _norm(text)
    compact = re.sub(r"\s+", "", text)
    if "옥탑지붕" in compact:
        return "PHR"
    if "옥탑" in compact:
        return "PH"
    if "지붕" in compact:
        return "R"
    match = re.search(r"(지하)?(\d{1,3})층", compact)
    if match:
        return ("B" if match.group(1) else "") + str(int(match.group(2)))
    match = re.search(r"\b(B)?(\d{1,2})\s*F(?:L)?\b", text.upper())
    if match:
        return ("B" if match.group(1) else "") + str(int(match.group(2)))
    if re.search(r"G\.?\s*L", text.upper()):
        return "GL"
    return None


def rank(level):
    """Order of levels from the lowest basement to the penthouse roof."""
    if level == "GL":
        return 0.5
    if level.startswith("B"):
        return -int(level[1:])
    if level.isdigit():
        return int(level)
    return {"R": 1000, "PH": 1001, "PHR": 1002}.get(level)


def parse_elevation(text):
    match = EL_VALUE.search(_norm(text))
    if not match:
        return None
    value = float(match.group("value").replace(",", ""))
    if value > 500:  # written in millimetres
        value /= 1000.0
    return -value if match.group("sign") in ("-", "−") else value


def section_area(text):
    compact = re.sub(r"\s+", "", _norm(text))
    if "주차장" in compact:
        return "주차장"
    match = re.search(r"(\d{2,4})동", compact)
    if match:
        return match.group(1) + "동"
    if "주동" in compact or "아파트" in compact:
        return "주동"
    return None


def read_sheet_levels(texts):
    """Elevation marks {level: elevation_m} and section-detail storey heights of one frame."""
    named = [t for t in texts if level_key(t["text"]) and parse_elevation(t["text"]) is None and len(t["text"]) <= 24]
    elevations = defaultdict(Counter)
    for text in texts:
        value = parse_elevation(text["text"])
        if value is None:
            continue
        key = level_key(text["text"])
        if key is None:
            h = max(float(text.get("h") or 1.0), 1.0)
            near = [u for u in named if abs(u["y"] - text["y"]) <= 2.5 * max(h, u.get("h") or 1.0)
                    and abs(u["x"] - text["x"]) <= 40 * h]
            if near:
                key = level_key(min(near, key=lambda u: abs(u["x"] - text["x"]) + abs(u["y"] - text["y"]))["text"])
        if key:
            elevations[key][round(value, 3)] += 1
    marks = [t for t in texts if SECTION_MARK.search(_norm(t["text"])) and level_key(t["text"])]
    columns = []
    for mark in sorted(marks, key=lambda t: t["x"]):
        for column in columns:
            if abs(column[0]["x"] - mark["x"]) <= 30 * max(float(mark.get("h") or 1.0), 1.0):
                column.append(mark)
                break
        else:
            columns.append([mark])
    heights = []
    for column in columns:
        ordered = sorted(column, key=lambda t: t["y"])
        for low, high in zip(ordered, ordered[1:]):
            a, b = level_key(low["text"]), level_key(high["text"])
            if a == b or rank(a) is None or rank(b) is None or rank(a) >= rank(b):
                continue
            heights.append({"from": a, "to": b, "height_m": round((high["y"] - low["y"]) / 1000.0, 3),
                            "area": section_area(low["text"]) or section_area(high["text"])})
    result = {}
    if elevations:
        result["elevations"] = {level: values.most_common(1)[0][0] for level, values in elevations.items()}
        conflicts = {level: sorted(values) for level, values in elevations.items()
                     if max(values) - min(values) > TOLERANCE_M}
        if conflicts:
            result["elevation_conflicts"] = conflicts
    unique = []
    for row in heights:
        if row not in unique:
            unique.append(row)
    if unique:
        result["section_heights"] = unique
    return result or None


def _storeys(elevations, uncertain=()):
    """{level: height_m} from elevations; neighbouring floors only, never through an uncertain level."""
    ordered = sorted((level for level in elevations if level != "GL" and rank(level) is not None), key=rank)
    out, rejected = {}, []
    for low, high in zip(ordered, ordered[1:]):
        adjacent = (rank(high) - rank(low) == 1 or (low.startswith("B") and high in ("1", "R"))
                    or (low.isdigit() and high == "R") or (low == "R" and high in ("PH", "PHR")) or (low == "PH" and high == "PHR"))
        if not adjacent or low in uncertain or high in uncertain:
            continue
        height = round(elevations[high] - elevations[low], 3)
        if 0 < height <= 12:
            out[low] = height
        else:
            rejected.append({"level": low, "values": [elevations[low], elevations[high]], "reason": "storey_height_out_of_range"})
    return out, rejected


def build_level_table(*cad_sets):
    """Storey heights per section from every identified frame of the uploads."""
    from . import quantity_member_links as links

    by_section = defaultdict(lambda: {"elevations": defaultdict(Counter), "sheets": defaultdict(set), "mixed": set()})
    section_rows = defaultdict(list)
    for cad in cad_sets:
        for data in (cad or {}).values():
            if not isinstance(data, dict):
                continue
            for sheet in data.get("sheets") or []:
                levels = sheet.get("levels")
                if not levels:
                    continue
                title = sheet.get("drawing_title") or ""
                section = links.parse_sheet_scope(title)["section"] or section_area(title) or "공통"
                for level, value in (levels.get("elevations") or {}).items():
                    by_section[section]["elevations"][level][value] += 1
                    by_section[section]["sheets"][level].add(sheet.get("drawing_number") or "?")
                by_section[section]["mixed"].update(levels.get("elevation_conflicts") or {})  # one drawing, several values
                for row in levels.get("section_heights") or []:
                    area = row.get("area") or section_area(title) or section
                    section_rows[area].append(dict(row, sheet=sheet.get("drawing_number")))
    sections, conflicts = {}, []
    for section, data in by_section.items():
        elevations, uncertain = {}, set(data["mixed"])
        for level, values in data["elevations"].items():
            ranked = values.most_common()
            elevations[level] = ranked[0][0]
            if max(values) - min(values) > TOLERANCE_M:
                # a clear majority across drawings still decides; otherwise the level stays open
                if len(ranked) > 1 and ranked[0][1] < 2 * ranked[1][1]:
                    uncertain.add(level)
                conflicts.append({"section": section, "level": level, "values": sorted(values),
                                  "reason": "elevation_differs_between_drawings"})
        for level in sorted(data["mixed"]):
            conflicts.append({"section": section, "level": level, "values": [elevations.get(level)],
                              "reason": "several_elevations_in_one_drawing"})
        storeys, rejected = _storeys(elevations, uncertain)
        conflicts.extend(dict(row, section=section) for row in rejected)
        heights = {level: {"height_m": h, "source": "elevation", "sheets": sorted(data["sheets"][level])}
                   for level, h in storeys.items()}
        sections[section] = {"elevations": elevations, "heights": heights, "uncertain": sorted(uncertain)}
    for area, rows in section_rows.items():
        target = sections.setdefault(area, {"elevations": {}, "heights": {}})
        values = defaultdict(Counter)
        sheets = defaultdict(set)
        for row in rows:
            values[row["from"]][row["height_m"]] += 1
            sheets[row["from"]].add(row["sheet"] or "?")
        for level, counter in values.items():
            height = counter.most_common(1)[0][0]
            if not 0 < height <= 12:
                continue
            known = target["heights"].get(level)
            if known and abs(known["height_m"] - height) > 0.05:
                conflicts.append({"section": area, "level": level, "values": [known["height_m"], height],
                                  "reason": "section_detail_differs_from_elevation"})
                continue
            if not known:
                target["heights"][level] = {"height_m": height, "source": "section_detail", "sheets": sorted(sheets[level])}
    return {"schema": "quantity_levels_v1", "sections": sections, "conflicts": conflicts}


def storey_height(table, section, level):
    """(height_m, source row) for a level of a section; buildings fall back to 주동 details.

    Without a known section the height is used only when every named section agrees.
    """
    from . import quantity_member_links as links

    sections = (table or {}).get("sections") or {}
    if not section or str(section).strip() in ("미상", "전체"):
        found = {name: (info.get("heights") or {}).get(level) for name, info in sections.items() if name != "공통"}
        values = {row["height_m"] for row in found.values() if row}
        if len(values) == 1:
            name, row = next((n, r) for n, r in found.items() if r)
            return row["height_m"], dict(row, section=name)
        return None, None
    candidates = [name for name in sections if name != "공통" and links._section_match(section, name)]
    exact = re.sub(r"\s+", "", str(section))
    candidates.sort(key=lambda name: re.sub(r"\s+", "", name) != exact)  # the same name before a partial match
    if re.search(r"\d+동", str(section)):
        candidates.append("주동")
    for name in candidates + ["공통"]:
        row = (sections.get(name) or {}).get("heights", {}).get(level)
        if row:
            return row["height_m"], dict(row, section=name)
    return None, None


_NOTES = {
    "height_matches": "AI 높이 {ai}m가 CAD 층고 {cad}m와 같습니다.",
    "height_within_slab": "AI 높이 {ai}m는 CAD 층고(바닥~바닥) {cad}m보다 {diff}m 낮습니다 — 슬래브 두께를 뺀 값이면 맞습니다.",
    "height_differs": "AI 높이 {ai}m, CAD 층고(바닥~바닥) {cad}m — 확인이 필요합니다.",
    "cad_height_only": "CAD 층고(바닥~바닥)는 {cad}m입니다(AI 높이 없음).",
    "no_cad_height": "이 동·층의 CAD 층고를 찾지 못했습니다.",
}
_SOURCE = {"elevation": "입면·단면 EL", "section_detail": "단면 상세 레벨선 간격"}


def _level_text(level):
    names = {"R": "지붕층", "PH": "옥탑층", "PHR": "옥탑지붕"}
    if level in names:
        return names[level]
    return ("지하" + level[1:] + "층") if level.startswith("B") else (level + "층")


def attach_height_evidence(members, table):
    """Compare extracted wall/column heights with CAD storey heights (never replaced)."""
    from . import quantity_member_links as links

    for category in ("walls", "columns"):
        for item in members.get(category) or []:
            if not isinstance(item, dict):
                continue
            levels = links.parse_zone_levels(item.get("zone")) or []
            found = []
            for level in levels:
                height, row = storey_height(table, item.get("section"), level)
                if height is not None:
                    found.append((level, height, row))
            grouped = defaultdict(set)
            for _l, _h, row in found:
                grouped[(row["section"], row["source"])].update(row["sheets"])
            evidence = {"status": "no_cad_height", "cad_height_m": None, "ai_height_m": item.get("height_m"),
                        "sources": [f"{section} {_SOURCE[source]} ({', '.join(sorted(sheets))})"
                                    for (section, source), sheets in sorted(grouped.items())],
                        "quantity_verified": False}
            counts = Counter(h for _l, h, _r in found)
            ai = item.get("height_m")
            numeric = isinstance(ai, (int, float)) and not isinstance(ai, bool)
            cad = None
            if counts:
                cad = counts.most_common(1)[0][0]  # the typical storey of the range
                evidence["cad_height_m"] = cad
                others = [f"{_level_text(level)} {h}m" for level, h, _r in found if h != cad]
                if others:
                    evidence["other_heights"] = others
                if not numeric:
                    evidence["status"] = "cad_height_only"
                elif abs(ai - cad) <= 0.05:
                    evidence["status"] = "height_matches"
                elif 0 < cad - ai <= 0.35:
                    evidence["status"] = "height_within_slab"
                else:
                    evidence["status"] = "height_differs"
            note = _NOTES[evidence["status"]].format(ai=ai, cad=cad if cad is not None else "-",
                                                     diff=round(cad - ai, 2) if cad is not None and numeric else "")
            if evidence.get("other_heights"):
                note += " 범위 안 다른 층고: " + ", ".join(evidence["other_heights"]) + "."
            if evidence["sources"]:
                note += " 근거: " + "; ".join(evidence["sources"]) + "."
            evidence["note"] = note
            item["height_evidence"] = evidence
    return members
