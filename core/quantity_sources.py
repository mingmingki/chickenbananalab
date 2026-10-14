"""Drawing identity and member locations; neither proves a measured quantity.

Role vocabulary is restored from wip/local-20260929. Only explicit title/number
fields are used for identity; body references and filenames cannot create links.
"""
from copy import deepcopy
from collections import Counter, defaultdict
import re
import math
import unicodedata

from . import quantity_sheet_identity

QUANTITY_ROLE_DEFINITIONS_V2 = {
    "general_notes": ("일반사항", "설계개요", "구조설계", "구조일반", "general notes", "specification"),
    "drawing_index": ("도면목록", "도면 리스트", "drawing index", "sheet list"),
    "floor_plan": ("평면도", "floor plan", "plan"),
    "foundation_plan": ("기초", "foundation", "footing", "mat foundation"),
    "framing_plan": ("골조평면", "구조평면", "framing plan", "structural plan"),
    "column_schedule": ("기둥일람표", "기둥일람", "column schedule"),
    "column_reinforcement_detail": ("기둥배근도", "기둥배근상세도", "기둥상세도", "column reinforcement", "column detail"),
    "wall_schedule": ("벽체일람표", "벽체일람", "지하외벽일람표", "wall schedule"),
    "wall_reinforcement_detail": ("벽체배근도", "벽체배근상세도", "벽체상세도", "wall reinforcement", "wall detail"),
    "beam_schedule": ("보일람표", "보일람", "beam schedule"),
    "beam_reinforcement_detail": ("보배근도", "보배근상세도", "보상세도", "beam reinforcement", "beam detail"),
    "slab_schedule": ("슬래브일람표", "슬래브일람", "slab schedule"),
    "slab_reinforcement_plan": ("슬래브배근도", "슬래브배근상세도", "slab reinforcement"),
    "slab_framing_plan": ("슬래브구조평면도", "슬래브골조평면도", "slab framing plan"),
    "reinforcement_detail": ("배근도", "철근상세", "reinforcement detail", "rebar detail"),
    "structural_detail": ("구조상세", "기초상세", "구조 상세", "structural detail"),
    "section": ("단면", "section"),
    "elevation": ("입면", "elevation"),
    "stair": ("계단", "stair"),
    "ramp": ("램프", "경사로", "ramp"),
    "finish_schedule": ("마감", "finish schedule", "finish"),
    "window_schedule": ("창호", "창문", "window schedule", "fenestration"),
    "door_schedule": ("문일람", "도어", "door schedule"),
    "detail": ("상세", "detail"),
    "sheet_frame": ("도곽", "title block", "sheet frame", "border"),
}

def _quantity_semantic_role_v2(text, discipline=None):
    """Classify a sheet from title/index text, never from a project number."""
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    matches = []
    # Specific sheet roles must win over the generic "plan" term.
    priority = (
        # Specific schedule phrases must win over generic rebar/plan terms.
        "slab_schedule", "column_schedule", "wall_schedule", "beam_schedule",
        "slab_reinforcement_plan", "slab_framing_plan",
        "column_reinforcement_detail", "wall_reinforcement_detail", "beam_reinforcement_detail",
        "general_notes", "drawing_index", "foundation_plan", "framing_plan",
        "window_schedule", "door_schedule", "finish_schedule", "ramp", "stair",
        "section", "elevation", "reinforcement_detail", "structural_detail",
        "floor_plan", "detail", "sheet_frame",
    )
    ordered_roles = list(priority) + [role for role in QUANTITY_ROLE_DEFINITIONS_V2 if role not in priority]
    for role in ordered_roles:
        terms = QUANTITY_ROLE_DEFINITIONS_V2[role]
        if any(str(term).casefold() in value for term in terms):
            matches.append(role)
    # A sheet frame is only a role when frame evidence exists and no quantity
    # role is present; callers still need independent Xref evidence.
    if not matches:
        return "other"
    if "sheet_frame" in matches and len(matches) > 1:
        matches.remove("sheet_frame")
    return matches[0]

