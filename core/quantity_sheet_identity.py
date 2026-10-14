"""Per-sheet drawing identity from CAD title blocks and PDF title-block text.

Identity sources, strongest first:
  1. CAD title-block cells located by the form's own labels (도면번호/DRAWING NO., 도면명, 축척).
     A form found in another file of the same upload is borrowed by name (xref forms).
  2. For a frame block without a readable form, a number cell learned from every insert
     carrying exactly one sheet number at the same insert-local position.
  3. A PDF page's sheet number at the document's dominant title-block text position.
  4. Page order between two such PDF numbers, only when the page count equals the CAD
     sheet count in between. This is marked inferred, never verified.
File names, drawing-index rows and body references never establish identity.
"""
from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from xml.etree import ElementTree as ET

NUMBER_RE = re.compile(r"^[A-Z]{1,3}-\d{2,4}[A-Z]?$")
LABEL_PATTERNS = {
    "number": re.compile(r"도\s*면\s*번\s*호|DRAWING\s*N(?:O\b|O\.|UMBER)|DWG\.?\s*NO|SHEET\s*N(?:O\b|O\.|UMBER)", re.I),
    "title": re.compile(r"도\s*면\s*명|DRAWING\s*(?:TITLE|NAME)|SHEET\s*TITLE", re.I),
    "scale": re.compile(r"축\s*척|\bSCALE\b", re.I),
}
OTHER_LABEL = re.compile(
    r"작\s*성\s*일\s*자|\bDATE\b|승\s*인|APPROVED|검\s*토|CHECKED|설\s*계|DESIGNED|\bDRAWN\b|"
    r"공\s*사\s*명|\bPROJECT\b|비\s*고|\bREMARKS?\b|일\s*련\s*번\s*호|\bSERIAL\b", re.I)
MAX_TEXTS = 20000
MAX_INSERTS = 5000
MAX_BLOCK_TEXTS = 20000
MAX_BLOCK_DEPTH = 8
INDEX_TITLE = re.compile(r"도면목록|DRAWINGINDEX|DRAWINGLIST|SHEETINDEX|SHEETLIST", re.I)
SCALE_LIKE = re.compile(r"^(?:NONE|N\.?S\.?|\d+\s*/\s*\d+|1\s*:\s*\d+|A\d\s*[:=].*)$", re.I)
MARK_RE = re.compile(r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9][A-Z0-9\-]{0,11}$")
NOT_A_MARK = re.compile(r"^\d+-?H?D\d+$|^H?D\d+$|\d+X\d+|@|^\d+MM$|^B?\d+F(?:-B?\d+F)?$")
MAX_SHEET_MARKS = 400
LEARN_TOLERANCE = 4.0
MAX_LEARN_TEXTS = 2000
MAX_LEARN_CANDIDATES = 300
PDF_TOLERANCE = 8.0
logger = logging.getLogger(__name__)


def _norm(text):
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def _compact(text):
    return re.sub(r"\s+", "", _norm(text)).casefold()


def _title_key(text):
    """Spacing, punctuation and the floor suffix are drafting style, not a different title."""
    return re.sub(r"[\s.,·・()\[\]~\-_:]|층", "", _norm(text)).casefold()


def _title_score(a, b):
    """2 same title, 1 one contains the other, 0 otherwise or missing."""
    a, b = _title_key(a), _title_key(b)
    if not a or not b:
        return 0
    return 2 if a == b else 1 if a in b or b in a else 0


def normalize_number(text):
    value = re.sub(r"\s+", "", _norm(text)).upper()
    return value if NUMBER_RE.match(value) else None


def natural_key(number):
    match = re.match(r"^([A-Z]+)-(\d+)([A-Z]?)$", number or "")
    return (match.group(1), int(match.group(2)), match.group(3)) if match else ("~", 0, number or "")


def number_prefix(number):
    return natural_key(number)[0]


def block_key(name):
    """A form's comparable name: no folder, no extension, no xref-dependent prefix."""
    value = _norm(name).replace("\\", "/").rsplit("/", 1)[-1]
    value = re.sub(r"\.(dwg|dxf)$", "", value, flags=re.I).split("|")[-1]
    return value.casefold()


