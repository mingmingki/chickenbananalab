"""Schedule tables, plan notes and foundation specs read from one CAD frame's texts.

Texts come from one identified frame: model space plus texts kept inside blocks, without the
frame's own title-block values. Two table layouts are read:
  label column  row labels (부호, SIZE, MAIN BAR, HOOP BAR, 상부근, 하부근, 늑근, ...) in the left
                column and one column per mark (column and beam schedules); a position row
                (양단부 / 중앙부 / ALL) under the marks splits a mark into sub-columns.
  header row    column labels in one header row or a stacked header band (층별, 두께, 수직근,
                수평근 / NO., TYPE, D, Lx, Ly ...); rows per floor range under a mark written
                above the header (wall schedules) or one row per mark (footing schedules).
Cells keep the text as drawn; "↑" copies the nearest value above in the same column (or below
when nothing is above, which is recorded). Notes say where another plan must be read
(참고/참조) or which mark an unlabelled member takes (미표기 기둥 : C1). Foundation plans
without member labels keep their rebar and size texts. All of this is evidence for review;
member values are compared, never replaced, and no quantity is verified here.
"""
from __future__ import annotations

import re
import statistics
import unicodedata

from . import quantity_sheet_identity as sid

MAX_TABLE_MARKS = 300
MAX_NOTES = 12
MAX_SPECS = 30
DITTO = {"↑", "〃", "″", "\""}

ROW_LABELS = (
    ("mark", r"부호|MARK|SYMBOL"),
    ("size", r"SIZE|단면크기|크기|B[X×]D|B[X×]H"),
    ("main_bar", r"MAINBAR|MAINREBAR|주근"),
    ("hoop_end", r"(?:HOOPBAR|HOOP|띠철근|후프)\((?:END|단부)\)"),
    ("hoop_mid", r"(?:HOOPBAR|HOOP|띠철근|후프)\((?:MID|CENTER|중앙부?)\)"),
    ("hoop", r"HOOPBAR|HOOP|띠철근|후프"),
    ("tie_bar", r"TIEBAR|TIE|보조띠철근"),
    ("top_bar", r"상부근|상부주근|TOPBAR|TOP"),
    ("bottom_bar", r"하부근|하부주근|BOTTOMBAR|BOTBAR|BOTTOM|BOT\.?"),
    ("stirrup", r"늑근|스터럽|STIRRUPS?|STR\.?"),
    ("skin_bar", r"SKINBAR|측면근|SIDEBAR"),
)
POSITION_WORDS = {"ALL", "전단면", "양단부", "중앙부", "단부", "중앙", "END", "ENDS", "MID", "CENTER",
                  "외단부", "내단부", "BOTHENDS", "EXT.", "INT."}
HEADER_LABELS = (
    ("levels", r"층별|층|구분층|LEVEL|FLOOR|STORY"),
    ("fck", r"FCK(?:\(MPA\))?|강도(?:\(MPA\))?"),
    ("thickness", r"두께(?:\(MM\))?|벽두께|THK\.?|THICK(?:NESS)?|WALLTHK\.?"),
    ("vertical_bar", r"수직근|VERT\.?|VERTICAL(?:BAR)?|V-?BAR"),
    ("horizontal_bar", r"수평근|HORI\.?|HORIZ\.?|HORIZONTAL(?:BAR)?|H-?BAR"),
    ("remark", r"비고|REMARKS?|NOTES?"),
    ("mark", r"NO\.?|부호|MARK|SYMBOL"),
    ("type", r"TYPE|형식"),
    ("depth", r"D(?:\(MM\))?"),
    ("length_x", r"LX(?:\(MM\))?"),
    ("length_y", r"LY(?:\(MM\))?"),
    ("column_x", r"CX(?:\(MM\))?"),
    ("column_y", r"CY(?:\(MM\))?"),
    ("height", r"H(?:\(MM\))?"),
)
PARENT_LABELS = (("footing_size", r"FOOTINGSIZE|기초크기"), ("column_size", r"COLUMNSIZE|기둥크기"),
                 ("rebar", r"RE-?BAR(?:SCHEDULE)?|배근|철근"))
