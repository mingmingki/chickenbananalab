"""
AI건설 구인구직 — job_views.py
카카오톡 스타일 채팅 기반 구직자 면접 + OpenAI 분석 보고서

필요 패키지:
    pip install openai reportlab
"""

import io
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

# CBL_AI_JOB_ENV_AUTO_LOAD_V1_1

logger = logging.getLogger(__name__)


def get_openai_client():
    """프로젝트 .env, Django 설정 또는 서버 환경변수에서 API 키를 읽습니다."""
    if load_dotenv:
        load_dotenv(Path(settings.BASE_DIR) / ".env", override=False)
    api_key = (
        getattr(settings, "OPENAI_API_KEY", "")
        or getattr(settings, "OPENAI_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not OpenAI or not api_key:
        return None
    return OpenAI(api_key=api_key)


# ─────────────────────────────────────────────
#  경력 / 직무 / 실무 질문 데이터
# ─────────────────────────────────────────────
CAREER_LEVELS = {
    "신입": {"label": "신입 (0~2년)", "years": "0~2년"},
    "중견": {"label": "중견 (3~7년)", "years": "3~7년"},
    "시니어": {"label": "시니어 (8년+)", "years": "8년 이상"},
}

JOB_FIELDS = {
    "공사": "공사",
    "공무": "공무",
    "견적/예산": "견적/예산",
    "BIM": "설계·BIM",
}

# 직무 × 경력별 면접 질문
INTERVIEW_QUESTIONS = {
    "공사": {
        "신입": [
            "현장에 처음 배치되었을 때 도면, 시방서, 공정표 중 무엇부터 확인하고 이유는 무엇입니까?",
            "철근콘크리트 공사에서 거푸집, 철근, 타설 전 검측 순서를 설명해 주세요.",
            "도면과 현장 치수가 다를 때 임의로 시공하지 않고 어떤 순서로 확인·보고하겠습니까?",
            "협력업체 작업 전 안전·품질·공정 측면에서 확인해야 할 사항을 설명해 주세요.",
            "작업일보와 현장사진을 남겨야 하는 이유와 기본 작성 기준을 설명해 주세요.",
        ],
        "중견": [
            "골조공사에서 공정 지연이 발생했을 때 원인을 어떻게 분석하고 만회계획을 세우겠습니까?",
            "콘크리트 타설 중 공급이 90분간 중단됐습니다. 품질·공정 측면에서 어떻게 조치하겠습니까?",
            "건축도면과 구조도면의 개구부 위치가 다를 때 어떤 자료를 확인하고 협의하겠습니까?",
            "협력업체 시공 품질이 반복적으로 기준에 미달할 때 어떤 절차로 개선하겠습니까?",
            "설계변경이 공정과 물량에 미치는 영향을 현장에서 어떻게 확인하고 기록하겠습니까?",
        ],
        "시니어": [
            "현장 전체 공정이 지연될 위험이 있을 때 만회공정표를 어떤 기준으로 만들고 어떤 리스크를 먼저 통제하겠습니까?",
            "골조 사이클을 단축하기 위해 형틀, 철근, 설비매립, 타설 순서를 어떻게 조정하겠습니까?",
            "발주처, 감리, 협력업체 의견이 충돌할 때 현장 책임자의 의사결정 기준을 설명해 주세요.",
            "중대 품질·안전 이슈가 발생했을 때 현장 조치, 본사 보고, 대외 대응 순서를 설명해 주세요.",
            "후배 현장기사에게 공사관리 실무를 교육한다면 가장 먼저 가르칠 기준과 습관은 무엇입니까?",
        ],
    },
    "공무": {
        "신입": [
            "공무 업무가 무엇인지 알고 있는 내용을 설명해 주세요.",
            "건설 현장에서 공정표가 왜 중요하다고 생각하시나요?",
            "협력업체와 소통할 때 가장 중요하게 생각하는 것은 무엇인가요?",
            "기성청구란 무엇인지 알고 있는 만큼 설명해 주세요.",
            "건설 공무 업무를 선택한 이유와 앞으로의 목표를 말씀해 주세요.",
        ],
        "중견": [
            "설계변경이 발생했을 때 공무담당자로서 어떻게 대응하셨나요? 실제 경험을 말씀해 주세요.",
            "공정 지연이 발생했을 때 협력업체와 어떻게 조율하셨나요?",
            "기성청구 프로세스와 실무에서 주의할 점을 설명해 주세요.",
            "현장 안전·품질과 공정 사이에서 균형을 어떻게 맞추셨나요?",
            "공사비 정산 시 가장 어려웠던 점과 해결 방법을 말씀해 주세요.",
        ],
        "시니어": [
            "대형 프로젝트 공무팀 총괄 경험이 있으시면 말씀해 주세요. 팀 규모와 역할을 포함해서요.",
            "발주처와 시공사 간 클레임이 발생했을 때 어떻게 처리하셨나요?",
            "공정 관리 시스템(Primavera, MS Project 등) 활용 경험과 한계를 말씀해 주세요.",
            "후배 공무 담당자를 교육할 때 가장 강조하는 부분은 무엇인가요?",
            "공사비 절감을 위해 시도한 방법 중 가장 효과적이었던 사례를 말씀해 주세요.",
        ],
    },
    "견적/예산": {
        "신입": [
            "내역서가 무엇인지, 어떤 항목으로 구성되는지 알고 있는 만큼 설명해 주세요.",
            "견적과 예산의 차이를 어떻게 이해하고 있나요?",
            "건설 공사에서 물량 산출이 중요한 이유는 무엇이라고 생각하시나요?",
            "엑셀이나 관련 프로그램 활용 능력을 말씀해 주세요.",
            "견적 업무를 선택한 이유와 앞으로 어떤 견적 전문가가 되고 싶은지 말씀해 주세요.",
            "실행예산과 도급내역이 어떻게 연결되는지 알고 있는 만큼 설명해 주세요.",
            "직접공사비, 간접비, 일반관리비의 차이를 설명해 주세요.",
            "월별 원가보고에서 예산, 실적, 잔여금액을 왜 함께 확인해야 합니까?",
            "협력업체 계약금액이 실행예산을 초과하면 어떤 자료부터 확인하겠습니까?",
            "견적·예산 업무에서 숫자 오류를 줄이기 위한 검토 방법을 설명해 주세요.",
        ],
        "중견": [
            "내역서 작성 시 누락 항목을 어떻게 검토하시나요? 구체적인 체크리스트가 있으신가요?",
            "실행예산과 도급예산의 차이를 실무에서 어떻게 관리하셨나요?",
            "원가 절감 사례가 있으면 구체적인 금액과 방법을 말씀해 주세요.",
            "외주 견적 검토 시 중점적으로 보는 항목은 무엇인가요?",
            "견적 오류가 발생했을 때 어떻게 대처하셨나요? 실제 경험이 있으면 말씀해 주세요.",
            "실행예산 편성 시 견적자료, 협력업체 계약, 현장조건을 어떤 순서로 반영하겠습니까?",
            "원가 초과가 예상될 때 물량, 단가, 공법, 공기 중 어떤 기준으로 원인을 분석하겠습니까?",
            "잠정실행과 본실행의 차이를 어떻게 관리하고 보고하겠습니까?",
            "월별 원가회의에서 핵심적으로 보고해야 할 지표와 리스크는 무엇입니까?",
            "VE 또는 설계변경이 예산에 미치는 영향을 어떻게 검토하고 기록하겠습니까?",
        ],
        "시니어": [
            "수백억 규모 프로젝트 견적 총괄 경험이 있으시면 말씀해 주세요.",
            "발주처 VE(Value Engineering) 요청 시 어떻게 대응하셨나요?",
            "견적 자동화 툴이나 시스템을 직접 구축하거나 도입한 경험이 있으신가요?",
            "후발 경쟁사 대비 경쟁력 있는 견적을 내기 위한 전략을 말씀해 주세요.",
            "견적·예산 조직 운영 경험이 있으시면 팀 구성과 역할 분담을 설명해 주세요.",
            "프로젝트 전체 실행예산을 총괄할 때 초기 편성 단계에서 가장 중요하게 보는 리스크는 무엇입니까?",
            "현장 원가율이 악화될 때 본사와 현장이 함께 판단해야 할 원인과 조치안을 설명해 주세요.",
            "잠정실행, 본실행, 준공정산까지 이어지는 예산관리 체계를 어떻게 표준화하겠습니까?",
            "대규모 증액·감액 이슈가 손익에 미치는 영향을 경영진에게 어떻게 보고하겠습니까?",
            "BIM 기반 수량산출을 견적과 실행예산에 연계할 때 필요한 검증 절차를 설명해 주세요.",
        ],
    },
    "BIM": {
        "신입": [
            "BIM이 무엇인지, 건설 현장에서 어떻게 활용되는지 알고 있는 만큼 설명해 주세요.",
            "Revit 사용 경험이 있으시면 어느 수준인지 말씀해 주세요.",
            "3D 모델링과 2D 도면의 차이와 BIM의 장점은 무엇이라고 생각하시나요?",
            "BIM 관련 자격증이나 교육 이수 내용이 있으면 말씀해 주세요.",
            "BIM 분야를 선택한 이유와 앞으로 어떤 BIM 전문가가 되고 싶은지 말씀해 주세요.",
        ],
        "중견": [
            "BIM 모델 납품 기준(LOD)을 어떻게 설정하고 관리하셨나요?",
            "설계 변경 시 BIM 모델 업데이트 프로세스를 설명해 주세요.",
            "Navisworks 등을 활용한 간섭 체크 경험을 말씀해 주세요.",
            "BIM을 통해 실제 현장 문제를 해결한 경험이 있으면 말씀해 주세요.",
            "4D/5D BIM 활용 경험이 있으시면 구체적으로 말씀해 주세요.",
        ],
        "시니어": [
            "BIM 발주 기준 수립이나 BIM 실행계획서(BEP) 작성 경험을 말씀해 주세요.",
            "Dynamo, Python 등 BIM 자동화 경험이 있으시면 말씀해 주세요.",
            "BIM 기반 물량 산출(5D)의 실무 적용 경험과 한계를 말씀해 주세요.",
            "BIM 팀 구성 및 운영 경험, 후배 교육 방법을 말씀해 주세요.",
            "스마트 건설 기술(디지털 트윈, AI 연계 등) 관련 경험이나 비전을 말씀해 주세요.",
        ],
    },
}


def normalize_job_field(field):
    """현재 직무 4종과 이전 견적/예산 값을 같은 내부 키로 정규화합니다."""
    value = str(field or "").strip()
    if "공무" in value or "계약" in value:
        return "공무"
    if "공사" in value or "현장" in value:
        return "공사"
    if any(keyword in value for keyword in ("견적", "예산", "수량", "원가")):
        return "견적/예산"
    if "BIM" in value.upper() or "설계" in value or "자동화" in value:
        return "BIM"
    return "공사"


# OpenAI 분석 시스템 프롬프트
# CBL_AI_JOB_AI_PDF_REPORT_V1
ANALYSIS_SYSTEM_PROMPT = """당신은 건축·건설 시공, 공무, 견적·예산, 설계·BIM 채용을 평가하는 전문 면접관입니다.
지원자의 경력단계와 지원 직무를 기준으로 오직 제공된 전사 답변의 내용만 평가하세요.

평가기준:
- 전문지식: 공법, 도면, 계약, 원가, BIM 등 직무 지식의 정확성
- 실무대응력: 현장 절차, 보고, 협의, 기록, 후속조치의 구체성
- 문제해결력: 원인분석, 대안비교, 리스크 통제의 논리성
- 의사소통: 답변의 구조, 명료성, 근거 제시 수준
- 성장가능성: 경력단계 대비 학습 태도와 역할 확장 가능성

각 점수는 1~5점입니다. 답변에 근거가 없으면 추측하지 말고 낮게 평가하며, 외모·목소리·성별·나이·억양 등 직무와 무관한 특성은 평가하지 마세요.
강점과 개선사항은 답변에서 확인되는 구체적인 근거를 포함하세요."""

INTERVIEW_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": {
                "expertise": {"type": "integer", "minimum": 1, "maximum": 5},
                "field_response": {"type": "integer", "minimum": 1, "maximum": 5},
                "problem_solving": {"type": "integer", "minimum": 1, "maximum": 5},
                "communication": {"type": "integer", "minimum": 1, "maximum": 5},
                "growth_potential": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["expertise", "field_response", "problem_solving", "communication", "growth_potential"],
            "additionalProperties": False,
        },
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "improvements": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string"},
        "fit_jobs": {"type": "array", "items": {"type": "string"}},
        "question_feedback": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "q_index": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 1, "maximum": 5},
                    "feedback": {"type": "string"},
                },
                "required": ["q_index", "score", "feedback"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scores", "summary", "strengths", "improvements", "recommendation", "fit_jobs", "question_feedback"],
    "additionalProperties": False,
}


