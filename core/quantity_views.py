"""
AI 구조산출 자동화 — views.py
구조도면 ZIP(DWG) + 구조/건축 PDF 합본을 받아 Gemini로 수량 추출

필요 패키지:
    pip install ezdxf pdf2image pillow google-genai openpyxl
    apt-get install poppler-utils  (pdf2image 의존, macOS는 brew install poppler)
"""

import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from functools import wraps

import ezdxf
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from pdf2image import convert_from_bytes, pdfinfo_from_bytes
from PIL import Image, ImageDraw

from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from google import genai
from google.genai import types

from .views import admin_required
from .quantity_calc import compute_structural_quantities, compute_massing_model


# ─────────────────────────────────────────────
#  접근 제한: 이 수량산출 도구는 요청 1건마다 Gemini API 비용이 실제로 발생한다.
#  로그인/권한 체크 없이 공개돼 있으면 누구든(봇 포함) 파일 없이/의미없는 파일로 반복
#  호출해서 크레딧을 소모시킬 수 있다 — 아직 결제 시스템을 붙이기 전(관리자 테스트 단계)
#  이므로, 실제로 비용이 드는 화면/엔드포인트는 전부 관리자(스태프/슈퍼유저)만 쓸 수
#  있게 막는다. 나중에 결제 시스템이 붙으면 이 제한을 결제 여부 체크로 바꾸면 된다.
# ─────────────────────────────────────────────
def _admin_only_json(view_func):
    """JSON을 돌려주는 API 엔드포인트용 — user_passes_test처럼 로그인 페이지로 리다이렉트하면
    fetch()가 HTML을 JSON으로 파싱하려다 깨지므로, 대신 403 JSON을 그대로 돌려준다."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not admin_required(request.user):
            return JsonResponse({"error": "관리자만 사용할 수 있는 기능입니다."}, status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped

# core/ai_writer.py와 동일한 방식으로 .env의 GEMINI_API_KEY를 사용합니다.
GEMINI_QUANTITY_MODEL = os.environ.get("GEMINI_QUANTITY_MODEL") or os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-pro")


def get_gemini_client():
    """GEMINI_API_KEY가 없으면 None을 반환합니다 (import 시점에 죽지 않도록 지연 초기화)."""
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


# ─────────────────────────────────────────────
#  진행률 표시: api_run_quantity가 한 번의 긴 HTTP 요청 안에서 여러 단계(입면/건축분석/
#  구조부재 배치추출)를 거치는 동안, 프론트엔드가 별도로 폴링해서 "지금 어디까지 됐는지"를
#  볼 수 있게 하는 아주 단순한 인메모리 진행상황 저장소.
#  - 로컬 단일 프로세스 개발 서버 기준으로 설계했다(멀티 워커/멀티 서버 배포에서는 각
#    워커가 자기 메모리만 보므로 폴링이 다른 워커로 튀면 못 볼 수 있음 — 지금 용도엔 충분).
#  - job_id는 프론트엔드가 요청 시작 전에 만들어서 업로드와 함께 보내고, 그 값으로 폴링한다.
#  - 오래된 항목이 계속 쌓이지 않도록, 조회/기록 시마다 일정 시간(_PROGRESS_TTL_SEC)이 지난
#    항목은 같이 청소한다.
# ─────────────────────────────────────────────
_PROGRESS_STORE = {}
_PROGRESS_LOCK = threading.Lock()
_PROGRESS_TTL_SEC = 60 * 30  # 30분 이상 조회 없는 job은 정리


def _progress_cleanup_locked():
    cutoff = time.time() - _PROGRESS_TTL_SEC
    stale = [jid for jid, v in _PROGRESS_STORE.items() if v.get("_updated_at", 0) < cutoff]
    for jid in stale:
        _PROGRESS_STORE.pop(jid, None)


def _progress_set(job_id, stage, current, total, label="", stage_index=1, total_stages=1):
    """job_id가 없으면(프론트가 진행률 표시를 요청하지 않은 옛 클라이언트 등) 조용히 무시한다.
    stage_index/total_stages는 "건축분석→입면검토→구조추출" 같은 여러 단계 중 지금 몇 번째
    단계인지를 나타내고, current/total은 그 단계 안에서의 진행(구조추출이면 배치 번호)이다.
    프론트엔드는 두 값을 합쳐 전체 퍼센티지를 계산한다."""
    if not job_id:
        return
    with _PROGRESS_LOCK:
        _PROGRESS_STORE[job_id] = {
            "stage": stage, "current": current, "total": max(total, 1), "label": label,
            "stage_index": stage_index, "total_stages": max(total_stages, 1),
            "_updated_at": time.time(),
        }
        _progress_cleanup_locked()


def _progress_clear(job_id):
    if not job_id:
        return
    with _PROGRESS_LOCK:
        _PROGRESS_STORE.pop(job_id, None)


def _progress_get(job_id):
    with _PROGRESS_LOCK:
        _progress_cleanup_locked()
        return _PROGRESS_STORE.get(job_id)


# ─────────────────────────────────────────────
#  취소 요청: 사용자가 분석 도중 "취소" 버튼을 누르면, 그 job_id를 이 집합에 넣어둔다.
#  api_run_quantity가 각 단계(건축분석/입면검토/구조부재 배치추출)를 시작하기 직전에
#  이 집합을 확인해서, 이미 취소 요청이 온 경우 그 단계부터는 건너뛰고 지금까지
#  모인 결과만 반환한다. 이미 Gemini에 보낸(진행 중인) 배치 1개는 어차피 끝까지
#  가야 하지만, 아직 시작 안 한 나머지 배치는 호출 자체를 안 해서 비용을 아낀다.
#  진행률 저장소와 동일하게 로컬 단일 프로세스 개발 서버 기준의 단순 구현이다.
# ─────────────────────────────────────────────
_CANCEL_STORE = set()
_CANCEL_LOCK = threading.Lock()


def _cancel_request(job_id):
    if not job_id:
        return
    with _CANCEL_LOCK:
        _CANCEL_STORE.add(job_id)


def _is_cancelled(job_id):
    if not job_id:
        return False
    with _CANCEL_LOCK:
        return job_id in _CANCEL_STORE


def _cancel_clear(job_id):
    if not job_id:
        return
    with _CANCEL_LOCK:
        _CANCEL_STORE.discard(job_id)


# ─────────────────────────────────────────────
#  도면 확인(검토) 단계: 구조 부재 추출(Gemini)까지만 끝내고 실제 물량 계산은 바로
#  하지 않는다 — 대신 추출된 members를 job_id로 임시 저장해두고, 채팅창에 "도면 확인"
#  버튼을 띄운다. 사용자가 색칠된 도면 팝업을 보고 틀린 부재를 체크/수정한 뒤 "진행"을
#  누르면, 그 수정사항(corrections)과 함께 job_id로 이 저장소에서 members를 꺼내와
#  보정한 다음에야 실제 물량 계산(compute_structural_quantities)을 실행한다.
#  진행률/취소 저장소와 동일하게 로컬 단일 프로세스 기준의 단순 인메모리 구현이다.
#  리뷰는 사람이 직접 보고 판단하는 단계라 진행률 저장소(30분)보다 TTL을 넉넉하게 둔다.
# ─────────────────────────────────────────────
_EXTRACTION_STORE = {}
_EXTRACTION_LOCK = threading.Lock()
_EXTRACTION_TTL_SEC = 60 * 60 * 2  # 2시간 — 도면 검토는 시간이 걸릴 수 있어 넉넉하게


def _extraction_cleanup_locked():
    cutoff = time.time() - _EXTRACTION_TTL_SEC
    stale = [jid for jid, v in _EXTRACTION_STORE.items() if v.get("_created_at", 0) < cutoff]
    for jid in stale:
        _EXTRACTION_STORE.pop(jid, None)


def _extraction_store_set(job_id, members, elevation_data):
    if not job_id:
        return
    with _EXTRACTION_LOCK:
        _EXTRACTION_STORE[job_id] = {
            "members": members, "elevation_data": elevation_data, "_created_at": time.time(),
        }
        _extraction_cleanup_locked()


def _extraction_store_pop(job_id):
    if not job_id:
        return None
    with _EXTRACTION_LOCK:
        _extraction_cleanup_locked()
        return _EXTRACTION_STORE.pop(job_id, None)


# 체크리스트에 보여주고, 사용자가 고칠 수 있게 허용할 카테고리별 핵심 필드.
# (전체 필드를 다 편집 가능하게 하면 UI가 너무 복잡해져서, 실제로 값이 자주 틀리는
#  치수/개수/철근규격 위주로만 추린다 — mark/zone/section/bbox 등은 편집 대상에서 제외)
_MEMBER_SUMMARY_FIELDS = {
    "foundations": ["length_m", "width_m", "thickness_m", "count", "rebar_size"],
    "columns": ["width_m", "depth_m", "height_m", "count", "main_rebar_size"],
    "beams": ["width_m", "depth_m", "length_m", "count", "main_rebar_size"],
    "slabs": ["area_m2", "thickness_m", "count", "rebar_size"],
    "walls": ["length_m", "height_m", "thickness_m", "count", "rebar_size"],
    "stairs": ["width_m", "length_m", "thickness_m", "count", "rebar_size"],
}


def _build_review_checklist(members):
    """members 딕셔너리를 프론트엔드 검토 팝업의 왼쪽 체크리스트용 평평한 리스트로 변환한다.
    각 항목은 review_id(카테고리:인덱스)로 식별되며, 이 review_id는 나중에 corrections에서
    "이 항목을 지워라/이 필드를 이 값으로 바꿔라"를 지정할 때 그대로 사용된다."""
    checklist = []
    for cat_key, label in _CATEGORY_KEY_TO_LABEL.items():
        for i, it in enumerate(members.get(cat_key) or []):
            if not isinstance(it, dict):
                continue
            fields = {}
            for f in _MEMBER_SUMMARY_FIELDS.get(cat_key, []):
                if f in it:
                    fields[f] = it.get(f)
            bbox = it.get("bbox") if isinstance(it.get("bbox"), dict) else None
            checklist.append({
                "review_id": f"{cat_key}:{i}",
                "category": label,
                "mark": it.get("mark") or "",
                "zone": it.get("zone") or "",
                "section": it.get("section") or "",
                "fields": fields,
                "bbox_page": bbox.get("page") if bbox else None,
            })
    return checklist


def _apply_member_corrections(members, corrections):
    """검토 팝업에서 받은 corrections(제거/수정 목록)를 members에 반영한다.
    review_id 형식은 "카테고리:인덱스"이며, _build_review_checklist가 만든 것과
    반드시 같은 순서/인덱스 기준이어야 한다(그 사이에 members가 바뀌면 안 됨)."""
    corrected = json.loads(json.dumps(members))  # 얕은 복사로는 중첩 dict가 공유돼서 깊은 복사 사용
    removals = set()
    edits = {}
    for c in (corrections or []):
        if not isinstance(c, dict):
            continue
        rid = c.get("review_id")
        if not rid:
            continue
        action = c.get("action")
        if action == "remove":
            removals.add(rid)
        elif action == "edit":
            edits[rid] = c.get("fields") or {}

    for cat_key in _CATEGORY_KEY_TO_LABEL.keys():
        items = corrected.get(cat_key) or []
        new_items = []
        for i, it in enumerate(items):
            rid = f"{cat_key}:{i}"
            if rid in removals:
                continue
            if rid in edits and isinstance(it, dict):
                allowed = set(_MEMBER_SUMMARY_FIELDS.get(cat_key, []))
                for k, v in edits[rid].items():
                    if k in allowed:
                        it[k] = v
            new_items.append(it)
        corrected[cat_key] = new_items
    return corrected


def _extract_text_from_gemini_response(response):
    """core/ai_writer.py의 동명 함수와 동일한 방식으로 응답 텍스트를 안전하게 추출합니다."""
    text = getattr(response, "text", None)
    if text:
        return str(text).strip()

    pieces = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                pieces.append(str(part_text))
    return "\n".join(piece for piece in pieces if piece).strip()


def _try_repair_truncated_json(raw_clean):
    """
    Gemini 응답이 max_output_tokens 한도에 걸려 중간에 잘렸을 때, 마지막으로 완전히 끝난
    항목까지만 남기고 열린 배열/객체를 강제로 닫아서 부분 복구를 시도한다.
    (전체 파싱 실패로 데이터를 통째로 버리는 것보다, 끝부분 몇 개 항목만 못 읽더라도
    앞부분 데이터는 건지는 게 낫기 때문 — 실제로 큰 프로젝트에서 이 문제가 발생했음)
    실패하면 None을 반환해서 호출부가 기존처럼 에러 처리하게 한다.
    """
    depth_stack = []
    in_str = False
    escape = False
    safe_cutoffs = []  # (그 지점까지의 인덱스, 그 시점의 열린 괄호 스택)
    for i, ch in enumerate(raw_clean):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            depth_stack.append(ch)
        elif ch in "}]":
            if depth_stack:
                depth_stack.pop()
            safe_cutoffs.append((i + 1, list(depth_stack)))
        elif ch == "," and depth_stack:
            safe_cutoffs.append((i, list(depth_stack)))

    for cutoff_idx, stack_snapshot in reversed(safe_cutoffs[-200:]):
        candidate = raw_clean[:cutoff_idx].rstrip().rstrip(",")
        closing = "".join("]" if c == "[" else "}" for c in reversed(stack_snapshot))
        attempt = candidate + closing
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue
    return None


def _code_matches_filename(code: str, filename_upper: str) -> bool:
    """
    도면 코드가 파일명에 존재하는지 확인한다.

    실제 현장 도면은 'S-001~002', 'S-111~130'처럼 여러 도면번호를 구간(~)으로
    묶어 파일명을 짓는 경우가 많다. 단순 substring 매칭("S-002" in filename)은
    "S-001~002"에서 "S-002"를 놓치므로, 구간 표기를 해석해서 코드가 그 구간에
    포함되는지까지 확인한다.
    """
    if code in filename_upper:
        return True

    m = re.match(r"^([A-Z]+)-(\d+)$", code)
    if not m:
        return False
    prefix, num_str = m.group(1), m.group(2)
    num = int(num_str)

    for fm in re.finditer(re.escape(prefix) + r"-(\d+)(?:~(\d+))?", filename_upper):
        low = int(fm.group(1))
        high = int(fm.group(2)) if fm.group(2) else low
        if low <= num <= high:
            return True
    return False


# ─────────────────────────────────────────────
#  필수 구조도면 목록 (도면번호: 도면명)
# ─────────────────────────────────────────────
REQUIRED_STRUCTURAL = {
    "S-001": "구조설계개요 및 시방서",
    "S-002": "사용자재표 (콘크리트/철근 규격)",
    "S-101": "기초평면도",
    "S-102": "기초상세도",
    "S-103": "지하층 골조평면도",
    "S-104": "지하외벽 배근도",
    "S-201": "기둥배근도",
    "S-202": "기둥일람표",
    "S-203": "보배근도",
    "S-204": "슬래브 배근도 (층별)",
    "S-301": "전단벽 배근도",
    "S-302": "계단 배근도",
    "S-401": "지붕층 골조평면도",
    "S-402": "옥탑층 골조평면도",
    "S-501": "부재일람표 요약본",
}

REQUIRED_ARCHITECTURAL = {
    "A-000": "건축 일반사항 / 범례",
    "A-001": "배치도",
    "A-101": "1층 평면도",
    "A-102": "2층 평면도",
    "A-103": "지붕층 평면도",
    "A-201": "입면도 (정면/배면/측면)",
    "A-301": "단면도",
    "A-401": "창호도",
    "A-501": "내부마감표",
    "A-601": "계단 상세도",
}


# ─────────────────────────────────────────────
#  뷰: 메인 페이지
# ─────────────────────────────────────────────
@user_passes_test(admin_required)
def quantity_main(request):
    """AI 구조산출 자동화 메인 페이지 — 관리자(스태프/슈퍼유저)만 접근 가능.
    Gemini 호출 비용이 실제로 발생하는 도구라 결제 시스템이 붙기 전까지는 공개하지 않는다."""
    user_display = request.user.username if request.user.is_authenticated else "게스트"
    return render(request, "core/quantity_main.html", {
        "user_display": user_display,
        "required_structural": REQUIRED_STRUCTURAL,
        "required_architectural": REQUIRED_ARCHITECTURAL,
    })


# ─────────────────────────────────────────────
#  API: ZIP 파일 검증 (DWG 목록 확인)
# ─────────────────────────────────────────────
@require_POST
@_admin_only_json
def api_check_zip(request):
    """
    ZIP 파일을 받아 구조/건축 도면 파일 존재 여부 확인
    Returns JSON: { structural: {code: {name, exists}}, architectural: {...}, missing: [...] }
    """
    zip_file = request.FILES.get("zip_file")
    if not zip_file:
        return JsonResponse({"error": "ZIP 파일이 없습니다."}, status=400)

    if not zip_file.name.lower().endswith(".zip"):
        return JsonResponse({"error": "ZIP 파일만 업로드 가능합니다."}, status=400)

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_file.read()))
    except zipfile.BadZipFile:
        return JsonResponse({"error": "올바른 ZIP 파일이 아닙니다."}, status=400)

    # ZIP 내부 파일명 목록 (소문자, 확장자 제거)
    zip_names = [os.path.splitext(os.path.basename(n))[0].upper() for n in zf.namelist()]

    def check_list(required_dict):
        result = {}
        for code, name in required_dict.items():
            exists = any(_code_matches_filename(code, fname) for fname in zip_names)
            result[code] = {"name": name, "exists": exists}
        return result

    structural_result = check_list(REQUIRED_STRUCTURAL)
    arch_result = check_list(REQUIRED_ARCHITECTURAL)

    missing_structural = [
        {"code": c, "name": d["name"]}
        for c, d in structural_result.items() if not d["exists"]
    ]
    missing_arch = [
        {"code": c, "name": d["name"]}
        for c, d in arch_result.items() if not d["exists"]
    ]

    return JsonResponse({
        "structural": structural_result,
        "architectural": arch_result,
        "missing_structural": missing_structural,
        "missing_architectural": missing_arch,
        "total_structural": len(REQUIRED_STRUCTURAL),
        "found_structural": sum(1 for d in structural_result.values() if d["exists"]),
        "total_architectural": len(REQUIRED_ARCHITECTURAL),
        "found_architectural": sum(1 for d in arch_result.values() if d["exists"]),
    })


# ─────────────────────────────────────────────
#  DWG → DXF 변환: ODA File Converter 연동
#  (core/views.py의 cblcad_dwg_to_dxf_api 와 동일한 탐색 방식을 재사용)
# ─────────────────────────────────────────────
def _find_oda_converter():
    """서버에 설치된 ODA File Converter 실행 파일 경로를 찾는다. 없으면 None."""
    candidates = [
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter 26.10.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter 25.12.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter 24.12.app/Contents/MacOS/ODAFileConverter",
        shutil.which("ODAFileConverter"),
        shutil.which("ODAFileConverter.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _convert_dwg_folder_to_dxf(dwg_dir, out_dir, timeout=90):
    """
    dwg_dir 안의 모든 .dwg 파일을 ODA File Converter로 일괄 변환해 out_dir에 저장.
    변환기가 없거나 실패하면 (False, 메시지) 반환.
    """
    import subprocess

    converter = _find_oda_converter()
    if not converter:
        return False, "ODA_NOT_FOUND"

    cmd = [converter, dwg_dir, out_dir, "ACAD2004", "DXF", "0", "1"]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    except Exception as e:
        return False, f"CONVERT_EXCEPTION: {e}"

    return True, "OK"


# ─────────────────────────────────────────────
#  DWG/DXF 파싱: ezdxf로 레이어/텍스트/치수/도형(길이·면적·블록개수) 추출
# ─────────────────────────────────────────────
def _polygon_area(pts):
    """슐레이스 공식으로 폐곡선 면적(부호 없음) 계산"""
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _polygon_centroid(pts):
    """폐곡선 도심(centroid) 계산 — point-in-polygon 판정용 대표점"""
    n = len(pts)
    A = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        A += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    A *= 0.5
    if abs(A) < 1e-9:
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    return (cx / (6 * A), cy / (6 * A))


def _point_in_polygon(pt, poly):
    """레이캐스팅 알고리즘으로 점이 폐곡선 내부에 있는지 판정"""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _split_outer_and_holes(polys):
    """
    같은 레이어의 폐곡선 리스트를 콘크리트 외곽선(outer)과 그 안에 뚫린
    개구부(hole)로 분리한다. 별도 레이어 이름 규칙에 의존하지 않고,
    순수 기하학적 포함관계(centroid가 더 큰 폴리곤 안에 있는지)만으로 판단한다.
    "선(콘크리트 외곽선)만 따서" 개구부를 자동으로 인식하기 위한 로직.
    Returns: (outer_area_sum, hole_area_sum, hole_count)
    """
    if not polys:
        return 0.0, 0.0, 0
    # 면적 큰 순서로 정렬 — 더 큰 폴리곤이 outer 후보
    sorted_polys = sorted(polys, key=lambda p: p[1], reverse=True)
    is_hole = [False] * len(sorted_polys)

    for i, (pts_i, area_i) in enumerate(sorted_polys):
        centroid_i = _polygon_centroid(pts_i)
        for j in range(i):
            if is_hole[j]:
                continue
            pts_j, area_j = sorted_polys[j]
            if area_j <= area_i:
                continue
            if _point_in_polygon(centroid_i, pts_j):
                is_hole[i] = True
                break

    outer_area = sum(a for (_, a), hole in zip(sorted_polys, is_hole) if not hole)
    hole_area = sum(a for (_, a), hole in zip(sorted_polys, is_hole) if hole)
    hole_count = sum(1 for hole in is_hole if hole)
    return outer_area, hole_area, hole_count


def _extract_dxf_quantities(doc):
    """ezdxf Document에서 레이어별 도형 통계(길이/면적/블록 개수) + 텍스트/치수를 뽑는다.

    닫힌 폴리곤(벽체/슬래브 콘크리트 외곽선 등)은 같은 레이어 안에서 다른 폴리곤을
    감싸고 있으면 그 안쪽 폴리곤을 "개구부(hole)"로 인식해 콘크리트 순면적에서
    자동으로 빼준다 (레이어 이름 규칙에 의존하지 않는 순수 기하 판정).
    """
    msp = doc.modelspace()

    layer_names = [l.dxf.name for l in doc.layers]
    texts = []
    dimensions = []
    block_counts = {}
    layer_stats = {}  # layer -> {"length": float, "count": int}
    closed_polys = {}  # layer -> [(points, area), ...]

    def stat(layer):
        return layer_stats.setdefault(layer, {"length": 0.0, "count": 0})

    for entity in msp:
        dxftype = entity.dxftype()
        layer = getattr(entity.dxf, "layer", "0")
        s = stat(layer)
        s["count"] += 1

        try:
            if dxftype == "LINE":
                sp, ep = entity.dxf.start, entity.dxf.end
                s["length"] += ((ep.x - sp.x) ** 2 + (ep.y - sp.y) ** 2) ** 0.5
            elif dxftype in ("LWPOLYLINE", "POLYLINE"):
                pts = list(entity.get_points("xy")) if dxftype == "LWPOLYLINE" else [
                    (v.dxf.location.x, v.dxf.location.y) for v in entity.vertices
                ]
                length = 0.0
                for i in range(len(pts) - 1):
                    length += ((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** 0.5
                is_closed = getattr(entity, "is_closed", False)
                if is_closed and len(pts) >= 3:
                    length += ((pts[0][0] - pts[-1][0]) ** 2 + (pts[0][1] - pts[-1][1]) ** 2) ** 0.5
                    area = _polygon_area(pts)
                    if area > 0:
                        closed_polys.setdefault(layer, []).append((pts, area))
                s["length"] += length
            elif dxftype == "HATCH":
                # 근사 면적(바운딩 박스 기반 - 정밀하지 않음, 참고용)
                try:
                    bbox = entity.paths[0].source_boundary_objects
                except Exception:
                    pass
            elif dxftype == "INSERT":
                block_counts[entity.dxf.name] = block_counts.get(entity.dxf.name, 0) + 1
            elif dxftype in ("TEXT", "MTEXT"):
                t = entity.dxf.text if dxftype == "TEXT" else entity.text
                if t and t.strip():
                    texts.append(t.strip()[:200])
            elif dxftype == "DIMENSION":
                val = entity.dxf.actual_measurement
                dimensions.append(round(val, 2))
        except Exception:
            pass

    # 레이어별 폐곡선을 outer(콘크리트 외곽선)/hole(개구부)로 분리해서
    # net_closed_area(개구부 제외 순면적)를 계산한다.
    opening_stats = {
        layer: _split_outer_and_holes(polys)
        for layer, polys in closed_polys.items()
    }

    # 도면 단위가 mm인지 m인지 알 수 없으므로 원시값(도면 단위) 그대로 반환하고
    # 어떤 단위인지는 AI가 텍스트/치수 정보를 보고 판단하도록 note를 남긴다.
    layer_summary = {}
    for layer, v in layer_stats.items():
        outer_area, hole_area, hole_count = opening_stats.get(layer, (0.0, 0.0, 0))
        layer_summary[layer] = {
            "entity_count": v["count"],
            "total_length": round(v["length"], 2),
            "net_closed_area": round(outer_area - hole_area, 2),
            "opening_area": round(hole_area, 2),
            "opening_count": hole_count,
        }

    return {
        "layers": layer_names[:40],
        "layer_geometry": layer_summary,
        "block_counts": block_counts,
        "texts": texts[:120],
        "dimensions": dimensions[:120],
        "unit_note": "length/area 값은 도면 원본 단위(대부분 mm) 기준 원시값입니다. 실제 축척/단위는 texts, dimensions, 도면 제목을 참고해 판단하세요.",
        "opening_note": "net_closed_area는 같은 레이어 안에서 다른 폐곡선에 완전히 둘러싸인 폴리곤을 개구부(hole)로 자동 인식해 뺀 순면적입니다. opening_count가 0보다 크면 해당 레이어(주로 벽체/슬래브 콘크리트 외곽선)에 뚫린 구멍이 있다는 뜻이니, walls/slabs의 openings 필드를 채울 때 이 값을 실제 도면 이미지와 대조해 참고하세요.",
    }


def parse_dwg_from_zip(zip_bytes, target_codes):
    """
    ZIP 바이트에서 target_codes에 해당하는 DWG/DXF 파일을 찾아 파싱한다.
    DWG(바이너리 AutoCAD 포맷)는 ezdxf가 직접 읽지 못하므로,
    서버에 ODA File Converter가 설치돼 있으면 자동으로 DXF로 변환 후 파싱한다.
    Returns: { filename: { layers, layer_geometry, block_counts, texts, dimensions, ... } | {"error": ...} }
    """
    result = {}
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))

    matched_members = []
    for member in zf.namelist():
        ext = os.path.splitext(member)[1].lower()
        if ext not in (".dwg", ".dxf"):
            continue
        basename = os.path.basename(member).upper()
        if any(_code_matches_filename(code, basename) for code in target_codes):
            matched_members.append(member)

    if not matched_members:
        return result

    with tempfile.TemporaryDirectory(prefix="cbl_qty_") as work_dir:
        dwg_dir = os.path.join(work_dir, "dwg_in")
        dxf_out_dir = os.path.join(work_dir, "dxf_out")
        os.makedirs(dwg_dir, exist_ok=True)
        os.makedirs(dxf_out_dir, exist_ok=True)

        # 1차: 원본 그대로 풀어두기 (파일명 충돌 방지를 위해 인덱스 접두어 사용)
        member_to_local = {}
        dwg_present = False
        for idx, member in enumerate(matched_members):
            ext = os.path.splitext(member)[1].lower()
            local_name = f"{idx:03d}_{os.path.basename(member)}"
            local_path = os.path.join(dwg_dir if ext == ".dwg" else work_dir, local_name)
            with open(local_path, "wb") as f:
                f.write(zf.read(member))
            member_to_local[member] = (ext, local_path, local_name)
            if ext == ".dwg":
                dwg_present = True

        oda_ok, oda_msg = (False, "SKIPPED")
        if dwg_present:
            oda_ok, oda_msg = _convert_dwg_folder_to_dxf(dwg_dir, dxf_out_dir)

        for member in matched_members:
            ext, local_path, local_name = member_to_local[member]
            out_name = os.path.basename(member)

            if ext == ".dxf":
                dxf_path = local_path
            else:
                # 변환된 DXF 찾기 (확장자만 dxf로 바뀌고 파일명은 동일)
                converted_name = os.path.splitext(local_name)[0] + ".dxf"
                dxf_path = os.path.join(dxf_out_dir, converted_name)
                if not oda_ok:
                    result[out_name] = {
                        "error": (
                            "DWG 파일은 서버에 설치된 ODA File Converter가 있어야 자동 변환됩니다. "
                            f"(사유: {oda_msg}) DXF로 내보내서 다시 업로드하거나, 서버에 ODA File Converter를 설치해 주세요."
                        )
                    }
                    continue
                if not os.path.exists(dxf_path):
                    result[out_name] = {"error": "DWG → DXF 변환에 실패했습니다 (변환 결과 파일 없음)."}
                    continue

            try:
                doc = ezdxf.readfile(dxf_path)
                result[out_name] = _extract_dxf_quantities(doc)
            except Exception as e:
                result[out_name] = {"error": str(e)}

    return result


# ─────────────────────────────────────────────
#  PDF → 이미지 변환
# ─────────────────────────────────────────────
# 합본 PDF가 수십~백 페이지인 실제 프로젝트 도면집을 감안해, 앞 5장만 보내던
# 이전 한도를 넉넉하게 올려둔다. 입력(이미지) 토큰 자체는 80장을 넣어도 Gemini 2.5 Pro의
# 1M 토큰 한도에 여유가 있다 — 문제는 출력(OUTPUT) 쪽이다: 부재가 많은 대형 프로젝트를
# 한 번에 몰아넣으면 응답 JSON이 max_output_tokens(65536)를 넘어 잘리거나, 요청 자체가
# 타임아웃/오류로 실패해서 전체 결과가 0건이 되는 문제가 실제로 발생했다. 그래서 구조 부재
# 추출(extract_structural_members)은 EXTRACTION_BATCH_PAGE_SIZE 단위로 나눠 여러 번
# 호출한 뒤 결과를 합치도록 배치 처리한다 — 아래 관련 코드 참고.
MAX_PDF_PAGES_TO_GEMINI = 80

# convert_from_bytes()는 요청한 페이지 전부를 한 번에 렌더링해서 메모리에 PIL Image로
# 들고 있는다. A1/A0 같은 대형 도면을 150dpi로 렌더링하면 장당 30~50MB 이상이 될 수 있어,
# 79페이지짜리 프로젝트를 한꺼번에 렌더링하면 수 GB 메모리가 순식간에 필요해져 서버가
# 죽거나(MemoryError) 매우 느려질 수 있다. 어차피 image_to_jpeg_bytes()에서 최종적으로
# 1536px로 축소하므로, 150dpi로 뽑을 필요 없이 120dpi 정도로도 충분하다(메모리 약 36% 절감).
PDF_RENDER_DPI = 120


def pdf_to_images(pdf_bytes, max_pages=MAX_PDF_PAGES_TO_GEMINI, dpi=PDF_RENDER_DPI):
    """PDF 바이트를 PIL Image 리스트로 변환 (최대 max_pages 페이지).
    구조 부재 추출(extract_structural_members)은 이 함수를 쓰지 않고 배치 단위로 직접
    페이지 범위를 지정해서 렌더링한다(메모리 절감) — 아래 _render_pdf_page_range 참고."""
    images = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=1, last_page=max_pages)
    return images


def _render_pdf_page_range(pdf_bytes, first_page, last_page, dpi=PDF_RENDER_DPI):
    """PDF의 지정된 페이지 범위만 렌더링한다. 대형 PDF를 배치 단위로 나눠 필요한
    페이지만 그때그때 렌더링하고 곧바로 버려서, 전체 페이지를 한꺼번에 메모리에
    올려두지 않게 하기 위함이다."""
    return convert_from_bytes(pdf_bytes, dpi=dpi, first_page=first_page, last_page=last_page)


# ─────────────────────────────────────────────
#  일람표/구조일반사항 페이지 자동 감지
#  — 실제 프로젝트에서 확인된 문제: 보/계단 등의 "마크"는 평면도(N번째 배치)에 있는데,
#  그 마크의 실제 치수가 담긴 부재일람표는 도면집 앞쪽(다른 배치)에 몰려있는 경우가 많다.
#  배치는 서로 독립적으로 Gemini에 보내지므로, 평면도만 본 배치는 그 일람표 내용을
#  전혀 모른 채로 "마크만 확인, 치수 미확인"으로 남게 된다. 이걸 완화하기 위해
#  일람표/구조일반사항으로 보이는 페이지를 텍스트 키워드로 미리 찾아서, 모든 배치의
#  이미지에 함께 끼워 넣는다(자기 배치 범위와 겹치는 페이지는 중복이라 제외).
# ─────────────────────────────────────────────
_SCHEDULE_PAGE_KEYWORDS = [
    "일람표", "SCHEDULE", "구조일반사항", "GENERAL NOTE", "이음길이표", "정착길이표",
    "사용재료", "재료강도", "표준상세",
]
SCHEDULE_PAGE_MAX_CHECK = 40  # 일람표는 관례상 도면집 앞쪽에 몰려있어 앞부분만 훑는다
SCHEDULE_PAGE_MAX_RESULTS = 6  # 배치마다 끼워넣을 페이지 수를 제한해서 토큰 증가폭을 억제


def _detect_schedule_pages(pdf_bytes, total_pages,
                            max_check_pages=SCHEDULE_PAGE_MAX_CHECK,
                            max_results=SCHEDULE_PAGE_MAX_RESULTS):
    """pdftotext(poppler-utils)로 페이지별 텍스트를 뽑아 일람표/구조일반사항 키워드가
    많이 나오는 페이지를 찾는다. CAD에서 뽑은 PDF가 벡터 텍스트를 유지하고 있으면 잘
    동작하고, 완전히 래스터(이미지) PDF면 텍스트가 안 잡혀 후보가 0개로 나올 수 있다
    — 이 경우 예외 없이 그냥 빈 리스트를 반환한다(배치 주입 기능만 조용히 꺼짐,
    나머지 추출 파이프라인은 정상 동작).
    Returns: 페이지 번호 리스트(오름차순, 최대 max_results개, 키워드 히트 많은 순 우선)."""
    check_upto = min(total_pages, max_check_pages)
    if check_upto <= 0:
        return []
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name

        scored = []
        for page_num in range(1, check_upto + 1):
            try:
                result = subprocess.run(
                    ["pdftotext", "-f", str(page_num), "-l", str(page_num), tmp_path, "-"],
                    capture_output=True, timeout=10,
                )
                text = result.stdout.decode("utf-8", errors="ignore").upper()
            except Exception:
                continue
            hits = sum(1 for kw in _SCHEDULE_PAGE_KEYWORDS if kw.upper() in text)
            if hits > 0:
                scored.append((hits, page_num))

        scored.sort(key=lambda x: (-x[0], x[1]))
        return sorted(p for _, p in scored[:max_results])
    except Exception:
        return []
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def image_to_jpeg_bytes(img: Image.Image, max_size=(1536, 1536)) -> bytes:
    """PIL Image → JPEG 바이트 (Gemini Vision 입력용)"""
    img = img.convert("RGB")
    img.thumbnail(max_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


# ─────────────────────────────────────────────
#  구조: Gemini는 "부재 리스트만 읽기" (계산은 quantity_calc.py가 결정론적으로 수행)
# ─────────────────────────────────────────────
MEMBER_EXTRACTION_SYSTEM_PROMPT = """당신은 구조도면을 판독하는 전문가입니다.
도면(부재일람표, 배근도, 평면도, 구조일반사항)에서 보이는 부재의 치수와 철근 정보를
"있는 그대로" 읽어서 구조화된 데이터로만 추출하세요.