GENERIC_LEAF = re.compile(r"^(?:\d|\(MM\))$")
LEVEL_PREFIX = re.compile(r"^(-\d{1,2}|B\d{1,2}|R|PH|P|\d{1,2})(?=[A-Z])(.+)$")
SPEC_PATTERNS = (("rebar", re.compile(r"(?:[A-Z]{0,3}D)\d{2}\s*@\s*\d{2,4}", re.I)),
                 ("size", re.compile(r"\d{3,5}\s*[X×x]\s*\d{3,5}(?:\s*[X×x]\s*\d{3,5})?")),
                 ("thickness", re.compile(r"(?:THK|T)\s*[.=:]?\s*\d{3,4}", re.I)))
SPEC_LABEL = re.compile(r"^[가-힣][가-힣A-Za-z0-9()]{1,7}$")


def _key(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).upper()


def _width(text, height):
    return height * sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.75 for ch in str(text))


def _center(text):
    return text["x"] + _width(text["text"], text["h"]) / 2


def _label(patterns, text):
    key = _key(text)
    return next((name for name, pattern in patterns if re.fullmatch(pattern, key)), None)


def schedule_mark(text):
    """A mark as written in a schedule; a level-prefixed mark such as "-1G1" is kept as is."""
    mark = sid.normalize_mark(text)
    if mark:
        return mark
    key = _key(text)
    return key if re.fullmatch(r"-\d{1,2}[A-Z][A-Z0-9]{0,10}", key) and re.search(r"\d$", key) else None


def level_prefix(mark):
    """("B1", "G1") for "-1G1", ("R", "G1") for "RG1"; None when the rest is not a mark."""
    match = LEVEL_PREFIX.match(mark or "")
    if not match or not sid.normalize_mark(match.group(2)):
        return None
    prefix = match.group(1)
    level = ("B" + prefix[1:] if prefix.startswith("-") else "PH" if prefix in ("P", "PH")
             else str(int(prefix)) if prefix.isdigit() else prefix)
    return level, match.group(2)


def _level_span(low, high):
    def rank(level):
        return -int(level[1:]) if level.startswith("B") else int(level)
    a, b = rank(low), rank(high)
    if a > b:
        a, b = b, a
    return ["B" + str(-n) if n < 0 else str(n) for n in range(a, b + 1) if n != 0]


def parse_level_text(text):
    """Floor range of a schedule row ("1F - 13F", "B2F~B1F", "지하2층~지상1층"); None if unreadable."""
    key = _key(text).replace("~", "-")
    match = re.fullmatch(r"(B?)(\d{1,3})F?(?:-(B?)(\d{1,3})F?)?", key)
    if match:
        low = ("B" if match.group(1) else "") + str(int(match.group(2)))
        if match.group(4) is None:
            return [low]
        return _level_span(low, ("B" if match.group(3) else "") + str(int(match.group(4))))
    if key in ("RF", "ROOF", "지붕", "지붕층"):
        return ["R"]
    from . import quantity_member_links
    return quantity_member_links.parse_zone_levels(text)


def _rows_by_y(texts):
    """Texts grouped into rows (top first); a text joins a row within 0.6 of the taller height."""
    rows = []
    for text in sorted(texts, key=lambda t: -t["y"]):
        if rows and abs(rows[-1][0] - text["y"]) <= 0.6 * max(rows[-1][1], text["h"]):
            rows[-1][2].append(text)
            rows[-1][1] = max(rows[-1][1], text["h"])
        else:
            rows.append([text["y"], text["h"], [text]])
    return [(y, h, sorted(items, key=lambda t: t["x"])) for y, h, items in rows]


def _nearest(columns, x, limit):
    best = min(columns, key=lambda column: abs(column["x"] - x)) if columns else None
    return best if best is not None and abs(best["x"] - x) <= limit else None


def _spacing(xs, fallback):
    xs = sorted(xs)
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 0]
    return min(gaps) if gaps else fallback


# ── header-row tables (walls, footings) ─────────────────────────────────────