def _normalize_answer_record(record, fallback_index=0):
    try:
        q_index = int(record.get("q_index", fallback_index))
    except (TypeError, ValueError):
        q_index = fallback_index
    try:
        duration = max(0, int(float(record.get("duration", 0))))
    except (TypeError, ValueError):
        duration = 0
    return {
        "q_index": q_index,
        "question": str(record.get("question", ""))[:2000],
        "answer": str(record.get("answer", ""))[:12000],
        "duration": duration,
    }


def _score_grade(scores):
    keys = ("expertise", "field_response", "problem_solving", "communication", "growth_potential")
    cleaned = {}
    for key in keys:
        try:
            cleaned[key] = min(5, max(1, int(scores.get(key, 1))))
        except (TypeError, ValueError):
            cleaned[key] = 1
    overall = round(sum(cleaned.values()) / 25 * 100)
    grade = "S" if overall >= 90 else "A" if overall >= 80 else "B" if overall >= 70 else "C" if overall >= 60 else "D"
    return cleaned, overall, grade


# ─────────────────────────────────────────────
#  뷰: 메인 페이지
# ─────────────────────────────────────────────
@xframe_options_sameorigin
def job_main(request):
    """AI건설 구인구직 메인 페이지"""
    user_display = request.user.username if request.user.is_authenticated else "게스트"
    # 세션 기반 면접 ID 초기화
    if "job_session_id" not in request.session:
        request.session["job_session_id"] = str(uuid.uuid4())

    return render(request, "core/job_main.html", {
        "user_display": user_display,
        "career_levels": CAREER_LEVELS,
        "job_fields": JOB_FIELDS,
    })


