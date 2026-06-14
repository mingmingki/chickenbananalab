#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f "manage.py" || ! -f "core/templates/core/base.html" ]]; then
    echo "오류: ChickenBananaLab 프로젝트 최상단에서 실행해주세요."
    exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="core/templates/core/base.html.bak.keyword_space_fix_${STAMP}"
cp core/templates/core/base.html "$BACKUP"
echo "백업 완료: $BACKUP"

python3 - <<'PY'
from pathlib import Path

path = Path("core/templates/core/base.html")
text = path.read_text(encoding="utf-8")

old = '''    document.addEventListener("input", function (event) {
        if (event.target.closest("#cblForceTopRowPanel")) {
            syncForm();
        }
    }, true);
'''

new = '''    document.addEventListener("input", function (event) {
        if (!event.target.closest("#cblForceTopRowPanel")) {
            return;
        }

        /*
         * 키워드 입력 중에는 syncForm()이 id/name/hidden 값을 다시 쓰면서
         * 다른 MutationObserver와 충돌해 끝 공백이 사라질 수 있다.
         * 키워드는 change/submit 시 최종 동기화하고,
         * 카테고리·이미지 개수만 입력 즉시 동기화한다.
         */
        if (event.target.classList.contains("cbl-row-keyword")) {
            return;
        }

        syncForm();
    }, true);
'''

count = text.count(old)
if count != 1:
    raise SystemExit(
        f"수정 대상 input 이벤트를 정확히 1개 찾아야 하지만 {count}개를 찾았습니다."
    )

text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("키워드 스페이스 입력 충돌 수정 완료")
PY

python3 manage.py check

echo
echo "패치 완료"
echo "- 키워드 입력 중 syncForm 재실행 차단"
echo "- 스페이스 및 한글 조합 입력 보존"
echo "- change/submit 시 최종 데이터 동기화 유지"
echo
echo "로컬 실행:"
echo "python manage.py runserver"
