#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import re

TARGET = Path("core/templates/core/post_form.html")

if not TARGET.exists():
    raise SystemExit(f"파일을 찾을 수 없습니다: {TARGET}")

text = TARGET.read_text(encoding="utf-8")

if "CBL_EDITOR_HIGHLIGHT_JS_START" not in text:
    raise SystemExit(
        "기존 하이라이트 기능이 아직 적용되지 않았습니다. "
        "먼저 add_editor_highlight.py를 실행해야 합니다."
    )

backup = TARGET.with_name(
    f"{TARGET.name}.bak.existing_highlight_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
)
backup.write_text(text, encoding="utf-8")

text = re.sub(
    r'<option\s+value=["\']#ffffff["\']>\s*하이라이트\s*해제\s*</option>',
    '<option value="clear">하이라이트 해제</option>',
    text,
    count=1,
    flags=re.IGNORECASE,
)

new_js = r'''
    // CBL_EDITOR_HIGHLIGHT_JS_START
    function getSelectedGeneratedHighlightSpans() {
        restoreSelection();

        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0) {
            return [];
        }

        const range = selection.getRangeAt(0);
        const targets = new Set();

        function addClosest(node) {
            if (!node) {
                return;
            }

            const element = node.nodeType === Node.ELEMENT_NODE
                ? node
                : node.parentElement;

            const closest = element?.closest?.(".cbl-ai-highlight");
            if (closest && editor.contains(closest)) {
                targets.add(closest);
            }
        }

        addClosest(range.startContainer);
        addClosest(range.endContainer);

        let root = range.commonAncestorContainer;
        if (root.nodeType !== Node.ELEMENT_NODE) {
            root = root.parentElement;
        }

        root?.querySelectorAll?.(".cbl-ai-highlight").forEach((element) => {
            try {
                if (range.intersectsNode(element)) {
                    targets.add(element);
                }
            } catch (error) {
                // 오래된 브라우저에서는 건너뜀
            }
        });

        return Array.from(targets);
    }

    function removeGeneratedHighlightSpan(span) {
        if (!span || !span.parentNode) {
            return;
        }

        const parent = span.parentNode;

        while (span.firstChild) {
            parent.insertBefore(span.firstChild, span);
        }

        parent.removeChild(span);
        parent.normalize();
    }

    function updateGeneratedHighlightSpans(color) {
        const spans = getSelectedGeneratedHighlightSpans();

        if (!spans.length) {
            return false;
        }

        const classMap = {
            "#fff59d": "cbl-highlight-yellow",
            "#fde68a": "cbl-highlight-yellow",
            "#fbcfe8": "cbl-highlight-pink",
            "#bbf7d0": "cbl-highlight-green",
            "#bfdbfe": "cbl-highlight-sky",
        };

        spans.forEach((span) => {
            Array.from(span.classList).forEach((className) => {
                if (className.startsWith("cbl-highlight-")) {
                    span.classList.remove(className);
                }
            });

            span.style.removeProperty("background");
            span.style.removeProperty("background-color");

            if (color === "clear") {
                removeGeneratedHighlightSpan(span);
                return;
            }

            const mappedClass = classMap[color];

            if (mappedClass) {
                span.classList.add(mappedClass);
            } else {
                span.style.backgroundColor = color;
            }
        });

        return true;
    }

    function changeHighlightColor(color) {
        if (!color || !editor) {
            return;
        }

        clearDefaultText();
        restoreSelection();

        // AI 자동글이 만든 class 기반 하이라이트를 우선 변경
        if (updateGeneratedHighlightSpans(color)) {
            saveSelection();
            updateCharCount();
            return;
        }

        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
            return;
        }

        if (color === "clear") {
            try {
                document.execCommand("styleWithCSS", false, true);
                document.execCommand("hiliteColor", false, "transparent");
                document.execCommand("backColor", false, "transparent");
            } catch (error) {
                // 수동 하이라이트 해제 실패 시 현재 선택 유지
            }

            saveSelection();
            updateCharCount();
            return;
        }

        try {
            document.execCommand("styleWithCSS", false, true);
        } catch (error) {
            // 일부 브라우저에서는 지원하지 않아도 계속 진행 가능
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

pattern = re.compile(
    r'\s*// CBL_EDITOR_HIGHLIGHT_JS_START[\s\S]*?// CBL_EDITOR_HIGHLIGHT_JS_END\s*',
    re.MULTILINE,
)

if not pattern.search(text):
    raise SystemExit(
        "기존 하이라이트 JavaScript 영역을 찾지 못했습니다. "
        f"백업 파일: {backup}"
    )

text = pattern.sub("\n" + new_js + "\n", text, count=1)

TARGET.write_text(text, encoding="utf-8")

print("기존 AI 자동 하이라이트 수정 기능 적용 완료")
print(f"수정 파일: {TARGET}")
print(f"백업 파일: {backup}")
print("기존 초록/노랑/분홍/하늘색 강조도 색상 변경 및 해제가 가능합니다.")
