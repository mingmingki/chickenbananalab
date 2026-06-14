#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f "manage.py" || ! -f "core/templates/core/base.html" ]]; then
    echo "오류: ChickenBananaLab 프로젝트 최상단에서 실행해주세요."
    exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="core/templates/core/base.html.bak.keyword_space_final_${STAMP}"
cp core/templates/core/base.html "$BACKUP"
echo "백업 완료: $BACKUP"

python3 - <<'PY'
from pathlib import Path

path = Path("core/templates/core/base.html")
text = path.read_text(encoding="utf-8")

# 1) syncPayload가 사용자가 입력 중인 키워드 칸을 trim 값으로 덮어쓰는 코드 제거
old_overwrite = '''            if (categoryEl) categoryEl.value = first.category;
            if (imageEl) imageEl.value = first.image_count;
            if (keywordEl) keywordEl.value = first.keyword;
'''

new_overwrite = '''            if (categoryEl) categoryEl.value = first.category;
            if (imageEl) imageEl.value = first.image_count;

            /*
             * 사용자가 입력 중인 키워드 값은 절대 다시 쓰지 않는다.
             * first.keyword는 trim된 값이라 입력 직후의 끝 공백을 지워버린다.
             */
'''

overwrite_count = text.count(old_overwrite)
if overwrite_count != 1:
    raise SystemExit(
        f"키워드 덮어쓰기 구간을 정확히 1개 찾아야 하지만 {overwrite_count}개를 찾았습니다."
    )

text = text.replace(old_overwrite, new_overwrite, 1)

# 2) 키워드 타이핑 중에는 syncPayload를 실행하지 않음
old_listener = '''    document.addEventListener("input", function (e) {
        const form = document.getElementById("aiPostForm");
        if (!form) return;

        if (
            e.target &&
            (
                e.target.classList.contains("cbl-row-category") ||
                e.target.classList.contains("cbl-row-image-count") ||
                e.target.classList.contains("cbl-row-keyword")
            )
        ) {
            syncPayload(form);
        }
    }, true);
'''

new_listener = '''    document.addEventListener("input", function (e) {
        const form = document.getElementById("aiPostForm");
        if (!form || !e.target) return;

        /*
         * 카테고리와 이미지 개수는 즉시 동기화한다.
         * 키워드는 타이핑 중 동기화하면 trim된 값이 다시 입력창에 반영되어
         * 스페이스와 한글 조합 입력이 깨질 수 있으므로 제외한다.
         */
        if (
            e.target.classList.contains("cbl-row-category") ||
            e.target.classList.contains("cbl-row-image-count")
        ) {
            syncPayload(form);
        }
    }, true);

    document.addEventListener("change", function (e) {
        const form = document.getElementById("aiPostForm");
        if (!form || !e.target) return;

        if (
            e.target.classList.contains("cbl-row-category") ||
            e.target.classList.contains("cbl-row-image-count") ||
            e.target.classList.contains("cbl-row-keyword")
        ) {
            syncPayload(form);
        }
    }, true);
'''

listener_count = text.count(old_listener)
if listener_count != 1:
    raise SystemExit(
        f"AI_ROW_PAYLOAD_LOCK input 이벤트 구간을 정확히 1개 찾아야 하지만 {listener_count}개를 찾았습니다."
    )

text = text.replace(old_listener, new_listener, 1)

path.write_text(text, encoding="utf-8")
print("키워드 스페이스 최종 수정 완료")
PY

python3 manage.py check

echo
echo "최종 패치 완료"
echo "- 키워드 입력값 강제 덮어쓰기 제거"
echo "- 키워드 타이핑 중 trim 동기화 차단"
echo "- 입력 완료(change)와 submit 시 데이터 동기화 유지"
echo "- 카테고리/이미지 개수 즉시 동기화 유지"
echo
echo "로컬 실행:"
echo "python manage.py runserver"
