"""The window and door schedule (창호일람표) from CAD (no AI): each type's size and count.

A schedule sheet draws one cell or box per type: its code (AW1, AD-2, SD1, WD03 ...), an elevation,
its size ("2,000 x 1,500", "W2000×H1500", or "W : 2,000" and "H : 1,500" on two lines) and often
its count ("40EA", "수량 40", or a 수량 row under the codes). Offices lay these out in a grid or in
boxes, so the reading goes by position, not by layout: each size and each count belongs to the
nearest code above it or beside it, every text to one code only (greedy, nearest first).

The code also gives the kind: the last letter before the number is D for a door, W for a window
(WD = wood door, AW = aluminium window). The same codes tag the windows and doors on the plans
(quantity_arch reads them), which is how the plans find their sizes here.

Other offices name a type by its letters and its size instead of a number: "FSD" under
"1.1x2.1" (metres), "PW" under "30X21" (decimetres), or "WD" under "1.0" and "2.2". Such a type
is keyed FSD1.1X2.1, and a plan window or door without a tag finds it by the letters and width in
its block name ("FSD-1100", "UNIT BPW-1400", "AW 1000"): listed_type.
"""
from __future__ import annotations

import math
import re
from collections import Counter

CODE = re.compile(r"^\s*([A-Z]{0,3}[WD])\s*-?\s*0*(\d{1,3})([A-Z]?)(?=$|[\s(\[:,])")
REBAR_GRADE = re.compile(r"^SD\d{3}$")
NUMBER = r"(\d{1,2},\d{3}|\d{3,5})"
SIZE = re.compile(r"(?:W\s*[:=]?\s*)?" + NUMBER + r"\s*[xX×*]\s*(?:H\s*[:=]?\s*)?" + NUMBER)
WIDTH_ONLY = re.compile(r"^\s*W\s*[:=]?\s*" + NUMBER + r"\s*(?:mm)?\s*$", re.I)
HEIGHT_ONLY = re.compile(r"^\s*H\s*[:=]?\s*" + NUMBER + r"\s*(?:mm)?\s*$", re.I)
COUNT_INLINE = re.compile(r"(?:수\s*량|Q'?TY)\s*[:：]?\s*(\d{1,4})|(\d{1,4})\s*(?:EA|개소|SET|틀)(?![A-Za-z])", re.I)
COUNT_LABEL = re.compile(r"^\s*(?:수\s*량|Q'?TY|QUANTITY)\s*$", re.I)
PLAIN_NUMBER = re.compile(r"^\s*(\d{1,4})\s*$")
MAX_TEXTS = 60000
# schedules that name a type by its letters and size ("FSD" under "1.1x2.1", "PW" under "30X21",
# "WD" under "1.0" "2.2"): the type is FSD1.1X2.1 (metres)
LETTERS = re.compile(r"^\s*([A-Z]{1,4}[WD])\s*$")
SMALL = r"(\d{1,2}(?:\.\d{1,2})?)"
SMALL_SIZE = re.compile(r"^\s*" + SMALL + r"\s*[xX×*]\s*" + SMALL + r"\s*$")
METRES = re.compile(r"^\s*(\d\.\d{1,2})\s*$")
LETTER_SIZE_KEY = re.compile(r"^([A-Z]{1,4})(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$")
BLOCK_SIZE = re.compile(r"(?<![A-Z])([A-Z]{1,4})[\s\-_]*W?(\d{3,4})(?!\d)")


def code_key(text):
    """"AW-01" → "AW1"; None when the text is not a window or door code."""
    match = CODE.match(str(text or "").upper())
    if not match:
        return None
    key = match.group(1) + str(int(match.group(2))) + match.group(3)
    if REBAR_GRADE.match(match.group(1) + match.group(2)):
        return None
    return key


def kind_of(key):
    letters = re.match(r"^[A-Z]+", key).group(0)
    return "door" if letters.endswith("D") else "window"


def _mm(text):
    value = float(text.replace(",", ""))
    return value


