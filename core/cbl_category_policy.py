# ChickenBananaLab content category policy
# 화면/글쓰기/자동글에서 사용할 신규 카테고리 기준

CBL_PUBLIC_CATEGORY_CHOICES = [
    ("construction_work", "건설실무"),
    ("construction_tech", "건설기술"),
    ("construction_real", "건설부동산"),
    ("bim", "REVIT/BIM"),
    ("dynamo_automation", "Dynamo/자동화"),
    ("four_d_five_d", "4D/5D"),
    ("tech_ai_development", "AI·개발"),
    ("tech_data_security", "데이터·보안"),
    ("tech_server_software", "인터넷·서버·소프트"),
    ("program", "업무용 프로그램"),
    ("tool_recommend", "툴소개/툴추천"),
]

# 기존 글 호환용. DB 삭제 금지.
CBL_LEGACY_CATEGORY_CHOICES = [
    ("architecture", "건축"),
    ("realestate", "부동산"),
    ("finance", "금융"),
    ("tech", "테크"),
    ("life", "일상"),
]

CBL_MODEL_CATEGORY_CHOICES = CBL_PUBLIC_CATEGORY_CHOICES + CBL_LEGACY_CATEGORY_CHOICES

CBL_CATEGORY_LABELS = dict(CBL_MODEL_CATEGORY_CHOICES)
CBL_PUBLIC_CATEGORY_CODES = tuple(code for code, _label in CBL_PUBLIC_CATEGORY_CHOICES)
CBL_PUBLIC_CATEGORY_CODE_SET = frozenset(CBL_PUBLIC_CATEGORY_CODES)

CBL_AI_CATEGORY_GUIDE = {
    "construction_work": {
        "label": "건설실무",
        "keywords": ["현장관리", "시공", "공정", "원가", "하자", "안전", "자재", "문서", "공사일보", "품질관리"],
        "writing_focus": "건설 현장에서 바로 써먹을 수 있는 실무 중심 글. 공정, 원가, 품질, 안전, 문서, 하자 대응을 구체적으로 설명한다.",
    },
    "construction_tech": {
        "label": "건설기술",
        "keywords": ["스마트건설", "AI 건설기술", "드론", "로봇", "신공법", "건설장비", "현장 자동화", "스마트 안전"],
        "writing_focus": "건설 현장에 적용되는 기술, 장비, 자동화 사례를 실무자가 이해하기 쉽게 설명한다.",
    },
    "construction_real": {
        "label": "건설부동산",
        "keywords": ["분양", "청약", "재건축", "재개발", "공사비", "건설사", "부동산 정책", "시장 흐름"],
        "writing_focus": "건설과 부동산이 만나는 이슈를 다룬다. 청약, 분양, 재건축, 공사비, 정책 영향을 쉽게 설명한다.",
    },
    "bim": {
        "label": "REVIT/BIM",
        "keywords": ["Revit", "REVIT", "BIM", "패밀리", "템플릿", "모델링", "BIM 협업", "물량산출", "도면검토"],
        "writing_focus": "Revit과 BIM 실무 중심 글. 모델링, 패밀리, 템플릿, 협업, 도면검토, 물량산출 흐름을 설명한다.",
    },
    "dynamo_automation": {
        "label": "Dynamo/자동화",
        "keywords": ["Dynamo", "다이나모", "자동화", "노드", "파라미터", "엑셀 연동", "Python", "반복작업"],
        "writing_focus": "Dynamo와 Python을 활용한 반복작업 자동화, 파라미터 입력, 엑셀 연동, BIM 자동화를 실무 예제로 설명한다.",
    },
    "four_d_five_d": {
        "label": "4D/5D",
        "keywords": ["4D", "5D", "Navisworks", "공정 시뮬레이션", "공정 연동", "수량 연동", "원가 연동", "5D BIM"],
        "writing_focus": "4D 공정 시뮬레이션과 5D 원가·수량 연동을 중심으로 BIM 활용 방식을 설명한다.",
    },
    "tech_ai_development": {
        "label": "AI·개발",
        "keywords": ["AI", "인공지능", "개발", "Python", "Django", "웹개발", "앱개발", "API", "코딩", "생성형 AI"],
        "writing_focus": "AI와 소프트웨어 개발의 원리, 활용 사례, 구현 방법을 비전공자도 이해할 수 있도록 구체적으로 설명한다.",
    },
    "tech_data_security": {
        "label": "데이터·보안",
        "keywords": ["데이터", "데이터베이스", "DB", "보안", "개인정보", "암호화", "백업", "로그", "인증", "해킹"],
        "writing_focus": "데이터 관리와 보안을 중심으로 저장, 백업, 인증, 개인정보 보호와 실무 대응 방법을 설명한다.",
    },
    "tech_server_software": {
        "label": "인터넷·서버·소프트",
        "keywords": ["인터넷", "서버", "소프트웨어", "클라우드", "호스팅", "도메인", "네트워크", "IPv4", "IPv6", "SSL"],
        "writing_focus": "인터넷, 서버, 네트워크, 클라우드와 소프트웨어의 구조 및 설정 방법을 실용적으로 설명한다.",
    },
    "program": {
        "label": "업무용 프로그램",
        "keywords": ["ChickenBananaLab", "프로그램", "PDF", "ZIP", "VIEW", "SS", "CUT", "설치법", "사용법", "업무용 프로그램"],
        "writing_focus": "업무용 프로그램 소개, 설치법, 사용법, 기능 설명, 실제 업무 적용 사례를 다룬다.",
    },
    "tool_recommend": {
        "label": "툴소개/툴추천",
        "keywords": ["AI 도구", "생산성 도구", "추천툴", "툴 추천", "무료 툴", "유료 툴", "업무 효율", "자동화 도구"],
        "writing_focus": "업무 효율을 높이는 툴 소개와 추천. 무료/유료 비교, 사용법, 장단점, 추천 대상을 구체적으로 설명한다.",
    },
}

