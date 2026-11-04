"""수량산출 AI 호출: OpenAI Responses API로 도면 이미지와 글을 보내고 JSON 응답을 받는다.

프롬프트·응답 본문·도면은 저장하지 않는다(OpenAI 쪽 보관도 store=False로 끈다). 토큰 수만
다른 AI 기능과 같은 사용량 표(GeminiUsageLog)에 남겨 관리자 화면에서 함께 본다.
"""
import base64
import os
import time
from dataclasses import dataclass

import openai
from django.conf import settings

from . import gemini_usage

QUANTITY_AI_MODEL = os.environ.get("OPENAI_QUANTITY_MODEL") or "gpt-6.1-sol"
REQUEST_TIMEOUT_SEC = int(os.environ.get("QTY_AI_TIMEOUT_SEC", "900"))
MAX_RETRIES = 3
MODEL_MAX_OUTPUT_TOKENS = 128000
# auto는 어떤 모델이든 받는 값이다. 이미지는 긴 변 1536px 안팎으로 보내므로 읽을 때 더 줄지 않는다.
IMAGE_DETAIL = "auto"
# 추론 토큰도 max_output_tokens에 포함되므로, 호출부가 정한 한도가 JSON에 온전히 쓰이도록 더해 준다.
REASONING_HEADROOM = {"low": 8192, "medium": 16384}
INCOMPLETE_REASONS = {"max_output_tokens": "MAX_TOKENS", "content_filter": "SAFETY"}
_JSON_REMINDER = "반드시 JSON 객체 하나만 반환하세요."


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime_type: str = "image/jpeg"


def image_part(data, mime_type="image/jpeg"):
    return ImagePart(data=data, mime_type=mime_type)


@dataclass(frozen=True)
class AiResponse:
    text: str
    finish_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0


def _user_content(contents):
    content = []
    for item in contents:
        if isinstance(item, ImagePart):
            encoded = base64.b64encode(item.data).decode("ascii")
            content.append({"type": "input_image", "detail": IMAGE_DETAIL,
                            "image_url": f"data:{item.mime_type};base64,{encoded}"})
        else:
            content.append({"type": "input_text", "text": str(item)})
    # JSON 모드는 입력 메시지에 "json"이 없으면 API가 400을 낸다. 지시문(instructions)은 쳐주지 않는다.
    if not any("json" in part.get("text", "").lower() for part in content):
        content.insert(0, {"type": "input_text", "text": _JSON_REMINDER})
    return content


def _finish_reason(raw):
    if raw.status == "completed":
        return "STOP"
    reason = getattr(raw.incomplete_details, "reason", None)
    return INCOMPLETE_REASONS.get(reason) or str(reason or raw.status or "").upper()


def _parse(raw):
    usage = raw.usage
    reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    output = getattr(usage, "output_tokens", 0) or 0
    return AiResponse(
        text=raw.output_text or "", finish_reason=_finish_reason(raw),
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=max(0, output - reasoning), reasoning_tokens=reasoning,
        cached_tokens=cached, total_tokens=getattr(usage, "total_tokens", 0) or 0,
    )


class QuantityAiClient:
    def __init__(self, sdk):
        self._sdk = sdk

    def generate(self, *, contents, system, max_output_tokens, effort="medium", timeout=None):
        """contents(글과 image_part를 순서대로)를 보내 AiResponse를 돌려준다.

        temperature는 보내지 않는다. 추론 모델은 받지 않는다.
        시간 초과는 내장 TimeoutError로 올린다. 호출부가 그것으로 시간 초과를 가려낸다.
        """
        started = time.perf_counter()
        context = gemini_usage._infer_call_context(skip_modules=(__name__,))
        common = dict(
            feature=context["feature"], model=QUANTITY_AI_MODEL[:160], tool_tokens=0,
            input_characters=sum(len(item) for item in contents if isinstance(item, str)),
            image_inputs=sum(1 for item in contents if isinstance(item, ImagePart)),
            callsite=context["callsite"], batch_index=context["batch_index"], batch_total=context["batch_total"],
            metadata={"drawing_type": context["drawing_type"]},
        )

        def elapsed_ms():
            return max(0, int((time.perf_counter() - started) * 1000))

        sdk = self._sdk.with_options(timeout=timeout) if timeout else self._sdk
        try:
            raw = sdk.responses.create(
                model=QUANTITY_AI_MODEL,
                instructions=system,
                input=[{"role": "user", "content": _user_content(contents)}],
                text={"format": {"type": "json_object"}},
                reasoning={"effort": effort},
                max_output_tokens=min(max_output_tokens + REASONING_HEADROOM.get(effort, REASONING_HEADROOM["medium"]),
                                      MODEL_MAX_OUTPUT_TOKENS),
                store=False,
            )
        except Exception as error:
            gemini_usage._save_usage_log(
                **common, prompt_tokens=0, output_tokens=0, total_tokens=0, cached_tokens=0, thoughts_tokens=0,
                duration_ms=elapsed_ms(), is_success=False,
                error_type=type(error).__name__[:120], error_message=str(error)[:1000],
            )
            if isinstance(error, openai.APITimeoutError):
                raise TimeoutError(f"AI 응답 대기 시간을 초과했습니다: {error}") from error
            raise

        response = _parse(raw)
        gemini_usage._save_usage_log(
            **common, prompt_tokens=response.input_tokens, output_tokens=response.output_tokens,
            total_tokens=response.total_tokens, cached_tokens=response.cached_tokens,
            thoughts_tokens=response.reasoning_tokens, duration_ms=elapsed_ms(), is_success=True,
            error_type="", error_message="",
        )
        return response


def get_ai_client():
    """OPENAI_QUANTITY_API_KEY가 없으면 None을 반환합니다 (import 시점에 죽지 않도록 지연 초기화).

    OPENAI_API_KEY와 일부러 나눠 둔다. 그 이름은 다른 기능도 읽으므로, 수량산출 키를 넣는 것만으로
    다른 기능이 켜지거나 그쪽 비용이 이 키로 나가지 않게 한다.
    """
    api_key = str(getattr(settings, "OPENAI_QUANTITY_API_KEY", "")
                  or os.environ.get("OPENAI_QUANTITY_API_KEY", "")).strip()
    if not api_key:
        return None
    return QuantityAiClient(openai.OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SEC, max_retries=MAX_RETRIES))
