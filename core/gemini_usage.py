"""Gemini API usage tracking for ChickenBanana Lab.

The tracker wraps ``google.genai.Client`` once during Django start-up. Every
sync ``client.models.generate_content(...)`` call is recorded without storing
the prompt, response body, uploaded drawing bytes, or API key.
"""

from __future__ import annotations

import inspect
import threading
import time
from functools import wraps


_install_lock = threading.Lock()
_db_warning_printed = False


FEATURE_LABELS = {
    "quantity_structural": "AI 수량산출 · 구조 부재",
    "quantity_architectural": "AI 수량산출 · 건축도면",
    "quantity_elevation": "AI 수량산출 · 입면/단면",
    "ai_post_body": "AI 글 · 본문 생성",
    "ai_recent_issue": "AI 글 · 최근 이슈 검색",
    "ai_headline": "AI 글 · 제목/썸네일 문구",
    "ai_factcheck": "AI 글 · 팩트체크",
    "ai_translation": "AI 글 · 번역/다국어",
    "ai_topic_planning": "AI 글 · 주제 기획",
    "ai_keyword_recommendation": "AI 글 · 키워드 추천",
    "ai_fallback_topics": "AI 기본글감 생성",
    "ai_image": "AI 이미지 생성",
    "naver_keyword_search": "오늘의 키워드 · Gemini 검색",
    "other": "기타 Gemini 호출",
}


# More-specific functions must be checked before generic wrapper functions.
_FEATURE_STACK_RULES = (
    ("_extract_structural_members_one_batch", "quantity_structural"),
    ("extract_elevation_section_data", "quantity_elevation"),
    ("analyze_with_gemini", "quantity_architectural"),
    ("generate_ai_fallback_topics", "ai_fallback_topics"),
    ("generate_image_bytes", "ai_image"),
    ("_cbl_generate_title_thumbnail_pair", "ai_headline"),
    ("_cbl_review_generated_post", "ai_factcheck"),
    ("_cbl_force_grounded_factcheck", "ai_factcheck"),
    ("_cbl_strict_grounded_generate", "ai_factcheck"),
    ("build_recent_issue_context", "ai_recent_issue"),
    ("generate_english_ai_post", "ai_translation"),
    ("generate_post_topics", "ai_topic_planning"),
    ("recommend_today_keywords", "ai_keyword_recommendation"),
    ("_cbl_v22_generate_group", "naver_keyword_search"),
    ("generate_ai_post", "ai_post_body"),
)


def _safe_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_value(usage, *names):
    if usage is None:
        return 0
    for name in names:
        try:
            if isinstance(usage, dict) and name in usage:
                return _safe_int(usage.get(name))
            value = getattr(usage, name, None)
            if value is not None:
                return _safe_int(value)
        except Exception:
            continue
    return 0


def _summarize_contents(value, depth=0):
    """Return (text characters, image/file parts) without retaining content."""
    if value is None or depth > 6:
        return 0, 0
    if isinstance(value, str):
        return len(value), 0
    if isinstance(value, (bytes, bytearray, memoryview)):
        return 0, 1
    if isinstance(value, (list, tuple)):
        chars = parts = 0
        for item in value:
            item_chars, item_parts = _summarize_contents(item, depth + 1)
            chars += item_chars
            parts += item_parts
        return chars, parts
    if isinstance(value, dict):
        chars = parts = 0
        for key, item in value.items():
            if str(key).lower() in {"data", "bytes", "blob"}:
                if item:
                    parts += 1
                continue
            item_chars, item_parts = _summarize_contents(item, depth + 1)
            chars += item_chars
            parts += item_parts
        return chars, parts

    chars = 0
    parts = 0
    try:
        text_value = getattr(value, "text", None)
        if isinstance(text_value, str):
            chars += len(text_value)
    except Exception:
        pass
    try:
        inline_data = getattr(value, "inline_data", None)
        file_data = getattr(value, "file_data", None)
        if inline_data is not None or file_data is not None:
            parts += 1
    except Exception:
        pass
    return chars, parts


