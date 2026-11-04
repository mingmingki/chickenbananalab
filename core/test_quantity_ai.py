"""수량산출 AI 호출 계층 테스트: 실제 OpenAI SDK를 가짜 전송 계층에 연결해 요청 본문과 응답 해석을 본다."""
import base64
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import openai
from django.test import SimpleTestCase, override_settings
from PIL import Image

from core import quantity_ai, quantity_views as views


def _api_response(text='{"items": []}', status="completed", reason=None, reasoning=7, cached=3):
    return {
        "id": "resp_1", "object": "response", "created_at": 1, "model": "gpt-test",
        "status": status,
        "incomplete_details": {"reason": reason} if reason else None,
        "error": None, "instructions": None, "metadata": {}, "parallel_tool_calls": True,
        "temperature": None, "tool_choice": "auto", "tools": [], "top_p": None,
        "output": [{
            "id": "msg_1", "type": "message", "role": "assistant", "status": status,
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
        "usage": {
            "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
            "input_tokens_details": {"cached_tokens": cached},
            "output_tokens_details": {"reasoning_tokens": reasoning},
        },
    }


class _Transport:
    """보낸 요청을 기억하고 정해진 응답을 돌려주는 가짜 전송 계층."""

    def __init__(self, payload=None, status_code=200):
        self.requests = []
        self.payload = payload or _api_response()
        self.status_code = status_code

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(self.status_code, json=self.payload)

    @property
    def body(self):
        return json.loads(self.requests[-1].content)


def _client(transport):
    sdk = openai.OpenAI(api_key="test-key", max_retries=0,
                        http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    return quantity_ai.QuantityAiClient(sdk)


class QuantityAiRequestTests(SimpleTestCase):
    def setUp(self):
        patcher = patch("core.gemini_usage._save_usage_log")
        self.save_log = patcher.start()
        self.addCleanup(patcher.stop)

    def test_text_and_images_go_in_order_with_json_mode_and_no_retention(self):
        transport = _Transport()
        _client(transport).generate(
            contents=["[도면 1쪽] JSON", quantity_ai.image_part(data=b"jpeg-bytes", mime_type="image/jpeg"), "끝"],
            system="JSON 객체만 반환하세요.", max_output_tokens=1000,
        )
        self.assertTrue(str(transport.requests[-1].url).endswith("/responses"))
        body = transport.body
        self.assertEqual(body["model"], quantity_ai.QUANTITY_AI_MODEL)
        self.assertEqual(body["instructions"], "JSON 객체만 반환하세요.")
        self.assertEqual(body["text"], {"format": {"type": "json_object"}})
        self.assertIs(body["store"], False)
        content = body["input"][0]["content"]
        self.assertEqual(body["input"][0]["role"], "user")
        self.assertEqual([item["type"] for item in content], ["input_text", "input_image", "input_text"])
        self.assertEqual(content[0]["text"], "[도면 1쪽] JSON")
        expected = "data:image/jpeg;base64," + base64.b64encode(b"jpeg-bytes").decode()
        self.assertEqual(content[1]["image_url"], expected)
        self.assertEqual(content[1]["detail"], "auto")

    def test_temperature_is_never_sent_because_reasoning_models_reject_it(self):
        transport = _Transport()
        _client(transport).generate(contents=["x"], system="JSON", max_output_tokens=10)
        self.assertNotIn("temperature", transport.body)
        self.assertNotIn("top_p", transport.body)

    def test_effort_is_sent_and_room_is_added_for_reasoning_tokens(self):
        transport = _Transport()
        client = _client(transport)
        client.generate(contents=["x"], system="JSON", max_output_tokens=4096, effort="low")
        self.assertEqual(transport.body["reasoning"], {"effort": "low"})
        self.assertEqual(transport.body["max_output_tokens"], 4096 + 8192)
        client.generate(contents=["x"], system="JSON", max_output_tokens=32768)
        self.assertEqual(transport.body["reasoning"], {"effort": "medium"})
        self.assertEqual(transport.body["max_output_tokens"], 32768 + 16384)

    def test_output_limit_stays_within_the_model_ceiling(self):
        transport = _Transport()
        _client(transport).generate(contents=["x"], system="JSON", max_output_tokens=127000)
        self.assertEqual(transport.body["max_output_tokens"], 128000)

    def test_json_word_goes_in_the_input_because_instructions_do_not_count(self):
        # 실제 호출에서 확인: 지시문에만 있으면 400 "input messages must contain the word 'json'".
        transport = _Transport()
        client = _client(transport)
        client.generate(contents=["표를 읽으세요.", quantity_ai.image_part(data=b"i")],
                        system="JSON 객체만 반환하세요.", max_output_tokens=10)
        self.assertEqual(transport.body["instructions"], "JSON 객체만 반환하세요.")
        content = transport.body["input"][0]["content"]
        self.assertEqual([item["type"] for item in content], ["input_text", "input_text", "input_image"])
        self.assertIn("JSON", content[0]["text"])
        self.assertEqual(content[1]["text"], "표를 읽으세요.")

    def test_no_json_line_is_added_when_the_input_already_has_the_word(self):
        transport = _Transport()
        _client(transport).generate(contents=["json으로 답하세요."], system="표를 읽으세요.", max_output_tokens=10)
        content = transport.body["input"][0]["content"]
        self.assertEqual([item["text"] for item in content], ["json으로 답하세요."])

    def test_timeout_is_passed_for_one_request(self):
        transport = _Transport()
        _client(transport).generate(contents=["x"], system="JSON", max_output_tokens=10, timeout=42)
        self.assertEqual(transport.requests[-1].extensions["timeout"]["read"], 42)


class QuantityAiResponseTests(SimpleTestCase):
    def setUp(self):
        patcher = patch("core.gemini_usage._save_usage_log")
        self.save_log = patcher.start()
        self.addCleanup(patcher.stop)

    def _generate(self, payload):
        return _client(_Transport(payload)).generate(contents=["x"], system="JSON", max_output_tokens=10)

    def test_completed_response_has_text_stop_and_token_counts(self):
        response = self._generate(_api_response('{"a": 1}'))
        self.assertEqual(views._ai_response_text(response), '{"a": 1}')
        diagnostics = views._ai_response_diagnostics(response)
        self.assertEqual(diagnostics["candidate_count"], 1)
        self.assertEqual(diagnostics["candidates"][0]["finish_reason"], "STOP")
        self.assertEqual(diagnostics["candidates"][0]["text_length"], 8)
        self.assertEqual(diagnostics["usage"], {
            "prompt_token_count": 100, "candidates_token_count": 13,
            "thoughts_token_count": 7, "total_token_count": 120,
        })

    def test_cut_off_response_keeps_partial_text_and_reports_max_tokens(self):
        # 부분 복구(_try_repair_truncated_json)와 잘린 쪽 재시도가 이 종료 사유와 잘린 글을 쓴다.
        response = self._generate(_api_response('{"items": [{"a"', status="incomplete", reason="max_output_tokens"))
        self.assertEqual(views._ai_response_text(response), '{"items": [{"a"')
        self.assertEqual(views._ai_response_diagnostics(response)["candidates"][0]["finish_reason"], "MAX_TOKENS")

    def test_filtered_response_reports_safety(self):
        response = self._generate(_api_response("", status="incomplete", reason="content_filter"))
        self.assertEqual(response.finish_reason, "SAFETY")
        self.assertEqual(views._ai_response_text(response), "")

    def test_diagnostics_accept_objects_without_ai_fields(self):
        self.assertEqual(views._ai_response_diagnostics(object())["candidates"][0]["finish_reason"], None)
        self.assertEqual(views._ai_response_text(object()), "")


def analyze_with_ai(client):
    """사용량 기록이 호출 스택의 함수 이름으로 기능을 맞추는지 보려는 흉내."""
    batch_idx, total_batches = 2, 9  # noqa: F841  (호출 스택에서 읽힌다)
    return client.generate(contents=["글", quantity_ai.image_part(data=b"i")], system="JSON", max_output_tokens=10)


class QuantityAiUsageLogTests(SimpleTestCase):
    def test_success_is_logged_with_feature_and_tokens_but_no_content(self):
        with patch("core.gemini_usage._save_usage_log") as save:
            analyze_with_ai(_client(_Transport()))
        values = save.call_args.kwargs
        self.assertEqual(values["feature"], "quantity_architectural")
        self.assertEqual(values["model"], quantity_ai.QUANTITY_AI_MODEL)
        self.assertEqual(values["prompt_tokens"], 100)
        self.assertEqual(values["output_tokens"], 13)
        self.assertEqual(values["thoughts_tokens"], 7)
        self.assertEqual(values["cached_tokens"], 3)
        self.assertEqual(values["total_tokens"], 120)
        self.assertEqual(values["input_characters"], 1)
        self.assertEqual(values["image_inputs"], 1)
        self.assertEqual(values["batch_index"], 2)
        self.assertEqual(values["batch_total"], 9)
        self.assertIs(values["is_success"], True)
        self.assertIn("analyze_with_ai", values["callsite"])
        self.assertNotIn("글", json.dumps(values, default=str, ensure_ascii=False))

    def test_failure_is_logged_and_raised(self):
        transport = _Transport({"error": {"message": "bad", "type": "invalid_request_error"}}, status_code=400)
        with patch("core.gemini_usage._save_usage_log") as save, self.assertRaises(openai.BadRequestError):
            analyze_with_ai(_client(transport))
        self.assertIs(save.call_args.kwargs["is_success"], False)
        self.assertEqual(save.call_args.kwargs["error_type"], "BadRequestError")


class QuantityAiTimeoutTests(SimpleTestCase):
    def test_timeout_becomes_builtin_timeout_error_and_is_logged(self):
        # SDK의 메시지는 "Request timed out."이라 호출부의 "timeout" 글자 검사에 걸리지 않는다.
        def transport(request):
            raise httpx.ReadTimeout("slow", request=request)

        with patch("core.gemini_usage._save_usage_log") as save, self.assertRaises(TimeoutError):
            _client(transport).generate(contents=["x"], system="JSON", max_output_tokens=10)
        self.assertIs(save.call_args.kwargs["is_success"], False)
        self.assertEqual(save.call_args.kwargs["error_type"], "APITimeoutError")


class QuantityAiClientSetupTests(SimpleTestCase):
    @override_settings(OPENAI_QUANTITY_API_KEY="")
    def test_no_key_gives_no_client(self):
        with patch.dict("os.environ", {"OPENAI_QUANTITY_API_KEY": "  "}):
            self.assertIsNone(quantity_ai.get_ai_client())

    @override_settings(OPENAI_QUANTITY_API_KEY="")
    def test_key_from_environment_gives_a_client(self):
        with patch.dict("os.environ", {"OPENAI_QUANTITY_API_KEY": "from-env"}):
            self.assertIsInstance(quantity_ai.get_ai_client(), quantity_ai.QuantityAiClient)

    @override_settings(OPENAI_QUANTITY_API_KEY="")
    def test_the_shared_key_name_is_not_used(self):
        # OPENAI_API_KEY를 읽는 다른 기능과 비용·켜짐을 나눈다.
        with patch.dict("os.environ", {"OPENAI_API_KEY": "shared", "OPENAI_QUANTITY_API_KEY": ""}):
            self.assertIsNone(quantity_ai.get_ai_client())

    def test_a_quantity_key_does_not_switch_on_features_that_read_the_shared_name(self):
        from core.job_views import get_openai_client
        with patch.dict("os.environ", {"OPENAI_QUANTITY_API_KEY": "from-env", "OPENAI_API_KEY": ""}), \
                patch("core.job_views.load_dotenv", None):
            self.assertIsNone(get_openai_client())

    def test_sdk_is_set_up_with_long_timeout_and_retries(self):
        with patch.dict("os.environ", {"OPENAI_QUANTITY_API_KEY": "from-env"}):
            client = quantity_ai.get_ai_client()
        self.assertEqual(client._sdk.max_retries, quantity_ai.MAX_RETRIES)
        self.assertEqual(client._sdk.timeout, quantity_ai.REQUEST_TIMEOUT_SEC)


class QuantityViewsUseOnlyTheNewLayerTests(SimpleTestCase):
    def test_no_gemini_left_in_the_quantity_code_or_its_screens(self):
        core = Path(views.__file__).parent
        for path in (core / "quantity_views.py", core / "quantity_calc.py",
                     core / "templates/core/quantity_embed.html", core / "templates/core/quantity_main.html"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?i)gemini|genai", path.name)

    def test_home_screen_quantity_messages_do_not_name_gemini(self):
        home = (Path(views.__file__).parent / "templates/core/home.html").read_text(encoding="utf-8")
        self.assertNotIn("도면 판독(Gemini)", home)

    def test_every_prompt_returns_a_json_object_the_parser_can_read(self):
        # JSON 모드는 최상위 배열을 못 돌려준다. 키를 지정하지 않으면 모델이 지은 키로 싸여
        # parsed.get("pages")가 조용히 비어 버린다.
        self.assertIn('{"pages":', views.GENERAL_NOTES_LOCATOR_PROMPT)
        for name in ("GENERAL_NOTES_LOCATOR_PROMPT", "OVERVIEW_PAGE_CLASSIFIER_PROMPT", "OVERVIEW_SPEC_SYSTEM_PROMPT",
                     "MEMBER_EXTRACTION_SYSTEM_PROMPT", "BASEMENT_PLAN_CHECK_SYSTEM_PROMPT",
                     "ELEVATION_SECTION_SYSTEM_PROMPT", "ARCHITECTURAL_SYSTEM_PROMPT",
                     "GENERAL_NOTES_OVERVIEW_TRANSCRIPTION_PROMPT_V5", "DRAWING_COORDINATION_LOCATOR_PROMPT",
                     "DRAWING_COORDINATION_DETAIL_PROMPT"):
            self.assertIn("JSON", getattr(views, name), name)

    def test_job_step_key_follows_the_model(self):
        self.assertEqual(views.QUANTITY_AI_MODEL, quantity_ai.QUANTITY_AI_MODEL)


class QuantityViewsThroughTheSdkTests(SimpleTestCase):
    """뷰 함수가 실제 SDK 응답을 받아 JSON 해석·잘림 복구까지 지나가는지 본다."""

    def setUp(self):
        patcher = patch("core.gemini_usage._save_usage_log")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_member_extraction_sends_the_member_prompt_and_reads_the_members(self):
        payload = {"columns": [{"mark": "C1"}], "notes": []}
        transport = _Transport(_api_response(json.dumps(payload)))
        result = views._extract_structural_members_one_batch(_client(transport), {"a.dxf": {"texts": ["C1"]}}, [], 1, 1)
        self.assertEqual(result["columns"], [{"mark": "C1"}])
        self.assertEqual(transport.body["instructions"], views.MEMBER_EXTRACTION_SYSTEM_PROMPT)
        self.assertEqual(transport.body["reasoning"], {"effort": "medium"})

    def test_cut_off_member_extraction_keeps_what_was_read_and_says_so(self):
        cut = '{"columns": [{"mark": "C1"}, {"mark": "C2"}, {"mark'
        transport = _Transport(_api_response(cut, status="incomplete", reason="max_output_tokens"))
        result = views._extract_structural_members_one_batch(_client(transport), {"a.dxf": {"texts": []}}, [], 1, 1)
        self.assertEqual([row["mark"] for row in result["columns"]], ["C1", "C2"])
        self.assertTrue(any("잘려" in note for note in result["notes"]), result["notes"])

    def test_failed_request_becomes_a_note_the_job_treats_as_incomplete(self):
        transport = _Transport({"error": {"message": "bad", "type": "invalid_request_error"}}, status_code=400)
        result = views._extract_structural_members_one_batch(_client(transport), {"a.dxf": {"texts": []}}, [], 1, 1)
        self.assertTrue(any("요청 중 오류" in note for note in result["notes"]), result["notes"])
        self.assertTrue(views._extraction_incomplete_reasons(result))

    def test_overview_classifier_retries_on_a_cut_off_batch(self):
        cut = '{"pages":[{"page_number":1,"page_type":"overview","confidence":0.9,"title_text":"t","evidence_terms":[]},'
        transport = _Transport(_api_response(cut, status="incomplete", reason="max_output_tokens"))
        with patch.object(views, "get_ai_client", return_value=_client(transport)), \
                patch.object(views, "_render_pdf_page_range",
                             side_effect=lambda *a, **k: [Image.new("RGB", (100, 50), "white")]):
            result = views._classify_overview_page_batch(b"pdf-cut-off", [1, 2])
        self.assertEqual(result.finish_reason, "MAX_TOKENS")
        self.assertEqual(result.missing_page_numbers, [2])
        content = transport.body["input"][0]["content"]
        self.assertEqual(sum(item["type"] == "input_image" for item in content), 2)
        self.assertEqual(transport.requests[-1].extensions["timeout"]["read"], views.OVERVIEW_VISION_TIMEOUT_SEC)