절대로 물량(부피, 중량, 면적)을 계산하지 마세요. 계산은 별도 시스템이 합니다.
당신의 역할은 도면에 적힌 치수/규격/개수를 정확히 옮겨 적는 것뿐입니다.

구조일반사항(구조설계개요) 도면이 있으면 반드시 아래 항목을 general_spec에 채워주세요:
- 레미콘(콘크리트) 설계기준강도 Fck (MPa) — 매우 중요: 부재 종류별로 값이 다르게 표기된 경우
  (예: "아파트 전부재(기초 제외) 30MPa", "기초/주차장 전부재 24MPa")에도 절대로 null로 남기지
  마세요. concrete_fck_mpa에는 가장 일반적인 대표값(보통 기초를 제외한 일반부재 값)을 반드시
  채우고, 추가로 general_spec.concrete_fck_table에 기초/기둥/보/슬래브/전단벽/계단 각각에 대해
  개별 행으로 풀어서 채우세요. 표에 "전부재" 처럼 여러 부재가 묶여 표기돼 있으면 해당하는
  개별 카테고리 각각에 같은 값으로 행을 만드세요. 예:
  [{"category":"기초","fck_mpa":24},{"category":"기둥","fck_mpa":30},{"category":"보","fck_mpa":30},
   {"category":"슬래브","fck_mpa":30},{"category":"전단벽","fck_mpa":30},{"category":"계단","fck_mpa":30}]
  모든 부재가 같은 Fck면 표 없이 concrete_fck_mpa 하나만 채워도 됩니다.
