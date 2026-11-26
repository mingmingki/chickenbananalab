"""The finish schedule (실내재료마감표) from CAD (no AI): what each room is finished with.

A finish schedule is a table: a header row naming the columns (실명, 바닥, 걸레받이, 벽, 천장, and
often 천장고 / 반자높이), then a row per room with its materials, a cell often holding several lines
(the finish on top, then its base: "강마루 THK7.5 / 시멘트몰탈 THK40 / 액체방수"). The reading goes
by position: the header row is the line holding at least three of those headers; each column
spans halfway to its neighbours; each room row takes the texts nearest to it (by height) in each
column, top to bottom. One sheet may hold several tables (세대 / 공용부), each under its own header.

Each room comes out with its floor, baseboard, wall and ceiling finish (the top line, without
thickness and size notes), whether waterproofing is named anywhere in its row, and its ceiling
height when the table gives one. The architectural takeoff (quantity_arch_finishes) uses them in
place of the finishes it otherwise picks by the room's name.
"""
from __future__ import annotations

import re

FINISH_WORD = r"(\s*마\s*감(\s*재)?)?"
HEADERS = (
    ("height", re.compile(r"^(천\s*[장정]\s*고|반\s*자\s*(높\s*이|고)|C\.?\s*H\.?|CH)$", re.I)),
    ("room", re.compile(r"^(실\s*명|실|ROOM(\s*NAME)?|구\s*분|실\s*구\s*분)$", re.I)),
    ("floor", re.compile(r"^(바\s*닥|FLOOR)" + FINISH_WORD + "$", re.I)),
    ("base", re.compile(r"^(걸\s*레\s*받\s*이|굽\s*도\s*리|BASE(\s*BOARD)?)$", re.I)),
    ("wall", re.compile(r"^(벽|벽\s*체|WALL)" + FINISH_WORD + "$", re.I)),
    ("ceiling", re.compile(r"^(천\s*[장정]|반\s*자|CEILING)" + FINISH_WORD + "$", re.I)),
)
# the second header line under a column (its finish and its base), never a material
SUBHEAD = re.compile(r"^(마\s*감(\s*재)?|바\s*탕(\s*재)?|하\s*지|재\s*료|규\s*격|두\s*께|비\s*고|FINISH|BASE\s*COAT)$", re.I)
FINISH_COLUMNS = ("floor", "base", "wall", "ceiling")
NOTHING = re.compile(r"^[-─—_/]+$|^없음$|^N/?A$", re.I)
NOTES = re.compile(r"\(.*?\)|THK\.?\s*[\d.]+|T\s*=?\s*[\d.]+(?:mm)?|\d+\s*[xX×*]\s*\d+|\d+\s*회|@\s*\d+|H\s*=\s*\d+", re.I)


def header_kind(text):
    text = str(text or "").strip()
    for kind, pattern in HEADERS:
        if pattern.match(text):
            return kind
    return None


def room_keys(text):
    """The room names a schedule cell or a plan label stands for, without numbers and notes:
    "침실1" → ["침실"], "거실/주방" → ["거실", "주방"], "욕실(공용)" → ["욕실"]."""
    out = []
    for part in re.split(r"[,/·&+]|\s및\s", re.sub(r"\(.*?\)", "", str(text or ""))):
        key = re.sub(r"[\s\d\-_.#]+", "", part)
        if key and key not in out:
            out.append(key)
    return out


def material(lines):
    """The finish of a cell: its top line without thickness, size and coat notes ("강마루 THK7.5" →
    "강마루"); None when the cell says there is none."""
    for line in lines:
        text = re.sub(r"\s+", " ", NOTES.sub(" ", line)).strip(" ,.;:")
        if not text:
            continue
        return None if NOTHING.match(text) else text
    return None


def _height(lines):
    for line in lines:
        match = re.search(r"(\d{1,2},\d{3}|\d{3,4}|\d\.\d{1,2})", line)
        if match:
            value = float(match.group(1).replace(",", ""))
            value = value / 1000.0 if value > 100 else value
            if 1.8 <= value <= 6.0:
                return round(value, 3)
    return None


