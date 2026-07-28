"""
결정론적 구조 수량계산 엔진
- 입력: OpenAI Vision이 도면에서 "읽어온" 부재 리스트 (구조화된 JSON, 계산은 안 함)
- 출력: 콘크리트/거푸집/철근 수량 (공식으로 코드가 직접 계산 — LLM 관여 없음)
"""

import math
import re

# ─────────────────────────────────────────────
# KS 표준 철근 단위중량표 (kg/m) — 코드 상수로 관리, LLM이 만들어내지 않음
# (SK에코플랜트 건축PART 견적 Manual Rev.18 규격별 단위중량표로 검증/보정함 — D25는
#  기존 3.850kg/m이 오류였고 3.980kg/m이 맞음: (π/4)×25.4²×7850/10^6 = 3.978kg/m)
# ─────────────────────────────────────────────
REBAR_UNIT_WEIGHT = {
    "D10": 0.560, "D13": 0.995, "D16": 1.560, "D19": 2.250,
    "D22": 3.040, "D25": 3.980, "D29": 5.040, "D32": 6.230,
    "D35": 7.510, "D38": 8.950,
}

# ─────────────────────────────────────────────
# 이음길이/정착길이 공식 엔진 (KDS 14 20 52 근사) — 정적 표 대신 매번 공식으로 계산합니다.
# 기본 공식(직선 인장이형철근 정착길이):
#   ld(mm) = case계수 × db(mm) × fy(MPa) / √fck(MPa) × ψs × ψt
#     - case계수: 0.6(피복/간격 충분, Case1) 또는 0.9(그 외, Case2) — 1.5배 차이
#     - ψs(대형철근계수): D19 이하 0.8, D22 이상 1.0
#     - ψt(상부철근계수): 1.3 (콘크리트 타설시 하부에 신선콘크리트 300mm 이상 쌓이는 위치, 예: 보/슬래브 상부근)
#   이음길이(B급) = 1.3 × ld (동일 조건의 직선 정착길이 기준, 갈고리 이음은 실무상 쓰지 않으므로 항상 직선 기준)
#   표준갈고리 정착길이: ldh(mm) = 0.24 × db(mm) × fy(MPa) / √fck(MPa), 최소 max(8db, 150mm)
# SD400/Fck24MPa/Case1/일반철근 기본값 기준으로 계산한 결과가 SK에코플랜트 건축PART 견적
# Manual Rev.18의 "정착, 이음" 항목(39.19d/48.99d 정착, 50.45d/63.69d 이음, d=철근 호칭지름 mm)과
# 정확히 일치함을 확인함. general_spec에서 실제 Fck/철근강종을 읽었으면 그 값을 그대로 반영합니다.
# ※ 실제 β(피복계수, 갈고리)·λ(경량콘크리트계수)·정확한 순피복/순간격 조건은 반영하지 않은 근사치이며,
#   general_spec.cover_thickness_mm(대표 피복두께)와 주철근 지름을 비교해 Case1/Case2만 단순 판정합니다.
# ─────────────────────────────────────────────
STOCK_BAR_LENGTH_M = 10.0  # 철근 장대(정척) 길이 기준값 — 이 길이를 넘는 부재는 이음(splice) 필요

# 철근 할증률 — 제강사 규격손실/절단로스 등 (SK에코플랜트 매뉴얼: 제강사별 1.5%~3.0%, 3% 적용).
# 순물량(설계 물량) 위에 곱해서 "발주 물량"을 만드는 계수입니다. 콘크리트/거푸집에는 적용하지 않습니다.
REBAR_WASTE_FACTOR = 1.03


def _with_waste(kg):
    return round(kg * REBAR_WASTE_FACTOR, 2)


# 지피티 독립 검토로 재현/확인된 실제 버그: 모든 calc_* 함수가 개수(count)를
# `it.get("count", 1) or 1` 관용구로 읽었는데, 이 표현은 "키가 아예 없음"과 "키는
# 있는데 값이 0/None"을 구분하지 못한다 — 파이썬에서 0과 None이 모두 falsy라서 `or 1`
# 폴백에 걸려 둘 다 조용히 "1개"로 둔갑한다. count=None은 두 가지 경우에서 생긴다:
# (1) Gemini가 원본을 읽을 때부터 "몇 개인지 모르겠다"는 뜻으로 명시적으로 null을
#     반환한 경우, (2) 원본이 0/음수 같은 무효값이어서 _sanitize_raw_members가
#     안전하게 비워둔 경우. 두 경우 모두 "실제 개수를 모른다"는 뜻이지 "1개"라는
#     뜻이 아니다 — 개수를 모르는 채로 1개라고 가정하면, 실제로는 부재가 여러 개인
#     경우 물량이 크게 과소산출될 수 있다(반대로 count=-2 같은 값을 검증 없이 그대로
#     계산에 넣으면 물량이 음수로 나오는 것도 별도로 확인됨). 그래서 "1개 기본값"은
#     키 자체가 없을 때(=원본 데이터에 count 필드가 아예 없어서 아무 신호도 없는 경우)
#     로만 한정하고, 키가 있는데 무효하면(None/0/음수/비정수) 그 부재를 계산에서
#     제외해서 사용자가 검토 화면에서 직접 채우게 한다 — 이음/정착길이 완전제외
#     정책(EXCLUDED_SOURCE)과 같은 원칙("모르면 추정하지 말고 빼고 알려라")이다.
def _resolve_count(it, category, mark, warnings=None):
    """it.get("count", ...) 자리를 대체하는 안전한 개수 해석 함수.
    반환값이 None이면 호출측이 continue로 그 부재를 건너뛰어야 한다(치수 누락과
    동일하게 취급). warnings는 선택 — 경고 목록을 안 쓰는 보조 계산(3D 뷰어 집계 등)은
    None으로 두면 조용히 건너뛴다."""
    if "count" not in it:
        return 1
    n = it["count"]
    if n is None or isinstance(n, bool) or not isinstance(n, (int, float)) or n != int(n) or n <= 0:
        if warnings is not None:
            warnings.append(f"{category} {mark}: 개수(count)를 확인할 수 없어(값: {n!r}) 계산에서 제외했습니다 — 검토 화면에서 직접 입력해 주세요")
        return None
    return int(n)


def _valid_rebar_layers(it, category, mark, warnings):
    """세분화 배근(rebar_layers) 필드를 검증해서 계산에 쓸 수 있는 층(layer)만 골라
    반환한다. 사용자 제공 철근참조자료(슬래브 X/Y·상하부·주열대/중간대, 기둥 MAIN BAR
    그룹·HOOP 단부/중앙부, 보 상하부·단부/중앙부·스터럽구간, 전단벽 수직/수평·단부/모서리/
    교차부/개구부보강, 계단 상하부·배력근·계단참)를 반영하려고 추가한 필드다.
    rebar_layers가 아예 없거나 빈 배열이면 빈 리스트를 반환한다 — 호출부는 이 경우
    기존 "대표 철근 1세트" 필드(main_rebar_size 등) 방식으로 계산해야 한다(하위호환).
    철근 규격을 모르거나(REBAR_UNIT_WEIGHT에 없음) 간격/개수가 둘 다 없는 층은
    EXCLUDED_SOURCE 정책과 같은 원칙으로 경고를 남기고 제외한다."""
    layers = it.get("rebar_layers")
    if not layers or not isinstance(layers, list):
        return []
    valid = []
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict):
            continue
        size = layer.get("size")
        if not size or size not in REBAR_UNIT_WEIGHT:
            warnings.append(f"{category} {mark}: 세분화배근[{i}]({layer.get('role','?')}) 철근규격을 확인할 수 없어(값: {size!r}) 제외했습니다")
            continue
        spacing = layer.get("spacing_m")
        count = layer.get("count")
        has_spacing = isinstance(spacing, (int, float)) and not isinstance(spacing, bool) and spacing > 0
        has_count = isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0
        if not has_spacing and not has_count:
            warnings.append(f"{category} {mark}: 세분화배근[{i}]({layer.get('role','?')}) 간격/개수 정보가 없어 제외했습니다")
            continue
        valid.append(layer)
    return valid


def _zone_segment_length_m(layer, member_length_m, default_ratio):
    """rebar_layers의 zone("단부"/"중앙부")에 해당하는 부재길이방향 길이(m)를 구한다.
    layer에 zone_length_m이 명시돼 있으면 그 값을 그대로 쓰고, 없으면 부재 전체길이의
    default_ratio 비율로 근사한다(단부 기본 25%, 중앙부 기본 50% — 실제 배근도 구간
    길이를 모를 때의 거친 근사치이므로, 정확도가 중요하면 zone_length_m을 채워야 한다)."""
    zl = layer.get("zone_length_m")
    if isinstance(zl, (int, float)) and not isinstance(zl, bool) and zl > 0:
        return zl
    if not isinstance(member_length_m, (int, float)):
        return 0.0
    return member_length_m * default_ratio


def _spacing_bar_count(length_m, spacing_m):
    """부재 길이(또는 폭/높이) length_m에 spacing_m 간격으로 철근/스터럽/띠철근을 배치할 때
    필요한 가닥(개소) 수. 기존에는 ceil(length/spacing)을 썼는데, 이는 "시작점에는 배치
    안 함"을 암묵적으로 가정한 값이라 실무 관행(양 끝에 1개씩 배치하고 그 사이를 간격으로
    채움, 이른바 펜스포스트 방식: 개수 = 구간 수 + 1)보다 보통 1개 적게 나온다.
    floor(length/spacing) + 1로 계산해서 이 관행에 맞춘다.
    부동소수점 오차로 길이가 간격의 정확한 배수일 때 한 칸 덜 세는 걸 막기 위해 아주 작은
    보정값(1e-9)을 더한 뒤 내림한다."""
    if not spacing_m or spacing_m <= 0 or not length_m or length_m <= 0:
        return 0
    return int(math.floor(length_m / spacing_m + 1e-9)) + 1


def _bar_diameter_mm(bar_size):
    """'D25' -> 25.0 (호칭지름 mm 근사, 국내 관행상 호칭번호를 mm로 그대로 사용)"""
    if not bar_size or not isinstance(bar_size, str) or not bar_size.upper().startswith("D"):
        return None
    try:
        return float(bar_size[1:])
    except ValueError:
        return None


def _rebar_fy_mpa(general_spec, bar_size=None):
    """
    철근 강종 -> 항복강도(MPa) 근사.
    1순위: general_spec.rebar_grade_table에서 해당 철근 지름에 맞는 행
      (표는 [{"bar_size_max": 10, "grade": "SD500", "fy_mpa": 500}, ...] 형태로,
      호칭지름이 bar_size_max 이하인 첫 번째 행을 오름차순으로 찾는다 — 도면에 지름별로
      강종이 다르게 표기된 경우(예: D16 이하 SD500, D19 이상 SD500S) 대응)
    2순위: general_spec.rebar_grade 단일 대표값
    3순위: SD400(400MPa) 기본값
    """
    table = (general_spec or {}).get("rebar_grade_table") or []
    if table:
        db = _bar_diameter_mm(bar_size) if bar_size else None
        if db is not None:
            rows = sorted(
                (r for r in table if isinstance(r.get("fy_mpa"), (int, float))),
                key=lambda r: r.get("bar_size_max") if isinstance(r.get("bar_size_max"), (int, float)) else 10**9,
            )
            for row in rows:
                bmax = row.get("bar_size_max")
                if bmax is None or db <= bmax:
                    return float(row["fy_mpa"])

    grade = ((general_spec or {}).get("rebar_grade") or "").upper().replace(" ", "")
    if "SD600" in grade:
        return 600.0
    if "SD500" in grade:
        return 500.0
    return 400.0


def _concrete_fck_mpa(general_spec, category=None, zone=None):
    """
    콘크리트 설계기준강도(MPa).
    1순위: general_spec.concrete_fck_table에서 category(기초/기둥/보/슬래브/전단벽/계단)가
      정확히 일치하는 행 — zone(예: "지하1층", "기준층(2~15층)")이 주어지면 그 중에서도
      row.zone_scope("지하"/"지상")가 zone 문자열과 부합하는 행을 우선한다(도면에 지하/지상
      Fck가 다르게 표기된 경우 대응). zone_scope가 없는(공통) 행은 위치 무관 폴백으로 쓴다.
      ※ category는 개요/구조일반사항 사전확인 프롬프트(OVERVIEW_SPEC_SYSTEM_PROMPT)와
      본 추출 프롬프트(MEMBER_EXTRACTION_SYSTEM_PROMPT) 양쪽 모두 이 6개 이름 그대로
      쓰도록 통일돼 있다 — 예전에는 사전확인 쪽이 "기초 콘크리트"/"지하층 벽·기둥"처럼 다른
      이름을 써서 이 매칭이 항상 실패하고 대표값(2순위)만 쓰이는 버그가 있었다(지피티 검토로
      재현/확인됨). 표에 없는 category라도 general_spec.concrete_fck_mpa로는 폴백되므로 계산
      자체가 죽지는 않았지만, 부위별로 다른 Fck가 반영되지 않는 문제였다.
    2순위: general_spec.concrete_fck_mpa 단일 대표값
    3순위: 24MPa 기본값
    """
    table = (general_spec or {}).get("concrete_fck_table") or []
    if category and table:
        zone_wanted = None
        if zone:
            zone_str = str(zone)
            if "지하" in zone_str:
                zone_wanted = "지하"
            elif "지상" in zone_str or "기준층" in zone_str:
                zone_wanted = "지상"

        # 1차: category + zone_scope가 정확히 일치하는 행 우선
        if zone_wanted:
            for row in table:
                fck = row.get("fck_mpa")
                if row.get("category") == category and row.get("zone_scope") == zone_wanted \
                        and isinstance(fck, (int, float)) and fck > 0:
                    return float(fck)
        # 2차: category만 일치하고 zone_scope가 없는(공통) 행
        for row in table:
            fck = row.get("fck_mpa")
            if row.get("category") == category and not row.get("zone_scope") \
                    and isinstance(fck, (int, float)) and fck > 0:
                return float(fck)
        # 3차: zone 매칭에 실패했어도 category만 일치하면 그 행이라도 사용(위치 무관보다 낫다)
        for row in table:
            fck = row.get("fck_mpa")
            if row.get("category") == category and isinstance(fck, (int, float)) and fck > 0:
                return float(fck)

    fck = (general_spec or {}).get("concrete_fck_mpa")
    return float(fck) if isinstance(fck, (int, float)) and fck > 0 else 24.0


def _adequate_cover(general_spec, bar_size):
    """
    피복두께가 충분한지(Case1) 여부를 단순 판정한다: 대표 피복두께(cover_thickness_mm) >= 주철근 지름.
    cover_thickness_mm 정보가 없으면 "충분(Case1)"으로 가정 — 이전 버전과 동일한 기본 동작 유지.
    ※ 실제 KDS 판정은 순간격/스터럽 배치 여부까지 함께 보므로, 이 판정은 근사치입니다.
    """
    cover_mm = (general_spec or {}).get("cover_thickness_mm")
    db = _bar_diameter_mm(bar_size)
    if not isinstance(cover_mm, (int, float)) or db is None:
        return True
    return cover_mm >= db


MIN_STRAIGHT_DEVELOPMENT_LENGTH_MM = 300.0  # KDS 14 20 52: 인장 이형철근의 정착길이는 항상 300mm 이상


def _development_length_straight_m(bar_size, general_spec, top_bar=False, adequate_cover=True, category=None):
    """직선철근 인장정착길이(ld, m) — KDS 14 20 52 근사 공식. 최소 300mm.
    사용자가 제공한 철근 참조 자료(SD400/SD500 이음길이 실측표)를 대조하다 발견한 갭:
    가는 철근(예: HD10)은 콘크리트 강도가 얼마든 이음길이가 300/390mm로 고정돼 있었는데,
    이건 공식 계산값이 KDS의 최소 정착길이(300mm) 규정보다 작아서 그 하한이 그대로 표에
    나온 것이다. 이 함수는 get_anchorage_length(직선)와 get_splice_length(이 값에 B급
    1.3배)가 공유하므로, 여기서 한 번만 최소값을 강제하면 이음길이도 자연히 300×1.3=390mm
    하한을 갖게 돼 자료의 표와 정확히 일치한다."""
    db = _bar_diameter_mm(bar_size)
    if db is None:
        return 0.0
    fy = _rebar_fy_mpa(general_spec, bar_size)
    fck = _concrete_fck_mpa(general_spec, category)
    psi_s = 0.8 if db <= 19 else 1.0
    case_coeff = 0.6 if adequate_cover else 0.9
    psi_t = 1.3 if top_bar else 1.0
    ld_mm = case_coeff * db * fy / math.sqrt(fck) * psi_s * psi_t
    ld_mm = max(ld_mm, MIN_STRAIGHT_DEVELOPMENT_LENGTH_MM)
    return round(ld_mm / 1000, 3)