- 철근 강종 (SD400, SD500 등) — 매우 중요: 철근 지름별로 강종/항복강도(fy)가 다르게 표기된 경우
  (예: "D10 이하 SD500(fy=500)", "D16 이상 SD500S(fy=500, 내진용)")에도 절대로 null로 남기지
  마세요. rebar_grade에는 가장 일반적인 대표 강종을 반드시 채우고, 추가로
  general_spec.rebar_grade_table에 지름 상한 기준으로 행을 만드세요 (bar_size_max는 그 지름
  "이하"에 적용된다는 뜻이며 mm 숫자만 적으세요, 가장 큰 지름 구간은 bar_size_max를 999로
  채우세요). 예: [{"bar_size_max":10,"grade":"SD500","fy_mpa":500},
  {"bar_size_max":999,"grade":"SD500S","fy_mpa":500}]. 모든 지름이 같은 강종이면 표 없이
  rebar_grade 하나만 채워도 됩니다.
- 철근 이음등급 (A급/B급)
- 철근 이음길이표 (직경별 이음길이, m 단위) — 표가 있으면 반드시 그대로 옮겨 적으세요(lap_splice_table).
  표에 상부근/하부근이 따로 나뉘어 있으면 각 행에 position을 "상부" 또는 "하부"로 채우고,
  구분이 없으면 position은 생략하세요. 근사치를 만들어내지 말고, 표가 없으면 빈 배열로 두세요.
- 철근 정착길이표(정착장, 직경별 m 단위) — 표가 있으면 반드시 그대로 옮겨 적으세요(anchorage_table).
  표준갈고리 정착길이표라면 각 행에 "hook": true를 채우고, 직선철근 정착길이표라면 "hook": false로
  채우세요(생략 시 false로 간주). 근사치를 만들어내지 말고, 표가 없으면 빈 배열로 두세요.
- 피복두께(콘크리트 표면부터 철근까지의 최소 거리, mm 단위) — 부재별로 다르게 표기돼 있으면
  그 중 가장 얇은 값을 general_spec.cover_thickness_mm에 채우세요. 확인 안 되면 null로 두세요.
- 우마근(Chair Bar, 슬래브 상하 2단 배근을 지지하는 보조철근) 규격과 높이 — 구조일반사항이나
  슬래브 상세도에 "우마근 HD13" 같은 표기와 상하 철근 사이 높이(H, mm 또는 m)가 함께 있으면
  general_spec.chair_bar_size, general_spec.chair_bar_height_m에 채우세요. 표기가 없으면 (즉,
  슬래브가 단일 배근이거나 우마근 언급이 없으면) 둘 다 null로 두세요 — 절대 추정하지 마세요.

기초(foundations)는 기초일람표 또는 기초상세도에 저판 하부 배근(철근 규격, 간격)이 표기되어
있으면 rebar_size(예: "D16")와 rebar_spacing_m(예: 0.2)을 반드시 채우세요. 도면에서 확인이
안 되면 null로 두고 notes에 "기초 배근 정보 미확인"이라고 남기세요 — 절대 임의로 추정해서
채우지 마세요.

기초상세도에 기둥/벽체가 기초에 연결되는 도웰바(Dowel Bar, 기초 철근과 별도로 표기된 수직
연결철근)가 표기돼 있으면 dowel_bar_size, dowel_bar_count(가닥수)를 채우고, 표준갈고리로
정착되면 dowel_has_hook을 true로 채우세요. 도면에 표기가 없으면 null로 두고 임의로 추정하지
마세요 (도웰바는 기둥 주근을 연장한 것일 수도 있고 별도 철근일 수도 있으므로 반드시 도면
근거가 있을 때만 채우세요).

기둥/보/벽체/슬래브의 주철근(main_rebar 또는 rebar)에 대해, 정착 방식을 판단할 수 있으면
아래 두 필드를 채우세요 (판단 근거가 없으면 둘 다 null로 두세요):
- has_hook: 부재 단부(기둥 하부, 보/슬래브 단부, 벽체 하부)의 정착이 90°/180° 표준갈고리로
  표시돼 있으면 true, 직선 정착이면 false
- is_top_bar (보/슬래브만 해당): 그 철근이 부재 상부에 위치한 철근(부모멘트근, 상부근)이면
  true, 하부(정모멘트근, 하부근)이면 false. 기둥/벽체 수직근에는 이 필드를 채우지 마세요
  (해당 없음).