def _quantity_normalize_index_entry_v2(title, discipline=None):
    """Normalize multilingual index titles without using drawing numbers."""
    value = unicodedata.normalize("NFKC", re.sub(r"\s+", " ", str(title or ""))).strip()
    compact = value.replace(" ", "")
    # Schedule is a semantic claim only when the title explicitly says
    # 일람표/schedule.  Concrete member + reinforcement/detail titles remain
    # detail/plan roles and must never be promoted to a schedule.
    exact_rules = (
        ("슬래브일람표", "slab_schedule"),
        ("기둥일람표", "column_schedule"),
        ("벽체일람표", "wall_schedule"),
        ("지하외벽일람표", "wall_schedule"),
        ("보일람표", "beam_schedule"),
        ("슬래브배근도", "slab_reinforcement_plan"),
        ("슬래브배근상세도", "slab_reinforcement_plan"),
        ("슬래브구조평면도", "slab_framing_plan"),
        ("슬래브골조평면도", "slab_framing_plan"),
        ("기둥배근도", "column_reinforcement_detail"),
        ("기둥배근상세도", "column_reinforcement_detail"),
        ("기둥상세도", "column_reinforcement_detail"),
        ("벽체배근도", "wall_reinforcement_detail"),
        ("벽체배근상세도", "wall_reinforcement_detail"),
        ("벽체상세도", "wall_reinforcement_detail"),
        ("보배근도", "beam_reinforcement_detail"),
        ("보배근상세도", "beam_reinforcement_detail"),
        ("보상세도", "beam_reinforcement_detail"),
        ("구조상세도", "structural_detail"),
        ("배근상세도", "structural_detail"),
        ("기초상세도", "structural_detail"),
        ("구조일반사항", "general_notes"),
        ("기초구조평면도", "foundation_plan"),
        ("기초평면도", "foundation_plan"),
        ("구조평면도", "framing_plan"),
        ("골조평면도", "framing_plan"),
    )
    role = next((normalized for needle, normalized in exact_rules if needle in compact), None)
    if role is None:
        role = _quantity_semantic_role_v2(value, discipline)
        # The generic classifier may see "배근" in a compound title.  Only
        # retain generic reinforcement_detail when no member-specific role was
        # identified; never infer a schedule from a bare reinforcement title.
        if "일람표" not in compact and not re.search(r"\bschedule\b", value, re.I) and role in {"column_schedule", "wall_schedule", "beam_schedule", "slab_schedule"}:
            role = "reinforcement_detail" if "배근" in compact else "other"
    return {"normalized_title": value, "role": role,
            "role_confidence": 0.9 if role != "other" else 0.0}

def _quantity_extract_sheet_numbers_v2(text):
    """Extract sheet identities, excluding grid/detail labels such as A1.

    A sheet identifier normally has an explicit separator before its numeric
    part (``S-401``, ``AR_12``) or a multi-digit alphanumeric code.  This is a
    lexical safety rule, not a project-specific drawing-number list.
    """
    value = unicodedata.normalize("NFKC", str(text or ""))
    patterns = (
        r"\b[A-Za-z가-힣]{1,12}\s*[-_–—]\s*\d{2,5}(?:\s*[~～]\s*(?:[A-Za-z가-힣]{1,12}\s*[-_–—]\s*)?\d{2,5})?\b",
        # Unseparated alphanumeric IDs need three digits; two-digit labels
        # such as rebar marks (D16) and grid bubbles (A1) are not sheet IDs.
        r"\b[A-Za-z]{1,8}\d{3,5}(?:\s*[~～]\s*\d{3,5})?\b",
        r"\b\d{1,4}\s*[-_]\s*\d{1,5}\b",
    )
    found = []
    for pattern in patterns:
        for item in re.findall(pattern, value):
            normalized = re.sub(r"\s+", "", item).upper()
            if normalized not in found:
                found.append(normalized)
            if "~" in normalized:
                range_match = re.match(
                    r"^([A-Z가-힣]+[-_]?)?(\d{2,5})~(?:[A-Z가-힣]+[-_]?)?(\d{2,5})$",
                    normalized,
                )
                if range_match:
                    prefix = range_match.group(1) or ""
                    for endpoint in (range_match.group(2), range_match.group(3)):
                        endpoint_value = prefix + endpoint
                        if endpoint_value not in found:
                            found.append(endpoint_value)
    return found[:40]
_CATEGORIES = ("foundations", "columns", "beams", "slabs", "walls", "stairs")
_CONTEXT_ROLES = {"general_notes", "column_schedule", "wall_schedule",
                  "beam_schedule", "slab_schedule"}
_DEFINITION_ROLES = _CONTEXT_ROLES | {"drawing_index", "reinforcement_detail",
                                     "column_reinforcement_detail",
                                     "wall_reinforcement_detail",
                                     "beam_reinforcement_detail", "structural_detail"}
