#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import re

TARGET = Path("core/templates/core/post_form.html")

if not TARGET.exists():
    raise SystemExit(f"파일을 찾을 수 없습니다: {TARGET}")

text = TARGET.read_text(encoding="utf-8")

if "CBL_EDITOR_HIGHLIGHT_START" not in text:
    raise SystemExit(
        "하이라이트 선택창을 찾지 못했습니다. "
        "먼저 기존 하이라이트 기능이 적용되어 있어야 합니다."
    )

if "CBL_EDITOR_HIGHLIGHT_JS_START" not in text:
    raise SystemExit(
        "하이라이트 JavaScript 영역을 찾지 못했습니다."
    )

backup = TARGET.with_name(
    f"{TARGET.name}.bak.highlight_v3_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
)
backup.write_text(text, encoding="utf-8")

# 하이라이트 메뉴를 누르기 직전에 선택 영역을 안전하게 보관합니다.
select_pattern = re.compile(
    r'(<select\b[^>]*id=["\']editorHighlightColor["\'][^>]*)(>)',
    flags=re.IGNORECASE | re.DOTALL,
)

select_match = select_pattern.search(text)
if not select_match:
    raise SystemExit(f"하이라이트 선택창을 찾지 못했습니다. 백업: {backup}")

select_open = select_match.group(1)
select_open = re.sub(
    r'\s+onmousedown=["\'][^"\']*["\']',
    "",
    select_open,
    flags=re.IGNORECASE,
)
select_open = re.sub(
    r'\s+onpointerdown=["\'][^"\']*["\']',
    "",
    select_open,
    flags=re.IGNORECASE,
)
select_open += ' onpointerdown="cblCaptureHighlightRange()"'

text = (
    text[:select_match.start()]
    + select_open
    + select_match.group(2)
    + text[select_match.end():]
)

# 하이라이트 해제 옵션 값 통일
text = re.sub(
    r'<option\s+value=["\'](?:#ffffff|transparent)["\']>\s*하이라이트\s*해제\s*</option>',
    '<option value="clear">하이라이트 해제</option>',
    text,
    count=1,
    flags=re.IGNORECASE,
)

