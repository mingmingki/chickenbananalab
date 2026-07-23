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


def _concrete_fck_mpa(general_spec, category=None):
    """
    콘크리트 설계기준강도(MPa).
    1순위: general_spec.concrete_fck_table에서 category(기초/기둥/보/슬래브/전단벽/계단)와
      일치하는 행 (도면에 부재 종류별로 Fck가 다르게 표기된 경우 대응, 예: 기초 24MPa/그 외 30MPa)
    2순위: general_spec.concrete_fck_mpa 단일 대표값
    3순위: 24MPa 기본값
    """
    table = (general_spec or {}).get("concrete_fck_table") or []
    if category and table:
        for row in table:
            row_cat = row.get("category")
            fck = row.get("fck_mpa")
            if row_cat == category and isinstance(fck, (int, float)) and fck > 0:
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


def _development_length_straight_m(bar_size, general_spec, top_bar=False, adequate_cover=True, category=None):
    """직선철근 인장정착길이(ld, m) — KDS 14 20 52 근사 공식."""
    db = _bar_diameter_mm(bar_size)
    if db is None:
        return 0.0
    fy = _rebar_fy_mpa(general_spec, bar_size)
    fck = _concrete_fck_mpa(general_spec, category)
    psi_s = 0.8 if db <= 19 else 1.0
    case_coeff = 0.6 if adequate_cover else 0.9
    psi_t = 1.3 if top_bar else 1.0
    ld_mm = case_coeff * db * fy / math.sqrt(fck) * psi_s * psi_t
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