_LABEL = re.compile(
    r"(?:(?P<number>도\s*면\s*번\s*호|DRAWING\s*(?:NO\.?|NUMBER)|SHEET\s*(?:NO\.?|NUMBER))"
    r"|(?P<title>도\s*면\s*명|DRAWING\s*(?:TITLE|NAME)|SHEET\s*TITLE)"
    r")(?=\s|[:：]|$)", re.I)


def role_from_title(title):
    if not title:
        return "other"
    if "도면목록" in re.sub(r"\s+", "", title) or re.search(r"drawing\s+index|sheet\s+list", title, re.I):
        return "drawing_index"
    role = _quantity_normalize_index_entry_v2(title)["role"]
    # Spaced Korean characters in exported title text still carry the same role.
    return role if role != "other" else _quantity_normalize_index_entry_v2(re.sub(r"\s+", "", title))["role"]


def identify_sheet(texts):
    """Read explicit fields. Missing or conflicting identity stays unresolved."""
    numbers, titles = [], []
    for text in texts:
        for line in unicodedata.normalize("NFKC", str(text or "")).splitlines():
            matches = list(_LABEL.finditer(line))
            if not matches or line[:matches[0].start()].strip():
                continue
            for i, match in enumerate(matches):
                value = line[match.end():matches[i+1].start() if i+1 < len(matches) else None]
                value = value.strip(" \t:：")
                if match.group("number"):
                    for number in _quantity_extract_sheet_numbers_v2(value):
                        if number not in numbers:
                            numbers.append(number)
                elif value and value not in titles:
                    titles.append(value)
    title_roles = [role_from_title(t) for t in titles]
    normalized_titles = {re.sub(r"\s+", "", title).casefold() for title in titles}
    conflict = len(numbers) > 1 or len(normalized_titles) > 1 or len(set(title_roles)) > 1
    status = ("conflict" if conflict else "identified" if numbers and titles
              else "title_only" if titles else "number_only" if numbers else "unidentified")
    return {"drawing_number": numbers[0] if len(numbers) == 1 else None,
            "drawing_numbers": numbers, "drawing_title": titles[0] if titles else None,
            "role": title_roles[0] if title_roles and not conflict else "other",
            "identity_status": status}


def _cad_file_identity(path, data):
    sheets = data.get("sheets") or []
    if not sheets:
        texts = [row.get("text", "") for row in data.get("source_texts", [])] or data.get("texts", [])
        return dict(identify_sheet(texts), source_method="explicit_text_fields")
    numbers = [sheet["drawing_number"] for sheet in sheets if sheet.get("drawing_number")]
    if len(sheets) == 1:
        sheet = sheets[0]
        return {"drawing_number": sheet.get("drawing_number"), "drawing_numbers": numbers,
                "drawing_title": sheet.get("drawing_title"), "role": role_from_title(sheet.get("drawing_title")),
                "identity_status": sheet["identity_status"], "source_method": "cad_title_block", "sheet_count": 1}
    return {"drawing_number": None, "drawing_numbers": numbers, "drawing_title": None, "role": "other",
            "identity_status": "multi_sheet" if numbers else "unidentified",
            "source_method": "cad_title_block", "sheet_count": len(sheets)}