벽체(walls)는 평면도에서 벽체 단부(끝나는 지점)의 형태를 판단할 수 있으면 end_condition을
채우세요: 벽체가 다른 벽체와 만나지 않고 끝나면 "일자형", T자로 다른 벽체와 만나면 "T자형",
직각으로 꺾이는 모서리면 "모서리". 판단이 안 되면 null로 두세요 (양쪽 단부 조건이 다르면
더 보강량이 많은 쪽을 기준으로 채우세요).

슬래브(slabs)는 데크플레이트(DECK, 철제 영구 거푸집)를 사용하는 슬래브면 is_deck_slab을
true로 채우세요. 일반 합판/유로폼 거푸집을 쓰면 false, 확인이 안 되면 null로 두세요.

부재일람표(기둥일람표, 보일람표)가 있으면 그것을 최우선 근거로 삼으세요.
이번 요청에 여러 장의 도면 이미지가 함께 주어집니다 — 어떤 이미지는 평면도(부재 마크만
보임)이고, 어떤 이미지는 부재일람표/구조일반사항(마크별 치수·철근 정보가 표로 정리됨)일 수
있습니다. 평면도에서 어떤 부재 마크(예: "S2W1", "C1")를 봤는데 그 마크의 치수를 같은
이미지에서 확인할 수 없으면, 함부로 "정보 없음"으로 넘기지 말고 반드시 이번 요청에 함께
포함된 다른 이미지들(특히 일람표/구조일반사항으로 보이는 이미지)을 먼저 확인해서 그 마크와
일치하는 행이 있는지 찾아보세요. 다른 이미지에서 찾았으면 그 치수를 사용하고 note에 "N번째
이미지의 일람표에서 치수 확인"처럼 남기고, 그래도 못 찾았을 때만 null로 두고 "일람표 미확인"
이라고 남기세요.
배근도나 평면도에서만 확인되는 값은 근사치일 수 있음을 notes에 남기세요.
도면에서 확인할 수 없는 값은 null로 남기고 생략하지 마세요.
단위는 전부 미터(m) 기준으로 환산해서 넣으세요 (mm로 적혀 있으면 나눠서 변환).
철근 규격은 D10, D13, D16, D19, D22, D25, D29, D32, D35, D38 표기법으로 통일하세요.

각 부재 항목(기초/기둥/보/슬래브/전단벽/계단)에 대해, 그 부재가 실제로 배치되어 보이는
평면도/배치도 이미지 위의 위치를 bbox 필드에 채워주세요:
{"page": <그 부재가 보이는 이미지 위에 표시된 "[도면 N페이지]"의 N>, "box_2d": [ymin, xmin, ymax, xmax]}
box_2d는 그 페이지 이미지 기준 0~1000 정규화 좌표입니다(좌상단이 (0,0), 우하단이 (1000,1000),
순서는 [ymin, xmin, ymax, xmax]). 부재일람표나 구조일반사항처럼 "표"만 있고 실제 배치 위치가
안 보이는 이미지에는 bbox를 채우지 마세요 — 반드시 그 부재가 평면도/배치도 상에 실제로
그려진 그림을 기준으로 위치를 표시하세요. 같은 마크가 여러 위치에 반복되면(예: 기준층 기둥이
여러 개소) 그 중 하나만 대표로 표시하면 됩니다. 위치를 확신할 수 없으면 bbox 필드 자체를
생략하세요 — 대충 찍어서 채우지 마세요(잘못된 위치 표시보다는 빈 값이 낫습니다).

벽체(walls)와 슬래브(slabs)는 배근도/평면도에 표시된 개구부(문, 계단실, EV실, 설비 샤프트,
덕트 등 콘크리트가 뚫린 구멍)를 openings 배열에 반드시 채우세요. 개구부가 전혀 없으면
openings를 빈 배열([])로 두세요 — null이나 생략은 금지입니다 (계산 시스템이 "정보 없음"과
"개구부 없음"을 구분해서 과다산출 경고를 다르게 표시하기 때문입니다).
DWG 파싱 데이터의 layer_geometry에 있는 opening_area/opening_count는 같은 레이어의
폐곡선(콘크리트 외곽선) 안에 기하학적으로 뚫려 있는 폴리곤을 자동 인식한 값입니다.
이 값이 0보다 크면 실제 도면 이미지에서 해당 벽체/슬래브의 개구부 치수를 찾아 openings에
반영하고, 폭·높이를 읽을 수 없으면 note에 "치수 확인 필요"라고 남기세요.