def cbl_public_category_choices():
    return CBL_PUBLIC_CATEGORY_CHOICES

def cbl_model_category_choices():
    return CBL_MODEL_CATEGORY_CHOICES

def cbl_category_label(slug):
    return CBL_CATEGORY_LABELS.get(slug, slug)

def cbl_ai_category_guide(slug):
    return CBL_AI_CATEGORY_GUIDE.get(slug, {})


_CBL_CATEGORY_LABEL_TO_CODE = {
    str(label).strip().casefold(): code
    for code, label in CBL_PUBLIC_CATEGORY_CHOICES
}
_CBL_CATEGORY_CODE_CASEFOLD = {
    str(code).strip().casefold(): code
    for code, _label in CBL_PUBLIC_CATEGORY_CHOICES
}
_CBL_LEGACY_TECH_VALUES = {
    "tech", "테크", "기술", "기술일반", "기술 일반", "general tech",
}
_CBL_SAFE_LEGACY_CATEGORY_MAP = {
    "architecture": "construction_work",
    "건축": "construction_work",
    "건설": "construction_work",
    "realestate": "construction_real",
    "real_estate": "construction_real",
    "부동산": "construction_real",
    "finance": "construction_real",
    "금융": "construction_real",
}

_CBL_AUTO_CATEGORY_TERMS = {
    "tech_ai_development": (
        "ai", "인공지능", "llm", "대규모 언어 모델", "생성형", "에이전트",
        "agent", "머신러닝", "machine learning", "딥러닝", "강화학습",
        "강화 학습", "tpu", "gpu", "모델 실행", "모델 훈련", "훈련",
        "프레임워크", "framework", "ray", "tunix", "개발", "코딩",
        "python", "django", "api",
    ),
    "tech_data_security": (
        "데이터베이스", "database", "개인정보", "보안", "security",
        "암호화", "해킹", "인증", "접근제어", "데이터 분석", "데이터분석",
        "빅데이터", "백업", "복구",
    ),
    "tech_server_software": (
        "서버", "server", "네트워크", "network", "클라우드", "cloud",
        "웹", "인터넷", "internet", "소프트웨어", "software", "호스팅",
        "도메인", "dns", "ssl", "http",
    ),
    "bim": ("revit", "bim", "패밀리", "bim 협업", "도면검토"),
    "dynamo_automation": ("dynamo", "다이나모", "노드", "파라미터 자동"),
    "four_d_five_d": ("4d", "5d", "navisworks", "공정 시뮬레이션"),
    "construction_work": (
        "현장관리", "시공", "공사일보", "하자", "안전관리", "품질관리",
    ),
    "construction_tech": (
        "스마트건설", "건설기술", "드론", "건설 로봇", "신공법",
    ),
    "construction_real": (
        "분양", "청약", "재건축", "재개발", "공사비", "부동산",
    ),
    "program": ("업무용 프로그램", "설치법", "사용법", "pdf 프로그램"),
    "tool_recommend": (
        "툴 추천", "툴추천", "추천 도구", "생산성 도구", "ai 도구",
    ),
}


