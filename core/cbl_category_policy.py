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