def _header_tables(texts, claimed):
    tables = []
    rows = _rows_by_y([t for t in texts if id(t) not in claimed])
    headerish = lambda t: (_label(HEADER_LABELS, t["text"]) or _label(PARENT_LABELS, t["text"])
                           or GENERIC_LEAF.match(_key(t["text"])))
    for index, (y, h, items) in enumerate(rows):
        keys = [_label(HEADER_LABELS, t["text"]) for t in items]
        recognized = [k for k in keys if k]
        if len(recognized) < 3 or not ({"levels", "mark"} & set(recognized)) or any(id(t) in claimed for t in items):
            continue
        band = [(y, h, items)]
        for step in (-1, 1):
            position = index + step
            while 0 <= position < len(rows) and len(band) < 4:
                other_y, other_h, other = rows[position]
                edge = band[0] if step < 0 else band[-1]
                if abs(edge[0] - other_y) > 2.6 * max(edge[1], other_h):
                    break
                if sum(1 for t in other if headerish(t)) < max(1, 0.6 * len(other)):
                    break
                band.insert(0, rows[position]) if step < 0 else band.append(rows[position])
                position += step
        start_key = next(k for k in keys if k in ("levels", "mark"))
        starts = [t for t, k in zip(items, keys) if k == start_key]
        for number, start in enumerate(starts):
            left = start["x"] - 2 * start["h"]
            right = starts[number + 1]["x"] - 2 * start["h"] if number + 1 < len(starts) else float("inf")
            table = _read_header_table(rows, band, left, right, start_key, claimed)
            if table:
                tables.extend(table)
    return tables


def _leaf_columns(band, left, right):
    """Lowest header cells of a header band; a stacked label takes its upper label as parent."""
    band_rows = [[t for t in items if left <= _center(t) < right] for _y, _h, items in band]
    parent_of, has_child = {}, set()
    for level, items in enumerate(band_rows):
        for text in items:
            for upper in reversed(band_rows[:level]):
                near = [u for u in upper if abs(_center(u) - _center(text)) <= _width(u["text"], u["h"]) / 2 + 3 * u["h"]]
                if near:
                    parent = min(near, key=lambda u: abs(_center(u) - _center(text)))
                    parent_of[id(text)] = parent
                    has_child.add(id(parent))
                    break
            if id(text) not in parent_of and GENERIC_LEAF.match(_key(text["text"])):
                # "1", "2", "(mm)" mean nothing alone; a centred parent label may be drawn wider or narrower.
                near = [u for upper in band_rows[:level] for u in upper
                        if abs(_center(u) - _center(text)) <= _width(u["text"], u["h"]) / 2 + 8 * u["h"]]
                if near:
                    parent = min(near, key=lambda u: abs(_center(u) - _center(text)))
                    parent_of[id(text)] = parent
                    has_child.add(id(parent))
    columns = []
    for items in band_rows:
        for text in items:
            if id(text) in has_child:
                continue
            parent = parent_of.get(id(text))
            own = _label(HEADER_LABELS, text["text"])
            parent_key = parent and (_label(PARENT_LABELS, parent["text"]) or _label(HEADER_LABELS, parent["text"]))
            leaf = _key(text["text"])
            if parent_key == "rebar" and leaf.isdigit():
                key = "rebar_" + leaf
            elif GENERIC_LEAF.match(leaf) and parent_key:
                key = parent_key
            else:
                key = own or "col_" + leaf.lower()
            label = (parent["text"] + " " + text["text"]) if parent is not None else text["text"]
            columns.append({"key": key, "label": label, "x": _center(text), "text": text})
    keys = [c["key"] for c in columns]
    for column in columns:
        if keys.count(column["key"]) > 1:
            same = sorted(c["x"] for c in columns if c["key"] == column["key"])
            column["key"] = column["key"] + "_" + str(same.index(column["x"]) + 1)
    return columns


