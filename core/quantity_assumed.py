"""What a takeoff or rebar run decided on its own (임의로 정한 것).

The floor takeoff and the rebar go on with an assumed value wherever the drawings say nothing
(a slab thickness, a plain wall's bars, a mat thickness, where an unlabelled wall footing runs).
Each such decision is kept here with its value, where it was used and roughly how much rides on
it, so a person sees them all in one place afterwards and can confirm or change them.
"""
from __future__ import annotations

CONCRETE, REBAR = "㎥", "kg"


def _span(storeys):
    if len(storeys) <= 3:
        return ", ".join(storeys)
    return f"{storeys[0]}~{storeys[-1]} 등 {len(storeys)}개 층"


class Assumed:
    def __init__(self):
        self._items = {}

    def add(self, key, label, value, building=None, storey=None, amount=0.0, unit="", change="", counted=True):
        """One use of an assumption. ``amount``: the quantity resting on it (m³ or kg); ``counted``:
        False when that part is kept out of the totals until a person confirms it."""
        item = self._items.get(key)
        if item is None:
            item = self._items[key] = {"key": key, "label": label, "values": [], "where": {}, "amount": 0.0,
                                       "left_out": 0.0, "unit": unit, "change": change}
        if value not in item["values"]:
            item["values"].append(value)
        if building:
            storeys = item["where"].setdefault(building, [])
            if storey and storey not in storeys:
                storeys.append(storey)
        item["amount"] += amount or 0.0
        if not counted:
            item["left_out"] += amount or 0.0

    def items(self):
        """Kept out of the totals first (they wait for a person), then by how much rides on them."""
        out = []
        for item in sorted(self._items.values(), key=lambda i: (not i["left_out"], -i["amount"])):
            out.append({"key": item["key"], "label": item["label"], "value": " · ".join(item["values"]),
                        "where": [b + (" " + _span(s) if s else "") for b, s in item["where"].items()],
                        "amount": round(item["amount"], 1), "left_out": round(item["left_out"], 1),
                        "unit": item["unit"], "change": item["change"]})
        return out
