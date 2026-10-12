"""Deterministic semantic definition layer for CanonicalTableCellModelV1.

This module deliberately stops at definition records.  It never creates plan
instances, counts, quantities, or synthetic cell values.
"""

from __future__ import annotations

import re
from typing import Any


HEADER_ALIASES = {
    "mark": ("mark", "부호", "기호", "부재명", "부재기호", "형태", "TYPE", "NO."),
    "level": ("level", "floor", "층", "층별", "적용층", "위치", "구간"),
    "width": ("width", "폭", "너비", "W", "B"),
    "depth": ("depth", "깊이", "춤", "D", "H"),
    "height": ("height", "높이", "고", "H"),
    "thickness": ("thickness", "두께", "THK", "T", "t"),
    "count": ("count", "수량", "개수", "QTY"),
    "concrete_strength": ("fck", "콘크리트강도", "콘크리트", "MPA"),
    "main_rebar": ("main bar", "main rebar", "주근", "주철근"),
    "top_rebar": ("top bar", "top rebar", "상부근", "상부철근"),
    "bottom_rebar": ("bottom bar", "bottom rebar", "하부근", "하부철근"),
    "vertical_rebar": ("vertical", "vertical bar", "수직근", "수직철근"),
    "horizontal_rebar": ("horizontal", "horizontal bar", "수평근", "수평철근"),
    "stirrup": ("stirrup", "hoop", "tie", "늑근", "스터럽", "후프"),
    "spacing": ("spacing", "간격", "@"),
    "section": ("section", "size", "단면", "크기", "규격"),
    "material": ("material", "재료", "사양", "specification"),
    "finish_floor": ("바닥", "floor finish", "바닥마감"),
    "finish_wall": ("벽", "wall finish", "벽마감"),
    "finish_ceiling": ("천정", "천장", "ceiling finish", "천장마감"),
    "window_width": ("창호폭", "window width", "폭"),
    "window_height": ("창호높이", "window height", "높이"),
    "window_type": ("창호형", "window type", "창호", "종류"),
    "glass": ("유리", "glass"),
    "frame": ("frame", "프레임", "창틀"),
    "remarks": ("remarks", "비고", "remark", "note"),
    "anchorage": ("anchorage", "정착", "정착길이"),
    "development": ("development", "development length", "Ld", "정착", "발달"),
    "dowel": ("dowel", "다월", "dowel bar"),
}


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_header(raw: str) -> dict[str, Any]:
    """Map a header by its text evidence; preserve unknown headers."""
    text = _compact(raw)
    folded = text.casefold()
    matches = []
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            a = _compact(alias).casefold()
            if a and (folded == a or a in folded):
                matches.append((field, alias))
    fields = sorted({field for field, _ in matches})
    return {
        "raw_header": text,
        "normalized_header_tokens": fields,
        "semantic_field_candidate": fields[0] if len(fields) == 1 else None,
        "ambiguous_candidates": fields if len(fields) > 1 else [],
        "status": "mapped" if len(fields) == 1 else ("ambiguous" if fields else "unknown_column"),
    }


def parse_typed_scalars(raw: str, field_hint: str | None = None) -> list[dict[str, Any]]:
    """Parse only evidence-bound notations; never infer field meaning."""
    text = str(raw or "")
    out: list[dict[str, Any]] = []

    def add(rule, value, unit, substring, parsed=None):
        out.append({"parsed_value": value, "unit": unit, "raw_substring": substring,
                    "parser_rule_id": rule, "field_hint": field_hint,
                    "parsed": parsed if parsed is not None else value})

    for m in re.finditer(r"(?P<a>\d+(?:\.\d+)?)\s*[x×]\s*(?P<b>\d+(?:\.\d+)?)", text, re.I):
        add("dimension_x", [float(m["a"]), float(m["b"])], "", m.group(0), "dimension")
    for m in re.finditer(r"(?:THK\.?|t\s*=)\s*(\d+(?:\.\d+)?)\s*(mm|m)?", text, re.I):
        unit = (m.group(2) or "").lower(); add("thickness", float(m.group(1)), unit, m.group(0))
    for m in re.finditer(r"(?P<grade>HD|D)\s*(?P<dia>\d+)\s*@\s*(?P<spacing>\d+)", text, re.I):
        add("rebar_spacing", {"grade": m["grade"].upper(), "diameter": int(m["dia"]),
                               "spacing": int(m["spacing"])}, "", m.group(0), "rebar")
    for m in re.finditer(r"(?P<count>\d+)\s*-\s*(?P<grade>HD|D)\s*(?P<dia>\d+)", text, re.I):
        add("rebar_count", {"count": int(m["count"]), "grade": m["grade"].upper(),
                             "diameter": int(m["dia"])}, "bar", m.group(0), "rebar")
    for m in re.finditer(r"\bSD\s*(400|500|600)\b", text, re.I):
        add("rebar_grade", m.group(0).replace(" ", "").upper(), "grade", m.group(0), "grade")
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(mm|m|MPa|kN/m[²2])\b", text, re.I):
        add("number_with_unit", float(m.group(1)), m.group(2), m.group(0))
    return out


def classify_row(raw_texts: list[str], *, role: str, has_header_evidence: bool,
                 explicit_repeat: bool = False, merged_scope: bool = False) -> str:
    texts = [_compact(t) for t in raw_texts if _compact(t)]
    joined = " ".join(texts)
    if not texts:
        return "blank"
    upper = joined.upper()
    if explicit_repeat:
        return "continuation_row"
    if merged_scope:
        return "merged_scope_row"
    if re.search(r"(^|\s)(NOTE|REMARK|비고|주기|참조|주의|공사|시공|엔지니어)", upper):
        return "note"
    if has_header_evidence and len(texts) >= 2 and not any(parse_typed_scalars(t) for t in texts):
        return "header"
    if role == "finish_schedule" and re.search(r"범례|LEGEND|외부마감|재료", joined, re.I):
        return "material_legend"
    mark_like = any(re.search(r"\b(?:[A-Z]{1,4}\d{1,4}|[A-Z]{1,4}-?\d{1,4})\b", t, re.I) for t in texts)
    if mark_like or role in {"finish_schedule", "window_schedule"} and len(texts) >= 2:
        return "data_definition"
    return "unresolved_row"


def canonical_field_value(raw: str, field: str | None, source_cell_id: str) -> dict[str, Any]:
    parsed = parse_typed_scalars(raw, field)
    return {"raw": raw, "typed": parsed, "source_cell_id": source_cell_id,
            "status": "parsed" if parsed else "missing_typed_pattern"}