def _read_header_table(rows, band, left, right, start_key, claimed):
    columns = _leaf_columns(band, left, right)
    keys = {c["key"] for c in columns}
    if len(columns) < 3:
        return []
    band_ids = {id(t) for _y, _h, items in band for t in items if left <= _center(t) < right}
    band_top, band_bottom = band[0][0], band[-1][0]
    height = statistics.median(t["h"] for _y, _h, items in band for t in items)
    limit = 0.75 * _spacing([c["x"] for c in columns], 10 * height)
    mark_column = next((c for c in columns if c["key"] == "mark"), None)
    level_column = next((c for c in columns if c["key"] == "levels"), None)
    data, last_y = [], band_bottom
    for y, h, items in rows:
        if y >= band_bottom - 0.6 * h:
            continue
        inside = [t for t in items if left <= _center(t) < right and id(t) not in claimed]
        if not inside:
            continue
        gap = last_y - y
        if gap > (8 if not data else 4) * max(height, h):
            break
        if any(_label(HEADER_LABELS, t["text"]) for t in inside) and sum(1 for t in inside if _label(HEADER_LABELS, t["text"])) >= 3:
            break  # the next header below
        cells = {}
        for text in inside:
            column = _nearest(columns, _center(text), limit)
            if column is not None:
                cells.setdefault(column["key"], []).append(text)
        values = {key: " ".join(t["text"] for t in sorted(found, key=lambda t: t["x"])) for key, found in cells.items()}
        if start_key == "mark":
            if not mark_column or not schedule_mark(values.get("mark")):
                break
        else:
            level_text = values.get("levels")
            if not level_text or not (level_text in DITTO or parse_level_text(level_text)):
                break
        data.append((values, [t for found in cells.values() for t in found]))
        last_y = y
    if not data:
        return []
    for _values, used in data:
        claimed.update(id(t) for t in used)
    claimed.update(band_ids)
    resolved = _resolve_dittos([values for values, _used in data])
    labels = {c["key"]: c["label"] for c in columns}
    category = ("walls" if {"vertical_bar", "horizontal_bar"} & keys
                else "foundations" if {"length_x", "length_y", "depth"} & keys else None)
    if start_key == "mark":
        tables = []
        for values, dittos in resolved:
            mark = schedule_mark(values.pop("mark", None))
            tables.append({"mark": mark, "category": category, "layout": "header_row", "labels": labels,
                           "rows": [{"position": None, "levels_text": None, "levels": None,
                                     "values": values, "ditto": dittos}]})
        return tables
    candidates = [t for y, h, items in rows if band_top < y <= band_top + 4.5 * height
                  for t in items if left <= _center(t) < right and schedule_mark(t["text"]) and id(t) not in claimed]
    if not candidates:
        return []
    mark_text = min(candidates, key=lambda t: (t["y"] - band_top, abs(_center(t) - (left + min(right, left + 400 * height)) / 2)))
    claimed.add(id(mark_text))
    table_rows = []
    for values, dittos in resolved:
        level_text = values.pop("levels", None)
        table_rows.append({"position": None, "levels_text": level_text, "levels": parse_level_text(level_text),
                           "values": values, "ditto": dittos})
    return [{"mark": schedule_mark(mark_text["text"]), "category": category, "layout": "header_row",
             "labels": labels, "rows": table_rows}]


def _resolve_dittos(rows):
    resolved = [(dict(values), []) for values in rows]
    for index, (values, dittos) in enumerate(resolved):
        for key, value in list(values.items()):
            if value not in DITTO:
                continue
            above = next((resolved[i][0][key] for i in range(index - 1, -1, -1)
                          if resolved[i][0].get(key) not in (None,) + tuple(DITTO)), None)
            below = next((rows[i][key] for i in range(index + 1, len(rows))
                          if rows[i].get(key) not in (None,) + tuple(DITTO)), None)
            values[key] = above if above is not None else below
            dittos.append(key if above is not None else key + ":from_below")
    return resolved


# ── label-column tables (columns, beams) ────────────────────────────────────