def build_source_manifest(page_texts, *, document_sha, total_pages, cad_data=None, pdf_title_numbers=None):
    """Account for every PDF page, CAD file and CAD sheet; identity evidence stays explicit.

    A page is identified by explicit title fields or its title-block number; between two
    such pages, page order may name a page only when page and CAD sheet counts agree, and
    that page stays "inferred_order" (an issue, never verified).
    """
    pages, cad_files, cad_sheets, issues, links = [], [], [], [], []
    for page in range(1, total_pages + 1):
        identity = identify_sheet([page_texts.get(page, "")])
        pages.append(dict(identity, page=page, document_sha=document_sha,
                          source_id=f"pdf:{document_sha}:{page}", source_method="explicit_text_fields",
                          text_available=bool(str(page_texts.get(page, "")).strip())))
    sheets_by_file, index_entries = {}, []
    for path, data in (cad_data or {}).items():
        source_sha = data.get("source_member_sha")
        identity = _cad_file_identity(path, data)
        row = dict(identity, file=path, source_member_sha=source_sha,
                   source_id=f"cad:{source_sha}:{path}" if source_sha else None,
                   drawing_unit=data.get("drawing_unit"), text_coverage=data.get("text_coverage"))
        if data.get("error"):
            row["identity_status"] = "unreadable"
        elif (data.get("text_coverage") or {}).get("truncated"):
            row["identity_status"] = "incomplete_text"
        cad_files.append(row)
        if row["identity_status"] not in ("identified", "multi_sheet") or not source_sha:
            issues.append({"source_id": row["source_id"], "file": path,
                           "reason": "cad_source_hash_missing" if not source_sha
                                     else "cad_identity_" + row["identity_status"]})
        coverage = data.get("text_coverage") or {}
        if coverage.get("truncated"):
            issues.append({"source_id": row["source_id"], "file": path, "reason": "cad_text_truncated"})
        if data.get("sheets"):
            sheets_by_file[path] = data["sheets"]
        index_entries.extend(data.get("index_entries") or [])
    registry = quantity_sheet_identity.build_sheet_registry(sheets_by_file, index_entries=index_entries)
    issues.extend(registry["issues"])
    for sheet in registry["sheets"]:
        sha = sheet.get("source_member_sha")
        cad_sheets.append({"file": sheet["file"], "frame_handle": sheet.get("frame_handle"),
                           "source_id": f"cad:{sha}:{sheet['file']}#{sheet.get('frame_handle')}" if sha else None,
                           "drawing_number": sheet.get("drawing_number"), "drawing_title": sheet.get("drawing_title"),
                           "scale": sheet.get("scale"), "role": role_from_title(sheet.get("drawing_title")),
                           "identity_status": sheet["identity_status"], "method": sheet.get("method"),
                           "frame_bbox": sheet.get("frame_bbox")})
    if pdf_title_numbers:
        assigned = {row["page"]: row for row in quantity_sheet_identity.assign_pdf_identities(
            [row for row in pdf_title_numbers if row["page"] <= total_pages], registry)}
        for row in pages:
            found = assigned.get(row["page"])
            if row["identity_status"] == "identified" or not found or found["identity_status"] == "unidentified":
                if found and row["identity_status"] != "identified":
                    row["identity_reason"] = found.get("reason")
                continue
            row.update(drawing_number=found["drawing_number"], drawing_numbers=[found["drawing_number"]],
                       drawing_title=found["drawing_title"], role=role_from_title(found["drawing_title"]),
                       identity_status=found["identity_status"], source_method=found["source_method"],
                       identity_warnings=found["warnings"], index_title=found.get("index_title"),
                       cad_sheet=found.get("cad_sheet"), cad_alternates=found.get("cad_alternates") or [])
            sheet = found.get("cad_sheet")
            if sheet:
                sha = sheet.get("source_member_sha")
                links.append({"drawing_number": found["drawing_number"], "pdf_page": row["page"],
                              "cad_file": sheet["file"], "cad_frame_handle": sheet.get("frame_handle"),
                              "pdf_source_id": row["source_id"],
                              "cad_source_id": f"cad:{sha}:{sheet['file']}#{sheet.get('frame_handle')}" if sha else None,
                              "match_method": found["source_method"], "identity_status": found["identity_status"],
                              "quantity_verified": False})
    for row in pages:
        if row["identity_status"] != "identified":
            issues.append({"source_id": row["source_id"], "reason": "pdf_identity_" + row["identity_status"],
                           "page": row["page"]})
    linked_pages = {link["pdf_page"] for link in links}
    pdf_by_number, cad_by_number = defaultdict(list), defaultdict(list)
    for row in pages:
        if row["identity_status"] == "identified" and row["role"] != "drawing_index" and row["page"] not in linked_pages:
            pdf_by_number[row["drawing_number"]].append(row)
    for row in cad_files:
        if row["identity_status"] == "identified" and row["source_id"] and row["role"] != "drawing_index":
            cad_by_number[row["drawing_number"]].append(row)
    for number in sorted(set(pdf_by_number) & set(cad_by_number)):
        pdfs, cads = pdf_by_number[number], cad_by_number[number]
        if len(pdfs) != 1 or len(cads) != 1:
            issues.append({"drawing_number": number, "reason": "ambiguous_pdf_cad_identity"})
            continue
        pdf, cad = pdfs[0], cads[0]
        if pdf["role"] == "other" or cad["role"] == "other" or pdf["role"] != cad["role"]:
            issues.append({"drawing_number": number, "reason": "pdf_cad_role_conflict"})
            continue
        links.append({"drawing_number": number, "pdf_page": pdf["page"], "cad_file": cad["file"],
                      "pdf_source_id": pdf["source_id"], "cad_source_id": cad["source_id"],
                      "match_method": "unique_explicit_number_and_role", "quantity_verified": False})
    statuses = Counter(row["identity_status"] for row in pages)
    return {"schema": "quantity_sources_v2", "document_sha": document_sha,
            "total_pages": total_pages, "pdf_pages": pages, "cad_files": cad_files,
            "cad_sheets": cad_sheets, "links": links, "issues": issues,
            "identity_summary": dict(statuses),
            "context_pages": [row["page"] for row in pages if row["role"] in _CONTEXT_ROLES
                              and row["identity_status"] in {"identified", "title_only", "inferred_order"}],
            "identity_complete": bool(pages or cad_files) and not issues,
            "quantity_verified": False}