# ─────────────────────────────────────────────
#  API: 면접 질문 가져오기
# ─────────────────────────────────────────────
@require_POST
def api_get_questions(request):
    """
    경력 + 직무를 받아 해당 직무의 면접 질문을 반환
    Body: { "career": "중견", "field": "공무" }
    """
    try:
        body = json.loads(request.body)
        career = body.get("career", "중견")
        field = body.get("field", "공무")
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "잘못된 요청"}, status=400)

    career_key = next((k for k in CAREER_LEVELS if k in career), "중견")
    field_key = normalize_job_field(field)

    questions = INTERVIEW_QUESTIONS.get(field_key, {}).get(career_key, [])

    # 세션에 면접 정보 저장
    request.session["job_career"] = career_key
    request.session["job_field"] = field_key
    request.session["job_answers"] = []
    request.session.modified = True

    return JsonResponse({
        "questions": questions,
        "career": CAREER_LEVELS[career_key]["label"],
        "field": JOB_FIELDS[field_key],
        "total": len(questions),
    })


# ─────────────────────────────────────────────
#  API: 답변 저장 (실시간)
# ─────────────────────────────────────────────
@require_POST
def api_save_answer(request):
    """
    질문 인덱스 + 답변 저장
    Body: { "q_index": 0, "question": "질문", "answer": "답변", "duration": 45 }
    """
    try:
        body = json.loads(request.body)
        q_index = body.get("q_index", 0)
        question = body.get("question", "")
        answer = body.get("answer", "")
        duration = body.get("duration", 0)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "잘못된 요청"}, status=400)

    answers = request.session.get("job_answers", [])

    # 중복 저장 방지
    existing = next((a for a in answers if a["q_index"] == q_index), None)
    if existing:
        existing.update({"answer": answer, "duration": duration})
    else:
        answers.append({
            "q_index": q_index,
            "question": question,
            "answer": answer,
            "duration": duration,
        })

    request.session["job_answers"] = answers
    request.session.modified = True

    return JsonResponse({"ok": True, "saved": len(answers)})


