#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import re

TARGET = Path("core/templates/core/post_form.html")

if not TARGET.exists():
    raise SystemExit(f"파일을 찾을 수 없습니다: {TARGET}")

text = TARGET.read_text(encoding="utf-8")

MARKER = "CBL_EDITOR_HIGHLIGHT_START"
if MARKER in text:
    print("이미 하이라이트 기능이 적용되어 있습니다.")
    raise SystemExit(0)

backup = TARGET.with_name(
    f"{TARGET.name}.bak.highlight_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
)
backup.write_text(text, encoding="utf-8")

toolbar_html = r'''
                <!-- CBL_EDITOR_HIGHLIGHT_START -->
                <select
                    id="editorHighlightColor"
                    title="하이라이트 색상"
                    onmousedown="saveSelection()"
                    onchange="changeHighlightColor(this.value); this.value='';"
                >
                    <option value="">하이라이트</option>
                    <option value="#fff59d">노랑</option>
                    <option value="#fde68a">진한 노랑</option>
                    <option value="#fbcfe8">분홍</option>
                    <option value="#bbf7d0">초록</option>
                    <option value="#bfdbfe">파랑</option>
                    <option value="#ddd6fe">보라</option>
                    <option value="#ffffff">하이라이트 해제</option>
                </select>
                <!-- CBL_EDITOR_HIGHLIGHT_END -->
'''

color_input_pattern = re.compile(
    r'''(<input\s*
        type=["']color["'][\s\S]*?
        title=["']직접\s*글자색\s*선택["'][\s\S]*?
        >)''',
    re.VERBOSE,
)

match = color_input_pattern.search(text)

if match:
    insert_at = match.end()
    text = text[:insert_at] + "\n" + toolbar_html + text[insert_at:]
else:
    text_color_select_pattern = re.compile(
        r'''(<select[^>]*onchange=["']changeTextColor\(this\.value\)["'][^>]*>[\s\S]*?</select>)''',
        re.IGNORECASE,
    )
    match = text_color_select_pattern.search(text)

    if match:
        insert_at = match.end()
        text = text[:insert_at] + "\n" + toolbar_html + text[insert_at:]
    else:
        raise SystemExit(
            "툴바의 글자색 영역을 찾지 못했습니다. "
            f"원본은 백업했습니다: {backup}"
        )

highlight_js = r'''
    // CBL_EDITOR_HIGHLIGHT_JS_START
    function changeHighlightColor(color) {
        if (!color || !editor) {
            return;
        }

        clearDefaultText();
        restoreSelection();

        try {
            document.execCommand("styleWithCSS", false, true);
        } catch (error) {
            // 일부 브라우저에서는 styleWithCSS를 지원하지 않아도 계속 진행 가능
        }

        let applied = false;

        try {
            applied = document.execCommand("hiliteColor", false, color);
        } catch (error) {
            applied = false;
        }

        if (!applied) {
            document.execCommand("backColor", false, color);
        }

        saveSelection();
        updateCharCount();
    }
    // CBL_EDITOR_HIGHLIGHT_JS_END

'''

js_anchor = re.search(r'\n\s*function\s+formatDoc\s*\(', text)

if not js_anchor:
    raise SystemExit(
        "JavaScript의 formatDoc() 위치를 찾지 못했습니다. "
        f"원본은 백업했습니다: {backup}"
    )

text = text[:js_anchor.start()] + "\n" + highlight_js + text[js_anchor.start():]

TARGET.write_text(text, encoding="utf-8")

print("하이라이트 기능 적용 완료")
print(f"수정 파일: {TARGET}")
print(f"백업 파일: {backup}")
print("기능: 노랑/진한 노랑/분홍/초록/파랑/보라/하이라이트 해제")