def valid_member_bbox(bbox):
    """A normalized PDF occurrence needs a positive extent within the page."""
    if not isinstance(bbox, dict) or type(bbox.get("page")) is not int or bbox["page"] < 1:
        return False
    if not all(type(bbox.get(field)) in (int, float) and math.isfinite(bbox[field])
               for field in ("x", "y", "w", "h")):
        return False
    x, y, width, height = (bbox[field] for field in ("x", "y", "w", "h"))
    return (0 <= x <= 1 and 0 <= y <= 1 and width > 0 and height > 0
            and x + width <= 1 + 1e-9 and y + height <= 1 + 1e-9)


def attach_member_sources(members, manifest):
    """Preserve source locations in reviews; never promote them to verified totals."""
    result = deepcopy(members)
    result["source_manifest"] = deepcopy(manifest)
    pages = {row["page"]: row for row in manifest["pdf_pages"]}
    cad_files = {row["file"]: row for row in manifest["cad_files"] if row["source_id"]}
    for category in _CATEGORIES:
        category_items = result.get(category)
        if category_items is None:
            continue
        if not isinstance(category_items, list):
            result[category] = []
            if not isinstance(result.get("notes"), list):
                result["notes"] = []
            result["notes"].append(f"부재 목록 형식 오류: {category} — 원본 추출 응답 확인이 필요합니다.")
            continue
        for item in category_items:
            if not isinstance(item, dict):
                continue
            bbox = item.get("bbox") if isinstance(item.get("bbox"), dict) else {}
            page = bbox.get("page")
            row = pages.get(page) if type(page) is int else None
            refs = []
            status, note = "missing_location", "부재의 원본 위치와 개수 근거를 확인해 주세요."
            if row:
                refs.append({"kind": "pdf", "page": page, "source_id": row["source_id"],
                             "document_sha": manifest["document_sha"], "drawing_number": row["drawing_number"],
                             "drawing_title": row["drawing_title"], "role": row["role"],
                             "identity_status": row["identity_status"],
                             "identity_method": row.get("source_method"),
                             "identity_warnings": list(row.get("identity_warnings") or []),
                             "bbox": deepcopy(bbox)})
                if valid_member_bbox(bbox):
                    status, note = "located", "원본 위치 연결됨. 치수·개수·배근은 별도 확인이 필요합니다."
                    refs[-1]["location_precision"] = "bbox"
                else:
                    status, note = "page_only", "원본 페이지 연결됨. 부재의 정확한 위치·치수·개수는 확인이 필요합니다."
                    refs[-1]["location_precision"] = "page_only"
                if row["role"] in _DEFINITION_ROLES:
                    status, note = "definition_only", "일람표·상세도의 정의 위치입니다. 실제 부재 개수는 평면도에서 확인해 주세요."
            elif page is not None:
                status, note = "invalid_page", "원본 PDF에 없는 페이지를 가리킵니다. 부재 위치를 다시 확인해 주세요."
            # CAD filename is an explicit model-returned location, never inferred from mark.
            source_file = item.get("source_file")
            cad = cad_files.get(source_file) if isinstance(source_file, str) else None
            if source_file is not None and not isinstance(source_file, str):
                status, note = "invalid_cad_path", "원본 CAD 파일명이 잘못된 형식입니다. 원본 위치를 다시 확인해 주세요."
            if cad:
                refs.append({"kind": "cad", "file": cad["file"], "source_id": cad["source_id"],
                             "source_member_sha": cad["source_member_sha"],
                             "drawing_number": cad["drawing_number"], "role": cad["role"]})
                if not row and status != "invalid_page":
                    status, note = "file_only", "원본 CAD 파일 연결됨. 실제 부재 위치·치수·개수는 별도 확인이 필요합니다."
            item["source_refs"] = refs
            item["source_review"] = {"status": status, "note": note, "quantity_verified": False}
    return result