def _infer_call_context():
    names = []
    callsite = ""
    batch_index = None
    batch_total = None
    drawing_type = ""

    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame else None
        for _ in range(24):
            if frame is None:
                break
            module_name = str(frame.f_globals.get("__name__", ""))
            function_name = str(frame.f_code.co_name or "")
            if module_name != __name__:
                names.append(function_name)
                if not callsite:
                    callsite = f"{module_name}.{function_name}"[:240]

                local_values = frame.f_locals
                if batch_index is None and "batch_idx" in local_values:
                    batch_index = _safe_int(local_values.get("batch_idx")) or None
                if batch_total is None and "total_batches" in local_values:
                    batch_total = _safe_int(local_values.get("total_batches")) or None
                if not drawing_type and "drawing_type" in local_values:
                    drawing_type = str(local_values.get("drawing_type") or "")[:80]
            frame = frame.f_back
    finally:
        del frame

    feature = "other"
    for function_name, feature_name in _FEATURE_STACK_RULES:
        if function_name in names:
            feature = feature_name
            break

    return {
        "feature": feature,
        "callsite": callsite,
        "batch_index": batch_index,
        "batch_total": batch_total,
        "drawing_type": drawing_type,
    }


def _save_usage_log(**values):
    global _db_warning_printed
    try:
        from django.apps import apps

        model = apps.get_model("core", "GeminiUsageLog")
        if model is None:
            return
        model.objects.create(**values)
    except Exception as error:
        # Usage logging must never break a user-facing Gemini request. This is
        # expected before migration 0030 has been applied.
        if not _db_warning_printed:
            _db_warning_printed = True
            print(
                "[GEMINI_USAGE_LOG_SKIPPED]",
                f"{type(error).__name__}: {str(error)[:240]}",
            )


class _TrackedModelsProxy:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def generate_content(self, *args, **kwargs):
        started = time.perf_counter()
        context = _infer_call_context()
        model_name = str(kwargs.get("model") or (args[0] if args else "") or "")[:160]
        contents = kwargs.get("contents")
        if contents is None and len(args) > 1:
            contents = args[1]
        input_chars, image_inputs = _summarize_contents(contents)

        try:
            response = self._inner.generate_content(*args, **kwargs)
        except Exception as error:
            _save_usage_log(
                feature=context["feature"],
                model=model_name,
                prompt_tokens=0,
                output_tokens=0,
                total_tokens=0,
                cached_tokens=0,
                thoughts_tokens=0,
                tool_tokens=0,
                input_characters=input_chars,
                image_inputs=image_inputs,
                duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
                is_success=False,
                error_type=type(error).__name__[:120],
                error_message=str(error)[:1000],
                callsite=context["callsite"],
                batch_index=context["batch_index"],
                batch_total=context["batch_total"],
                metadata={"drawing_type": context["drawing_type"]},
            )
            raise

        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = _usage_value(usage, "prompt_token_count", "prompt_tokens")
        output_tokens = _usage_value(
            usage,
            "candidates_token_count",
            "output_token_count",
            "output_tokens",
        )
        cached_tokens = _usage_value(
            usage,
            "cached_content_token_count",
            "cached_token_count",
        )
        thoughts_tokens = _usage_value(usage, "thoughts_token_count", "thinking_token_count")
        tool_tokens = _usage_value(usage, "tool_use_prompt_token_count", "tool_token_count")
        total_tokens = _usage_value(usage, "total_token_count", "total_tokens")
        if not total_tokens:
            total_tokens = prompt_tokens + output_tokens + thoughts_tokens + tool_tokens

        _save_usage_log(
            feature=context["feature"],
            model=model_name,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            thoughts_tokens=thoughts_tokens,
            tool_tokens=tool_tokens,
            input_characters=input_chars,
            image_inputs=image_inputs,
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            is_success=True,
            error_type="",
            error_message="",
            callsite=context["callsite"],
            batch_index=context["batch_index"],
            batch_total=context["batch_total"],
            metadata={"drawing_type": context["drawing_type"]},
        )
        return response


class _TrackedClientProxy:
    def __init__(self, inner):
        self._inner = inner
        self.models = _TrackedModelsProxy(inner.models)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __enter__(self):
        enter = getattr(self._inner, "__enter__", None)
        if enter:
            enter()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        exit_method = getattr(self._inner, "__exit__", None)
        if exit_method:
            return exit_method(exc_type, exc_value, traceback)
        return False


def install_gemini_usage_tracking():
    """Install the process-wide google.genai.Client wrapper once."""
    with _install_lock:
        try:
            from google import genai
        except Exception as error:
            print("[GEMINI_USAGE_TRACKER_UNAVAILABLE]", type(error).__name__)
            return False

        if getattr(genai, "_cbl_usage_tracking_installed", False):
            return True

        original_client = genai.Client

        @wraps(original_client)
        def tracked_client(*args, **kwargs):
            client = original_client(*args, **kwargs)
            if isinstance(client, _TrackedClientProxy):
                return client
            return _TrackedClientProxy(client)

        genai.Client = tracked_client
        genai._cbl_usage_tracking_installed = True
        genai._cbl_original_client = original_client
        print("[GEMINI_USAGE_TRACKER_READY]")
        return True