def _development_length_hooked_m(bar_size, general_spec, category=None):
    """표준갈고리 인장정착길이(ldh, m) — KDS 14 20 52 근사 공식. 최소 max(8db, 150mm)."""
    db = _bar_diameter_mm(bar_size)
    if db is None:
        return 0.0
    fy = _rebar_fy_mpa(general_spec, bar_size)
    fck = _concrete_fck_mpa(general_spec, category)
    ldh_mm = 0.24 * db * fy / math.sqrt(fck)
    ldh_mm = max(ldh_mm, 8 * db, 150.0)
    return round(ldh_mm / 1000, 3)


# get_splice_length/get_anchorage_length가 반환하는 "source" 값 중, 이 값이면 호출측
# calc_* 함수가 그 항목의 철근 물량을 아예 빼야 한다는 신호다(공식 추정값이 아님).
# 개요/구조일반사항 사전 확인 흐름에서 사용자가 "미확인 항목은 계산에서 완전히 빼 달라"고
# 명시적으로 요청해서 추가한 정책 — general_spec._confirmed가 True일 때만 발동한다.
EXCLUDED_SOURCE = "미확인(확정전 계산제외)"


def _valid_table_length_m(v):
    """lap_splice_table/anchorage_table 행의 length_m이 실제로 쓸 수 있는 값인지 검사한다.
    지피티 독립 검토에서 재현된 버그: 예전에는 `not row.get("length_m")`만 걸러서
    0/None만 막고, 음수(-1)나 문자열("bad") 같은 값은 그대로 "도면표기재"로 채택했다 —
    음수는 경고 없이 음수 길이가 물량에 섞여 들어갔고, 문자열은 이후 산술 연산에서
    TypeError로 계산 전체가 죽었다. 표에 있는 값이라고 무조건 신뢰하지 않고, 여기서
    막힌 행은 "이 항목은 표에 없는 것"과 동일하게 취급해 _row_ok가 건너뛰게 한다 —
    그러면 general_spec._confirmed=True일 때는 EXCLUDED_SOURCE로 안전하게 제외된다."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _excluded_note(category, mark, size, item_label, parts):
    """calc_* 함수들이 EXCLUDED_SOURCE를 받았을 때 공통으로 쓰는 경고 문구를 만든다.
    parts는 ["이음길이", "정착길이"] 중 실제로 못 찾은 항목들."""
    part_str = "/".join(parts) if parts else "이음/정착길이"
    return (
        f"{category} {mark}: {size} {part_str}를 도면에서 확인하지 못했습니다 — "
        f"해당 항목({item_label})은 확정 전까지 철근 실수량 계산에서 제외됩니다."
    )


def get_splice_length(bar_size, general_spec, top_bar=False, category=None):
    """
    이음길이(m)를 구한다.
    1순위: 도면 구조일반사항에서 읽은 표(general_spec.lap_splice_table, position이 일치하는 행 우선)
    2순위: 공식 계산값 (B급 이음길이 = 1.3 × 직선 정착길이, 부재 카테고리별 Fck·철근지름별 강종 반영)
      — 이 경우 "확인 필요"로 표시
    ※ 겹침이음(splice)은 실무상 항상 직선 구간에서 이뤄지므로 갈고리는 적용하지 않습니다.
    """
    table = (general_spec or {}).get("lap_splice_table") or []
    # top_bar=False(하부근)일 때 wanted_pos를 None으로 두면 아래 매칭 조건에서
    # "포지션 무관 매칭"과 똑같이 취급돼, 표에 상부/하부가 나뉘어 있어도 먼저 나오는
    # 행(대개 상부)을 하부근에도 그대로 돌려주는 버그가 있었다. 하부근이면 명시적으로
    # "하부"를 찾게 해야, 표에 포지션이 없는 행(row_pos is None → 공통값)만 무관 매칭되고
    # 상부/하부가 실제로 나뉜 표에서는 정확한 쪽을 고른다.
    #
    # 2-pass로 훑는 이유: 표 순서가 뒤죽박죽이어서(예: 공통값 행이 하부 전용 행보다
    # 앞에 나오는 경우) 첫 매치에서 바로 return하면 "포지션 무관 공통값"을 "포지션이
    # 정확히 일치하는 값"보다 먼저 골라버리는 문제가 있었다 — 실제로 재현 확인됨.
    # 그래서 1차로 표 전체를 훑어 "포지션이 정확히 일치하는 행"만 찾고, 그게 없을 때만
    # 2차로 "포지션 무관(공통) 행"을 찾는다. 이렇게 하면 표 안의 행 순서와 상관없이
    # 항상 더 구체적인(정확히 일치하는) 값이 우선한다.
    wanted_pos = "상부" if top_bar else "하부"

    def _row_ok(row):
        if row.get("bar_size") != bar_size or not _valid_table_length_m(row.get("length_m")):
            return False
        # 이음은 항상 B급으로 계산한다는 방침 — 표에 A급/B급이 각각 행으로 나뉘어 있고
        # 그 행에 splice_class="A"가 찍혀 있으면 그 행은 건너뛴다(길이가 더 짧아서 실제
        # 이음길이를 과소 산정하게 되므로). splice_class가 없거나 "B"면 그대로 쓴다.
        row_class = row.get("splice_class")
        if row_class and str(row_class).strip().upper().startswith("A"):
            return False
        return True

    for row in table:  # 1차: 정확히 일치하는 포지션 우선
        if _row_ok(row) and row.get("position") == wanted_pos:
            return row["length_m"], "도면표기재(B급)"
    for row in table:  # 2차: 포지션 구분이 없는 공통값
        if _row_ok(row) and row.get("position") is None:
            return row["length_m"], "도면표기재(B급)"

    # general_spec._confirmed가 True면(개요/구조일반사항 사전 확인 단계를 거쳐 사용자가
    # "예"로 확정한 경우), 표에서 못 찾은 항목은 공식으로 추정하지 않고 완전히 제외한다 —
    # 사용자가 "미확인 항목은 직접 값을 채우기 전까지 계산에서 빼 달라"고 명시적으로
    # 요청한 정책이다. _confirmed가 없으면(기존 경로/이 사전확인 단계를 거치지 않은
    # 경우) 하위호환을 위해 기존처럼 공식 추정값을 그대로 쓴다.
    if (general_spec or {}).get("_confirmed"):
        return 0.0, EXCLUDED_SOURCE

    adequate = _adequate_cover(general_spec, bar_size)
    ld = _development_length_straight_m(bar_size, general_spec, top_bar=top_bar, adequate_cover=adequate, category=category)
    if ld <= 0:
        return 0.0, "알수없음"
    return round(ld * 1.3, 3), "추정값(확인필요)"


def get_anchorage_length(bar_size, general_spec, top_bar=False, hook=False, category=None):
    """
    정착길이(m)를 구한다.
    1순위: 도면 구조일반사항에서 읽은 표(general_spec.anchorage_table, hook/position이 일치하는 행 우선)
    2순위: 공식 계산값 — hook=True면 표준갈고리 정착길이, 아니면 직선철근 정착길이(상부근/피복/부재
      카테고리별 Fck·철근지름별 강종 반영)
    """
    table = (general_spec or {}).get("anchorage_table") or []
    # get_splice_length와 동일한 이유로 하부근일 때 "하부"를 명시적으로 찾는다 —
    # None으로 두면 상부/하부가 나뉜 표에서도 먼저 나오는 행(대개 상부)을 그대로
    # 돌려주는 버그가 있었다. 마찬가지로 표 순서와 무관하게 정확한 포지션 일치를
    # 공통값보다 항상 우선하도록 2-pass로 찾는다(get_splice_length와 동일한 이유).
    wanted_pos = "상부" if top_bar else "하부"

    def _row_ok(row):
        if row.get("bar_size") != bar_size or not _valid_table_length_m(row.get("length_m")):
            return False
        return bool(row.get("hook", False)) == bool(hook)

    for row in table:  # 1차: 정확히 일치하는 포지션 우선
        if _row_ok(row) and row.get("position") == wanted_pos:
            return row["length_m"], "도면표기재"
    for row in table:  # 2차: 포지션 구분이 없는 공통값
        if _row_ok(row) and row.get("position") is None:
            return row["length_m"], "도면표기재"

    # get_splice_length와 동일한 정책 — 사전 확인 단계에서 확정된(_confirmed) general_spec
    # 인데 표에서 못 찾으면 공식 추정 없이 완전히 제외한다.
    if (general_spec or {}).get("_confirmed"):
        return 0.0, EXCLUDED_SOURCE

    if hook:
        ldh = _development_length_hooked_m(bar_size, general_spec, category=category)
        if ldh <= 0:
            return 0.0, "알수없음"
        return ldh, "추정값(확인필요)"

    adequate = _adequate_cover(general_spec, bar_size)
    ld = _development_length_straight_m(bar_size, general_spec, top_bar=top_bar, adequate_cover=adequate, category=category)
    if ld <= 0:
        return 0.0, "알수없음"
    return ld, "추정값(확인필요)"


def rebar_weight(size, total_length_m):
    """철근 규격 + 총 길이(m) -> 중량(kg)"""
    unit = REBAR_UNIT_WEIGHT.get(size)
    if unit is None:
        return 0.0, f"알 수 없는 철근 규격: {size}"
    return round(total_length_m * unit, 2), None


def _chair_bar_weight(net_area_m2, general_spec):
    """
    우마근(Chair Bar)/우마고정근 중량(kg) 근사 산출 — SK에코플랜트 매뉴얼 공식.
    슬래브/계단 상하 2단 배근을 지지하는 보조철근으로, general_spec.chair_bar_size와
    chair_bar_height_m(우마 높이 H, 도면에서 직접 읽은 값)이 둘 다 있을 때만 계산합니다
    — 도면에 없으면 임의로 추정하지 않고 0을 반환합니다(단일 배근 슬래브에는 불필요할 수 있음).
    우마근 길이 = 2H + 0.8m, 우마고정근 길이 = 2H, 우마근 개소 = 순면적/1.5²(1.5m 그리드),
    우마고정근 개소 = 우마근 개소의 50%.
    """
    size = (general_spec or {}).get("chair_bar_size")
    H = (general_spec or {}).get("chair_bar_height_m")
    if not size or not isinstance(H, (int, float)) or H <= 0 or net_area_m2 <= 0:
        return 0.0, None
    chair_count = math.ceil(net_area_m2 / (1.5 * 1.5))
    fix_count = math.ceil(chair_count * 0.5)
    total_len = chair_count * (2 * H + 0.8) + fix_count * (2 * H)
    wt, err = rebar_weight(size, total_len)
    if err:
        return 0.0, err
    return wt, None


def calc_foundations(items, general_spec=None):
    """기초: 콘크리트 = L x W x T x 개수, 거푸집 = 둘레 x 두께 x 개수(측면만)
    철근중량 = 저판 하부 2방향 배근 (L방향 스팬에 W방향 간격으로 놓인 바 + 그 반대)
      + 도웰바(기둥/벽체 연결용 정착철근, dowel_bar_size/dowel_bar_count가 있을 때만)
    ※ 개별기초(독립기초) 기준 근사치입니다. 상부근은 미반영.
    ※ 스팬이 철근 장대길이(STOCK_BAR_LENGTH_M)를 넘으면 이음 반영 (매트기초 등 대형 기초 대비).
    ※ 도웰바 길이 = 하부정착길이 + 기초두께(T) + 이음길이 (SK에코플랜트 매뉴얼의
      "이음보정: 모든 수직부재(기둥,옹벽) FT부분 철근물량은 (하부정착길이+기초두께+이음길이)로
      적용" 공식을 그대로 반영). dowel_has_hook이 true면 정착길이는 표준갈고리 공식 사용.
    """
    concrete = 0.0
    formwork = 0.0
    rebar_total = 0.0
    warnings = []
    for it in items:
        L, W, T = it.get("length_m"), it.get("width_m"), it.get("thickness_m")
        mark = it.get("mark", "?")
        n = _resolve_count(it, "기초", mark, warnings)
        if n is None:
            continue
        if not all(isinstance(v, (int, float)) for v in (L, W, T)):
            warnings.append(f"기초 {mark}: 치수 누락으로 계산 제외")
            continue
        concrete += L * W * T * n
        formwork += 2 * (L + W) * T * n

        size = it.get("rebar_size")
        spacing = it.get("rebar_spacing_m")
        if size and spacing:
            bars_along_w = _spacing_bar_count(W, spacing)  # L방향으로 뻗는 바, W폭에 걸쳐 spacing 간격 배치
            bars_along_l = _spacing_bar_count(L, spacing)  # W방향으로 뻗는 바, L폭에 걸쳐 spacing 간격 배치

            l_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)
            w_splices = max(0, math.ceil(W / STOCK_BAR_LENGTH_M) - 1)
            splice_len, splice_src = get_splice_length(size, general_spec, category="기초")

            if (l_splices or w_splices) and splice_src == EXCLUDED_SOURCE:
                warnings.append(_excluded_note("기초", mark, size, "저판 하부 2방향 배근", ["이음길이"]))
            else:
                len_l = L + l_splices * splice_len
                len_w = W + w_splices * splice_len
                total_len = (bars_along_w * len_l + bars_along_l * len_w) * n

                wt, err = rebar_weight(size, total_len)
                if err:
                    warnings.append(f"기초 {mark}: {err}")
                else:
                    rebar_total += wt
                    warnings.append(f"기초 {mark}: 저판 하부 2방향 배근 근사치 (상부근은 미반영)")
                    if (l_splices or w_splices) and splice_src == "추정값(확인필요)":
                        warnings.append(f"기초 {mark}: {size} 이음길이를 구조일반사항에서 못 찾아 {splice_len}m로 추정했습니다 — 확인 필요")
        else:
            warnings.append(f"기초 {mark}: 철근 규격/간격 정보가 없어 철근량 계산에서 제외했습니다 — 기초일람표/기초상세도 확인 필요")

        dowel_size = it.get("dowel_bar_size")
        dowel_count = it.get("dowel_bar_count")
        if dowel_size and dowel_count:
            dowel_hook = bool(it.get("dowel_has_hook"))
            d_anchor_len, d_anchor_src = get_anchorage_length(dowel_size, general_spec, top_bar=False, hook=dowel_hook, category="기초")
            d_splice_len, d_splice_src = get_splice_length(dowel_size, general_spec, top_bar=False, category="기초")
            if d_anchor_src == EXCLUDED_SOURCE or d_splice_src == EXCLUDED_SOURCE:
                parts = []
                if d_splice_src == EXCLUDED_SOURCE:
                    parts.append("이음길이")
                if d_anchor_src == EXCLUDED_SOURCE:
                    parts.append("정착길이")
                warnings.append(_excluded_note("기초", mark, dowel_size, "도웰바", parts))
            else:
                dowel_len_per_bar = d_anchor_len + T + d_splice_len
                wt, err = rebar_weight(dowel_size, dowel_len_per_bar * dowel_count * n)
                if err:
                    warnings.append(f"기초 {mark}: 도웰바 {err}")
                else:
                    rebar_total += wt
                    warnings.append(
                        f"기초 {mark}: 도웰바(기둥/벽체 연결 정착철근) {dowel_count}가닥 × {n}개소 반영 "
                        f"(정착{d_anchor_len}m + 기초두께{T}m + 이음{d_splice_len}m)"
                        + (" [갈고리 정착]" if dowel_hook else "")
                    )
                    if d_anchor_src == "추정값(확인필요)":
                        warnings.append(f"기초 {mark}: 도웰바 {dowel_size} 정착길이를 구조일반사항에서 못 찾아 {d_anchor_len}m로 추정했습니다 — 확인 필요")
                    if d_splice_src == "추정값(확인필요)":
                        warnings.append(f"기초 {mark}: 도웰바 {dowel_size} 이음길이를 구조일반사항에서 못 찾아 {d_splice_len}m로 추정했습니다 — 확인 필요")
        else:
            warnings.append(f"기초 {mark}: 도웰바(기둥/벽체 연결 정착철근) 정보가 없어 계산에서 제외했습니다 — 기초상세도 확인 필요")

    return round(concrete, 3), round(formwork, 3), round(rebar_total, 2), warnings


def calc_columns(items, general_spec=None, fallback_height_m=None):
    """기둥: 콘크리트 = 단면적 x 층고 x 개수, 거푸집 = 둘레 x 층고 x 개수
    주철근 중량 = (층고 + 이음길이 + 정착길이) x 개수 x 단위중량 x 부재개수
      ※ 기둥 주철근은 관행상 층마다 1개소 이음(하부에서 겹침)이 들어간다고 가정
      ※ 하부(기초/하부 부재 접합부) 1개소 정착길이를 근사 반영 (상부 정착은 미반영)
      ※ has_hook=true면 하부 정착에 표준갈고리 공식 사용 (좁은 기초 깊이 등)
      ※ floor_repeat_count(기준층 반복)가 적용된 항목은 count가 이미 "1개 층 기준 개수 ×
        반복 층수"로 부풀려져 있다 — 정착길이는 기둥 1개 위치가 여러 층을 관통하는 연속
        부재 전체에서 딱 1번(기초 접합부)만 필요한데, count에 비례해서 그대로 곱하면 반복
        층수만큼 정착길이가 중복 반영된다. 그래서 정착길이 항만 _floor_repeat 배율로 나눠
        되돌린다 — 층마다 반복되는 이음길이(splice_len)는 실제로 층마다 있으므로 그대로 둔다.
    띠철근 중량 = (층고/간격) x 둘레(근사) x 단위중량 x 부재개수 (이음 없음, 개별 폐합 형상)
    ※ height_m이 없으면 fallback_height_m(입면/단면도에서 읽은 층고)으로 대체 시도.
    """
    concrete = 0.0
    formwork = 0.0
    rebar_total = 0.0
    warnings = []
    for it in items:
        w, d, h = it.get("width_m"), it.get("depth_m"), it.get("height_m")
        mark = it.get("mark", "?")
        n = _resolve_count(it, "기둥", mark, warnings)
        if n is None:
            continue
        if not isinstance(h, (int, float)) and fallback_height_m:
            h = fallback_height_m
            warnings.append(f"기둥 {mark}: 층고 정보 없어 입면/단면도 층고값 {h}m로 대체 사용 — 확인 필요")
        if not all(isinstance(v, (int, float)) for v in (w, d, h)):
            warnings.append(f"기둥 {mark}: 치수 누락으로 계산 제외")
            continue
        concrete += w * d * h * n
        perimeter = 2 * (w + d)
        formwork += perimeter * h * n

        layers = _valid_rebar_layers(it, "기둥", mark, warnings)
        if layers:
            # ── 세분화 배근(rebar_layers) 경로 — 사용자 제공 철근참조자료 기준으로
            # MAIN BAR를 그룹(모서리근/중간근 등)별로, HOOP을 단부/중앙부 구간별로 나눠 반영 ──
            main_layers = [l for l in layers if l.get("role") == "주근"]
            hoop_layers = [l for l in layers if l.get("role") in ("후프", "타이")]
            floor_repeat = it.get("_floor_repeat", 1) or 1
            if main_layers:
                for layer in main_layers:
                    size, count = layer["size"], layer.get("count")
                    if not count:
                        continue
                    hook = bool(layer.get("has_hook")) if layer.get("has_hook") is not None else bool(it.get("has_hook"))
                    splice_len, splice_src = get_splice_length(size, general_spec, category="기둥")
                    anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=False, hook=hook, category="기둥")
                    if splice_src == EXCLUDED_SOURCE or anchor_src == EXCLUDED_SOURCE:
                        parts = []
                        if splice_src == EXCLUDED_SOURCE:
                            parts.append("이음길이")
                        if anchor_src == EXCLUDED_SOURCE:
                            parts.append("정착길이")
                        warnings.append(_excluded_note("기둥", mark, size, f"주근({layer.get('note') or '그룹'})", parts))
                        continue
                    length_per_bar = h + splice_len + anchor_len / floor_repeat
                    wt, err = rebar_weight(size, length_per_bar * count * n)
                    if err:
                        warnings.append(f"기둥 {mark}: {err}")
                    else:
                        rebar_total += wt
                        if splice_src == "추정값(확인필요)" or anchor_src == "추정값(확인필요)":
                            warnings.append(f"기둥 {mark}: 주근({layer.get('note') or size}) 이음/정착길이 일부를 추정값으로 반영했습니다 — 확인 필요")
            else:
                warnings.append(f"기둥 {mark}: 세분화배근에 주근(역할=주근) 항목이 없어 주철근량 계산에서 제외했습니다")

            if hoop_layers:
                for layer in hoop_layers:
                    size, spacing = layer["size"], layer.get("spacing_m")
                    if not spacing:
                        continue
                    zone = layer.get("zone")
                    if zone == "단부":
                        seg_len = _zone_segment_length_m(layer, h, 0.25)
                    elif zone == "중앙부":
                        seg_len = _zone_segment_length_m(layer, h, 0.5)
                    else:
                        seg_len = h
                    num = _spacing_bar_count(seg_len, spacing) * n
                    wt, err = rebar_weight(size, num * perimeter)
                    if err:
                        warnings.append(f"기둥 {mark}: {err}")
                    else:
                        rebar_total += wt
                        if not layer.get("zone_length_m") and zone in ("단부", "중앙부"):
                            warnings.append(f"기둥 {mark}: 후프/타이({zone}) 구간길이를 확인 못해 층고의 {'25%' if zone=='단부' else '50%'}로 근사했습니다 — 정확한 구간길이 확인 권장")
            else:
                warnings.append(f"기둥 {mark}: 세분화배근에 후프/타이 항목이 없어 띠철근량 계산에서 제외했습니다")
        else:
            # ── 기존(레거시) 대표 철근 1세트 경로 — 세분화 배근이 없을 때의 하위호환 계산 ──
            main_size = it.get("main_rebar_size")
            main_count = it.get("main_rebar_count")
            if main_size and main_count:
                hook = bool(it.get("has_hook"))
                splice_len, splice_src = get_splice_length(main_size, general_spec, category="기둥")
                anchor_len, anchor_src = get_anchorage_length(main_size, general_spec, top_bar=False, hook=hook, category="기둥")
                if splice_src == EXCLUDED_SOURCE or anchor_src == EXCLUDED_SOURCE:
                    parts = []
                    if splice_src == EXCLUDED_SOURCE:
                        parts.append("이음길이")
                    if anchor_src == EXCLUDED_SOURCE:
                        parts.append("정착길이")
                    warnings.append(_excluded_note("기둥", mark, main_size, "주철근", parts))
                else:
                    floor_repeat = it.get("_floor_repeat", 1) or 1
                    # anchor_len(정착)은 반복 배율로 나눠서, count(=원래 개수×반복층수)를 곱했을 때
                    # 정착길이 총합이 "반복 층수와 무관하게 원래 개수만큼"만 나오게 한다 — 정착은
                    # 부재 전체에서 1번뿐이라서다. splice_len은 층마다 실제로 있으므로 그대로 둔다.
                    length_per_bar = h + splice_len + anchor_len / floor_repeat  # 층당 1개소 이음 + 하부 1개소 정착 가정
                    wt, err = rebar_weight(main_size, length_per_bar * main_count * n)
                    if err:
                        warnings.append(f"기둥 {mark}: {err}")
                    else:
                        rebar_total += wt
                        if splice_src == "추정값(확인필요)":
                            warnings.append(f"기둥 {mark}: {main_size} 이음길이를 구조일반사항에서 못 찾아 {splice_len}m로 추정했습니다 — 확인 필요")
                        if anchor_src == "추정값(확인필요)":
                            warnings.append(f"기둥 {mark}: {main_size} 정착길이를 구조일반사항에서 못 찾아 {anchor_len}m로 추정했습니다(하부 1개소 가정) — 확인 필요")
            else:
                warnings.append(f"기둥 {mark}: 주철근 규격/개수 정보가 없어 주철근량 계산에서 제외했습니다 — 기둥일람표 확인 필요")

            tie_size = it.get("tie_rebar_size")
            tie_spacing = it.get("tie_spacing_m")
            if tie_size and tie_spacing:
                num_ties = _spacing_bar_count(h, tie_spacing) * n
                wt, err = rebar_weight(tie_size, num_ties * perimeter)
                if err:
                    warnings.append(f"기둥 {mark}: {err}")
                else:
                    rebar_total += wt
            else:
                warnings.append(f"기둥 {mark}: 띠철근 규격/간격 정보가 없어 띠철근량 계산에서 제외했습니다 — 기둥일람표 확인 필요")

    return round(concrete, 3), round(formwork, 3), round(rebar_total, 2), warnings


def calc_beams(items, general_spec=None):
    """보: 콘크리트 = 단면적 x 길이 x 개수, 거푸집 = (폭 + 2xD) x 길이 x 개수 (바닥+양측면)
    주철근 중량 = (길이 + 이음길이x이음횟수 + 정착길이x2) x 개수 x 단위중량
      ※ 정척 철근길이(STOCK_BAR_LENGTH_M)를 넘는 길이만 이음횟수 발생한다고 가정
      ※ 양단(기둥/벽체 접합부) 각 1개소씩 정착길이를 근사 반영
      ※ is_top_bar=true면 상부철근 계수(1.3배) 적용, has_hook=true면 정착에 표준갈고리 공식 사용
    스터럽 중량 = (길이/간격) x 스터럽둘레 x 단위중량 (이음 없음)
    """
    concrete = 0.0
    formwork = 0.0
    rebar_total = 0.0
    warnings = []
    for it in items:
        w, d, L = it.get("width_m"), it.get("depth_m"), it.get("length_m")
        mark = it.get("mark", "?")
        n = _resolve_count(it, "보", mark, warnings)
        if n is None:
            continue
        if not all(isinstance(v, (int, float)) for v in (w, d, L)):
            warnings.append(f"보 {mark}: 치수 누락으로 계산 제외")
            continue
        concrete += w * d * L * n
        formwork += (w + 2 * d) * L * n

        layers = _valid_rebar_layers(it, "보", mark, warnings)
        stirrup_len = 2 * (w + d)
        if layers:
            # ── 세분화 배근 경로: 주근을 상/하부 + 단부/중앙부로, 스터럽을 구간별 간격으로 반영 ──
            main_layers = [l for l in layers if l.get("role") == "주근"]
            stirrup_layers = [l for l in layers if l.get("role") == "스터럽"]
            if main_layers:
                for layer in main_layers:
                    size, count = layer["size"], layer.get("count")
                    if not count:
                        continue
                    top_bar = (layer.get("position") == "상부") if layer.get("position") else bool(it.get("is_top_bar"))
                    hook = bool(layer.get("has_hook")) if layer.get("has_hook") is not None else bool(it.get("has_hook"))
                    zone = layer.get("zone")
                    label = f"주근({layer.get('position') or '?'}{'/' + zone if zone else ''})"
                    if zone == "단부":
                        # 단부 구간에만 배치되는 감소(컷오프)근 근사 — 짧은 구간이라 이음/정착은
                        # 반영하지 않는다(부재 전체 정착은 연속되는 중앙부/정착 그룹 쪽에서 처리).
                        seg_len = _zone_segment_length_m(layer, L, 0.25)
                        wt, err = rebar_weight(size, seg_len * count * n)
                        if err:
                            warnings.append(f"보 {mark}: {err}")
                        else:
                            rebar_total += wt
                            if not layer.get("zone_length_m"):
                                warnings.append(f"보 {mark}: {label} 구간길이를 확인 못해 전체길이의 25%로 근사했습니다 — 정확한 구간길이 확인 권장")
                    else:
                        num_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)
                        splice_len, splice_src = get_splice_length(size, general_spec, top_bar=top_bar, category="보")
                        anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=top_bar, hook=hook, category="보")
                        if (anchor_src == EXCLUDED_SOURCE) or (num_splices and splice_src == EXCLUDED_SOURCE):
                            parts = []
                            if num_splices and splice_src == EXCLUDED_SOURCE:
                                parts.append("이음길이")
                            if anchor_src == EXCLUDED_SOURCE:
                                parts.append("정착길이")
                            warnings.append(_excluded_note("보", mark, size, label, parts))
                            continue
                        length_per_bar = L + num_splices * splice_len + 2 * anchor_len
                        wt, err = rebar_weight(size, length_per_bar * count * n)
                        if err:
                            warnings.append(f"보 {mark}: {err}")
                        else:
                            rebar_total += wt
                            if (num_splices and splice_src == "추정값(확인필요)") or anchor_src == "추정값(확인필요)":
                                warnings.append(f"보 {mark}: {label} 이음/정착길이 일부를 추정값으로 반영했습니다 — 확인 필요")
            else:
                warnings.append(f"보 {mark}: 세분화배근에 주근(역할=주근) 항목이 없어 주철근량 계산에서 제외했습니다")

            if stirrup_layers:
                for layer in stirrup_layers:
                    size, spacing = layer["size"], layer.get("spacing_m")
                    if not spacing:
                        continue
                    zone = layer.get("zone")
                    if zone == "단부":
                        seg_len = _zone_segment_length_m(layer, L, 0.25)
                    elif zone == "중앙부":
                        seg_len = _zone_segment_length_m(layer, L, 0.5)
                    else:
                        seg_len = L
                    num = _spacing_bar_count(seg_len, spacing) * n
                    wt, err = rebar_weight(size, num * stirrup_len)
                    if err:
                        warnings.append(f"보 {mark}: {err}")
                    else:
                        rebar_total += wt
                        if not layer.get("zone_length_m") and zone in ("단부", "중앙부"):
                            warnings.append(f"보 {mark}: 스터럽({zone}) 구간길이를 확인 못해 부재길이의 {'25%' if zone=='단부' else '50%'}로 근사했습니다 — 확인 권장")
            else:
                warnings.append(f"보 {mark}: 세분화배근에 스터럽 항목이 없어 스터럽량 계산에서 제외했습니다")
        else:
            # ── 기존(레거시) 대표 철근 1세트 경로 ──
            main_size = it.get("main_rebar_size")
            main_count = it.get("main_rebar_count")
            if main_size and main_count:
                top_bar = bool(it.get("is_top_bar"))
                hook = bool(it.get("has_hook"))
                num_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)
                splice_len, splice_src = get_splice_length(main_size, general_spec, top_bar=top_bar, category="보")
                anchor_len, anchor_src = get_anchorage_length(main_size, general_spec, top_bar=top_bar, hook=hook, category="보")
                if (anchor_src == EXCLUDED_SOURCE) or (num_splices and splice_src == EXCLUDED_SOURCE):
                    parts = []
                    if num_splices and splice_src == EXCLUDED_SOURCE:
                        parts.append("이음길이")
                    if anchor_src == EXCLUDED_SOURCE:
                        parts.append("정착길이")
                    warnings.append(_excluded_note("보", mark, main_size, "주철근", parts))
                else:
                    length_per_bar = L + num_splices * splice_len + 2 * anchor_len  # 양단 각 1개소 정착 가정
                    wt, err = rebar_weight(main_size, length_per_bar * main_count * n)
                    if err:
                        warnings.append(f"보 {mark}: {err}")
                    else:
                        rebar_total += wt
                        if num_splices and splice_src == "추정값(확인필요)":
                            warnings.append(f"보 {mark}: {main_size} 이음길이를 구조일반사항에서 못 찾아 {splice_len}m로 추정했습니다{'(상부근 1.3배 반영)' if top_bar else ''} — 확인 필요")
                        if anchor_src == "추정값(확인필요)":
                            warnings.append(f"보 {mark}: {main_size} 정착길이를 구조일반사항에서 못 찾아 {anchor_len}m로 추정했습니다(양단 각 1개소 가정{'상부근 1.3배' if top_bar else ''}{', 갈고리' if hook else ''}) — 확인 필요")
            else:
                warnings.append(f"보 {mark}: 주철근 규격/개수 정보가 없어 주철근량 계산에서 제외했습니다 — 보일람표 확인 필요")

            stirrup_size = it.get("stirrup_size")
            stirrup_spacing = it.get("stirrup_spacing_m")
            if stirrup_size and stirrup_spacing:
                num_stirrups = _spacing_bar_count(L, stirrup_spacing) * n
                wt, err = rebar_weight(stirrup_size, num_stirrups * stirrup_len)
                if err:
                    warnings.append(f"보 {mark}: {err}")
                else:
                    rebar_total += wt
            else:
                warnings.append(f"보 {mark}: 스터럽 규격/간격 정보가 없어 스터럽량 계산에서 제외했습니다 — 보일람표 확인 필요")

    return round(concrete, 3), round(formwork, 3), round(rebar_total, 2), warnings


def _opening_area_m2(it, mark, member_label, warnings):
    """
    부재(walls/slabs) 항목의 openings 배열에서 총 개구부 면적(m²)을 구한다.
    openings 키가 아예 없으면(=AI가 개구부 여부를 확인하지 못함) 과다산출 경고를 남기고,
    빈 배열([])이면 "개구부 없음"으로 확인된 것이므로 경고 없이 0을 반환한다.
    """
    if "openings" not in it:
        warnings.append(
            f"{member_label} {mark}: 개구부(문/샤프트 등) 정보를 확인하지 못해 전체 면적으로 계산했습니다 "
            "— 실제보다 과다산출됐을 수 있습니다"
        )
        return 0.0

    openings = it.get("openings") or []
    total = 0.0
    for op in openings:
        w, h, cnt = op.get("width_m"), op.get("height_m"), op.get("count", 1) or 1
        if isinstance(w, (int, float)) and isinstance(h, (int, float)):
            total += w * h * cnt
        else:
            warnings.append(f"{member_label} {mark}: 개구부 '{op.get('label','?')}' 치수 확인 필요 — 이번 계산에서 제외")
    if total > 0:
        # rebar_layers에 "개구부보강근" 역할 항목이 실제로 있으면(전단벽 세분화배근 경로에서
        # 별도로 반영됨) "미반영" 문구가 모순돼 보이므로 안내 문구를 다르게 남긴다.
        has_opening_reinforcement = any(
            isinstance(l, dict) and l.get("role") == "개구부보강근"
            for l in (it.get("rebar_layers") or [])
        )
        note = (
            "(개구부보강근은 세분화배근 항목으로 별도 반영됨)" if has_opening_reinforcement
            else "(인방보 등 개구부 보강철근은 미반영 — 별도 확인 필요)"
        )
        warnings.append(f"{member_label} {mark}: 개구부 {total}㎡ 콘크리트/거푸집에서 차감함 {note}")
    return total


def calc_slabs(items, general_spec=None):
    """슬래브: 콘크리트 = (면적 - 개구부면적) x 두께, 거푸집 = (면적 - 개구부면적) (바닥 서포트 기준)
    철근중량 = (면적/간격) x 평균부재길이(sqrt(면적)로 근사) x 단위중량 x 2방향
    ※ 슬래브 철근은 근사치입니다 — 실제 배근도의 방향별 스팬 길이로 보정 필요
    ※ 개구부 면적은 openings 필드(도면 판독) 기준으로 차감. 철근량은 개구부 미반영 근사치.
    ※ 스팬이 철근 장대 길이(STOCK_BAR_LENGTH_M)를 넘으면 이음(splice)이 필요하다 — 보/벽체와
      동일한 방식으로 이음 개수·이음길이를 반영한다 (이전 버전은 이 부분이 누락돼 있었음).
    ※ 스팬 양단(보/벽체 접합부) 각 1개소씩 정착길이를 근사 반영한다.
    ※ is_top_bar=true면 상부철근(부모멘트근) 계수 적용, has_hook=true면 양단 정착에 표준갈고리 공식 사용.
    """
    concrete = 0.0
    formwork = 0.0
    rebar_total = 0.0
    warnings = []
    for it in items:
        area, T = it.get("area_m2"), it.get("thickness_m")
        mark = it.get("mark", "?")
        n = _resolve_count(it, "슬래브", mark, warnings)
        if n is None:
            continue
        if not all(isinstance(v, (int, float)) for v in (area, T)):
            warnings.append(f"슬래브 {mark}: 치수 누락으로 계산 제외")
            continue

        opening_area = _opening_area_m2(it, mark, "슬래브", warnings)
        net_area = max(0.0, area - opening_area)

        concrete += net_area * T * n
        formwork += net_area * n

        avg_span = math.sqrt(net_area) if net_area > 0 else 0.0
        layers = _valid_rebar_layers(it, "슬래브", mark, warnings)
        if layers:
            # ── 세분화 배근 경로: X/Y 방향 + 상/하부 + 주열대/중간대를 각각 별도 배근군으로
            # 반영한다. 실제 방향별 스팬 길이 데이터는 없으므로(면적만 있음) 기존과 동일하게
            # sqrt(면적)을 그 방향의 근사 스팬으로 쓴다 — 주열대/중간대 구분이 있으면 그
            # 스트립이 슬래브 폭의 절반을 담당한다고 근사(직접설계법의 대략적인 배분 관행)한다.
            main_layers = [l for l in layers if l.get("role") == "주근"]
            if main_layers:
                for layer in main_layers:
                    size, spacing = layer["size"], layer.get("spacing_m")
                    if not spacing:
                        continue
                    top_bar = (layer.get("position") == "상부") if layer.get("position") else bool(it.get("is_top_bar"))
                    hook = bool(layer.get("has_hook")) if layer.get("has_hook") is not None else bool(it.get("has_hook"))
                    strip = layer.get("strip")
                    direction = layer.get("direction") or "?"
                    label = f"주근({direction}방향/{layer.get('position') or '?'}{'/' + strip if strip else ''})"
                    perp_width = avg_span * 0.5 if strip else avg_span
                    num_bars = _spacing_bar_count(perp_width, spacing) if perp_width else 0
                    if not num_bars:
                        continue
                    num_splices = max(0, math.ceil(avg_span / STOCK_BAR_LENGTH_M) - 1) if avg_span else 0
                    splice_len, splice_src = (0.0, None)
                    if num_splices:
                        splice_len, splice_src = get_splice_length(size, general_spec, top_bar=top_bar, category="슬래브")
                    anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=top_bar, hook=hook, category="슬래브")
                    if (anchor_src == EXCLUDED_SOURCE) or (num_splices and splice_src == EXCLUDED_SOURCE):
                        parts = []
                        if num_splices and splice_src == EXCLUDED_SOURCE:
                            parts.append("이음길이")
                        if anchor_src == EXCLUDED_SOURCE:
                            parts.append("정착길이")
                        warnings.append(_excluded_note("슬래브", mark, size, label, parts))
                        continue
                    length_per_bar = avg_span + num_splices * splice_len + 2 * anchor_len
                    total_len = num_bars * length_per_bar * n
                    wt, err = rebar_weight(size, total_len)
                    if err:
                        warnings.append(f"슬래브 {mark}: {err}")
                    else:
                        rebar_total += wt
                        if (num_splices and splice_src == "추정값(확인필요)") or anchor_src == "추정값(확인필요)":
                            warnings.append(f"슬래브 {mark}: {label} 이음/정착길이 일부를 추정값으로 반영했습니다 — 확인 필요")
                if not any(l.get("direction") for l in main_layers):
                    warnings.append(f"슬래브 {mark}: 세분화배근에 방향(X/Y) 구분이 없어 스팬 근사치가 부정확할 수 있습니다")
            else:
                warnings.append(f"슬래브 {mark}: 세분화배근에 주근(역할=주근) 항목이 없어 철근량 계산에서 제외했습니다")
        else:
            # ── 기존(레거시) 대표 철근 1세트 경로 ──
            size = it.get("rebar_size")
            spacing = it.get("rebar_spacing_m")
            if size and spacing:
                top_bar = bool(it.get("is_top_bar"))
                hook = bool(it.get("has_hook"))
                num_bars = _spacing_bar_count(avg_span, spacing) if avg_span else 0

                num_splices = max(0, math.ceil(avg_span / STOCK_BAR_LENGTH_M) - 1) if avg_span else 0
                splice_len, splice_src = (0.0, None)
                if num_splices:
                    splice_len, splice_src = get_splice_length(size, general_spec, top_bar=top_bar, category="슬래브")
                anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=top_bar, hook=hook, category="슬래브")

                if (anchor_src == EXCLUDED_SOURCE) or (num_splices and splice_src == EXCLUDED_SOURCE):
                    parts = []
                    if num_splices and splice_src == EXCLUDED_SOURCE:
                        parts.append("이음길이")
                    if anchor_src == EXCLUDED_SOURCE:
                        parts.append("정착길이")
                    warnings.append(_excluded_note("슬래브", mark, size, "주철근", parts))
                else:
                    length_per_bar = avg_span
                    if num_splices:
                        length_per_bar = avg_span + num_splices * splice_len
                        warnings.append(
                            f"슬래브 {mark}: 스팬({round(avg_span,2)}m)이 철근 장대길이({STOCK_BAR_LENGTH_M}m)를 넘어 "
                            f"가닥당 이음 {num_splices}개소 반영함"
                        )
                        if splice_src == "추정값(확인필요)":
                            warnings.append(f"슬래브 {mark}: {size} 이음길이를 구조일반사항에서 못 찾아 {splice_len}m로 추정했습니다 — 확인 필요")

                    length_per_bar += 2 * anchor_len  # 스팬 양단 각 1개소 정착 가정
                    if anchor_src == "추정값(확인필요)":
                        warnings.append(f"슬래브 {mark}: {size} 정착길이를 구조일반사항에서 못 찾아 {anchor_len}m로 추정했습니다(양단 각 1개소 가정{', 상부근' if top_bar else ''}{', 갈고리' if hook else ''}) — 확인 필요")

                    total_len = num_bars * length_per_bar * 2 * n  # 2방향(X,Y) 근사, 양방향 모두 이음+정착 반영
                    wt, err = rebar_weight(size, total_len)
                    if err:
                        warnings.append(f"슬래브 {mark}: {err}")
                    else:
                        rebar_total += wt
                        warnings.append(f"슬래브 {mark}: 철근량은 정방향 근사치이므로 배근도 방향별 스팬으로 재검증 권장")
            else:
                warnings.append(f"슬래브 {mark}: 철근 규격/간격 정보가 없어 철근량 계산에서 제외했습니다 — 슬래브배근도 확인 필요")

        chair_wt, chair_err = _chair_bar_weight(net_area, general_spec)
        if chair_err:
            warnings.append(f"슬래브 {mark}: 우마근 {chair_err}")
        elif chair_wt:
            rebar_total += chair_wt * n
            warnings.append(f"슬래브 {mark}: 우마근/우마고정근 {round(chair_wt*n,2)}kg 반영 (1.5m 그리드 근사)")

    return round(concrete, 3), round(formwork, 3), round(rebar_total, 2), warnings


def calc_walls(items, general_spec=None, fallback_height_m=None):
    """전단벽: 콘크리트 = (길이x높이 - 개구부면적) x 두께, 거푸집 = (길이x높이 - 개구부면적) x 2(양면)
    수직철근 = (높이 + 이음길이 + 정착길이) x 개소수 x 단위중량 (층당 1개소 이음 + 하부 1개소 정착 가정)
    수평철근 = (길이 + 이음길이x이음횟수) x 개소수 x 단위중량 (정척 길이 초과시 이음)
    ※ 개구부 면적은 openings 필드(도면 판독) 기준으로 차감. 철근량은 개구부 미반영 근사치
      (개구부 주변 인방보/보강철근은 별도 반영 필요).
    ※ height_m이 없으면 fallback_height_m(입면/단면도에서 읽은 층고)으로 대체 시도.
    ※ has_hook=true면 수직근 하부 정착에 표준갈고리 공식 사용.
    ※ end_condition("일자형"/"T자형"/"모서리")이 있으면 SK에코플랜트 매뉴얼 기준 벽체
      단부보강근(추가 수직근 + U형바/C형바)을 근사 반영합니다.
    ※ is_single_face 처리: 대부분의 구조 전단벽(두께 180mm 이상)은 벽 두께 방향으로 앞뒤
      두 겹(양면) 배근이 표준이고, 도면에 rebar_size/rebar_spacing_m이 한 줄로만 적혀 있어도
      실제로는 양면 모두에 같은 사양이 들어가는 경우가 많습니다. 그래서 is_single_face가
      명시적으로 true(도면에서 "단면"으로 확인됨)가 아닌 이상 기본적으로 양면(2배)으로
      계산합니다 — 실제 도면으로 검증되기 전까지는 가정치이므로 notes로 안내합니다.
    """
    concrete = 0.0
    formwork = 0.0
    rebar_total = 0.0
    warnings = []
    assumed_double_face_count = 0
    for it in items:
        L, H, T = it.get("length_m"), it.get("height_m"), it.get("thickness_m")
        mark = it.get("mark", "?")
        n = _resolve_count(it, "전단벽", mark, warnings)
        if n is None:
            continue
        if not isinstance(H, (int, float)) and fallback_height_m:
            H = fallback_height_m
            warnings.append(f"전단벽 {mark}: 층고 정보 없어 입면/단면도 층고값 {H}m로 대체 사용 — 확인 필요")
        if not all(isinstance(v, (int, float)) for v in (L, H, T)):
            warnings.append(f"전단벽 {mark}: 치수 누락으로 계산 제외")
            continue

        opening_area = _opening_area_m2(it, mark, "전단벽", warnings)
        net_face_area = max(0.0, L * H - opening_area)

        concrete += net_face_area * T * n
        formwork += 2 * net_face_area * n

        layers = _valid_rebar_layers(it, "전단벽", mark, warnings)
        if layers:
            # ── 세분화 배근 경로: 수직근/수평근을 각각 별도 간격으로, 단부·모서리·교차부·
            # 개구부 보강근을 사용자가 직접 지정한 가닥수로 반영한다(기존 end_condition
            # 기반 SK매뉴얼 근사식 대신 도면에서 직접 읽은 값을 우선 사용).
            hook = bool(it.get("has_hook"))
            floor_repeat = it.get("_floor_repeat", 1) or 1
            v_layers = [l for l in layers if l.get("role") == "수직근"]
            h_layers = [l for l in layers if l.get("role") == "수평근"]
            reinforce_layers = [l for l in layers if l.get("role") in ("단부보강근", "모서리보강근", "교차부보강근")]
            opening_layers = [l for l in layers if l.get("role") == "개구부보강근"]

            if v_layers:
                for layer in v_layers:
                    size, spacing = layer["size"], layer.get("spacing_m")
                    if not spacing:
                        continue
                    l_hook = bool(layer.get("has_hook")) if layer.get("has_hook") is not None else hook
                    splice_len, splice_src = get_splice_length(size, general_spec, category="전단벽")
                    anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=False, hook=l_hook, category="전단벽")
                    if splice_src == EXCLUDED_SOURCE or anchor_src == EXCLUDED_SOURCE:
                        parts = []
                        if splice_src == EXCLUDED_SOURCE:
                            parts.append("이음길이")
                        if anchor_src == EXCLUDED_SOURCE:
                            parts.append("정착길이")
                        warnings.append(_excluded_note("전단벽", mark, size, "수직근", parts))
                        continue
                    vertical_bars = _spacing_bar_count(L, spacing)
                    total_len = vertical_bars * (H + splice_len + anchor_len / floor_repeat) * n
                    wt, err = rebar_weight(size, total_len)
                    if err:
                        warnings.append(f"전단벽 {mark}: {err}")
                    else:
                        rebar_total += wt
                        if splice_src == "추정값(확인필요)" or anchor_src == "추정값(확인필요)":
                            warnings.append(f"전단벽 {mark}: 수직근 이음/정착길이 일부를 추정값으로 반영했습니다 — 확인 필요")
            else:
                warnings.append(f"전단벽 {mark}: 세분화배근에 수직근 항목이 없어 수직철근량 계산에서 제외했습니다")

            if h_layers:
                for layer in h_layers:
                    size, spacing = layer["size"], layer.get("spacing_m")
                    if not spacing:
                        continue
                    splice_len, splice_src = get_splice_length(size, general_spec, category="전단벽")
                    if splice_src == EXCLUDED_SOURCE:
                        warnings.append(_excluded_note("전단벽", mark, size, "수평근", ["이음길이"]))
                        continue
                    horizontal_bars = _spacing_bar_count(H, spacing)
                    h_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)
                    total_len = horizontal_bars * (L + h_splices * splice_len) * n
                    wt, err = rebar_weight(size, total_len)
                    if err:
                        warnings.append(f"전단벽 {mark}: {err}")
                    else:
                        rebar_total += wt
                        if h_splices and splice_src == "추정값(확인필요)":
                            warnings.append(f"전단벽 {mark}: 수평근 이음길이를 추정값으로 반영했습니다 — 확인 필요")
            else:
                warnings.append(f"전단벽 {mark}: 세분화배근에 수평근 항목이 없어 수평철근량 계산에서 제외했습니다")

            for layer in reinforce_layers:
                size, count = layer["size"], layer.get("count")
                if not count:
                    continue
                anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=False, hook=bool(layer.get("has_hook")), category="전단벽")
                if anchor_src == EXCLUDED_SOURCE:
                    warnings.append(_excluded_note("전단벽", mark, size, layer.get("role"), ["정착길이"]))
                    continue
                seg_len = H + anchor_len / floor_repeat
                wt, err = rebar_weight(size, seg_len * count * n)
                if err:
                    warnings.append(f"전단벽 {mark}: {layer.get('role')} {err}")
                else:
                    rebar_total += wt
                    warnings.append(f"전단벽 {mark}: {layer.get('role')} {count}가닥 반영 (도면 지정값)")

            for layer in opening_layers:
                size, count = layer["size"], layer.get("count")
                if not count:
                    continue
                anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=False, hook=bool(layer.get("has_hook")), category="전단벽")
                if anchor_src == EXCLUDED_SOURCE:
                    warnings.append(_excluded_note("전단벽", mark, size, "개구부보강근", ["정착길이"]))
                    continue
                seg_len = layer.get("zone_length_m") or (2 * anchor_len)
                if not layer.get("zone_length_m"):
                    warnings.append(f"전단벽 {mark}: 개구부보강근 길이를 확인 못해 정착길이×2로 근사했습니다 — 실제 개구부 치수 기준 확인 권장")
                wt, err = rebar_weight(size, seg_len * count * n)
                if err:
                    warnings.append(f"전단벽 {mark}: 개구부보강근 {err}")
                else:
                    rebar_total += wt
                    warnings.append(f"전단벽 {mark}: 개구부보강근 {count}가닥 반영 (도면 지정값)")
        else:
            # ── 기존(레거시) 대표 철근 1세트 경로 ──
            size = it.get("rebar_size")
            spacing = it.get("rebar_spacing_m")
            if size and spacing:
                hook = bool(it.get("has_hook"))
                is_single_face = it.get("is_single_face") is True
                face_count = 1 if is_single_face else 2
                if not is_single_face:
                    assumed_double_face_count += 1
                splice_len, splice_src = get_splice_length(size, general_spec, category="전단벽")
                anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=False, hook=hook, category="전단벽")
                vertical_bars = _spacing_bar_count(L, spacing) * face_count
                horizontal_bars = _spacing_bar_count(H, spacing) * face_count
                h_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)
                floor_repeat = it.get("_floor_repeat", 1) or 1

                main_excluded = (splice_src == EXCLUDED_SOURCE) or (anchor_src == EXCLUDED_SOURCE)
                if main_excluded:
                    parts = []
                    if splice_src == EXCLUDED_SOURCE:
                        parts.append("이음길이")
                    if anchor_src == EXCLUDED_SOURCE:
                        parts.append("정착길이")
                    warnings.append(_excluded_note("전단벽", mark, size, "수직/수평 철근", parts))
                else:
                    # calc_columns와 동일한 이유 — count가 이미 반복 층수만큼 곱해져 있으므로,
                    # 정착길이(수직근 하부 1개소, 부재 전체에서 1번뿐)는 반복 배율로 나눠서 중복
                    # 반영을 막는다. 이음길이는 층마다 실제로 있어서 그대로 둔다.
                    vertical_len = vertical_bars * (H + splice_len + anchor_len / floor_repeat)  # 하부(기초 접합부) 1개소 정착
                    horizontal_len = horizontal_bars * (L + h_splices * splice_len)
                    total_len = (vertical_len + horizontal_len) * n

                    wt, err = rebar_weight(size, total_len)
                    if err:
                        warnings.append(f"전단벽 {mark}: {err}")
                    else:
                        rebar_total += wt
                        if splice_src == "추정값(확인필요)":
                            warnings.append(f"전단벽 {mark}: {size} 이음길이를 구조일반사항에서 못 찾아 {splice_len}m로 추정했습니다 — 확인 필요")
                        if anchor_src == "추정값(확인필요)":
                            warnings.append(f"전단벽 {mark}: {size} 정착길이를 구조일반사항에서 못 찾아 {anchor_len}m로 추정했습니다(수직근 하부 1개소 가정{', 갈고리' if hook else ''}) — 확인 필요")

                end_condition = it.get("end_condition")
                if end_condition:
                    extra_vertical = {"일자형": 2, "T자형": 4, "모서리": 4}.get(end_condition, 0)
                    if extra_vertical:
                        if main_excluded:
                            warnings.append(f"전단벽 {mark}: 단부보강근(수직) {size} 이음/정착길이 미확인으로 확정 전까지 계산에서 제외됩니다.")
                        else:
                            extra_len = H + splice_len + anchor_len / floor_repeat  # 위 수직근과 동일 이유
                            wt_extra, err_extra = rebar_weight(size, extra_vertical * extra_len * n)
                            if err_extra:
                                warnings.append(f"전단벽 {mark}: 단부보강근(수직) {err_extra}")
                            else:
                                rebar_total += wt_extra
                                warnings.append(
                                    f"전단벽 {mark}: 단부보강근(수직) {end_condition} 기준 +{extra_vertical}가닥 반영 "
                                    "(SK에코플랜트 매뉴얼 기준 근사)"
                                )

                    u_bar_len = 2 * 0.3 + T  # 양측 300mm 연장 + 벽두께 감싸는 구간 근사(벤딩 여유 미포함)
                    if L <= 0.6:
                        u_count, shape = horizontal_bars, "C형(편측)"
                    elif L <= 1.2:
                        u_count, shape = horizontal_bars, "U형+C형"
                    else:
                        u_count, shape = horizontal_bars * 2, "U형(양단)"
                    wt_u, err_u = rebar_weight(size, u_count * u_bar_len * n)
                    if err_u:
                        warnings.append(f"전단벽 {mark}: 단부보강근(수평) {err_u}")
                    elif wt_u:
                        rebar_total += wt_u
                        warnings.append(
                            f"전단벽 {mark}: 단부보강근(수평, {shape}) {u_count}개×{round(u_bar_len,2)}m 근사 반영 "
                            "— 실제 벤딩(절곡) 형상과 다를 수 있어 확인 필요"
                        )
            else:
                warnings.append(f"전단벽 {mark}: 철근 규격/간격 정보가 없어 철근량 계산에서 제외했습니다 — 벽체배근도 확인 필요")

    if assumed_double_face_count:
        warnings.append(
            f"전단벽 철근량: 도면에서 단면(편측) 배근이 확인되지 않은 {assumed_double_face_count}개소는 "
            "양면(두 겹) 배근으로 가정해 계산했습니다 — 실제 벽체배근도 확인 후 다르면 검토 화면에서 "
            "'단면(편측) 배근'으로 고쳐주세요."
        )

    return round(concrete, 3), round(formwork, 3), round(rebar_total, 2), warnings


def calc_stairs(items, general_spec=None):
    """계단: 경사슬래브로 근사. 콘크리트 = 폭 x 경사길이 x 두께 x 개수, 거푸집 = 폭 x 경사길이 x 개수(하부만)
    주근(경사방향) 중량 = (경사길이 + 이음 + 정착x2) x (폭/간격) x 단위중량 x 개수
    배력근(폭방향) 중량 = (폭 + 이음) x (경사길이/간격) x 단위중량 x 개수 (배력근 정보 없으면 미반영)
    ※ 계단참(landing) 별도 형상, 챌판/디딤판 요철, 계단 측판 보강근은 반영하지 않은 근사치입니다.
    ※ SK에코플랜트 매뉴얼 기준 계단 이음보정은 슬래브와 동일 계수를 적용합니다.
    """
    concrete = 0.0
    formwork = 0.0
    rebar_total = 0.0
    warnings = []
    for it in items:
        W, L, T = it.get("width_m"), it.get("length_m"), it.get("thickness_m")
        mark = it.get("mark", "?")
        n = _resolve_count(it, "계단", mark, warnings)
        if n is None:
            continue
        if not all(isinstance(v, (int, float)) for v in (W, L, T)):
            warnings.append(f"계단 {mark}: 치수 누락으로 계산 제외")
            continue
        concrete += W * L * T * n
        formwork += W * L * n  # 경사면(하부) 거푸집 기준, 측판/챌판 거푸집은 미반영

        layers = _valid_rebar_layers(it, "계단", mark, warnings)
        if layers:
            # ── 세분화 배근 경로: 주근(상/하부, 계단참 구간 별도)과 배력근을 각각 반영 ──
            main_layers = [l for l in layers if l.get("role") == "주근"]
            dist_layers = [l for l in layers if l.get("role") == "배력근"]
            if main_layers:
                for layer in main_layers:
                    size, spacing = layer["size"], layer.get("spacing_m")
                    if not spacing:
                        continue
                    top_bar = (layer.get("position") == "상부") if layer.get("position") else bool(it.get("is_top_bar"))
                    hook = bool(layer.get("has_hook")) if layer.get("has_hook") is not None else bool(it.get("has_hook"))
                    zone = layer.get("zone")
                    main_bars = _spacing_bar_count(W, spacing)
                    label = f"주근({layer.get('position') or '?'}{'/' + zone if zone else ''})"
                    if zone == "계단참":
                        seg_len = _zone_segment_length_m(layer, L, 0.25)
                        wt, err = rebar_weight(size, main_bars * seg_len * n)
                        if err:
                            warnings.append(f"계단 {mark}: {err}")
                        else:
                            rebar_total += wt
                            if not layer.get("zone_length_m"):
                                warnings.append(f"계단 {mark}: {label} 구간길이를 확인 못해 전체 경사길이의 25%로 근사했습니다 — 확인 권장")
                    else:
                        num_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)
                        splice_len, splice_src = get_splice_length(size, general_spec, top_bar=top_bar, category="계단")
                        anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=top_bar, hook=hook, category="계단")
                        if (anchor_src == EXCLUDED_SOURCE) or (num_splices and splice_src == EXCLUDED_SOURCE):
                            parts = []
                            if num_splices and splice_src == EXCLUDED_SOURCE:
                                parts.append("이음길이")
                            if anchor_src == EXCLUDED_SOURCE:
                                parts.append("정착길이")
                            warnings.append(_excluded_note("계단", mark, size, label, parts))
                            continue
                        main_len_per_bar = L + num_splices * splice_len + 2 * anchor_len
                        wt, err = rebar_weight(size, main_bars * main_len_per_bar * n)
                        if err:
                            warnings.append(f"계단 {mark}: {err}")
                        else:
                            rebar_total += wt
                            if (num_splices and splice_src == "추정값(확인필요)") or anchor_src == "추정값(확인필요)":
                                warnings.append(f"계단 {mark}: {label} 이음/정착길이 일부를 추정값으로 반영했습니다 — 확인 필요")
            else:
                warnings.append(f"계단 {mark}: 세분화배근에 주근(역할=주근) 항목이 없어 주근 계산에서 제외했습니다")

            if dist_layers:
                for layer in dist_layers:
                    size, spacing = layer["size"], layer.get("spacing_m")
                    if not spacing:
                        continue
                    dist_bars = _spacing_bar_count(L, spacing)
                    wt2, err2 = rebar_weight(size, dist_bars * W * n)
                    if err2:
                        warnings.append(f"계단 {mark}: {err2}")
                    else:
                        rebar_total += wt2
            else:
                warnings.append(f"계단 {mark}: 세분화배근에 배력근 항목이 없어 배력근 계산에서 제외했습니다")
        else:
            # ── 기존(레거시) 대표 철근 1세트 경로 ──
            size = it.get("rebar_size")
            spacing = it.get("rebar_spacing_m")
            if size and spacing:
                top_bar = bool(it.get("is_top_bar"))
                hook = bool(it.get("has_hook"))
                num_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)
                splice_len, splice_src = get_splice_length(size, general_spec, top_bar=top_bar, category="계단")
                anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=top_bar, hook=hook, category="계단")
                main_bars = _spacing_bar_count(W, spacing)
                if (anchor_src == EXCLUDED_SOURCE) or (num_splices and splice_src == EXCLUDED_SOURCE):
                    parts = []
                    if num_splices and splice_src == EXCLUDED_SOURCE:
                        parts.append("이음길이")
                    if anchor_src == EXCLUDED_SOURCE:
                        parts.append("정착길이")
                    warnings.append(_excluded_note("계단", mark, size, "주근", parts))
                else:
                    main_len_per_bar = L + num_splices * splice_len + 2 * anchor_len
                    wt, err = rebar_weight(size, main_bars * main_len_per_bar * n)
                    if err:
                        warnings.append(f"계단 {mark}: {err}")
                    else:
                        rebar_total += wt
                        if num_splices and splice_src == "추정값(확인필요)":
                            warnings.append(f"계단 {mark}: {size} 이음길이를 구조일반사항에서 못 찾아 {splice_len}m로 추정했습니다 — 확인 필요")
                        if anchor_src == "추정값(확인필요)":
                            warnings.append(f"계단 {mark}: {size} 정착길이를 구조일반사항에서 못 찾아 {anchor_len}m로 추정했습니다(양단 각 1개소 가정) — 확인 필요")

                dist_size = it.get("distribution_rebar_size")
                dist_spacing = it.get("distribution_rebar_spacing_m")
                if dist_size and dist_spacing:
                    dist_bars = _spacing_bar_count(L, dist_spacing)
                    wt2, err2 = rebar_weight(dist_size, dist_bars * W * n)
                    if err2:
                        warnings.append(f"계단 {mark}: {err2}")
                    else:
                        rebar_total += wt2
                else:
                    warnings.append(f"계단 {mark}: 배력근(폭방향) 정보가 없어 주근(경사방향)만 계산했습니다 — 계단상세도 확인 필요")
            else:
                warnings.append(f"계단 {mark}: 철근 규격/간격 정보가 없어 철근량 계산에서 제외했습니다 — 계단상세도 확인 필요")

    return round(concrete, 3), round(formwork, 3), round(rebar_total, 2), warnings


def calc_spacers(members):
    """
    간격재(스페이서) 개수(EA) 근사 산출 — SK에코플랜트 매뉴얼 산출기준.
    철근이 아니라 배근 위치를 유지하는 플라스틱/모르타르 부속재이므로 철근중량(TON)이 아닌
    개수(EA)로 별도 집계합니다.
    - 슬래브용: 순면적(개구부 차감) / 0.81 x 2  (0.9m×0.9m 그리드, 상하 2단 기준)
      ※ is_deck_slab=true인 슬래브는 데크플레이트가 철근을 직접 지지하므로 스페이서 산출에서
        제외합니다 (매뉴얼: "DECK면적은 SPACER 산출에서 제외한다").
    - 벽체용:   순면적(개구부 차감, 벽 1개 면 기준) / 0.81 x 2
    - 기둥용:   4 x 층고 / 0.9 x 개수
    - 보용:     보의 총길이(길이×개수 합) / 0.9
    """
    warnings = []
    slab_ea = 0.0
    deck_excluded_count = 0
    for it in members.get("slabs", []) or []:
        area = it.get("area_m2")
        n = _resolve_count(it, "슬래브", it.get("mark", "?"), warnings)
        if n is None or not isinstance(area, (int, float)):
            continue
        if it.get("is_deck_slab"):
            deck_excluded_count += 1
            continue
        opening_area = 0.0
        for op in it.get("openings") or []:
            w, h, cnt = op.get("width_m"), op.get("height_m"), op.get("count", 1) or 1
            if isinstance(w, (int, float)) and isinstance(h, (int, float)):
                opening_area += w * h * cnt
        net_area = max(0.0, area - opening_area)
        slab_ea += (net_area / 0.81) * 2 * n

    wall_ea = 0.0
    for it in members.get("walls", []) or []:
        L, H = it.get("length_m"), it.get("height_m")
        n = _resolve_count(it, "전단벽", it.get("mark", "?"), warnings)
        if n is None or not isinstance(L, (int, float)) or not isinstance(H, (int, float)):
            continue
        opening_area = 0.0
        for op in it.get("openings") or []:
            w, h, cnt = op.get("width_m"), op.get("height_m"), op.get("count", 1) or 1
            if isinstance(w, (int, float)) and isinstance(h, (int, float)):
                opening_area += w * h * cnt
        net_area = max(0.0, L * H - opening_area)
        wall_ea += (net_area / 0.81) * 2 * n

    column_ea = 0.0
    for it in members.get("columns", []) or []:
        h = it.get("height_m")
        n = _resolve_count(it, "기둥", it.get("mark", "?"), warnings)
        if n is None or not isinstance(h, (int, float)):
            continue
        column_ea += 4 * (h / 0.9) * n

    beam_ea = 0.0
    for it in members.get("beams", []) or []:
        L = it.get("length_m")
        n = _resolve_count(it, "보", it.get("mark", "?"), warnings)
        if n is None or not isinstance(L, (int, float)):
            continue
        beam_ea += (L * n) / 0.9

    total = slab_ea + wall_ea + column_ea + beam_ea
    if total > 0:
        warnings.append(
            "간격재(스페이서) 개수는 SK에코플랜트 매뉴얼 산출기준(슬래브 순면적/0.81×2, "
            "벽체 순면적/0.81×2, 기둥 4×층고/0.9×개수, 보 총길이/0.9)으로 근사 계산했습니다 "
            "— 실제 발주 전 재검증이 필요합니다."
        )
    if deck_excluded_count:
        warnings.append(
            f"데크플레이트 슬래브 {deck_excluded_count}건은 스페이서 산출에서 제외했습니다 "
            "(데크플레이트가 철근을 직접 지지하므로 별도 스페이서 불필요)."
        )

    return {
        "slab_ea": round(slab_ea),
        "wall_ea": round(wall_ea),
        "column_ea": round(column_ea),
        "beam_ea": round(beam_ea),
        "total_ea": round(total),
    }, warnings


def _typical_floor_height_m(elevation_data):
    """입면/단면도에서 읽은 floor_heights 중 repeat_count로 가중치를 준 최빈값을 대표 층고로 삼는다.
    (예: 기준층 2.9m가 repeat_count=14로 대부분의 층을 차지하면 그게 대표 층고가 됨)"""
    entries = (elevation_data or {}).get("floor_heights", []) or []
    weighted = {}
    for fh in entries:
        h = fh.get("height_m")
        if not isinstance(h, (int, float)):
            continue
        try:
            repeat = max(1, int(fh.get("repeat_count") or 1))
        except (TypeError, ValueError):
            repeat = 1
        weighted[h] = weighted.get(h, 0) + repeat
    if not weighted:
        return None
    return max(weighted, key=weighted.get)


def _openings_area_from_items(items):
    """부재 리스트(walls 등)의 openings 필드에서 총 개구부 면적(㎡)을 합산 (경고 없이 순수 합계용)."""
    total = 0.0
    for it in items:
        for op in it.get("openings") or []:
            w, h, cnt = op.get("width_m"), op.get("height_m"), op.get("count", 1) or 1
            if isinstance(w, (int, float)) and isinstance(h, (int, float)):
                total += w * h * cnt
    return round(total, 2)


def _openings_area_from_elevation(elevation_data):
    """입면도에서 읽은 openings 목록의 총 면적(㎡)."""
    total = 0.0
    for op in (elevation_data or {}).get("openings", []) or []:
        w, h, cnt = op.get("width_m"), op.get("height_m"), op.get("count", 1) or 1
        if isinstance(w, (int, float)) and isinstance(h, (int, float)):
            total += w * h * cnt
    return round(total, 2)


# ─────────────────────────────────────────────
# 구역별(지하/지상-단위세대/지상-공용 등) 철근콘크리트비(kg/m³) 점검
# — 철근 누락 여부를 잡아내기 위한 이상치 탐지용 진단 기능.
# 아래 범위는 KDS 등 공식 기준이 아니라, 국내 RC 아파트 통상 시공에서 흔히 쓰이는
# "감각치" 참고 범위입니다. 설계기준(내진등급/하중조건)에 따라 실제 범위는 다를 수 있으니
# 정밀 검증 기준이 아니라 "이 정도로 낮으면 배근도 판독이 빠졌을 가능성이 크다"는
# 참고선으로만 사용하세요.
# ─────────────────────────────────────────────
TYPICAL_REBAR_RATIO_KG_M3 = {
    # 하한값 재보정 근거: 경기도 부천시 오정동 139-5 외 12필지 가로주택정비사업(경성빌라, 지하2층/
    # 지상13층 아파트+부대시설, 2023.11, 한길건축사사무소/해밀EnC 구조설계) 실제 산출서(101동/102동/
    # 지하주차장/기전실/정화조/주민공동시설/관리사무소/경비실 8개 동, 전체동 합계 콘크리트 15,741m³·
    # 철근 1,308ton) 검증 결과, 기초(지내력 온통기초/매트기초 형식)는 37~45kg/m³, 슬래브는 47~67kg/m³,
    # 계단은 86~89kg/m³로 기존 하한값보다 실측값이 낮게 나와 하한을 낮췄다(기초/슬래브 독립기초·후판
    # 슬래브 등 다른 형식은 여전히 범위 안에 들어오도록 상한은 유지). 기둥(180~200kg/m³)·전단벽
    # (63~98kg/m³ 중 다수 90 부근)·보(142~270kg/m³)는 기존 범위와 대체로 부합해 소폭만 조정.
    "기초": (30, 150),  # 온통기초/매트기초는 30대~40대도 정상(독립기초는 기존처럼 높을 수 있음)
    "기둥": (150, 280),
    "보": (140, 270),
    "슬래브": (45, 130),
    "전단벽": (60, 160),
    "계단": (70, 180),  # 경사슬래브 근사 — 실측 86~89kg/m³ 반영, 여전히 정밀 기준은 아님
}


def _group_items_by_zone(items):
    """zone 필드(도면 판독 기준: 지하1층/1층/기준층(2~15층)/옥탑 등 실제 층 라벨) 기준으로 묶는다.
    zone이 없으면 '미상'으로 묶어서, 어떤 부재가 층 판독이 안 됐는지도 드러나게 한다."""
    groups = {}
    for it in items:
        zone = (it.get("zone") or "").strip() or "미상"
        groups.setdefault(zone, []).append(it)
    return groups


def _floor_repeat_count(item):
    """floor_repeat_count 필드값(기준층처럼 하나의 항목이 대표하는 반복 층수).
    없거나 1 미만/숫자가 아니면 1(반복 없음)로 간주한다."""
    val = item.get("floor_repeat_count")
    try:
        val = float(val)
    except (TypeError, ValueError):
        return 1.0
    if val and val >= 1:
        return val
    return 1.0


def _expand_items_by_floor_repeat(items):
    """기준층처럼 여러 층이 하나의 zone 항목(floor_repeat_count>1)으로 묶여 있으면,
    count를 floor_repeat_count만큼 곱한 사본을 만들어 전체 건물 기준 물량이 되도록 한다.
    원본 딕셔너리는 건드리지 않는다(사본만 생성). 반복이 없으면(=1) 원본 그대로 사용.
    Returns: (expanded_items, applied_notes) — applied_notes는 실제로 반복 적용된 항목들의
    설명 문구 리스트(사용자에게 어떤 가정이 적용됐는지 드러내기 위함)."""
    expanded = []
    applied_notes = []
    for it in items:
        repeat = _floor_repeat_count(it)
        if repeat and repeat != 1:
            new_it = dict(it)
            new_it["count"] = (it.get("count") or 1) * repeat
            # calc_columns/calc_walls가 "하부 1개소 정착길이"를 count에 비례해서 더하면,
            # 반복된 층 수만큼 정착길이가 중복 반영된다 — 실제로는 기둥/벽체 1개 위치가
            # 여러 층을 관통하는 연속 부재라 정착(기초 접합부)은 그 부재 전체에서 딱 1번뿐이고,
            # 층 경계마다 있는 건 이음(겹침)이다. count는 이미 반복 층수만큼 곱했으니, 정착길이
            # 계산 쪽에서 이 배율로 나눠 되돌릴 수 있게 반복 배율을 같이 넘겨준다.
            new_it["_floor_repeat"] = repeat
            expanded.append(new_it)
            mark = it.get("mark") or "(mark 미상)"
            zone = it.get("zone") or "미상"
            applied_notes.append(f"{mark}({zone}): 1개 층 기준 물량 × {int(repeat) if repeat == int(repeat) else repeat}개 층 반복 적용")
        else:
            expanded.append(it)
    return expanded, applied_notes


def _expand_members_by_floor_repeat(members):
    """foundations/columns/beams/slabs/walls/stairs 전체에 대해 floor_repeat_count 확장을
    일괄 적용한 새 members 딕셔너리를 반환한다 (general_spec/notes 등 나머지 키는 그대로 유지)."""
    expanded = dict(members)
    all_notes = []
    for key in ("foundations", "columns", "beams", "slabs", "walls", "stairs"):
        items, notes = _expand_items_by_floor_repeat(members.get(key, []) or [])
        expanded[key] = items
        all_notes += notes
    return expanded, all_notes


_FLOOR_SORT_UNKNOWN_RANK = 500


def _floor_sort_key(zone):
    """층별 결과표를 지하→지상→옥탑→미상 순서로 사람이 보기 자연스럽게 정렬하기 위한 키.
    지하는 깊은 층(지하2층)이 얕은 층(지하1층)보다 먼저 오도록, 기준층(2~15층) 같은 범위
    표기는 시작 층수 기준으로 배치한다."""
    z = (zone or "").strip()
    if not z or z == "미상":
        return (2, 0, z)
    if "옥탑" in z or "지붕" in z or "PH" in z.upper():
        return (1, 9999, z)
    m = re.search(r"지하\s*(\d+)", z)
    if m:
        return (0, -int(m.group(1)), z)
    m = re.search(r"(\d+)\s*~\s*\d+\s*층", z)
    if m:
        return (0, int(m.group(1)), z)
    m = re.search(r"(\d+)\s*층", z)
    if m:
        return (0, int(m.group(1)), z)
    return (0, _FLOOR_SORT_UNKNOWN_RANK, z)


def _physical_floor_above(zone):
    """수평재(슬래브/보)를 "시공순서(타설 사이클)" 기준으로 재배정하기 위한 헬퍼.
    실제 산출서 관행: "N층 물량 = N층 수직재(기둥,벽체) + N층 수직재 위에 얹히는 슬래브/보"이고,
    그 슬래브/보는 도면상 "물리적으로 바로 위층"의 바닥으로 표기된다(지상은 N+1층, 지하는
    번호가 하나 작은 지하(N-1)층 — 지하1층의 바로 위는 지상1층). 즉 도면에서 읽은 슬래브/보의
    zone을 이 함수로 변환하면, 그 부재가 속해야 할 "물리적으로 바로 위층" 라벨이 나온다.
    기준층(2~15층)처럼 범위로 묶인 반복 표기, 옥탑/지붕, 미상은 shift 대상에서 제외하고
    원본 그대로 반환한다(범위 내부는 이미 동일 그룹이라 shift가 의미 없고, 옥탑 위에는
    더 이상 층이 없어 shift할 대상이 없기 때문)."""
    z = (zone or "").strip()
    if not z or z == "미상":
        return z
    if "옥탑" in z or "지붕" in z or "PH" in z.upper():
        return z
    if "~" in z:  # 기준층(2~15층) 같은 반복 범위 표기
        return z
    m = re.match(r"^지하\s*(\d+)\s*층$", z)
    if m:
        n = int(m.group(1))
        return "1층" if n <= 1 else f"지하{n - 1}층"
    m = re.match(r"^(\d+)\s*층$", z)
    if m:
        n = int(m.group(1))
        return f"{n + 1}층"
    return z  # 패턴에 안 맞으면 안전하게 원본 유지(잘못 옮기지 않도록)


# 수평재(층 경계에서 "물리적으로 바로 위층" 수직재와 한 그룹으로 묶여야 하는 부재종류)
_HORIZONTAL_CATEGORIES = {"슬래브", "보"}


def compute_floor_breakdown(members: dict, general_spec: dict = None, fallback_height_m=None):
    """
    section(구간/동) x zone(층) 기준으로 콘크리트/거푸집/철근(할증 포함)을 다시 집계해서,
    "구간별 → 층별 소계" 중첩 리스트를 만든다. 각 층 아래에 부재종류별 세부 항목도 담는다.

    실제 전문 산출서 관행을 따라, 슬래브/보(수평재)는 도면에서 읽은 zone 그대로가 아니라
    _physical_floor_above()로 "시공순서 기준 물리적으로 바로 위층" 그룹으로 재배정한다
    (예: "지하3층 물량 = 지하3층 수직재(기둥,벽체) + 지하2층 바닥 수평재(슬래브,보)").
    기초/기둥/벽체/계단은 도면 zone을 그대로 사용한다.

    ratio_check와 달리 여기서는 실제 물량 표시가 목적이므로 철근에 할증률을 반영한다.

    fallback_height_m: compute_structural_quantities가 입면/단면도에서 읽은 대표 층고.
    height_m이 없는 기둥/벽체 항목에 이 값을 대체로 써야, 총괄 합계(compute_structural_
    quantities의 calc_columns/calc_walls 직접 호출)와 여기 층별 표가 서로 어긋나지 않는다.
    (이전 버전은 이 인자를 안 넘겨서, height_m 누락 항목이 총괄 합계엔 반영되는데 층별
    표에서만 통째로 빠지는 불일치가 있었다 — 실제 프로젝트에서 기둥/벽체 대부분이
    height_m 없이 입면도 대체값에만 의존하고 있어서 그 표에서 기둥/벽체 행 자체가
    안 보이는 현상으로 나타났다.)

    Returns: (floor_breakdown, shift_notes) — shift_notes는 실제로 재배정이 적용된 항목의
    설명 문구 리스트(사용자에게 어떤 가정이 적용됐는지 드러내기 위함).
    """
    general_spec = general_spec or {}
    category_specs = [
        ("기초", members.get("foundations", []) or [], calc_foundations),
        ("기둥", members.get("columns", []) or [], lambda items, gs: calc_columns(items, gs, fallback_height_m)),
        ("보", members.get("beams", []) or [], calc_beams),
        ("슬래브", members.get("slabs", []) or [], calc_slabs),
        ("전단벽", members.get("walls", []) or [], lambda items, gs: calc_walls(items, gs, fallback_height_m)),
        ("계단", members.get("stairs", []) or [], calc_stairs),
    ]

    # (section, zone) -> {"concrete_m3":.., "formwork_m2":.., "rebar_kg":.., "categories": {cat: {...}}}
    combo_totals = {}
    section_order = []
    shift_notes = []

    for cat_label, items, calc_fn in category_specs:
        if not items:
            continue
        is_horizontal = cat_label in _HORIZONTAL_CATEGORIES
        buckets = {}
        bucket_order = []
        for it in items:
            section = (it.get("section") or "").strip() or "미상"
            raw_zone = (it.get("zone") or "").strip() or "미상"
            if is_horizontal:
                eff_zone = _physical_floor_above(raw_zone)
                if eff_zone != raw_zone:
                    mark = it.get("mark") or "?"
                    shift_notes.append(
                        f"{cat_label} {mark}({section}): {raw_zone} 수평재 → {eff_zone} 그룹으로 재배정"
                    )
            else:
                eff_zone = raw_zone
            key = (section, eff_zone)
            if key not in buckets:
                buckets[key] = []
                bucket_order.append(key)
            buckets[key].append(it)
            if section not in section_order:
                section_order.append(section)

        for key in bucket_order:
            section, zone = key
            bucket_items = buckets[key]
            concrete, formwork, rebar_kg, _warn = calc_fn(bucket_items, general_spec)

            if concrete <= 0 and formwork <= 0 and rebar_kg <= 0:
                continue

            rebar_kg = _with_waste(rebar_kg)
            combo = combo_totals.setdefault(key, {
                "concrete_m3": 0.0, "formwork_m2": 0.0, "rebar_kg": 0.0, "categories": {}
            })
            combo["concrete_m3"] += concrete
            combo["formwork_m2"] += formwork
            combo["rebar_kg"] += rebar_kg
            cat_bucket = combo["categories"].setdefault(cat_label, {
                "concrete_m3": 0.0, "formwork_m2": 0.0, "rebar_kg": 0.0
            })
            cat_bucket["concrete_m3"] += concrete
            cat_bucket["formwork_m2"] += formwork
            cat_bucket["rebar_kg"] += rebar_kg

    sections_dict = {}
    for (section, zone), totals in combo_totals.items():
        sections_dict.setdefault(section, []).append((zone, totals))

    result = []
    for section in section_order:
        floors = sorted(sections_dict.get(section, []), key=lambda pair: _floor_sort_key(pair[0]))
        floor_list = []
        section_concrete = section_formwork = section_rebar = 0.0
        for zone, totals in floors:
            categories = [
                {
                    "category": cat,
                    "concrete_m3": round(v["concrete_m3"], 3),
                    "formwork_m2": round(v["formwork_m2"], 2),
                    "rebar_kg": round(v["rebar_kg"], 1),
                }
                for cat, v in sorted(totals["categories"].items())
            ]
            floor_list.append({
                "zone": zone,
                "concrete_m3": round(totals["concrete_m3"], 3),
                "formwork_m2": round(totals["formwork_m2"], 2),
                "rebar_kg": round(totals["rebar_kg"], 1),
                "categories": categories,
            })
            section_concrete += totals["concrete_m3"]
            section_formwork += totals["formwork_m2"]
            section_rebar += totals["rebar_kg"]

        result.append({
            "section": section,
            "concrete_m3": round(section_concrete, 3),
            "formwork_m2": round(section_formwork, 2),
            "rebar_kg": round(section_rebar, 1),
            "floors": floor_list,
        })

    return result, shift_notes


def compute_rebar_concrete_ratio_check(members: dict, general_spec: dict = None, fallback_height_m=None) -> list:
    """
    구역(zone) x 부재종류별로 콘크리트체적/철근중량을 다시 집계해서 철근콘크리트비(kg/m³)를
    계산하고, 통상 참고범위(TYPICAL_REBAR_RATIO_KG_M3)를 벗어나면 플래그를 붙인다.
    기존 calc_foundations/calc_columns/calc_beams/calc_slabs/calc_walls를 zone으로 필터링한
    부분집합에 그대로 재사용해서, 전체 합계와 계산식이 어긋나지 않도록 한다.

    ※ 기초(foundations)는 기초일람표/기초상세도에서 rebar_size/rebar_spacing_m을
      읽었을 때만 철근중량이 계산된다 — 해당 정보가 없으면 rebar_kg=0으로 나오고
      이 진단 함수가 그 사실을 "철근 데이터 없음"으로 정확히 잡아낸다.

    fallback_height_m: compute_floor_breakdown과 동일한 이유로 필요하다 — height_m이 없는
    기둥/벽체는 이 값이 없으면 concrete=0으로 계산되어 "철근 데이터 없음"조차 표시되지
    않고 진단 결과에서 통째로 빠져버린다(실제 발생했던 문제).
    """
    general_spec = general_spec or {}
    category_specs = [
        ("기초", members.get("foundations", []) or [], "foundations"),
        ("기둥", members.get("columns", []) or [], "columns"),
        ("보", members.get("beams", []) or [], "beams"),
        ("슬래브", members.get("slabs", []) or [], "slabs"),
        ("전단벽", members.get("walls", []) or [], "walls"),
        ("계단", members.get("stairs", []) or [], "stairs"),
    ]

    results = []
    for cat_label, items, cat_key in category_specs:
        if not items:
            continue
        zone_groups = _group_items_by_zone(items)
        for zone, zone_items in zone_groups.items():
            rebar_kg = 0.0
            if cat_key == "foundations":
                concrete, _formwork, rebar_kg, _warn = calc_foundations(zone_items, general_spec)
            elif cat_key == "columns":
                concrete, _formwork, rebar_kg, _warn = calc_columns(zone_items, general_spec, fallback_height_m)
            elif cat_key == "beams":
                concrete, _formwork, rebar_kg, _warn = calc_beams(zone_items, general_spec)
            elif cat_key == "slabs":
                concrete, _formwork, rebar_kg, _warn = calc_slabs(zone_items, general_spec)
            elif cat_key == "walls":
                concrete, _formwork, rebar_kg, _warn = calc_walls(zone_items, general_spec, fallback_height_m)
            else:  # stairs
                concrete, _formwork, rebar_kg, _warn = calc_stairs(zone_items, general_spec)

            if concrete <= 0:
                continue

            ratio = round(rebar_kg / concrete, 1)
            low, high = TYPICAL_REBAR_RATIO_KG_M3.get(cat_label, (0, 10 ** 6))

            if rebar_kg <= 0:
                flag = "철근 데이터 없음 — 배근도 판독 누락 가능성 매우 높음"
            elif ratio < low:
                flag = f"통상 참고범위({low}~{high}kg/m³)보다 낮음 — 배근 누락 가능성"
            elif ratio > high * 1.3:
                flag = f"통상 참고범위({low}~{high}kg/m³)보다 높음 — 중복 계산 가능성"
            else:
                flag = None

            results.append({
                "zone": zone,
                "category": cat_label,
                "concrete_m3": round(concrete, 2),
                "rebar_kg": round(rebar_kg, 1),
                "ratio_kg_m3": ratio,
                "typical_range_kg_m3": f"{low}~{high}",
                "flag": flag,
            })

    return results


def compute_structural_quantities(members: dict, elevation_data: dict = None) -> dict:
    """
    members: OpenAI Vision이 도면에서 읽어온 부재 리스트 + general_spec (구조일반사항)
    elevation_data: 건축 입면도/단면도에서 읽은 층고(floor_heights) + 개구부(openings) 참고자료.
        - 구조 부재에 층고가 누락된 경우 대체값으로 사용 (계산에서 제외되지 않도록)
        - 벽체 개구부 합계와 대조해서 정합성 경고를 생성
    Returns: 기존 Excel export가 기대하는 {items, summary, warnings, missing_info} 포맷
    """
    members, floor_repeat_notes = _expand_members_by_floor_repeat(members)
    general_spec = members.get("general_spec") or {}
    fallback_height_m = _typical_floor_height_m(elevation_data)

    items = []
    all_warnings = []

    if floor_repeat_notes:
        all_warnings.append(
            "기준층 반복(floor_repeat_count) 적용: 아래 항목은 도면 1개 층 기준 물량에 반복 층수를 "
            "곱해 전체 건물 물량으로 확장했습니다 — " + " / ".join(floor_repeat_notes)
        )

    if elevation_data and elevation_data.get("floor_heights"):
        if fallback_height_m:
            all_warnings.append(
                f"입면/단면도에서 층고 정보를 확인했습니다 (대표값 {fallback_height_m}m). "
                "구조 부재에 층고가 누락된 경우 이 값으로 대체 계산합니다."
            )

    # ── 구조일반사항 확인 (레미콘 강도 / 철근 강종 / 이음등급) ──
    fck = general_spec.get("concrete_fck_mpa")
    rebar_grade = general_spec.get("rebar_grade")
    splice_class = general_spec.get("lap_splice_class")

    spec_lines = []
    if fck:
        spec_lines.append(f"레미콘 설계기준강도 Fck={fck}MPa")
    else:
        all_warnings.append("구조일반사항에서 레미콘 설계기준강도(Fck)를 확인하지 못했습니다 — 도면 확인 필요")
    if rebar_grade:
        spec_lines.append(f"철근 강종 {rebar_grade}")
    else:
        all_warnings.append("구조일반사항에서 철근 강종(SD400/SD500 등)을 확인하지 못했습니다 — 도면 확인 필요")
    if splice_class:
        spec_lines.append(f"이음등급 {splice_class}급(도면 표기)")
        if str(splice_class).strip().upper().startswith("A"):
            all_warnings.append(
                "도면 구조일반사항에는 이음등급이 A급으로 표기돼 있지만, 이 도구는 안전측 기준으로 "
                "이음길이를 항상 B급(직선 정착길이×1.3) 기준으로 계산합니다 — 정책에 따른 의도된 동작입니다."
            )
    else:
        all_warnings.append("구조일반사항에서 이음등급(A급/B급) 표기를 확인하지 못했습니다 — 이음길이는 항상 B급 기준으로 계산했습니다.")
    if not general_spec.get("lap_splice_table"):
        all_warnings.append("이음길이표가 도면에서 확인되지 않아 KDS 근사 공식값으로 계산했습니다 — 실제 이음길이표로 재검증 권장")
    if not general_spec.get("anchorage_table"):
        all_warnings.append(
            "정착길이표(anchorage_table)가 도면에서 확인되지 않아 KDS 근사 공식값"
            "(직선철근 ld=계수×db×fy/√fck, B급 이음길이=1.3×ld)으로 계산했습니다 — 실제 정착길이표로 재검증 권장"
        )
    if not general_spec.get("cover_thickness_mm"):
        all_warnings.append(
            "피복두께(cover_thickness_mm)가 도면에서 확인되지 않아 '피복 충분(Case1)'으로 가정하고 계산했습니다 "
            "— 실제 피복두께가 주철근 지름보다 얇으면(Case2) 정착/이음길이가 1.5배까지 늘어날 수 있습니다"
        )

    all_warnings.append(
        f"철근 물량에는 할증률 {round((REBAR_WASTE_FACTOR-1)*100)}%(REBAR_WASTE_FACTOR={REBAR_WASTE_FACTOR})를 "
        "반영했습니다(제강사 규격손실/절단로스 등, 제강사·현장 조건에 따라 1.5~3.0% 범위에서 다를 수 있음). "
        "단, 구역별 철근콘크리트비 점검(ratio_check)은 할증 반영 전 순물량 기준입니다 — 설계 물량 자체의 "
        "과소/과다 여부를 보는 진단이므로 발주 할증과 섞지 않았습니다."
    )

    all_warnings.append(
        f"철근 장대(정척) 길이는 {STOCK_BAR_LENGTH_M}m 기준으로 이음 개수를 계산했습니다 "
        "(기둥/보/벽체/슬래브 모두 반영). 정착길이도 KDS 근사 공식으로 반영했습니다 — 기둥/전단벽 수직근은 "
        "하부(기초 접합부) 1개소, 보/슬래브는 양단 각 1개소를 가정한 단순 근사치입니다. "
        "갈고리(hook)·상부철근(1.3배)·기초 도웰바는 도면에서 has_hook/is_top_bar/dowel_bar_size 등을 "
        "읽었을 때만 반영되며, 못 읽은 부재는 '일반철근·직선정착·도웰바 없음'으로 기본 계산됩니다."
    )

    items.append({
        "category": "구조일반사항",
        "sub_category": "레미콘/철근 규격 확인",
        "quantity": "",
        "unit": "",
        "confidence": "high" if (fck and rebar_grade and splice_class) else "low",
        "note": " / ".join(spec_lines) if spec_lines else "구조일반사항에서 확인된 규격 없음 — 도면 확인 필요",
    })

    f_concrete, f_formwork, f_rebar, f_warn = calc_foundations(members.get("foundations", []), general_spec)
    all_warnings += f_warn
    f_rebar = _with_waste(f_rebar)
    if f_concrete:
        items.append({"category": "콘크리트", "sub_category": "기초", "quantity": f_concrete, "unit": "m³", "confidence": "high", "note": "L×W×T×개수로 코드 계산"})
    if f_formwork:
        items.append({"category": "거푸집", "sub_category": "기초 측면", "quantity": f_formwork, "unit": "m²", "confidence": "high", "note": "둘레×두께×개수"})
    if f_rebar:
        items.append({"category": "철근", "sub_category": "기초 (저판 2방향 배근 + 도웰바)", "quantity": round(f_rebar / 1000, 3), "unit": "ton", "confidence": "medium", "note": f"저판 하부 2방향 배근 근사치, 할증 {round((REBAR_WASTE_FACTOR-1)*100)}% 포함. 도웰바는 도면에 dowel_bar_size/count가 표기된 경우만 포함, 상부근은 미반영"})

    c_concrete, c_formwork, c_rebar, c_warn = calc_columns(members.get("columns", []), general_spec, fallback_height_m)
    all_warnings += c_warn
    c_rebar = _with_waste(c_rebar)
    if c_concrete:
        items.append({"category": "콘크리트", "sub_category": "기둥", "quantity": c_concrete, "unit": "m³", "confidence": "high", "note": "단면적×층고×개수로 코드 계산"})
    if c_formwork:
        items.append({"category": "거푸집", "sub_category": "기둥", "quantity": c_formwork, "unit": "m²", "confidence": "high", "note": "둘레×층고×개수"})
    if c_rebar:
        items.append({"category": "철근", "sub_category": "기둥 (주근+띠철근, 이음 포함)", "quantity": round(c_rebar / 1000, 3), "unit": "ton", "confidence": "medium", "note": f"KS 단위중량표 + 이음길이 반영, 할증 {round((REBAR_WASTE_FACTOR-1)*100)}% 포함"})

    b_concrete, b_formwork, b_rebar, b_warn = calc_beams(members.get("beams", []), general_spec)
    all_warnings += b_warn
    b_rebar = _with_waste(b_rebar)
    if b_concrete:
        items.append({"category": "콘크리트", "sub_category": "보", "quantity": b_concrete, "unit": "m³", "confidence": "high", "note": "단면적×길이×개수로 코드 계산"})
    if b_formwork:
        items.append({"category": "거푸집", "sub_category": "보", "quantity": b_formwork, "unit": "m²", "confidence": "high", "note": "(폭+2×춤)×길이×개수"})
    if b_rebar:
        items.append({"category": "철근", "sub_category": "보 (주근+스터럽, 이음 포함)", "quantity": round(b_rebar / 1000, 3), "unit": "ton", "confidence": "medium", "note": f"KS 단위중량표 + 이음길이 반영, 할증 {round((REBAR_WASTE_FACTOR-1)*100)}% 포함"})

    s_concrete, s_formwork, s_rebar, s_warn = calc_slabs(members.get("slabs", []), general_spec)
    all_warnings += s_warn
    s_rebar = _with_waste(s_rebar)
    if s_concrete:
        items.append({"category": "콘크리트", "sub_category": "슬래브", "quantity": s_concrete, "unit": "m³", "confidence": "high", "note": "(면적-개구부)×두께로 코드 계산"})
    if s_formwork:
        items.append({"category": "거푸집", "sub_category": "슬래브 바닥", "quantity": s_formwork, "unit": "m²", "confidence": "high", "note": "면적-개구부 기준"})
    if s_rebar:
        items.append({"category": "철근", "sub_category": "슬래브", "quantity": round(s_rebar / 1000, 3), "unit": "ton", "confidence": "low", "note": f"정방향 근사치, 할증 {round((REBAR_WASTE_FACTOR-1)*100)}% 포함 — 배근도 방향별 스팬으로 재검증 필요"})

    w_concrete, w_formwork, w_rebar, w_warn = calc_walls(members.get("walls", []), general_spec, fallback_height_m)
    all_warnings += w_warn
    w_rebar = _with_waste(w_rebar)
    if w_concrete:
        items.append({"category": "콘크리트", "sub_category": "전단벽", "quantity": w_concrete, "unit": "m³", "confidence": "high", "note": "(길이×높이-개구부)×두께로 코드 계산"})
    if w_formwork:
        items.append({"category": "거푸집", "sub_category": "전단벽 (양면)", "quantity": w_formwork, "unit": "m²", "confidence": "high", "note": "(길이×높이-개구부)×2"})
    if w_rebar:
        items.append({"category": "철근", "sub_category": "전단벽 (이음 포함)", "quantity": round(w_rebar / 1000, 3), "unit": "ton", "confidence": "medium", "note": f"종횡 배근 간격 + 이음길이 반영, 할증 {round((REBAR_WASTE_FACTOR-1)*100)}% 포함"})

    st_concrete, st_formwork, st_rebar, st_warn = calc_stairs(members.get("stairs", []), general_spec)
    all_warnings += st_warn
    st_rebar = _with_waste(st_rebar)
    if st_concrete:
        items.append({"category": "콘크리트", "sub_category": "계단", "quantity": st_concrete, "unit": "m³", "confidence": "medium", "note": "폭×경사길이×두께로 근사 계산 (계단참/챌판 형상 미반영)"})
    if st_formwork:
        items.append({"category": "거푸집", "sub_category": "계단 (하부)", "quantity": st_formwork, "unit": "m²", "confidence": "medium", "note": "경사면 기준, 측판/챌판 거푸집 미반영"})
    if st_rebar:
        items.append({"category": "철근", "sub_category": "계단 (주근+배력근)", "quantity": round(st_rebar / 1000, 3), "unit": "ton", "confidence": "low", "note": f"경사슬래브 근사치, 할증 {round((REBAR_WASTE_FACTOR-1)*100)}% 포함 — 계단상세도로 재검증 필요"})

    spacer_counts, spacer_warn = calc_spacers(members)
    all_warnings += spacer_warn
    if spacer_counts["total_ea"]:
        items.append({
            "category": "간격재", "sub_category": "스페이서(슬래브/벽체/기둥/보)",
            "quantity": spacer_counts["total_ea"], "unit": "EA", "confidence": "low",
            "note": f"슬래브 {spacer_counts['slab_ea']}EA + 벽체 {spacer_counts['wall_ea']}EA + "
                    f"기둥 {spacer_counts['column_ea']}EA + 보 {spacer_counts['beam_ea']}EA "
                    "(데크슬래브는 산출 제외 반영), 근사치"
        })

    # ── 입면도 개구부(창호) 합계 vs 구조 벽체에서 읽은 개구부 합계 정합성 대조 ──
    if elevation_data and elevation_data.get("openings"):
        elev_opening_area = _openings_area_from_elevation(elevation_data)
        wall_opening_area = _openings_area_from_items(members.get("walls", []))
        if elev_opening_area > 0:
            diff = elev_opening_area - wall_opening_area
            rel_diff = abs(diff) / elev_opening_area
            if rel_diff > 0.3 and abs(diff) > 5:
                all_warnings.append(
                    f"개구부 정합성 확인 필요: 입면도 기준 개구부 합계 {elev_opening_area}㎡, "
                    f"구조배근도 판독 개구부 합계 {wall_opening_area}㎡ — 차이가 커서(약 {round(rel_diff*100)}%) "
                    "누락된 벽체 개구부가 있는지 대조해 보세요."
                )
            else:
                all_warnings.append(
                    f"개구부 정합성 확인: 입면도({elev_opening_area}㎡)와 구조배근도 판독({wall_opening_area}㎡) "
                    "개구부 합계가 대체로 일치합니다."
                )

    total_concrete = round(f_concrete + c_concrete + b_concrete + s_concrete + w_concrete + st_concrete, 3)
    total_formwork = round(f_formwork + c_formwork + b_formwork + s_formwork + w_formwork + st_formwork, 3)
    total_rebar_ton = round((f_rebar + c_rebar + b_rebar + s_rebar + w_rebar + st_rebar) / 1000, 3)

    summary = (
        f"콘크리트 총 {total_concrete}m³, 거푸집 총 {total_formwork}m², 철근 총 {total_rebar_ton}ton "
        f"(기초/기둥/보/슬래브/전단벽/계단 기준, 이음길이·정착길이 반영, 철근은 할증 "
        f"{round((REBAR_WASTE_FACTOR-1)*100)}% 포함 코드 계산)"
    )

    missing_info = list(members.get("notes", []))
    if elevation_data and elevation_data.get("notes"):
        missing_info += [f"[입면/단면] {n}" for n in elevation_data["notes"]]

    # ── 구역(지하/지상-단위세대 등)별 철근콘크리트비 점검 — 철근 누락 진단용 ──
    ratio_check = compute_rebar_concrete_ratio_check(members, general_spec, fallback_height_m)
    for r in ratio_check:
        if r["flag"]:
            missing_info.append(
                f"[철근비 점검] {r['zone']} {r['category']}: {r['ratio_kg_m3']}kg/m³ — {r['flag']}"
            )

    overall_rebar_kg = f_rebar + c_rebar + b_rebar + s_rebar + w_rebar + st_rebar
    overall_ratio = round(overall_rebar_kg / total_concrete, 1) if total_concrete else None

    # ── 구간(동)x층별 물량 소계 — 부재종류를 합쳐서 구간x층 단위로 다시 집계 (할증 반영) ──
    floor_breakdown, floor_shift_notes = compute_floor_breakdown(members, general_spec, fallback_height_m)
    if floor_shift_notes:
        preview = floor_shift_notes[:5]
        more = len(floor_shift_notes) - len(preview)
        all_warnings.append(
            "층별 집계 시 슬래브/보(수평재)를 시공순서 기준으로 물리적 바로 위층 그룹에 재배정했습니다"
            " (예: N층 물량 = N층 기둥/벽체 + 그 위에 얹히는 슬래브/보): "
            + " / ".join(preview) + (f" 외 {more}건" if more > 0 else "")
        )

    return {
        "items": items,
        "summary": summary,
        "warnings": all_warnings,
        "missing_info": missing_info,
        "totals": {
            "concrete_m3": total_concrete,
            "formwork_m2": total_formwork,
            "rebar_ton": total_rebar_ton,
            "rebar_concrete_ratio_kg_m3": overall_ratio,
        },
        "ratio_check": ratio_check,
        "floor_breakdown": floor_breakdown,
    }


# ─────────────────────────────────────────────
# 간단 3D 매싱 모델
# - 정밀 배치(BIM)가 아니라, 층고(입면/단면도)와 대표 슬래브 면적으로
#   층별 박스를 쌓아올린 "개략 매싱"입니다. 실제 평면 형상/부재 위치는 반영하지 않습니다.
# ─────────────────────────────────────────────
DEFAULT_MASSING_FOOTPRINT_M2 = 400.0  # 슬래브 면적을 전혀 못 읽었을 때의 임의 기본값 (20m x 20m)


def _representative_footprint_area_m2(members):
    """대표 층 바닥면적(㎡)을 슬래브 항목 중 가장 큰 area_m2로 근사한다."""
    best = None
    for s in members.get("slabs", []) or []:
        a = s.get("area_m2")
        if isinstance(a, (int, float)) and a > 0 and (best is None or a > best):
            best = a
    return best


_MEMBER_CATEGORY_LABEL = {
    "foundations": "기초", "columns": "기둥", "beams": "보",
    "slabs": "슬래브", "walls": "전단벽", "stairs": "계단",
}


def _synthesize_display_layers(cat_key, it):
    """3D 뷰어의 "부재별 상세보기"에서 세분화 배근(rebar_layers)이 없는 레거시 부재도
    똑같은 형태로 보여주기 위해, 카테고리별 레거시 스칼라 필드(main_rebar_size 등)를
    rebar_layers와 같은 모양의 리스트로 변환한다. 표시 전용 변환이며 계산에는 쓰이지
    않는다 — 실제 계산은 calc_*의 rebar_layers/레거시 분기가 각자 담당한다."""
    layers = it.get("rebar_layers")
    if isinstance(layers, list) and layers:
        return layers
    out = []
    if cat_key == "columns":
        if it.get("main_rebar_size") and it.get("main_rebar_count"):
            out.append({"role": "주근", "size": it["main_rebar_size"], "count": it["main_rebar_count"]})
        if it.get("tie_rebar_size") and it.get("tie_spacing_m"):
            out.append({"role": "후프", "size": it["tie_rebar_size"], "spacing_m": it["tie_spacing_m"]})
    elif cat_key == "beams":
        if it.get("main_rebar_size") and it.get("main_rebar_count"):
            out.append({
                "role": "주근", "position": "상부" if it.get("is_top_bar") else "하부",
                "size": it["main_rebar_size"], "count": it["main_rebar_count"],
            })
        if it.get("stirrup_size") and it.get("stirrup_spacing_m"):
            out.append({"role": "스터럽", "size": it["stirrup_size"], "spacing_m": it["stirrup_spacing_m"]})
    elif cat_key == "slabs":
        if it.get("rebar_size") and it.get("rebar_spacing_m"):
            out.append({
                "role": "주근", "position": "상부" if it.get("is_top_bar") else "하부",
                "size": it["rebar_size"], "spacing_m": it["rebar_spacing_m"],
            })
    elif cat_key == "walls":
        if it.get("rebar_size") and it.get("rebar_spacing_m"):
            out.append({"role": "수직근", "size": it["rebar_size"], "spacing_m": it["rebar_spacing_m"]})
            out.append({"role": "수평근", "size": it["rebar_size"], "spacing_m": it["rebar_spacing_m"]})
    elif cat_key == "stairs":
        if it.get("rebar_size") and it.get("rebar_spacing_m"):
            out.append({
                "role": "주근", "position": "상부" if it.get("is_top_bar") else "하부",
                "size": it["rebar_size"], "spacing_m": it["rebar_spacing_m"],
            })
        if it.get("distribution_rebar_size") and it.get("distribution_rebar_spacing_m"):
            out.append({
                "role": "배력근", "size": it["distribution_rebar_size"],
                "spacing_m": it["distribution_rebar_spacing_m"],
            })
    return out


def compute_member_rebar_detail(members: dict) -> list:
    """3D 뷰어의 "부재별 보기"(클릭해서 그 부재 배근만 표시)용 데이터를 만든다.
    zone(도면에 기재된 층 라벨) 기준으로 부재를 묶어, 층마다 부재 목록 + 각 부재의
    세분화 배근(rebar_layers, 없으면 레거시 필드에서 변환)을 함께 내려준다.

    실제 부재의 X/Y 좌표는 도면에서 추출하지 않으므로 여기엔 위치 정보가 없다 —
    3D 뷰어는 이 목록을 "클릭 가능한 부재 리스트"로 보여주고, 선택된 부재의 배근을
    층 박스 안에 스키매틱하게(실제 배치가 아닌 참고용으로) 그린다.

    Returns: [{"zone": "지하1층", "members": [{"category","mark","count","rebar_layers"}, ...]}, ...]
    지하→지상→옥탑→미상 순으로 정렬된다(_floor_sort_key 재사용, compute_floor_breakdown과
    동일한 정렬 기준이라 다른 화면과 층 순서가 어긋나지 않는다).
    """
    by_zone = {}
    for cat_key, label in _MEMBER_CATEGORY_LABEL.items():
        for it in members.get(cat_key, []) or []:
            if not isinstance(it, dict):
                continue
            zone = (it.get("zone") or "").strip() or "미상"
            by_zone.setdefault(zone, []).append({
                "category": label,
                "mark": it.get("mark") or "무명",
                "count": it.get("count"),
                "rebar_layers": _synthesize_display_layers(cat_key, it),
            })
    return [
        {"zone": zone, "members": by_zone[zone]}
        for zone in sorted(by_zone.keys(), key=_floor_sort_key)
    ]


def compute_rebar_bar_counts(members: dict) -> dict:
    """
    3D 매싱 뷰어에서 철근을 "라인"으로 그릴 때, 임의 개수가 아니라 실제 배근 수량에
    가까운 가닥 수를 쓰기 위한 집계. 중량(rebar_weight)이 아니라 "가닥 수"만 세는
    시각화 전용 개략 계산이며, calc_columns/calc_walls/calc_beams의 이음/스터럽 개수
    산정 로직과 동일한 방식(_spacing_bar_count: 간격당 +1, 펜스포스트 방식)을 재사용한다.

    Returns: {"vertical_bar_count": int, "horizontal_ring_count": int}
      - vertical_bar_count: 기둥 주근 + 벽체 수직근 가닥 수 총합
      - horizontal_ring_count: 기둥 띠철근 + 벽체 수평근 + 보 스터럽 개수 총합
    """
    vertical = 0
    horizontal = 0

    for it in members.get("columns", []) or []:
        n = _resolve_count(it, "기둥", it.get("mark", "?"))
        if n is None:
            continue
        main_count = it.get("main_rebar_count")
        if isinstance(main_count, (int, float)) and main_count > 0:
            vertical += int(main_count) * n

        h = it.get("height_m")
        spacing = it.get("tie_spacing_m")
        if isinstance(h, (int, float)) and isinstance(spacing, (int, float)) and spacing > 0:
            horizontal += _spacing_bar_count(h, spacing) * n

    for it in members.get("walls", []) or []:
        n = _resolve_count(it, "전단벽", it.get("mark", "?"))
        if n is None:
            continue
        # calc_walls와 동일한 가정 — is_single_face가 true로 명시되지 않으면 양면(2배)로 본다.
        face_count = 1 if it.get("is_single_face") is True else 2
        L, H = it.get("length_m"), it.get("height_m")
        spacing = it.get("rebar_spacing_m")
        if isinstance(L, (int, float)) and isinstance(spacing, (int, float)) and spacing > 0:
            vertical += _spacing_bar_count(L, spacing) * face_count * n
        if isinstance(H, (int, float)) and isinstance(spacing, (int, float)) and spacing > 0:
            horizontal += _spacing_bar_count(H, spacing) * face_count * n

    for it in members.get("beams", []) or []:
        n = _resolve_count(it, "보", it.get("mark", "?"))
        if n is None:
            continue
        L = it.get("length_m")
        spacing = it.get("stirrup_spacing_m")
        if isinstance(L, (int, float)) and isinstance(spacing, (int, float)) and spacing > 0:
            horizontal += _spacing_bar_count(L, spacing) * n

    return {"vertical_bar_count": vertical, "horizontal_ring_count": horizontal}


def compute_massing_model(members: dict, elevation_data: dict = None) -> dict:
    """
    구조 부재 데이터 + 입면/단면도 층고 정보로 "개략 건물 매싱 박스" 모델을 만든다.
    3D 뷰어(Three.js)가 그대로 그릴 수 있는 층별 박스 리스트를 반환한다.
    정확한 부재 배치(X/Y 좌표)는 도면에서 추출하지 않으므로, 층마다 같은 정사각형
    발자국(footprint)을 대표값으로 사용하는 단순 매싱이다. BIM 수준의 정밀 모델이 아니다.
    """
    warnings = []
    floor_heights = (elevation_data or {}).get("floor_heights") or []

    footprint_area = _representative_footprint_area_m2(members)
    if footprint_area is None:
        footprint_area = DEFAULT_MASSING_FOOTPRINT_M2
        warnings.append(
            f"슬래브 면적 정보가 없어 매싱 박스 바닥면적을 임의값({DEFAULT_MASSING_FOOTPRINT_M2}㎡)으로 표시합니다."
        )
    footprint_side = round(math.sqrt(footprint_area), 2)

    floors = []
    z = 0.0
    if not floor_heights:
        warnings.append("입면/단면도에서 층고 정보를 확인하지 못해 매싱 박스를 만들 수 없습니다 — 입면·단면도를 업로드해 보세요.")
    else:
        for fh in floor_heights:
            h = fh.get("height_m")
            if not isinstance(h, (int, float)) or h <= 0:
                continue
            try:
                repeat = max(1, int(fh.get("repeat_count") or 1))
            except (TypeError, ValueError):
                repeat = 1
            level_label = fh.get("level") or "층"
            for i in range(repeat):
                floors.append({
                    "level": level_label if repeat == 1 else f"{level_label} #{i + 1}",
                    "z_base_m": round(z, 2),
                    "height_m": round(h, 2),
                    "footprint_area_m2": footprint_area,
                    "footprint_side_m": footprint_side,
                })
                z += h

    return {
        "floors": floors,
        "total_height_m": round(z, 2),
        "footprint_area_m2": footprint_area,
        "footprint_side_m": footprint_side,
        "warnings": warnings,
        "note": "실제 평면 형상·부재 배치가 아닌, 대표 슬래브 면적과 층고를 이용한 개략 매싱 박스입니다 — 참고용으로만 사용하세요.",
        "rebar_bar_counts": compute_rebar_bar_counts(members),
        # 부재별 상세보기(클릭해서 그 부재만 표시)용 — zone(도면 층 라벨) 기준 부재 목록.
        # 위 floors(박스 층수)와는 서로 다른 출처(elevation_data vs 부재 zone 필드)라
        # 이름이 정확히 일치하지 않을 수 있다 — 프론트는 이 목록을 별도 선택 UI로 보여준다.
        "member_rebar_by_zone": compute_member_rebar_detail(members),
    }
