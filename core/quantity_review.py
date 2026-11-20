"""2D·3D review of a free CAD takeoff, and the fixes people make there.

``review_data`` puts together what a person checks before trusting the numbers: every building's
storeys at their elevation and storey height, the sheet each storey was built from, the coloured
plan of that sheet, and the storey's concrete, formwork, rebar and architectural quantities, with
the 3D model of the floor job. ``render_html`` makes one page of it (the 3D viewer, the plans and
the storey table) that works offline: the folder command writes it next to the Excel, the web page
serves it for a floor job.

Fixes made on the page come back as feedback: storey heights (``apply_storey_heights``), the
values the takeoff assumed (quantity_assumed.EDITABLE), foundation thickness and the members added
or taken out in the 3D (quantity_edits). The web page saves them with the floor job and the
takeoff runs again with them; the folder command reads them with ``--feedback``.
"""
import copy
import json
import os
import re

STOREY_HEIGHT_M = (1.5, 12.0)   # what a storey height may be set to
MAX_FEEDBACK_BUILDINGS = 50
MAX_FEEDBACK_STOREYS = 200

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "core")
ASSETS = {
    "three": os.path.join(_STATIC, "vendor", "three-r128.min.js"),
    "mass3d": os.path.join(_STATIC, "js", "quantity_mass3d.js"),
    "deck_css": os.path.join(_STATIC, "css", "quantity_deck.css"),
    "review": os.path.join(_STATIC, "js", "quantity_review.js"),
    "review_css": os.path.join(_STATIC, "css", "quantity_review.css"),
}


