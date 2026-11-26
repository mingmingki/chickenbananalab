"""What a takeoff or rebar run decided on its own (임의로 정한 것), and what a person set instead.

The floor takeoff and the rebar go on with an assumed value wherever the drawings say nothing
(a slab thickness, a plain wall's bars, a mat thickness, where an unlabelled wall footing runs).
Each such decision is kept here with its value, where it was used and roughly how much rides on
it, so a person sees them all in one place afterwards. Most of them a person can change on the
spot (``EDITABLE``): the change is kept with the floor job (``clean_overrides``) and the item then
shows the person's value. Numbers people set are learned: other jobs start from their median.
"""
from __future__ import annotations

import math
import re

CONCRETE, REBAR = "㎥", "kg"

# what a person may set in place of an assumption: the input it takes and its bounds
EDITABLE = {
    "slab_thickness": {"kind": "number", "unit": "mm", "min": 80, "max": 600, "step": 10, "label": "슬래브 두께"},
    "beam_depth": {"kind": "number", "unit": "mm", "min": 200, "max": 2500, "step": 50, "label": "일람표에 춤이 없는 보"},
    "window_height": {"kind": "number", "unit": "m", "min": 0.3, "max": 3.0, "step": 0.1, "label": "외벽 개구부 높이"},
    "parapet": {"kind": "number", "unit": "m", "min": 0.2, "max": 3.0, "step": 0.1, "label": "옥탑 지붕 위 난간벽 높이"},
    "wall_footing_place": {"kind": "toggle", "on": "외벽 아래에 넣기", "label": "줄기초 위치"},
    "plain_wall_bars": {"kind": "bar", "hint": "예: D10@300, HD13@200", "label": "부호 없는 벽 배근"},
    "slab_bars": {"kind": "bar", "hint": "예: D10@200 (상·하부 양방향)", "label": "슬래브 배근"},
    "stair_bars": {"kind": "number", "unit": "kg/㎡", "min": 5, "max": 200, "step": 1, "label": "계단 철근"},
    "grade": {"kind": "choice", "choices": ["SD400", "SD500", "SD600"], "label": "강종 표기 없는 철근"},
    "foundation_bars": {"kind": "bar", "hint": "예: D16@300 (상·하부 양방향)", "label": "기초 매트 배근"},
    "mat_extra_bars": {"kind": "toggle", "on": "매트 전체에 깔기", "label": "매트 보강근 범위"},
    "door_height": {"kind": "number", "unit": "m", "min": 1.5, "max": 3.5, "step": 0.1, "label": "문 높이 (창호일람표에 없는 문)"},
    "window_opening_height": {"kind": "number", "unit": "m", "min": 0.3, "max": 3.0, "step": 0.1, "label": "창 높이 (창호일람표에 없는 창)"},
    "ceiling_height": {"kind": "number", "unit": "m", "min": 2.0, "max": 5.0, "step": 0.05, "label": "반자 높이 (벽 마감 높이)"},
    "excavation_method": {"kind": "choice", "choices": ["오픈컷", "흙막이 (수직 굴착)"], "label": "굴착 방식"},
    "excavation_margin": {"kind": "number", "unit": "m", "min": 0.3, "max": 3.0, "step": 0.1, "label": "작업 여유폭"},
    "excavation_slope": {"kind": "number", "unit": "", "min": 0.2, "max": 2.0, "step": 0.1, "label": "굴착 경사 (1 : n)"},
    "exterior_finish": {"kind": "choice", "choices": ["외부 수성페인트", "석재", "외단열 미장", "금속패널", "타일"], "label": "외벽 마감재"},
    "waterproof_rise": {"kind": "number", "unit": "m", "min": 0.1, "max": 3.0, "step": 0.1, "label": "욕실 벽 방수 높이"},
}
TAKEOFF_KEYS = ("slab_thickness", "beam_depth", "window_height", "parapet", "wall_footing_place",
                "excavation_method", "excavation_margin", "excavation_slope", "exterior_finish")
REBAR_KEYS = ("plain_wall_bars", "slab_bars", "stair_bars", "grade", "foundation_bars", "mat_extra_bars")
ARCH_KEYS = ("slab_thickness", "door_height", "window_opening_height", "ceiling_height", "waterproof_rise")
LEARNED = ("slab_thickness", "beam_depth", "window_height", "parapet", "stair_bars", "door_height", "window_opening_height",
           "ceiling_height", "waterproof_rise", "excavation_margin", "excavation_slope")  # numbers other jobs start from