def get_splice_length(bar_size, general_spec, top_bar=False, category=None):
    """
    이음길이(m)를 구한다.
    1순위: 도면 구조일반사항에서 읽은 표(general_spec.lap_splice_table, position이 일치하는 행 우선)
    2순위: 공식 계산값 (B급 이음길이 = 1.3 × 직선 정착길이, 부재 카테고리별 Fck·철근지름별 강종 반영)
      — 이 경우 "확인 필요"로 표시
    ※ 겹침이음(splice)은 실무상 항상 직선 구간에서 이뤄지므로 갈고리는 적용하지 않습니다.
    """
    table = (general_spec or {}).get("lap_splice_table") or []
    wanted_pos = "상부" if top_bar else None
    for row in table:
        if row.get("bar_size") != bar_size or not row.get("length_m"):
            continue
        row_pos = row.get("position")
        if wanted_pos is None or row_pos is None or row_pos == wanted_pos:
            return row["length_m"], "도면표기재"

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
    wanted_pos = "상부" if top_bar else None
    for row in table:
        if row.get("bar_size") != bar_size or not row.get("length_m"):
            continue
        if bool(row.get("hook", False)) != bool(hook):
            continue
        row_pos = row.get("position")
        if wanted_pos is None or row_pos is None or row_pos == wanted_pos:
            return row["length_m"], "도면표기재"

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
        L, W, T, n = it.get("length_m"), it.get("width_m"), it.get("thickness_m"), it.get("count", 1) or 1
        mark = it.get("mark", "?")
        if not all(isinstance(v, (int, float)) for v in (L, W, T)):
            warnings.append(f"기초 {mark}: 치수 누락으로 계산 제외")
            continue
        concrete += L * W * T * n
        formwork += 2 * (L + W) * T * n

        size = it.get("rebar_size")
        spacing = it.get("rebar_spacing_m")
        if size and spacing:
            bars_along_w = math.ceil(W / spacing)  # L방향으로 뻗는 바, W폭에 걸쳐 spacing 간격 배치
            bars_along_l = math.ceil(L / spacing)  # W방향으로 뻗는 바, L폭에 걸쳐 spacing 간격 배치

            l_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)
            w_splices = max(0, math.ceil(W / STOCK_BAR_LENGTH_M) - 1)
            splice_len, splice_src = get_splice_length(size, general_spec, category="기초")

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
    띠철근 중량 = (층고/간격) x 둘레(근사) x 단위중량 x 부재개수 (이음 없음, 개별 폐합 형상)
    ※ height_m이 없으면 fallback_height_m(입면/단면도에서 읽은 층고)으로 대체 시도.
    """
    concrete = 0.0
    formwork = 0.0
    rebar_total = 0.0
    warnings = []
    for it in items:
        w, d, h, n = it.get("width_m"), it.get("depth_m"), it.get("height_m"), it.get("count", 1) or 1
        mark = it.get("mark", "?")
        if not isinstance(h, (int, float)) and fallback_height_m:
            h = fallback_height_m
            warnings.append(f"기둥 {mark}: 층고 정보 없어 입면/단면도 층고값 {h}m로 대체 사용 — 확인 필요")
        if not all(isinstance(v, (int, float)) for v in (w, d, h)):
            warnings.append(f"기둥 {mark}: 치수 누락으로 계산 제외")
            continue
        concrete += w * d * h * n
        perimeter = 2 * (w + d)
        formwork += perimeter * h * n

        main_size = it.get("main_rebar_size")
        main_count = it.get("main_rebar_count")
        if main_size and main_count:
            hook = bool(it.get("has_hook"))
            splice_len, splice_src = get_splice_length(main_size, general_spec, category="기둥")
            anchor_len, anchor_src = get_anchorage_length(main_size, general_spec, top_bar=False, hook=hook, category="기둥")
            length_per_bar = h + splice_len + anchor_len  # 층당 1개소 이음 + 하부 1개소 정착 가정
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
            num_ties = math.ceil(h / tie_spacing) * n
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
        w, d, L, n = it.get("width_m"), it.get("depth_m"), it.get("length_m"), it.get("count", 1) or 1
        mark = it.get("mark", "?")
        if not all(isinstance(v, (int, float)) for v in (w, d, L)):
            warnings.append(f"보 {mark}: 치수 누락으로 계산 제외")
            continue
        concrete += w * d * L * n
        formwork += (w + 2 * d) * L * n

        main_size = it.get("main_rebar_size")
        main_count = it.get("main_rebar_count")
        if main_size and main_count:
            top_bar = bool(it.get("is_top_bar"))
            hook = bool(it.get("has_hook"))
            num_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)
            splice_len, splice_src = get_splice_length(main_size, general_spec, top_bar=top_bar, category="보")
            anchor_len, anchor_src = get_anchorage_length(main_size, general_spec, top_bar=top_bar, hook=hook, category="보")
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
            num_stirrups = math.ceil(L / stirrup_spacing) * n
            stirrup_len = 2 * (w + d)
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
        warnings.append(
            f"{member_label} {mark}: 개구부 {total}㎡ 콘크리트/거푸집에서 차감함 "
            "(인방보 등 개구부 보강철근은 미반영 — 별도 확인 필요)"
        )
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
        area, T, n = it.get("area_m2"), it.get("thickness_m"), it.get("count", 1) or 1
        mark = it.get("mark", "?")
        if not all(isinstance(v, (int, float)) for v in (area, T)):
            warnings.append(f"슬래브 {mark}: 치수 누락으로 계산 제외")
            continue

        opening_area = _opening_area_m2(it, mark, "슬래브", warnings)
        net_area = max(0.0, area - opening_area)

        concrete += net_area * T * n
        formwork += net_area * n

        size = it.get("rebar_size")
        spacing = it.get("rebar_spacing_m")
        if size and spacing:
            top_bar = bool(it.get("is_top_bar"))
            hook = bool(it.get("has_hook"))
            avg_span = math.sqrt(net_area) if net_area > 0 else 0.0
            num_bars = math.ceil(avg_span / spacing) if avg_span else 0

            num_splices = max(0, math.ceil(avg_span / STOCK_BAR_LENGTH_M) - 1) if avg_span else 0
            length_per_bar = avg_span
            if num_splices:
                splice_len, splice_src = get_splice_length(size, general_spec, top_bar=top_bar, category="슬래브")
                length_per_bar = avg_span + num_splices * splice_len
                warnings.append(
                    f"슬래브 {mark}: 스팬({round(avg_span,2)}m)이 철근 장대길이({STOCK_BAR_LENGTH_M}m)를 넘어 "
                    f"가닥당 이음 {num_splices}개소 반영함"
                )
                if splice_src == "추정값(확인필요)":
                    warnings.append(f"슬래브 {mark}: {size} 이음길이를 구조일반사항에서 못 찾아 {splice_len}m로 추정했습니다 — 확인 필요")

            anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=top_bar, hook=hook, category="슬래브")
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
    """
    concrete = 0.0
    formwork = 0.0
    rebar_total = 0.0
    warnings = []
    for it in items:
        L, H, T, n = it.get("length_m"), it.get("height_m"), it.get("thickness_m"), it.get("count", 1) or 1
        mark = it.get("mark", "?")
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

        size = it.get("rebar_size")
        spacing = it.get("rebar_spacing_m")
        if size and spacing:
            hook = bool(it.get("has_hook"))
            splice_len, splice_src = get_splice_length(size, general_spec, category="전단벽")
            anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=False, hook=hook, category="전단벽")
            vertical_bars = math.ceil(L / spacing)
            horizontal_bars = math.ceil(H / spacing)
            h_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)

            vertical_len = vertical_bars * (H + splice_len + anchor_len)  # 하부(기초 접합부) 1개소 정착
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
                    extra_len = H + splice_len + anchor_len
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
        W, L, T, n = it.get("width_m"), it.get("length_m"), it.get("thickness_m"), it.get("count", 1) or 1
        mark = it.get("mark", "?")
        if not all(isinstance(v, (int, float)) for v in (W, L, T)):
            warnings.append(f"계단 {mark}: 치수 누락으로 계산 제외")
            continue
        concrete += W * L * T * n
        formwork += W * L * n  # 경사면(하부) 거푸집 기준, 측판/챌판 거푸집은 미반영

        size = it.get("rebar_size")
        spacing = it.get("rebar_spacing_m")
        if size and spacing:
            top_bar = bool(it.get("is_top_bar"))
            hook = bool(it.get("has_hook"))
            num_splices = max(0, math.ceil(L / STOCK_BAR_LENGTH_M) - 1)
            splice_len, splice_src = get_splice_length(size, general_spec, top_bar=top_bar, category="계단")
            anchor_len, anchor_src = get_anchorage_length(size, general_spec, top_bar=top_bar, hook=hook, category="계단")
            main_bars = math.ceil(W / spacing)
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
                dist_bars = math.ceil(L / dist_spacing)
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
        area, n = it.get("area_m2"), it.get("count", 1) or 1
        if not isinstance(area, (int, float)):
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
        L, H, n = it.get("length_m"), it.get("height_m"), it.get("count", 1) or 1
        if not isinstance(L, (int, float)) or not isinstance(H, (int, float)):
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
        h, n = it.get("height_m"), it.get("count", 1) or 1
        if not isinstance(h, (int, float)):
            continue
        column_ea += 4 * (h / 0.9) * n

    beam_ea = 0.0
    for it in members.get("beams", []) or []:
        L, n = it.get("length_m"), it.get("count", 1) or 1
        if not isinstance(L, (int, float)):
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
        spec_lines.append(f"이음등급 {splice_class}급")
    else:
        all_warnings.append("구조일반사항에서 이음등급(A급/B급)을 확인하지 못했습니다 — 기본값(추정 이음길이)으로 계산했습니다")
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