def _label_column_tables(texts, claimed):
    free = [t for t in texts if id(t) not in claimed]
    anchors = [t for t in free if _label(ROW_LABELS, t["text"]) == "mark"]
    tables = []
    for anchor in anchors:
        h = anchor["h"]
        mark_row = sorted((t for t in free if t is not anchor and abs(t["y"] - anchor["y"]) <= 0.6 * max(h, t["h"])
                           and t["x"] > anchor["x"] + h and schedule_mark(t["text"])), key=lambda t: t["x"])
        if not mark_row:
            continue
        mark_xs = [_center(t) for t in mark_row]
        mark_spacing = _spacing(mark_xs, 30 * h)
        label_limit = mark_xs[0] - 0.5 * mark_spacing
        lower = [a for a in anchors if a is not anchor and a["y"] < anchor["y"] - h and abs(a["x"] - anchor["x"]) <= 4 * h]
        bottom = max((a["y"] for a in lower), default=float("-inf"))
        labels = sorted((t for t in free if bottom + 0.6 * h < t["y"] < anchor["y"] - 0.6 * h
                         and _center(t) < label_limit and abs(t["x"] - anchor["x"]) <= 8 * h
                         and _label(ROW_LABELS, t["text"]) not in (None, "mark")), key=lambda t: -t["y"])
        if not labels:
            continue
        right_texts = [t for t in free if bottom + 0.6 * h < t["y"] < anchor["y"] - 0.6 * h and _center(t) >= label_limit]
        first_label_y = labels[0]["y"]
        columns = [{"mark": t["text"], "position": None, "x": x} for t, x in zip(mark_row, mark_xs)]
        for _y, _h, items in _rows_by_y([t for t in right_texts if t["y"] > first_label_y + 0.6 * h]):
            if items and all(_key(t["text"]) in POSITION_WORDS for t in items):
                bounds = [(a + b) / 2 for a, b in zip(mark_xs, mark_xs[1:])]
                columns = []
                for text in items:
                    x = _center(text)
                    owner = sum(1 for bound in bounds if x > bound)
                    columns.append({"mark": mark_row[owner]["text"], "position": text["text"], "x": x})
                claimed.update(id(t) for t in items)
                break
        limit = 0.6 * _spacing([c["x"] for c in columns], 30 * h)
        cells = {}
        for label in labels:
            key = _label(ROW_LABELS, label["text"])
            row_texts = [t for t in right_texts if abs(t["y"] - label["y"]) <= 0.6 * max(label["h"], t["h"])]
            for text in row_texts:
                column = _nearest(columns, _center(text), limit)
                if column is not None:
                    cells.setdefault((id(column), key), []).append(text)
                    claimed.add(id(text))
            claimed.add(id(label))
        claimed.add(id(anchor))
        claimed.update(id(t) for t in mark_row)
        found_keys = {key for _column, key in cells}
        category = ("beams" if {"top_bar", "bottom_bar", "stirrup"} & found_keys
                    else "columns" if {"main_bar", "hoop", "hoop_mid", "hoop_end"} & found_keys else None)
        names = {_label(ROW_LABELS, t["text"]): t["text"] for t in labels}
        by_mark = {}
        for column in columns:
            values = {key: " ".join(t["text"] for t in sorted(cells[(id(column), key)], key=lambda t: t["x"]))
                      for key in names if (id(column), key) in cells}
            by_mark.setdefault(column["mark"], []).append({"position": column["position"], "levels_text": None,
                                                           "levels": None, "values": values, "ditto": []})
        for mark_text, rows in by_mark.items():
            if any(row["values"] for row in rows):
                tables.append({"mark": schedule_mark(mark_text), "category": category, "layout": "label_column",
                               "labels": names, "rows": rows})
    return tables


def read_tables(texts):
    claimed = set()
    tables = _header_tables(texts, claimed) + _label_column_tables(texts, claimed)
    return [t for t in tables if t["mark"]][:MAX_TABLE_MARKS]


# ── notes and foundation specs ──────────────────────────────────────────────

NOTE_START = re.compile(r"^\s*(?:\d{1,2}\s*[.)]|[*※]|\(\d{1,2}\))")
REFERENCE = re.compile(r"참\s*고|참\s*조|REFER|SEE\s", re.I)
UNLABELLED = re.compile(r"미\s*표\s*기\s*(?P<word>[^:：]{1,12}?)\s*[:：]\s*(?P<mark>[A-Za-z][A-Za-z0-9\-]{0,10})")


def _next_line(texts, line, direction):
    h = line["h"]
    found = [t for t in texts if t is not line and abs(t["x"] - line["x"]) <= 1.5 * h
             and 0 < direction * (line["y"] - t["y"]) <= 2.6 * max(h, t["h"])]
    return min(found, key=lambda t: abs(line["y"] - t["y"])) if found else None


def _full_note(texts, hit):
    start, steps = hit, 0
    while not NOTE_START.match(start["text"]) and steps < 4:
        previous = _next_line(texts, start, -1)
        if previous is None:
            break
        start, steps = previous, steps + 1
    lines, current = [start], start
    for _ in range(6):
        following = _next_line(texts, current, 1)
        if following is None or NOTE_START.match(following["text"]):
            break
        lines.append(following)
        current = following
    return re.sub(r"\s+", " ", " ".join(line["text"] for line in lines)).strip()


def area_of(text):
    """parking / building from a plan title, a section name or a note subject; None if neither."""
    compact = _key(text)
    if "주차장" in compact or "PARKING" in compact:
        return "parking"
    if re.search(r"아파트|주동|\d{2,4}동|동구조|APT", compact):
        return "building"
    return None