new_js = r'''
    // CBL_EDITOR_HIGHLIGHT_JS_START
    let cblHighlightRange = null;

    function cblNodeInsideEditor(node) {
        if (!node || !editor) {
            return false;
        }

        const element = node.nodeType === Node.ELEMENT_NODE
            ? node
            : node.parentElement;

        return element === editor || Boolean(element && editor.contains(element));
    }

    function cblCaptureHighlightRange() {
        if (!editor) {
            return;
        }

        const selection = window.getSelection();

        if (
            !selection
            || selection.rangeCount === 0
            || selection.isCollapsed
            || !cblNodeInsideEditor(selection.anchorNode)
            || !cblNodeInsideEditor(selection.focusNode)
        ) {
            return;
        }

        cblHighlightRange = selection.getRangeAt(0).cloneRange();
    }

    function cblRestoreHighlightRange() {
        if (!cblHighlightRange) {
            return null;
        }

        const selection = window.getSelection();
        selection.removeAllRanges();

        try {
            selection.addRange(cblHighlightRange);
        } catch (error) {
            cblHighlightRange = null;
            return null;
        }

        return cblHighlightRange;
    }

    function cblIsHighlightClass(className) {
        const value = String(className || "").toLowerCase();

        return (
            value === "cbl-ai-highlight"
            || value.startsWith("cbl-highlight-")
            || value.includes("highlight")
        );
    }

    function cblUnwrapNode(node) {
        if (!node || !node.parentNode) {
            return;
        }

        const parent = node.parentNode;

        while (node.firstChild) {
            parent.insertBefore(node.firstChild, node);
        }

        parent.removeChild(node);
    }

    function cblCleanHighlightMarkup(fragment) {
        if (!fragment) {
            return fragment;
        }

        const elements = Array.from(
            fragment.querySelectorAll(
                "mark, span, [class*='highlight'], [style*='background']"
            )
        ).reverse();

        elements.forEach((element) => {
            Array.from(element.classList || []).forEach((className) => {
                if (cblIsHighlightClass(className)) {
                    element.classList.remove(className);
                }
            });

            element.style.removeProperty("background");
            element.style.removeProperty("background-color");
            element.style.removeProperty("background-image");
            element.style.removeProperty("box-shadow");

            if (!element.getAttribute("style")) {
                element.removeAttribute("style");
            }

            if (!element.getAttribute("class")) {
                element.removeAttribute("class");
            }

            const tagName = String(element.tagName || "").toLowerCase();
            const hasMeaningfulAttributes = element.attributes.length > 0;

            if (
                tagName === "mark"
                || (tagName === "span" && !hasMeaningfulAttributes)
            ) {
                cblUnwrapNode(element);
            }
        });

        return fragment;
    }

    function cblBuildHighlightWrapper(color) {
        const span = document.createElement("span");
        span.className = "cbl-ai-highlight cbl-highlight-manual";

        // 기존 CSS 그라데이션이나 클래스 색상보다 항상 우선하도록 저장
        span.style.setProperty("background", color, "important");
        span.style.setProperty("background-color", color, "important");

        return span;
    }

    function cblSelectInsertedNode(node) {
        if (!node) {
            return;
        }

        const selection = window.getSelection();
        const range = document.createRange();

        try {
            range.selectNodeContents(node);
            selection.removeAllRanges();
            selection.addRange(range);
            cblHighlightRange = range.cloneRange();
        } catch (error) {
            cblHighlightRange = null;
        }
    }

    function cblNotifyEditorChanged() {
        if (!editor) {
            return;
        }

        editor.dispatchEvent(new Event("input", { bubbles: true }));

        if (typeof updateCharCount === "function") {
            updateCharCount();
        }

        if (typeof saveSelection === "function") {
            saveSelection();
        }
    }

    function changeHighlightColor(color) {
        if (!color || !editor) {
            return;
        }

        if (typeof clearDefaultText === "function") {
            clearDefaultText();
        }

        const range = cblRestoreHighlightRange();

        if (!range || range.collapsed) {
            alert("하이라이트를 바꿀 문장을 먼저 드래그해주세요.");
            return;
        }

        /*
         * 핵심 동작:
         * 1. 선택한 부분을 기존 강조 태그째 추출
         * 2. 기존 하이라이트 클래스·배경색만 제거
         * 3. 새 색상을 한 번에 다시 적용
         *
         * 따라서 '기존 강조 해제 → 다시 선택 → 새 색 적용' 과정이 필요 없습니다.
         */
        const selectedFragment = range.extractContents();
        cblCleanHighlightMarkup(selectedFragment);

        let insertedNode = null;

        if (color === "clear") {
            insertedNode = document.createElement("span");
            insertedNode.appendChild(selectedFragment);
            range.insertNode(insertedNode);

            const firstChild = insertedNode.firstChild;
            const lastChild = insertedNode.lastChild;
            const parent = insertedNode.parentNode;

            while (insertedNode.firstChild) {
                parent.insertBefore(insertedNode.firstChild, insertedNode);
            }

            parent.removeChild(insertedNode);
            parent.normalize();

            if (firstChild && lastChild) {
                const selection = window.getSelection();
                const cleanRange = document.createRange();

                try {
                    cleanRange.setStartBefore(firstChild);
                    cleanRange.setEndAfter(lastChild);
                    selection.removeAllRanges();
                    selection.addRange(cleanRange);
                    cblHighlightRange = cleanRange.cloneRange();
                } catch (error) {
                    cblHighlightRange = null;
                }
            }
        } else {
            const wrapper = cblBuildHighlightWrapper(color);
            wrapper.appendChild(selectedFragment);
            range.insertNode(wrapper);
            insertedNode = wrapper;
            cblSelectInsertedNode(wrapper);
        }

        editor.normalize();
        cblNotifyEditorChanged();
    }

    if (editor) {
        editor.addEventListener("mouseup", cblCaptureHighlightRange);
        editor.addEventListener("keyup", cblCaptureHighlightRange);
        editor.addEventListener("touchend", cblCaptureHighlightRange);
    }
    // CBL_EDITOR_HIGHLIGHT_JS_END
'''

js_pattern = re.compile(
    r'\s*// CBL_EDITOR_HIGHLIGHT_JS_START[\s\S]*?// CBL_EDITOR_HIGHLIGHT_JS_END\s*',
    flags=re.MULTILINE,
)

if not js_pattern.search(text):
    raise SystemExit(f"기존 하이라이트 JS를 찾지 못했습니다. 백업: {backup}")

text = js_pattern.sub("\n" + new_js + "\n", text, count=1)

TARGET.write_text(text, encoding="utf-8")

print("하이라이트 원클릭 교체 V3 적용 완료")
print(f"수정 파일: {TARGET}")
print(f"백업 파일: {backup}")
print("이제 기존 하이라이트 문장을 드래그한 뒤 새 색을 한 번만 선택하면 바로 교체됩니다.")