def _default_decode(value):
    value = re.sub(r"\\U\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), value or "")
    try:
        from ezdxf.lldxf.encoding import decode_mif_to_unicode, has_mif_encoding
        return decode_mif_to_unicode(value) if has_mif_encoding(value) else value
    except Exception:
        return value


def _entity_text(entity, decode):
    kind = entity.dxftype()
    if kind == "MTEXT":
        raw = entity.plain_text()
    elif kind == "ATTDEF":
        raw = entity.dxf.get("prompt") or entity.dxf.get("tag") or ""
    else:
        raw = entity.dxf.get("text", "")
    return _norm(decode(raw))


def _height(entity):
    for key in ("height", "char_height"):
        value = entity.dxf.get(key) if entity.dxf.is_supported(key) else None
        if value:
            return float(value)
    return 1.0


def _template(entities, decode, base):
    """Cells of a title-block form, from its own labels. None without a number label."""
    from ezdxf import bbox as ezbbox

    entities = list(entities)
    labels = []
    for entity in entities:
        if entity.dxftype() not in ("TEXT", "MTEXT", "ATTDEF"):
            continue
        text = _entity_text(entity, decode)
        kind = next((name for name, pattern in LABEL_PATTERNS.items() if pattern.search(text)), None)
        if kind is None and not OTHER_LABEL.search(text):
            continue
        point = entity.dxf.insert
        labels.append({"kind": kind or "other", "x": float(point.x) - base[0],
                       "y": float(point.y) - base[1], "h": _height(entity)})
    if [label["kind"] for label in labels].count("number") != 1:
        return None
    try:
        extents = ezbbox.extents(entities, fast=True)
        extent = [float(extents.extmin.x) - base[0], float(extents.extmin.y) - base[1],
                  float(extents.extmax.x) - base[0], float(extents.extmax.y) - base[1]]
    except Exception:
        return None
    if not all(math.isfinite(v) for v in extent):
        return None
    cells = {}
    for label in labels:
        kind = label["kind"]
        if kind == "other" or kind in cells:
            continue
        h = label["h"]
        below = [other["y"] + other["h"] for other in labels if other is not label
                 and other["y"] < label["y"] - 0.5 * h and abs(other["x"] - label["x"]) <= 6 * h]
        right = [other["x"] for other in labels if other is not label
                 and abs(other["y"] - label["y"]) <= h and other["x"] > label["x"] + h]
        cells[kind] = [label["x"] - 2 * h, max(below) if below else extent[1],
                       min(right) - 0.5 * h if right else extent[2], label["y"] + 1.5 * h]
    return {"cells": cells, "extent": extent}


def _block_texts(doc, name, decode, cache, depth=0):
    """Texts of one block definition in block coordinates, nested blocks included (cached by name)."""
    if name in cache:
        return cache[name]
    cache[name] = []  # a block that refers to itself stops here
    block = doc.blocks.get(name)
    if block is None or block.block_record.is_xref or depth > MAX_BLOCK_DEPTH:
        return []
    rows = []
    for entity in block:
        kind = entity.dxftype()
        if kind in ("TEXT", "MTEXT"):
            text = _entity_text(entity, decode)
            if text:
                point = entity.dxf.insert
                rows.append((float(point.x), float(point.y), float(point.z), text, _height(entity), entity.dxf.get("layer")))
        elif kind == "INSERT":
            rows.extend(_placed_texts(entity, _block_texts(doc, entity.dxf.name, decode, cache, depth + 1)))
            for attrib in entity.attribs:
                text = _entity_text(attrib, decode)
                if text:
                    point = attrib.dxf.insert
                    rows.append((float(point.x), float(point.y), float(point.z), text, _height(attrib), attrib.dxf.get("layer")))
        if len(rows) >= MAX_BLOCK_TEXTS:
            break
    cache[name] = rows
    return rows


def _placed_texts(insert, rows):
    """Block texts moved to where the insert puts them; layer 0 takes the insert's layer."""
    if not rows:
        return []
    matrix = insert.matrix44()
    scale = abs(float(insert.dxf.get("yscale", 1) or 1))
    layer = insert.dxf.get("layer")
    placed = []
    for x, y, z, text, height, own_layer in rows:
        point = matrix.transform((x, y, z))
        placed.append((float(point.x), float(point.y), float(point.z), text, height * scale,
                       layer if own_layer in (None, "0") else own_layer))
    return placed


def extract_sheet_evidence(doc, decode=None, source_member_sha=None):
    """Compact title-block evidence of one drawing; resolved across files later.

    `texts` (model space and insert attributes) identify sheets. `block_texts` are texts drawn
    inside ordinary blocks (pasted content, labels kept in blocks); they are member evidence
    only, never title-block identity. Xrefs and title-block forms are not opened.
    """
    decode = decode or _default_decode
    base = tuple(doc.header.get("$INSBASE", (0, 0, 0)))[:2]
    msp = doc.modelspace()
    templates, form_blocks = [], set()
    for block in doc.blocks:
        if block.name.startswith("*") or block.block_record.is_xref:
            continue
        point = block.block.dxf.base_point
        template = _template(block, decode, (float(point.x), float(point.y)))
        if template:
            templates.append(dict(template, name=block_key(block.name)))
            form_blocks.add(block.name)
    inserts, texts, block_texts, cache = [], [], [], {}
    for entity in msp:
        kind = entity.dxftype()
        if kind == "INSERT" and len(inserts) < MAX_INSERTS:
            block = doc.blocks.get(entity.dxf.name)
            inserts.append({"name": block_key(decode(entity.dxf.name)), "raw_name": decode(entity.dxf.name),
                            "xref": bool(block is not None and block.block_record.is_xref),
                            "handle": entity.dxf.get("handle"),
                            "x": float(entity.dxf.insert.x), "y": float(entity.dxf.insert.y),
                            "sx": float(entity.dxf.get("xscale", 1) or 1), "sy": float(entity.dxf.get("yscale", 1) or 1),
                            "rotation": float(entity.dxf.get("rotation", 0) or 0)})
            for attrib in entity.attribs:
                if len(texts) < MAX_TEXTS:
                    texts.append({"text": _entity_text(attrib, decode), "x": float(attrib.dxf.insert.x),
                                  "y": float(attrib.dxf.insert.y), "h": _height(attrib),
                                  "layer": attrib.dxf.get("layer"), "handle": attrib.dxf.get("handle")})
            if (block is not None and not block.block_record.is_xref and entity.dxf.name not in form_blocks
                    and len(block_texts) < MAX_BLOCK_TEXTS):
                try:
                    placed = _placed_texts(entity, _block_texts(doc, entity.dxf.name, decode, cache))
                except Exception:
                    placed = []
                for x, y, _z, text, height, layer in placed[:MAX_BLOCK_TEXTS - len(block_texts)]:
                    block_texts.append({"text": text, "x": x, "y": y, "h": height, "layer": layer,
                                        "insert": entity.dxf.get("handle")})
        elif kind in ("TEXT", "MTEXT") and len(texts) < MAX_TEXTS:
            text = _entity_text(entity, decode)
            if text:
                texts.append({"text": text, "x": float(entity.dxf.insert.x), "y": float(entity.dxf.insert.y),
                              "h": _height(entity), "layer": entity.dxf.get("layer"), "handle": entity.dxf.get("handle")})
    from . import quantity_geometry
    try:
        geometry = quantity_geometry.collect_segments(doc, skip_blocks=form_blocks)
    except Exception as exc:  # member lengths are extra evidence; identity must not depend on them
        logger.warning("quantity_geometry_collect_failed error=%s", str(exc)[:200])
        geometry = {"segments": [], "paths": [], "truncated": False}
    return {"version": 2, "source_member_sha": source_member_sha,
            "self_template": _template(msp, decode, base), "templates": templates,
            "inserts": inserts, "texts": texts, "block_texts": block_texts, "geometry": geometry,
            "truncated": len(inserts) >= MAX_INSERTS or len(texts) >= MAX_TEXTS,
            "block_texts_truncated": len(block_texts) >= MAX_BLOCK_TEXTS}


def _to_local(insert, x, y):
    dx, dy = x - insert["x"], y - insert["y"]
    angle = -math.radians(insert["rotation"])
    sx = insert["sx"] or 1.0
    sy = insert["sy"] or sx
    return ((dx * math.cos(angle) - dy * math.sin(angle)) / sx, (dx * math.sin(angle) + dy * math.cos(angle)) / sy)


def _to_world(insert, x, y):
    angle = math.radians(insert["rotation"])
    x, y = x * insert["sx"], y * insert["sy"]
    return (insert["x"] + x * math.cos(angle) - y * math.sin(angle), insert["y"] + x * math.sin(angle) + y * math.cos(angle))


def _inside(cell, point):
    return cell[0] <= point[0] <= cell[2] and cell[1] <= point[1] <= cell[3]


def _learn_number_cell(inserts, number_texts):
    """One number per insert at one shared local position, or nothing."""
    number_texts = number_texts[:MAX_LEARN_TEXTS]  # bounded work on unusually dense drawings
    local = [[(_to_local(insert, t["x"], t["y"]), t) for t in number_texts] for insert in inserts]
    candidates = [pair for row in local[:3] for pair in row][:MAX_LEARN_CANDIDATES]
    for candidate, _text in candidates:
        hits = [[t for point, t in row if math.dist(point, candidate) <= LEARN_TOLERANCE] for row in local]
        if all(len(row_hits) <= 1 for row_hits in hits) and sum(len(row_hits) for row_hits in hits) >= max(2, math.ceil(0.8 * len(inserts))):
            return candidate
    return None


def _cells_for_learned(point, library):
    for template in library:
        number_cell = template["cells"].get("number")
        if number_cell and _inside(number_cell, point):
            return template, "learned_number_cell"
    margin = LEARN_TOLERANCE
    return {"cells": {"number": [point[0] - margin, point[1] - margin, point[0] + margin, point[1] + margin]},
            "extent": None}, "learned_number_cell"


def _sheet(file, evidence, insert, template, method, texts):
    cells = template["cells"]
    found = defaultdict(list)
    for text in texts:
        point = _to_local(insert, text["x"], text["y"])
        for field, cell in cells.items():
            if _inside(cell, point):
                found[field].append((point, text))
    numbers = []
    for _point, text in found.get("number", []):
        number = normalize_number(text["text"])
        if number and number not in numbers:
            numbers.append(number)

    def joined(field):
        rows = sorted(found.get(field, []), key=lambda pair: (-round(pair[0][1], 1), pair[0][0]))
        values = [text["text"] for _point, text in rows if text["text"] and not normalize_number(text["text"])]
        return " ".join(values) or None

    title = joined("title") if "title" in cells else None
    status = ("conflict" if len(numbers) > 1 else "identified" if numbers and title
              else "number_only" if numbers else "title_only" if title else "unidentified")
    frame_bbox = None
    if template.get("extent"):
        x0, y0, x1, y1 = template["extent"]
        corners = [_to_world(insert, x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        frame_bbox = [min(c[0] for c in corners), min(c[1] for c in corners),
                      max(c[0] for c in corners), max(c[1] for c in corners)]
    used = {text["handle"] for pairs in found.values() for _point, text in pairs if text.get("handle")}
    return {"file": file, "source_member_sha": evidence.get("source_member_sha"),
            "frame_handle": insert.get("handle"), "frame_block": insert.get("raw_name"),
            "method": method, "drawing_number": numbers[0] if len(numbers) == 1 else None,
            "number_candidates": numbers, "drawing_title": title,
            "scale": joined("scale") if "scale" in cells else None,
            "identity_status": status, "frame_bbox": frame_bbox, "text_handles": sorted(used)}


def normalize_mark(text):
    value = re.sub(r"\s+", "", _norm(text)).upper()
    if not MARK_RE.match(value) or NOT_A_MARK.search(value) or normalize_number(value):
        return None
    return value


def frame_texts(sheet, evidence):
    """Texts inside one frame, model space and block contents, without its own title-block values."""
    if not sheet.get("frame_bbox"):
        return []
    x0, y0, x1, y1 = sheet["frame_bbox"]
    own = set(sheet.get("text_handles") or [])
    rows = [dict(text, source="model_space") for text in evidence.get("texts", [])
            if x0 <= text["x"] <= x1 and y0 <= text["y"] <= y1 and text.get("handle") not in own]
    rows += [dict(text, source="block") for text in evidence.get("block_texts", [])
             if x0 <= text["x"] <= x1 and y0 <= text["y"] <= y1]
    return rows


def sheet_marks(sheet, evidence, texts=None):
    """Member-mark labels inside one frame, counted per layer (title block excluded).

    Labels kept inside pasted blocks count too; `in_blocks` says how many came from blocks.
    """
    counts, in_blocks = defaultdict(Counter), Counter()
    for text in frame_texts(sheet, evidence) if texts is None else texts:
        mark = normalize_mark(text["text"])
        if mark:
            counts[mark][text.get("layer") or ""] += 1
            if text.get("source") == "block":
                in_blocks[mark] += 1
    rows = [{"mark": mark, "count": sum(layers.values()), "layers": dict(layers), "in_blocks": in_blocks[mark]}
            for mark, layers in counts.items()]
    rows.sort(key=lambda row: (-row["count"], row["mark"]))
    return rows[:MAX_SHEET_MARKS]


def attach_sheet_identity(parsed, evidence_by_file):
    """Store per-file sheets (with mark labels, tables and notes) and index rows on the parse result."""
    from . import quantity_geometry, quantity_schedules

    sheets = resolve_cad_sheets(evidence_by_file)
    for file, rows in sheets.items():
        for sheet in rows:
            texts = frame_texts(sheet, evidence_by_file[file])
            sheet["marks"] = sheet_marks(sheet, evidence_by_file[file], texts)
            try:
                sheet.update(quantity_schedules.read_sheet_content(texts, sheet.get("drawing_title")))
            except Exception as exc:  # tables are extra evidence; identity must not depend on them
                logger.warning("quantity_sheet_content_failed file=%s error=%s", file, str(exc)[:200])
                sheet.update(tables=[], notes=[], specs=[])
            try:
                measured = quantity_geometry.measure_sheet(sheet, evidence_by_file[file].get("geometry"), texts, normalize_mark)
            except Exception as exc:
                logger.warning("quantity_geometry_measure_failed file=%s error=%s", file, str(exc)[:200])
                measured = None
            if measured:
                sheet["geometry"] = measured
    for file in evidence_by_file:
        if isinstance(parsed.get(file), dict):
            parsed[file]["sheets"] = sheets.get(file, [])
    for entry in parse_drawing_index(sheets, evidence_by_file):
        target = parsed.get(entry["index_file"])
        if isinstance(target, dict):
            target.setdefault("index_entries", []).append(entry)
    return parsed


def resolve_cad_sheets(evidence_by_file):
    """{file: [sheet, ...]} for every drawing; forms are shared within the upload."""
    library, by_name = [], {}
    for file, evidence in evidence_by_file.items():
        if evidence.get("self_template"):
            template = dict(evidence["self_template"], name=block_key(file))
            library.append(template)
            by_name.setdefault(template["name"], template)
        for template in evidence.get("templates", []):
            library.append(template)
            by_name.setdefault(template["name"], template)
    result = {}
    for file, evidence in evidence_by_file.items():
        texts = evidence.get("texts", [])
        groups = defaultdict(list)
        for insert in evidence.get("inserts", []):
            groups[insert["name"]].append(insert)
        rows, claimed = [], set()
        for name, inserts in groups.items():
            template = by_name.get(name)
            if not template:
                continue
            for insert in inserts:
                row = _sheet(file, evidence, insert, template, "template_labels", texts)
                claimed.update(row["text_handles"])
                rows.append(row)
        unknown = sorted((name for name in groups if name not in by_name and len(groups[name]) <= 200),
                         key=lambda name: (-len(groups[name]), name))
        for name in unknown:
            number_texts = [t for t in texts if normalize_number(t["text"]) and t.get("handle") not in claimed]
            if not number_texts:
                break
            point = _learn_number_cell(groups[name], number_texts)
            if point is None:
                continue
            template, method = _cells_for_learned(point, library)
            available = [t for t in texts if t.get("handle") not in claimed]
            for insert in groups[name]:
                row = _sheet(file, evidence, insert, template, method, available)
                claimed.update(row["text_handles"])
                rows.append(row)
        rows.sort(key=lambda row: (natural_key(row["drawing_number"]), row["frame_bbox"] or [0, 0]))
        result[file] = rows
    return result


def parse_drawing_index(sheets_by_file, evidence_by_file):
    """Rows of CAD drawing-index sheets, in reading order. Used only to order sheets."""
    entries = []
    index_sheets = [s for rows in sheets_by_file.values() for s in rows
                    if s.get("frame_bbox") and INDEX_TITLE.search(_compact(s.get("drawing_title")))]
    index_sheets.sort(key=lambda s: (natural_key(s["drawing_number"]), s["file"], s["frame_bbox"][0], -s["frame_bbox"][1]))
    for sheet in index_sheets:
        x0, y0, x1, y1 = sheet["frame_bbox"]
        own = set(sheet.get("text_handles") or [])
        texts = [t for t in (evidence_by_file.get(sheet["file"]) or {}).get("texts", [])
                 if x0 <= t["x"] <= x1 and y0 <= t["y"] <= y1 and t.get("handle") not in own]
        numbers = sorted((t for t in texts if normalize_number(t["text"])), key=lambda t: t["x"])
        columns = []
        for text in numbers:
            if columns and abs(text["x"] - columns[-1][0]) <= 5 * max(text["h"], 1.0):
                columns[-1][1].append(text)
            else:
                columns.append([text["x"], [text]])
        for position, (column_x, members) in enumerate(columns):
            right = columns[position + 1][0] if position + 1 < len(columns) else x1
            for number in sorted(members, key=lambda t: -t["y"]):
                row = sorted((t for t in texts if t is not number and abs(t["y"] - number["y"]) <= 0.6 * number["h"]
                              and number["x"] < t["x"] < right and not normalize_number(t["text"])),
                             key=lambda t: t["x"])
                entries.append({"drawing_number": normalize_number(number["text"]),
                                "drawing_title": next((t["text"] for t in row if not SCALE_LIKE.match(t["text"])), None),
                                "scale": next((t["text"] for t in row if SCALE_LIKE.match(t["text"])), None),
                                "index_file": sheet["file"], "index_frame": sheet.get("frame_handle")})
    return entries


def _slot(number, sheet, *, source, index_title=None, warnings=None):
    return {"drawing_number": number, "drawing_title": (sheet or {}).get("drawing_title") or index_title,
            "index_title": index_title, "source": source, "cad_sheet": sheet, "alternates": [],
            "warnings": list(warnings or [])}


def _sequence_numeric(frames):
    """Number order; the same sheet drawn again in another file (same title) is one slot."""
    slots = []
    for frame in frames:
        version_of = next((slot for slot in slots if slot["drawing_number"] == frame["drawing_number"]
                           and frame["file"] not in {slot["cad_sheet"]["file"], *(a["file"] for a in slot["alternates"])}
                           and _title_score(slot["cad_sheet"].get("drawing_title"), frame.get("drawing_title")) == 2), None)
        if version_of:
            version_of["alternates"].append(frame)
            if "cad_multiple_versions" not in version_of["warnings"]:
                version_of["warnings"].append("cad_multiple_versions")
        else:
            slots.append(_slot(frame["drawing_number"], frame, source="cad_frames"))
    return slots


def _sequence_from_index(entries, frames, issues):
    """Index order; same-number sheets are told apart by title, revisions collapse."""
    unused, matched_numbers, slots = list(frames), set(), []
    for entry in entries:
        number, title = entry["drawing_number"], entry["drawing_title"]
        candidates = [f for f in unused if f["drawing_number"] == number]
        warnings, pick = [], None
        if candidates:
            pick = max(candidates, key=lambda f: (_title_score(f.get("drawing_title"), title), -candidates.index(f)))
        else:
            loose = [f for f in unused if f["drawing_number"] in matched_numbers
                     and _title_score(f.get("drawing_title"), title) >= 1]
            if loose:
                pick = loose[0]
                warnings.append("cad_number_differs_from_index")
        if pick is None:
            slots.append(_slot(number, None, source="drawing_index", index_title=title,
                               warnings=["index_entry_without_cad_sheet"]))
            continue
        unused.remove(pick)
        matched_numbers.add(pick["drawing_number"])
        if title and pick.get("drawing_title") and _title_score(pick["drawing_title"], title) == 0:
            warnings.append("cad_title_differs_from_index")
        slot = _slot(number, pick, source="drawing_index", index_title=title, warnings=warnings)
        for other in [f for f in unused if f["drawing_number"] == pick["drawing_number"] and f["file"] != pick["file"]
                      and _title_score(f.get("drawing_title"), pick.get("drawing_title")) == 2]:
            unused.remove(other)
            slot["alternates"].append(other)
        if slot["alternates"]:
            slot["warnings"].append("cad_multiple_versions")
        slots.append(slot)
    # The index defines the drawing set; an unlisted frame is reported, not aligned to pages.
    for frame in unused:
        issues.append({"reason": "cad_sheet_not_in_index", "drawing_number": frame["drawing_number"], "file": frame["file"]})
    return slots


def build_sheet_registry(sheets_by_file, evidence_by_file=None, index_entries=None):
    sheets = [row for rows in sheets_by_file.values() for row in rows]
    sheets.sort(key=lambda row: (natural_key(row["drawing_number"]), row["file"], row.get("frame_bbox") or [0, 0]))
    issues = []
    counts = Counter(row["drawing_number"] for row in sheets if row["drawing_number"])
    for number in sorted(counts, key=natural_key):
        if counts[number] > 1:
            issues.append({"reason": "cad_duplicate_number", "drawing_number": number, "count": counts[number]})
    for row in sheets:
        if row["identity_status"] in ("conflict", "unidentified", "title_only"):
            issues.append({"reason": "cad_sheet_" + row["identity_status"], "file": row["file"],
                           "frame_handle": row.get("frame_handle")})
    if index_entries is not None:
        entries = list(index_entries)
    else:
        entries = parse_drawing_index(sheets_by_file, evidence_by_file) if evidence_by_file else []
    sequence = {}
    prefixes = {number_prefix(s["drawing_number"]) for s in sheets if s["drawing_number"]}
    prefixes |= {number_prefix(e["drawing_number"]) for e in entries}
    for prefix in sorted(prefixes):
        frames = [s for s in sheets if s["drawing_number"] and number_prefix(s["drawing_number"]) == prefix]
        listed = [e for e in entries if number_prefix(e["drawing_number"]) == prefix]
        sequence[prefix] = _sequence_from_index(listed, frames, issues) if listed else _sequence_numeric(frames)
    return {"sheets": sheets, "sequence": sequence, "index_entries": entries, "issues": issues}


def parse_pdf_title_block_numbers(xml_text):
    xml_text = re.sub(r"<!DOCTYPE[^>]*>", "", xml_text)
    xml_text = re.sub(r'\sxmlns="[^"]*"', "", xml_text)
    root = ET.fromstring(xml_text)
    pages = []
    for page in root.iter("page"):
        words = [(w.text or "", float(w.get("xMin")), float(w.get("yMin"))) for w in page.iter("word")]
        numbers = [(normalize_number(text), x, y) for text, x, y in words if normalize_number(text)]
        pages.append({"words": len(words), "numbers": numbers})
    support = Counter()
    candidates = [(x, y) for page in pages for _n, x, y in page["numbers"]]
    for point in set((round(x), round(y)) for x, y in candidates):
        support[point] = sum(1 for page in pages if any(math.dist(point, (x, y)) <= PDF_TOLERANCE
                                                         for _n, x, y in page["numbers"]))
    position = None
    if support:
        point, count = max(support.items(), key=lambda item: (item[1], -item[0][1]))
        if count >= 2:  # one page alone cannot tell a title block from a body reference
            position = point
    result = []
    for index, page in enumerate(pages, 1):
        row = {"page": index, "drawing_number": None, "text_available": page["words"] > 0,
               "title_block_position": list(position) if position else None, "reason": None}
        if not page["words"]:
            row["reason"] = "no_text_layer"
        elif position is None:
            row["reason"] = "title_block_position_unknown"
        else:
            values = sorted({n for n, x, y in page["numbers"] if math.dist(position, (x, y)) <= PDF_TOLERANCE})
            if len(values) == 1:
                row["drawing_number"] = values[0]
            else:
                row["reason"] = "multiple_numbers_in_title_block" if values else "no_number_in_title_block"
        result.append(row)
    return result


def pdf_title_block_numbers(pdf_bytes, timeout=120):
    with tempfile.TemporaryDirectory(prefix="cbl_qty_pdf_") as work:
        path = os.path.join(work, "source.pdf")
        with open(path, "wb") as handle:
            handle.write(pdf_bytes)
        result = subprocess.run(["pdftotext", "-bbox", path, "-"], capture_output=True, timeout=timeout)
    if result.returncode != 0:
        return []
    return parse_pdf_title_block_numbers(result.stdout.decode("utf-8", errors="replace"))


def _cad_ref(sheet):
    return {"file": sheet["file"], "frame_handle": sheet.get("frame_handle"),
            "drawing_number": sheet.get("drawing_number"), "drawing_title": sheet.get("drawing_title"),
            "source_member_sha": sheet.get("source_member_sha"), "method": sheet.get("method"),
            "frame_bbox": sheet.get("frame_bbox")}


def assign_pdf_identities(page_numbers, registry):
    """PDF page identities from title-block numbers and bounded page order."""
    rows = [{"page": p["page"], "drawing_number": p.get("drawing_number"), "drawing_title": None,
             "identity_status": "unidentified", "source_method": None, "cad_sheet": None,
             "warnings": [], "reason": p.get("reason"), "text_available": p.get("text_available")}
            for p in page_numbers]
    prefixes = Counter(number_prefix(row["drawing_number"]) for row in rows if row["drawing_number"])
    if not prefixes:
        return rows
    prefix = prefixes.most_common(1)[0][0]
    sequence = (registry.get("sequence") or {}).get(prefix)
    if sequence is None:
        sequence = _sequence_numeric([s for s in registry["sheets"]
                                      if s["drawing_number"] and number_prefix(s["drawing_number"]) == prefix])
    counts = Counter(s["drawing_number"] for s in sequence)
    anchors = []
    for row in rows:
        number = row["drawing_number"]
        if not number:
            continue
        row["source_method"] = "pdf_title_block_text"
        if counts.get(number, 0) == 0:
            row["identity_status"] = "number_only"
            row["reason"] = "no_cad_sheet_with_number"
            continue
        row["identity_status"] = "identified"
        if counts[number] > 1:
            row["warnings"].append("cad_duplicate_number")
            continue
        index = next(i for i, s in enumerate(sequence) if s["drawing_number"] == number)
        _apply_slot(row, sequence[index])
        anchors.append((row["page"], index))
    if not anchors:
        return rows
    if any(b[1] <= a[1] for a, b in zip(anchors, anchors[1:])):
        for row in rows:
            if not row["drawing_number"]:
                row["reason"] = "title_block_numbers_out_of_order"
        return rows
    by_page = {row["page"]: row for row in rows}
    bounds = [(0, -1)] + anchors + [(len(rows) + 1, len(sequence))]
    for (page0, index0), (page1, index1) in zip(bounds, bounds[1:]):
        gap_pages = [by_page[p] for p in range(page0 + 1, page1) if p in by_page]
        gap_sheets = sequence[index0 + 1:index1]
        if not gap_pages and not gap_sheets:
            continue
        if len(gap_pages) != len(gap_sheets):
            for row in gap_pages:
                if not row["drawing_number"]:
                    row["reason"] = "page_count_differs_from_cad_sheets"
            continue
        if any(row["drawing_number"] and row["drawing_number"] != sheet["drawing_number"]
               for row, sheet in zip(gap_pages, gap_sheets)):
            for row in gap_pages:
                if not row["drawing_number"]:
                    row["reason"] = "order_conflicts_with_page_number"
            continue
        for row, slot in zip(gap_pages, gap_sheets):
            if not row["drawing_number"]:
                row.update(drawing_number=slot["drawing_number"], identity_status="inferred_order",
                           source_method="page_order_between_title_block_numbers", reason=None)
            _apply_slot(row, slot)
            if counts[slot["drawing_number"]] > 1 and "cad_duplicate_number" not in row["warnings"]:
                row["warnings"].append("cad_duplicate_number")
    return rows


def _apply_slot(row, slot):
    row["drawing_title"] = slot.get("drawing_title")
    row["index_title"] = slot.get("index_title")
    sheet = slot.get("cad_sheet") if "cad_sheet" in slot else slot
    row["cad_sheet"] = _cad_ref(sheet) if sheet else None
    row["cad_alternates"] = [_cad_ref(other) for other in slot.get("alternates", [])]
    for warning in slot.get("warnings", []):
        if warning not in row["warnings"]:
            row["warnings"].append(warning)