def classify_note(text):
    unlabelled = UNLABELLED.search(text)
    if unlabelled:
        from . import quantity_member_links
        word = unlabelled.group("word").strip()
        return {"kind": "unlabelled_default", "text": text, "member_word": word,
                "category": quantity_member_links.title_category(word),
                "mark": sid.normalize_mark(unlabelled.group("mark"))}
    if REFERENCE.search(text) and re.search(r"평면|PLAN|도면", text, re.I) and not re.search(r"일반사항|상세|표준", text):
        subject = re.match(r"^\s*(?:\d{1,2}\s*[.)]\s*)?(.*?)(?:부분|영역|구간|부위)", text)
        return {"kind": "reference", "text": text, "area": area_of(subject.group(1)) if subject else None}
    return None


def read_notes(texts):
    notes, seen = [], set()
    for hit in texts:
        if not (REFERENCE.search(hit["text"]) or UNLABELLED.search(hit["text"])):
            continue
        note = classify_note(_full_note(texts, hit))
        if note and note["text"] not in seen:
            seen.add(note["text"])
            notes.append(note)
        if len(notes) >= MAX_NOTES:
            break
    return notes


def read_specs(texts):
    """Rebar, size and thickness texts of a foundation plan (non-plotting Defpoints excluded)."""
    found, order = {}, []
    labels = [t for t in texts if SPEC_LABEL.match(t["text"])]
    for text in texts:
        if str(text.get("layer") or "").casefold() == "defpoints":
            continue
        kind = next((name for name, pattern in SPEC_PATTERNS if pattern.search(text["text"])), None)
        if kind is None:
            continue
        key = (kind, text["text"])
        if key not in found:
            found[key] = {"count": 0, "labels": {}}
            order.append(key)
        found[key]["count"] += 1
        if kind == "size" and len(text["text"]) <= 25:  # a pit or opening size names what it sizes
            near = [t for t in labels if abs(t["x"] - text["x"]) + abs(t["y"] - text["y"]) <= 6 * max(t["h"], text["h"])]
            if near:
                label = min(near, key=lambda t: abs(t["x"] - text["x"]) + abs(t["y"] - text["y"]))["text"]
                found[key]["labels"][label] = found[key]["labels"].get(label, 0) + 1
    rows = [{"kind": kind, "text": value, "count": found[(kind, value)]["count"],
             "label": max(found[(kind, value)]["labels"], key=found[(kind, value)]["labels"].get, default=None)}
            for kind, value in order]
    rows.sort(key=lambda row: -row["count"])
    return rows[:MAX_SPECS]


def is_foundation_plan(title):
    compact = _key(title)
    return ("기초" in compact or "FOUNDATION" in compact or "FOOTING" in compact) and "일람" not in compact \
        and "SCHEDULE" not in compact


def read_sheet_content(texts, title):
    return {"tables": read_tables(texts), "notes": read_notes(texts),
            "specs": read_specs(texts) if is_foundation_plan(title) else []}


# ── values and comparison with extracted members ────────────────────────────

def parse_size(text):
    match = re.search(r"(\d[\d,]*)\s*[X×x*]\s*(\d[\d,]*)", str(text or ""))
    return tuple(sorted(int(v.replace(",", "")) for v in match.groups())) if match else None


def parse_bars(text):
    groups = re.findall(r"(\d+)\s*-\s*[A-Z]{0,3}D(\d{2})", _key(text))
    return [(int(count), int(diameter)) for count, diameter in groups]


def parse_spaced(text):
    match = re.search(r"[A-Z]{0,3}D(\d{2})@(\d[\d,]*)", _key(text))
    return (int(match.group(1)), int(match.group(2).replace(",", ""))) if match else None


def parse_mm(text):
    key = _key(text).replace(",", "")
    return int(key) if key.isdigit() else None


def _diameter(value):
    match = re.search(r"(\d{2})", str(value or ""))
    return int(match.group(1)) if match else None


def _mm(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value * 1000))


def _values(rows, keys):
    return [row["values"][key] for row in rows for key in keys if row["values"].get(key)]


def _compare_section(ai_a, ai_b, texts):
    sizes = {parse_size(t) for t in texts} - {None}
    if not sizes:
        return "cad_missing"
    a, b = _mm(ai_a), _mm(ai_b)
    if a is None and b is None:
        return "ai_missing"
    if a is not None and b is not None:
        return "match" if any(all(abs(x - y) <= 1 for x, y in zip(sorted((a, b)), size)) for size in sizes) else "differs"
    one = a if a is not None else b
    return "match" if any(abs(one - v) <= 1 for size in sizes for v in size) else "differs"