기초/기둥/보/슬래브/벽체/계단 각 항목에는 zone(층/구역) 필드도 채우세요. 그 부재가 어느 층에
속하는지 도면 제목이나 표기(예: "지하1층 구조평면도", "1층 골조평면도", "기준층(2~15층)
평면도", "옥탑층 골조평면도")를 근거로 최대한 구체적인 "층" 단위로 적으세요:
- 지하층은 "지하1층", "지하2층"처럼 층수를 명시하세요.
- 지상층은 "1층", "2층"처럼 층수를 명시하세요.
- 여러 층이 완전히 동일한 평면(기준층)으로 표기돼 있으면 "기준층(2~15층)"처럼 범위로 묶어
  하나의 zone으로 적고, 그 범위에 포함된 실제 층수를 floor_repeat_count에 채우세요 (예:
  2~15층이 전부 동일하면 floor_repeat_count=14). 이때 count/rebar_count 등 개수 필드는
  "그 평면도 1개 층 기준"의 개수여야 합니다 — floor_repeat_count와 곱해져서 전체 층수만큼의
  물량이 계산되므로, 절대 직접 층수를 곱해서 채우지 마세요.
- 어느 층인지 도면에서 판단하기 어려우면(공용부/전체 개요 등) zone을 "미상"으로 적으세요.
- 반복이 없어 그 zone이 정확히 한 층만 가리키면 floor_repeat_count는 1로 두거나 생략하세요
  (생략 시 1로 간주합니다).
이 값들은 층별 물량 집계와 구역별 철근콘크리트비(철근 누락 여부 점검용) 계산에 모두 쓰입니다.

같은 항목에 section(구간/동) 필드도 채우세요 — 프로젝트가 여러 동/구간(예: "101동", "102동",
"지하주차장", "기전실", "정화조", "주민공동시설", "관리사무소", "경비실")으로 나뉘어 있으면
그 부재가 속한 동/구간명을 도면 제목이나 도면목록표(예: "101동 지하2층 구조평면도" → "101동")
에서 그대로 옮겨 적으세요. 프로젝트 전체가 단일 동/단일 건물이면 그 건물명(또는 "본동")을
적거나, 구분할 필요가 없다고 판단되면 생략해도 됩니다(생략 시 "미상"으로 처리됩니다). zone은
"그 동 안에서의 층"만 나타내고 section은 "어느 동인지"를 나타내므로 서로 다른 동의 같은
층수(예: 101동 1층과 102동 1층)가 섞이지 않도록 반드시 함께 채우세요.

계단(stairs)은 배근도/평면도/단면도에 표기된 경사길이(계단참 포함 전체 사판 길이, length_m),
폭(width_m), 계단판 두께(thickness_m)를 채우고, 경사방향 주근(rebar_size/rebar_spacing_m)과
폭방향 배력근(distribution_rebar_size/distribution_rebar_spacing_m)을 각각 채우세요.
계단은 별도 부재로, slabs 배열에 포함하지 말고 반드시 stairs 배열에 넣으세요.

반드시 아래와 같은 키를 가진 JSON 객체 하나만 반환하세요. 다른 텍스트는 절대 포함하지 마세요.
값을 모르면 null, 해당 부재가 없으면 빈 배열([])로 두세요. 예시(형식 참고용, 실제 값 아님):
{
  "foundations": [{"mark": "F1", "length_m": 2.0, "width_m": 2.0, "thickness_m": 0.6, "count": 4, "rebar_size": "D16", "rebar_spacing_m": 0.2, "dowel_bar_size": "D25", "dowel_bar_count": 8, "dowel_has_hook": false, "zone": "지하1층", "floor_repeat_count": 1, "section": "101동", "bbox": {"page": 5, "box_2d": [120, 200, 260, 340]}}],
  "columns": [{"mark": "C1", "width_m": 0.5, "depth_m": 0.5, "height_m": 3.2, "count": 12, "main_rebar_size": "D25", "main_rebar_count": 8, "tie_rebar_size": "D10", "tie_spacing_m": 0.2, "has_hook": false, "zone": "기준층(2~15층)", "floor_repeat_count": 14, "section": "101동", "bbox": {"page": 12, "box_2d": [300, 410, 380, 490]}}],
  "beams": [{"mark": "G1", "width_m": 0.4, "depth_m": 0.6, "length_m": 6.0, "count": 10, "main_rebar_size": "D22", "main_rebar_count": 6, "stirrup_size": "D10", "stirrup_spacing_m": 0.2, "has_hook": false, "is_top_bar": false, "zone": "기준층(2~15층)", "floor_repeat_count": 14, "section": "101동", "bbox": {"page": 12, "box_2d": [280, 300, 320, 600]}}],
  "slabs": [{"mark": "SL1", "area_m2": 120.0, "thickness_m": 0.15, "count": 1, "rebar_size": "D13", "rebar_spacing_m": 0.2, "has_hook": false, "is_top_bar": false, "is_deck_slab": false, "openings": [{"label": "계단실 개구부", "width_m": 2.4, "height_m": 4.0, "count": 1}], "zone": "기준층(2~15층)", "floor_repeat_count": 14, "section": "101동", "bbox": {"page": 12, "box_2d": [100, 100, 700, 700]}}],
  "walls": [{"mark": "W1", "length_m": 5.0, "height_m": 3.2, "thickness_m": 0.2, "count": 2, "rebar_size": "D13", "rebar_spacing_m": 0.2, "has_hook": false, "end_condition": "모서리", "openings": [{"label": "출입구", "width_m": 0.9, "height_m": 2.1, "count": 1}], "zone": "지하1층", "floor_repeat_count": 1, "section": "지하주차장", "bbox": {"page": 8, "box_2d": [400, 100, 900, 250]}}],
  "stairs": [{"mark": "ST1", "width_m": 1.2, "length_m": 4.5, "thickness_m": 0.15, "count": 2, "rebar_size": "D13", "rebar_spacing_m": 0.2, "distribution_rebar_size": "D10", "distribution_rebar_spacing_m": 0.3, "is_top_bar": false, "has_hook": false, "zone": "1층", "floor_repeat_count": 1, "section": "101동", "bbox": {"page": 3, "box_2d": [500, 600, 650, 750]}}],
  "notes": ["확인이 필요하거나 근사치인 항목에 대한 메모"],
  "general_spec": {"concrete_fck_mpa": 30, "rebar_grade": "SD500", "lap_splice_class": "B", "cover_thickness_mm": 40, "chair_bar_size": "D10", "chair_bar_height_m": 0.1, "concrete_fck_table": [{"category": "기초", "fck_mpa": 24}, {"category": "기둥", "fck_mpa": 30}, {"category": "보", "fck_mpa": 30}, {"category": "슬래브", "fck_mpa": 30}, {"category": "전단벽", "fck_mpa": 30}, {"category": "계단", "fck_mpa": 30}], "rebar_grade_table": [{"bar_size_max": 10, "grade": "SD500", "fy_mpa": 500}, {"bar_size_max": 999, "grade": "SD500S", "fy_mpa": 500}], "lap_splice_table": [{"bar_size": "D25", "length_m": 1.3, "position": "하부"}], "anchorage_table": [{"bar_size": "D25", "length_m": 1.0, "hook": false}]}
}"""

_EMPTY_MEMBERS = {
    "foundations": [], "columns": [], "beams": [], "slabs": [], "walls": [], "stairs": [],
    "notes": [], "general_spec": {},
}


# 대형 프로젝트(수십~백 페이지)를 한 번의 Gemini 호출에 몰아넣으면 부재가 많을수록
# 응답 JSON이 커져서 max_output_tokens(65536)를 넘어 잘리거나, 요청 자체가 타임아웃/오류로
# 실패해서 "전체 결과 0건"이 되는 문제가 실제로 발생했다. 그래서 페이지를 이 크기 단위로
# 나눠 순차 호출하고 결과를 합친다 — 배치 하나가 실패해도 나머지 배치의 데이터는 살아남는다.
# 15페이지였을 때 실제 대형 아파트 프로젝트(경성빌라 참고 사례)에서 배치 6개 중 4개가
# max_output_tokens에 걸려 응답이 중간에 잘리는 게 확인돼서 8로 낮췄다 — 배치당 예상
# 출력 토큰을 줄여 잘림 위험을 낮추는 대신, Gemini 호출 횟수(=비용)는 그만큼 늘어난다.
EXTRACTION_BATCH_PAGE_SIZE = 8


def _merge_extracted_members(batch_results: list) -> dict:
    """extract_structural_members()가 배치별로 얻은 결과 리스트를 하나로 합친다.
    - foundations/columns/beams/slabs/walls/stairs: 배열을 그대로 이어붙인다.
    - notes: 배치별 메모를 순서대로 이어붙인다(각 메모에 이미 배치 태그가 붙어 있음).
    - general_spec: 구조일반사항은 보통 도면 앞쪽 한 곳에만 있으므로, 스칼라 필드는
      처음 나온 non-null 값을, 표(테이블) 필드는 처음 나온 비어있지 않은 표를 채택한다
      (여러 배치에 같은 정보가 중복 추출돼도 뒤에 나온 값으로 덮어쓰지 않는다)."""
    merged = {
        "foundations": [], "columns": [], "beams": [], "slabs": [], "walls": [], "stairs": [],
        "notes": [], "general_spec": {},
    }
    scalar_fields = [
        "concrete_fck_mpa", "rebar_grade", "lap_splice_class", "cover_thickness_mm",
        "chair_bar_size", "chair_bar_height_m",
    ]
    table_fields = ["concrete_fck_table", "rebar_grade_table", "lap_splice_table", "anchorage_table"]

    for data in batch_results:
        for key in ("foundations", "columns", "beams", "slabs", "walls", "stairs"):
            merged[key].extend(data.get(key) or [])
        merged["notes"].extend(data.get("notes") or [])

        gs = data.get("general_spec") or {}
        for field in scalar_fields:
            if merged["general_spec"].get(field) in (None, "") and gs.get(field) not in (None, ""):
                merged["general_spec"][field] = gs[field]
        for field in table_fields:
            if not merged["general_spec"].get(field) and gs.get(field):
                merged["general_spec"][field] = gs[field]

    return merged


def _extract_structural_members_one_batch(client, dwg_data, image_batch, batch_idx, total_batches, page_numbers=None):
    """extract_structural_members()의 배치 1개를 처리한다. 실패해도 예외를 던지지 않고
    _EMPTY_MEMBERS(+오류 메모)를 반환해서, 이 배치가 실패해도 다른 배치는 계속 처리되게 한다.

    page_numbers: image_batch와 같은 길이의 리스트로, 각 이미지가 실제 PDF의 몇 페이지인지
    알려준다. 반드시 넘겨야 한다 — 안 넘기면(None) 이미지 순번(1,2,3...)을 페이지 번호처럼
    라벨링하게 되는데, 이 배치가 예를 들어 16~30페이지 범위라면 실제로는 16페이지인 이미지가
    "1페이지"로 잘못 표시되는 문제가 있었다(과거 버그). 부재 위치(bbox.page)를 실제 페이지
    번호와 연결해서 도면에 색칠 미리보기를 그리려면 이 번호가 정확해야 한다."""
    is_multi_batch = total_batches > 1
    batch_tag = f"[배치 {batch_idx}/{total_batches}] " if is_multi_batch else ""

    def _tag_notes(note_list):
        return [f"{batch_tag}{n}" for n in note_list] if is_multi_batch else list(note_list)

    if page_numbers is None:
        # 하위 호환: 실제 페이지 번호를 모르면 순번으로라도 라벨링한다(이전 동작과 동일).
        page_numbers = list(range(1, len(image_batch) + 1))

    contents = [
        f"[구조도면 DWG 파싱 데이터]\n"
        f"{json.dumps(dwg_data, ensure_ascii=False, indent=2)[:60000]}\n\n"
        "위 데이터와 아래 도면 이미지를 보고, 부재일람표/배근도에 적힌 치수와 철근 정보를 "
        "그대로 읽어서 스키마에 맞게 추출해주세요. 계산하지 말고 읽기만 하세요."
        + (
            f"\n\n※ 이 요청은 전체 도면 중 {batch_idx}/{total_batches}번째 배치입니다. "
            "이 배치에 포함된 이미지에서 실제로 보이는 부재만 추출하세요 — 다른 배치에 있을 "
            "부재를 추측해서 채우거나 이 배치에 없는 부재를 생략하지 마세요."
            if is_multi_batch else ""
        )
    ]
    for i, img in enumerate(image_batch):
        page_label = page_numbers[i] if i < len(page_numbers) else (i + 1)
        contents.append(f"[도면 {page_label}페이지]")
        contents.append(types.Part.from_bytes(data=image_to_jpeg_bytes(img), mime_type="image/jpeg"))

    try:
        response = client.models.generate_content(
            model=GEMINI_QUANTITY_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=MEMBER_EXTRACTION_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.0,
                max_output_tokens=65536,
            ),
        )
    except Exception as e:
        return dict(_EMPTY_MEMBERS, notes=_tag_notes([f"Gemini 부재 추출 요청 중 오류가 발생했습니다: {str(e)[:200]}"]))

    raw = _extract_text_from_gemini_response(response)

    try:
        raw_clean = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw_clean)
    except json.JSONDecodeError:
        data = _try_repair_truncated_json(raw.replace("```json", "").replace("```", "").strip())
        if data is None:
            return dict(_EMPTY_MEMBERS, notes=_tag_notes([f"JSON 파싱 오류 - 원본 응답 확인 필요: {raw[:300]}"]))
        data.setdefault("notes", [])
        data["notes"].append(
            "Gemini 응답이 도중에 잘려(max_output_tokens 한도) 마지막 일부 부재는 누락됐을 수 있습니다 "
            "— 부분 복구된 데이터입니다. 개수가 적어 보이면 도면을 더 잘게 나눠서 재실행해 주세요."
        )

    # 응답에 일부 키가 빠져도 계산 엔진이 죽지 않도록 방어
    for key in ("foundations", "columns", "beams", "slabs", "walls", "stairs", "notes"):
        data.setdefault(key, [])
    data.setdefault("general_spec", {})

    data["notes"] = _tag_notes(data.get("notes") or [])
    return data


def extract_structural_members(dwg_data: dict, pdf_bytes, progress_cb=None, cancel_cb=None) -> dict:
    """
    DWG 파싱 데이터 + 구조도면 PDF(원본 바이트) 를 Gemini Vision에 보내
    '부재 리스트만' 구조화된 JSON으로 추출한다 (계산은 하지 않음).
    실제 물량 계산은 quantity_calc.compute_structural_quantities가 결정론적으로 수행한다.

    두 가지 방식으로 대형 프로젝트(수십~백 페이지)에서의 실패를 막는다:
    1) Gemini 호출을 EXTRACTION_BATCH_PAGE_SIZE 페이지 단위로 나눠 순차 호출 후 병합
       — 한 번에 몰아넣으면 응답이 max_output_tokens를 넘어 잘리거나 요청이 타임아웃/
       오류로 실패해서 전체 결과가 0건이 되는 문제를 막는다.
    2) PDF 페이지도 한꺼번에 다 렌더링하지 않고 배치 단위로 그때그때만 렌더링한다
       — 79페이지 같은 대형 도면집을 한 번에 전부 이미지로 뽑으면(pdf2image가 전체를
       메모리에 올림) 수 GB 메모리가 필요해져 서버가 죽거나(MemoryError) 매우 느려질
       수 있기 때문이다.
    배치 1개(렌더링이든 Gemini 호출이든)가 실패해도 다른 배치의 데이터는 그대로 살아남는다.

    progress_cb(idx, total_batches)가 주어지면, 각 배치를 처리하기 직전에 호출해서
    "지금 몇 배치째"인지 외부(예: 진행률 폴링 저장소)에 알릴 수 있게 한다. 필수 아님 —
    None이면 그냥 진행률 보고를 생략한다(기존 호출부와 호환).

    cancel_cb()가 주어지고 True를 반환하면, 다음 배치를 아예 Gemini에 보내지 않고
    루프를 즉시 멈춘다(이미 시작된 배치 1개는 어차피 끝까지 처리— Gemini 호출 자체를
    중간에 끊을 방법은 없다). 사용자가 "취소"를 누른 뒤 아직 시작 안 한 배치들의
    비용을 아끼기 위한 용도다. None이면 취소 체크를 생략한다.
    """
    client = get_gemini_client()
    if client is None:
        return dict(_EMPTY_MEMBERS, notes=["GEMINI_API_KEY가 설정되지 않았습니다. .env 확인 후 서버를 재시작해 주세요."])

    if not pdf_bytes:
        # PDF가 없고 DWG 데이터만 있는 경우(구조 ZIP만 업로드) — 배치 없이 한 번만 호출
        if progress_cb:
            progress_cb(1, 1)
        return _merge_extracted_members(
            [_extract_structural_members_one_batch(client, dwg_data, [], 1, 1)]
        )

    try:
        info = pdfinfo_from_bytes(pdf_bytes)
        total_pages = min(int(info.get("Pages", 0) or 0), MAX_PDF_PAGES_TO_GEMINI)
    except Exception as e:
        return dict(_EMPTY_MEMBERS, notes=[f"구조도면 PDF 페이지 수 확인 중 오류가 발생했습니다: {str(e)[:200]}"])

    if total_pages <= 0:
        return dict(_EMPTY_MEMBERS, notes=["구조도면 PDF에서 읽을 수 있는 페이지가 없습니다 — 파일이 비어있거나 손상됐을 수 있습니다."])

    page_ranges = [
        (start, min(start + EXTRACTION_BATCH_PAGE_SIZE - 1, total_pages))
        for start in range(1, total_pages + 1, EXTRACTION_BATCH_PAGE_SIZE)
    ]
    total_batches = len(page_ranges)

    # 일람표/구조일반사항으로 보이는 페이지를 미리 찾아서, 각 배치 이미지에 함께 끼워
    # 넣는다 — 여러 배치로 나눠 처리할 때만 의미가 있다(배치가 1개면 어차피 전체를 다
    # 보내므로 불필요). 페이지 텍스트가 안 뽑히는 PDF면 schedule_pages가 그냥 빈 리스트로
    # 나오고, 이 기능만 조용히 꺼진다(에러 아님).
    schedule_pages = []
    schedule_page_images = {}
    if total_batches > 1:
        try:
            schedule_pages = _detect_schedule_pages(pdf_bytes, total_pages)
        except Exception:
            schedule_pages = []
        for p in schedule_pages:
            try:
                imgs = _render_pdf_page_range(pdf_bytes, p, p)
                if imgs:
                    schedule_page_images[p] = imgs[0]
            except Exception:
                continue
        # 렌더링까지 실제로 성공한 페이지만 남긴다(감지는 됐는데 렌더링이 실패한 페이지 제외)
        schedule_pages = [p for p in schedule_pages if p in schedule_page_images]

    batch_results = []
    schedule_note_added = False
    for idx, (first_page, last_page) in enumerate(page_ranges, 1):
        if cancel_cb and cancel_cb():
            batch_results.append(dict(
                _EMPTY_MEMBERS,
                notes=[f"사용자가 취소를 요청하여 배치 {idx}/{total_batches}부터 남은 페이지({first_page}~{total_pages}페이지)는 처리하지 않았습니다."],
            ))
            break
        if progress_cb:
            progress_cb(idx, total_batches)
        try:
            image_batch = _render_pdf_page_range(pdf_bytes, first_page, last_page)
        except Exception as e:
            tag = f"[배치 {idx}/{total_batches}] " if total_batches > 1 else ""
            batch_results.append(dict(
                _EMPTY_MEMBERS,
                notes=[f"{tag}PDF {first_page}~{last_page}페이지 렌더링 중 오류가 발생했습니다: {str(e)[:200]}"],
            ))
            continue

        # 이 배치 자체 범위와 겹치지 않는 일람표 페이지만 앞에 붙인다(겹치면 이미 image_batch
        # 안에 들어있으니 중복 전송할 필요 없음). 이렇게 하면 평면도만 보이는 배치도 같은
        # 요청 안에서 부재 마크의 실제 치수(일람표)를 함께 보고 매칭할 수 있다.
        extra_pairs = [
            (p, img) for p, img in schedule_page_images.items()
            if not (first_page <= p <= last_page)
        ]
        extra_images = [img for _, img in extra_pairs]
        combined_batch = extra_images + image_batch
        # image_batch의 각 이미지는 first_page..last_page 순서 그대로이므로, 실제 페이지
        # 번호를 그 순서에 맞춰 만들어 붙인다 — 이래야 Gemini에게 보여주는 "[도면 N페이지]"
        # 라벨과 부재 위치(bbox.page)가 실제 PDF 페이지 번호와 정확히 일치한다.
        combined_page_numbers = [p for p, _ in extra_pairs] + list(range(first_page, last_page + 1))

        one_result = _extract_structural_members_one_batch(
            client, dwg_data, combined_batch, idx, total_batches, page_numbers=combined_page_numbers
        )
        if extra_images and not schedule_note_added:
            schedule_note_added = True
            one_result = dict(one_result)
            one_result["notes"] = list(one_result.get("notes") or []) + [
                f"일람표/구조일반사항으로 추정되는 페이지({', '.join(str(p) for p in schedule_pages)})를 "
                "모든 배치 이미지에 함께 포함해서, 평면도만 보이는 배치에서도 부재 마크의 치수를 "
                "찾을 수 있도록 했습니다."
            ]
        batch_results.append(one_result)
        del image_batch  # 다음 배치로 넘어가기 전에 명시적으로 메모리 해제

    return _merge_extracted_members(batch_results)


# ─────────────────────────────────────────────
#  색칠된 도면 미리보기: Gemini가 채운 bbox(부재 위치)를 원본 도면 페이지 위에
#  부재종류별 색상 박스로 그려서, 의뢰인이 채팅창에서 "빠진 벽/보가 있는지" 눈으로
#  직접 확인할 수 있게 한다. bbox가 없는 항목/페이지는 조용히 건너뛴다(이 기능이
#  실패하거나 데이터가 없어도 나머지 수량산출 결과에는 영향이 없어야 한다).
# ─────────────────────────────────────────────
_CATEGORY_KEY_TO_LABEL = {
    "foundations": "기초", "columns": "기둥", "beams": "보",
    "slabs": "슬래브", "walls": "전단벽", "stairs": "계단",
}
# 이미지 위에 직접 그려 넣는 라벨은 일부러 영문 약어를 쓴다 — PIL의 기본 폰트는 한글
# 글리프가 없어서(운영 서버에 한글 지원 폰트가 없을 수도 있음), 한글로 그리면 네모(tofu)로
# 깨져 보인다. 한글 부재종류 이름은 categories 필드(HTML/채팅창 텍스트)로만 노출하고,
# 픽셀에 직접 굽는 라벨은 폰트 의존성 없는 영문 약어로 통일한다.
_CATEGORY_SHORT_LABEL = {
    "기초": "FTG", "기둥": "COL", "보": "BEAM",
    "슬래브": "SLAB", "전단벽": "WALL", "계단": "STAIR",
}
_CATEGORY_BBOX_COLOR = {
    "기초": (230, 126, 34),   # 주황
    "기둥": (231, 76, 60),    # 빨강
    "보": (52, 152, 219),     # 파랑
    "슬래브": (46, 204, 113), # 초록
    "전단벽": (155, 89, 182), # 보라
    "계단": (241, 196, 15),   # 노랑
}
ANNOTATED_PAGE_MAX_PAGES = 12  # 채팅창에 한 번에 띄울 색칠 페이지 수 상한(응답 크기 제한용)


def _draw_member_boxes_on_image(img: Image.Image, boxes):
    """boxes: [(category_label_kr, mark, box_2d[ymin,xmin,ymax,xmax](0~1000 정규화), color_rgb), ...]
    반투명 채우기 + 테두리 + 라벨 텍스트를 원본 이미지 위에 겹쳐 그린 새 이미지를 반환한다
    (원본은 건드리지 않음). RGBA 오버레이를 따로 그려서 알파 합성해야 실제로 반투명하게
    보인다 — RGB 이미지에 바로 그리면 PIL이 알파를 무시하고 불투명하게 그려버린다.
    라벨 텍스트는 category_label_kr을 영문 약어(_CATEGORY_SHORT_LABEL)로 바꿔서 그린다
    (한글 폰트 미설치 서버에서도 깨지지 않도록)."""
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = base.size
    for category_label, mark, box_2d, color in boxes:
        try:
            ymin, xmin, ymax, xmax = (float(v) for v in box_2d)
        except (TypeError, ValueError):
            continue
        x0, y0 = xmin / 1000.0 * w, ymin / 1000.0 * h
        x1, y1 = xmax / 1000.0 * w, ymax / 1000.0 * h
        if x1 <= x0 or y1 <= y0:
            continue
        draw.rectangle([x0, y0, x1, y1], outline=color + (255,), width=3, fill=color + (55,))
        short_label = _CATEGORY_SHORT_LABEL.get(category_label, category_label)
        label = f"{short_label} {mark}".strip() if mark else short_label
        text_y = max(0, y0 - 14)
        draw.rectangle([x0, text_y, x0 + 7 * len(label) + 4, text_y + 13], fill=color + (200,))
        draw.text((x0 + 2, text_y), label, fill=(255, 255, 255, 255))
    return Image.alpha_composite(base, overlay).convert("RGB")


def build_annotated_pages(pdf_bytes, members: dict, max_pages=ANNOTATED_PAGE_MAX_PAGES):
    """추출된 members(foundations/columns/beams/slabs/walls/stairs)의 bbox 필드를 모아
    페이지별로 묶고, 그 페이지의 원본 이미지를 다시 렌더링해서 부재종류별 색상 박스를
    그린 뒤 base64 JPEG로 인코딩한다.
    Returns: [{"page": int, "image_data_url": str, "categories": [...], "member_count": int}, ...]
    (페이지 번호 오름차순). pdf_bytes가 없거나 bbox 데이터가 하나도 없으면 빈 리스트를
    반환한다 — 이 기능은 있으면 좋은 보조 기능이라, 실패해도 예외를 던지지 않고 조용히
    빈 리스트로 넘어간다(호출부에서 굳이 try/except로 감싸지 않아도 되게)."""
    if not pdf_bytes or not isinstance(members, dict):
        return []

    page_boxes = {}
    for key, label in _CATEGORY_KEY_TO_LABEL.items():
        color = _CATEGORY_BBOX_COLOR[label]
        for it in (members.get(key) or []):
            if not isinstance(it, dict):
                continue
            bbox = it.get("bbox")
            if not isinstance(bbox, dict):
                continue
            page = bbox.get("page")
            box_2d = bbox.get("box_2d")
            if not isinstance(page, (int, float)) or not (isinstance(box_2d, list) and len(box_2d) == 4):
                continue
            try:
                page = int(page)
            except (TypeError, ValueError):
                continue
            mark = it.get("mark") or ""
            page_boxes.setdefault(page, []).append((label, mark, box_2d, color))

    if not page_boxes:
        return []

    annotated = []
    for page in sorted(page_boxes.keys())[:max_pages]:
        try:
            imgs = _render_pdf_page_range(pdf_bytes, page, page)
            if not imgs:
                continue
            annotated_img = _draw_member_boxes_on_image(imgs[0], page_boxes[page])
            jpeg_bytes = image_to_jpeg_bytes(annotated_img, max_size=(1400, 1400))
            data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")
            annotated.append({
                "page": page,
                "image_data_url": data_url,
                "categories": sorted({b[0] for b in page_boxes[page]}),
                "member_count": len(page_boxes[page]),
            })
        except Exception:
            continue

    return annotated


# ─────────────────────────────────────────────
#  건축 입면도/단면도 검토: 층고 + 개구부(창호) 목록 읽기
#  (계산에 쓰이는 게 아니라, 구조 부재 데이터의 층고 누락을 메꾸고
#   개구부 정보의 정합성을 대조하기 위한 참고 데이터)
# ─────────────────────────────────────────────
ELEVATION_SECTION_SYSTEM_PROMPT = """당신은 건축 입면도/단면도를 판독하는 전문가입니다.
입면도(정면/배면/측면)와 단면도에서 아래 두 가지만 "있는 그대로" 읽어서 추출하세요.
계산하지 말고, 도면에 적힌 값을 옮겨 적기만 하세요.