def drawing_texts(doc, decode=None):
    """[(text, x, y, height)] of a drawing: model space, insert attributes and the texts inside
    ordinary blocks, placed where they are drawn (xrefs are not opened)."""
    from ezdxf.math import Matrix44, Vec3
    decode = decode or (lambda value: value)
    out = []

    def text_of(entity):
        kind = entity.dxftype()
        try:
            raw = entity.plain_text() if kind == "MTEXT" else entity.dxf.get("text", "")
        except Exception:
            raw = ""
        return re.sub(r"\s+", " ", str(decode(raw) or "")).strip()

    def height_of(entity):
        for key in ("height", "char_height"):
            if entity.dxf.is_supported(key) and entity.dxf.get(key):
                return float(entity.dxf.get(key))
        return 1.0

    def add(entity, matrix, scale):
        text = text_of(entity)
        if not text:
            return
        point = matrix.transform(Vec3(entity.dxf.insert))
        out.append((text, float(point.x), float(point.y), height_of(entity) * scale))

    def walk(entities, matrix, scale, depth):
        for entity in entities:
            if len(out) >= MAX_TEXTS:
                return
            kind = entity.dxftype()
            if kind in ("TEXT", "MTEXT"):
                add(entity, matrix, scale)
            elif kind == "INSERT":
                for attrib in entity.attribs:
                    add(attrib, matrix, scale)
                block = doc.blocks.get(entity.dxf.name)
                if block is None or block.block_record.is_xref or depth >= 4:
                    continue
                try:
                    placed = Matrix44.chain(entity.matrix44(), matrix)
                except Exception:
                    continue
                walk(block, placed, scale * abs(float(entity.dxf.get("yscale", 1) or 1)), depth + 1)

    walk(doc.modelspace(), Matrix44(), 1.0, 0)
    return out


def _pair(codes, items, reach_of):
    """{code index: item index}: each item to the nearest code above it or beside it (a column below
    the code reads closest), each code and item used once."""
    pairs = []
    for ci, (_key, cx, cy, ch) in enumerate(codes):
        reach = reach_of(ch)
        for ii, item in enumerate(items):
            dx, dy = item[-2] - cx, item[-1] - cy
            if dy > 1.5 * ch or abs(dx) > reach or -dy > reach:
                continue  # well above the code, or too far
            pairs.append((abs(dx) + 0.5 * abs(dy), ci, ii))
    pairs.sort()
    used_codes, used_items, out = set(), set(), {}
    for _score, ci, ii in pairs:
        if ci in used_codes or ii in used_items:
            continue
        used_codes.add(ci)
        used_items.add(ii)
        out[ci] = ii
    return out


def _small_metres(a, b):
    """(w, h) in metres of a size written small: metres ("1.1x2.1") or decimetres ("24X23.5")."""
    if a < 10 and b < 10:
        return a, b
    if a < 100 and b < 100:
        return a / 10.0, b / 10.0
    return None


def letter_size_key(letters, width, height):
    return f"{letters}{round(width, 2):g}X{round(height, 2):g}"