def _compare_bars(size, count, texts):
    groups = [parse_bars(t) for t in texts]
    groups = [g for g in groups if g]
    if not groups:
        return "cad_missing"
    diameter = _diameter(size)
    if diameter is None and not isinstance(count, int):
        return "ai_missing"
    ok_size = diameter is None or any(diameter == d for g in groups for _c, d in g)
    totals = {sum(c for c, _d in g) for g in groups} | {c for g in groups for c, _d in g}
    ok_count = not isinstance(count, int) or isinstance(count, bool) or count in totals
    return "match" if ok_size and ok_count else "differs"


def _compare_spaced(size, spacing_m, texts):
    spaced = {parse_spaced(t) for t in texts} - {None}
    if not spaced:
        return "cad_missing"
    diameter, spacing = _diameter(size), _mm(spacing_m)
    if diameter is None and spacing is None:
        return "ai_missing"
    ok = any((diameter is None or diameter == d) and (spacing is None or spacing == s) for d, s in spaced)
    return "match" if ok else "differs"


def _compare_mm(value_m, texts):
    values = {parse_mm(t) for t in texts} - {None}
    if not values:
        return "cad_missing"
    value = _mm(value_m)
    if value is None:
        return "ai_missing"
    return "match" if any(abs(value - v) <= 1 for v in values) else "differs"


def _ai_size(a, b):
    parts = [str(_mm(v)) for v in (a, b) if _mm(v) is not None]
    return "×".join(parts) or None


def _ai_spaced(size, spacing):
    spacing = _mm(spacing)
    return (str(size or "") + ("@" + str(spacing) if spacing is not None else "")) or None


def compare_member(category, item, rows):
    """Field-by-field comparison of one extracted member with schedule rows of its mark."""
    fields = []

    def add(field, label, ai, cad_texts, status):
        fields.append({"field": field, "label": label, "ai": ai,
                       "cad": ", ".join(dict.fromkeys(cad_texts)) or None, "status": status})

    get = item.get
    if category == "columns":
        sizes = _values(rows, ["size"])
        add("section", "단면", _ai_size(get("width_m"), get("depth_m")), sizes,
            _compare_section(get("width_m"), get("depth_m"), sizes))
        bars = _values(rows, ["main_bar"])
        add("main_bar", "주근", (f"{get('main_rebar_count')}-{get('main_rebar_size')}"
                                 if get("main_rebar_size") or get("main_rebar_count") else None),
            bars, _compare_bars(get("main_rebar_size"), get("main_rebar_count"), bars))
        hoops = _values(rows, ["hoop", "hoop_mid", "hoop_end"])
        add("hoop", "띠철근", _ai_spaced(get("tie_rebar_size"), get("tie_spacing_m")), hoops,
            _compare_spaced(get("tie_rebar_size"), get("tie_spacing_m"), hoops))
    elif category == "beams":
        sizes = _values(rows, ["size"])
        add("section", "단면", _ai_size(get("width_m"), get("depth_m")), sizes,
            _compare_section(get("width_m"), get("depth_m"), sizes))
        bars = _values(rows, ["top_bar", "bottom_bar", "main_bar"])
        add("main_bar", "주근(상부·하부)", (f"{get('main_rebar_count')}-{get('main_rebar_size')}"
                                         if get("main_rebar_size") or get("main_rebar_count") else None),
            bars, _compare_bars(get("main_rebar_size"), get("main_rebar_count"), bars))
        stirrups = _values(rows, ["stirrup"])
        add("stirrup", "늑근", _ai_spaced(get("stirrup_size"), get("stirrup_spacing_m")), stirrups,
            _compare_spaced(get("stirrup_size"), get("stirrup_spacing_m"), stirrups))
    elif category == "walls":
        thickness = _values(rows, ["thickness"])
        add("thickness", "두께", str(_mm(get("thickness_m"))) if _mm(get("thickness_m")) is not None else None,
            thickness, _compare_mm(get("thickness_m"), thickness))
        bars = _values(rows, ["vertical_bar", "horizontal_bar"])
        add("rebar", "수직·수평근", _ai_spaced(get("rebar_size"), get("rebar_spacing_m")), bars,
            _compare_spaced(get("rebar_size"), get("rebar_spacing_m"), bars))
    elif category == "foundations":
        depth = _values(rows, ["depth", "thickness"])
        add("thickness", "두께", str(_mm(get("thickness_m"))) if _mm(get("thickness_m")) is not None else None,
            depth, _compare_mm(get("thickness_m"), depth))
        plan_sizes = [f"{row['values']['length_x']} X {row['values']['length_y']}" for row in rows
                      if row["values"].get("length_x") and row["values"].get("length_y")]
        add("plan_size", "평면 크기", _ai_size(get("length_m"), get("width_m")), plan_sizes,
            _compare_section(get("length_m"), get("width_m"), plan_sizes))
        bars = _values(rows, ["rebar_1", "rebar_2", "rebar"])
        add("rebar", "배근", _ai_spaced(get("rebar_size"), get("rebar_spacing_m")), bars,
            _compare_spaced(get("rebar_size"), get("rebar_spacing_m"), bars))
    return fields