# ─────────────────────────────────────────────
#  API: 질문별 영상/음성 답변 전사
# ─────────────────────────────────────────────
@require_POST
def api_transcribe_answer(request):
    media = request.FILES.get("media")
    if media is None:
        return JsonResponse({"ok": False, "error": "전사할 음성 파일이 없습니다."}, status=400)

    if media.size <= 0:
        return JsonResponse({"ok": False, "error": "빈 음성 파일입니다."}, status=400)
    if media.size > 25 * 1024 * 1024:
        return JsonResponse({"ok": False, "error": "질문별 음성 파일은 25MB 이하여야 합니다."}, status=413)

    allowed_ext = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}
    original_name = Path(media.name or "answer.webm").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in allowed_ext:
        content_type = (media.content_type or "").lower()
        suffix = ".mp4" if "mp4" in content_type else ".webm"
        original_name = f"answer{suffix}"

    try:
        q_index = int(request.POST.get("q_index", 0))
    except (TypeError, ValueError):
        q_index = 0
    try:
        duration = max(0, int(float(request.POST.get("duration", 0))))
    except (TypeError, ValueError):
        duration = 0
    question = str(request.POST.get("question", ""))[:2000]

    client = get_openai_client()
    if client is None:
        return JsonResponse({"ok": False, "error": "OPENAI_API_KEY 또는 openai 패키지 설정이 필요합니다."}, status=503)

    try:
        transcription = client.audio.transcriptions.create(
            model=getattr(settings, "OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
            file=(original_name, media.read(), media.content_type or "application/octet-stream"),
            language="ko",
            response_format="text",
            prompt="건축·건설 면접입니다. 철근콘크리트, 거푸집, 기성, 설계변경, 실행예산, 수량산출, Revit, Navisworks, Dynamo, BIM 용어를 정확히 전사하세요.",
        )
        transcript = getattr(transcription, "text", None)
        if transcript is None:
            transcript = str(transcription)
        transcript = transcript.strip()
        if not transcript:
            transcript = "(음성 답변을 인식하지 못했습니다.)"
    except Exception:
        logger.exception("AI 면접 음성 전사 실패")
        return JsonResponse({"ok": False, "error": "음성 전사에 실패했습니다. OpenAI 키·모델·파일 형식을 확인해 주세요."}, status=502)

    answer = _normalize_answer_record({
        "q_index": q_index,
        "question": question,
        "answer": transcript,
        "duration": duration,
    }, q_index)

    answers = request.session.get("job_answers", [])
    answers = [a for a in answers if int(a.get("q_index", -1)) != q_index]
    answers.append(answer)
    answers.sort(key=lambda item: item.get("q_index", 0))
    request.session["job_answers"] = answers
    request.session.modified = True

    return JsonResponse({"ok": True, "answer": answer})


# ─────────────────────────────────────────────
#  API: OpenAI 면접 분석
# ─────────────────────────────────────────────
@require_POST
def api_analyze_interview(request):
    """전사된 질문별 답변을 직무·경력 기준으로 구조화 분석합니다."""
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"ok": False, "error": "잘못된 요청입니다."}, status=400)

    profile = body.get("profile", {}) if isinstance(body.get("profile", {}), dict) else {}
    raw_answers = body.get("answers", [])
    if not raw_answers:
        raw_answers = request.session.get("job_answers", [])
    answers = [
        _normalize_answer_record(item, index)
        for index, item in enumerate(raw_answers)
        if isinstance(item, dict)
    ]
    if not answers:
        return JsonResponse({"ok": False, "error": "분석할 면접 답변이 없습니다."}, status=400)

    career = str(profile.get("career") or request.session.get("job_career", "중견"))
    career_key = next((key for key in CAREER_LEVELS if key in career), "중견")
    field = normalize_job_field(profile.get("field") or request.session.get("job_field", "공사"))
    clean_profile = {
        "name": str(profile.get("name", "미입력"))[:100],
        "region": str(profile.get("region", "미입력"))[:100],
        "career": career_key,
        "field": field,
    }

    qa_parts = []
    for answer in answers:
        qa_parts.append(
            f"질문 {answer['q_index'] + 1}: {answer['question']}\n"
            f"전사 답변: {answer['answer'] or '(답변 없음)'}\n"
            f"답변 시간: {answer['duration']}초"
        )

    user_prompt = (
        f"지원 경력단계: {CAREER_LEVELS[career_key]['label']}\n"
        f"지원 직무: {JOB_FIELDS.get(field, field)}\n\n"
        + "\n\n".join(qa_parts)
        + "\n\n각 질문별 피드백의 q_index는 위 질문 번호에서 1을 뺀 값으로 작성하세요. "
          "전사가 불명확하거나 답변이 없으면 그 한계를 평가에 명시하세요."
    )

    client = get_openai_client()
    if client is None:
        return JsonResponse({"ok": False, "error": "OPENAI_API_KEY 또는 openai 패키지 설정이 필요합니다."}, status=503)

    try:
        response = client.responses.create(
            model=getattr(settings, "OPENAI_INTERVIEW_MODEL", "gpt-5.6"),
            input=[
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "construction_interview_report",
                    "schema": INTERVIEW_RESULT_SCHEMA,
                    "strict": True,
                }
            },
            max_output_tokens=2400,
            store=False,
        )
        raw = (response.output_text or "").strip()
        if not raw:
            raise ValueError("OpenAI 응답에 분석 본문이 없습니다.")
        result = json.loads(raw)
        scores, overall_score, grade = _score_grade(result.get("scores", {}))
        result["scores"] = scores
        result["overall_score"] = overall_score
        result["grade"] = grade
    except Exception:
        logger.exception("AI 면접 구조화 분석 실패")
        return JsonResponse({"ok": False, "error": "AI 면접 분석에 실패했습니다. OpenAI 키·모델 설정과 서버 로그를 확인해 주세요."}, status=502)

    request.session["job_result"] = result
    request.session["job_profile"] = clean_profile
    request.session["job_answers"] = answers
    request.session["job_career"] = career_key
    request.session["job_field"] = field
    request.session.modified = True

    return JsonResponse({"ok": True, "result": result})