def clean_storey_heights(raw):
    """{building: {storey key: m}} with only real buildings' names, storey keys and heights in range."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for name, storeys in list(raw.items())[:MAX_FEEDBACK_BUILDINGS]:
        if not isinstance(name, str) or not isinstance(storeys, dict):
            continue
        kept = {}
        for key, value in list(storeys.items())[:MAX_FEEDBACK_STOREYS]:
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9]{1,6}", key):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if STOREY_HEIGHT_M[0] <= number <= STOREY_HEIGHT_M[1]:
                kept[key] = round(number, 3)
        if kept:
            out[name[:100]] = kept
    return out


def apply_storey_heights(model, heights):
    """The model with the storey heights people set: the storey gets the new height and every storey
    above it moves up or down by the difference, so the elevations stay stacked. The model given is
    not changed; storeys set this way carry ``height_from = "person"``."""
    heights = clean_storey_heights(heights)
    if not heights or not (model or {}).get("buildings"):
        return model
    model = copy.deepcopy(model)
    for building in model["buildings"]:
        given = heights.get(building.get("name")) or {}
        if not given:
            continue
        shift = 0.0
        for level in building.get("levels") or []:
            if shift and level.get("elev") is not None:
                level["elev"] = round(float(level["elev"]) + shift, 3)
            new = given.get(level.get("key"))
            if new is None:
                continue
            old = float(level.get("height") or 0)
            if abs(new - old) < 1e-6:
                continue
            level["height_before"] = old
            level["height"] = new
            level["height_from"] = "person"
            shift += new - old
    return model


def clean_feedback(raw):
    """What a review page sends back, checked: storey heights, assumed values, foundations, edits."""
    from .quantity_assumed import clean_overrides
    from . import quantity_edits
    raw = raw if isinstance(raw, dict) else {}
    foundations = {}
    for name, value in list((raw.get("foundations") or {}).items())[:MAX_FEEDBACK_BUILDINGS] if isinstance(raw.get("foundations"), dict) else []:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if isinstance(name, str) and 0.1 <= number <= 5.0:
            foundations[name[:100]] = round(number, 3)
    edits = raw.get("edits")
    return {"storey_heights": clean_storey_heights(raw.get("storey_heights")),
            "overrides": clean_overrides(raw.get("overrides") if isinstance(raw.get("overrides"), dict) else {}),
            "foundations": foundations,
            "edits": quantity_edits.clean(edits) if isinstance(edits, list) else []}


def _storey_kind(key):
    if key.startswith("B"):
        return "basement"
    if key in ("R", "PH", "PHR"):
        return "roof"
    return "ground" if key == "1" else "typical"


def plan_view(views, building, level):
    """The plan picture of one storey: the sheet it was built from, else a structural view of that
    building whose floor names or covers it (the page's planSvgFor)."""
    mine = [v for v in views or [] if v.get("building") == building and v.get("svg")]

    def covers(view):
        if view.get("floor") == level.get("label"):
            return True
        found = re.fullmatch(r"(\d+)~(\d+)층", view.get("floor") or "")
        key = level.get("key") or ""
        return bool(found) and key.isdigit() and int(found.group(1)) <= int(key) <= int(found.group(2))

    sheet = level.get("sheet")
    ranked = ([v for v in mine if sheet and v.get("sheet") == sheet]
              + [v for v in mine if v.get("discipline") != "arch" and covers(v)] + [v for v in mine if covers(v)])
    return ranked[0] if ranked else None


def _arch_view(views, building, level, arch_row):
    sheet = (arch_row or {}).get("sheet")
    for view in views or []:
        if view.get("building") == building and view.get("svg") and view.get("discipline") == "arch":
            if (sheet and view.get("sheet") == sheet) or (not sheet and view.get("floor") == level.get("label")):
                return view
    return None


def _num(value, digits=2):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def review_data(model, takeoff, rebar=None, arch=None, views=None, title="", feedback=None, assumed=None):
    """Everything the review page shows, as one JSON-ready dict."""
    model = model or {}
    feedback = clean_feedback(feedback)
    takeoff_rows = {b["name"]: {r["key"]: r for r in b.get("rows") or []} for b in (takeoff or {}).get("buildings") or []}
    rebar_rows = {b["name"]: {r["key"]: r for r in b.get("rows") or []} for b in (rebar or {}).get("buildings") or []}
    arch_rows = {b["name"]: {r["key"]: r for r in b.get("rows") or []} for b in (arch or {}).get("buildings") or []}
    pictures, picture_index = [], {}

    def picture(view):
        if not view:
            return None
        key = (view.get("building"), view.get("sheet"), view.get("floor"), view.get("plan_id"), view.get("discipline"))
        if key not in picture_index:
            picture_index[key] = len(pictures)
            pictures.append({"svg": view["svg"], "sheet": view.get("sheet") or "", "title": view.get("title") or "",
                             "floor": view.get("floor") or "", "discipline": view.get("discipline") or ""})
        return picture_index[key]

    buildings = []
    for building in model.get("buildings") or []:
        name = building.get("name") or ""
        plans = building.get("plans") or []
        lift = float(((building.get("site") or {}).get("lift")) or 0.0)
        storeys = []
        for index, level in enumerate(building.get("levels") or []):
            key = level.get("key") or ""
            plan = plans[level["plan"]] if isinstance(level.get("plan"), int) and level["plan"] < len(plans) else {}
            measure = plan.get("measure") or {}
            row = takeoff_rows.get(name, {}).get(key) or {}
            bars = rebar_rows.get(name, {}).get(key) or {}
            walls = arch_rows.get(name, {}).get(key) or {}
            concrete, formwork = row.get("concrete") or {}, row.get("formwork") or {}
            arch_walls = walls.get("walls") or {}
            storeys.append({
                "index": index, "key": key, "label": level.get("label") or key, "kind": _storey_kind(key),
                "elev": _num((level.get("elev") or 0.0) + lift, 3), "height": _num(level.get("height"), 3),
                "height_before": _num(level.get("height_before"), 3), "height_from": level.get("height_from") or "",
                "sheet": level.get("sheet") or "", "sheets": row.get("sheets") or {},
                "walls": len(plan.get("walls") or []), "columns": len(plan.get("cols") or []),
                "beams": len(plan.get("beams") or []), "slab_area": _num(plan.get("slab_area"), 1),
                "openings": _num(sum(measure.get("openings") or []) + sum(measure.get("stairs") or []), 1),
                "concrete": {k: _num(v) for k, v in concrete.items()}, "formwork": {k: _num(v) for k, v in formwork.items()},
                "rebar_kg": _num(bars.get("kg") if not isinstance(bars.get("kg"), dict) else bars["kg"].get("total"), 1),
                "arch": {"masonry": _num(_wall_area(arch_walls, "masonry"), 1), "alc": _num(_wall_area(arch_walls, "alc"), 1),
                         "dry": _num(_wall_area(arch_walls, "dry"), 1), "windows": walls.get("windows"), "doors": walls.get("doors")}
                if walls else None,
                "approximate": bool(row.get("approximate")), "notes": (row.get("notes") or [])[:6],
                "plan": picture(plan_view(views, name, level)), "arch_plan": picture(_arch_view(views, name, level, walls)),
            })
        foundation = takeoff_rows.get(name, {}).get("FT")
        totals = next((b.get("totals") for b in (takeoff or {}).get("buildings") or [] if b.get("name") == name), {}) or {}
        rough = next((b.get("approximate_totals") for b in (takeoff or {}).get("buildings") or [] if b.get("name") == name), {}) or {}
        bar_totals = next((b.get("totals") for b in (rebar or {}).get("buildings") or [] if b.get("name") == name), {}) or {}
        arch_totals = next((b.get("totals") for b in (arch or {}).get("buildings") or [] if b.get("name") == name), {}) or {}
        buildings.append({
            "name": name, "kind": building.get("kind") or "", "site": building.get("site") or {},
            "storeys": storeys,
            "foundation": {"height": _num(foundation.get("height"), 3), "concrete": _num((foundation.get("concrete") or {}).get("total"))}
            if foundation else None,
            "totals": {"concrete": _num((totals.get("concrete") or {}).get("total"), 1),
                       "formwork": _num((totals.get("formwork") or {}).get("total"), 1),
                       "rebar_t": _num((bar_totals.get("total") or 0) / 1000.0, 2),
                       "approximate_concrete": _num((rough.get("concrete") or {}).get("total"), 1),
                       "arch": {k: _num(v, 1) for k, v in arch_totals.items() if isinstance(v, (int, float))}},
        })
    if assumed is None:
        assumed = [a for part in (takeoff, rebar, arch) for a in (part or {}).get("assumed") or []]
    from .quantity_assumed import EDITABLE
    return {
        "title": title, "model": model, "buildings": buildings, "pictures": pictures,
        "heights_from_drawings": bool(model.get("heights_from_drawings")),
        "assumed": [{"key": a.get("key"), "label": a.get("label"), "value": a.get("value"), "unit": a.get("unit"),
                     "amount": a.get("amount"), "set": a.get("set"), "edit": a.get("edit")} for a in assumed],
        "editable": EDITABLE,
        "feedback": feedback,
    }


def _wall_area(walls, kind):
    value = walls.get(kind)
    if isinstance(value, dict):
        value = value.get("area", value.get("total"))
    return value


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _json_for_script(data):
    text = json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render_html(data, options=None):
    """The review page as one HTML file: three.js, the 3D viewer and the review script inlined, so it
    opens offline. ``options`` go to the page script (web: {"save_url", "floor_job_id", "csrf"})."""
    title = data.get("title") or "수량산출"
    options = dict(options or {})
    escaped_title = (title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>2D·3D 도면 검토 — {escaped_title}</title>"
        f"<style>{_read(ASSETS['deck_css'])}</style><style>{_read(ASSETS['review_css'])}</style>"
        '</head><body class="rv-body"><div id="cbl-review" class="rv-root"></div>'
        f'<script type="application/json" id="cbl-review-data">{_json_for_script(data)}</script>'
        f'<script type="application/json" id="cbl-review-options">{_json_for_script(options)}</script>'
        f"<script>{_read(ASSETS['three'])}</script><script>{_read(ASSETS['mass3d'])}</script>"
        f"<script>{_read(ASSETS['review'])}</script>"
        "<script>window.cblQuantityReview.mount(document.getElementById('cbl-review'),"
        "JSON.parse(document.getElementById('cbl-review-data').textContent),"
        "JSON.parse(document.getElementById('cbl-review-options').textContent));</script>"
        "</body></html>")