_NOTES = {
    "schedule_matches": "CAD 일람표({sheets})와 같습니다: {fields}.",
    "schedule_differs": "CAD 일람표({sheets})와 다릅니다: {fields} — 확인이 필요합니다.",
    "schedule_not_compared": "CAD 일람표({sheets})에 이 부호가 있으나 비교할 값이 없습니다.",
    "no_schedule_row_for_level": "CAD 일람표({sheets})에 이 층 범위의 행이 없습니다.",
    "not_in_schedule": "CAD 일람표에서 이 부호의 정의를 찾지 못했습니다.",
    "no_schedule_tables": "비교할 CAD 일람표 값이 없습니다.",
}
_STATUS_WORDS = {"match": "같음", "differs": "다름"}


def _field_text(field):
    if field["status"] == "match":
        return f"{field['label']} {field['cad']}"
    return f"{field['label']} AI {field['ai']} / CAD {field['cad']}"


def schedule_evidence(category, item, specs, levels, section_match):
    """Evidence for one member from the schedule specs of its mark (never changes the member)."""
    evidence = {"status": None, "sheets": [], "fields": [], "flags": [], "quantity_verified": False}
    candidates = [spec for spec in specs if section_match(item.get("section"), spec.get("section"))]
    if levels is not None:
        prefixed = [spec for spec in candidates if spec.get("levels") is not None]
        if prefixed:
            matching = [spec for spec in prefixed if set(spec["levels"]) & set(levels)]
            candidates = matching + [spec for spec in candidates if spec.get("levels") is None]
    if not candidates:
        evidence["status"] = "not_in_schedule"
        return evidence
    evidence["sheets"] = sorted({spec.get("drawing_number") or "?" for spec in candidates})
    if len(evidence["sheets"]) > 1:
        evidence["flags"].append("multiple_schedule_sheets")
    rows = [dict(row, levels=row.get("levels") or (parse_level_text(row["levels_text"]) if row.get("levels_text") else None))
            for spec in candidates for row in spec["rows"]]
    if levels is not None and any(row.get("levels") for row in rows):
        level_rows = [row for row in rows if row.get("levels") and set(row["levels"]) & set(levels)]
        if not level_rows:
            evidence["status"] = "no_schedule_row_for_level"
            return evidence
        rows = level_rows
    if any(d.endswith(":from_below") for row in rows for d in row.get("ditto") or []):
        evidence["flags"].append("ditto_from_below")
    fields = compare_member(category, item, rows)
    evidence["fields"] = fields
    statuses = {f["status"] for f in fields}
    evidence["status"] = ("schedule_differs" if "differs" in statuses
                          else "schedule_matches" if "match" in statuses else "schedule_not_compared")
    return evidence


def note_for(evidence):
    compared = [f for f in evidence.get("fields") or [] if f["status"] in _STATUS_WORDS]
    if evidence["status"] == "schedule_differs":
        compared = [f for f in compared if f["status"] == "differs"]
    note = _NOTES[evidence["status"]].format(sheets=", ".join(evidence.get("sheets") or []) or "-",
                                             fields=", ".join(_field_text(f) for f in compared) or "-")
    if "ditto_from_below" in evidence.get("flags", []):
        note += " 일부 값은 \"↑\" 표시로 아래 행에서 이어 읽었습니다."
    return note