def _tables(texts):
    """[(header y, row height, [(kind, x)] by x, left, right)] of the tables: a header row holds at
    least three finish headers; on one line a header met again starts the next table (tables
    side by side)."""
    heads = [(header_kind(t), x, y, h) for t, x, y, h in texts if header_kind(t)]
    out, seen = [], set()
    for kind, x, y, h in sorted(heads, key=lambda item: -item[2]):
        band = sorted({(k, hx, hh) for k, hx, hy, hh in heads if abs(hy - y) <= 1.5 * max(h, hh)}, key=lambda item: item[1])
        key = tuple(sorted(band))
        if key in seen:
            continue
        seen.add(key)
        groups, current = [], []
        for k, hx, hh in band:
            if any(k == c[0] for c in current):
                groups.append(current)
                current = []
            current.append((k, hx, hh))
        groups.append(current)
        for group in groups:
            if sum(1 for k, _x, _h in group if k in FINISH_COLUMNS) < 3:
                continue
            columns = [(k, hx) for k, hx, _h in group]
            xs = [hx for _k, hx in columns]
            step = (xs[-1] - xs[0]) / max(len(xs) - 1, 1) if len(xs) > 1 else 50 * h
            out.append((y, h, columns, xs[0] - step, xs[-1] + step))
    # a table's right edge stops short of the next table on its line
    for i, (y, h, columns, left, right) in enumerate(out):
        nexts = [l for yy, hh, _c, l, _r in out if abs(yy - y) <= 1.5 * max(h, hh) and l > left]
        if nexts and min(nexts) < right:
            out[i] = (y, h, columns, left, min(nexts))
    return out


def read_finish_schedule(texts, name=None):
    """{"rooms": {room key: {"names", "floor", "base", "wall", "ceiling", "waterproof",
    "ceiling_height", "cells", "sheet"}}} from the texts of a finish schedule drawing
    ([(text, x, y, height)], e.g. quantity_arch_schedule.drawing_texts)."""
    rooms = {}
    tables = _tables(texts)
    for head_y, head_h, order, left, right in tables:
        # the next table under this one (overlapping it across) ends its rows
        below = max([yy for yy, _hh, _c, l, r in tables if yy < head_y - head_h and l < right and r > left], default=float("-inf"))
        columns = dict(order)
        xs = [x for _k, x in order]
        bounds = [left] + [(a + b) / 2 for a, b in zip(xs, xs[1:])] + [right]

        def column(x, order=order, bounds=bounds):
            for k, (kind, _cx) in enumerate(order):
                if bounds[k] <= x < bounds[k + 1]:
                    return kind
            return None
        cells = [(t, x, y, h) for t, x, y, h in texts if below < y < head_y - 0.5 * head_h and left <= x < right]
        first_finish = min(x for kind, x in order if kind in FINISH_COLUMNS)
        room_col = "room" if "room" in columns else None
        labels = sorted(((t, y) for t, x, y, h in cells
                         if (column(x) == "room" if room_col else x < first_finish - 0.5 * head_h)
                         and len(t) <= 24), key=lambda item: -item[1])
        if not labels:
            continue
        rows = [{"label": t, "y": y, "cells": {}} for t, y in labels]
        for text, x, y, _h in cells:
            kind = column(x)
            if kind in (None, "room") or SUBHEAD.match(text):  # (the header row itself is above the cells)
                continue
            row = min(rows, key=lambda r: abs(r["y"] - y))
            row["cells"].setdefault(kind, []).append((y, x, text))
        for row in rows:
            lines = {kind: [text for _y, _x, text in sorted(items, key=lambda item: (-item[0], item[1]))]
                     for kind, items in row["cells"].items()}
            entry = {"names": room_keys(row["label"]), "label": row["label"], "sheet": name,
                     "waterproof": any("방수" in text for items in lines.values() for text in items),
                     "ceiling_height": _height(lines.get("height") or []),
                     "cells": {kind: " / ".join(items) for kind, items in lines.items()}}
            for kind in FINISH_COLUMNS:
                entry[kind] = material(lines.get(kind) or [])
            entry["has_base"] = entry["base"] is not None
            for key in entry["names"]:
                rooms.setdefault(key, entry)
    return {"rooms": rooms}


def merge(schedules):
    out = {}
    for schedule in schedules:
        for key, entry in (schedule.get("rooms") or {}).items():
            out.setdefault(key, entry)
    return {"rooms": out}


def match(schedule, names):
    """The schedule row for a plan room named ``names`` (the first name the schedule knows), else None."""
    rooms = (schedule or {}).get("rooms") or {}
    for name in names or ():
        for key in room_keys(name):
            if key in rooms:
                return rooms[key]
    return None


def is_finish_schedule_file(name):
    """A finish schedule (실내재료마감표, 실내마감표, 마감표) by its file name."""
    text = re.sub(r"\s+", "", str(name or ""))
    return bool(re.search(r"마감\s*표|재료\s*마감|마감\s*재료", text))
