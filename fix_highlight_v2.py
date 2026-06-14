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
        "현재 post_form.html을 먼저 확인해야 합니다."
    )

if "CBL_EDITOR_HIGHLIGHT_JS_START" not in text:
    raise SystemExit(
        "하이라이트 JavaScript 영역을 찾지 못했습니다. "
        "현재 post_form.html을 먼저 확인해야 합니다."
    )

backup = TARGET.with_name(
    f"{TARGET.name}.bak.highlight_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
)
backup.write_text(text, encoding="utf-8")

# 선택창을 누르기 직전에 에디터 선택 영역을 별도로 저장합니다.
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

    function cblHighlightElementsInRange(range) {
        if (!range || !editor) {
            return [];
        }

        const selectors = [
            "mark",
            ".cbl-ai-highlight",
            "[class*='highlight']",
            "[style*='background']",
            "[style*='background-color']",
        ].join(",");

        return Array.from(editor.querySelectorAll(selectors)).filter((element) => {
            try {
                return range.intersectsNode(element);
            } catch (error) {
                return false;
            }
        });
    }

    function cblUnwrapElement(element) {
        if (!element || !element.parentNode) {
            return;
        }

        const parent = element.parentNode;

        while (element.firstChild) {
            parent.insertBefore(element.firstChild, element);
        }

        parent.removeChild(element);
        parent.normalize();
    }

    function cblClearHighlightElement(element) {
        if (!element) {
            return;
        }

        element.style.removeProperty("background");
        element.style.removeProperty("background-color");

        Array.from(element.classList || []).forEach((className) => {
            const lower = String(className || "").toLowerCase();

            if (
                lower === "cbl-ai-highlight"
                || lower.startsWith("cbl-highlight-")
                || lower.includes("highlight")
            ) {
                element.classList.remove(className);
            }
        });

        const tagName = String(element.tagName || "").toLowerCase();

        if (
            tagName === "mark"
            || tagName === "span"
            || element.attributes.length === 0
        ) {
            cblUnwrapElement(element);
        }
    }

    function cblSetHighlightElementColor(element, color) {
        if (!element) {
            return;
        }

        Array.from(element.classList || []).forEach((className) => {
            if (String(className || "").toLowerCase().startsWith("cbl-highlight-")) {
                element.classList.remove(className);
            }
        });

        // 기존 CSS의 linear-gradient 배경보다 우선하도록 inline !important로 저장합니다.
        element.style.setProperty("background", color, "important");
        element.style.setProperty("background-color", color, "important");
    }

    function cblNotifyEditorChanged() {
        if (!editor) {
            return;
        }

        editor.dispatchEvent(new Event("input", { bubbles: true }));

        if (typeof updateCharCount === "function") {
            updateCharCount();
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

        const existingHighlights = cblHighlightElementsInRange(range);

        if (existingHighlights.length > 0) {
            existingHighlights.forEach((element) => {
                if (color === "clear") {
                    cblClearHighlightElement(element);
                } else {
                    cblSetHighlightElementColor(element, color);
                }
            });

            cblNotifyEditorChanged();
            cblHighlightRange = null;
            return;
        }

        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);

        try {
            document.execCommand("styleWithCSS", false, true);
        } catch (error) {
            // 지원하지 않는 브라우저에서도 아래 명령은 계속 시도합니다.
        }

        if (color === "clear") {
            try {
                document.execCommand("hiliteColor", false, "transparent");
                document.execCommand("backColor", false, "transparent");
            } catch (error) {
                // 기존 강조 요소가 없으면 변경할 내용이 없을 수 있습니다.
            }
        } else {
            let applied = false;

            try {
                applied = document.execCommand("hiliteColor", false, color);
            } catch (error) {
                applied = false;
            }

            if (!applied) {
                document.execCommand("backColor", false, color);
            }
        }

        cblNotifyEditorChanged();
        cblHighlightRange = null;
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

# 기존 #ffffff 방식의 해제 옵션도 보정합니다.
text = re.sub(
    r'<option\s+value=["\']#ffffff["\']>\s*하이라이트\s*해제\s*</option>',
    '<option value="clear">하이라이트 해제</option>',
    text,
    count=1,
    flags=re.IGNORECASE,
)

TARGET.write_text(text, encoding="utf-8")

print("하이라이트 수정 기능 V2 적용 완료")
print(f"수정 파일: {TARGET}")
print(f"백업 파일: {backup}")
print("지원 대상: AI class 하이라이트, <mark>, inline background, 새 선택 영역")