BAR_TEXT = re.compile(r"^[A-Z]{0,3}D(10|13|16|19|22|25|29|32|35)@(\d{2,4})$")


def clean_overrides(raw):
    """{key: value} a person set, checked against ``EDITABLE`` (anything else is dropped)."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, value in list(raw.items())[:40]:
        spec = EDITABLE.get(key)
        if not spec:
            continue
        kind = spec["kind"]
        if kind == "number" and not isinstance(value, bool):
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and spec["min"] <= number <= spec["max"]:
                out[key] = round(number, 3)
        elif kind == "bar":
            text = re.sub(r"\s+", "", str(value or "")).upper()
            match = BAR_TEXT.match(text)
            if match and 50 <= int(match.group(2)) <= 1000:
                out[key] = text
        elif kind == "choice" and value in spec["choices"]:
            out[key] = value
        elif kind == "toggle" and isinstance(value, bool):
            out[key] = value
    return out


def pick(key, overrides, learned, default):
    """(value, source) for one editable value: the person's, else other jobs' median, else the default."""
    if key in (overrides or {}):
        return overrides[key], "person"
    value = (learned or {}).get("override:" + key) if key in LEARNED else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and EDITABLE[key]["min"] <= value <= EDITABLE[key]["max"]:
        return value, "learned"
    return default, "assumed"


def said(text, source):
    return text + {"person": " (입력값)", "learned": " (다른 현장 입력값)"}.get(source, "")


def _span(storeys):
    if len(storeys) <= 3:
        return ", ".join(storeys)
    return f"{storeys[0]}~{storeys[-1]} 등 {len(storeys)}개 층"


class Assumed:
    def __init__(self):
        self._items = {}

    def add(self, key, label, value, building=None, storey=None, amount=0.0, unit="", change="", counted=True,
            raw=None, person=False):
        """One use of an assumption. ``amount``: the quantity resting on it (m³ or kg); ``counted``:
        False when that part is kept out of the totals until a person confirms it; ``raw``: the value
        an edit box starts from; ``person``: a person set this value."""
        item = self._items.get(key)
        if item is None:
            item = self._items[key] = {"key": key, "label": label, "values": [], "where": {}, "amount": 0.0,
                                       "left_out": 0.0, "unit": unit, "change": change, "raw": raw, "person": False}
        if value not in item["values"]:
            item["values"].append(value)
        if building:
            storeys = item["where"].setdefault(building, [])
            if storey and storey not in storeys:
                storeys.append(storey)
        item["amount"] += amount or 0.0
        if not counted:
            item["left_out"] += amount or 0.0
        item["person"] = item["person"] or bool(person)

    def keep_unused(self, overrides, keys):
        """A value a person set that this run used nowhere still shows, so it can be changed back."""
        for key in keys:
            if key not in (overrides or {}) or key in self._items:
                continue
            spec, value = EDITABLE[key], overrides[key]
            text = (spec.get("on", "넣기") if value else "빼기") if spec["kind"] == "toggle" else (
                f"{value:g}{spec.get('unit', '')}" if spec["kind"] == "number" else str(value))
            self.add(key, spec["label"], text + " (입력값) — 이번 도면에는 쓰인 곳이 없어요", raw=value, person=True)

    def items(self):
        """Kept out of the totals first (they wait for a person), then by how much rides on them;
        what a person already set comes last."""
        out = []
        for item in sorted(self._items.values(), key=lambda i: (i["person"], not i["left_out"], -i["amount"])):
            row = {"key": item["key"], "label": item["label"], "value": " · ".join(item["values"]),
                   "where": [b + (" " + _span(s) if s else "") for b, s in item["where"].items()],
                   "amount": round(item["amount"], 1), "left_out": round(item["left_out"], 1),
                   "unit": item["unit"], "change": item["change"], "set": item["person"]}
            if item["key"] in EDITABLE and item["raw"] is not None:
                row["edit"] = dict(EDITABLE[item["key"]], value=item["raw"])
            out.append(row)
        return out