1) 층고(floor_heights): 각 층의 바닥~바닥(FL~FL) 또는 바닥~천장 높이.
   단면도에 GL(지반), 1FL, 2FL... 식으로 레벨이 표기돼 있으면 그 차이로 계산해서 읽어주세요.
   같은 층고가 반복되면 "기준층" 등으로 대표해서 하나만 적고, repeat_count에 몇 개 층이
   그 층고로 반복되는지 적어주세요 (예: 2~15층이 전부 2.9m면 level="기준층(2~15층)",
   height_m=2.9, repeat_count=14). 반복 여부를 모르면 repeat_count=1로 두세요.

2) 개구부(openings): 입면도에 보이는 창호/출입구 등 벽면 개구부 목록.
   위치(어느 입면, 몇 층인지)와 폭/높이, 같은 크기가 반복되면 개수(count)로 묶어서 적으세요.
   정확한 치수를 알 수 없으면 note에 "치수 확인 필요"라고 남기고 width_m/height_m은 null로 두세요.

단위는 전부 미터(m) 기준으로 환산해서 넣으세요 (mm로 적혀 있으면 나눠서 변환).
도면에서 확인할 수 없는 값은 빈 배열로 두고 억지로 만들어내지 마세요.

반드시 아래와 같은 키를 가진 JSON 객체 하나만 반환하세요. 다른 텍스트는 절대 포함하지 마세요.
예시(형식 참고용, 실제 값 아님):
{
  "floor_heights": [{"level": "1층", "height_m": 3.6, "repeat_count": 1, "note": "GL~1FL"}, {"level": "기준층(2~15층)", "height_m": 2.9, "repeat_count": 14, "note": ""}],
  "openings": [{"location": "정면 2~15층 반복", "width_m": 1.5, "height_m": 1.8, "count": 42, "note": "거실 창호"}],
  "notes": ["확인이 필요하거나 근사치인 항목에 대한 메모"]
}"""

_EMPTY_ELEVATION_SECTION = {"floor_heights": [], "openings": [], "notes": []}


def extract_elevation_section_data(dwg_data: dict, pdf_images: list) -> dict:
    """
    건축 입면도/단면도(DWG 파싱 데이터 + PDF 이미지)에서 층고와 개구부(창호) 목록만
    구조화된 JSON으로 추출한다. 물량 계산에는 관여하지 않고, quantity_calc.py가
    구조 부재 데이터의 층고 누락 보완 / 개구부 정합성 대조용 참고자료로만 사용한다.
    """
    client = get_gemini_client()
    if client is None:
        return dict(_EMPTY_ELEVATION_SECTION, notes=["GEMINI_API_KEY가 설정되지 않았습니다. .env 확인 후 서버를 재시작해 주세요."])

    if not pdf_images:
        return dict(_EMPTY_ELEVATION_SECTION)

    contents = [
        f"[건축 입면/단면 DWG 파싱 데이터]\n"
        f"{json.dumps(dwg_data, ensure_ascii=False, indent=2)[:60000]}\n\n"
        "위 데이터와 아래 입면도/단면도 이미지를 보고 층고와 개구부 목록을 읽어서 추출해주세요."
    ]

    for i, img in enumerate(pdf_images[:MAX_PDF_PAGES_TO_GEMINI]):
        contents.append(f"[도면 {i + 1}페이지]")
        contents.append(types.Part.from_bytes(data=image_to_jpeg_bytes(img), mime_type="image/jpeg"))

    try:
        response = client.models.generate_content(
            model=GEMINI_QUANTITY_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=ELEVATION_SECTION_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.0,
                max_output_tokens=32768,
            ),
        )
    except Exception as e:
        return dict(_EMPTY_ELEVATION_SECTION, notes=[f"Gemini 입면/단면 판독 요청 중 오류가 발생했습니다: {str(e)[:200]}"])

    raw = _extract_text_from_gemini_response(response)

    try:
        raw_clean = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw_clean)
    except json.JSONDecodeError:
        data = _try_repair_truncated_json(raw.replace("```json", "").replace("```", "").strip())
        if data is None:
            return dict(_EMPTY_ELEVATION_SECTION, notes=[f"JSON 파싱 오류 - 원본 응답 확인 필요: {raw[:300]}"])
        data.setdefault("notes", [])
        data["notes"].append(
            "Gemini 응답이 도중에 잘려(max_output_tokens 한도) 마지막 일부 항목은 누락됐을 수 있습니다 "
            "— 부분 복구된 데이터입니다."
        )

    for key in ("floor_heights", "openings", "notes"):
        data.setdefault(key, [])
    return data


ARCHITECTURAL_SYSTEM_PROMPT = """당신은 건설 건축 수량산출 전문가입니다.
건축도면(PDF 이미지)과 DWG 파싱 데이터를 분석하여 수량을 산출합니다.