# ─────────────────────────────────────────────
#  API: PDF 보고서 다운로드
# ─────────────────────────────────────────────
def api_download_report(request):
    """
    분석 결과를 PDF로 다운로드
    GET 요청, 세션에서 결과 불러옴
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        )
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from xml.sax.saxutils import escape as xml_escape
    except ImportError:
        return HttpResponse("reportlab 패키지가 설치되지 않았습니다. pip install reportlab", status=500)

    result = request.session.get("job_result", {})
    profile = request.session.get("job_profile", {})
    answers = request.session.get("job_answers", [])

    if not result:
        return HttpResponse("분석 결과가 없습니다. 먼저 면접을 진행해 주세요.", status=404)

    # macOS·Linux 한글 폰트를 찾고, 없으면 ReportLab 내장 한국어 CID 폰트를 사용합니다.
    font_name = "CBLKorean"
    font_candidates = [
        getattr(settings, "CBL_PDF_FONT_PATH", ""),
        os.path.expanduser("~/Library/Fonts/NanumGothic.ttf"),
        "/Library/Fonts/NanumGothic.ttf",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    registered = False
    for font_path in font_candidates:
        if font_path and os.path.isfile(font_path):
            try:
                pdfmetrics.registerFont(TTFont(font_name, font_path))
                registered = True
                break
            except Exception:
                continue
    if not registered:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
        font_name = "HYSMyeongJo-Medium"

    def safe_text(value):
        return xml_escape(str(value if value is not None else ""))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    CB_NAVY   = colors.HexColor("#073A5B")
    CB_YELLOW = colors.HexColor("#F9C20A")
    CB_GREEN  = colors.HexColor("#41a86b")
    CB_LIGHT  = colors.HexColor("#f0faf4")
    CB_GRAY   = colors.HexColor("#f8faf9")

    styles = getSampleStyleSheet()

    def style(name, **kw):
        kw.setdefault("fontName", font_name)
        return ParagraphStyle(name, **kw)

    s_title    = style("title",    fontSize=18, textColor=CB_NAVY,  spaceAfter=4,  leading=24, fontName=font_name)
    s_subtitle = style("sub",      fontSize=11, textColor=colors.HexColor("#69736d"), spaceAfter=12, leading=16)
    s_h2       = style("h2",       fontSize=13, textColor=CB_NAVY,  spaceAfter=6,  leading=18, fontName=font_name)
    s_body     = style("body",     fontSize=10, textColor=colors.HexColor("#151a17"), spaceAfter=4, leading=15)
    s_muted    = style("muted",    fontSize=9,  textColor=colors.HexColor("#69736d"), spaceAfter=3, leading=13)
    s_green    = style("green",    fontSize=10, textColor=CB_GREEN, fontName=font_name)
    s_bullet   = style("bullet",   fontSize=10, textColor=colors.HexColor("#151a17"), leftIndent=12, leading=15)

    story = []

    # ── 제목 ──
    story.append(Paragraph("AI 면접 분석 보고서", s_title))
    story.append(Paragraph(
        f"ChickenBanana Lab · 건설 AI 구인구직 · {datetime.now().strftime('%Y년 %m월 %d일')}",
        s_subtitle
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=CB_NAVY, spaceAfter=12))

    # ── 프로필 ──
    story.append(Paragraph("지원자 프로필", s_h2))
    field_key = normalize_job_field(profile.get("field", "공사"))
    profile_data = [
        ["이름", profile.get("name", "미입력"),   "경력", profile.get("career", "미입력")],
        ["직무", JOB_FIELDS.get(field_key, field_key), "희망지역", profile.get("region", "미입력")],
    ]
    profile_table = Table(profile_data, colWidths=[25*mm, 55*mm, 25*mm, 55*mm])
    profile_table.setStyle(TableStyle([
        ("FONTNAME",    (0,0),(-1,-1), font_name),
        ("FONTSIZE",    (0,0),(-1,-1), 10),
        ("TEXTCOLOR",   (0,0),(0,-1),  CB_NAVY),
        ("TEXTCOLOR",   (2,0),(2,-1),  CB_NAVY),
        ("FONTNAME",    (0,0),(0,-1),  font_name),
        ("FONTNAME",    (2,0),(2,-1),  font_name),
        ("BACKGROUND",  (0,0),(0,-1),  CB_GRAY),
        ("BACKGROUND",  (2,0),(2,-1),  CB_GRAY),
        ("GRID",        (0,0),(-1,-1), 0.5, colors.HexColor("#dfe5e1")),
        ("PADDING",     (0,0),(-1,-1), 6),
        ("VALIGN",      (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(profile_table)
    story.append(Spacer(1, 12))

    # ── 종합 등급 ──
    grade = result.get("grade", "B")
    grade_color = {
        "S": "#24633c", "A": "#1a5276",
        "B": "#d97706", "C": "#dc2626", "D": "#7f8c8d"
    }.get(grade, "#073A5B")

    story.append(Paragraph("종합 평가", s_h2))
    story.append(Paragraph(
        f'<font color="{grade_color}" size="22"><b>{safe_text(grade)}등급 · {int(result.get("overall_score", 0))}점</b></font>',
        style("grade", fontSize=22, spaceAfter=4, leading=28)
    ))
    story.append(Paragraph(safe_text(result.get("summary", "")), s_body))
    story.append(Spacer(1, 10))

    # ── 역량 점수 ──
    story.append(Paragraph("역량 평가", s_h2))
    scores = result.get("scores", {})
    score_labels = {
        "expertise":       "전문 지식",
        "field_response":  "실무 대응력",
        "problem_solving": "문제 해결력",
        "communication":   "커뮤니케이션",
        "growth_potential":"성장 가능성",
    }
    score_data = [["항목", "점수", "평가"]]
    for key, label in score_labels.items():
        sc = scores.get(key, 3)
        stars = "★" * sc + "☆" * (5 - sc)
        eval_text = {5:"탁월", 4:"우수", 3:"보통", 2:"미흡", 1:"개선필요"}.get(sc, "보통")
        score_data.append([label, stars, eval_text])

    score_table = Table(score_data, colWidths=[55*mm, 55*mm, 50*mm])
    score_table.setStyle(TableStyle([
        ("FONTNAME",   (0,0),(-1,-1), font_name),
        ("FONTSIZE",   (0,0),(-1,-1), 10),
        ("BACKGROUND", (0,0),(-1,0),  CB_NAVY),
        ("TEXTCOLOR",  (0,0),(-1,0),  colors.white),
        ("BACKGROUND", (0,1),(-1,-1), CB_LIGHT),
        ("ROWBACKGROUNDS", (0,1),(-1,-1), [colors.white, CB_LIGHT]),
        ("GRID",       (0,0),(-1,-1), 0.5, colors.HexColor("#dfe5e1")),
        ("PADDING",    (0,0),(-1,-1), 6),
        ("ALIGN",      (1,0),(1,-1),  "CENTER"),
        ("ALIGN",      (2,0),(2,-1),  "CENTER"),
        ("TEXTCOLOR",  (1,1),(-1,-1), CB_NAVY),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 12))

    # ── 강점 ──
    story.append(Paragraph("강점", s_h2))
    for s in result.get("strengths", []):
        story.append(Paragraph(f"✓  {safe_text(s)}", s_bullet))
    story.append(Spacer(1, 8))

    # ── 개선 사항 ──
    story.append(Paragraph("개선 필요 사항", s_h2))
    for imp in result.get("improvements", []):
        story.append(Paragraph(f"•  {safe_text(imp)}", s_bullet))
    story.append(Spacer(1, 8))

    # ── 적합 직무 ──
    story.append(Paragraph("추천 직무", s_h2))
    fit_jobs = result.get("fit_jobs", [])
    fit_text = "  /  ".join(fit_jobs)
    story.append(Paragraph(safe_text(fit_text), s_green))
    story.append(Spacer(1, 8))

    # ── 채용 추천 ──
    story.append(Paragraph("채용 추천 의견", s_h2))
    story.append(Paragraph(safe_text(result.get("recommendation", "")), s_body))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#dfe5e1"), spaceBefore=12))

    # ── 면접 Q&A ──
    story.append(Spacer(1, 8))
    story.append(Paragraph("면접 질문 및 답변 기록", s_h2))
    for a in answers:
        story.append(Paragraph(
            f"Q{a['q_index']+1}. {safe_text(a['question'])}",
            style("q", fontSize=10, textColor=CB_NAVY, spaceAfter=3, leading=15, fontName=font_name)
        ))
        ans_text = a.get("answer") or "(답변 없음 / 시간 초과)"
        story.append(Paragraph(f"A. {safe_text(ans_text)}", s_body))
        story.append(Paragraph(
            f"답변 시간: {a.get('duration', 0)}초",
            s_muted
        ))
        story.append(Spacer(1, 6))

    # ── 질문별 AI 피드백 ──
    feedback_items = result.get("question_feedback", [])
    if feedback_items:
        story.append(Spacer(1, 6))
        story.append(Paragraph("질문별 AI 피드백", s_h2))
        for item in feedback_items:
            q_number = int(item.get("q_index", 0)) + 1
            q_score = min(5, max(1, int(item.get("score", 1))))
            story.append(Paragraph(
                f"Q{q_number} · {q_score}/5 — {safe_text(item.get('feedback', ''))}",
                s_body,
            ))

    # ── 푸터 ──
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#dfe5e1"), spaceBefore=8))
    story.append(Paragraph(
        "본 보고서는 ChickenBanana Lab AI건설 구인구직 시스템이 자동 생성한 참고 자료입니다.",
        s_muted
    ))

    doc.build(story)

    buf.seek(0)
    filename = f"CBL_interview_report_{datetime.now().strftime('%Y%m%d')}.pdf"

    response = HttpResponse(buf.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