def compute_rebar_bar_counts(members: dict) -> dict:
    """
    3D 매싱 뷰어에서 철근을 "라인"으로 그릴 때, 임의 개수가 아니라 실제 배근 수량에
    가까운 가닥 수를 쓰기 위한 집계. 중량(rebar_weight)이 아니라 "가닥 수"만 세는
    시각화 전용 개략 계산이며, calc_columns/calc_walls/calc_beams의 이음/스터럽 개수
    산정 로직과 동일한 방식(간격으로 나눠 올림)을 재사용한다.

    Returns: {"vertical_bar_count": int, "horizontal_ring_count": int}
      - vertical_bar_count: 기둥 주근 + 벽체 수직근 가닥 수 총합
      - horizontal_ring_count: 기둥 띠철근 + 벽체 수평근 + 보 스터럽 개수 총합
    """
    vertical = 0
    horizontal = 0

    for it in members.get("columns", []) or []:
        n = it.get("count", 1) or 1
        main_count = it.get("main_rebar_count")
        if isinstance(main_count, (int, float)) and main_count > 0:
            vertical += int(main_count) * n

        h = it.get("height_m")
        spacing = it.get("tie_spacing_m")
        if isinstance(h, (int, float)) and isinstance(spacing, (int, float)) and spacing > 0:
            horizontal += math.ceil(h / spacing) * n

    for it in members.get("walls", []) or []:
        n = it.get("count", 1) or 1
        L, H = it.get("length_m"), it.get("height_m")
        spacing = it.get("rebar_spacing_m")
        if isinstance(L, (int, float)) and isinstance(spacing, (int, float)) and spacing > 0:
            vertical += math.ceil(L / spacing) * n
        if isinstance(H, (int, float)) and isinstance(spacing, (int, float)) and spacing > 0:
            horizontal += math.ceil(H / spacing) * n

    for it in members.get("beams", []) or []:
        n = it.get("count", 1) or 1
        L = it.get("length_m")
        spacing = it.get("stirrup_spacing_m")
        if isinstance(L, (int, float)) and isinstance(spacing, (int, float)) and spacing > 0:
            horizontal += math.ceil(L / spacing) * n

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
    }