반드시 아래 JSON 형식으로만 응답하세요:
{
  "items": [
    {
      "category": "공종 대분류 (예: 조적, 미장, 타일, 창호, 도장, 내장재)",
      "sub_category": "세부 항목",
      "quantity": 숫자,
      "unit": "단위",
      "confidence": "high/medium/low",
      "note": "산출 근거"
    }
  ],
  "summary": "전체 산출 요약",
  "warnings": ["주의 사항"],
  "missing_info": ["확인 불가 항목"]
}"""


def analyze_with_gemini(dwg_data: dict, pdf_images: list, system_prompt: str, drawing_type: str) -> dict:
    """
    DWG 파싱 데이터 + PDF 이미지를 Gemini Vision으로 분석
    Returns: 수량산출 결과 dict
    """
    client = get_gemini_client()
    if client is None:
        return {
            "items": [],
            "summary": "GEMINI_API_KEY가 설정되지 않았습니다.",
            "warnings": [".env 파일에 GEMINI_API_KEY를 추가한 뒤 서버를 재시작해 주세요."],
            "missing_info": [],
        }

    # contents 구성: 파싱 데이터 텍스트 + 도면 이미지(최대 MAX_PDF_PAGES_TO_GEMINI 페이지)
    contents = [
        f"[{drawing_type} DWG 파싱 데이터]\n"
        f"{json.dumps(dwg_data, ensure_ascii=False, indent=2)[:60000]}\n\n"
        "위 데이터의 layer_geometry(레이어별 도형 개수/길이/닫힌면적), block_counts(블록 삽입 개수), "
        "texts(도면 내 문자)를 실제 수량 산출의 근거로 적극 활용하고, 아래 도면 이미지와 교차 검증해 주세요."
    ]

    for i, img in enumerate(pdf_images[:MAX_PDF_PAGES_TO_GEMINI]):
        contents.append(f"[도면 {i + 1}페이지]")
        contents.append(types.Part.from_bytes(data=image_to_jpeg_bytes(img), mime_type="image/jpeg"))

    try:
        response = client.models.generate_content(
            model=GEMINI_QUANTITY_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=32768,
            ),
        )
    except Exception as e:
        return {
            "items": [],
            "summary": f"Gemini 분석 요청 중 오류가 발생했습니다: {str(e)[:200]}",
            "warnings": [],
            "missing_info": [],
        }

    raw = _extract_text_from_gemini_response(response)

    # JSON 파싱
    try:
        raw_clean = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw_clean)
    except json.JSONDecodeError:
        repaired = _try_repair_truncated_json(raw.replace("```json", "").replace("```", "").strip())
        if repaired is not None:
            repaired.setdefault("items", [])
            repaired.setdefault("warnings", [])
            repaired["warnings"].append(
                "Gemini 응답이 도중에 잘려(max_output_tokens 한도) 마지막 일부 항목은 누락됐을 수 있습니다 "
                "— 부분 복구된 데이터입니다."
            )
            repaired.setdefault("summary", "일부 데이터가 잘려 복구된 결과입니다.")
            repaired.setdefault("missing_info", [])
            return repaired
        return {
            "items": [],
            "summary": "JSON 파싱 오류 - 원본 응답 확인 필요",
            "warnings": [raw[:500]],
            "missing_info": [],
        }


# ─────────────────────────────────────────────
#  API: 수량산출 실행 (메인 엔드포인트)
# ─────────────────────────────────────────────
@require_POST
@_admin_only_json
def api_run_quantity(request):
    """
    파일 4개 받아서 수량산출 실행
    - structural_zip: 구조도면 ZIP (DWG)
    - structural_pdf: 구조도면 합본 PDF
    - architectural_zip: 건축도면 ZIP (DWG)
    - architectural_pdf: 건축도면 합본 PDF
    """
    structural_zip = request.FILES.get("structural_zip")
    structural_pdf = request.FILES.get("structural_pdf")
    architectural_zip = request.FILES.get("architectural_zip")
    architectural_pdf = request.FILES.get("architectural_pdf")

    if not any([structural_zip, structural_pdf, architectural_zip, architectural_pdf]):
        return JsonResponse({"error": "파일이 없습니다."}, status=400)

    # ── 진행률 표시용: job_id는 프론트엔드가 업로드와 함께 보내는 임의 문자열이다.
    #    없으면(구버전 클라이언트 등) 아래 _progress_set 호출들은 전부 조용히 무시된다.
    job_id = request.POST.get("job_id") or None
    progress_stages = []
    if architectural_zip or architectural_pdf:
        progress_stages += ["architectural", "elevation"]
    if structural_zip or structural_pdf:
        progress_stages.append("structural")
    progress_total_stages = len(progress_stages) or 1

    def _report_progress(stage, current, total, label):
        try:
            stage_idx = progress_stages.index(stage) + 1
        except ValueError:
            stage_idx = 1
        _progress_set(job_id, stage, current, total, label,
                       stage_index=stage_idx, total_stages=progress_total_stages)

    def _cancelled():
        return _is_cancelled(job_id)

    results = {}
    elevation_data = None
    cancelled_note_added = False

    def _note_cancelled_once():
        nonlocal cancelled_note_added
        if not cancelled_note_added:
            cancelled_note_added = True
            return "사용자가 취소를 요청하여 이후 단계는 처리하지 않았습니다 — 여기까지 처리된 결과만 표시합니다."
        return None

    try:
        # ── 건축 수량산출 + 입면도/단면도 검토 (구조 계산보다 먼저 실행 —
        #    여기서 읽은 층고/개구부를 구조 계산의 층고 대체값/개구부 대조에 사용) ──
        if (architectural_zip or architectural_pdf) and _cancelled():
            results["architectural"] = {"items": [], "missing_info": [_note_cancelled_once() or "사용자가 취소를 요청했습니다."], "warnings": []}
        elif architectural_zip or architectural_pdf:
            arch_dwg_data = {}
            arch_pdf_images = []

            if architectural_zip:
                zip_bytes = architectural_zip.read()
                arch_dwg_data = parse_dwg_from_zip(zip_bytes, list(REQUIRED_ARCHITECTURAL.keys()))

            if architectural_pdf:
                pdf_bytes = architectural_pdf.read()
                try:
                    arch_pdf_images = pdf_to_images(pdf_bytes, max_pages=MAX_PDF_PAGES_TO_GEMINI)
                except Exception as e:
                    arch_pdf_images = []
                    results["architectural_pdf_error"] = str(e)

            if arch_dwg_data or arch_pdf_images:
                _report_progress("architectural", 0, 1, "건축도면 분석 중")
                try:
                    results["architectural"] = analyze_with_gemini(
                        arch_dwg_data, arch_pdf_images,
                        ARCHITECTURAL_SYSTEM_PROMPT, "건축도면"
                    )
                except Exception as e:
                    results["architectural"] = {"error": str(e), "items": [], "missing_info": [f"건축 수량산출 처리 중 오류가 발생했습니다: {str(e)[:300]}"], "warnings": []}
                _report_progress("architectural", 1, 1, "건축도면 분석 완료")

                if _cancelled():
                    elevation_data = dict(_EMPTY_ELEVATION_SECTION, notes=[_note_cancelled_once() or "사용자가 취소를 요청했습니다."])
                else:
                    # 입면도/단면도 검토: 층고 + 개구부(창호) 목록만 별도로 읽음
                    # (건축 수량산출과 같은 파싱 데이터/이미지를 재사용해 중복 변환을 피한다)
                    _report_progress("elevation", 0, 1, "입면/단면 검토 중")
                    try:
                        elevation_data = extract_elevation_section_data(arch_dwg_data, arch_pdf_images)
                    except Exception as e:
                        elevation_data = dict(_EMPTY_ELEVATION_SECTION, notes=[f"입면/단면 검토 중 오류: {str(e)[:200]}"])
                    _report_progress("elevation", 1, 1, "입면/단면 검토 완료")
            elif architectural_zip or architectural_pdf:
                # 파일은 올렸지만 arch_dwg_data/arch_pdf_images 둘 다 비어서(PDF 변환 실패 등)
                # 분석을 아예 시작도 못한 경우 — 이 사유를 architectural.missing_info에 남겨서
                # 프론트엔드가 결과가 0건일 때 "왜"인지 보여줄 수 있게 한다 (그냥 조용히
                # results["architectural"] 키 자체가 안 생기면 원인을 알 방법이 없어진다).
                pdf_err = results.get("architectural_pdf_error")
                reason = (
                    f"건축도면 PDF를 이미지로 변환하는 중 오류가 발생했습니다: {pdf_err}"
                    if pdf_err else
                    "건축도면 파일에서 읽을 수 있는 데이터가 없습니다 — ZIP/PDF가 비어있거나 손상됐을 수 있습니다."
                )
                results["architectural"] = {"items": [], "missing_info": [reason], "warnings": []}

        # ── 구조 수량산출 ──
        if structural_zip or structural_pdf:
            dwg_data = {}
            structural_pdf_bytes = None

            if structural_zip:
                zip_bytes = structural_zip.read()
                dwg_data = parse_dwg_from_zip(zip_bytes, list(REQUIRED_STRUCTURAL.keys()))

            if structural_pdf:
                # PDF를 여기서 미리 전부 이미지로 렌더링하지 않는다 — 대형 도면집(수십~백 페이지)을
                # 한꺼번에 렌더링하면 메모리를 너무 많이 써서 서버가 죽을 수 있다. 원본 바이트만
                # 넘기고, extract_structural_members()가 배치 단위로 필요한 페이지만 그때그때
                # 렌더링한다(page 수 확인/렌더링 실패도 그 안에서 배치별로 안전하게 처리됨).
                structural_pdf_bytes = structural_pdf.read()

            if _cancelled() and (dwg_data or structural_pdf_bytes):
                results["structural"] = {
                    "items": [],
                    "missing_info": [_note_cancelled_once() or "사용자가 취소를 요청했습니다."],
                    "warnings": [],
                }
            elif dwg_data or structural_pdf_bytes:
                try:
                    # 1단계: Gemini는 '읽기'만 (구조화된 부재 리스트). progress_cb로 배치별
                    # 진행상황("배치 3/6 처리 중")을 폴링 저장소에 실시간으로 남긴다. cancel_cb는
                    # 취소 요청이 들어오면 아직 시작 안 한 배치들을 건너뛰게 한다(비용 절약).
                    def _structural_progress_cb(idx, total):
                        _report_progress("structural", idx, total, f"구조 부재 추출 배치 {idx}/{total} 처리 중")

                    members = extract_structural_members(
                        dwg_data, structural_pdf_bytes, progress_cb=_structural_progress_cb, cancel_cb=_cancelled
                    )
                    # 색칠된 도면 미리보기 — Gemini가 채운 bbox(부재 위치)를 원본 페이지 위에
                    # 색상 박스로 그려서, 의뢰인이 채팅창에서 놓친 벽/보가 있는지 직접 확인할
                    # 수 있게 한다. bbox 데이터가 없거나 실패해도 예외를 던지지 않는 보조
                    # 기능이라, 여기서 문제가 생겨도 이후 흐름에는 영향이 없다.
                    try:
                        annotated_pages = build_annotated_pages(structural_pdf_bytes, members)
                    except Exception as e:
                        annotated_pages = []
                        results.setdefault("structural_warnings_pre", []).append(
                            f"색칠된 도면 미리보기 생성 중 오류가 발생했습니다: {str(e)[:200]}"
                        )

                    if job_id and not _cancelled():
                        # ── 실제 물량 계산 전에 사용자 확인을 거치게 한다 ──
                        # job_id가 있는 클라이언트(최신 프론트)에 한해서만 리뷰 단계를 넣는다.
                        # 여기서 바로 계산하지 않고 members를 job_id로 저장해두고, 프론트가
                        # "도면 확인" 팝업에서 사용자 확인/수정을 받은 뒤 /api/quantity/confirm-review/
                        # 로 다시 요청해야 실제 계산(compute_structural_quantities)이 실행된다.
                        _extraction_store_set(job_id, members, elevation_data)
                        results["structural"] = {
                            "review_required": True,
                            "job_id": job_id,
                            "annotated_pages": annotated_pages,
                            "checklist": _build_review_checklist(members),
                            "warnings": results.pop("structural_warnings_pre", []),
                        }
                    else:
                        # job_id가 없는 옛 클라이언트 호환용 — 리뷰 단계 없이 바로 계산.
                        results["structural"] = compute_structural_quantities(members, elevation_data)
                        results["structural"]["_raw_members"] = members  # 디버그/검증용
                        if elevation_data:
                            results["structural"]["_elevation_section"] = elevation_data  # 디버그/검증용
                        results["structural"]["massing"] = compute_massing_model(members, elevation_data)
                        results["structural"]["annotated_pages"] = annotated_pages
                        for w in results.pop("structural_warnings_pre", []):
                            results["structural"].setdefault("warnings", []).append(w)
                except Exception as e:
                    results["structural"] = {
                        "error": str(e), "items": [],
                        "missing_info": [f"구조 수량산출 처리 중 오류가 발생했습니다: {str(e)[:300]}"],
                        "warnings": [],
                    }
            else:
                # 파일은 올렸지만 dwg_data/structural_pdf_bytes 둘 다 비어서(빈 ZIP, 빈 PDF 등)
                # 추출을 아예 시작도 못한 경우 — 이전에는 이 경우 results에 "structural" 키
                # 자체가 생기지 않아, 프론트엔드에서 "왜 실패했는지" 전혀 알 수 없는 상태로
                # 그냥 "추출하지 못했어요"만 표시됐다. 실제 원인을 missing_info에 남겨서
                # 화면에 보이도록 한다.
                results["structural"] = {
                    "items": [],
                    "missing_info": ["구조도면 파일에서 읽을 수 있는 데이터가 없습니다 — ZIP/PDF가 비어있거나 손상됐을 수 있습니다."],
                    "warnings": [],
                }
    finally:
        # 요청이 끝나면(성공/실패 무관) 진행률/취소 항목을 정리한다 — 프론트엔드는 최종
        # 응답을 받는 순간 폴링을 멈추므로, 여기 남겨둬도 TTL(30분)로 언젠가 청소되긴
        # 하지만 job_id 재사용/누적을 막기 위해 즉시 지운다.
        _progress_clear(job_id)
        _cancel_clear(job_id)

    return JsonResponse({"ok": True, "results": results})


@require_POST
@_admin_only_json
def api_quantity_confirm_review(request):
    """도면 확인 팝업에서 "진행" 버튼을 눌렀을 때 호출되는 엔드포인트.
    api_run_quantity가 job_id로 임시 저장해둔 추출 결과(members)를 꺼내와서,
    사용자가 체크리스트에서 지정한 corrections(제거/수정)을 반영한 뒤에야
    실제 물량 계산(compute_structural_quantities)을 실행한다.
    corrections 형식(JSON 문자열, POST의 "corrections" 필드):
      [{"review_id": "columns:3", "action": "remove"},
       {"review_id": "walls:5", "action": "edit", "fields": {"count": 4, "length_m": 3.6}}]
    """
    job_id = request.POST.get("job_id") or None
    if not job_id:
        return JsonResponse({"error": "job_id가 없습니다."}, status=400)

    stored = _extraction_store_pop(job_id)
    if not stored:
        return JsonResponse({
            "error": "검토할 추출 결과를 찾을 수 없습니다 — 시간이 너무 지났거나(2시간 초과) "
                     "이미 처리된 job_id일 수 있습니다. 도면을 다시 업로드해서 분석해 주세요.",
        }, status=404)

    raw_corrections = request.POST.get("corrections") or "[]"
    try:
        corrections = json.loads(raw_corrections)
        if not isinstance(corrections, list):
            corrections = []
    except (TypeError, ValueError):
        corrections = []

    members = stored.get("members") or _EMPTY_MEMBERS
    elevation_data = stored.get("elevation_data")

    try:
        corrected_members = _apply_member_corrections(members, corrections)
        structural_results = compute_structural_quantities(corrected_members, elevation_data)
        structural_results["_raw_members"] = corrected_members  # 디버그/검증용
        if elevation_data:
            structural_results["_elevation_section"] = elevation_data  # 디버그/검증용
        structural_results["massing"] = compute_massing_model(corrected_members, elevation_data)
        if corrections:
            structural_results.setdefault("warnings", []).insert(
                0, f"도면 확인 단계에서 {len(corrections)}건의 수정사항이 반영됐습니다."
            )
    except Exception as e:
        return JsonResponse({
            "error": f"수정사항 반영 후 물량 계산 중 오류가 발생했습니다: {str(e)[:300]}",
        }, status=500)

    return JsonResponse({"ok": True, "results": {"structural": structural_results}})


@require_POST
@_admin_only_json
def api_quantity_cancel(request):
    """취소 요청 엔드포인트. 프론트엔드가 "취소" 버튼을 누르면 job_id를 담아 호출한다.
    api_run_quantity가 아직 처리 중이면(같은 job_id로 폴링 저장소에 진행상황이 있으면)
    다음 체크포인트(다음 배치/다음 단계 시작 전)에서 이 요청을 확인하고 이후 단계를
    건너뛴다. 이미 시작된 Gemini 호출 1개는 중간에 끊을 방법이 없어 끝까지 처리된다."""
    job_id = request.POST.get("job_id") or None
    if not job_id:
        return JsonResponse({"error": "job_id가 없습니다."}, status=400)
    _cancel_request(job_id)
    return JsonResponse({"ok": True})


@require_POST
@_admin_only_json
def api_quantity_progress(request):
    """진행률 폴링 엔드포인트. 프론트엔드가 api_run_quantity 요청을 보낸 직후부터
    주기적으로(예: 1.5초마다) job_id를 담아 호출해서 지금 어느 단계/배치까지 됐는지 읽는다.
    아직 기록이 없으면(요청이 아직 시작 전이거나, 이미 끝나서 정리된 경우) found=false로
    응답한다 — 프론트는 이 경우 "진행 중"으로만 표시하고 굳이 오류 취급하지 않는다."""
    job_id = request.POST.get("job_id") or request.GET.get("job_id") or None
    progress = _progress_get(job_id) if job_id else None
    if not progress:
        return JsonResponse({"found": False})
    return JsonResponse({
        "found": True,
        "stage": progress.get("stage"),
        "current": progress.get("current"),
        "total": progress.get("total"),
        "label": progress.get("label"),
        "stage_index": progress.get("stage_index"),
        "total_stages": progress.get("total_stages"),
    })


# ─────────────────────────────────────────────
#  API: 엑셀 다운로드
# ─────────────────────────────────────────────
def _build_excel_response(request):
    """
    수량산출 결과 JSON을 받아 엑셀 파일로 반환
    Body: { "results": { "structural": {...}, "architectural": {...} } }
    """
    try:
        body = json.loads(request.body)
        results = body.get("results", {})
    except (json.JSONDecodeError, AttributeError):
        return HttpResponse("잘못된 요청입니다.", status=400)

    wb = openpyxl.Workbook()

    # 스타일 정의
    navy_fill = PatternFill("solid", fgColor="073A5B")
    yellow_fill = PatternFill("solid", fgColor="F9C20A")
    green_fill = PatternFill("solid", fgColor="41a86b")
    light_fill = PatternFill("solid", fgColor="F0FAF4")
    header_font = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=11)
    sub_header_font = Font(name="맑은 고딕", bold=True, color="073A5B", size=10)
    body_font = Font(name="맑은 고딕", size=10)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    thin = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    def write_sheet(ws, data, sheet_title):
        ws.title = sheet_title

        # 제목 행
        ws.merge_cells("A1:G1")
        ws["A1"] = f"AI 수량산출 결과 — {sheet_title}"
        ws["A1"].font = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=13)
        ws["A1"].fill = navy_fill
        ws["A1"].alignment = center
        ws.row_dimensions[1].height = 32

        # 요약
        summary = data.get("summary", "")
        ws.merge_cells("A2:G2")
        ws["A2"] = f"요약: {summary}"
        ws["A2"].font = Font(name="맑은 고딕", italic=True, color="073A5B", size=10)
        ws["A2"].alignment = left
        ws.row_dimensions[2].height = 20

        # 컬럼 헤더
        headers = ["No.", "공종", "세부 항목", "수량", "단위", "신뢰도", "비고"]
        col_widths = [6, 18, 30, 12, 8, 10, 35]
        for i, (h, w) in enumerate(zip(headers, col_widths), 1):
            cell = ws.cell(row=3, column=i, value=h)
            cell.font = header_font
            cell.fill = PatternFill("solid", fgColor="1a5276") if i % 2 == 0 else navy_fill
            cell.alignment = center
            cell.border = thin
            ws.column_dimensions[cell.column_letter].width = w
        ws.row_dimensions[3].height = 22

        # 데이터 행
        items = data.get("items", [])
        for idx, item in enumerate(items, 1):
            row = idx + 3
            confidence_color = {
                "high": "D5F5E3",
                "medium": "FEF9E7",
                "low": "FADBD8",
            }.get(item.get("confidence", "medium"), "FFFFFF")

            values = [
                idx,
                item.get("category", ""),
                item.get("sub_category", ""),
                item.get("quantity", ""),
                item.get("unit", ""),
                {"high": "높음", "medium": "보통", "low": "낮음"}.get(item.get("confidence", ""), ""),
                item.get("note", ""),
            ]
            for col, val in enumerate(values, 1):
                cell = ws.cell(row=row, column=col, value=val)
                cell.font = body_font
                cell.fill = PatternFill("solid", fgColor=confidence_color)
                cell.alignment = center if col in (1, 4, 5, 6) else left
                cell.border = thin
            ws.row_dimensions[row].height = 18

        # 합계 행 (콘크리트 m³ / 철근 ton)
        last_row = len(items) + 4
        ws.merge_cells(f"A{last_row}:C{last_row}")
        ws[f"A{last_row}"] = "합계 (참고)"
        ws[f"A{last_row}"].font = sub_header_font
        ws[f"A{last_row}"].fill = light_fill
        ws[f"A{last_row}"].alignment = center
        ws[f"A{last_row}"].border = thin

        concrete_total = sum(
            item.get("quantity", 0) or 0
            for item in items if item.get("unit") == "m³"
        )
        rebar_total = sum(
            item.get("quantity", 0) or 0
            for item in items if item.get("unit") == "ton"
        )
        ws[f"D{last_row}"] = round(concrete_total, 2)
        ws[f"D{last_row}"].font = sub_header_font
        ws[f"D{last_row}"].fill = light_fill
        ws[f"D{last_row}"].border = thin
        ws[f"E{last_row}"] = f"m³(콘크리트) / {round(rebar_total,2)}ton(철근)"
        ws[f"E{last_row}"].font = body_font
        ws[f"E{last_row}"].fill = light_fill
        ws.merge_cells(f"E{last_row}:G{last_row}")
        ws[f"E{last_row}"].border = thin

        # 경고 사항
        warnings = data.get("warnings", [])
        if warnings:
            warn_row = last_row + 2
            ws[f"A{warn_row}"] = "⚠ 주의사항"
            ws[f"A{warn_row}"].font = Font(name="맑은 고딕", bold=True, color="E74C3C")
            for i, w in enumerate(warnings):
                ws[f"A{warn_row+1+i}"] = f"• {w}"
                ws[f"A{warn_row+1+i}"].font = Font(name="맑은 고딕", size=9, color="922B21")
                ws.merge_cells(f"A{warn_row+1+i}:G{warn_row+1+i}")

        # 누락 정보
        missing = data.get("missing_info", [])
        if missing:
            m_row = last_row + 2 + len(warnings) + 2
            ws[f"A{m_row}"] = "확인 필요 항목"
            ws[f"A{m_row}"].font = Font(name="맑은 고딕", bold=True, color="1A5276")
            for i, m in enumerate(missing):
                ws[f"A{m_row+1+i}"] = f"• {m}"
                ws[f"A{m_row+1+i}"].font = Font(name="맑은 고딕", size=9, color="1A5276")
                ws.merge_cells(f"A{m_row+1+i}:G{m_row+1+i}")

        ws.freeze_panes = "A4"

    def write_floor_breakdown_sheet(ws, floor_breakdown):
        """구조 부재를 구간(동/섹션) x 층(zone) 기준 물량 소계로 펼쳐서 보여주는 시트.
        compute_floor_breakdown()이 만든 [{"section", "concrete_m3", "formwork_m2", "rebar_kg",
        "floors": [{"zone", "concrete_m3", "formwork_m2", "rebar_kg", "categories":[...]}]}]
        중첩 리스트를 그대로 받아 그린다. 슬래브/보(수평재)는 이미 시공순서 기준(물리적 바로
        위층)으로 재배정된 상태로 들어온다 — compute_floor_breakdown() 참고."""
        ws.title = "층별 수량산출"

        ws.merge_cells("A1:E1")
        ws["A1"] = "AI 수량산출 결과 — 구간(동)x층별 소계 (시공순서 기준, 기준층 반복·철근 할증 적용)"
        ws["A1"].font = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=13)
        ws["A1"].fill = navy_fill
        ws["A1"].alignment = center
        ws.row_dimensions[1].height = 32

        headers = ["구간/층", "부재종류", "콘크리트(m³)", "거푸집(m²)", "철근(ton)"]
        col_widths = [22, 18, 14, 14, 14]
        for i, (h, w) in enumerate(zip(headers, col_widths), 1):
            cell = ws.cell(row=2, column=i, value=h)
            cell.font = header_font
            cell.fill = navy_fill
            cell.alignment = center
            cell.border = thin
            ws.column_dimensions[cell.column_letter].width = w
        ws.row_dimensions[2].height = 22

        row = 3
        grand_concrete = 0.0
        grand_formwork = 0.0
        grand_rebar_kg = 0.0

        for section in floor_breakdown:
            sec_row = row
            ws.cell(row=sec_row, column=1, value=f"[{section.get('section', '미상')}] 구간 합계")
            ws.cell(row=sec_row, column=2, value="")
            ws.cell(row=sec_row, column=3, value=section.get("concrete_m3", 0))
            ws.cell(row=sec_row, column=4, value=section.get("formwork_m2", 0))
            ws.cell(row=sec_row, column=5, value=round((section.get("rebar_kg", 0) or 0) / 1000, 3))
            for col in range(1, 6):
                c = ws.cell(row=sec_row, column=col)
                c.font = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=10)
                c.fill = navy_fill
                c.alignment = center if col != 1 else left
                c.border = thin
            ws.merge_cells(start_row=sec_row, start_column=1, end_row=sec_row, end_column=2)
            ws.row_dimensions[sec_row].height = 20
            row += 1

            for floor in section.get("floors", []):
                zone_row = row
                ws.cell(row=zone_row, column=1, value="  " + floor.get("zone", "미상"))
                ws.cell(row=zone_row, column=2, value="(층 소계)")
                ws.cell(row=zone_row, column=3, value=floor.get("concrete_m3", 0))
                ws.cell(row=zone_row, column=4, value=floor.get("formwork_m2", 0))
                ws.cell(row=zone_row, column=5, value=round((floor.get("rebar_kg", 0) or 0) / 1000, 3))
                for col in range(1, 6):
                    c = ws.cell(row=zone_row, column=col)
                    c.font = sub_header_font
                    c.fill = yellow_fill if col == 1 else light_fill
                    c.alignment = center if col != 1 else left
                    c.border = thin
                ws.row_dimensions[zone_row].height = 20
                row += 1

                for cat in floor.get("categories", []):
                    ws.cell(row=row, column=1, value="")
                    ws.cell(row=row, column=2, value="    " + cat.get("category", ""))
                    ws.cell(row=row, column=3, value=cat.get("concrete_m3", 0))
                    ws.cell(row=row, column=4, value=cat.get("formwork_m2", 0))
                    ws.cell(row=row, column=5, value=round((cat.get("rebar_kg", 0) or 0) / 1000, 3))
                    for col in range(1, 6):
                        c = ws.cell(row=row, column=col)
                        c.font = body_font
                        c.alignment = center if col != 2 else left
                        c.border = thin
                    ws.row_dimensions[row].height = 18
                    row += 1

            grand_concrete += section.get("concrete_m3", 0) or 0
            grand_formwork += section.get("formwork_m2", 0) or 0
            grand_rebar_kg += section.get("rebar_kg", 0) or 0
            row += 1  # 구간 사이 여백

        ws.cell(row=row, column=1, value="전체 합계")
        ws.cell(row=row, column=2, value="")
        ws.cell(row=row, column=3, value=round(grand_concrete, 3))
        ws.cell(row=row, column=4, value=round(grand_formwork, 2))
        ws.cell(row=row, column=5, value=round(grand_rebar_kg / 1000, 3))
        for col in range(1, 6):
            c = ws.cell(row=row, column=col)
            c.font = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=10)
            c.fill = green_fill
            c.alignment = center if col != 1 else left
            c.border = thin
        ws.row_dimensions[row].height = 20

        ws.freeze_panes = "A3"

    # 구조 시트
    if "structural" in results and results["structural"].get("items"):
        ws_s = wb.active
        write_sheet(ws_s, results["structural"], "구조 수량산출")
    else:
        wb.active.title = "구조 수량산출"
        wb.active["A1"] = "구조도면 데이터 없음"

    # 층별 수량산출 시트 (zone/floor_repeat_count 판독이 되어 소계가 있을 때만 생성)
    struct_floor_breakdown = (results.get("structural") or {}).get("floor_breakdown") or []
    if struct_floor_breakdown:
        ws_floor = wb.create_sheet("층별 수량산출")
        write_floor_breakdown_sheet(ws_floor, struct_floor_breakdown)

    # 건축 시트
    if "architectural" in results and results["architectural"].get("items"):
        ws_a = wb.create_sheet("건축 수량산출")
        write_sheet(ws_a, results["architectural"], "건축 수량산출")

    # 통합 시트
    ws_total = wb.create_sheet("통합 요약")
    ws_total["A1"] = "AI 구조산출 자동화 — 통합 요약"
    ws_total["A1"].font = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=13)
    ws_total["A1"].fill = navy_fill
    ws_total.merge_cells("A1:E1")
    ws_total["A1"].alignment = center
    ws_total.row_dimensions[1].height = 32

    row = 3
    for type_key, type_name in [("structural", "구조"), ("architectural", "건축")]:
        data = results.get(type_key, {})
        items = data.get("items", [])
        if not items:
            continue

        ws_total[f"A{row}"] = f"[{type_name} 수량산출 요약]"
        ws_total[f"A{row}"].font = Font(name="맑은 고딕", bold=True, color="073A5B", size=11)
        ws_total.merge_cells(f"A{row}:E{row}")
        row += 1

        categories = {}
        for item in items:
            cat = item.get("category", "기타")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(item)

        for cat, cat_items in categories.items():
            ws_total[f"A{row}"] = cat
            ws_total[f"A{row}"].font = Font(name="맑은 고딕", bold=True, size=10)
            ws_total[f"A{row}"].fill = light_fill
            ws_total.merge_cells(f"A{row}:E{row}")
            row += 1
            for item in cat_items:
                ws_total[f"B{row}"] = item.get("sub_category", "")
                ws_total[f"C{row}"] = item.get("quantity", "")
                ws_total[f"D{row}"] = item.get("unit", "")
                ws_total[f"E{row}"] = {"high": "●", "medium": "◐", "low": "○"}.get(
                    item.get("confidence", ""), "?"
                )
                for col in "BCDE":
                    ws_total[f"{col}{row}"].font = body_font
                    ws_total[f"{col}{row}"].border = thin
                row += 1
        row += 1

    for col_letter, width in zip("ABCDE", [30, 35, 12, 8, 8]):
        ws_total.column_dimensions[col_letter].width = width

    # 응답
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    response = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="CBL_수량산출결과.xlsx"'
    return response


@require_POST
@_admin_only_json
def api_download_excel(request):
    """엑셀 다운로드 뷰. 실제 생성 작업은 _build_excel_response()가 담당하고, 여기서는
    그 과정에서 나는 모든 예외를 잡아서 사용자에게 실패 사유를 그대로 보여준다 — 예전에는
    (예: results 구조가 예상과 달라 openpyxl에서 KeyError/TypeError가 나는 경우) 예외가
    Django 500 에러로 그대로 터져서, 사용자는 이유를 알 수 없는 "엑셀 다운로드에 실패했어요"
    메시지만 봐야 했다."""
    try:
        return _build_excel_response(request)
    except Exception as e:
        return HttpResponse(f"엑셀 생성 중 오류가 발생했습니다: {str(e)[:300]}", status=500)