def _letter_size_types(texts, name=None):
    """{key: type} of a schedule naming each type by its letters with its size just above them:
    "1.1x2.1" (m) or "30X21" (dm) in one text, or "1.1" and "2.2" (m) side by side. Each letters
    text takes the nearest size above it (within six text heights up, half a column across)."""
    heads = [(m.group(1), x, y, max(h, 1e-6)) for t, x, y, h in texts if (m := LETTERS.match(t)) and len(m.group(1)) >= 2]
    if not heads:
        return {}
    sizes = []
    for text, x, y, h in texts:
        match = SMALL_SIZE.match(text)
        if match:
            size = _small_metres(float(match.group(1)), float(match.group(2)))
            if size and 0.3 <= size[0] <= 12 and 0.3 <= size[1] <= 6:
                sizes.append((size, x, y))
    plain = sorted(((float(m.group(1)), x, y, h) for t, x, y, h in texts if (m := METRES.match(t))), key=lambda item: (item[2], item[1]))
    for (w, x, y, h), (hh, x2, y2, _h2) in zip(plain, plain[1:]):
        if abs(y2 - y) <= 0.3 * h and 0 < x2 - x <= 4 * h and 0.3 <= w <= 12 and 0.3 <= hh <= 6:
            sizes.append(((w, hh), x, y))
    pairs = []
    for hi, (_letters, hx, hy, hh) in enumerate(heads):
        for si, (_size, sx, sy) in enumerate(sizes):
            dy, dx = sy - hy, sx - hx
            if 0 < dy <= 6 * hh and abs(dx) <= 12 * hh:
                pairs.append((abs(dx) + abs(dy), hi, si))
    used_h, used_s, out = set(), set(), {}
    for _d, hi, si in sorted(pairs):
        if hi in used_h or si in used_s:
            continue
        used_h.add(hi)
        used_s.add(si)
        letters = heads[hi][0]
        width, height = sizes[si][0]
        key = letter_size_key(letters, width, height)
        out.setdefault(key, {"code": key, "kind": kind_of(letters), "width": round(width, 3), "height": round(height, 3),
                             "count": None, "sheet": name})
    return out


def listed_type(types, name, width=None):
    """The schedule's type of a plan window or door: by its tag, else, for a schedule naming types
    by letters and size (FSD1.1X2.1), by the letters and width in its block name ("FSD-1100",
    "UNIT BPW-1400", "AW 1000") or its drawn width; {} when none fits (or several heights do)."""
    item = (types or {}).get(name)
    if item:
        return item
    last = str(name or "").split("$0$")[-1].split("|")[-1].upper()
    match = BLOCK_SIZE.search(last)
    if not match:
        return {}
    letters, wanted = match.group(1), int(match.group(2)) / 1000.0
    found = []
    for key, entry in (types or {}).items():
        parts = LETTER_SIZE_KEY.match(key)
        if parts and parts.group(1) == letters and entry.get("width") and abs(entry["width"] - wanted) <= 0.06:
            found.append(entry)
    heights = {entry.get("height") for entry in found}
    return found[0] if len(heights) == 1 else {}