def cbl_auto_category_prompt_guide():
    """자동 분류 프롬프트와 관리자 선택지가 공유하는 정확한 저장값/표시명."""
    return "\n".join(
        f"- {code}: {label}"
        for code, label in CBL_PUBLIC_CATEGORY_CHOICES
    )


def cbl_resolve_auto_post_category(raw_category, *, title="", summary="", content=""):
    """
    자동글 저장용 카테고리를 canonical code로 검증한다.

    반환값은 (canonical 또는 None, diagnostics)이다. 허용 목록 밖 값이나 legacy
    'tech'는 제목·요약·본문의 짧은 텍스트 근거로 재분류하며, 근거가 없을 때 임의의
    첫 카테고리로 보내지 않는다.
    """
    raw_text = str(raw_category or "").strip()
    folded = raw_text.casefold()
    normalized = (
        _CBL_CATEGORY_CODE_CASEFOLD.get(folded)
        or _CBL_CATEGORY_LABEL_TO_CODE.get(folded)
        or _CBL_SAFE_LEGACY_CATEGORY_MAP.get(folded)
        or raw_text
    )
    legacy_mapping_used = folded in {
        value.casefold() for value in _CBL_LEGACY_TECH_VALUES
    }

    if normalized in CBL_PUBLIC_CATEGORY_CODE_SET and not legacy_mapping_used:
        return normalized, {
            "raw_category": raw_text,
            "normalized_before": normalized,
            "canonical_category": normalized,
            "legacy_mapping_used": False,
            "fallback_reason": "",
        }

    searchable = " ".join(
        str(value or "") for value in (title, summary, content)
    ).casefold()[:12000]
    scores = {}
    for code, terms in _CBL_AUTO_CATEGORY_TERMS.items():
        score = sum(
            2 if len(term) >= 4 else 1
            for term in terms
            if term.casefold() in searchable
        )
        if score:
            scores[code] = score

    canonical = None
    fallback_reason = "unsupported_category_without_text_evidence"
    if scores:
        top_score = max(scores.values())
        winners = [code for code, score in scores.items() if score == top_score]
        if len(winners) == 1:
            canonical = winners[0]
            fallback_reason = (
                "legacy_tech_reclassified_from_content"
                if legacy_mapping_used
                else "unsupported_category_reclassified_from_content"
            )
        else:
            fallback_reason = "ambiguous_text_evidence"

    return canonical, {
        "raw_category": raw_text,
        "normalized_before": normalized,
        "canonical_category": canonical,
        "legacy_mapping_used": legacy_mapping_used,
        "fallback_reason": fallback_reason,
        "category_scores": scores,
    }