def read_opening_schedule(texts, name=None):
    """{"types": {code: {"code", "kind", "width", "height", "count", "sheet"}}} from the texts of a
    schedule drawing (drawing_texts). Sizes in metres; a type without a size keeps width/height None."""
    codes = [(code_key(t), x, y, max(h, 1e-6)) for t, x, y, h in texts if code_key(t) and len(t) <= 24]
    if not codes:
        return {"types": _letter_size_types(texts, name)}
    heights = sorted(h for *_rest, h in codes)
    unit = heights[len(heights) // 2]
    reach = lambda h: 60 * max(h, unit)
    sizes, widths, heights_only, counts = [], [], [], []
    for text, x, y, _h in texts:
        size = SIZE.search(text)
        if size:
            w, h = _mm(size.group(1)), _mm(size.group(2))
            if 300 <= w <= 12000 and 300 <= h <= 6000:
                sizes.append(((w, h), x, y))
                continue
        only = WIDTH_ONLY.match(text)
        if only:
            widths.append((_mm(only.group(1)), x, y))
            continue
        only = HEIGHT_ONLY.match(text)
        if only:
            heights_only.append((_mm(only.group(1)), x, y))
            continue
        count = COUNT_INLINE.search(text)
        if count and not code_key(text):
            counts.append((int(count.group(1) or count.group(2)), x, y))
    # a 수량 row: plain numbers on the label's line, each under the code of its column
    labels = [(x, y, h) for t, x, y, h in texts if COUNT_LABEL.match(t)]
    row_counts = {}
    for lx, ly, lh in labels:
        for text, x, y, h in texts:
            number = PLAIN_NUMBER.match(text)
            if not number or x <= lx or abs(y - ly) > max(lh, h, unit) * 0.8:
                continue
            column = min(range(len(codes)), key=lambda i: abs(codes[i][1] - x) + (0 if codes[i][2] > y else 1e9))
            if codes[column][2] > y and abs(codes[column][1] - x) < reach(codes[column][3]) / 4:
                row_counts.setdefault(column, int(number.group(1)))
    found = {}

    def note(index, field, value):
        found.setdefault(codes[index][0], {}).setdefault(field, []).append(value)

    for ci, ii in _pair(codes, sizes, reach).items():
        note(ci, "size", sizes[ii][0])
    for ci, ii in _pair(codes, widths, reach).items():
        note(ci, "w", widths[ii][0])
    for ci, ii in _pair(codes, heights_only, reach).items():
        note(ci, "h", heights_only[ii][0])
    for ci, ii in _pair(codes, counts, reach).items():
        note(ci, "count", counts[ii][0])
    for ci, value in row_counts.items():
        note(ci, "count", value)
    types = {}
    for key in sorted({c[0] for c in codes}, key=lambda k: (kind_of(k), k)):
        got = found.get(key, {})
        size = Counter(got.get("size") or []).most_common(1)
        width = size[0][0][0] if size else (Counter(got.get("w") or []).most_common(1) or [(None,)])[0][0]
        height = size[0][0][1] if size else (Counter(got.get("h") or []).most_common(1) or [(None,)])[0][0]
        count = Counter(got.get("count") or []).most_common(1)
        if width is None and height is None and not count:
            continue  # a code named somewhere on the sheet (a note, a detail call-out) with nothing of its own
        types[key] = {"code": key, "kind": kind_of(key),
                      "width": round(width / 1000.0, 3) if width else None,
                      "height": round(height / 1000.0, 3) if height else None,
                      "count": count[0][0] if count else None, "sheet": name}
    return {"types": types}


def merge(schedules):
    """One schedule from several sheets: a type keeps the first sheet's size; counts add up only
    when the sheets are different (a type listed on two sheets is not counted twice)."""
    out = {}
    for schedule in schedules:
        for key, item in (schedule.get("types") or {}).items():
            have = out.get(key)
            if have is None:
                out[key] = dict(item)
                continue
            for field in ("width", "height", "count"):
                if have.get(field) is None and item.get(field) is not None:
                    have[field] = item[field]
    return {"types": out}


def is_schedule_title(text):
    return bool(re.search(r"창\s*호\s*(일\s*람\s*표|도|리\s*스\s*트)|창호\s*상세|WINDOW\s*(AND|&)\s*DOOR\s*SCHEDULE|DOOR\s*SCHEDULE|WINDOW\s*SCHEDULE",
                          str(text or ""), re.I))


def nearest_tags(openings, tags, reach, allowed=None):
    """{opening index: tag key}: each window or door to the nearest type tag within ``reach`` (one
    tag per opening, nearest first). openings / tags: [(x, y)] / [(key, x, y)]; ``allowed(opening
    index, key)`` can rule a pairing out."""
    grid = {}
    for ti, (_key, tx, ty) in enumerate(tags):
        grid.setdefault((int(math.floor(tx / reach)), int(math.floor(ty / reach))), []).append(ti)
    pairs = []
    for oi, (ox, oy) in enumerate(openings):
        gx, gy = int(math.floor(ox / reach)), int(math.floor(oy / reach))
        for cx in (gx - 1, gx, gx + 1):
            for cy in (gy - 1, gy, gy + 1):
                for ti in grid.get((cx, cy), ()):
                    _key, tx, ty = tags[ti]
                    d = math.hypot(tx - ox, ty - oy)
                    if d <= reach and (allowed is None or allowed(oi, tags[ti][0])):
                        pairs.append((d, oi, ti))
    pairs.sort()
    used_o, used_t, out = set(), set(), {}
    for _d, oi, ti in pairs:
        if oi in used_o or ti in used_t:
            continue
        used_o.add(oi)
        used_t.add(ti)
        out[oi] = tags[ti][0]
    return out
