import logging
from django.db import models
from .models import CalendarEvent as CBLCalendarEvent
from django.contrib.admin.views.decorators import staff_member_required as cbl_staff_member_required
from django.views.decorators.http import require_POST as cbl_require_POST
from django.http import JsonResponse as CBLJsonResponse, JsonResponse, HttpResponse
import calendar
import json
import os
import uuid
import traceback
import hashlib
import time as _cbl_time

from datetime import date, timedelta

from curl_cffi import request
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q, Count, Sum, Min
from django.db.models.functions import TruncDate
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.middleware.csrf import get_token
from django.views.decorators.http import require_POST
from django.utils.text import slugify

from .market_data import get_market_data
from .realestate_subscription import get_latest_subscription_items
from .models import (
    CalendarEvent,
    Post,
    Comment,
    UserProfile,
    ExperienceVault,
    VisitLog,
    AIAutoWriterSetting,
    AIAutoKeywordQueue,
    HomeProgramDownload,
)
from .forms import PostForm, CommentForm, NicknameForm, ExperienceVaultForm
from .naver_news import recommend_keywords_from_news
from .keyword_dedupe import unpack_recommendation, is_duplicate_candidate, build_news_context
from .ai_writer import (
    generate_ai_post,
    generate_english_ai_post,
    generate_post_topics,
    recommend_today_keywords,
    make_generated_image_file,
    save_inline_image,
    replace_image_placeholders,
)

from .telegram_alerts import notify_post_view, notify_signup
from .account_extras import CblSignupForm, INTEREST_CHOICES

CATEGORY_PAGES = {
    "architecture": {
        "title": "건축",
        "label": "Architecture",
        "icon": "🏠",
        "headline": "건축 자동화와 현장관리",
        "description": "CAD/BIM 수량산출, 현장 사진관리, 공정 데이터, 공사일보 자동화를 다룹니다.",
        "theme": "architecture",
    },
    "bim": {
        "title": "REVIT/BIM",
        "label": "BIM",
        "icon": "▧",
        "headline": "Revit·Dynamo·4D/5D 자동화",
        "description": "Revit, Dynamo, 4D/5D, 모델링, 자동화 컨텐츠를 다룹니다.",
        "theme": "bim",
    },
    "realestate": {
        "title": "부동산",
        "label": "Real Estate",
        "icon": "🏢",
        "headline": "부동산 정보와 데이터 분석",
        "description": "아파트, 오피스텔, 토지, 분양, 투자 데이터를 정리하고 분석합니다.",
        "theme": "realestate",
    },
    "finance": {
        "title": "금융",
        "label": "Finance",
        "icon": "💹",
        "headline": "금융 데이터와 자동매매",
        "description": "코인, 주식, 자동매매, 자산현황, 손익 그래프를 관리합니다.",
        "theme": "finance",
    },
    "tech": {
        "title": "테크",
        "label": "Tech",
        "icon": "💻",
        "headline": "기술 개발과 AI 자동화",
        "description": "AI, 개발, 데이터, 보안, 인터넷, 서버, 소프트, IT 기기 컨텐츠를 다룹니다.",
        "theme": "tech",
    },
    "program": {
        "title": "업무용 프로그램",
        "label": "Programs",
        "icon": "⌘",
        "headline": "업무용 프로그램과 추천 툴",
        "description": "업무용 프로그램, 툴소개/추천툴 컨텐츠를 다룹니다.",
        "theme": "program",
    },
    "life": {
        "title": "일상",
        "label": "Daily",
        "icon": "☕",
        "headline": "일상 기록과 콘텐츠",
        "description": "일상, 육아, 쇼츠, 유튜브, 장비 리뷰 같은 콘텐츠를 정리합니다.",
        "theme": "life",
    },
}


# CBL_CONSTRUCTION_CATEGORY_PAGES_START
# 글 작성 카테고리는 건축/부동산을 직접 쓰지 않고
# 건설실무/건설기술/건설부동산으로 나눕니다.
CONSTRUCTION_CATEGORY_SLUGS = [
    "construction_work",
    "construction_tech",
    "construction_real",
]
CONSTRUCTION_CATEGORY_LABELS = {
    "construction_work": "건설실무",
    "construction_tech": "건설기술",
    "construction_real": "건설부동산",
}

CATEGORY_PAGES.update({
    "construction_work": {
        "title": "건설실무",
        "label": "Construction Practice",
        "icon": "🏗️",
        "headline": "시공·공정·적산·원가 실무",
        "description": "현장, 공정, 견적, 문서, 원가, 이슈, 시공 실무를 정리합니다.",
        "theme": "architecture",
    },
    "construction_tech": {
        "title": "건설기술",
        "label": "Construction Technology",
        "icon": "🧱",
        "headline": "BIM·AI·스마트건설 기술",
        "description": "BIM, CAD, AI 자동화, 스마트건설, 도면 검토 기술을 다룹니다.",
        "theme": "architecture",
    },
    "construction_real": {
        "title": "건설부동산",
        "label": "Construction Real Estate",
        "icon": "🏢",
        "headline": "분양·청약·재건축·건설 부동산",
        "description": "분양, 청약, 재건축, 개발, 공사비와 부동산 흐름을 정리합니다.",
        "theme": "realestate",
    },
})


def cbl_normalize_editor_category(value):
    value = str(value or "").strip()
    alias = {
        "건설": "construction_work",
        "건설실무": "construction_work",
        "시공": "construction_work",
        "건축": "construction_work",
        "architecture": "construction_work",
        "construction_work": "construction_work",

        "건설기술": "construction_tech",
        "BIM": "construction_tech",
        "bim": "construction_tech",
        "construction_tech": "construction_tech",

        "건설부동산": "construction_real",
        "건설 부동산": "construction_real",
        "부동산": "construction_real",
        "realestate": "construction_real",
        "real_estate": "construction_real",
        "construction_real": "construction_real",

        "금융": "finance",
        "경제": "finance",
        "finance": "finance",
        "테크": "tech",
        "IT": "tech",
        "it": "tech",
        "tech": "tech",
        "일상": "life",
        "생활": "life",
        "라이프": "life",
        "life": "life",
    }
    return alias.get(value, value)
# CBL_CONSTRUCTION_CATEGORY_PAGES_END


# CBL_BTP_PORTAL_CONFIG_START
CBL_BTP_PORTAL_CONFIG = {
    "bim": {
        "title": "REVIT/BIM",
        "subtitle": "Revit, Dynamo, 4D/5D, 모델링, 자동화",
        "search_placeholder": "BIM 자료, Revit, Dynamo, 자동화 정보를 검색하세요",
        "badge": "BIM",
        "fallback_icon": "▧",
        "recent_title": "최근 컨텐츠",
        "main_title": "Revit · BIM 컨텐츠",
        "main_badge": "Revit/BIM",
        "main_empty": "Revit·BIM 컨텐츠가 아직 없습니다.",
        "sub_title": "Dynamo · 자동화",
        "sub_badge": "Dynamo",
        "sub_empty": "Dynamo·자동화 컨텐츠가 아직 없습니다.",
        "third_title": "4D/5D",
        "third_badge": "4D/5D",
        "third_empty": "4D/5D 컨텐츠가 아직 없습니다.",
        "video_title": "BIM 동영상/쇼츠",
        "video_badge": "BIM영상",
        "all_keywords": ["BIM", "Revit", "레빗", "Dynamo", "다이나모", "4D", "5D", "모델링", "수량산출", "자동화"],
        "main_keywords": ["BIM", "Revit", "레빗", "모델", "패밀리", "템플릿", "수량산출"],
        "sub_keywords": ["Dynamo", "다이나모", "스크립트", "자동화", "파라미터", "Python"],
        "third_keywords": ["4D", "5D", "모델링", "시뮬레이션", "공정", "원가", "Navisworks"],
    },
    "tech": {
        "title": "테크",
        "subtitle": "AI, 개발, 데이터, 보안, 인터넷, 서버, 소프트, IT 기기",
        "search_placeholder": "AI, 개발, 데이터, 서버, 보안 정보를 검색하세요",
        "badge": "테크",
        "fallback_icon": "▣",
        "recent_title": "최근 컨텐츠",
        "main_title": "AI · 개발 컨텐츠",
        "main_badge": "AI/개발",
        "main_empty": "AI·개발 컨텐츠가 아직 없습니다.",
        "sub_title": "데이터 · 보안",
        "sub_badge": "데이터/보안",
        "sub_empty": "데이터·보안 컨텐츠가 아직 없습니다.",
        "third_title": "인터넷 · 서버 · 소프트",
        "third_badge": "서버/소프트",
        "third_empty": "인터넷·서버·소프트 컨텐츠가 아직 없습니다.",
        "video_title": "테크 동영상/쇼츠",
        "video_badge": "테크영상",
        "all_keywords": ["AI", "개발", "데이터", "보안", "인터넷", "서버", "소프트", "Python", "Django", "클라우드"],
        "main_keywords": ["AI", "개발", "Python", "Django", "앱", "웹", "자동화", "코딩"],
        "sub_keywords": ["데이터", "보안", "DB", "API", "백업", "로그", "개인정보"],
        "third_keywords": ["인터넷", "서버", "소프트", "클라우드", "호스팅", "도메인", "SSL", "HTTPS"],
    },
    "program": {
        "title": "업무용 프로그램",
        "subtitle": "업무용 프로그램, 툴소개/추천툴",
        "search_placeholder": "업무용 프로그램, 툴소개, 추천툴을 검색하세요",
        "badge": "프로그램",
        "fallback_icon": "⌘",
        "recent_title": "최근 컨텐츠",
        "main_title": "업무용 프로그램 컨텐츠",
        "main_badge": "업무툴",
        "main_empty": "업무용 프로그램 컨텐츠가 아직 없습니다.",
        "sub_title": "툴소개/추천툴",
        "sub_badge": "툴소개",
        "sub_empty": "툴소개/추천툴 컨텐츠가 아직 없습니다.",
        "third_title": "추천툴",
        "third_badge": "추천툴",
        "third_empty": "추천툴 컨텐츠가 아직 없습니다.",
        "video_title": "프로그램 동영상/쇼츠",
        "video_badge": "프로그램영상",
        "all_keywords": ["프로그램", "앱", "툴", "자동화", "추천", "업무", "다운로드", "ZIP", "PDF", "뷰어"],
        "main_keywords": ["프로그램", "업무용", "앱", "자동화", "다운로드", "설치"],
        "sub_keywords": ["툴", "소개", "기능", "사용법", "리뷰", "비교", "추천", "추천툴", "생산성", "업무효율", "무료", "유료"],
        "third_keywords": ["추천", "추천툴", "생산성", "업무효율", "무료", "유료"],
    },
}
# CBL_BTP_PORTAL_CONFIG_END


def get_post_detail_context(post):
    """
    영어 글(slug가 en-으로 시작하는 글)은 상세페이지 UI 문구를 영어로 표시합니다.
    """
    slug_value = str(getattr(post, "slug", "") or "")
    is_english = slug_value.startswith("en-")

    category_page = CATEGORY_PAGES.get(post.category, {})

    if is_english:
        category_label = category_page.get("label") or post.category
    else:
        category_label = post.get_category_display()

    return {
        "post": post,
        "is_english": is_english,
        "category_label": category_label,
        "comments": post.comments.select_related(
            "author",
            "author__profile",
        ).all(),
    }


def admin_required(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)

def is_internal_user(user):
    """
    사이트 운영자/관리자/부관리자는 방문 통계와 글 조회수에서 제외합니다.
    """
    if not user.is_authenticated:
        return False

    if user.is_staff or user.is_superuser:
        return True

    try:
        return user.profile.is_sub_admin
    except UserProfile.DoesNotExist:
        return False

def can_write_post(user):
    if not user.is_authenticated:
        return False

    if user.is_staff or user.is_superuser:
        return True

    try:
        return user.profile.is_sub_admin
    except UserProfile.DoesNotExist:
        return False


def editor_context(extra_context=None):
    context = {
        "kakao_javascript_key": settings.KAKAO_JAVASCRIPT_KEY,
    }

    if extra_context:
        context.update(extra_context)

    return context


def get_post_field_names():
    return [field.name for field in Post._meta.fields]


def set_post_optional_seo_fields(post, ai_data):
    """
    Post 모델에 summary, meta_description, thumbnail_prompt 같은 필드가 있을 경우에만 저장.
    아직 모델에 해당 필드가 없어도 에러 없이 지나가도록 처리.
    """
    post_field_names = get_post_field_names()
    update_fields = []

    if "summary" in post_field_names:
        post.summary = ai_data.get("summary", "")
        update_fields.append("summary")

    if "meta_description" in post_field_names:
        post.meta_description = ai_data.get("meta_description", "")
        update_fields.append("meta_description")

    if "thumbnail_prompt" in post_field_names:
        post.thumbnail_prompt = ai_data.get("thumbnail_prompt", "")
        update_fields.append("thumbnail_prompt")

    if update_fields:
        if "updated_at" in post_field_names:
            update_fields.append("updated_at")

        post.save(update_fields=update_fields)


def normalize_html_spaces(value):
    """
    에디터에서 생기는 &nbsp; /   공백을 일반 공백으로 정리합니다.
    카드 요약에 &nbsp;가 그대로 노출되는 문제를 예방합니다.
    """
    if not isinstance(value, str):
        return value

    targets = [
        "&nbsp;",
        "&amp;nbsp;",
        "&#160;",
        "&amp;#160;",
        "\xa0",
    ]

    for target in targets:
        value = value.replace(target, " ")

    return value


def get_plain_text_length(value):
    """
    HTML 태그와 특수 공백을 제거한 실제 본문 글자 수를 계산합니다.
    자동글이 제목/썸네일만 저장되고 content가 비는 문제를 방지하기 위한 검증용입니다.
    """
    text = normalize_html_spaces(value or "")
    text = strip_tags(text)
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = " ".join(text.split())
    return len(text)


def validate_generated_content_or_raise(content, title="", min_length=200):
    """
    AI 자동글 저장 직전 최종 본문을 검증합니다.
    본문이 비었거나 지나치게 짧으면 Post를 저장하지 않고 에러로 중단합니다.
    """
    content = normalize_html_spaces(content or "").strip()
    plain_length = get_plain_text_length(content)

    if plain_length < min_length:
        short_title = str(title or "제목 없음").strip()[:80]
        raise ValueError(
            f"AI 글 생성 실패: 본문이 비어 있거나 너무 짧아 저장하지 않았습니다. "
            f"제목='{short_title}', 본문 글자수={plain_length}자, 최소 기준={min_length}자"
        )

    return content


def delete_file_safely(file_name):
    if not file_name:
        return

    try:
        if default_storage.exists(file_name):
            default_storage.delete(file_name)
    except Exception:
        pass


def cbl_video_post_q():
    """실제 재생 가능한 영상/쇼츠가 연결된 게시글만 판별합니다."""
    return (
        Q(post_type="video")
        | (Q(youtube_url__isnull=False) & ~Q(youtube_url=""))
        | (Q(video_file__isnull=False) & ~Q(video_file=""))
        | (Q(shorts_video__isnull=False) & ~Q(shorts_video=""))
    )


def cbl_effective_category_key(post):
    """기존 글을 현재 운영 중인 카테고리 중 가장 가까운 분류로 표시합니다."""
    current_categories = {
        "construction_work",
        "construction_tech",
        "construction_real",
        "bim",
        "dynamo_automation",
        "four_d_five_d",
        "tech_ai_development",
        "tech_data_security",
        "tech_server_software",
        "program",
        "tool_recommend",
    }

    original_category = str(getattr(post, "category", "") or "")
    if original_category in current_categories:
        return original_category

    text = " ".join([
        str(getattr(post, "title", "") or ""),
        str(getattr(post, "summary", "") or ""),
        str(getattr(post, "tags", "") or ""),
        strip_tags(str(getattr(post, "content", "") or "")),
    ]).lower()

    if "dynamo" in text or "다이나모" in text:
        return "dynamo_automation"

    if any(word in text for word in ["4d", "5d", "4차원", "5차원"]):
        return "four_d_five_d"

    if any(word in text for word in ["bim", "revit", "레빗", "navisworks", "나비스웍스"]):
        return "bim"

    if original_category == "architecture" and any(word in text for word in [
        "cad", "자동화", "스마트건설", "건설기술", "드론", "스캔", "디지털", "모듈러", "osc", "ai",
    ]):
        return "construction_tech"

    if original_category in {"realestate", "finance"} or any(word in text for word in [
        "부동산", "분양", "청약", "아파트", "재건축", "재개발", "토지", "금리", "대출",
    ]):
        return "construction_real"

    if any(word in text for word in [
        "프로그램", "소프트웨어", "다운로드", "설치", "스크립트", "플러그인", "매크로",
    ]):
        return "program"

    if original_category == "tech" and any(word in text for word in [
        "데이터", "database", "db", "보안", "개인정보", "암호화", "백업", "로그", "인증", "해킹",
    ]):
        return "tech_data_security"

    if original_category == "tech" and any(word in text for word in [
        "인터넷", "서버", "클라우드", "호스팅", "도메인", "네트워크",
        "ipv4", "ipv6", "ssl", "https", "소프트웨어",
    ]):
        return "tech_server_software"

    if original_category == "tech" and any(word in text for word in [
        "ai", "인공지능", "개발", "python", "django", "코딩", "api", "생성형",
    ]):
        return "tech_ai_development"

    if original_category in {"tech", "life"} or any(word in text for word in [
        "앱", "툴", "도구", "추천", "리뷰", "비교", "노트북", "스마트폰", "태블릿",
    ]):
        return "tool_recommend"

    if any(word in text for word in [
        "cad", "자동화", "스마트건설", "건설기술", "드론", "스캔", "디지털", "모듈러", "osc",
    ]):
        return "construction_tech"

    if original_category == "architecture":
        return "construction_work"

    return None


def cbl_apply_effective_categories(posts):
    """조회된 객체의 화면 표시용 카테고리만 현재 분류로 바꿉니다. DB에는 저장하지 않습니다."""
    for post in posts:
        post.category = cbl_effective_category_key(post)
    return posts


def cbl_posts_by_effective_categories(queryset, categories, limit):
    """기존 카테고리 글을 현재 분류로 판별한 뒤 관련 글만 반환합니다."""
    allowed = set(categories)
    matched = []
    # 오래된 글까지 무제한 순회하지 않으면서 최근 후보는 충분히 확인합니다.
    for post in queryset[:200]:
        effective_category = cbl_effective_category_key(post)
        if effective_category not in allowed:
            continue
        post.category = effective_category
        matched.append(post)
        if len(matched) >= limit:
            break
    return matched


def home(request):
    published = Post.objects.filter(is_published=True).order_by("-created_at")
    regular_published = published.exclude(cbl_video_post_q())

    # 최근 콘텐츠는 현재 운영 중인 실제 저장 카테고리만 사용합니다.
    # 제목/본문 키워드로 다른 페이지 글을 끌어오거나 화면용 카테고리를 덮어쓰지 않습니다.
    current_categories = [
        "construction_work",
        "construction_tech",
        "construction_real",
        "bim",
        "dynamo_automation",
        "four_d_five_d",
        "tech_ai_development",
        "tech_data_security",
        "tech_server_software",
        "program",
        "tool_recommend",
    ]
    latest_all = cbl_posts_by_effective_categories(
        regular_published, current_categories, 4
    )
    latest_architecture = cbl_posts_by_effective_categories(regular_published, [
        "construction_work",
        "construction_tech",
        "construction_real",
    ], 4)
    latest_bim = cbl_posts_by_effective_categories(regular_published, [
        "bim",
        "dynamo_automation",
        "four_d_five_d",
    ], 4)
    latest_tech = cbl_posts_by_effective_categories(
        regular_published, [
            "tech_ai_development",
            "tech_data_security",
            "tech_server_software",
        ], 4
    )
    latest_program = cbl_posts_by_effective_categories(regular_published, [
        "program",
        "tool_recommend",
    ], 4)

    popular_programs = (
        published.exclude(program_file="")
        .order_by("-views", "-created_at")[:5]
    )

    resource_posts = (
        published.filter(
            Q(title__icontains="자료")
            | Q(title__icontains="체크리스트")
            | Q(title__icontains="템플릿")
            | Q(tags__icontains="자료")
        )[:6]
    )

    recent_comments = (
        Comment.objects.select_related("post", "author")
        .filter(post__is_published=True)
        .order_by("-created_at")[:5]
    )

    home_video_posts = list(
        published.filter(cbl_video_post_q()).distinct()[:8]
    )

    return render(request, "core/home.html", {
        "latest_posts": latest_all,
        "latest_all": latest_all,
        "latest_architecture": latest_architecture,
        "latest_bim": latest_bim,
        "latest_tech": latest_tech,
        "latest_program": latest_program,
        "popular_programs": popular_programs,
        "resource_posts": resource_posts,
        "recent_comments": recent_comments,
        "home_video_posts": home_video_posts,
    })


def category_page(request, slug):
    page = CATEGORY_PAGES.get(slug)

    if page is None:
        raise Http404("존재하지 않는 페이지입니다.")

    base_posts = Post.objects.filter(
        category=slug,
        is_published=True,
    ).exclude(cbl_video_post_q()).order_by("-created_at")

    posts = base_posts[:15]

    subscription_data = {
        "items": [],
        "error": "",
        "updated_at": "",
        "total_count": 0,
    }

    if slug in ("realestate", "architecture", "construction_real"):
        subscription_data = get_latest_subscription_items(limit=30)

    context = {
        "page": page,
        "slug": slug,
        "posts": posts,
        "subscription_items": subscription_data["items"],
        "subscription_error": subscription_data["error"],
        "subscription_updated_at": subscription_data["updated_at"],
        "subscription_total_count": subscription_data["total_count"],
    }

    # CBL_PROGRAM_PAGE_UPLOADED_DOWNLOADS_CONTEXT_START
    # 홈 인기 프로그램 팝업에서 업로드한 파일을 /program/ 페이지에도 표시합니다.
    program_page_is_staff = bool(
        request.user.is_authenticated and (
            request.user.is_staff or request.user.is_superuser
        )
    )

    program_uploaded_downloads = []

    if slug == "program":
        program_uploaded_qs = HomeProgramDownload.objects.all().order_by("order", "id")

        # 일반 사용자는 공개 + 파일 있음 상태만 볼 수 있습니다.
        if not program_page_is_staff:
            program_uploaded_qs = (
                program_uploaded_qs
                .filter(is_public=True, file__isnull=False)
                .exclude(file="")
            )

        program_uploaded_downloads = list(program_uploaded_qs)

    context["program_uploaded_downloads"] = program_uploaded_downloads
    context["program_page_is_staff"] = program_page_is_staff
    # CBL_PROGRAM_PAGE_UPLOADED_DOWNLOADS_CONTEXT_END


    # CBL_BTP_PORTAL_CONTEXT_START
    if slug in CBL_BTP_PORTAL_CONFIG:
        portal_cfg = CBL_BTP_PORTAL_CONFIG[slug]

        def portal_keyword_q(*keywords):
            query = Q()
            for keyword in keywords:
                query |= Q(title__icontains=keyword)
                query |= Q(summary__icontains=keyword)
                query |= Q(content__icontains=keyword)
                query |= Q(tags__icontains=keyword)
            return query

        def portal_fill(primary_qs, fallback_qs, limit):
            items = list(primary_qs[:limit])
            seen_ids = [item.pk for item in items]
            if len(items) < limit:
                items.extend(list(fallback_qs.exclude(pk__in=seen_ids)[: limit - len(items)]))
            return items

        portal_video_q = cbl_video_post_q()

        # 포털과 각 섹션은 실제 저장 카테고리만 사용합니다.
        # 글이 부족하더라도 제목/본문 키워드가 우연히 겹치는 다른 카테고리
        # 게시글을 가져오지 않습니다.
        portal_category_pools = {
            "bim": {
                "recent": ["bim", "dynamo_automation", "four_d_five_d"],
                "main": ["bim"],
                "sub": ["dynamo_automation"],
                "third": ["four_d_five_d"],
                "keyword_sections": [],
            },
            "tech": {
                "recent": [
                    "tech_ai_development",
                    "tech_data_security",
                    "tech_server_software",
                ],
                "main": ["tech_ai_development"],
                "sub": ["tech_data_security"],
                "third": ["tech_server_software"],
                "keyword_sections": [],
            },
            "program": {
                "recent": ["program", "tool_recommend"],
                "main": ["program"],
                "sub": ["tool_recommend"],
                "third": ["tool_recommend"],
                "keyword_sections": ["sub", "third"],
            },
        }
        portal_pools = portal_category_pools.get(
            slug,
            {
                "recent": [slug],
                "main": [slug],
                "sub": [slug],
                "third": [slug],
                "keyword_sections": ["main", "sub", "third"],
            },
        )

        def portal_effective_posts(pool_name, limit, videos=False):
            queryset = Post.objects.filter(is_published=True)
            if pool_name in portal_pools["keyword_sections"]:
                keywords = portal_cfg[f"{pool_name}_keywords"]
                queryset = queryset.filter(portal_keyword_q(*keywords))
            if videos:
                queryset = queryset.filter(portal_video_q)
            else:
                queryset = queryset.exclude(portal_video_q)
            return cbl_posts_by_effective_categories(
                queryset.order_by("-created_at").distinct(),
                portal_pools[pool_name],
                limit,
            )

        portal_recent_posts = portal_effective_posts(
            "recent",
            5,
        )

        portal_main_posts = portal_effective_posts(
            "main",
            4,
        )
        portal_sub_posts = portal_effective_posts(
            "sub",
            3,
        )
        portal_third_posts = portal_effective_posts(
            "third",
            6,
        )
        portal_video_posts = portal_effective_posts(
            "recent",
            3,
            videos=True,
        )


        def portal_section_videos(pool_name, limit=64):
            return portal_effective_posts(
                pool_name,
                limit,
                videos=True,
            )
        context.update({
            "portal_config": portal_cfg,
            "portal_recent_posts": portal_recent_posts,
            "portal_main_posts": portal_main_posts,
            "portal_sub_posts": portal_sub_posts,
            "portal_third_posts": portal_third_posts,
            "portal_video_posts": portal_video_posts,
            "portal_main_popup_posts": portal_effective_posts(
                "main",
                80,
            ),
            "portal_sub_popup_posts": portal_effective_posts(
                "sub",
                80,
            ),
            "portal_third_popup_posts": portal_effective_posts(
                "third",
                80,
            ),
            "portal_main_popup_video_posts": portal_section_videos("main", 64),
            "portal_sub_popup_video_posts": portal_section_videos("sub", 64),
            "portal_third_popup_video_posts": portal_section_videos("third", 64),
            "portal_video_popup_posts": portal_effective_posts(
                "recent",
                64,
                videos=True,
            ),
        })
    # CBL_BTP_PORTAL_CONTEXT_END

    if slug == "architecture":
        def keyword_q(*keywords):
            query = Q()
            for keyword in keywords:
                query |= Q(title__icontains=keyword)
                query |= Q(summary__icontains=keyword)
                query |= Q(content__icontains=keyword)
                query |= Q(tags__icontains=keyword)
            return query

        def fill_posts(primary_qs, fallback_qs, limit):
            items = list(primary_qs[:limit])
            seen_ids = [item.pk for item in items]
            if len(items) < limit:
                items.extend(list(fallback_qs.exclude(pk__in=seen_ids)[: limit - len(items)]))
            return items

        video_q = cbl_video_post_q()

        architecture_all = Post.objects.filter(
            category="architecture",
            is_published=True,
        ).order_by("-created_at")
        architecture_base = architecture_all.exclude(video_q)

        construction_property_base = Post.objects.filter(
            is_published=True,
        ).filter(
            Q(category="architecture") | Q(category="realestate")
        ).exclude(video_q).order_by("-created_at")

        practical_q = keyword_q(
            "시공", "공정", "적산", "수량", "원가", "공사비", "실행예산",
            "견적", "계약", "클레임", "품질", "안전", "현장", "체크리스트",
            "공정표", "프리캐스트", "물량산출", "내역", "정산",
        )
        technology_q = keyword_q(
            "BIM", "CAD", "Revit", "레빗", "Dynamo", "다이나모", "AI", "자동화",
            "스마트건설", "건설기술", "건설로봇", "드론", "스캔", "도면", "검토",
            "수량산출", "디지털", "모듈러", "OSC",
        )
        property_q = keyword_q(
            "부동산", "재건축", "재개발", "분양", "청약", "아파트", "오피스텔",
            "토지", "개발", "리모델링", "정비사업", "시장", "정책", "공사비", "분양가",
        )
        arch_recent_posts = fill_posts(architecture_base, architecture_base, 5)
        arch_practical_posts = fill_posts(
            architecture_base.filter(practical_q).distinct(),
            architecture_base,
            4,
        )
        arch_tech_posts = fill_posts(
            architecture_base.filter(technology_q).distinct(),
            architecture_base,
            3,
        )
        arch_property_posts = fill_posts(
            construction_property_base.filter(property_q).distinct(),
            construction_property_base,
            6,
        )
        arch_video_posts = list(architecture_all.filter(video_q).distinct()[:3])

        # CBL_CONSTRUCTION_ARCH_PORTAL_CATEGORY_POOLS_START
        construction_all_base = Post.objects.filter(
            category__in=[
                "construction_work",
                "construction_tech",
                "construction_real",
                "bim",
                "architecture",
                "realestate",
            ],
            is_published=True,
        ).exclude(video_q).order_by("-created_at")

        construction_video_base = Post.objects.filter(
            category__in=[
                "construction_work",
                "construction_tech",
                "construction_real",
                "bim",
                "architecture",
                "realestate",
            ],
            is_published=True,
        ).filter(video_q).order_by("-created_at").distinct()

        construction_work_base = Post.objects.filter(is_published=True).filter(
            Q(category="construction_work")
            | (Q(category="architecture") & practical_q)
        ).exclude(video_q).order_by("-created_at").distinct()

        construction_tech_base = Post.objects.filter(is_published=True).filter(
            Q(category="construction_tech")
            | Q(category="bim")
            | (Q(category="architecture") & technology_q)
        ).exclude(video_q).order_by("-created_at").distinct()

        construction_real_base = Post.objects.filter(is_published=True).filter(
            Q(category="construction_real") | Q(category="realestate")
        ).exclude(video_q).order_by("-created_at").distinct()

        arch_recent_posts = cbl_posts_by_effective_categories(
            Post.objects.filter(is_published=True)
            .exclude(video_q)
            .order_by("-created_at"),
            ["construction_work", "construction_tech", "construction_real"],
            5,
        )

        arch_practical_posts = fill_posts(
            construction_work_base,
            construction_all_base.filter(practical_q).distinct(),
            4,
        )
        arch_tech_posts = fill_posts(
            construction_tech_base,
            construction_all_base.filter(technology_q).distinct(),
            3,
        )
        arch_property_posts = fill_posts(
            construction_real_base,
            construction_all_base.filter(property_q).distinct(),
            6,
        )
        arch_video_posts = list(construction_video_base[:3])
        # CBL_CONSTRUCTION_ARCH_PORTAL_CATEGORY_POOLS_END

        # CBL_ARCH_SECTION_POPUP_CONTEXT_START
        # 섹션별 전체보기 팝업용 목록입니다.
        # 4열 카드 팝업에서 16개 이상 늘어나도 내부 스크롤로 볼 수 있게 넉넉히 넘깁니다.
        try:
            construction_all_base
        except NameError:
            construction_all_base = Post.objects.filter(
                category__in=[
                    "construction_work",
                    "construction_tech",
                    "construction_real",
                    "architecture",
                    "realestate",
                ],
                is_published=True,
            ).order_by("-created_at")

        try:
            construction_work_base
        except NameError:
            construction_work_base = Post.objects.filter(
                category="construction_work",
                is_published=True,
            ).order_by("-created_at")

        try:
            construction_tech_base
        except NameError:
            construction_tech_base = Post.objects.filter(
                category="construction_tech",
                is_published=True,
            ).order_by("-created_at")

        try:
            construction_real_base
        except NameError:
            construction_real_base = Post.objects.filter(
                category="construction_real",
                is_published=True,
            ).order_by("-created_at")

        # 새 카테고리만 표시하고 기존 건축/부동산 글은 자동 보충하지 않습니다.
        construction_work_popup_base = construction_work_base
        construction_tech_popup_base = construction_tech_base
        construction_real_popup_base = construction_real_base

        def cbl_popup_posts(primary_qs, fallback_qs, limit=80):
            return fill_posts(primary_qs, fallback_qs, limit)

        def cbl_popup_videos(primary_qs, fallback_qs, limit=80):
            return fill_posts(primary_qs.distinct(), fallback_qs.distinct(), limit)

        arch_practical_all_posts = cbl_popup_posts(
            construction_work_popup_base,
            construction_all_base.filter(practical_q).distinct(),
            80,
        )
        arch_tech_all_posts = cbl_popup_posts(
            construction_tech_popup_base,
            construction_all_base.filter(technology_q).distinct(),
            80,
        )
        arch_property_all_posts = cbl_popup_posts(
            construction_real_popup_base,
            construction_all_base.filter(property_q).distinct(),
            80,
        )

        arch_practical_video_posts = cbl_popup_videos(
            construction_video_base.filter(category="construction_work"),
            construction_video_base.filter(practical_q),
            80,
        )
        arch_tech_video_posts = cbl_popup_videos(
            construction_video_base.filter(category__in=["construction_tech", "bim"]),
            construction_video_base.filter(technology_q),
            80,
        )
        arch_property_video_posts = cbl_popup_videos(
            construction_video_base.filter(category="construction_real"),
            construction_video_base.filter(property_q),
            80,
        )
        arch_all_video_posts = cbl_popup_videos(
            construction_video_base,
            construction_video_base,
            80,
        )

        # 메인 건설 동영상/쇼츠 섹션은 세부 카테고리 전체 영상/쇼츠에서 자동으로 채웁니다.
        arch_video_posts = list(construction_video_base[:3])
        # CBL_ARCH_SECTION_POPUP_CONTEXT_END

        context.update({
            "arch_recent_posts": arch_recent_posts,
            "arch_practical_posts": arch_practical_posts,
            "arch_tech_posts": arch_tech_posts,
            "arch_property_posts": arch_property_posts,
            "arch_video_posts": arch_video_posts,
            "arch_practical_all_posts": arch_practical_all_posts,
            "arch_tech_all_posts": arch_tech_all_posts,
            "arch_property_all_posts": arch_property_all_posts,
            "arch_practical_video_posts": arch_practical_video_posts,
            "arch_tech_video_posts": arch_tech_video_posts,
            "arch_property_video_posts": arch_property_video_posts,
            "arch_all_video_posts": arch_all_video_posts,
        })


    # CBL_TECH_DEVICE_ONLY_CONTEXT_START
    if slug == "tech":
        def cbl_tech_device_q():
            keywords = [
                "IT기기", "IT 기기", "노트북", "맥북", "맥북프로", "아이맥",
                "아이폰", "갤럭시", "스마트폰", "태블릿", "아이패드",
                "모니터", "키보드", "마우스", "컴퓨터", "PC", "윈도우",
                "맥", "장비", "디바이스", "트리플 모니터", "스마트워치",
            ]
            query = Q()
            for keyword in keywords:
                query |= Q(title__icontains=keyword)
                query |= Q(summary__icontains=keyword)
                query |= Q(content__icontains=keyword)
                query |= Q(tags__icontains=keyword)
            return query

        device_q = cbl_tech_device_q()

        tech_device_posts = list(
            Post.objects.filter(
                category="tech",
                is_published=True,
            ).filter(device_q).order_by("-created_at").distinct()[:6]
        )

        seen_device_ids = [item.pk for item in tech_device_posts]

        if len(tech_device_posts) < 6:
            tech_device_posts.extend(
                list(
                    Post.objects.filter(
                        category="tech",
                        is_published=True,
                    ).exclude(pk__in=seen_device_ids).order_by("-created_at")[: 6 - len(tech_device_posts)]
                )
            )

        context["tech_device_posts"] = tech_device_posts[:6]
    # CBL_TECH_DEVICE_ONLY_CONTEXT_END

    return render(request, "core/category.html", context)


def search(request):
    query = request.GET.get("q", "").strip()
    results = Post.objects.none()

    category_keywords = {
        "건설": "architecture",
        "BIM": "bim",
        "비아이엠": "bim",
        "프로그램": "program",
        "툴": "program",
        "BIM": "bim",
        "bim": "bim",
        "비아이엠": "bim",
        "프로그램": "program",
        "툴": "program",
        "건축": "architecture",
        "건설실무": "construction_work",
        "시공": "construction_work",
        "건설기술": "construction_tech",
        "BIM": "construction_tech",
        "건설부동산": "construction_real",
        "건설 부동산": "construction_real",
        "부동산": "construction_real",
        "금융": "finance",
        "테크": "tech",
        "기술": "tech",
        "일상": "life",
    }

    category_slug = category_keywords.get(query)

    if query:
        search_filter = (
            Q(title__icontains=query) |
            Q(content__icontains=query) |
            Q(category__icontains=query) |
            Q(tags__icontains=query)
        )

        if category_slug:
            search_filter = search_filter | Q(category=category_slug)

        post_field_names = get_post_field_names()

        if "summary" in post_field_names:
            search_filter = search_filter | Q(summary__icontains=query)

        if "meta_description" in post_field_names:
            search_filter = search_filter | Q(meta_description__icontains=query)

        results = Post.objects.filter(
            search_filter,
            is_published=True,
        ).order_by("-created_at")

    return render(request, "core/search.html", {
        "query": query,
        "results": results,
    })


def post_detail(request, pk):
    post = get_object_or_404(Post, pk=pk)

    if not post.is_published and not admin_required(request.user):
        raise Http404("존재하지 않는 글입니다.")

    if post.is_published and not is_internal_user(request.user):
        post.views += 1
        post.save(update_fields=["views"])
        notify_post_view(request, post)

    return render(
        request,
        "core/post_detail.html",
        get_post_detail_context(post),
    )


def post_detail_by_slug(request, slug):
    post = get_object_or_404(Post, slug=slug)

    if not post.is_published and not admin_required(request.user):
        raise Http404("존재하지 않는 글입니다.")

    if post.is_published and not is_internal_user(request.user):
        post.views += 1
        post.save(update_fields=["views"])
        notify_post_view(request, post)

    return render(
        request,
        "core/post_detail.html",
        get_post_detail_context(post),
    )


@login_required
@require_POST
def comment_create(request, pk):
    post = get_object_or_404(Post, pk=pk)

    if not post.is_published and not admin_required(request.user):
        raise Http404("존재하지 않는 글입니다.")

    form = CommentForm(request.POST)

    if form.is_valid():
        comment = form.save(commit=False)
        comment.post = post
        comment.author = request.user
        comment.save()

        messages.success(request, "댓글이 등록되었습니다.")
    else:
        error_message = "댓글 내용을 확인해주세요."

        if form.errors:
            first_errors = next(iter(form.errors.values()), None)
            if first_errors:
                error_message = str(first_errors[0])

        messages.error(request, error_message)

    return redirect(f"{post.get_absolute_url()}#comments")


@login_required
@require_POST
def comment_delete(request, comment_id):
    comment = get_object_or_404(
        Comment.objects.select_related("post", "author"),
        pk=comment_id,
    )

    post = comment.post

    can_delete = (
        comment.author_id == request.user.id
        or request.user.is_staff
        or request.user.is_superuser
    )

    if not can_delete:
        messages.error(request, "본인이 작성한 댓글만 삭제할 수 있습니다.")
        return redirect(f"{post.get_absolute_url()}#comments")

    comment.delete()
    messages.success(request, "댓글이 삭제되었습니다.")

    return redirect(f"{post.get_absolute_url()}#comments")


@user_passes_test(can_write_post)
def post_create(request):
    initial_category = cbl_normalize_editor_category(request.GET.get("category", ""))

    if request.method == "POST":
        form = PostForm(request.POST, request.FILES)

        if form.is_valid():
            post = form.save(commit=False)
            post.content = normalize_html_spaces(post.content)
            post.save()
            form.save_m2m()
            return redirect("post_detail", pk=post.pk)

        messages.error(request, "입력 내용을 확인해주세요.")

    else:
        form = PostForm(initial={
            "category": initial_category,
        })

    return render(
        request,
        "core/post_form.html",
        editor_context({
            "form": form,
            "mode": "create",
            "post": None,
        })
    )


@login_required
@user_passes_test(admin_required)
@require_POST
def video_post_upload(request):
    """관리 팝업에서 동영상 게시글을 별도로 등록합니다."""
    form_data = request.POST.copy()
    form_data["post_type"] = "video"
    if not (form_data.get("content") or "").strip():
        form_data["content"] = "<p>영상 설명이 아직 없습니다.</p>"

    # 체크되지 않은 공개 여부는 False로 저장되도록 ModelForm 입력을 그대로 사용합니다.
    form = PostForm(form_data, request.FILES)

    if not form.is_valid():
        errors = []
        for field_errors in form.errors.values():
            errors.extend(str(error) for error in field_errors)
        return JsonResponse({
            "ok": False,
            "error": errors[0] if errors else "입력 내용을 확인해주세요.",
            "errors": form.errors.get_json_data(),
        }, status=400)

    post = form.save(commit=False)
    post.post_type = "video"
    post.content = normalize_html_spaces(post.content or "")
    post.save()
    form.save_m2m()

    return JsonResponse({
        "ok": True,
        "post_id": post.pk,
        "redirect_url": post.get_absolute_url(),
    })


@user_passes_test(admin_required)
def post_update(request, pk):
    post = get_object_or_404(Post, pk=pk)

    old_thumbnail_name = post.thumbnail.name if post.thumbnail else ""
    old_program_file_name = post.program_file.name if post.program_file else ""
    old_video_file_name = post.video_file.name if post.video_file else ""

    if request.method == "POST":
        form = PostForm(request.POST, request.FILES, instance=post)

        if form.is_valid():
            post = form.save(commit=False)
            post.content = normalize_html_spaces(post.content)
            post.save()
            form.save_m2m()

            # 새 파일로 교체된 경우 기존 파일을 정리합니다.
            if request.FILES.get("thumbnail") and old_thumbnail_name != (post.thumbnail.name if post.thumbnail else ""):
                delete_file_safely(old_thumbnail_name)

            if request.FILES.get("program_file") and old_program_file_name != (post.program_file.name if post.program_file else ""):
                delete_file_safely(old_program_file_name)

            if request.FILES.get("video_file") and old_video_file_name != (post.video_file.name if post.video_file else ""):
                delete_file_safely(old_video_file_name)

            return redirect("post_detail", pk=post.pk)

        messages.error(request, "입력 내용을 확인해주세요.")

    else:
        form = PostForm(instance=post)

    return render(
        request,
        "core/post_form.html",
        editor_context({
            "form": form,
            "mode": "update",
            "post": post,
        })
    )


@user_passes_test(admin_required)
@require_POST
def post_delete(request, pk):
    post = get_object_or_404(Post, pk=pk)
    post.delete()
    messages.success(request, "글이 삭제되었습니다.")
    return redirect("admin_dashboard")


@user_passes_test(admin_required)
def post_publish(request, pk):
    post = get_object_or_404(Post, pk=pk)

    if request.method == "POST":
        post.is_published = True
        post.save(update_fields=["is_published", "updated_at"])
        messages.success(request, "글이 공개되었습니다.")

    return redirect("post_detail", pk=post.pk)


@user_passes_test(admin_required)
def post_unpublish(request, pk):
    post = get_object_or_404(Post, pk=pk)

    if request.method == "POST":
        post.is_published = False
        post.save(update_fields=["is_published", "updated_at"])
        messages.success(request, "글이 비공개 초안으로 변경되었습니다.")

    return redirect("post_detail", pk=post.pk)

def make_unique_english_slug(title, source_pk=None):
    """
    영어 제목을 검색 친화적인 slug 주소로 변환합니다.
    예: /post/slug/en-macbook-neo-price-release-date/
    """
    base_slug = slugify(str(title or ""), allow_unicode=False).strip("-")

    if not base_slug:
        base_slug = f"english-post-{source_pk or uuid.uuid4().hex[:8]}"

    base_slug = f"en-{base_slug}"[:180].strip("-")
    slug = base_slug
    number = 2

    while Post.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{number}"[:200].strip("-")
        number += 1

    return slug


@user_passes_test(admin_required)
@require_POST
def post_translate_english(request, pk):
    """
    기존 한국어 글을 영어 글로 자동 번역합니다.
    - 영어 글은 항상 비공개 초안
    - 썸네일 사진 / 본문 이미지 / 첨부 파일 / 위치 정보는 기존 글과 동일
    - 태그는 영어 SEO 태그로 자동 번역
    - 주소는 영어 SEO slug로 생성
    """
    source_post = get_object_or_404(Post, pk=pk)
    post_field_names = get_post_field_names()

    try:
        korean_ai_data = {
            "title": source_post.title,
            "summary": getattr(source_post, "summary", ""),
            "meta_description": getattr(source_post, "meta_description", ""),
            "thumbnail_text": getattr(source_post, "thumbnail_text", ""),
            "tags": getattr(source_post, "tags", ""),
            "content": getattr(source_post, "content", ""),
        }

        english_data = generate_english_ai_post(
            category=source_post.category,
            korean_ai_data=korean_ai_data,
            korean_final_content=source_post.content,
            source_keywords=source_post.tags or source_post.title,
            source_title=source_post.title,
        )

        english_title = str(english_data.get("title", "")).strip()
        if not english_title:
            english_title = f"{source_post.title} English Guide"

        english_content = str(english_data.get("content", "")).strip()
        english_content = normalize_html_spaces(english_content)
        english_content = validate_generated_content_or_raise(
            english_content,
            title=english_title,
            min_length=200,
        )

        english_tags = str(english_data.get("tags") or "").strip()

        # 영어 태그가 비어 있으면 한국어 태그를 그대로 쓰지 않고,
        # 해외 검색용 기본 영어 태그로 안전하게 저장합니다.
        if not english_tags:
            english_tags = "English guide,ChickenBanana Lab"

        with transaction.atomic():
            english_post = Post(
                category=source_post.category,
                title=english_title[:200],
                content=english_content,
                is_published=False,
                tags=english_tags,
            )

            # 썸네일 문구는 해외 독자용 영어 문구 사용
            if "thumbnail_text" in post_field_names:
                english_post.thumbnail_text = str(
                    english_data.get("thumbnail_text", "")
                ).strip()[:100]

            # 썸네일 사진은 기존 글과 동일
            if "thumbnail" in post_field_names and getattr(source_post, "thumbnail", None):
                english_post.thumbnail = source_post.thumbnail.name

            # 본문 대표 사진이 있으면 동일
            if "content_image" in post_field_names and getattr(source_post, "content_image", None):
                english_post.content_image = source_post.content_image.name

            # 위치 정보 동일
            if "location" in post_field_names:
                english_post.location = getattr(source_post, "location", "")

            # 동영상/프로그램 파일도 있으면 동일하게 연결
            if "video_file" in post_field_names and getattr(source_post, "video_file", None):
                english_post.video_file = source_post.video_file.name

            if "program_file" in post_field_names and getattr(source_post, "program_file", None):
                english_post.program_file = source_post.program_file.name

            # 영어 SEO 주소 생성
            if "slug" in post_field_names:
                english_post.slug = make_unique_english_slug(
                    english_title,
                    source_pk=source_post.pk,
                )

            english_post.save()

            # summary / meta_description / thumbnail_prompt 등이 있으면 저장
            set_post_optional_seo_fields(english_post, {
                "summary": english_data.get("summary", ""),
                "meta_description": english_data.get("meta_description", ""),
                "thumbnail_prompt": english_data.get("thumbnail_prompt", ""),
            })

        messages.success(
            request,
            f"영어 자동번역 초안이 생성되었습니다: {english_post.title}"
        )
        return redirect("post_update", pk=english_post.pk)

    except Exception as error:
        messages.error(request, f"영어 자동번역 중 오류가 발생했습니다: {error}")
        return redirect("admin_dashboard")

def about(request):
    return render(request, "core/about.html")


def contact(request):
    return render(request, "core/contact.html")


@user_passes_test(admin_required)
def admin_dashboard(request):
    posts = Post.objects.all().order_by("-created_at")

    published_count = Post.objects.filter(is_published=True).count()
    draft_count = Post.objects.filter(is_published=False).count()

    return render(request, "core/admin_dashboard.html", {
        "posts": posts,
        "published_count": published_count,
        "draft_count": draft_count,
    })


@user_passes_test(admin_required)
def experience_vault(request):
    vault, created = ExperienceVault.objects.get_or_create(pk=1)

    if request.method == "POST":
        form = ExperienceVaultForm(request.POST, instance=vault)

        if form.is_valid():
            form.save()
            messages.success(request, "경험창고가 저장되었습니다.")
            return redirect("experience_vault")

        messages.error(request, "경험창고 저장 중 오류가 발생했습니다. 입력 내용을 확인해주세요.")

    else:
        form = ExperienceVaultForm(instance=vault)

    return render(request, "core/experience_vault.html", {
        "form": form,
        "vault": vault,
    })


@user_passes_test(admin_required)
def site_stats(request):
    total_posts = Post.objects.count()
    published_posts = Post.objects.filter(is_published=True).count()
    draft_posts = Post.objects.filter(is_published=False).count()

    total_views = Post.objects.aggregate(total=Sum("views"))["total"] or 0

    program_file_count = Post.objects.exclude(program_file="").count()
    video_file_count = Post.objects.exclude(video_file="").count()

    category_stats = (
        Post.objects.values("category")
        .annotate(count=Count("id"), views=Sum("views"))
        .order_by("-count")
    )

    category_name_map = dict(Post.CATEGORY_CHOICES)

    category_stats_list = []

    for item in category_stats:
        category_stats_list.append({
            "category": item["category"],
            "category_name": category_name_map.get(item["category"], item["category"]),
            "count": item["count"],
            "views": item["views"] or 0,
        })

    top_posts = Post.objects.order_by("-views", "-created_at")[:10]
    recent_posts = Post.objects.order_by("-created_at")[:10]

    # 방문자 통계 기간 선택
    today = timezone.localdate()
    period = request.GET.get("period", "30")

    valid_periods = ["7", "30", "90", "all"]

    if period not in valid_periods:
        period = "30"

    visit_base_qs = VisitLog.objects.filter(is_bot=False)

    if period == "7":
        start_date = today - timedelta(days=6)
        period_label = "최근 7일"
    elif period == "30":
        start_date = today - timedelta(days=29)
        period_label = "최근 30일"
    elif period == "90":
        start_date = today - timedelta(days=89)
        period_label = "최근 90일"
    else:
        first_visit = visit_base_qs.aggregate(first=Min("created_at"))["first"]

        if first_visit:
            start_date = timezone.localtime(first_visit).date()
        else:
            start_date = today

        period_label = "전체 기간"

    daily_visit_qs = (
        visit_base_qs
        .filter(
            created_at__date__gte=start_date,
            created_at__date__lte=today,
        )
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            visits=Count("id"),
            visitors=Count("visitor_key", distinct=True),
        )
        .order_by("day")
    )

    daily_map = {
        item["day"]: {
            "visits": item["visits"],
            "visitors": item["visitors"],
        }
        for item in daily_visit_qs
    }

    visit_labels = []
    visit_counts = []
    visitor_counts = []
    daily_visit_rows = []

    total_days = (today - start_date).days + 1

    for i in range(total_days):
        day = start_date + timedelta(days=i)

        visits = daily_map.get(day, {}).get("visits", 0)
        visitors = daily_map.get(day, {}).get("visitors", 0)

        visit_labels.append(day.strftime("%m.%d"))
        visit_counts.append(visits)
        visitor_counts.append(visitors)

        daily_visit_rows.append({
            "date": day,
            "visits": visits,
            "visitors": visitors,
        })

    daily_visit_rows.reverse()

    today_visits = daily_map.get(today, {}).get("visits", 0)
    today_visitors = daily_map.get(today, {}).get("visitors", 0)

    total_visits_period = sum(visit_counts)
    total_visitors_period = (
        visit_base_qs
        .filter(
            created_at__date__gte=start_date,
            created_at__date__lte=today,
        )
        .values("visitor_key")
        .distinct()
        .count()
    )

    range_buttons = [
        {
            "value": "7",
            "label": "최근 7일",
        },
        {
            "value": "30",
            "label": "최근 30일",
        },
        {
            "value": "90",
            "label": "최근 90일",
        },
        {
            "value": "all",
            "label": "전체",
        },
    ]

    return render(request, "core/site_stats.html", {
        "total_posts": total_posts,
        "published_posts": published_posts,
        "draft_posts": draft_posts,
        "total_views": total_views,
        "program_file_count": program_file_count,
        "video_file_count": video_file_count,
        "category_stats": category_stats_list,
        "top_posts": top_posts,
        "recent_posts": recent_posts,

        # 방문자 그래프용 데이터
        "visit_labels": json.dumps(visit_labels, ensure_ascii=False),
        "visit_counts": json.dumps(visit_counts),
        "visitor_counts": json.dumps(visitor_counts),

        # 방문자 요약 데이터
        "today_visits": today_visits,
        "today_visitors": today_visitors,
        "total_visits_30": total_visits_period,
        "total_visitors_30": total_visitors_period,

        # 기간 선택 / 표 데이터
        "period": period,
        "period_label": period_label,
        "range_buttons": range_buttons,
        "daily_visit_rows": daily_visit_rows,
    })


@user_passes_test(admin_required)
def _cbl_original_ai_post_generate(request):
    if request.method != "POST":
        return redirect("admin_dashboard")

    category = request.POST.get("category", "construction_work")
    keywords = request.POST.get("keywords", "").strip()
    selected_keywords_raw = request.POST.get("selected_keywords", "").strip()
    selected_keywords = []

    if selected_keywords_raw:
        try:
            selected_keywords = json.loads(selected_keywords_raw)
        except json.JSONDecodeError:
            selected_keywords = []

    selected_keywords = [
        str(keyword).strip()
        for keyword in selected_keywords
        if str(keyword).strip()
    ][:10]

    if selected_keywords:
        keywords = ", ".join(selected_keywords)

    writing_style = request.POST.get("writing_style", "practical")
    extra_prompt = request.POST.get("extra_prompt", "").strip()

    experience_vault_text = ""

    try:
        vault = ExperienceVault.objects.filter(pk=1, is_active=True).first()

        if vault and vault.content.strip():
            experience_vault_text = vault.content.strip()[-12000:]

    except Exception:
        experience_vault_text = ""

    default_human_prompt = """
너무 AI처럼 딱딱하게 정리하지 말고, 사람이 개인 블로그에 직접 정리하듯이 자연스럽게 써줘.
글을 무조건 '핵심 기준 3가지', '체크리스트', 'FAQ' 같은 고정 구조로 만들지 말고, 글 흐름에 필요할 때만 넣어줘.
확인되지 않은 수치, 순위, 비교, 완료율, 우위 표현은 단정하지 말고 조심스럽게 표현해줘.
건축·부동산·건설 관련 주제는 실제 확인 기준과 주의할 점을 중심으로 풀어줘.
금융·세금·건강·법률 관련 주제는 단정적인 조언을 피하고 참고용 정보라는 뉘앙스를 유지해줘.
문장 길이를 다양하게 섞고, 같은 문장 끝 표현을 반복하지 말아줘.
첫 문단은 너무 뻔한 '최근 ~가 주목받고 있습니다'로 시작하지 말고, 사람이 실제로 이슈를 보고 느낀 관점에서 시작해줘.
이미지 설명 문구를 본문에 반복해서 넣지 말아줘.
"""

    if experience_vault_text:
        default_human_prompt += f"""

아래는 블로그 운영자가 직접 적어둔 경험창고 내용입니다.
글 주제와 관련 있는 부분만 자연스럽게 참고하세요.
관련 없는 내용은 억지로 넣지 마세요.
내용을 그대로 복사하지 말고, 운영자의 경험과 관점이 묻어나게 재해석하세요.

[경험창고]
{experience_vault_text}
"""

    extra_prompt = f"{default_human_prompt}\n\n{extra_prompt}".strip()

    try:
        count = int(request.POST.get("count", 1))
    except ValueError:
        count = 1

    count = max(1, min(count, 10))

    try:
        image_count = int(request.POST.get("image_count", 0))
    except ValueError:
        image_count = 0

    image_count = max(0, min(image_count, 5))

    make_thumbnail = request.POST.get("make_thumbnail") == "on"
    include_tags = request.POST.get("include_tags") == "on"
    save_draft = request.POST.get("save_draft") == "on"
    make_english_version = request.POST.get("make_english_version") == "on"

    if not keywords:
        messages.error(request, "주요 이슈 키워드를 입력해주세요. 직접 입력하거나 추천 키워드를 선택해주세요.")
        return redirect("admin_dashboard")

    created_posts = []
    created_index_urls = []

    try:
        first_keyword = keywords.split()[0] if keywords.split() else keywords

        if first_keyword:
            existing_titles = list(
                Post.objects.filter(title__icontains=first_keyword)
                .order_by("-created_at")
                .values_list("title", flat=True)[:20]
            )
        else:
            existing_titles = []

        if selected_keywords:
            count = len(selected_keywords)

            topics = [
                {
                    "title": keyword,
                    "keywords": keyword,
                    "angle": "선택한 추천 키워드 기준으로 글 작성",
                    "search_intent": "해당 키워드를 검색한 독자가 바로 이해할 수 있는 정보 탐색",
                    "extra_prompt": f"이 글은 반드시 '{keyword}' 키워드 하나에 집중해서 작성할 것",
                }
                for keyword in selected_keywords
            ]

        elif count > 1:
            topics = generate_post_topics(
                category=category,
                keywords=keywords,
                writing_style=writing_style,
                extra_prompt=extra_prompt,
                count=count,
                existing_titles=existing_titles,
            )

        else:
            topics = [
                {
                    "title": keywords,
                    "keywords": keywords,
                    "angle": extra_prompt,
                    "search_intent": "정보 탐색",
                    "extra_prompt": extra_prompt,
                }
            ]

        for index, topic in enumerate(topics, start=1):
            topic_title = (topic.get("title") or keywords).strip()
            topic_keywords = (topic.get("keywords") or topic_title or keywords).strip()
            topic_angle = (topic.get("angle") or "").strip()
            topic_search_intent = (topic.get("search_intent") or "").strip()
            topic_extra_prompt = (topic.get("extra_prompt") or "").strip()

            combined_extra_prompt = f"""
{extra_prompt}

이번 글 세부 기획:
- 세부 제목: {topic_title}
- 세부 키워드: {topic_keywords}
- 글 방향: {topic_angle}
- 검색 의도: {topic_search_intent}
- 추가 조건: {topic_extra_prompt}

글쓰기 톤:
- 사람이 직접 블로그에 쓰는 것처럼 자연스럽게 작성
- 너무 교과서식으로 정리하지 말고, 실제로 생각을 풀어내는 흐름으로 작성
- 첫 문단은 “최근 ~가 주목받고 있습니다”처럼 뻔하게 시작하지 말 것
- 문장 길이를 일부러 다양하게 섞을 것
- 짧은 문장, 긴 문장, 설명 문장을 자연스럽게 섞을 것
- “중요합니다”, “필요합니다”, “가능합니다” 같은 문장 끝 반복을 줄일 것
- 너무 완벽하게 정리된 느낌보다 사람이 직접 판단하고 설명하는 느낌을 줄 것
- 중간중간 “조금 더 현실적으로 보면”, “처음 보는 분들은”, “이 부분에서 헷갈리기 쉬운 점은” 같은 자연스러운 연결 문장을 사용할 것
- 단, 과한 감탄사나 광고 문구는 사용하지 말 것

내용 작성 규칙:
- 핵심 키워드는 자연스럽게 포함하되 반복하지 말 것
- 확인되지 않은 사실, 수치, 순위, 완료율, 비교 우위는 단정하지 말 것
- 기사나 공식 자료 확인이 필요한 내용은 “보도에 따르면”, “업계에서는”, “확인된 자료 기준으로는”처럼 조심스럽게 표현
- 실제 근거가 없는 경우 “~로 보입니다”, “~로 해석할 수 있습니다” 수준으로 작성
- 건축, 부동산, 금융, 건강, 법률 주제는 단정적인 조언을 피하고 주의 문구 포함
- 표, FAQ, 체크리스트는 매번 넣지 말고 글 흐름에 꼭 필요할 때만 사용
- FAQ를 넣더라도 1~2개 정도만 자연스럽게 넣을 것
- 소제목은 너무 딱딱한 보고서 제목보다 블로그식 문장형 제목으로 작성
- 본문에는 h2, h3, p, ul, li, strong 태그를 사용할 수 있음
- 이미지 설명 문구를 본문에 반복해서 넣지 말 것

경험창고 활용 규칙:
- 경험창고 내용은 글 주제와 관련 있을 때만 자연스럽게 반영
- 관련 없는 경험은 절대 억지로 넣지 말 것
- 경험창고 문장을 그대로 복사하지 말고 블로그 운영자의 관점처럼 재해석
- 경험창고에 있는 회사명, 현장명, 금액, 민감한 내용은 구체적으로 노출하지 말고 일반화해서 표현

사람 느낌을 살리는 방식:
- 글 앞부분에 이 이슈를 왜 보게 됐는지 짧게 설명
- 중간에는 단순 요약보다 실제 상황에서 어떤 의미인지 해석
- 마지막은 뻔한 결론보다 독자가 가져갈 관점으로 마무리
- 같은 표현을 반복하지 말고 문단마다 리듬을 다르게 구성
- 너무 완성된 보고서처럼 쓰지 말고, 블로그 운영자가 직접 정리한 글처럼 작성

중요:
- 이 세부 주제에서 벗어나지 말 것
- 같은 키워드의 다른 글과 제목, 도입부, 결론 구조가 비슷하지 않게 작성할 것
- 허위 정보나 확인되지 않은 비교 표현을 만들지 말 것
""".strip()

            ai_data = generate_ai_post(
                category=category,
                keywords=topic_keywords,
                writing_style=writing_style,
                extra_prompt=combined_extra_prompt,
                include_tags=include_tags,
                make_thumbnail=make_thumbnail,
                image_count=image_count,
                planned_title=topic_title,
            )

            content = ai_data.get("content", "")
            inline_image_blocks = []

            for image_index, image_data in enumerate(ai_data.get("content_images", []), start=1):
                image_prompt = (image_data.get("prompt") or "").strip()
                caption = (image_data.get("caption") or "").strip()

                if not image_prompt:
                    continue

                try:
                    image_url = save_inline_image(
                        prompt=image_prompt,
                        prefix=f"{category}-{index}-{image_index}",
                    )
                except Exception as error:
                    print("========== 본문 이미지 생성 실패 ==========")
                    print(error)
                    traceback.print_exc()
                    print("========================================")
                    image_url = ""

                if image_url:
                    inline_image_blocks.append({
                        "url": image_url,
                        "caption": caption,
                    })

            content = replace_image_placeholders(content, inline_image_blocks)
            content = normalize_html_spaces(content)
            content = validate_generated_content_or_raise(
                content,
                title=ai_data.get("title", topic_title),
                min_length=200,
            )

            post = Post.objects.create(
                category=category,
                title=ai_data.get("title", topic_title),
                thumbnail_text=ai_data.get("thumbnail_text", ""),
                content=content,
                tags=ai_data.get("tags", ""),
                is_published=not save_draft,
            )

            set_post_optional_seo_fields(post, ai_data)

            thumbnail_prompt = (ai_data.get("thumbnail_prompt") or "").strip()

            if make_thumbnail and thumbnail_prompt:
                try:
                    thumbnail_filename, thumbnail_file = make_generated_image_file(
                        prompt=thumbnail_prompt,
                        prefix=f"thumbnail-{post.pk}",
                    )

                    if thumbnail_filename and thumbnail_file:
                        post.thumbnail.save(
                            thumbnail_filename,
                            thumbnail_file,
                            save=True,
                        )
                except Exception as error:
                    print("========== 썸네일 이미지 생성 실패 ==========")
                    print(error)
                    traceback.print_exc()
                    print("==========================================")

            created_posts.append(post)
            created_index_urls.append(f"한글: {request.build_absolute_uri(post.get_absolute_url())}")

            if make_english_version:
                english_source_data = dict(ai_data)
                english_source_data["content"] = content

                english_ai_data = generate_english_ai_post(
                    category=category,
                    korean_ai_data=english_source_data,
                    korean_final_content=content,
                    source_keywords=topic_keywords,
                    source_title=post.title,
                )

                english_title = english_ai_data.get("title", f"{post.title} English Version")
                english_content = normalize_html_spaces(english_ai_data.get("content", ""))
                english_content = validate_generated_content_or_raise(
                    english_content,
                    title=english_title,
                    min_length=200,
                )

                english_create_kwargs = {
                    "category": category,
                    "title": english_title,
                    "thumbnail_text": english_ai_data.get("thumbnail_text", ""),
                    "content": english_content,
                    "tags": english_ai_data.get("tags", ""),
                    "is_published": not save_draft,
                }

                if "slug" in get_post_field_names():
                    english_create_kwargs["slug"] = make_unique_english_slug(
                        english_title,
                        source_pk=post.pk,
                    )

                english_post = Post.objects.create(**english_create_kwargs)

                set_post_optional_seo_fields(english_post, english_ai_data)

                # 영어 글은 한글 글과 같은 대표 썸네일 파일을 사용합니다.
                if post.thumbnail:
                    english_post.thumbnail = post.thumbnail
                    english_post.save(update_fields=["thumbnail", "updated_at"])

                created_posts.append(english_post)
                created_index_urls.append(f"영어: {request.build_absolute_uri(english_post.get_absolute_url())}")

    except Exception as error:
        print("========== AI 글 생성 오류 ==========")
        print(error)
        traceback.print_exc()
        print("===================================")

        messages.error(request, f"AI 글 생성 중 오류가 발생했습니다: {error}")
        return redirect("admin_dashboard")

    if make_english_version:
        index_url_text = " | ".join(created_index_urls)
        messages.success(
            request,
            f"AI 글 {len(created_posts)}개를 생성했습니다. 구글 서치콘솔 색인요청 주소: {index_url_text}"
        )
        return redirect("admin_dashboard")

    if len(created_posts) == 1:
        return redirect("post_detail", pk=created_posts[0].pk)

    if created_index_urls:
        index_url_text = " | ".join(created_index_urls)
        messages.success(
            request,
            f"AI 글 {len(created_posts)}개를 생성했습니다. 색인요청 주소: {index_url_text}"
        )
    else:
        messages.success(request, f"AI 글 {len(created_posts)}개를 생성했습니다.")

    return redirect("admin_dashboard")


# CBL_AI_GENERATE_DRAFT_RESULT_HOTFIX_START
# 목적:
# 1) 비공개 초안 저장 선택 시, AI 생성글이 공개로 저장되는 문제 방지
# 2) 실제 글은 생성됐는데 응답 JSON 때문에 "실패"로 표시되는 문제 보정
import json as _cbl_ai_json
import threading as _cbl_ai_threading
from django.db.models.signals import pre_save as _cbl_ai_pre_save

try:
    from .models import Post as _cbl_ai_Post
except Exception:
    _cbl_ai_Post = Post

_cbl_ai_generate_local = _cbl_ai_threading.local()


def _cbl_ai_str(v):
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    return str(v)


def _cbl_ai_truthy(v):
    s = _cbl_ai_str(v).strip().lower()
    return s in {
        "1", "true", "on", "yes", "y", "checked",
        "draft", "private", "비공개", "초안", "비공개초안"
    }


def _cbl_ai_falsey(v):
    s = _cbl_ai_str(v).strip().lower()
    return s in {
        "0", "false", "off", "no", "n", "none", "null", "",
        "draft", "private", "비공개", "초안", "비공개초안"
    }


def _cbl_ai_collect_request_values(request):
    values = {}

    def add(k, v):
        if k is None:
            return
        key = _cbl_ai_str(k).strip().lower()
        if not key:
            return
        values.setdefault(key, []).append(v)

    try:
        for k in request.GET.keys():
            for v in request.GET.getlist(k):
                add(k, v)
    except Exception:
        pass

    try:
        for k in request.POST.keys():
            for v in request.POST.getlist(k):
                add(k, v)
    except Exception:
        pass

    try:
        raw = getattr(request, "body", b"")
        if raw:
            text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
            text = text.strip()
            if text.startswith("{") and text.endswith("}"):
                payload = _cbl_ai_json.loads(text)
                if isinstance(payload, dict):
                    for k, v in payload.items():
                        if isinstance(v, (list, tuple)):
                            for item in v:
                                add(k, item)
                        else:
                            add(k, v)
    except Exception:
        pass

    return values


def _cbl_ai_force_draft_requested(request):
    values = _cbl_ai_collect_request_values(request)

    # 초안/비공개 계열 값이 명시적으로 들어오면 무조건 비공개
    draft_key_tokens = (
        "draft",
        "private",
        "비공개",
        "초안",
        "save_as_draft",
        "is_draft",
        "private_draft",
    )

    for key, vals in values.items():
        if any(token in key for token in draft_key_tokens):
            if any(_cbl_ai_truthy(v) for v in vals):
                return True

    # 공개 여부 값이 false/private/draft 로 들어오면 비공개
    publish_key_tokens = (
        "publish",
        "published",
        "is_published",
        "publish_immediately",
        "auto_publish",
    )

    for key, vals in values.items():
        if any(token in key for token in publish_key_tokens):
            if vals and any(_cbl_ai_falsey(v) for v in vals):
                return True

    # status / visibility 값 보정
    for key in ("status", "visibility", "post_status"):
        vals = values.get(key, [])
        for v in vals:
            s = _cbl_ai_str(v).strip().lower()
            if s in {"draft", "private", "비공개", "초안"}:
                return True

    return False


def _cbl_ai_force_draft_presave(sender, instance, **kwargs):
    try:
        if getattr(_cbl_ai_generate_local, "force_draft", False):
            if hasattr(instance, "is_published"):
                instance.is_published = False
    except Exception:
        pass


try:
    _cbl_ai_pre_save.connect(
        _cbl_ai_force_draft_presave,
        sender=_cbl_ai_Post,
        dispatch_uid="cbl_ai_generate_force_draft_presave",
        weak=False,
    )
except Exception:
    pass


def _cbl_ai_count_created_from_data(data):
    if not isinstance(data, dict):
        return 0

    for key in ("created_count", "success_count", "created_posts_count"):
        try:
            n = int(data.get(key) or 0)
            if n > 0:
                return n
        except Exception:
            pass

    for key in ("created_posts", "posts", "created", "created_items", "results", "items"):
        value = data.get(key)
        if isinstance(value, list):
            count = 0
            for item in value:
                if not isinstance(item, dict):
                    count += 1
                    continue

                status = _cbl_ai_str(
                    item.get("status")
                    or item.get("result")
                    or item.get("state")
                    or item.get("message")
                ).lower()

                if (
                    item.get("post_id")
                    or item.get("id")
                    or item.get("url")
                    or item.get("detail_url")
                    or item.get("edit_url")
                    or "성공" in status
                    or "완료" in status
                    or status in {"success", "ok", "created"}
                ):
                    count += 1

            if count > 0:
                return count

    if data.get("post_id") or data.get("id") or data.get("url") or data.get("detail_url"):
        return 1

    return 0


def _cbl_ai_normalize_response(response, db_created_count=0, force_draft=False):
    try:
        content_type = response.get("Content-Type", "")
        if "application/json" not in content_type:
            return response

        raw = response.content.decode("utf-8", errors="ignore")
        data = _cbl_ai_json.loads(raw)

        if not isinstance(data, dict):
            return response

        created_count = int(db_created_count or 0)
        if created_count <= 0:
            created_count = _cbl_ai_count_created_from_data(data)

        # 실제 DB에 글이 생겼거나 응답 안에 생성 근거가 있으면 성공으로 보정
        if created_count > 0:
            data["success"] = True
            data["ok"] = True
            data["created_count"] = created_count
            data["success_count"] = created_count

            # 글이 실제 생성된 경우에는 UI 실패 카운트를 0으로 보정
            data["failed_count"] = 0
            data["error_count"] = 0

            if force_draft:
                data["is_published"] = False
                data["publish_immediately"] = False
                data["status"] = data.get("status") or "draft"

            if created_count == 1:
                data["message"] = data.get("message") or "AI 글 생성 완료"
            else:
                data["message"] = data.get("message") or f"AI 글 {created_count}개 생성 완료"

            new_content = _cbl_ai_json.dumps(data, ensure_ascii=False).encode("utf-8")
            response.content = new_content
            response["Content-Length"] = str(len(new_content))

    except Exception:
        return response

    return response


def ai_post_generate(request, *args, **kwargs):
    # CBL_FORCE_SELECTED_CATEGORY_FOR_AI_POST_START
    try:
        if getattr(request, "method", "").upper() == "POST":
            _cbl_category_alias = {
                "건축": "architecture",
                "건설": "architecture",
        "BIM": "bim",
        "비아이엠": "bim",
        "프로그램": "program",
        "툴": "program",
        "BIM": "bim",
        "bim": "bim",
        "비아이엠": "bim",
        "프로그램": "program",
        "툴": "program",
                "architecture": "architecture",

                "부동산": "realestate",
                "realestate": "realestate",
                "real_estate": "realestate",

                "금융": "finance",
                "경제": "finance",
                "finance": "finance",

                "테크": "tech",
                "기술": "tech",
                "IT": "tech",
                "it": "tech",
                "tech": "tech",

                "일상": "life",
                "라이프": "life",
                "life": "life",
            }
            _cbl_valid_categories = {"architecture", "realestate", "finance", "tech", "life"}

            _cbl_post = request.POST.copy()

            _cbl_force_category = (
                _cbl_post.get("cbl_force_category")
                or _cbl_post.get("auto_category")
                or _cbl_post.get("selected_category")
                or _cbl_post.get("post_category")
                or ""
            )

            if not _cbl_force_category:
                _cbl_keywords = (
                    _cbl_post.getlist("auto_keywords[]")
                    or _cbl_post.getlist("auto_keywords")
                    or _cbl_post.getlist("keywords[]")
                    or _cbl_post.getlist("keywords")
                )
                _cbl_categories = (
                    _cbl_post.getlist("auto_categories[]")
                    or _cbl_post.getlist("auto_categories")
                    or _cbl_post.getlist("categories[]")
                    or _cbl_post.getlist("categories")
                )
                _cbl_current_keyword = (
                    _cbl_post.get("keyword")
                    or _cbl_post.get("title")
                    or _cbl_post.get("post_title")
                    or ""
                ).strip()

                if _cbl_current_keyword and _cbl_keywords and _cbl_categories:
                    for _idx, _kw in enumerate(_cbl_keywords):
                        if str(_kw).strip() == _cbl_current_keyword and _idx < len(_cbl_categories):
                            _cbl_force_category = _cbl_categories[_idx]
                            break

            _cbl_force_category = _cbl_category_alias.get(
                str(_cbl_force_category).strip(),
                str(_cbl_force_category).strip()
            )

            if _cbl_force_category in _cbl_valid_categories:
                for _name in (
                    "category",
                    "post_category",
                    "post_category_slug",
                    "selected_category",
                    "ai_category",
                    "auto_category",
                    "cbl_locked_category",
                ):
                    _cbl_post[_name] = _cbl_force_category
                request.POST = _cbl_post
    except Exception as _cbl_category_lock_error:
        print("CBL category lock skipped:", _cbl_category_lock_error)
    # CBL_FORCE_SELECTED_CATEGORY_FOR_AI_POST_END
    force_draft = _cbl_ai_force_draft_requested(request)

    before_max_id = 0
    try:
        before_max_id = _cbl_ai_Post.objects.order_by("-id").values_list("id", flat=True).first() or 0
    except Exception:
        before_max_id = 0

    old_force = getattr(_cbl_ai_generate_local, "force_draft", False)
    _cbl_ai_generate_local.force_draft = bool(old_force or force_draft)

    try:
        response = _cbl_original_ai_post_generate(request, *args, **kwargs)
    finally:
        _cbl_ai_generate_local.force_draft = old_force

    db_created_count = 0

    try:
        new_posts = _cbl_ai_Post.objects.filter(id__gt=before_max_id)
        db_created_count = new_posts.count()

        # 혹시 기존 저장 로직에서 공개로 저장했더라도 최종적으로 초안 처리
        if force_draft and db_created_count > 0:
            new_posts.update(is_published=False)
    except Exception:
        db_created_count = 0

    return _cbl_ai_normalize_response(
        response,
        db_created_count=db_created_count,
        force_draft=force_draft,
    )
# CBL_AI_GENERATE_DRAFT_RESULT_HOTFIX_END


@user_passes_test(admin_required)
def ai_keyword_recommend(request):
    if request.method != "POST":
        return JsonResponse({
            "ok": False,
            "message": "POST 요청만 가능합니다.",
        }, status=405)

    category = request.POST.get("category", "construction_work")

    try:
        keywords = recommend_keywords_from_news(category)

        return JsonResponse({
            "ok": True,
            "keywords": keywords,
        })

    except Exception as error:
        return JsonResponse({
            "ok": False,
            "message": str(error),
        }, status=500)


def signup(request):
    ref_username = (request.GET.get("ref") or request.POST.get("ref") or "").strip()

    if request.method == "POST":
        form = CblSignupForm(request.POST)

        if form.is_valid():
            user = form.save()

            UserProfile.objects.get_or_create(user=user)

            notify_signup(request, user)

            auth_login(
                request,
                user,
                backend="django.contrib.auth.backends.ModelBackend"
            )

            return redirect("profile_setup")

    else:
        initial = {}
        if ref_username:
            initial["referrer_username"] = ref_username
        form = CblSignupForm(initial=initial)

    return render(request, "core/signup.html", {
        "form": form,
        "interest_choices": INTEREST_CHOICES,
        "ref_username": ref_username,
    })


@login_required
def profile_setup(request):
    profile, created = UserProfile.objects.get_or_create(user=request.user)

    if profile.nickname:
        return redirect("home")

    if request.method == "POST":
        form = NicknameForm(request.POST, instance=profile)

        if form.is_valid():
            form.save()
            messages.success(request, "닉네임이 저장되었습니다.")
            return redirect("home")

    else:
        form = NicknameForm(instance=profile)

    return render(request, "core/profile_setup.html", {
        "form": form,
        "profile": profile,
    })


@login_required
@require_POST
def profile_update(request):
    profile, created = UserProfile.objects.get_or_create(user=request.user)
    form = NicknameForm(request.POST, instance=profile)

    if form.is_valid():
        form.save()
        messages.success(request, "닉네임이 변경되었습니다.")
    else:
        messages.error(request, "닉네임을 확인해주세요.")

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    return redirect(next_url)


@user_passes_test(admin_required)
def member_manage(request):
    users = (
        User.objects
        .select_related("profile", "profile__referred_by", "profile__referred_by__profile")
        .order_by("-date_joined")
    )

    return render(request, "core/member_manage.html", {
        "users": users,
    })


@user_passes_test(admin_required)
@require_POST
def member_role_update(request, user_id):
    target_user = get_object_or_404(User, pk=user_id)

    if target_user.is_superuser:
        messages.error(request, "최고 관리자는 권한을 변경할 수 없습니다.")
        return redirect("member_manage")

    profile, created = UserProfile.objects.get_or_create(user=target_user)

    profile.is_sub_admin = request.POST.get("is_sub_admin") == "on"
    profile.save(update_fields=["is_sub_admin", "updated_at"])

    messages.success(request, "회원 권한이 변경되었습니다.")
    return redirect("member_manage")


@user_passes_test(admin_required)
@require_POST
def member_delete(request, user_id):
    target_user = get_object_or_404(User, pk=user_id)

    if target_user.is_superuser:
        messages.error(request, "최고 관리자는 삭제할 수 없습니다.")
        return redirect("member_manage")

    if target_user == request.user:
        messages.error(request, "현재 로그인한 본인 계정은 삭제할 수 없습니다.")
        return redirect("member_manage")

    target_user.delete()
    messages.success(request, "회원이 삭제되었습니다.")
    return redirect("member_manage")


def robots_txt(request):
    content = """User-agent: *
Allow: /
Disallow: /admin/
Disallow: /accounts/

Sitemap: https://www.chickenbananalab.com/sitemap.xml

DaumWebMasterTool:cd4a4e7da3d5aba2064ce10dd8a01c5bbac84b05c26920b56559c9c84f5c6c57:bQ6JPGlq9DdzxfuBfgOS7A==
"""
    return HttpResponse(content, content_type="text/plain")


@login_required
@require_POST
def editor_image_upload(request):
    image = request.FILES.get("image")

    if not image:
        return JsonResponse({
            "success": False,
            "error": "이미지 파일이 없습니다.",
        }, status=400)

    allowed_types = [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    ]

    if image.content_type not in allowed_types:
        return JsonResponse({
            "success": False,
            "error": "jpg, png, webp, gif 이미지만 업로드할 수 있습니다.",
        }, status=400)

    max_size = 50 * 1024 * 1024

    if image.size > max_size:
        return JsonResponse({
            "success": False,
            "error": "이미지 용량은 최대 50MB까지 업로드할 수 있습니다.",
        }, status=400)

    ext = os.path.splitext(image.name)[1].lower()

    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
        ext = ".jpg"

    filename = f"{uuid.uuid4().hex}{ext}"
    save_path = f"editor/{filename}"

    path = default_storage.save(save_path, ContentFile(image.read()))
    image_url = default_storage.url(path)

    return JsonResponse({
        "success": True,
        "url": image_url,
    })


def terms(request):
    return render(request, "core/terms.html")


def privacy(request):
    return render(request, "core/privacy.html")

AI_AUTO_CATEGORY_ORDER = [
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


def get_enabled_ai_auto_categories(setting):
    categories = []

    if getattr(setting, "use_architecture", True):
        categories.append(("construction_work", "건설실무"))

    if getattr(setting, "use_construction_tech", True):
        categories.append(("construction_tech", "건설기술"))

    if getattr(setting, "use_realestate", True):
        categories.append(("construction_real", "건설부동산"))

    if getattr(setting, "use_bim", True):
        categories.append(("bim", "REVIT/BIM"))

    if getattr(setting, "use_dynamo_automation", True):
        categories.append(("dynamo_automation", "Dynamo/자동화"))

    if getattr(setting, "use_four_d_five_d", True):
        categories.append(("four_d_five_d", "4D/5D"))

    if getattr(setting, "use_tech_ai_development", True):
        categories.append(("tech_ai_development", "AI·개발"))

    if getattr(setting, "use_tech_data_security", True):
        categories.append(("tech_data_security", "데이터·보안"))

    if getattr(setting, "use_tech_server_software", True):
        categories.append(("tech_server_software", "인터넷·서버·소프트"))

    if getattr(setting, "use_program", True):
        categories.append(("program", "업무용 프로그램"))

    if getattr(setting, "use_tool_recommend", True):
        categories.append(("tool_recommend", "툴소개/툴추천"))

    return categories


def refill_ai_auto_keyword_queue(setting):
    """오늘 뉴스 추천을 카테고리 전체 기준으로 중복 제거해 대기열에 저장합니다."""
    try:
        keyword_count = int(setting.keyword_count_per_category or 5)
    except (TypeError, ValueError):
        keyword_count = 5
    keyword_count = max(1, min(keyword_count, 5))
    enabled_categories = get_enabled_ai_auto_categories(setting)
    if not enabled_categories:
        raise ValueError("사용할 카테고리를 1개 이상 선택해주세요.")

    recommended_by_category = {}
    globally_accepted = []
    for category, category_label in enabled_categories:
        category_items = []
        for raw_item in recommend_keywords_from_news(category) or []:
            candidate = unpack_recommendation(raw_item, category_label)
            if not candidate["keyword"] or is_duplicate_candidate(candidate, globally_accepted):
                continue
            candidate["news_context"] = build_news_context(candidate)
            category_items.append(candidate)
            globally_accepted.append(candidate)
            if len(category_items) >= keyword_count:
                break
        recommended_by_category[category] = category_items

    with transaction.atomic():
        AIAutoKeywordQueue.objects.filter(status="waiting").delete()
        created_count, order = 0, 1
        for keyword_index in range(keyword_count):
            for category, _label in enabled_categories:
                items = recommended_by_category.get(category, [])
                if keyword_index >= len(items):
                    continue
                item = items[keyword_index]
                AIAutoKeywordQueue.objects.create(category=category, keyword=item["keyword"], reason=item.get("reason", ""), news_context=item.get("news_context", ""), status="waiting", order=order)
                created_count += 1
                order += 1
    return created_count
@user_passes_test(admin_required)
def ai_auto_writer_manage(request):
    setting = AIAutoWriterSetting.load()

    if request.method == "POST":
        action = request.POST.get("ai_auto_action", "save")

        try:
            interval_minutes = int(request.POST.get("interval_minutes", 30))
        except ValueError:
            interval_minutes = 30

        if interval_minutes not in [10, 30, 60, 120]:
            interval_minutes = 30

        try:
            keyword_count_per_category = int(request.POST.get("keyword_count_per_category", 7))
        except ValueError:
            keyword_count_per_category = 7

        keyword_count_per_category = max(1, min(keyword_count_per_category, 7))

        try:
            daily_limit = int(request.POST.get("daily_limit", 30))
        except ValueError:
            daily_limit = 30

        daily_limit = max(1, min(daily_limit, 144))

        setting.interval_minutes = interval_minutes
        setting.keyword_count_per_category = keyword_count_per_category
        setting.daily_limit = daily_limit
        setting.make_thumbnail = request.POST.get("make_thumbnail") == "on"
        setting.include_tags = request.POST.get("include_tags") == "on"
        save_draft = request.POST.get("save_draft") == "on"
        setting.publish_immediately = not save_draft
        setting.use_architecture = bool(request.POST.get("use_construction_work") or request.POST.get("use_architecture"))
        setting.use_construction_tech = bool(request.POST.get("use_construction_tech"))
        setting.use_realestate = bool(request.POST.get("use_construction_real") or request.POST.get("use_realestate"))

        if hasattr(setting, "use_bim"):
            setting.use_bim = bool(request.POST.get("use_bim"))
        if hasattr(setting, "use_dynamo_automation"):
            setting.use_dynamo_automation = bool(request.POST.get("use_dynamo_automation"))
        if hasattr(setting, "use_four_d_five_d"):
            setting.use_four_d_five_d = bool(request.POST.get("use_four_d_five_d"))
        if hasattr(setting, "use_tech_ai_development"):
            setting.use_tech_ai_development = bool(request.POST.get("use_tech_ai_development"))
        if hasattr(setting, "use_tech_data_security"):
            setting.use_tech_data_security = bool(request.POST.get("use_tech_data_security"))
        if hasattr(setting, "use_tech_server_software"):
            setting.use_tech_server_software = bool(request.POST.get("use_tech_server_software"))
        if hasattr(setting, "use_program"):
            setting.use_program = bool(request.POST.get("use_program"))
        if hasattr(setting, "use_tool_recommend"):
            setting.use_tool_recommend = bool(request.POST.get("use_tool_recommend"))

        # 기존 필드는 더 이상 시간별 자동글 기준으로 쓰지 않음
        setting.use_finance = False
        setting.use_tech = False
        setting.use_life = False
        setting.make_thumbnail = request.POST.get("make_thumbnail") == "on"
        setting.include_tags = request.POST.get("include_tags") == "on"

        save_draft = request.POST.get("save_draft") == "on"
        setting.publish_immediately = not save_draft

        try:
            image_count = int(request.POST.get("image_count", 0))
        except ValueError:
            image_count = 0

        setting.image_count = max(0, min(image_count, 5))

        if action == "keywords":
            setting.save()

            try:
                created_count = refill_ai_auto_keyword_queue(setting)
                messages.success(
                    request,
                    f"오늘의 추천키워드 {created_count}개를 대기열에 저장했습니다."
                )
            except Exception as error:
                messages.error(
                    request,
                    f"오늘의 추천키워드 가져오기 중 오류가 발생했습니다: {error}"
                )

            return redirect("ai_auto_writer_manage")

        if action == "start":
            setting.is_enabled = True
            setting.next_run_at = timezone.now() + timedelta(minutes=setting.interval_minutes)
            setting.save()

            messages.success(
                request,
                f"AI 자동글 생성을 시작했습니다. {setting.interval_minutes}분마다 1개씩 생성됩니다."
            )

            return redirect("ai_auto_writer_manage")

        if action == "stop":
            setting.is_enabled = False
            setting.next_run_at = None
            setting.save()

            messages.success(request, "AI 자동글 생성을 중지했습니다.")
            return redirect("ai_auto_writer_manage")

        setting.save()
        messages.success(request, "AI 자동글 생성 설정을 저장했습니다.")
        return redirect("ai_auto_writer_manage")

    context = {
    "ai_auto_setting": setting,
    "ai_auto_waiting_count": AIAutoKeywordQueue.objects.filter(status="waiting").count(),
    "ai_auto_done_count": AIAutoKeywordQueue.objects.filter(status="done").count(),
    "ai_auto_failed_count": AIAutoKeywordQueue.objects.filter(status="failed").count(),
    "ai_auto_queue_items": AIAutoKeywordQueue.objects.filter(status="waiting").order_by("order", "created_at")[:50],
}

    return render(request, "core/ai_auto_writer_manage.html", context)
from .shorts_maker import make_shorts_for_post

def shorts_admin_required(user):
    return user.is_authenticated and user.is_staff


@user_passes_test(shorts_admin_required)
def post_generate_shorts(request, post_id):
    post = get_object_or_404(Post, pk=post_id)

    if request.method != "POST":
        return redirect("/dashboard/")

    try:
        post.shorts_status = "processing"
        post.shorts_error = ""
        post.save(update_fields=["shorts_status", "shorts_error"])

        result = make_shorts_for_post(post)

        if isinstance(result, dict):
            video_path = result.get("video") or ""
            cover_path = result.get("cover") or ""
        else:
            video_path = str(result) if result else ""
            cover_path = ""

        if not video_path:
            raise RuntimeError("생성된 쇼츠 영상 경로가 비어 있습니다.")

        post.shorts_video.name = video_path

        update_fields = [
            "shorts_video",
            "shorts_status",
            "shorts_error",
            "shorts_created_at",
        ]

        if hasattr(post, "shorts_cover") and cover_path:
            post.shorts_cover.name = cover_path
            update_fields.append("shorts_cover")

        post.shorts_status = "done"
        post.shorts_error = ""
        post.shorts_created_at = timezone.now()
        post.save(update_fields=update_fields)

        messages.success(request, "쇼츠 영상 생성이 완료되었습니다.")

    except Exception as e:
        post.shorts_status = "failed"

        try:
            err_msg = str(e)
        except Exception:
            err_msg = repr(e)

        post.shorts_error = err_msg[:2000]
        post.save(update_fields=["shorts_status", "shorts_error"])

        messages.error(request, f"쇼츠 영상 생성 실패: {err_msg[:300]}")

    return redirect("/dashboard/")


# ============================================================
# CBL_MINI_CAPCUT_V1
# 미니 CapCut 스타일 쇼츠 편집기
# ============================================================
def _mini_capcut_admin_required(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)

def _mini_capcut_seed_state_from_post_short_video(post):
    """
    기존 쇼츠 영상 파일이 있으면 미니 CapCut 편집기에
    영상 1개짜리 프로젝트로 자동 불러오기 위한 초기 데이터.
    실제 필드명이 달라도 short/video/render/output/generated 계열 FileField를 자동 탐색한다.
    """
    candidates = []

    for field in getattr(post, "_meta").fields:
        name = getattr(field, "name", "")
        lower = name.lower()
        value = getattr(post, name, None)

        if not value:
            continue

        try:
            url = value.url
        except Exception:
            continue

        if not url:
            continue

        score = 0

        if "short" in lower:
            score += 100
        if "render" in lower or "output" in lower or "generated" in lower:
            score += 60
        if "video" in lower:
            score += 30

        if score <= 0:
            continue

        candidates.append((score, name, url))

    if not candidates:
        return {}

    candidates.sort(reverse=True)
    _, field_name, url = candidates[0]

    asset_id = uuid.uuid4().hex
    clip_id = uuid.uuid4().hex

    return {
        "assets": [
            {
                "id": asset_id,
                "name": "기존 쇼츠 영상",
                "url": url,
                "type": "video",
                "sourceField": field_name,
            }
        ],
        "clips": [
            {
                "id": clip_id,
                "assetId": asset_id,
                "type": "video",
                "name": "기존 쇼츠 영상",
                "url": url,
                "start": 0,
                "duration": 15,
                "speed": 1,
                "volume": 1,
                "transition": "none",
                "text": "",
            }
        ],
        "selectedClipId": clip_id,
    }


from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.utils import timezone
import json
import uuid
import os
from .cbl_category_policy import (
    CBL_AI_CATEGORY_GUIDE,
    CBL_CATEGORY_LABELS,
    CBL_PUBLIC_CATEGORY_CHOICES,
    CBL_PUBLIC_CATEGORY_CODE_SET,
    cbl_resolve_auto_post_category,
)


logger = logging.getLogger(__name__)


@login_required
@user_passes_test(_mini_capcut_admin_required)
def mini_capcut_home(request):
    from .models import Post, MiniCapcutProject
    from django.db.models import Exists, OuterRef, Subquery

    latest_project = MiniCapcutProject.objects.filter(post=OuterRef("pk")).order_by("-updated_at")

    posts = (
        Post.objects
        .annotate(
            has_mini_capcut_project=Exists(latest_project),
            latest_mini_capcut_project_id=Subquery(latest_project.values("id")[:1]),
        )
        .order_by("-created_at")[:80]
    )

    projects = MiniCapcutProject.objects.select_related("post").order_by("-updated_at")[:30]

    return render(
        request,
        "core/mini_capcut_editor.html",
        {
            "post": None,
            "project": None,
            "posts": posts,
            "projects": projects,
            "initial_state": {},
            "editor_meta": {
                "postId": None,
                "projectId": None,
            },
        },
    )


@login_required
@user_passes_test(_mini_capcut_admin_required)
def mini_capcut_editor(request, post_id):
    from .models import Post, MiniCapcutProject

    post = get_object_or_404(Post, id=post_id)
    project = MiniCapcutProject.objects.filter(post=post).order_by("-updated_at").first()

    if project and isinstance(project.data, dict):
        initial_state = project.data
    else:
        initial_state = _mini_capcut_seed_state_from_post_short_video(post)

    return render(
        request,
        "core/mini_capcut_editor.html",
        {
            "post": post,
            "project": project,
            "posts": None,
            "projects": None,
            "initial_state": initial_state,
            "editor_meta": {
                "postId": post.id,
                "projectId": project.id if project else None,
            },
        },
    )


@login_required
@user_passes_test(_mini_capcut_admin_required)
@require_POST
def mini_capcut_upload(request):
    allowed_exts = {
        ".mp4", ".mov", ".m4v", ".webm",
        ".jpg", ".jpeg", ".png", ".webp", ".gif",
        ".mp3", ".wav", ".m4a", ".aac", ".ogg",
    }

    uploaded = []
    today = timezone.now().strftime("%Y%m%d")

    for f in request.FILES.getlist("files"):
        original_name = f.name
        ext = os.path.splitext(original_name)[1].lower()

        if ext not in allowed_exts:
            continue

        content_type = getattr(f, "content_type", "") or ""

        if content_type.startswith("video/") or ext in [".mp4", ".mov", ".m4v", ".webm"]:
            asset_type = "video"
        elif content_type.startswith("audio/") or ext in [".mp3", ".wav", ".m4a", ".aac", ".ogg"]:
            asset_type = "audio"
        else:
            asset_type = "image"

        safe_name = f"{uuid.uuid4().hex}{ext}"
        rel_path = f"mini_capcut/{today}/{safe_name}"
        saved_path = default_storage.save(rel_path, ContentFile(f.read()))

        uploaded.append(
            {
                "id": uuid.uuid4().hex,
                "name": original_name,
                "url": settings.MEDIA_URL + saved_path,
                "type": asset_type,
                "size": f.size,
            }
        )

    return JsonResponse({"ok": True, "files": uploaded})


@login_required
@user_passes_test(_mini_capcut_admin_required)
@require_POST
def mini_capcut_save(request):
    from .models import Post, MiniCapcutProject

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "잘못된 저장 데이터입니다."}, status=400)

    post_id = payload.get("post_id")
    project_id = payload.get("project_id")
    title = payload.get("title") or "미니 CapCut 프로젝트"
    data = payload.get("data") or {}

    post = None
    if post_id:
        post = get_object_or_404(Post, id=post_id)

    if project_id:
        project = get_object_or_404(MiniCapcutProject, id=project_id)
    else:
        project = MiniCapcutProject(post=post)

    project.post = post
    project.title = title
    project.data = data
    project.save()

    return JsonResponse(
        {
            "ok": True,
            "project_id": project.id,
            "message": "프로젝트가 저장되었습니다.",
        }
    )


@login_required
@user_passes_test(_mini_capcut_admin_required)
@require_POST
def mini_capcut_export(request):
    """
    1차 버전에서는 편집 프로젝트 저장까지만 담당.
    다음 단계에서 ffmpeg 기반 MP4 렌더링을 이 함수에 연결한다.
    """
    return JsonResponse(
        {
            "ok": True,
            "message": "1차 버전은 프로젝트 저장까지 완료됩니다. 다음 단계에서 MP4 렌더링을 연결합니다.",
        }
    )


# ===== CBL_MULTILANG_AI_GENERATE_PATCH_START =====
# 다국어 자동글 생성 패치
# - 기존 ai_post_generate 함수를 삭제하지 않고 여기서 같은 이름으로 다시 정의하여 덮어씁니다.
# - DB 마이그레이션 없이 작동합니다.
# - 나중에 Post.language 필드를 추가하면 자동으로 저장되도록 안전 처리했습니다.

CBL_TARGET_LANGUAGE_LABELS = {
    "ko": "한국어",
    "en": "영어",
    "zh": "중국어",
    "ar": "아랍어",
    "ja": "일본어",
}

CBL_TARGET_LANGUAGE_NAMES = {
    "ko": "Korean",
    "en": "English",
    "zh": "Simplified Chinese",
    "ar": "Modern Standard Arabic",
    "ja": "Japanese",
}


def cbl_get_selected_languages(request):
    allowed = ["ko", "en", "zh", "ar", "ja"]

    selected = [
        lang.strip()
        for lang in request.POST.getlist("target_languages")
        if lang.strip() in allowed
    ]

    if not selected:
        selected = ["ko"]

    # 중복 제거, 순서 유지
    result = []
    for lang in selected:
        if lang not in result:
            result.append(lang)

    return result


def cbl_split_keywords_from_request(request):
    keyword_list = []

    # 새 UI: keywords[] 여러 개
    for value in request.POST.getlist("keywords[]"):
        value = str(value or "").strip()
        if value:
            keyword_list.append(value)

    # 일부 브라우저/템플릿에서 name="keywords"로 들어오는 경우
    if not keyword_list:
        raw_keywords = str(request.POST.get("keywords", "") or "").strip()

        if raw_keywords:
            # 줄바꿈 우선, 없으면 쉼표 기준 분리
            raw_keywords = raw_keywords.replace("\r", "\n")
            pieces = []

            for line in raw_keywords.split("\n"):
                if "," in line:
                    pieces.extend(line.split(","))
                else:
                    pieces.append(line)

            keyword_list = [piece.strip() for piece in pieces if piece.strip()]

    # 기존 오늘자 키워드 추천: selected_keywords JSON
    selected_keywords_raw = str(request.POST.get("selected_keywords", "") or "").strip()

    if selected_keywords_raw:
        try:
            selected_keywords = json.loads(selected_keywords_raw)
        except json.JSONDecodeError:
            selected_keywords = []

        if isinstance(selected_keywords, list):
            selected_cleaned = [
                str(keyword).strip()
                for keyword in selected_keywords
                if str(keyword).strip()
            ]

            if selected_cleaned:
                keyword_list = selected_cleaned

    # 중복 제거, 최대 20개
    result = []
    seen = set()

    for keyword in keyword_list:
        key = keyword.replace(" ", "").lower()

        if not key or key in seen:
            continue

        result.append(keyword)
        seen.add(key)

        if len(result) >= 20:
            break

    return result


def cbl_build_language_prompt(language, keyword, base_extra_prompt):
    language_name = CBL_TARGET_LANGUAGE_NAMES.get(language, "Korean")

    if language == "ko":
        lang_rule = """
이번 글은 한국어로 작성하세요.

언어 규칙:
- 제목, 요약, 본문, FAQ, 태그를 모두 자연스러운 한국어로 작성하세요.
- 한국 독자가 검색해서 읽는 블로그 글처럼 작성하세요.
- 본문 최상단에 h1 태그는 절대 사용하지 마세요.
""".strip()

    elif language == "en":
        lang_rule = """
Write the entire article in natural English for international readers.

Language rules:
- Title, summary, meta description, body, FAQ, and tags must be written in English.
- Use clear and beginner-friendly English.
- Start with a direct answer within the first two paragraphs.
- Use H2 and H3 headings.
- Add practical examples where useful.
- Do not mention that the article was written by AI.
- Do not use Korean except for proper nouns that need Korean context.
- Never use an h1 tag at the top of the body.
""".strip()

    elif language == "zh":
        lang_rule = """
请用简体中文撰写整篇文章，面向海外读者。

语言规则：
- 标题、摘要、SEO说明、正文、FAQ和标签都必须使用简体中文。
- 语言要自然、清晰，适合初学者阅读。
- 前两段要直接回答搜索者的问题。
- 使用 h2、h3、p、ul、li 等 HTML 标签。
- 不要说明文章由 AI 生成。
- 正文最上方绝对不要使用 h1 标签。
""".strip()

    elif language == "ar":
        lang_rule = """
اكتب المقال بالكامل باللغة العربية الفصحى الحديثة للقراء العرب.

قواعد اللغة:
- يجب أن يكون العنوان والملخص ووصف SEO والمحتوى والأسئلة الشائعة والوسوم باللغة العربية.
- استخدم أسلوبًا واضحًا ومفيدًا ومناسبًا للمبتدئين.
- ابدأ بإجابة مباشرة خلال أول فقرتين.
- استخدم عناوين h2 و h3 عند الحاجة.
- أضف أمثلة عملية عند الحاجة.
- لا تذكر أن المقال تمت كتابته بواسطة الذكاء الاصطناعي.
- لا تستخدم اللغة الكورية إلا عند الحاجة لأسماء الأماكن أو المصطلحات.
- لا تستخدم وسم h1 في أعلى المحتوى.
""".strip()

    elif language == "ja":
        lang_rule = """
この記事全体を自然な日本語で作成してください。

言語ルール:
- タイトル、要約、SEO説明、本文、FAQ、タグはすべて日本語で書いてください。
- 初心者にもわかりやすい自然な文章にしてください。
- 最初の2段落で検索者の疑問に直接答えてください。
- 必要に応じて h2、h3、p、ul、li タグを使ってください。
- AIが作成した文章であることは書かないでください。
- 本文の最上部に h1 タグは絶対に使わないでください。
""".strip()

    else:
        lang_rule = f"Write the entire article in {language_name}."

    return f"""
{lang_rule}

이번 생성 대상 키워드:
{keyword}

추가 요청사항:
{base_extra_prompt}
""".strip()


def cbl_make_unique_language_slug(title, language):
    if language == "ko":
        return ""

    base_slug = slugify(str(title or ""), allow_unicode=False).strip("-")

    if not base_slug:
        base_slug = f"{language}-post-{uuid.uuid4().hex[:10]}"

    base_slug = f"{language}-{base_slug}"[:180].strip("-")
    slug = base_slug
    number = 2

    while Post.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{number}"[:200].strip("-")
        number += 1

    return slug


@user_passes_test(admin_required)
def ai_post_generate(request):
    if request.method != "POST":
        return redirect("admin_dashboard")

    category = request.POST.get("category", "construction_work")
    writing_style = request.POST.get("writing_style", "practical")
    extra_prompt_input = request.POST.get("extra_prompt", "").strip()

    keyword_list = cbl_split_keywords_from_request(request)
    target_languages = cbl_get_selected_languages(request)

    if not keyword_list:
        messages.error(request, "주요 이슈 키워드를 1개 이상 입력해주세요.")
        return redirect("admin_dashboard")

    total_count = len(keyword_list) * len(target_languages)

    if total_count > 30:
        messages.error(
            request,
            f"한 번에 생성할 글이 너무 많습니다. 현재 {total_count}개입니다. 30개 이하로 줄여주세요."
        )
        return redirect("admin_dashboard")

    try:
        image_count = int(request.POST.get("image_count", 0))
    except ValueError:
        image_count = 0

    image_count = max(0, min(image_count, 5))
    canonical_category, category_diagnostics = cbl_resolve_auto_post_category(
        category,
        title=" ".join(keyword_list),
        summary=extra_prompt_input,
    )
    logger.info(
        "auto_post_category_before_save raw_category=%r normalized_before=%r "
        "canonical_category=%r legacy_mapping_used=%s fallback_reason=%s",
        category,
        category_diagnostics.get("normalized_before"),
        canonical_category,
        category_diagnostics.get("legacy_mapping_used"),
        category_diagnostics.get("fallback_reason") or "",
    )
    if canonical_category is None:
        logger.error(
            "auto_post_category_rejected raw_category=%r fallback_reason=%s",
            category, category_diagnostics.get("fallback_reason"),
        )
        messages.error(request, "자동글 카테고리를 현재 허용 목록으로 분류하지 못했습니다.")
        return redirect("admin_dashboard")
    category = canonical_category


    make_thumbnail = request.POST.get("make_thumbnail") == "on"
    include_tags = request.POST.get("include_tags") == "on"
    save_draft = request.POST.get("save_draft") == "on"

    experience_vault_text = ""

    try:
        vault = ExperienceVault.objects.filter(pk=1, is_active=True).first()

        if vault and vault.content.strip():
            experience_vault_text = vault.content.strip()[-12000:]

    except Exception:
        experience_vault_text = ""

    default_human_prompt = """
너무 AI처럼 딱딱하게 정리하지 말고, 사람이 개인 블로그에 직접 정리하듯이 자연스럽게 써줘.
확인되지 않은 수치, 순위, 비교, 완료율, 우위 표현은 단정하지 말고 조심스럽게 표현해줘.
건축·부동산·건설 관련 주제는 실제 확인 기준과 주의할 점을 중심으로 풀어줘.
금융·세금·건강·법률 관련 주제는 단정적인 조언을 피하고 참고용 정보라는 뉘앙스를 유지해줘.
문장 길이를 다양하게 섞고, 같은 문장 끝 표현을 반복하지 말아줘.
본문에는 h2, h3, p, ul, li, strong 태그를 사용할 수 있음.
본문 최상단에 h1 태그는 절대 사용하지 말 것.
이미지 설명 문구를 본문에 반복해서 넣지 말 것.
""".strip()

    if experience_vault_text:
        default_human_prompt += f"""

아래는 블로그 운영자가 직접 적어둔 경험창고 내용입니다.
글 주제와 관련 있는 부분만 자연스럽게 참고하세요.
관련 없는 내용은 억지로 넣지 마세요.
내용을 그대로 복사하지 말고, 운영자의 경험과 관점이 묻어나게 재해석하세요.

[경험창고]
{experience_vault_text}
"""

    base_extra_prompt = f"{default_human_prompt}\n\n{extra_prompt_input}".strip()

    created_posts = []
    created_index_urls = []

    try:
        for keyword_index, keyword in enumerate(keyword_list, start=1):
            for language in target_languages:
                language_label = CBL_TARGET_LANGUAGE_LABELS.get(language, language)
                language_prompt = cbl_build_language_prompt(
                    language=language,
                    keyword=keyword,
                    base_extra_prompt=base_extra_prompt,
                )

                ai_data = generate_ai_post(
                    category=category,
                    keywords=keyword,
                    writing_style=writing_style,
                    extra_prompt=language_prompt,
                    include_tags=include_tags,
                    make_thumbnail=make_thumbnail,
                    image_count=image_count,
                    planned_title=keyword,
                )

                content = ai_data.get("content", "")
                inline_image_blocks = []

                for image_index, image_data in enumerate(ai_data.get("content_images", []), start=1):
                    image_prompt = (image_data.get("prompt") or "").strip()
                    caption = (image_data.get("caption") or "").strip()

                    if not image_prompt:
                        continue

                    try:
                        image_url = save_inline_image(
                            prompt=image_prompt,
                            prefix=f"{category}-{language}-{keyword_index}-{image_index}",
                        )
                    except Exception as error:
                        print("========== 본문 이미지 생성 실패 ==========")
                        print(error)
                        traceback.print_exc()
                        print("========================================")
                        image_url = ""

                    if image_url:
                        inline_image_blocks.append({
                            "url": image_url,
                            "caption": caption,
                        })

                content = replace_image_placeholders(content, inline_image_blocks)
                content = normalize_html_spaces(content)
                content = validate_generated_content_or_raise(
                    content,
                    title=ai_data.get("title", keyword),
                    min_length=200,
                )

                post_create_kwargs = {
                    "category": category,
                    "title": ai_data.get("title", keyword),
                    "thumbnail_text": ai_data.get("thumbnail_text", ""),
                    "content": content,
                    "tags": ai_data.get("tags", "") if include_tags else "",
                    "is_published": not save_draft,
                }

                post_field_names = get_post_field_names()

                # 추후 Post.language 필드를 추가하면 자동 저장됨
                if "language" in post_field_names:
                    post_create_kwargs["language"] = language

                if language != "ko" and "slug" in post_field_names:
                    language_slug = cbl_make_unique_language_slug(
                        ai_data.get("title", keyword),
                        language,
                    )

                    if language_slug:
                        post_create_kwargs["slug"] = language_slug

                post = Post.objects.create(**post_create_kwargs)
                set_post_optional_seo_fields(post, ai_data)

                thumbnail_prompt = (ai_data.get("thumbnail_prompt") or "").strip()

                if make_thumbnail and thumbnail_prompt:
                    try:
                        thumbnail_filename, thumbnail_file = make_generated_image_file(
                            prompt=thumbnail_prompt,
                            prefix=f"thumbnail-{language}-{post.pk}",
                        )

                        if thumbnail_filename and thumbnail_file:
                            post.thumbnail.save(
                                thumbnail_filename,
                                thumbnail_file,
                                save=True,
                            )
                    except Exception as error:
                        print("========== 썸네일 이미지 생성 실패 ==========")
                        print(error)
                        traceback.print_exc()
                        print("==========================================")

                created_posts.append(post)
                created_index_urls.append(
                    f"{language_label}: {request.build_absolute_uri(post.get_absolute_url())}"
                )

    except Exception as error:
        print("========== AI 다국어 글 생성 오류 ==========")
        print(error)
        traceback.print_exc()
        print("=========================================")

        messages.error(request, f"AI 글 생성 중 오류가 발생했습니다: {error}")
        # CBL_AJAX_AI_ERROR_PATCH
        if request.headers.get("X-CBL-Sequential-AI") == "1" or request.POST.get("cbl_sequential_generate") == "1":
            return JsonResponse({"ok": False, "error": str(locals().get("e", "AI 글 생성 오류"))}, status=500)
        return redirect("admin_dashboard")

    if len(created_posts) == 1:
        return redirect("post_detail", pk=created_posts[0].pk)

    index_url_text = " | ".join(created_index_urls)
    messages.success(
        request,
        f"AI 글 {len(created_posts)}개를 생성했습니다. 색인요청 주소: {index_url_text}"
    )

    return redirect("admin_dashboard")
# ===== CBL_MULTILANG_AI_GENERATE_PATCH_END =====


# CBL_AI_FORCE_DRAFT_AND_RESULT_V2_START
# 목적:
# AI 자동/수동 생성 결과는 안전하게 일단 무조건 비공개 초안으로 저장한다.
# 또한 실제 DB에 글이 생성됐으면 응답 JSON의 실패 표시를 생성 개수 기준으로 보정한다.
try:
    import json as _cbl_v2_json
    from django.http import JsonResponse as _cbl_v2_JsonResponse

    _cbl_prev_ai_post_generate_v2 = ai_post_generate

    def _cbl_v2_get_post_model():
        try:
            from .models import Post
            return Post
        except Exception:
            return None

    def _cbl_v2_count_requested_items(request):
        count = 0

        def scan_value(value):
            nonlocal count
            if value is None:
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    scan_value(item)
                return

            s = str(value).lower()
            # 언어 선택값 카운트
            tokens = []
            for sep in [",", "|", ";", " "]:
                if sep in s:
                    tokens = [x.strip() for x in s.replace("|", ",").replace(";", ",").replace(" ", ",").split(",")]
                    break
            if not tokens:
                tokens = [s.strip()]

            for t in tokens:
                if t in {"ko", "kr", "korean", "한국어", "en", "english", "영어", "ja", "jp", "japanese", "일본어", "zh", "chinese", "중국어"}:
                    count += 1

        try:
            for key in request.POST.keys():
                lk = str(key).lower()
                if "lang" in lk or "language" in lk or "selected" in lk:
                    for value in request.POST.getlist(key):
                        scan_value(value)
        except Exception:
            pass

        try:
            raw = getattr(request, "body", b"")
            if raw:
                text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
                if text.strip().startswith("{"):
                    data = _cbl_v2_json.loads(text)
                    if isinstance(data, dict):
                        for key, value in data.items():
                            lk = str(key).lower()
                            if "lang" in lk or "language" in lk or "selected" in lk:
                                scan_value(value)
        except Exception:
            pass

        return count or 0

    def _cbl_v2_normalize_json_response(response, created_count, requested_count):
        try:
            content_type = response.get("Content-Type", "")
            if "application/json" not in content_type:
                return response

            raw = response.content.decode("utf-8", errors="ignore")
            data = _cbl_v2_json.loads(raw)
            if not isinstance(data, dict):
                return response

            if created_count > 0:
                failed_count = 0
                if requested_count and requested_count > created_count:
                    failed_count = requested_count - created_count

                data["success"] = True
                data["ok"] = True
                data["created_count"] = created_count
                data["success_count"] = created_count
                data["failed_count"] = failed_count
                data["error_count"] = failed_count
                data["is_published"] = False
                data["publish_immediately"] = False
                data["status"] = "draft"

                if failed_count > 0:
                    data["message"] = f"일부 생성 완료: 성공 {created_count}개, 실패 {failed_count}개가 있습니다."
                else:
                    data["message"] = f"AI 글 {created_count}개 생성 완료"

                new_content = _cbl_v2_json.dumps(data, ensure_ascii=False).encode("utf-8")
                response.content = new_content
                response["Content-Length"] = str(len(new_content))

            return response
        except Exception:
            return response

    def ai_post_generate(request, *args, **kwargs):
        Post = _cbl_v2_get_post_model()

        before_max_id = 0
        requested_count = _cbl_v2_count_requested_items(request)

        try:
            if Post is not None:
                before_max_id = Post.objects.order_by("-id").values_list("id", flat=True).first() or 0
        except Exception:
            before_max_id = 0

        response = _cbl_prev_ai_post_generate_v2(request, *args, **kwargs)

        created_count = 0

        try:
            if Post is not None:
                qs = Post.objects.filter(id__gt=before_max_id)
                created_count = qs.count()

                # 가장 중요한 부분: AI 생성 직후 무조건 비공개 초안 처리
                if created_count > 0:
                    qs.update(is_published=False)
        except Exception as e:
            print("CBL v2 force draft update error:", e)

        return _cbl_v2_normalize_json_response(response, created_count, requested_count)

except Exception as _cbl_force_draft_v2_error:
    print("CBL_AI_FORCE_DRAFT_AND_RESULT_V2 patch load error:", _cbl_force_draft_v2_error)
# CBL_AI_FORCE_DRAFT_AND_RESULT_V2_END


# CBL_AI_POPUP_RESULT_FINAL_FIX_V3_START
# 목적:
# 실제 DB에 AI 글이 생성됐는데 팝업에서 "실패 n개"로 잘못 표시되는 문제 최종 보정.
# 기준을 프론트의 추정 카운트가 아니라 DB에 새로 생성된 Post 개수로 잡는다.
try:
    import json as _cbl_popup_v3_json

    _cbl_prev_ai_post_generate_popup_v3 = ai_post_generate

    def _cbl_popup_v3_get_post_model():
        try:
            from .models import Post
            return Post
        except Exception:
            return None

    def _cbl_popup_v3_normalize_response(response, created_count):
        try:
            content_type = response.get("Content-Type", "")
            if "application/json" not in content_type:
                return response

            raw = response.content.decode("utf-8", errors="ignore")
            data = _cbl_popup_v3_json.loads(raw)

            if not isinstance(data, dict):
                return response

            # DB에 글이 실제로 생성됐으면 팝업은 성공 기준으로 보정
            if created_count > 0:
                data["success"] = True
                data["ok"] = True
                data["created_count"] = created_count
                data["success_count"] = created_count

                # 기존 잘못된 실패 카운트 제거
                data["failed_count"] = 0
                data["error_count"] = 0
                data["failed_items"] = []
                data["errors"] = []

                # 초안 상태 명시
                data["is_published"] = False
                data["publish_immediately"] = False
                data["status"] = "draft"

                if created_count == 1:
                    data["message"] = "AI 글 1개 생성 완료"
                else:
                    data["message"] = f"AI 글 {created_count}개 생성 완료"

                new_content = _cbl_popup_v3_json.dumps(data, ensure_ascii=False).encode("utf-8")
                response.content = new_content
                response["Content-Length"] = str(len(new_content))

            return response
        except Exception as e:
            print("CBL AI popup result v3 normalize error:", e)
            return response

    def ai_post_generate(request, *args, **kwargs):
        Post = _cbl_popup_v3_get_post_model()

        before_max_id = 0
        try:
            if Post is not None:
                before_max_id = Post.objects.order_by("-id").values_list("id", flat=True).first() or 0
        except Exception:
            before_max_id = 0

        response = _cbl_prev_ai_post_generate_popup_v3(request, *args, **kwargs)

        created_count = 0
        try:
            if Post is not None:
                qs = Post.objects.filter(id__gt=before_max_id)
                created_count = qs.count()

                # 혹시라도 공개로 저장된 경우 최종적으로 초안 잠금
                if created_count > 0:
                    qs.update(is_published=False)
        except Exception as e:
            print("CBL AI popup result v3 draft update error:", e)

        return _cbl_popup_v3_normalize_response(response, created_count)

except Exception as _cbl_popup_v3_error:
    print("CBL_AI_POPUP_RESULT_FINAL_FIX_V3 load error:", _cbl_popup_v3_error)
# CBL_AI_POPUP_RESULT_FINAL_FIX_V3_END


# CBL_AI_REDIRECT_TO_JSON_SUCCESS_V4_START
# 목적:
# /ai-post/generate/ 가 글 생성 후 302 redirect(/post/id/)를 반환하면
# 팝업 JS가 실패로 오판한다.
# 실제 DB에 글이 생성된 경우 redirect/html 응답을 JSON 성공 응답으로 변환한다.
try:
    from django.http import JsonResponse as _cbl_v4_JsonResponse
    import json as _cbl_v4_json

    _cbl_prev_ai_post_generate_v4 = ai_post_generate

    def _cbl_v4_get_post_model():
        try:
            from .models import Post
            return Post
        except Exception:
            return None

    def _cbl_v4_build_success_payload(posts):
        items = []

        for post in posts:
            item = {
                "id": getattr(post, "id", None),
                "post_id": getattr(post, "id", None),
                "title": getattr(post, "title", ""),
                "is_published": False,
                "status": "draft",
            }

            try:
                if hasattr(post, "get_absolute_url"):
                    item["url"] = post.get_absolute_url()
                    item["detail_url"] = post.get_absolute_url()
                else:
                    item["url"] = f"/post/{post.id}/"
                    item["detail_url"] = f"/post/{post.id}/"
            except Exception:
                try:
                    item["url"] = f"/post/{post.id}/"
                    item["detail_url"] = f"/post/{post.id}/"
                except Exception:
                    pass

            items.append(item)

        created_count = len(items)

        return {
            "success": True,
            "ok": True,
            "created_count": created_count,
            "success_count": created_count,
            "failed_count": 0,
            "error_count": 0,
            "failed_items": [],
            "errors": [],
            "created_posts": items,
            "posts": items,
            "is_published": False,
            "publish_immediately": False,
            "status": "draft",
            "message": "AI 글 1개 생성 완료" if created_count == 1 else f"AI 글 {created_count}개 생성 완료",
        }

    def _cbl_v4_json_response_from_posts(posts):
        payload = _cbl_v4_build_success_payload(posts)
        return _cbl_v4_JsonResponse(payload, json_dumps_params={"ensure_ascii": False})

    def _cbl_v4_normalize_existing_json_response(response, posts):
        try:
            content_type = response.get("Content-Type", "")
            if "application/json" not in content_type:
                return None

            raw = response.content.decode("utf-8", errors="ignore")
            data = _cbl_v4_json.loads(raw)

            if not isinstance(data, dict):
                return None

            created_count = len(posts)

            if created_count > 0:
                data["success"] = True
                data["ok"] = True
                data["created_count"] = created_count
                data["success_count"] = created_count
                data["failed_count"] = 0
                data["error_count"] = 0
                data["failed_items"] = []
                data["errors"] = []
                data["is_published"] = False
                data["publish_immediately"] = False
                data["status"] = "draft"
                data["message"] = "AI 글 1개 생성 완료" if created_count == 1 else f"AI 글 {created_count}개 생성 완료"

                new_content = _cbl_v4_json.dumps(data, ensure_ascii=False).encode("utf-8")
                response.content = new_content
                response["Content-Length"] = str(len(new_content))
                return response

            return response
        except Exception:
            return None

    def ai_post_generate(request, *args, **kwargs):
        Post = _cbl_v4_get_post_model()

        before_max_id = 0
        try:
            if Post is not None:
                before_max_id = Post.objects.order_by("-id").values_list("id", flat=True).first() or 0
        except Exception:
            before_max_id = 0

        response = _cbl_prev_ai_post_generate_v4(request, *args, **kwargs)

        posts = []
        try:
            if Post is not None:
                qs = Post.objects.filter(id__gt=before_max_id).order_by("id")
                posts = list(qs)

                # 생성된 글은 최종적으로 무조건 비공개 초안
                if posts:
                    qs.update(is_published=False)

                    # update 후 객체 값도 보정
                    for post in posts:
                        try:
                            post.is_published = False
                        except Exception:
                            pass
        except Exception as e:
            print("CBL AI redirect json v4 post check error:", e)
            posts = []

        # 실제 글이 생성됐으면 응답 형태와 상관없이 성공 JSON으로 반환
        if posts:
            normalized = _cbl_v4_normalize_existing_json_response(response, posts)
            if normalized is not None:
                return normalized

            # 핵심: 302 redirect 또는 HTML 응답이면 팝업용 JSON 성공 응답으로 변환
            return _cbl_v4_json_response_from_posts(posts)

        return response

except Exception as _cbl_v4_error:
    print("CBL_AI_REDIRECT_TO_JSON_SUCCESS_V4 load error:", _cbl_v4_error)
# CBL_AI_REDIRECT_TO_JSON_SUCCESS_V4_END


# CBL_POST_DETAIL_REDIRECT_START
def post_detail_redirect(request, pk=None, post_id=None, id=None):
    from django.shortcuts import get_object_or_404, redirect
    from core.models import Post

    post_pk = pk or post_id or id
    post = get_object_or_404(Post, pk=post_pk)

    if getattr(post, "slug", None):
        return redirect("post_detail_slug", slug=post.slug, permanent=True)

    return post_detail(request, post.pk)
# CBL_POST_DETAIL_REDIRECT_END


# CBL_AI_KEYWORD_RECOMMEND_CATEGORY_LOCK_VIEW_START
try:
    _cbl_prev_ai_keyword_recommend = ai_keyword_recommend

    def _cbl_read_keyword_request_payload(request):
        import json
        data = {}

        try:
            if request.body:
                data = json.loads(request.body.decode("utf-8"))
        except Exception:
            data = {}

        return data if isinstance(data, dict) else {}

    def _cbl_detect_keyword_category(request, payload=None):
        payload = payload or {}

        candidates = [
            payload.get("category"),
            payload.get("selected_category"),
            payload.get("ai_category"),
            payload.get("post_category"),
            request.POST.get("category"),
            request.POST.get("selected_category"),
            request.GET.get("category"),
            request.GET.get("selected_category"),
        ]

        for c in candidates:
            if c:
                return str(c).strip()

        return ""

    def _cbl_keyword_text_from_item(item):
        if isinstance(item, dict):
            return (
                item.get("keyword")
                or item.get("title")
                or item.get("text")
                or item.get("name")
                or ""
            )
        return str(item or "")

    def _cbl_keyword_category_from_item(item, fallback_category=""):
        if isinstance(item, dict):
            return (
                item.get("category")
                or item.get("category_slug")
                or item.get("post_category")
                or item.get("selected_category")
                or fallback_category
                or ""
            )
        return fallback_category or ""

    def _cbl_filter_keyword_items(items, fallback_category="", limit=7):
        from core.ai_writer import (
            cbl_filter_today_keywords_by_category,
            cbl_today_keyword_category_profile,
        )

        if not isinstance(items, list):
            return items

        # dict 리스트: [{"category": "...", "keyword": "..."}] 구조 대응
        if items and isinstance(items[0], dict):
            cleaned = []
            counters = {}

            for item in items:
                category = _cbl_keyword_category_from_item(item, fallback_category)
                keyword = _cbl_keyword_text_from_item(item)

                if not keyword:
                    continue

                filtered = cbl_filter_today_keywords_by_category(
                    category,
                    [keyword],
                    1,
                )

                if not filtered:
                    continue

                key = str(category or fallback_category or "").strip()
                counters[key] = counters.get(key, 0) + 1

                new_item = dict(item)
                if "keyword" in new_item:
                    new_item["keyword"] = filtered[0]
                elif "title" in new_item:
                    new_item["title"] = filtered[0]
                elif "text" in new_item:
                    new_item["text"] = filtered[0]
                else:
                    new_item["keyword"] = filtered[0]

                cleaned.append(new_item)

            return cleaned

        # 문자열 리스트 구조 대응
        filtered = cbl_filter_today_keywords_by_category(
            fallback_category,
            items,
            limit,
        )

        # 너무 적게 남으면 안전 예시 키워드로 보충
        if fallback_category and len(filtered) < limit:
            try:
                profile = cbl_today_keyword_category_profile(fallback_category)
                for ex in profile.get("examples", []):
                    if ex not in filtered:
                        filtered.append(ex)
                    if len(filtered) >= limit:
                        break
            except Exception:
                pass

        return filtered[:limit]

    def _cbl_filter_keyword_response_data(data, request_category=""):
        if not isinstance(data, dict):
            return data

        # 가장 흔한 응답 키들 대응
        for key in ["keywords", "recommended_keywords", "items", "results"]:
            if key in data and isinstance(data[key], list):
                data[key] = _cbl_filter_keyword_items(
                    data[key],
                    fallback_category=request_category,
                    limit=7,
                )

        # 카테고리별 dict 응답 대응
        # 예: {"architecture": [...], "tech": [...]}
        category_keys = [
            "architecture", "realestate", "finance", "tech", "life",
            "건축", "부동산", "금융", "테크", "일상",
        ]

        for key in category_keys:
            if key in data and isinstance(data[key], list):
                data[key] = _cbl_filter_keyword_items(
                    data[key],
                    fallback_category=key,
                    limit=7,
                )

        # nested 구조 대응
        # 예: {"data": {"keywords": [...]}}
        if isinstance(data.get("data"), dict):
            data["data"] = _cbl_filter_keyword_response_data(
                data["data"],
                request_category=request_category,
            )

        return data

    def ai_keyword_recommend(request, *args, **kwargs):
        import json
        from django.http import JsonResponse

        payload = _cbl_read_keyword_request_payload(request)
        request_category = _cbl_detect_keyword_category(request, payload)

        response = _cbl_prev_ai_keyword_recommend(request, *args, **kwargs)

        try:
            content_type = response.get("Content-Type", "")
        except Exception:
            content_type = ""

        if "application/json" not in content_type:
            return response

        try:
            data = json.loads(response.content.decode("utf-8"))
        except Exception:
            return response

        data = _cbl_filter_keyword_response_data(
            data,
            request_category=request_category,
        )

        return JsonResponse(
            data,
            status=getattr(response, "status_code", 200),
            safe=isinstance(data, dict),
            json_dumps_params={"ensure_ascii": False},
        )

except NameError:
    pass
# CBL_AI_KEYWORD_RECOMMEND_CATEGORY_LOCK_VIEW_END


# CBL_AI_ROW_CATEGORY_GENERATE_START
# 목적:
# 자동글 생성 모달에서 여러 행을 선택했을 때
# 각 행의 category / keyword / image_count를 따로 적용해 글을 생성한다.
try:
    import json as _cbl_row_json
    from django.http import JsonResponse as _cbl_row_JsonResponse

    _cbl_prev_ai_post_generate_row_category = ai_post_generate

    def _cbl_row_normalize_category(value, text=""):
        canonical, diagnostics = cbl_resolve_auto_post_category(
            value, title=text,
        )
        logger.info(
            "auto_post_category_row raw_category=%r normalized_before=%r "
            "canonical_category=%r legacy_mapping_used=%s fallback_reason=%s",
            value,
            diagnostics.get("normalized_before"),
            canonical,
            diagnostics.get("legacy_mapping_used"),
            diagnostics.get("fallback_reason") or "",
        )
        if canonical is None:
            raise ValueError("자동글 행의 카테고리를 안전하게 분류하지 못했습니다.")
        return canonical

    def _cbl_row_clean_image_count(value):
        try:
            value = str(value or "0").replace("장", "").strip()
            value = int(value)
        except Exception:
            value = 0

        return str(max(0, min(value, 5)))

    def _cbl_parse_keyword_rows(request):
        rows = []

        raw = str(request.POST.get("cbl_keyword_rows", "") or "").strip()

        if raw:
            try:
                parsed = _cbl_row_json.loads(raw)
            except Exception:
                parsed = []

            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue

                    keyword = str(item.get("keyword", "") or "").strip()
                    if not keyword:
                        continue

                    rows.append({
                        "keyword": keyword,
                        "category": _cbl_row_normalize_category(
                            item.get("category"), keyword,
                        ),
                        "image_count": _cbl_row_clean_image_count(item.get("image_count")),
                    })

        if not rows:
            keywords = request.POST.getlist("cbl_row_keywords[]")
            categories = request.POST.getlist("cbl_row_categories[]")
            image_counts = request.POST.getlist("cbl_row_image_counts[]")

            for idx, keyword in enumerate(keywords):
                keyword = str(keyword or "").strip()
                if not keyword:
                    continue

                rows.append({
                    "keyword": keyword,
                    "category": _cbl_row_normalize_category(
                        categories[idx] if idx < len(categories) else request.POST.get("category"),
                        keyword,
                    ),
                    "image_count": _cbl_row_clean_image_count(image_counts[idx] if idx < len(image_counts) else request.POST.get("image_count")),
                })

        # 중복 제거
        result = []
        seen = set()

        for row in rows:
            key = row["keyword"].replace(" ", "").lower()
            if not key or key in seen:
                continue

            result.append(row)
            seen.add(key)

            if len(result) >= 20:
                break

        return result

    def _cbl_get_post_model_for_rows():
        try:
            from .models import Post
            return Post
        except Exception:
            return None

    def _cbl_success_response_for_row_posts(posts):
        items = []

        for post in posts:
            url = ""

            try:
                url = post.get_absolute_url()
            except Exception:
                url = f"/post/{getattr(post, 'id', '')}/"

            items.append({
                "id": getattr(post, "id", None),
                "post_id": getattr(post, "id", None),
                "title": getattr(post, "title", ""),
                "category": getattr(post, "category", ""),
                "url": url,
                "detail_url": url,
                "is_published": False,
                "status": "draft",
            })

        return _cbl_row_JsonResponse({
            "success": True,
            "ok": True,
            "created_count": len(items),
            "success_count": len(items),
            "failed_count": 0,
            "error_count": 0,
            "created_posts": items,
            "posts": items,
            "is_published": False,
            "publish_immediately": False,
            "status": "draft",
            "message": "AI 글 1개 생성 완료" if len(items) == 1 else f"AI 글 {len(items)}개 생성 완료",
        }, json_dumps_params={"ensure_ascii": False})

    def ai_post_generate(request, *args, **kwargs):
        if getattr(request, "method", "").upper() != "POST":
            return _cbl_prev_ai_post_generate_row_category(request, *args, **kwargs)

        rows = _cbl_parse_keyword_rows(request)

        # 행별 payload가 없으면 기존 로직 그대로 사용
        if not rows:
            return _cbl_prev_ai_post_generate_row_category(request, *args, **kwargs)

        Post = _cbl_get_post_model_for_rows()

        before_max_id = 0
        try:
            if Post is not None:
                before_max_id = Post.objects.order_by("-id").values_list("id", flat=True).first() or 0
        except Exception:
            before_max_id = 0

        original_post = request.POST

        try:
            # 핵심:
            # 기존 ai_post_generate는 category를 1번만 읽고 모든 키워드에 적용한다.
            # 그래서 여기서 행별로 POST를 바꿔서 기존 생성 함수를 1번씩 호출한다.
            for row in rows:
                qd = original_post.copy()

                qd["category"] = row["category"]
                qd["post_category"] = row["category"]
                qd["selected_category"] = row["category"]
                qd["ai_category"] = row["category"]
                qd["cbl_force_category"] = row["category"]

                qd["keywords"] = row["keyword"]
                qd["selected_keywords"] = _cbl_row_json.dumps([row["keyword"]], ensure_ascii=False)
                qd["count"] = "1"
                qd["image_count"] = row["image_count"]

                # 행별 생성 중에는 전체 행 JSON을 비워서 재분기 방지
                qd["cbl_keyword_rows"] = ""

                request.POST = qd
                _cbl_prev_ai_post_generate_row_category(request, *args, **kwargs)

        except Exception as error:
            request.POST = original_post
            print("CBL row category generate error:", error)

            return _cbl_row_JsonResponse({
                "success": False,
                "ok": False,
                "error": str(error),
                "message": f"AI 글 생성 중 오류가 발생했습니다: {error}",
            }, status=500, json_dumps_params={"ensure_ascii": False})

        finally:
            request.POST = original_post

        posts = []

        try:
            if Post is not None:
                qs = Post.objects.filter(id__gt=before_max_id).order_by("id")
                posts = list(qs)

                if posts:
                    qs.update(is_published=False)

                    # 안전장치: 생성된 순서대로 행 카테고리를 다시 한 번 고정
                    # 한국어만 생성하면 posts 개수 == rows 개수
                    # 영어까지 생성하면 한 행당 여러 글이 생길 수 있으므로 keyword 순서 기반으로 최대한 보정
                    row_index = 0

                    for post in posts:
                        if row_index >= len(rows):
                            row_index = len(rows) - 1

                        target_category = rows[row_index]["category"]

                        try:
                            post.category = target_category
                            post.is_published = False
                            post.save(update_fields=["category", "is_published", "updated_at"])
                        except Exception:
                            try:
                                post.category = target_category
                                post.is_published = False
                                post.save(update_fields=["category", "is_published"])
                            except Exception:
                                pass

                        # 영어버전 등 언어가 여러 개면 제목 기준이 완벽하지 않을 수 있어서,
                        # 기본은 생성 순서대로 진행한다.
                        row_index += 1

        except Exception as error:
            print("CBL row category post fix error:", error)

        if posts:
            return _cbl_success_response_for_row_posts(posts)

        return _cbl_prev_ai_post_generate_row_category(request, *args, **kwargs)

except Exception as _cbl_row_category_generate_load_error:
    print("CBL_AI_ROW_CATEGORY_GENERATE load error:", _cbl_row_category_generate_load_error)
# CBL_AI_ROW_CATEGORY_GENERATE_END

# CBL_ENGLISH_LOCALIZATION_PROMPT_PATCH_START
# 영어 선택 시 직역이 아닌 영어권 독자용 현지화 재작성 지시를 추가한다.
_cbl_build_language_prompt_before_localization = cbl_build_language_prompt


def cbl_build_language_prompt(*args, **kwargs):
    prompt = _cbl_build_language_prompt_before_localization(
        *args,
        **kwargs,
    )

    language = kwargs.get("language")

    if language is None and args:
        language = args[0]

    if str(language or "").strip().lower() != "en":
        return prompt

    localization_rules = """
[English localization and editorial adaptation rules]

- Write for international English-speaking readers.
- Do not use literal or sentence-by-sentence translation.
- Preserve verified facts, figures, dates, names, URLs, warnings, and conclusions.
- Never invent statistics, prices, legal rules, rankings, or other factual claims.
- Create a distinct English title rather than translating the Korean title word for word.
- Rewrite the introduction using a different but relevant opening angle.
- Vary sentence structure and paragraph flow naturally.
- Reorganize H2 and H3 sections when that improves clarity.
- Do not mechanically copy the original paragraph and heading order.
- Explain Korea-specific terms briefly when overseas readers may not understand them.
- Adapt examples only when the underlying facts remain unchanged.
- Keep technical terms, brands, companies, products, and proper nouns accurate.
- Write a fresh English summary, meta description, thumbnail phrase, and SEO tags.
- Avoid repetitive AI-style phrases and generic introductions.
- The result should feel independently edited for English readers.
""".strip()

    return f"{prompt}\n\n{localization_rules}"


# CBL_ENGLISH_LOCALIZATION_PROMPT_PATCH_END

# CBL_KEYWORD_RESPONSE_FILTER_V8_START
#
# naver_news.py에서 이미 카테고리 분류·안전 보정을 마친
# dict 추천 결과를 views.py에서 다시 과도하게 삭제하지 않는다.
#
# 유지:
# - 기존 JSON 구조
# - 기존 카드 레이아웃
# - 카테고리별 최대 7개
#
# 제거:
# - 동일 키워드 중복
# - 명백한 타 카테고리 금지 주제
# - 빈 키워드
#

def _cbl_filter_keyword_items(
    items,
    fallback_category="",
    limit=7,
):
    from core.ai_writer import (
        cbl_filter_today_keywords_by_category,
        cbl_today_keyword_category_profile,
    )

    if not isinstance(items, list):
        return items

    try:
        limit = max(1, min(int(limit or 7), 7))
    except (TypeError, ValueError):
        limit = 7

    # naver_news.py가 반환하는 dict 리스트
    if items and isinstance(items[0], dict):
        cleaned = []
        counters = {}
        seen = set()

        category_alias = {
            "건축": "architecture",
            "건설": "architecture",
        "BIM": "bim",
        "비아이엠": "bim",
        "프로그램": "program",
        "툴": "program",
        "BIM": "bim",
        "bim": "bim",
        "비아이엠": "bim",
        "프로그램": "program",
        "툴": "program",
            "architecture": "architecture",

            "부동산": "realestate",
            "realestate": "realestate",

            "금융": "finance",
            "경제": "finance",
            "finance": "finance",

            "테크": "tech",
            "기술": "tech",
            "IT": "tech",
            "it": "tech",
            "tech": "tech",

            "일상": "life",
            "생활": "life",
            "life": "life",
        }

        for item in items:
            if not isinstance(item, dict):
                continue

            category_raw = _cbl_keyword_category_from_item(
                item,
                fallback_category,
            )

            category = category_alias.get(
                str(category_raw or "").strip(),
                str(category_raw or fallback_category or "").strip(),
            )

            keyword = str(
                _cbl_keyword_text_from_item(item) or ""
            ).strip()

            if not keyword:
                continue

            category_key = category or str(
                fallback_category or ""
            ).strip()

            if counters.get(category_key, 0) >= limit:
                continue

            normalized = "".join(
                keyword.lower().split()
            )

            duplicate_key = (
                category_key,
                normalized,
            )

            if duplicate_key in seen:
                continue

            # 명백한 금지 주제만 검사한다.
            # 허용 단어가 반드시 들어가야 한다는 조건은 적용하지 않는다.
            try:
                profile = cbl_today_keyword_category_profile(
                    category_key
                )

                blocked_words = [
                    str(word or "").lower()
                    for word in profile.get("block", [])
                    if str(word or "").strip()
                ]

                keyword_lower = keyword.lower()

                if any(
                    blocked in keyword_lower
                    for blocked in blocked_words
                ):
                    continue

            except Exception:
                pass

            new_item = dict(item)

            if "keyword" in new_item:
                new_item["keyword"] = keyword
            elif "title" in new_item:
                new_item["title"] = keyword
            elif "text" in new_item:
                new_item["text"] = keyword
            else:
                new_item["keyword"] = keyword

            # 카테고리 누락 시 복원
            if not new_item.get("category") and category_key:
                new_item["category"] = category_key

            cleaned.append(new_item)
            seen.add(duplicate_key)
            counters[category_key] = (
                counters.get(category_key, 0) + 1
            )

        print(
            "[TODAY_KEYWORD_RESPONSE_V8]",
            f"input={len(items)}",
            f"output={len(cleaned)}",
            f"categories={counters}",
        )

        return cleaned

    # 문자열 리스트는 기존 엄격 필터 유지
    filtered = cbl_filter_today_keywords_by_category(
        fallback_category,
        items,
        limit,
    )

    if fallback_category and len(filtered) < limit:
        try:
            profile = cbl_today_keyword_category_profile(
                fallback_category
            )

            for example in profile.get("examples", []):
                example = str(example or "").strip()

                if not example:
                    continue

                if example in filtered:
                    continue

                filtered.append(example)

                if len(filtered) >= limit:
                    break

        except Exception:
            pass

    return filtered[:limit]


# CBL_KEYWORD_RESPONSE_FILTER_V8_END

# CBL_DISABLE_GENERIC_KEYWORD_FALLBACK_V21_START

def _cbl_v21_remove_generic_keyword_fallback(items):
    cleaned = []

    for item in items or []:
        if not isinstance(item, dict):
            continue

        reason = str(item.get("reason", "") or "").strip()

        if reason == "추천 키워드":
            continue

        cleaned.append(item)

    return cleaned

# CBL_DISABLE_GENERIC_KEYWORD_FALLBACK_V21_END

# CBL_FINAL_KEYWORD_ENDPOINT_V22_START

@user_passes_test(admin_required)
def ai_keyword_recommend(request):
    if request.method != "POST":
        return JsonResponse({
            "ok": False,
            "keywords": [],
            "message": "POST 요청만 가능합니다.",
        }, status=405)

    requested_category = str(
        request.POST.get("category", "all") or "all"
    ).strip()

    categories = [
        "construction_work",
        "construction_tech",
        "construction_real",
        "bim",
        "dynamo_automation",
        "four_d_five_d",
        "tech_ai_development",
        "tech_data_security",
        "tech_server_software",
        "program",
        "tool_recommend",
    ]

    try:
        if requested_category == "all":
            raw_items = []

            for category in categories:
                raw_items.extend(
                    recommend_keywords_from_news(category)
                )
        else:
            raw_items = recommend_keywords_from_news(
                requested_category
            )

        cleaned = []
        seen = set()

        for item in raw_items or []:
            if not isinstance(item, dict):
                continue

            keyword = str(
                item.get("keyword", "") or ""
            ).strip()

            reason = str(
                item.get("reason", "") or ""
            ).strip()

            category_label = str(
                item.get("category", "") or ""
            ).strip()

            if not keyword or not reason:
                continue

            if reason == "추천 키워드":
                continue

            key = keyword.replace(" ", "").lower()

            if not key or key in seen:
                continue

            cleaned.append({
                "category": category_label,
                "keyword": keyword,
                "reason": reason,
            })

            seen.add(key)

        if not cleaned:
            return JsonResponse({
                "ok": False,
                "keywords": [],
                "message": (
                    "최신 키워드 검색에 실패했습니다. "
                    "잠시 후 다시 시도해주세요."
                ),
            }, status=503)

        print(
            "[KEYWORD_V22_ENDPOINT]",
            f"category={requested_category}",
            f"items={len(cleaned)}",
        )

        return JsonResponse({
            "ok": True,
            "keywords": cleaned,
        })

    except Exception as error:
        print(
            "[KEYWORD_V22_ENDPOINT_ERROR]",
            f"error={type(error).__name__}: {error}",
        )

        return JsonResponse({
            "ok": False,
            "keywords": [],
            "message": "최신 키워드 검색 중 오류가 발생했습니다.",
        }, status=500)


# CBL_FINAL_KEYWORD_ENDPOINT_V22_END


# CBL_MANUAL_TODAY_KEYWORD_DEDUPE_V26_1_START
@user_passes_test(admin_required)
def ai_keyword_recommend(request):
    """
    자동글 작성 화면의 '오늘자 키워드 추천' 최종 엔드포인트.

    - 카테고리 전체에서 동일 URL 제거
    - 제목 문구가 조금 다른 유사 키워드 제거
    - 최근 작성 글과 유사한 키워드 재추천 방지
    - 기존 응답 필드 유지
    """
    from core.keyword_dedupe import (
        unpack_recommendation,
        is_duplicate_candidate,
    )

    if request.method != "POST":
        return JsonResponse({
            "ok": False,
            "keywords": [],
            "message": "POST 요청만 가능합니다.",
        }, status=405)

    requested_category = str(
        request.POST.get("category", "all") or "all"
    ).strip()

    categories = [
        "construction_work",
        "construction_tech",
        "construction_real",
        "bim",
        "dynamo_automation",
        "four_d_five_d",
        "tech_ai_development",
        "tech_data_security",
        "tech_server_software",
        "program",
        "tool_recommend",
    ]

    if requested_category != "all":
        categories = [requested_category]

    accepted = []

    # 최근 생성 글과 비슷한 키워드는 추천 목록에서 제외한다.
    for title in (
        Post.objects
        .order_by("-created_at")
        .values_list("title", flat=True)[:300]
    ):
        accepted.append({
            "keyword": str(title or "").strip(),
            "source_url": "",
        })

    cleaned = []

    try:
        for category in categories:
            raw_items = recommend_keywords_from_news(category) or []

            for raw_item in raw_items:
                candidate = unpack_recommendation(raw_item)

                if not candidate.get("keyword"):
                    continue

                if is_duplicate_candidate(candidate, accepted):
                    continue

                item = dict(raw_item) if isinstance(raw_item, dict) else {}
                item["keyword"] = candidate["keyword"]
                item["reason"] = candidate.get("reason", "")
                item["source_url"] = candidate.get("source_url", "")
                item["source"] = candidate.get("source", "")
                item["published_at"] = candidate.get("published_at", "")

                if not item.get("category"):
                    item["category"] = candidate.get("category_label", "")

                cleaned.append(item)
                accepted.append(candidate)

        if not cleaned:
            return JsonResponse({
                "ok": False,
                "keywords": [],
                "message": (
                    "중복 항목과 최근 작성 글을 제외한 뒤 "
                    "새 추천키워드가 남지 않았습니다."
                ),
            }, status=503)

        return JsonResponse({
            "ok": True,
            "keywords": cleaned,
        })

    except Exception as error:
        return JsonResponse({
            "ok": False,
            "keywords": [],
            "message": str(error),
        }, status=500)
# CBL_MANUAL_TODAY_KEYWORD_DEDUPE_V26_1_END


# CBL_CALENDAR_COLOR_ALLDAY_HELPERS_START
def _cbl_calendar_bool(value):
    return str(value or "").strip().lower() in ("1", "true", "on", "yes", "y")

def _cbl_calendar_normalize_color(value):
    raw = (value or "").strip() or "#2f9e97"
    if not raw.startswith("#"):
        raw = "#" + raw
    raw = raw[:7]
    if len(raw) != 7:
        return "#2f9e97"
    hex_part = raw[1:]
    allowed = "0123456789abcdefABCDEF"
    if any(ch not in allowed for ch in hex_part):
        return "#2f9e97"
    return raw.lower()
# CBL_CALENDAR_COLOR_ALLDAY_HELPERS_END

def calendar_events_month_api(request):
    """
    메인 주요 일정 달력용 월별 이벤트 API
    /api/calendar-events/?year=2026&month=6
    """
    today = timezone.localdate()

    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month

    if month < 1 or month > 12:
        year, month = today.year, today.month

    _, last_day = calendar.monthrange(year, month)
    start = date(year, month, 1)
    end = date(year, month, last_day)

    qs = (
        CalendarEvent.objects
        .filter(is_public=True, event_date__lte=end)
        .filter(models.Q(end_date__isnull=True, event_date__gte=start) | models.Q(end_date__gte=start))
        .order_by("event_date", "start_time", "id")
    )

    # CBL_CALENDAR_API_DEDUPE_CLEAN_START
    # 같은 일정이 실수로 여러 번 등록되어도 화면에는 1개만 내려보냅니다.
    calendar_seen_keys = set()
    # CBL_CALENDAR_API_DEDUPE_CLEAN_END

    events = []
    for ev in qs:
        ev_end_date = ev.end_date or ev.event_date

        calendar_event_key = (
            ev.title or "",
            ev.event_date.isoformat() if ev.event_date else "",
            ev_end_date.isoformat() if ev_end_date else "",
            ev.start_time.isoformat() if ev.start_time else "",
            ev.end_time.isoformat() if ev.end_time else "",
            ev.category or "일정",
        )

        if calendar_event_key in calendar_seen_keys:
            continue

        calendar_seen_keys.add(calendar_event_key)
        if ev_end_date == ev.event_date:
            date_label = f"{ev.event_date.day}일"
        else:
            if ev.event_date.month == ev_end_date.month:
                date_label = f"{ev.event_date.day}일~{ev_end_date.day}일"
            else:
                date_label = f"{ev.event_date.month}/{ev.event_date.day}~{ev_end_date.month}/{ev_end_date.day}"

        events.append({
            "id": ev.id,
            "title": ev.title,
            "date": ev.event_date.isoformat(),
            "end_date": ev_end_date.isoformat(),
            "day": ev.event_date.day,
            "end_day": ev_end_date.day,
            "date_label": date_label,
            "start_time": ev.start_time.strftime("%H:%M") if ev.start_time else "",
            "end_time": ev.end_time.strftime("%H:%M") if ev.end_time else "",
            "category": ev.category or "일정",
            "description": ev.description or "",
            "link_url": ev.link_url or "",
            "is_important": ev.is_important,
            "is_all_day": getattr(ev, "is_all_day", False),
            "event_color": getattr(ev, "event_color", "#2f9e97") or "#2f9e97",
        })

    return JsonResponse({
        "year": year,
        "month": month,
        "today": today.isoformat(),
        "events": events,
    })


@cbl_staff_member_required
@cbl_require_POST
def calendar_event_create_api(request):
    """
    홈 주요 일정 팝업 등록 API
    staff 계정만 등록 가능
    """
    from datetime import datetime

    keyword = (request.POST.get("keyword") or "").strip()
    title = (request.POST.get("title") or "").strip()

    if not title and keyword:
        title = keyword
    event_date_raw = (request.POST.get("event_date") or "").strip()
    end_date_raw = (request.POST.get("end_date") or "").strip()
    start_time_raw = (request.POST.get("start_time") or "").strip()
    end_time_raw = (request.POST.get("end_time") or "").strip()
    category = (request.POST.get("category") or "일정").strip()
    description = (request.POST.get("description") or "").strip()
    link_url = (request.POST.get("link_url") or "").strip()
    is_important = request.POST.get("is_important") in ("1", "true", "on", "yes")
    is_all_day = _cbl_calendar_bool(request.POST.get("is_all_day"))
    event_color = _cbl_calendar_normalize_color(request.POST.get("event_color"))

    if not title:
        return CBLJsonResponse({"ok": False, "message": "일정명을 입력해 주세요."}, status=400)

    if not event_date_raw:
        return CBLJsonResponse({"ok": False, "message": "일정 날짜를 선택해 주세요."}, status=400)

    try:
        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()
    except ValueError:
        return CBLJsonResponse({"ok": False, "message": "날짜 형식이 올바르지 않습니다."}, status=400)

    end_date = event_date
    if end_date_raw:
        try:
            end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date()
        except ValueError:
            return CBLJsonResponse({"ok": False, "message": "종료일 형식이 올바르지 않습니다."}, status=400)

    if end_date < event_date:
        return CBLJsonResponse({"ok": False, "message": "종료일은 시작일보다 빠를 수 없습니다."}, status=400)

    def parse_time(value):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError:
            return None

    ev = CBLCalendarEvent.objects.create(
        title=title,
        event_date=event_date,
        end_date=end_date,
        start_time=None if is_all_day else parse_time(start_time_raw),
        end_time=None if is_all_day else parse_time(end_time_raw),
        category=category or "일정",
        description=description,
        link_url=link_url,
        is_public=True,
        is_important=is_important,
        is_all_day=is_all_day,
        event_color=event_color,
    )

    return CBLJsonResponse({
        "ok": True,
        "message": "일정이 등록되었습니다.",
        "event": {
            "id": ev.id,
            "title": ev.title,
            "date": ev.event_date.isoformat(),
        }
    })


# CBL_CALENDAR_MANAGE_API_START
def _cbl_parse_calendar_payload(request):
    """
    캘린더 등록/수정 공통 파서
    """
    from datetime import datetime

    title = (request.POST.get("title") or "").strip()
    event_date_raw = (request.POST.get("event_date") or "").strip()
# CBL_CALENDAR_AI_SUGGEST_API_START
# 2026-07-28 사용자 요청: "일정등록에 AI를 사용해 자동일정등록 — 키워드 기입할 수 있는 칸
# (추가/삭제), 일정들 리스트가 나오면 체크해서 자동으로 일정이 등록". 관리자가 키워드를
# 여러 개 입력하면 Gemini가 후보 일정 목록(제목/날짜/분류)을 만들어 보여주고, 그중 체크한
# 것만 골라서 한 번에 등록하는 2단계(제안 → 일괄등록) 기능. 정확한 미래 날짜(공휴일 등)를
# 모델이 지어낼 위험이 있으므로, 날짜를 확신 못 하면 반드시 null로 비우고 note에 이유를
# 남기라고 프롬프트에서 강제한다 — 프론트는 날짜가 없는 항목은 사용자가 직접 채우기 전엔
# 체크할 수 없게 만든다.
# 2026-07-28 사용자 지적: "야 .env 에 제미나이 있잖아" — 처음엔 OPENAI_API_KEY 기반으로
# 만들었는데, 이 서버 .env에는 GEMINI_API_KEY만 있고 OPENAI_API_KEY가 없어 실제로는
# 동작하지 않았다. ai_writer.py의 gemini_generate_text(재시도 래퍼)+extract_json을
# 그대로 재사용한다 — 이미 recommend_today_keywords 등에서 검증된 "키워드 → JSON 목록"
# 패턴과 동일하다. 게다가 ai_writer.py는 기본적으로 GEMINI_USE_GOOGLE_SEARCH=true라
# 구글 검색 근거를 함께 참고하므로, 공휴일처럼 실제 날짜가 있는 항목은 순수 LLM 추측보다
# 더 신뢰할 수 있다 — 그래도 프롬프트의 "확신 없으면 null" 규칙과 프론트의 체크 제한은
# 그대로 유지한다(안전망은 이중으로 둔다).
@cbl_staff_member_required
@cbl_require_POST
def calendar_ai_suggest_api(request):
    """
    키워드 목록을 받아 Gemini로 캘린더 일정 후보 목록을 생성해 반환한다.
    실제로 등록하지는 않는다 — 사용자가 체크한 것만 calendar_ai_bulk_create_api로
    별도 등록한다.
    """
    from datetime import datetime
    import json

    from .ai_writer import gemini_generate_text, extract_json

    raw_keywords = request.POST.get("keywords") or "[]"
    try:
        keywords = json.loads(raw_keywords)
        if not isinstance(keywords, list):
            keywords = []
    except Exception:
        keywords = []
    keywords = [str(k or "").strip() for k in keywords if str(k or "").strip()][:10]

    if not keywords:
        return CBLJsonResponse({"ok": False, "message": "키워드를 1개 이상 입력해 주세요."}, status=400)

    today = datetime.now().date()

    prompt = f"""
너는 건설/BIM/실무 업무 사이트(ChickenBananaLab)의 캘린더 일정 후보를 만드는 어시스턴트다.

오늘 날짜: {today.isoformat()}
관리자가 준 키워드: {json.dumps(keywords, ensure_ascii=False)}

위 키워드를 참고해서 캘린더에 등록할 만한 일정 후보를 각 키워드당 1~3개씩, 최대 12개 만들어라.

가장 중요한 규칙:
- 정확한 날짜(공휴일, 특정 행사일 등)를 확신할 수 없으면 절대 날짜를 지어내지 말고
  event_date를 null로 두고 note에 "정확한 날짜 확인 필요"라고 남겨라.
- 확신할 수 있는 경우(예: 이미 안정적으로 아는 매년 고정 국경일, 검색으로 실제 확인한 날짜)만
  event_date를 채워라.
- title은 20자 이내로 간결하게.
- date는 반드시 YYYY-MM-DD 형식 또는 null.

반환은 반드시 아래 JSON 형식만 사용해라. 마크다운, 코드블록, 설명문은 쓰지 마라.

{{
  "suggestions": [
    {{
      "title": "일정 제목",
      "event_date": "YYYY-MM-DD 또는 null",
      "end_date": "YYYY-MM-DD 또는 null(단일일이면 event_date와 동일하거나 null)",
      "category": "업데이트/공지/강의/개인일정/행사 등 짧은 분류",
      "is_all_day": true,
      "note": "왜 이 일정을 제안했는지, 날짜가 불확실하면 그 이유",
      "confident_date": true
    }}
  ]
}}
"""

    try:
        text = gemini_generate_text(prompt)
        data = extract_json(text)
        suggestions_raw = data.get("suggestions") if isinstance(data, dict) else None

        if not isinstance(suggestions_raw, list):
            return CBLJsonResponse({"ok": False, "message": "AI 응답을 해석하지 못했습니다. 다시 시도해 주세요."}, status=502)

    except Exception as error:
        print("[CBL_CALENDAR_AI_SUGGEST_ERROR]", type(error).__name__, str(error))
        return CBLJsonResponse({"ok": False, "message": f"AI 호출에 실패했습니다: {type(error).__name__}"}, status=502)

    suggestions = []
    for item in suggestions_raw[:12]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        event_date = str(item.get("event_date") or "").strip() or None
        end_date = str(item.get("end_date") or "").strip() or None
        if event_date:
            try:
                datetime.strptime(event_date, "%Y-%m-%d")
            except ValueError:
                event_date = None
        if end_date:
            try:
                datetime.strptime(end_date, "%Y-%m-%d")
            except ValueError:
                end_date = None
        suggestions.append({
            "title": title[:120],
            "event_date": event_date,
            "end_date": end_date,
            "category": (str(item.get("category") or "일정").strip() or "일정")[:30],
            "is_all_day": bool(item.get("is_all_day")),
            "note": str(item.get("note") or "").strip()[:200],
            "confident_date": bool(item.get("confident_date")) and event_date is not None,
        })

    return CBLJsonResponse({"ok": True, "suggestions": suggestions})


def _cbl_calendar_build_event_kwargs(fields):
    """calendar_event_create_api와 calendar_ai_bulk_create_api가 공유하는 검증 로직.
    Returns: (kwargs dict | None, error_message | None)"""
    from datetime import datetime

    title = str(fields.get("title") or "").strip()
    if not title:
        return None, "일정명을 입력해 주세요."

    event_date_raw = str(fields.get("event_date") or "").strip()
    if not event_date_raw:
        return None, "일정 날짜가 없습니다."
    try:
        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()
    except ValueError:
        return None, "날짜 형식이 올바르지 않습니다."

    end_date_raw = str(fields.get("end_date") or "").strip()
    end_date = event_date
    if end_date_raw:
        try:
            end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date()
        except ValueError:
            return None, "종료일 형식이 올바르지 않습니다."
    if end_date < event_date:
        return None, "종료일은 시작일보다 빠를 수 없습니다."

    category = str(fields.get("category") or "일정").strip() or "일정"

    return {
        "title": title[:120],
        "event_date": event_date,
        "end_date": end_date,
        "start_time": None,
        "end_time": None,
        "category": category[:30],
        "description": str(fields.get("description") or "").strip(),
        "link_url": str(fields.get("link_url") or "").strip(),
        "is_public": True,
        "is_important": bool(fields.get("is_important")),
        "is_all_day": _cbl_calendar_bool(fields.get("is_all_day")),
        "event_color": _cbl_calendar_normalize_color(fields.get("event_color")),
    }, None


@cbl_staff_member_required
@cbl_require_POST
def calendar_ai_bulk_create_api(request):
    """AI 추천 목록 중 사용자가 체크한 항목들을 한 번에 등록한다."""
    import json

    raw_items = request.POST.get("items") or "[]"
    try:
        items = json.loads(raw_items)
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []

    if not items:
        return CBLJsonResponse({"ok": False, "message": "등록할 일정을 선택해 주세요."}, status=400)

    created = []
    errors = []
    for idx, item in enumerate(items[:30]):
        if not isinstance(item, dict):
            errors.append({"index": idx, "message": "잘못된 항목 형식입니다."})
            continue
        kwargs, err = _cbl_calendar_build_event_kwargs(item)
        if err:
            errors.append({"index": idx, "title": item.get("title"), "message": err})
            continue
        ev = CBLCalendarEvent.objects.create(**kwargs)
        created.append({"id": ev.id, "title": ev.title, "date": ev.event_date.isoformat()})

    return CBLJsonResponse({
        "ok": len(created) > 0,
        "created": created,
        "errors": errors,
        "message": f"{len(created)}개 일정을 등록했습니다." + (f" ({len(errors)}개 실패)" if errors else ""),
    })
# CBL_CALENDAR_AI_SUGGEST_API_END
    end_date_raw = (request.POST.get("end_date") or "").strip()
    start_time_raw = (request.POST.get("start_time") or "").strip()
    end_time_raw = (request.POST.get("end_time") or "").strip()
    category = (request.POST.get("category") or "일정").strip()
    description = (request.POST.get("description") or "").strip()
    link_url = (request.POST.get("link_url") or "").strip()
    is_important = request.POST.get("is_important") in ("1", "true", "on", "yes")

    if not title:
        raise ValueError("일정명을 입력해 주세요.")

    if not event_date_raw:
        raise ValueError("시작일을 선택해 주세요.")

    try:
        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("시작일 형식이 올바르지 않습니다.")

    end_date = event_date
    if end_date_raw:
        try:
            end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("종료일 형식이 올바르지 않습니다.")

    if end_date < event_date:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")

    def parse_time(value):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError:
            return None

    return {
        "title": title,
        "event_date": event_date,
        "end_date": end_date,
        "start_time": parse_time(start_time_raw),
        "end_time": parse_time(end_time_raw),
        "category": category or "일정",
        "description": description,
        "link_url": link_url,
        "is_important": is_important,
        "is_public": True,
    }


# CBL_CALENDAR_MANAGE_API_END


# CBL_CALENDAR_EDIT_DELETE_API_V2_START
def _cbl_calendar_parse_payload_v2(request):
    """
    캘린더 등록/수정 공통 파서 V2
    """
    from datetime import datetime

    title = (request.POST.get("title") or request.POST.get("keyword") or "").strip()
    event_date_raw = (request.POST.get("event_date") or "").strip()
    end_date_raw = (request.POST.get("end_date") or "").strip()
    start_time_raw = (request.POST.get("start_time") or "").strip()
    end_time_raw = (request.POST.get("end_time") or "").strip()
    category = (request.POST.get("category") or "일정").strip()
    description = (request.POST.get("description") or "").strip()
    link_url = (request.POST.get("link_url") or "").strip()
    is_important = request.POST.get("is_important") in ("1", "true", "on", "yes")

    if not title:
        raise ValueError("일정명을 입력해 주세요.")

    if not event_date_raw:
        raise ValueError("시작일을 선택해 주세요.")

    try:
        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("시작일 형식이 올바르지 않습니다.")

    end_date = event_date
    if end_date_raw:
        try:
            end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("종료일 형식이 올바르지 않습니다.")

    if end_date < event_date:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")

    def parse_time(value):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError:
            return None

    return {
        "title": title,
        "event_date": event_date,
        "end_date": end_date,
        "start_time": parse_time(start_time_raw),
        "end_time": parse_time(end_time_raw),
        "category": category or "일정",
        "description": description,
        "link_url": link_url,
        "is_public": True,
        "is_important": is_important,
    }


@cbl_staff_member_required
@cbl_require_POST
def calendar_event_update_api(request, pk):
    """
    캘린더 일정 수정 API V2
    """
    try:
        ev = get_object_or_404(CBLCalendarEvent, pk=pk)
        payload = _cbl_calendar_parse_payload_v2(request)

        for key, value in payload.items():
            setattr(ev, key, value)

        ev.save()

        return CBLJsonResponse({
            "ok": True,
            "message": "일정이 수정되었습니다.",
            "event": {
                "id": ev.id,
                "title": ev.title,
                "date": ev.event_date.isoformat(),
            },
        })

    except ValueError as error:
        return CBLJsonResponse({"ok": False, "message": str(error)}, status=400)

    except Exception as error:
        return CBLJsonResponse({"ok": False, "message": str(error)}, status=500)


@cbl_staff_member_required
@cbl_require_POST
def calendar_event_delete_api(request, pk):
    """
    캘린더 일정 삭제 API V2
    """
    try:
        ev = get_object_or_404(CBLCalendarEvent, pk=pk)
        ev.delete()

        return CBLJsonResponse({
            "ok": True,
            "message": "일정이 삭제되었습니다.",
            "deleted_id": pk,
        })

    except Exception as error:
        return CBLJsonResponse({"ok": False, "message": str(error)}, status=500)
# CBL_CALENDAR_EDIT_DELETE_API_V2_END


# CBL_CALENDAR_EDIT_DELETE_API_V3_START
def _cbl_calendar_parse_payload_v3(request):
    """
    캘린더 수정 API 전용 파서
    """
    from datetime import datetime

    title = (request.POST.get("title") or request.POST.get("keyword") or "").strip()
    event_date_raw = (request.POST.get("event_date") or "").strip()
    end_date_raw = (request.POST.get("end_date") or "").strip()
    start_time_raw = (request.POST.get("start_time") or "").strip()
    end_time_raw = (request.POST.get("end_time") or "").strip()
    category = (request.POST.get("category") or "일정").strip()
    description = (request.POST.get("description") or "").strip()
    link_url = (request.POST.get("link_url") or "").strip()
    is_important = request.POST.get("is_important") in ("1", "true", "on", "yes")

    if not title:
        raise ValueError("일정명을 입력해 주세요.")

    if not event_date_raw:
        raise ValueError("시작일을 선택해 주세요.")

    try:
        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("시작일 형식이 올바르지 않습니다.")

    end_date = event_date
    if end_date_raw:
        try:
            end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("종료일 형식이 올바르지 않습니다.")

    if end_date < event_date:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")

    def parse_time(value):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError:
            return None

    return {
        "title": title,
        "event_date": event_date,
        "end_date": end_date,
        "start_time": parse_time(start_time_raw),
        "end_time": parse_time(end_time_raw),
        "category": category or "일정",
        "description": description,
        "link_url": link_url,
        "is_public": True,
        "is_important": is_important,
    }


@cbl_staff_member_required
@cbl_require_POST
def calendar_event_update_v3_api(request, pk):
    """
    캘린더 일정 수정 API V3
    """
    try:
        ev = get_object_or_404(CBLCalendarEvent, pk=pk)
        payload = _cbl_calendar_parse_payload_v3(request)

        for key, value in payload.items():
            setattr(ev, key, value)

        ev.save()

        return CBLJsonResponse({
            "ok": True,
            "message": "일정이 수정되었습니다.",
            "event": {
                "id": ev.id,
                "title": ev.title,
                "date": ev.event_date.isoformat(),
            },
        })

    except ValueError as error:
        return CBLJsonResponse({"ok": False, "message": str(error)}, status=400)

    except Exception as error:
        return CBLJsonResponse({"ok": False, "message": str(error)}, status=500)


@cbl_staff_member_required
@cbl_require_POST
def calendar_event_delete_v3_api(request, pk):
    """
    캘린더 일정 삭제 API V3
    """
    try:
        ev = get_object_or_404(CBLCalendarEvent, pk=pk)
        ev.delete()

        return CBLJsonResponse({
            "ok": True,
            "message": "일정이 삭제되었습니다.",
            "deleted_id": pk,
        })

    except Exception as error:
        return CBLJsonResponse({"ok": False, "message": str(error)}, status=500)
# CBL_CALENDAR_EDIT_DELETE_API_V3_END


# CBL_CONSTRUCTION_AI_CATEGORY_FINAL_LOCK_START
# 자동글 생성 시 화면 선택값은 건설 세부 카테고리로 저장하고,
# 글 생성 프롬프트는 기존 architecture/realestate 계열을 재사용합니다.
try:
    _cbl_prev_ai_post_generate_construction_final = ai_post_generate

    def _cbl_construction_norm_category(value, text=""):
        canonical, diagnostics = cbl_resolve_auto_post_category(
            value, title=text,
        )
        logger.info(
            "auto_post_category_request raw_category=%r normalized_before=%r "
            "canonical_category=%r legacy_mapping_used=%s fallback_reason=%s",
            value,
            diagnostics.get("normalized_before"),
            canonical,
            diagnostics.get("legacy_mapping_used"),
            diagnostics.get("fallback_reason") or "",
        )
        if canonical is None:
            raise ValueError("자동글 요청 카테고리를 안전하게 분류하지 못했습니다.")
        return canonical

    def _cbl_construction_generation_category(value, text=""):
        return _cbl_construction_norm_category(value, text)

    def _cbl_construction_extract_targets(request):
        import json
        targets = []
        raw = str(request.POST.get("cbl_keyword_rows", "") or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = []
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    keyword = str(item.get("keyword", "") or "").strip()
                    if not keyword:
                        continue
                    targets.append(_cbl_construction_norm_category(
                        item.get("category"), keyword,
                    ))
        if targets:
            return targets

        keywords = request.POST.getlist("cbl_row_keywords[]")
        categories = request.POST.getlist("cbl_row_categories[]")
        for idx, kw in enumerate(keywords):
            if not str(kw or "").strip():
                continue
            raw_cat = categories[idx] if idx < len(categories) else request.POST.get("category")
            targets.append(_cbl_construction_norm_category(raw_cat, kw))
        if targets:
            return targets

        return [_cbl_construction_norm_category(
            request.POST.get("cbl_force_category")
            or request.POST.get("auto_category")
            or request.POST.get("selected_category")
            or request.POST.get("post_category")
            or request.POST.get("category"),
            request.POST.get("keyword")
            or request.POST.get("title")
            or " ".join(request.POST.getlist("keywords[]")),
        )]

    def _cbl_construction_rewrite_post_for_generation(request):
        import json
        qd = request.POST.copy()
        request_text = (
            qd.get("keyword")
            or qd.get("title")
            or " ".join(qd.getlist("keywords[]"))
        )

        for name in (
            "category", "post_category", "post_category_slug", "selected_category",
            "ai_category", "auto_category", "cbl_locked_category", "cbl_force_category",
        ):
            if qd.get(name):
                qd[name] = _cbl_construction_generation_category(
                    qd.get(name), request_text,
                )

        raw = str(qd.get("cbl_keyword_rows", "") or "").strip()
        if raw:
            try:
                rows = json.loads(raw)
            except Exception:
                rows = []
            if isinstance(rows, list):
                for item in rows:
                    if isinstance(item, dict):
                        item["category"] = _cbl_construction_generation_category(
                            item.get("category"), item.get("keyword", ""),
                        )
                qd["cbl_keyword_rows"] = json.dumps(rows, ensure_ascii=False)

        for list_name in (
            "cbl_row_categories[]", "auto_categories[]", "auto_categories",
            "categories[]", "categories",
        ):
            values = qd.getlist(list_name)
            if values:
                qd.setlist(list_name, [
                    _cbl_construction_generation_category(value, request_text)
                    for value in values
                ])

        return qd

    def ai_post_generate(request, *args, **kwargs):
        if getattr(request, "method", "").upper() != "POST":
            return _cbl_prev_ai_post_generate_construction_final(request, *args, **kwargs)

        try:
            from .models import Post as _CBLPost
        except Exception:
            _CBLPost = None

        before_max_id = 0
        try:
            if _CBLPost is not None:
                before_max_id = _CBLPost.objects.order_by("-id").values_list("id", flat=True).first() or 0
        except Exception:
            before_max_id = 0

        targets = _cbl_construction_extract_targets(request)
        original_post = request.POST
        request.POST = _cbl_construction_rewrite_post_for_generation(request)

        try:
            response = _cbl_prev_ai_post_generate_construction_final(request, *args, **kwargs)
        finally:
            request.POST = original_post

        try:
            if _CBLPost is not None and targets:
                posts = list(_CBLPost.objects.filter(id__gt=before_max_id).order_by("id"))
                if posts:
                    for idx, post in enumerate(posts):
                        target = targets[min(idx, len(targets) - 1)]
                        if target in CBL_PUBLIC_CATEGORY_CODE_SET:
                            post.category = target
                            try:
                                post.save(update_fields=["category", "updated_at"])
                            except Exception:
                                post.save(update_fields=["category"])
        except Exception as error:
            print("CBL construction final category fix skipped:", error)

        return response
except Exception as _cbl_construction_ai_category_final_error:
    print("CBL_CONSTRUCTION_AI_CATEGORY_FINAL_LOCK load error:", _cbl_construction_ai_category_final_error)
# CBL_CONSTRUCTION_AI_CATEGORY_FINAL_LOCK_END


def community(request):
    return render(request, "core/community.html")


# CBL_COMMUNITY_QNA_VIEW_START
def community(request):
    from django.shortcuts import render, redirect
    from django.contrib import messages
    from django.db.models import Q
    from .models import CommunityQuestion

    if request.method == "POST":
        category = request.POST.get("category", "question").strip()
        title = request.POST.get("title", "").strip()
        body = request.POST.get("body", "").strip()
        author_name = request.POST.get("author_name", "").strip() or "익명"
        contact = request.POST.get("contact", "").strip()

        valid_categories = {"question", "error", "request", "faq"}
        if category not in valid_categories:
            category = "question"

        if not title or not body:
            messages.error(request, "제목과 문의 내용을 입력해주세요.")
            return redirect("community")

        CommunityQuestion.objects.create(
            category=category,
            title=title,
            body=body,
            author_name=author_name,
            contact=contact,
            is_public=True,
        )

        messages.success(request, "문의가 등록되었습니다. 답변은 확인 후 순차적으로 추가됩니다.")
        return redirect("community")

    keyword = request.GET.get("q", "").strip()
    category = request.GET.get("category", "all").strip()

    questions = CommunityQuestion.objects.filter(is_public=True)

    if category in {"question", "error", "request", "faq"}:
        questions = questions.filter(category=category)

    if keyword:
        questions = questions.filter(
            Q(title__icontains=keyword) |
            Q(body__icontains=keyword) |
            Q(answer__icontains=keyword) |
            Q(author_name__icontains=keyword)
        )

    return render(request, "core/community.html", {
        "questions": questions[:80],
        "keyword": keyword,
        "active_category": category,
    })
# CBL_COMMUNITY_QNA_VIEW_END


# CBL_NEW_CONTENT_CATEGORY_VIEW_PATCH_START
# 신규 콘텐츠 카테고리 표시 보강. 기존 글 호환을 위해 legacy category는 삭제하지 않는다.
try:
    CBL_CATEGORY_PAGE_LABELS = globals().get("CBL_CATEGORY_PAGE_LABELS", {})
    CBL_CATEGORY_PAGE_LABELS.update(CBL_CATEGORY_LABELS)
except Exception:
    pass

try:
    # BTP 포털형 카테고리 신규 추가
    CBL_BTP_PORTAL_CONFIG.update({
        "dynamo_automation": {
            "title": "Dynamo/자동화",
            "subtitle": "Dynamo, 자동화, 파라미터, 엑셀 연동, Python",
            "description": "Dynamo/자동화 컨텐츠를 다룹니다.",
            "search_placeholder": "Dynamo, 자동화, 파라미터, 엑셀 연동을 검색하세요",
            "main_title": "Dynamo 컨텐츠",
            "main_badge": "Dynamo",
            "main_empty": "Dynamo 컨텐츠가 아직 없습니다.",
            "sub_title": "자동화 실무",
            "sub_badge": "자동화",
            "sub_empty": "자동화 컨텐츠가 아직 없습니다.",
            "third_title": "Python/Excel 연동",
            "third_badge": "연동",
            "third_empty": "연동 컨텐츠가 아직 없습니다.",
            "video_title": "Dynamo 동영상/쇼츠",
            "video_badge": "Dynamo영상",
            "all_keywords": CBL_AI_CATEGORY_GUIDE["dynamo_automation"]["keywords"],
            "main_keywords": ["Dynamo", "다이나모", "노드", "파라미터"],
            "sub_keywords": ["자동화", "반복작업", "업무자동화", "BIM 자동화"],
            "third_keywords": ["Python", "엑셀", "Excel", "연동", "스크립트"],
        },
        "four_d_five_d": {
            "title": "4D/5D",
            "subtitle": "공정 시뮬레이션, 수량 연동, 원가 연동, 5D BIM",
            "description": "4D/5D 컨텐츠를 다룹니다.",
            "search_placeholder": "4D, 5D, Navisworks, 공정·원가 연동을 검색하세요",
            "main_title": "4D 컨텐츠",
            "main_badge": "4D",
            "main_empty": "4D 컨텐츠가 아직 없습니다.",
            "sub_title": "5D 컨텐츠",
            "sub_badge": "5D",
            "sub_empty": "5D 컨텐츠가 아직 없습니다.",
            "third_title": "공정·원가 연동",
            "third_badge": "연동",
            "third_empty": "공정·원가 연동 컨텐츠가 아직 없습니다.",
            "video_title": "4D/5D 동영상/쇼츠",
            "video_badge": "4D/5D영상",
            "all_keywords": CBL_AI_CATEGORY_GUIDE["four_d_five_d"]["keywords"],
            "main_keywords": ["4D", "공정", "시뮬레이션", "Navisworks"],
            "sub_keywords": ["5D", "원가", "수량", "BIM"],
            "third_keywords": ["공정 연동", "원가 연동", "수량 연동", "5D BIM"],
        },
        "tool_recommend": {
            "title": "툴소개/툴추천",
            "subtitle": "AI 도구, 생산성 도구, 무료/유료 툴 비교",
            "description": "툴소개/툴추천 컨텐츠를 다룹니다.",
            "search_placeholder": "AI 도구, 생산성 도구, 추천툴을 검색하세요",
            "main_title": "툴 소개",
            "main_badge": "툴소개",
            "main_empty": "툴 소개 컨텐츠가 아직 없습니다.",
            "sub_title": "추천툴",
            "sub_badge": "추천툴",
            "sub_empty": "추천툴 컨텐츠가 아직 없습니다.",
            "third_title": "업무 효율 툴",
            "third_badge": "효율툴",
            "third_empty": "업무 효율 툴 컨텐츠가 아직 없습니다.",
            "video_title": "툴 동영상/쇼츠",
            "video_badge": "툴영상",
            "all_keywords": CBL_AI_CATEGORY_GUIDE["tool_recommend"]["keywords"],
            "main_keywords": ["툴", "소개", "사용법", "리뷰"],
            "sub_keywords": ["추천툴", "툴 추천", "무료 툴", "유료 툴"],
            "third_keywords": ["생산성", "업무 효율", "AI 도구", "자동화 도구"],
        },
    })
except Exception:
    pass
# CBL_NEW_CONTENT_CATEGORY_VIEW_PATCH_END


# CBL_CHICKENBANANA_CUT_GENERATE_VIEW_START
# 치킨바나나컷 자동 초안 생성
# - 카테고리별 비공개 글 생성
# - 대본/이미지 컷 기반 MiniCapcutProject 생성
# - 생성 후 치킨바나나컷 편집기로 이동

try:
    _mini_capcut_admin_required
except NameError:
    def _mini_capcut_admin_required(user):
        return user.is_authenticated and (user.is_staff or user.is_superuser)


def _cbl_cut_escape_html(value):
    import html
    return html.escape(str(value or "").strip())


def _cbl_cut_category_label(category):
    labels = {
        "construction_work": "건설실무",
        "construction_tech": "건설기술",
        "construction_real": "건설부동산",
        "bim": "REVIT/BIM",
        "dynamo_automation": "Dynamo/자동화",
        "four_d_five_d": "4D/5D",
        "tech_ai_development": "AI·개발",
        "tech_data_security": "데이터·보안",
        "tech_server_software": "인터넷·서버·소프트",
        "program": "업무용 프로그램",
        "tool_recommend": "툴소개/툴추천",
    }
    return labels.get(category, "건설실무")


def _cbl_cut_svg_data_url(title, subtitle, scene_no):
    from urllib.parse import quote

    title = _cbl_cut_escape_html(title)[:42]
    subtitle = _cbl_cut_escape_html(subtitle)[:180]

    colors = [
        ("#111827", "#2563eb", "#f8fafc"),
        ("#172554", "#7c3aed", "#eef2ff"),
        ("#064e3b", "#16a34a", "#ecfdf5"),
        ("#3b0764", "#db2777", "#fdf2f8"),
        ("#1f2937", "#f97316", "#fff7ed"),
        ("#0f172a", "#0891b2", "#ecfeff"),
    ]

    bg, accent, panel = colors[(int(scene_no) - 1) % len(colors)]

    svg_parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920" viewBox="0 0 1080 1920">',
        '<defs>',
        f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">',
        f'<stop offset="0%" stop-color="{bg}"/>',
        f'<stop offset="100%" stop-color="{accent}"/>',
        '</linearGradient>',
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">',
        '<feDropShadow dx="0" dy="22" stdDeviation="28" flood-color="#000000" flood-opacity="0.28"/>',
        '</filter>',
        '</defs>',
        '<rect width="1080" height="1920" fill="url(#g)"/>',
        '<circle cx="920" cy="220" r="180" fill="#ffffff" opacity="0.10"/>',
        '<circle cx="140" cy="1640" r="260" fill="#ffffff" opacity="0.10"/>',
        f'<rect x="90" y="360" width="900" height="1050" rx="64" fill="{panel}" opacity="0.96" filter="url(#shadow)"/>',
        f'<text x="120" y="470" font-size="42" font-family="Apple SD Gothic Neo, Pretendard, Arial" font-weight="800" fill="{accent}">CHICKENBANANACUT</text>',
        f'<text x="120" y="585" font-size="86" font-family="Apple SD Gothic Neo, Pretendard, Arial" font-weight="900" fill="#111827">SCENE {scene_no}</text>',
        '<foreignObject x="120" y="700" width="840" height="520">',
        f'<div xmlns="http://www.w3.org/1999/xhtml" style="font-family:Apple SD Gothic Neo,Pretendard,Arial;font-size:58px;font-weight:900;line-height:1.22;color:#111827;word-break:keep-all;">{title}</div>',
        '</foreignObject>',
        '<foreignObject x="120" y="1180" width="840" height="180">',
        f'<div xmlns="http://www.w3.org/1999/xhtml" style="font-family:Apple SD Gothic Neo,Pretendard,Arial;font-size:30px;font-weight:700;line-height:1.45;color:#334155;word-break:keep-all;">{subtitle}</div>',
        '</foreignObject>',
        f'<rect x="120" y="1510" width="840" height="8" rx="4" fill="{accent}"/>',
        '<text x="120" y="1588" font-size="34" font-family="Apple SD Gothic Neo, Pretendard, Arial" font-weight="800" fill="#ffffff">글·이미지 기반 쇼츠 초안</text>',
        '</svg>',
    ]

    svg = "\n".join(svg_parts)
    return "data:image/svg+xml;charset=utf-8," + quote(svg)


def _cbl_cut_build_project_state(title, category, scripts, image_count):
    import uuid

    tracks = [
        {"id": "track_main", "name": "메인 영상", "type": "video", "muted": False, "locked": False, "hidden": False},
        {"id": "track_overlay_1", "name": "대본 자막", "type": "overlay", "muted": False, "locked": False, "hidden": False},
        {"id": "track_overlay_2", "name": "강조 문구", "type": "overlay", "muted": False, "locked": False, "hidden": False},
        {"id": "track_audio", "name": "대본 음성", "type": "audio", "muted": False, "locked": False, "hidden": False},
    ]

    assets = []
    clips = []

    category_label = _cbl_cut_category_label(category)

    try:
        scene_count = int(image_count or 5)
    except Exception:
        scene_count = 5

    scene_count = max(1, min(12, scene_count))

    if scripts:
        scene_count = max(scene_count, min(12, len(scripts)))

    duration = 4.5

    for i in range(scene_count):
        script = scripts[i % len(scripts)] if scripts else title
        scene_no = i + 1
        start = round(i * duration, 1)

        asset_id = uuid.uuid4().hex
        image_url = _cbl_cut_svg_data_url(
            title=f"{category_label} 쇼츠",
            subtitle=script,
            scene_no=scene_no,
        )

        assets.append({
            "id": asset_id,
            "name": f"AI 이미지 컷 {scene_no}",
            "url": image_url,
            "type": "image",
            "source": "ChickenBananaCut 자동 생성",
        })

        clips.append({
            "id": uuid.uuid4().hex,
            "assetId": asset_id,
            "type": "image",
            "name": f"이미지 컷 {scene_no}",
            "url": image_url,
            "trackId": "track_main",
            "start": start,
            "duration": duration,
            "speed": 1,
            "volume": 1,
            "transition": "fade",
            "text": "",
            "sourceOffset": 0,
        })

        clips.append({
            "id": uuid.uuid4().hex,
            "type": "text",
            "name": f"대본 자막 {scene_no}",
            "trackId": "track_overlay_1",
            "start": start,
            "duration": duration,
            "speed": 1,
            "volume": 1,
            "transition": "none",
            "text": script,
        })

        clips.append({
            "id": uuid.uuid4().hex,
            "type": "voice",
            "name": f"대본 음성 {scene_no}",
            "trackId": "track_audio",
            "start": start,
            "duration": max(3, min(8, round(len(script) / 13, 1))),
            "speed": 1,
            "volume": 1,
            "transition": "none",
            "text": script,
        })

    return {
        "assets": assets,
        "clips": clips,
        "tracks": tracks,
        "selectedClipId": clips[0]["id"] if clips else None,
        "currentTime": 0,
        "pxPerSec": 80,
        "chickenBananaCut": {
            "category": category,
            "categoryLabel": category_label,
            "title": title,
            "scripts": scripts,
            "imageCount": image_count,
            "status": "draft_project",
        },
    }


@login_required
@user_passes_test(_mini_capcut_admin_required)
def chickenbanana_cut_generate(request):
    from .models import Post, MiniCapcutProject
    from django.shortcuts import redirect
    import re

    if request.method != "POST":
        return redirect("mini_capcut_home")

    allowed_categories = {
        "construction_work",
        "construction_tech",
        "construction_real",
        "bim",
        "dynamo_automation",
        "four_d_five_d",
        "tech_ai_development",
        "tech_data_security",
        "tech_server_software",
        "program",
        "tool_recommend",
    }

    category = (request.POST.get("category") or "construction_work").strip()

    if category not in allowed_categories:
        category = "construction_work"

    try:
        image_count = int(request.POST.get("image_count") or 5)
    except Exception:
        image_count = 5

    image_count = max(1, min(12, image_count))

    title = (request.POST.get("title") or "").strip()

    scripts = []
    for value in request.POST.getlist("scripts"):
        value = str(value or "").strip()
        if value:
            scripts.append(value)

    raw_script = (request.POST.get("script_text") or "").strip()
    if raw_script:
        for line in re.split(r"\n+", raw_script):
            line = line.strip()
            if line:
                scripts.append(line)

    cleaned = []
    for s in scripts:
        if s not in cleaned:
            cleaned.append(s)

    scripts = cleaned[:12]

    if not scripts:
        scripts = [
            "첫 장면에서는 이 주제가 왜 중요한지 짧고 강하게 보여줍니다.",
            "두 번째 장면에서는 실무자가 바로 이해할 수 있는 핵심 기준을 설명합니다.",
            "마지막 장면에서는 실제 업무에 적용할 수 있는 체크포인트로 정리합니다.",
        ]

    if not title:
        title = scripts[0][:46]
        if len(scripts[0]) > 46:
            title += "..."

    category_label = _cbl_cut_category_label(category)

    content_lines = [
        "<h2>치킨바나나컷 쇼츠 대본</h2>",
        f"<p><strong>카테고리:</strong> {category_label}</p>",
        f"<p><strong>이미지 컷 수:</strong> {image_count}장</p>",
        "<hr>",
        "<h3>대본 구성</h3>",
        "<ol>",
    ]

    for script in scripts:
        content_lines.append(f"<li>{_cbl_cut_escape_html(script)}</li>")

    content_lines.extend([
        "</ol>",
        "<p>이 글은 치킨바나나컷 자동 생성 초안입니다. 편집기에서 이미지 컷, 자막, 음성 클립을 조정한 뒤 영상으로 저장하세요.</p>",
    ])

    is_draft = bool(request.POST.get("save_draft", "on"))

    post = Post.objects.create(
        category=category,
        title=title,
        content="\n".join(content_lines),
        summary=f"{category_label} 치킨바나나컷 쇼츠 초안입니다.",
        meta_description=f"{category_label} 주제로 생성한 치킨바나나컷 쇼츠 대본 및 편집 초안입니다.",
        tags=f"치킨바나나컷,쇼츠,자동영상,{category_label},{keyword}",
        is_published=not is_draft,
    )

    state = _cbl_cut_build_project_state(
        title=title,
        category=category,
        scripts=scripts,
        image_count=image_count,
    )

    MiniCapcutProject.objects.create(
        post=post,
        title=f"치킨바나나컷 - {title}",
        data=state,
    )

    return redirect("mini_capcut_editor", post_id=post.id)
# CBL_CHICKENBANANA_CUT_GENERATE_VIEW_END


# CBL_CHICKENBANANA_CUT_OPENAI_SCRIPT_START
# 치킨바나나컷: 선택한 오늘자 키워드를 OpenAI로 정리해 쇼츠 제목/대본 10개 생성

def _cbc_ai_category_label(category):
    labels = {
        "construction_work": "건설실무",
        "construction_tech": "건설기술",
        "construction_real": "건설부동산",
        "bim": "REVIT/BIM",
        "dynamo_automation": "Dynamo/자동화",
        "four_d_five_d": "4D/5D",
        "tech_ai_development": "AI·개발",
        "tech_data_security": "데이터·보안",
        "tech_server_software": "인터넷·서버·소프트",
        "program": "업무용 프로그램",
        "tool_recommend": "툴소개/툴추천",
    }
    return labels.get(category, "건설실무")


def _cbc_ai_normalize_category(category):
    category = str(category or "").strip()

    aliases = {
        "건설실무": "construction_work",
        "construction_work": "construction_work",
        "건설기술": "construction_tech",
        "construction_tech": "construction_tech",
        "건설부동산": "construction_real",
        "construction_real": "construction_real",
        "REVIT/BIM": "bim",
        "BIM": "bim",
        "bim": "bim",
        "Dynamo/자동화": "dynamo_automation",
        "Dynamo": "dynamo_automation",
        "다이나모": "dynamo_automation",
        "dynamo_automation": "dynamo_automation",
        "4D/5D": "four_d_five_d",
        "4D": "four_d_five_d",
        "5D": "four_d_five_d",
        "four_d_five_d": "four_d_five_d",
        "AI·개발": "tech_ai_development",
        "AI/개발": "tech_ai_development",
        "tech_ai_development": "tech_ai_development",
        "데이터·보안": "tech_data_security",
        "데이터/보안": "tech_data_security",
        "tech_data_security": "tech_data_security",
        "인터넷·서버·소프트": "tech_server_software",
        "인터넷/서버/소프트": "tech_server_software",
        "tech_server_software": "tech_server_software",
        "업무용 프로그램": "program",
        "프로그램": "program",
        "program": "program",
        "툴소개/툴추천": "tool_recommend",
        "툴추천": "tool_recommend",
        "추천툴": "tool_recommend",
        "tool_recommend": "tool_recommend",
    }

    return aliases.get(category, aliases.get(category.lower(), "construction_work"))


def _cbc_ai_fallback_scripts(keyword, category):
    category = _cbc_ai_normalize_category(category)
    label = _cbc_ai_category_label(category)

    return {
        "title": keyword,
        "category": category,
        "category_label": label,
        "scripts": [
            f"1. {keyword}, 그냥 넘기면 실무에서 놓치는 부분이 생길 수 있습니다.",
            f"2. 특히 {label}에서는 작은 기준 하나가 일정과 결과를 바꿀 수 있습니다.",
            f"3. 이 주제는 관련 업무를 하는 사람이 먼저 확인해야 할 핵심 포인트입니다.",
            f"4. 가장 흔한 실수는 자료를 많이 보면서도 판단 기준을 정하지 않는 것입니다.",
            f"5. 먼저 현재 업무에서 이 키워드가 어디에 연결되는지 확인해야 합니다.",
            f"6. 다음으로 도면, 문서, 데이터, 일정 중 어떤 기준이 필요한지 나눠봐야 합니다.",
            f"7. 여기서 중요한 건 복잡한 설명보다 바로 확인할 수 있는 체크포인트입니다.",
            f"8. 이 기준을 적용하면 반복 확인 시간을 줄이고 오류 가능성도 낮출 수 있습니다.",
            f"9. 정리하면 {keyword}는 {label}에서 바로 써먹을 수 있는 실무 주제입니다.",
            f"10. 오늘은 크게 시작하지 말고, 지금 업무에서 바로 확인할 한 가지 기준부터 적용해보면 됩니다.",
        ],
        "fallback": True,
    }


def _cbc_ai_extract_response_text(data):
    if not isinstance(data, dict):
        return ""

    if data.get("output_text"):
        return str(data.get("output_text") or "")

    chunks = []

    for output in data.get("output", []) or []:
        for content in output.get("content", []) or []:
            if isinstance(content, dict):
                if content.get("text"):
                    chunks.append(str(content.get("text") or ""))
                elif content.get("type") == "output_text" and content.get("text"):
                    chunks.append(str(content.get("text") or ""))

    return "\n".join(chunks).strip()


def _cbc_ai_parse_json_text(text):
    import json
    import re

    raw = str(text or "").strip()

    try:
        return json.loads(raw)
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    return None


@login_required
@user_passes_test(_mini_capcut_admin_required)
def chickenbanana_cut_ai_script(request):
    from django.http import JsonResponse
    import json
    import os
    import urllib.request

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    category = _cbc_ai_normalize_category(request.POST.get("category"))
    category_label = _cbc_ai_category_label(category)
    keyword = str(request.POST.get("keyword") or "").strip()
    source_text = str(request.POST.get("source_text") or "").strip()

    if not keyword:
        return JsonResponse({"ok": False, "error": "키워드가 없습니다."}, status=400)

    fallback = _cbc_ai_fallback_scripts(keyword, category)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not api_key:
        fallback["ok"] = True
        fallback["message"] = "OPENAI_API_KEY가 없어 기본 대본으로 생성했습니다."
        return JsonResponse(fallback)

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"

    system_prompt = (
        "너는 한국어 쇼츠 영상 대본 작가이자 건설/BIM/업무자동화 콘텐츠 편집자다. "
        "사용자가 선택한 키워드를 그대로 베끼지 말고, 쇼츠용으로 제목과 대본을 정리한다. "
        "반드시 JSON만 반환한다. 마크다운, 코드블록, 설명문은 쓰지 않는다."
    )

    user_prompt = {
        "category": category_label,
        "category_slug": category,
        "keyword": keyword,
        "source_text": source_text,
        "task": "15~45초 쇼츠 편집용 제목과 대본 10개를 생성",
        "rules": [
            "한국어로 작성",
            "대본은 정확히 10개",
            "각 대본은 1~2문장, 너무 길지 않게",
            "1번은 강한 후킹",
            "2번은 문제 제기",
            "3번은 현장/업무 상황",
            "4번은 자주 하는 실수",
            "5~6번은 체크포인트",
            "7번은 적용 방법",
            "8번은 기대 효과",
            "9번은 핵심 요약",
            "10번은 마무리 행동 유도",
            "과장, 투자 조언, 확인되지 않은 수치 금지",
            "뉴스 키워드라도 사실 단정하지 말고 실무 해설형으로 정리",
        ],
        "return_schema": {
            "title": "쇼츠 제목",
            "category": category,
            "category_label": category_label,
            "scripts": ["대본1", "대본2", "대본3", "대본4", "대본5", "대본6", "대본7", "대본8", "대본9", "대본10"],
        },
    }

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
        ],
        "max_output_tokens": 1600,
    }

    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=45) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        output_text = _cbc_ai_extract_response_text(response_data)
        parsed = _cbc_ai_parse_json_text(output_text)

        if not isinstance(parsed, dict):
            raise ValueError("OpenAI 응답 JSON 파싱 실패")

        title = str(parsed.get("title") or keyword).strip()
        scripts = parsed.get("scripts") or []

        scripts = [str(s or "").strip() for s in scripts if str(s or "").strip()]
        scripts = scripts[:10]

        if len(scripts) < 10:
            fallback_scripts = fallback["scripts"]
            for script in fallback_scripts:
                if len(scripts) >= 10:
                    break
                if script not in scripts:
                    scripts.append(script)

        return JsonResponse({
            "ok": True,
            "title": title,
            "category": category,
            "category_label": category_label,
            "scripts": scripts[:10],
            "fallback": False,
        })

    except Exception as error:
        print("[CBC_OPENAI_SCRIPT_ERROR]", type(error).__name__, str(error))
        fallback["ok"] = True
        fallback["message"] = f"OpenAI 호출 실패로 기본 대본을 사용했습니다: {type(error).__name__}"
        return JsonResponse(fallback)
# CBL_CHICKENBANANA_CUT_OPENAI_SCRIPT_END


# CBL_CALENDAR_CLEAN_DELETE_API_START
def _cbl_calendar_clean_parse_payload(request):
    """
    캘린더 수정용 공통 파서
    """
    from datetime import datetime

    title = (request.POST.get("title") or request.POST.get("keyword") or "").strip()
    event_date_raw = (request.POST.get("event_date") or "").strip()
    end_date_raw = (request.POST.get("end_date") or "").strip()
    start_time_raw = (request.POST.get("start_time") or "").strip()
    end_time_raw = (request.POST.get("end_time") or "").strip()
    category = (request.POST.get("category") or "일정").strip()
    description = (request.POST.get("description") or "").strip()
    link_url = (request.POST.get("link_url") or "").strip()
    is_important = request.POST.get("is_important") in ("1", "true", "on", "yes")

    if not title:
        raise ValueError("일정명을 입력해 주세요.")

    if not event_date_raw:
        raise ValueError("시작일을 선택해 주세요.")

    try:
        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("시작일 형식이 올바르지 않습니다.")

    end_date = event_date
    if end_date_raw:
        try:
            end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("종료일 형식이 올바르지 않습니다.")

    if end_date < event_date:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")

    def parse_time(value):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError:
            return None

    return {
        "title": title,
        "event_date": event_date,
        "end_date": end_date,
        "start_time": parse_time(start_time_raw),
        "end_time": parse_time(end_time_raw),
        "category": category or "일정",
        "description": description,
        "link_url": link_url,
        "is_public": True,
        "is_important": is_important,
    }


@cbl_staff_member_required
@cbl_require_POST
def calendar_event_update_clean_api(request, pk):
    """
    캘린더 일정 수정 API
    실제 모델명 CalendarEvent 기준
    """
    try:
        ev = get_object_or_404(CalendarEvent, pk=pk)
        payload = _cbl_calendar_clean_parse_payload(request)

        for key, value in payload.items():
            setattr(ev, key, value)

        ev.save()

        return CBLJsonResponse({
            "ok": True,
            "message": "일정이 수정되었습니다.",
            "event": {
                "id": ev.id,
                "title": ev.title,
                "date": ev.event_date.isoformat(),
            },
        })

    except ValueError as error:
        return CBLJsonResponse({"ok": False, "message": str(error)}, status=400)

    except Exception as error:
        return CBLJsonResponse({"ok": False, "message": str(error)}, status=500)


@cbl_staff_member_required
@cbl_require_POST
def calendar_event_delete_clean_api(request, pk):
    """
    캘린더 일정 삭제 API
    같은 제목/기간/시간/분류로 중복 등록된 일정까지 같이 정리합니다.
    """
    try:
        ev = get_object_or_404(CalendarEvent, pk=pk)

        same_qs = CalendarEvent.objects.filter(
            title=ev.title,
            event_date=ev.event_date,
            end_date=ev.end_date,
            start_time=ev.start_time,
            end_time=ev.end_time,
            category=ev.category,
        )

        deleted_count = same_qs.count()
        same_qs.delete()

        return CBLJsonResponse({
            "ok": True,
            "message": "일정이 삭제되었습니다.",
            "deleted_id": pk,
            "deleted_count": deleted_count,
        })

    except Exception as error:
        return CBLJsonResponse({"ok": False, "message": str(error)}, status=500)
# CBL_CALENDAR_CLEAN_DELETE_API_END


# CBL_CALENDAR_FINAL_DELETE_API_START
def _cbl_calendar_final_model():
    from django.apps import apps

    for model_name in ("CalendarEvent", "CBLCalendarEvent"):
        try:
            return apps.get_model("core", model_name)
        except LookupError:
            pass

    raise LookupError("CalendarEvent 모델을 찾지 못했습니다.")


def _cbl_calendar_final_json(data, status=200):
    from django.http import JsonResponse
    return JsonResponse(data, status=status)


def _cbl_calendar_final_is_staff(request):
    user = getattr(request, "user", None)
    return bool(
        user
        and user.is_authenticated
        and (user.is_staff or user.is_superuser)
    )


def calendar_event_delete_final_api(request, pk):
    """
    캘린더 일정 삭제 최종 API.
    같은 제목/기간/시간/분류로 중복 등록된 일정도 같이 삭제합니다.
    """
    if request.method != "POST":
        return _cbl_calendar_final_json(
            {"ok": False, "message": "POST 요청만 가능합니다."},
            status=405,
        )

    if not _cbl_calendar_final_is_staff(request):
        return _cbl_calendar_final_json(
            {"ok": False, "message": "관리자만 삭제할 수 있습니다."},
            status=403,
        )

    try:
        from django.shortcuts import get_object_or_404

        Model = _cbl_calendar_final_model()
        ev = get_object_or_404(Model, pk=pk)

        same_qs = Model.objects.filter(
            title=ev.title,
            event_date=ev.event_date,
            end_date=ev.end_date,
            start_time=ev.start_time,
            end_time=ev.end_time,
            category=ev.category,
        )

        if not same_qs.exists():
            same_qs = Model.objects.filter(pk=pk)

        deleted_count = same_qs.count()
        same_qs.delete()

        return _cbl_calendar_final_json({
            "ok": True,
            "message": "일정이 삭제되었습니다.",
            "deleted_id": pk,
            "deleted_count": deleted_count,
        })

    except Exception as error:
        return _cbl_calendar_final_json(
            {"ok": False, "message": str(error)},
            status=500,
        )


def calendar_event_update_final_api(request, pk):
    """
    캘린더 일정 수정 최종 API.
    """
    if request.method != "POST":
        return _cbl_calendar_final_json(
            {"ok": False, "message": "POST 요청만 가능합니다."},
            status=405,
        )

    if not _cbl_calendar_final_is_staff(request):
        return _cbl_calendar_final_json(
            {"ok": False, "message": "관리자만 수정할 수 있습니다."},
            status=403,
        )

    try:
        from datetime import datetime
        from django.shortcuts import get_object_or_404

        Model = _cbl_calendar_final_model()
        ev = get_object_or_404(Model, pk=pk)

        title = (request.POST.get("title") or "").strip()
        event_date_raw = (request.POST.get("event_date") or "").strip()
        end_date_raw = (request.POST.get("end_date") or "").strip()

        if not title:
            return _cbl_calendar_final_json(
                {"ok": False, "message": "일정명을 입력해 주세요."},
                status=400,
            )

        if not event_date_raw:
            return _cbl_calendar_final_json(
                {"ok": False, "message": "시작일을 선택해 주세요."},
                status=400,
            )

        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()

        if end_date_raw:
            end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date()
        else:
            end_date = event_date

        if end_date < event_date:
            return _cbl_calendar_final_json(
                {"ok": False, "message": "종료일은 시작일보다 빠를 수 없습니다."},
                status=400,
            )

        def parse_time(value):
            value = (value or "").strip()
            if not value:
                return None
            return datetime.strptime(value, "%H:%M").time()

        fields = {
            "title": title,
            "event_date": event_date,
            "end_date": end_date,
            "start_time": parse_time(request.POST.get("start_time")),
            "end_time": parse_time(request.POST.get("end_time")),
            "category": (request.POST.get("category") or "일정").strip(),
            "description": (request.POST.get("description") or "").strip(),
            "link_url": (request.POST.get("link_url") or "").strip(),
            "is_important": request.POST.get("is_important") in ("1", "true", "on", "yes"),
        }

        if hasattr(ev, "is_public"):
            fields["is_public"] = True

        for key, value in fields.items():
            if hasattr(ev, key):
                setattr(ev, key, value)

        ev.save()

        return _cbl_calendar_final_json({
            "ok": True,
            "message": "일정이 수정되었습니다.",
            "event": {
                "id": ev.id,
                "title": ev.title,
                "date": ev.event_date.isoformat(),
            },
        })

    except Exception as error:
        return _cbl_calendar_final_json(
            {"ok": False, "message": str(error)},
            status=500,
        )
# CBL_CALENDAR_FINAL_DELETE_API_END


# CBL_CALENDAR_FORCE_DELETE_API_START
from django.views.decorators.csrf import csrf_exempt as cbl_calendar_csrf_exempt

@cbl_calendar_csrf_exempt
def calendar_event_force_delete_api(request, pk):
    """
    캘린더 일정 강제 삭제 API.
    실제 모델 CalendarEvent 기준으로 삭제합니다.
    같은 제목/기간/시간/분류로 중복 등록된 일정도 함께 삭제합니다.
    """
    from django.http import JsonResponse
    from django.shortcuts import get_object_or_404
    from django.apps import apps

    if request.method != "POST":
        return JsonResponse({
            "ok": False,
            "message": "POST 요청만 가능합니다.",
        }, status=405)

    user = getattr(request, "user", None)
    if not (
        user
        and user.is_authenticated
        and (user.is_staff or user.is_superuser)
    ):
        return JsonResponse({
            "ok": False,
            "message": "관리자만 삭제할 수 있습니다.",
        }, status=403)

    try:
        CalendarModel = apps.get_model("core", "CalendarEvent")
        ev = get_object_or_404(CalendarModel, pk=pk)

        same_qs = CalendarModel.objects.filter(
            title=ev.title,
            event_date=ev.event_date,
            end_date=ev.end_date,
            start_time=ev.start_time,
            end_time=ev.end_time,
            category=ev.category,
        )

        if not same_qs.exists():
            same_qs = CalendarModel.objects.filter(pk=pk)

        deleted_ids = list(same_qs.values_list("id", flat=True))
        deleted_count = same_qs.count()
        same_qs.delete()

        return JsonResponse({
            "ok": True,
            "message": "일정이 삭제되었습니다.",
            "deleted_id": pk,
            "deleted_ids": deleted_ids,
            "deleted_count": deleted_count,
        })

    except Exception as error:
        return JsonResponse({
            "ok": False,
            "message": str(error),
        }, status=500)
# CBL_CALENDAR_FORCE_DELETE_API_END


# CBL_CALENDAR_REAL_ACTION_API_START
from django.views.decorators.csrf import csrf_exempt as cbl_calendar_real_csrf_exempt

def _cbl_calendar_real_staff_check(request):
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def _cbl_calendar_real_json(data, status=200):
    from django.http import JsonResponse
    return JsonResponse(data, status=status)


@cbl_calendar_real_csrf_exempt
def calendar_event_delete_real_api(request, pk):
    """
    캘린더 일정 삭제 전용 API.
    같은 제목/날짜/시간/분류로 중복 등록된 일정도 같이 삭제한다.
    """
    if request.method != "POST":
        return _cbl_calendar_real_json({"ok": False, "message": "POST 요청만 가능합니다."}, status=405)

    if not _cbl_calendar_real_staff_check(request):
        return _cbl_calendar_real_json({"ok": False, "message": "관리자만 삭제할 수 있습니다."}, status=403)

    try:
        from django.shortcuts import get_object_or_404
        from django.db.models import Q

        ev = get_object_or_404(CalendarEvent, pk=pk)

        same_qs = CalendarEvent.objects.filter(
            title=ev.title,
            event_date=ev.event_date,
            start_time=ev.start_time,
            end_time=ev.end_time,
            category=ev.category,
        ).filter(
            Q(end_date=ev.end_date) |
            Q(end_date__isnull=True) |
            Q(end_date=ev.event_date)
        )

        if not same_qs.exists():
            same_qs = CalendarEvent.objects.filter(pk=pk)

        deleted_ids = list(same_qs.values_list("id", flat=True))
        deleted_count = same_qs.count()
        same_qs.delete()

        return _cbl_calendar_real_json({
            "ok": True,
            "message": "일정이 삭제되었습니다.",
            "deleted_id": pk,
            "deleted_ids": deleted_ids,
            "deleted_count": deleted_count,
        })

    except Exception as error:
        return _cbl_calendar_real_json({"ok": False, "message": str(error)}, status=500)


@cbl_calendar_real_csrf_exempt
def calendar_event_update_real_api(request, pk):
    """
    캘린더 일정 수정 전용 API.
    """
    if request.method != "POST":
        return _cbl_calendar_real_json({"ok": False, "message": "POST 요청만 가능합니다."}, status=405)

    if not _cbl_calendar_real_staff_check(request):
        return _cbl_calendar_real_json({"ok": False, "message": "관리자만 수정할 수 있습니다."}, status=403)

    try:
        from datetime import datetime
        from django.shortcuts import get_object_or_404

        ev = get_object_or_404(CalendarEvent, pk=pk)

        title = (request.POST.get("title") or request.POST.get("keyword") or "").strip()
        event_date_raw = (request.POST.get("event_date") or "").strip()
        end_date_raw = (request.POST.get("end_date") or "").strip()

        if not title:
            return _cbl_calendar_real_json({"ok": False, "message": "일정명을 입력해 주세요."}, status=400)

        if not event_date_raw:
            return _cbl_calendar_real_json({"ok": False, "message": "시작일을 선택해 주세요."}, status=400)

        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()

        if end_date_raw:
            end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date()
        else:
            end_date = event_date

        if end_date < event_date:
            return _cbl_calendar_real_json({"ok": False, "message": "종료일은 시작일보다 빠를 수 없습니다."}, status=400)

        def parse_time(value):
            value = (value or "").strip()
            if not value:
                return None
            return datetime.strptime(value, "%H:%M").time()

        ev.title = title
        ev.event_date = event_date
        ev.end_date = end_date
        ev.start_time = None if is_all_day else parse_time(request.POST.get("start_time"))
        ev.end_time = None if is_all_day else parse_time(request.POST.get("end_time"))
        ev.category = (request.POST.get("category") or "일정").strip()
        ev.description = (request.POST.get("description") or "").strip()
        ev.link_url = (request.POST.get("link_url") or "").strip()
        ev.is_public = True
        ev.is_important = request.POST.get("is_important") in ("1", "true", "on", "yes")
        ev.is_all_day = is_all_day
        ev.event_color = event_color
        ev.save()

        return _cbl_calendar_real_json({
            "ok": True,
            "message": "일정이 수정되었습니다.",
            "event": {
                "id": ev.id,
                "title": ev.title,
                "date": ev.event_date.isoformat(),
            },
        })

    except Exception as error:
        return _cbl_calendar_real_json({"ok": False, "message": str(error)}, status=500)
# CBL_CALENDAR_REAL_ACTION_API_END

# CBL_CALENDAR_DELETE_ANCHOR_FINAL_START
def calendar_event_delete_now_view(request, pk):
    from django.http import HttpResponseForbidden
    from django.shortcuts import redirect
    from django.apps import apps

    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and (user.is_staff or user.is_superuser)):
        return HttpResponseForbidden("관리자만 삭제할 수 있습니다.")

    next_url = (request.GET.get("next") or "/").strip() or "/"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    CalendarEvent = apps.get_model("core", "CalendarEvent")

    try:
        ev = CalendarEvent.objects.get(pk=pk)
    except CalendarEvent.DoesNotExist:
        print(f"⚠️ CBL calendar delete ANCHOR: missing id={pk}")
        return redirect(next_url)

    same_qs = CalendarEvent.objects.filter(
        title=ev.title,
        event_date=ev.event_date,
        start_time=ev.start_time,
        end_time=ev.end_time,
        category=ev.category,
    )

    ids = list(same_qs.values_list("id", flat=True))
    deleted_count, _ = same_qs.delete()

    print(f"✅ CBL calendar delete ANCHOR: clicked_id={pk}, deleted={deleted_count}, ids={ids}, title={ev.title}")

    return redirect(next_url)
# CBL_CALENDAR_DELETE_ANCHOR_FINAL_END

# CBL_CALENDAR_REGISTER_COLOR_FIX_START
def _cbl_cal_bool_fix(value):
    return str(value or "").strip().lower() in ("1", "true", "on", "yes", "y")

def _cbl_cal_color_fix(value):
    raw = (value or "").strip() or "#2f9e97"
    if not raw.startswith("#"):
        raw = "#" + raw
    raw = raw[:7]
    if len(raw) != 7:
        return "#2f9e97"
    allowed = "0123456789abcdefABCDEF"
    if any(ch not in allowed for ch in raw[1:]):
        return "#2f9e97"
    return raw.lower()

def _cbl_cal_staff_fix(request):
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))

def _cbl_cal_payload_fix(request):
    from datetime import datetime
    from django.apps import apps

    title = (request.POST.get("title") or request.POST.get("keyword") or "").strip()
    event_date_raw = (request.POST.get("event_date") or "").strip()
    end_date_raw = (request.POST.get("end_date") or "").strip()
    is_all_day = _cbl_cal_bool_fix(request.POST.get("is_all_day"))

    if not title:
        raise ValueError("일정명을 입력해 주세요.")
    if not event_date_raw:
        raise ValueError("시작일을 선택해 주세요.")

    event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date() if end_date_raw else event_date

    if end_date < event_date:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")

    def parse_time(value):
        value = (value or "").strip()
        if is_all_day or not value:
            return None
        return datetime.strptime(value, "%H:%M").time()

    payload = {
        "title": title,
        "event_date": event_date,
        "end_date": end_date,
        "start_time": parse_time(request.POST.get("start_time")),
        "end_time": parse_time(request.POST.get("end_time")),
        "category": (request.POST.get("category") or "일정").strip() or "일정",
        "description": (request.POST.get("description") or "").strip(),
        "link_url": (request.POST.get("link_url") or "").strip(),
        "is_public": True,
        "is_important": _cbl_cal_bool_fix(request.POST.get("is_important")),
    }

    CalendarEvent = apps.get_model("core", "CalendarEvent")
    field_names = {f.name for f in CalendarEvent._meta.fields}

    if "is_all_day" in field_names:
        payload["is_all_day"] = is_all_day

    if "event_color" in field_names:
        payload["event_color"] = _cbl_cal_color_fix(request.POST.get("event_color"))

    return payload

def _cbl_cal_date_label_fix(ev):
    end_date = ev.end_date or ev.event_date
    if end_date == ev.event_date:
        return f"{ev.event_date.day}일"
    if end_date.month == ev.event_date.month:
        return f"{ev.event_date.day}일~{end_date.day}일"
    return f"{ev.event_date.month}/{ev.event_date.day}~{end_date.month}/{end_date.day}"

def _cbl_cal_event_dict_fix(ev):
    end_date = ev.end_date or ev.event_date
    is_all_day = bool(getattr(ev, "is_all_day", False))

    return {
        "id": ev.id,
        "title": ev.title,
        "date": ev.event_date.isoformat(),
        "end_date": end_date.isoformat(),
        "day": ev.event_date.day,
        "end_day": end_date.day,
        "date_label": _cbl_cal_date_label_fix(ev),
        "start_time": "" if is_all_day else (ev.start_time.strftime("%H:%M") if ev.start_time else ""),
        "end_time": "" if is_all_day else (ev.end_time.strftime("%H:%M") if ev.end_time else ""),
        "category": ev.category or "일정",
        "description": ev.description or "",
        "link_url": ev.link_url or "",
        "is_important": ev.is_important,
        "is_all_day": is_all_day,
        "event_color": getattr(ev, "event_color", "#2f9e97") or "#2f9e97",
    }

def calendar_events_month_api(request):
    from datetime import date
    from django.apps import apps
    from django.db.models import Q
    from django.http import JsonResponse
    from django.utils import timezone
    import calendar as py_calendar

    CalendarEvent = apps.get_model("core", "CalendarEvent")
    today = timezone.localdate()

    try:
        year = int(request.GET.get("year") or today.year)
        month = int(request.GET.get("month") or today.month)
    except Exception:
        year, month = today.year, today.month

    first_day = date(year, month, 1)
    last_day = date(year, month, py_calendar.monthrange(year, month)[1])

    qs = (
        CalendarEvent.objects
        .filter(is_public=True)
        .filter(
            Q(event_date__range=(first_day, last_day)) |
            Q(event_date__lte=last_day, end_date__gte=first_day)
        )
        .order_by("event_date", "start_time", "id")
    )

    events = []
    seen = set()

    for ev in qs:
        end_date = ev.end_date or ev.event_date
        key = (
            ev.title,
            ev.event_date,
            end_date,
            ev.start_time,
            ev.end_time,
            ev.category,
            getattr(ev, "is_all_day", False),
            getattr(ev, "event_color", "#2f9e97"),
        )
        if key in seen:
            continue
        seen.add(key)
        events.append(_cbl_cal_event_dict_fix(ev))

    return JsonResponse({
        "year": year,
        "month": month,
        "today": today.isoformat(),
        "events": events,
    })

from django.views.decorators.csrf import csrf_exempt as cbl_cal_fix_csrf_exempt

@cbl_cal_fix_csrf_exempt
def calendar_event_create_api(request):
    from django.apps import apps
    from django.http import JsonResponse

    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "POST 요청만 가능합니다."}, status=405)

    if not _cbl_cal_staff_fix(request):
        return JsonResponse({"ok": False, "message": "관리자만 등록할 수 있습니다."}, status=403)

    try:
        CalendarEvent = apps.get_model("core", "CalendarEvent")
        ev = CalendarEvent.objects.create(**_cbl_cal_payload_fix(request))
        print(f"✅ CBL calendar create COLOR FIX: id={ev.id}, title={ev.title}, color={getattr(ev, 'event_color', '')}, all_day={getattr(ev, 'is_all_day', False)}")
        return JsonResponse({
            "ok": True,
            "message": "일정이 등록되었습니다.",
            "event": _cbl_cal_event_dict_fix(ev),
        })
    except Exception as error:
        print(f"❌ CBL calendar create COLOR FIX error: {error}")
        return JsonResponse({"ok": False, "message": str(error)}, status=500)

@cbl_cal_fix_csrf_exempt
def calendar_event_update_real_api(request, pk):
    from django.apps import apps
    from django.http import JsonResponse
    from django.shortcuts import get_object_or_404

    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "POST 요청만 가능합니다."}, status=405)

    if not _cbl_cal_staff_fix(request):
        return JsonResponse({"ok": False, "message": "관리자만 수정할 수 있습니다."}, status=403)

    try:
        CalendarEvent = apps.get_model("core", "CalendarEvent")
        ev = get_object_or_404(CalendarEvent, pk=pk)

        payload = _cbl_cal_payload_fix(request)
        for key, value in payload.items():
            setattr(ev, key, value)

        ev.save()
        print(f"✅ CBL calendar update COLOR FIX: id={ev.id}, title={ev.title}, color={getattr(ev, 'event_color', '')}, all_day={getattr(ev, 'is_all_day', False)}")
        return JsonResponse({
            "ok": True,
            "message": "일정이 수정되었습니다.",
            "event": _cbl_cal_event_dict_fix(ev),
        })
    except Exception as error:
        print(f"❌ CBL calendar update COLOR FIX error: {error}")
        return JsonResponse({"ok": False, "message": str(error)}, status=500)
# CBL_CALENDAR_REGISTER_COLOR_FIX_END

# CBL_CALENDAR_REAL_CONNECTED_BAR_API_START
def _cbl_calendar_bar_bool(value):
    return str(value or "").strip().lower() in ("1", "true", "on", "yes", "y")

def _cbl_calendar_bar_color(value):
    raw = (value or "").strip() or "#2f9e97"
    if not raw.startswith("#"):
        raw = "#" + raw
    raw = raw[:7]
    if len(raw) != 7:
        return "#2f9e97"
    allowed = "0123456789abcdefABCDEF"
    if any(ch not in allowed for ch in raw[1:]):
        return "#2f9e97"
    return raw.lower()

def _cbl_calendar_bar_staff(request):
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))

def _cbl_calendar_bar_payload(request):
    from datetime import datetime
    from django.apps import apps

    title = (request.POST.get("title") or request.POST.get("keyword") or "").strip()
    event_date_raw = (request.POST.get("event_date") or "").strip()
    end_date_raw = (request.POST.get("end_date") or "").strip()
    is_all_day = _cbl_calendar_bar_bool(request.POST.get("is_all_day"))

    if not title:
        raise ValueError("일정명을 입력해 주세요.")
    if not event_date_raw:
        raise ValueError("시작일을 선택해 주세요.")

    event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date() if end_date_raw else event_date

    if end_date < event_date:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")

    def parse_time(value):
        value = (value or "").strip()
        if is_all_day or not value:
            return None
        return datetime.strptime(value, "%H:%M").time()

    payload = {
        "title": title,
        "event_date": event_date,
        "end_date": end_date,
        "start_time": parse_time(request.POST.get("start_time")),
        "end_time": parse_time(request.POST.get("end_time")),
        "category": (request.POST.get("category") or "일정").strip() or "일정",
        "description": (request.POST.get("description") or "").strip(),
        "link_url": (request.POST.get("link_url") or "").strip(),
        "is_public": True,
        "is_important": _cbl_calendar_bar_bool(request.POST.get("is_important")),
    }

    CalendarEvent = apps.get_model("core", "CalendarEvent")
    field_names = {f.name for f in CalendarEvent._meta.fields}

    if "is_all_day" in field_names:
        payload["is_all_day"] = is_all_day
    if "event_color" in field_names:
        payload["event_color"] = _cbl_calendar_bar_color(request.POST.get("event_color"))

    return payload

def _cbl_calendar_bar_date_label(ev):
    end_date = ev.end_date or ev.event_date

    if end_date == ev.event_date:
        return f"{ev.event_date.day}일"

    if end_date.month == ev.event_date.month:
        return f"{ev.event_date.day}일~{end_date.day}일"

    return f"{ev.event_date.month}/{ev.event_date.day}~{end_date.month}/{end_date.day}"

def _cbl_calendar_bar_event_dict(ev):
    end_date = ev.end_date or ev.event_date
    is_all_day = bool(getattr(ev, "is_all_day", False))

    return {
        "id": ev.id,
        "title": ev.title,
        "date": ev.event_date.isoformat(),
        "end_date": end_date.isoformat(),
        "day": ev.event_date.day,
        "end_day": end_date.day,
        "date_label": _cbl_calendar_bar_date_label(ev),
        "start_time": "" if is_all_day else (ev.start_time.strftime("%H:%M") if ev.start_time else ""),
        "end_time": "" if is_all_day else (ev.end_time.strftime("%H:%M") if ev.end_time else ""),
        "category": ev.category or "일정",
        "description": ev.description or "",
        "link_url": ev.link_url or "",
        "is_important": ev.is_important,
        "is_all_day": is_all_day,
        "event_color": getattr(ev, "event_color", "#2f9e97") or "#2f9e97",
    }

def calendar_events_month_api(request):
    from datetime import date
    from django.apps import apps
    from django.db.models import Q
    from django.http import JsonResponse
    from django.utils import timezone
    import calendar as py_calendar

    CalendarEvent = apps.get_model("core", "CalendarEvent")
    today = timezone.localdate()

    try:
        year = int(request.GET.get("year") or today.year)
        month = int(request.GET.get("month") or today.month)
    except Exception:
        year, month = today.year, today.month

    first_day = date(year, month, 1)
    last_day = date(year, month, py_calendar.monthrange(year, month)[1])

    qs = (
        CalendarEvent.objects
        .filter(is_public=True)
        .filter(
            Q(event_date__range=(first_day, last_day)) |
            Q(event_date__lte=last_day, end_date__gte=first_day)
        )
        .order_by("event_date", "start_time", "id")
    )

    events = []
    seen = set()

    for ev in qs:
        end_date = ev.end_date or ev.event_date
        key = (
            ev.title,
            ev.event_date,
            end_date,
            ev.start_time,
            ev.end_time,
            ev.category,
            getattr(ev, "is_all_day", False),
            getattr(ev, "event_color", "#2f9e97"),
        )
        if key in seen:
            continue
        seen.add(key)
        events.append(_cbl_calendar_bar_event_dict(ev))

    return JsonResponse({
        "year": year,
        "month": month,
        "today": today.isoformat(),
        "events": events,
    })

from django.views.decorators.csrf import csrf_exempt as cbl_calendar_bar_csrf_exempt

@cbl_calendar_bar_csrf_exempt
def calendar_event_create_api(request):
    from django.apps import apps
    from django.http import JsonResponse

    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "POST 요청만 가능합니다."}, status=405)

    if not _cbl_calendar_bar_staff(request):
        return JsonResponse({"ok": False, "message": "관리자만 등록할 수 있습니다."}, status=403)

    try:
        CalendarEvent = apps.get_model("core", "CalendarEvent")
        ev = CalendarEvent.objects.create(**_cbl_calendar_bar_payload(request))
        print(f"✅ CBL calendar create BAR: id={ev.id}, title={ev.title}, color={getattr(ev, 'event_color', '')}, all_day={getattr(ev, 'is_all_day', False)}")
        return JsonResponse({
            "ok": True,
            "message": "일정이 등록되었습니다.",
            "event": _cbl_calendar_bar_event_dict(ev),
        })
    except Exception as error:
        print(f"❌ CBL calendar create BAR error: {error}")
        return JsonResponse({"ok": False, "message": str(error)}, status=500)

@cbl_calendar_bar_csrf_exempt
def calendar_event_update_real_api(request, pk):
    from django.apps import apps
    from django.http import JsonResponse
    from django.shortcuts import get_object_or_404

    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "POST 요청만 가능합니다."}, status=405)

    if not _cbl_calendar_bar_staff(request):
        return JsonResponse({"ok": False, "message": "관리자만 수정할 수 있습니다."}, status=403)

    try:
        CalendarEvent = apps.get_model("core", "CalendarEvent")
        ev = get_object_or_404(CalendarEvent, pk=pk)
        payload = _cbl_calendar_bar_payload(request)

        for key, value in payload.items():
            setattr(ev, key, value)

        ev.save()
        print(f"✅ CBL calendar update BAR: id={ev.id}, title={ev.title}, color={getattr(ev, 'event_color', '')}, all_day={getattr(ev, 'is_all_day', False)}")
        return JsonResponse({
            "ok": True,
            "message": "일정이 수정되었습니다.",
            "event": _cbl_calendar_bar_event_dict(ev),
        })
    except Exception as error:
        print(f"❌ CBL calendar update BAR error: {error}")
        return JsonResponse({"ok": False, "message": str(error)}, status=500)
# CBL_CALENDAR_REAL_CONNECTED_BAR_API_END

# CBL_CALENDAR_OVERLAY_BAR_FINAL_API_START
def _cbl_overlay_cal_bool(value):
    return str(value or "").strip().lower() in ("1", "true", "on", "yes", "y")

def _cbl_overlay_cal_color(value):
    raw = (value or "").strip() or "#2f9e97"
    if not raw.startswith("#"):
        raw = "#" + raw
    raw = raw[:7]
    if len(raw) != 7:
        return "#2f9e97"
    allowed = "0123456789abcdefABCDEF"
    if any(ch not in allowed for ch in raw[1:]):
        return "#2f9e97"
    return raw.lower()

def _cbl_overlay_cal_staff(request):
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))

def _cbl_overlay_cal_payload(request):
    from datetime import datetime
    from django.apps import apps

    title = (request.POST.get("title") or request.POST.get("keyword") or "").strip()
    event_date_raw = (request.POST.get("event_date") or "").strip()
    end_date_raw = (request.POST.get("end_date") or "").strip()
    is_all_day = _cbl_overlay_cal_bool(request.POST.get("is_all_day"))

    if not title:
        raise ValueError("일정명을 입력해 주세요.")
    if not event_date_raw:
        raise ValueError("시작일을 선택해 주세요.")

    event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date() if end_date_raw else event_date

    if end_date < event_date:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")

    def parse_time(value):
        value = (value or "").strip()
        if is_all_day or not value:
            return None
        return datetime.strptime(value, "%H:%M").time()

    payload = {
        "title": title,
        "event_date": event_date,
        "end_date": end_date,
        "start_time": parse_time(request.POST.get("start_time")),
        "end_time": parse_time(request.POST.get("end_time")),
        "category": (request.POST.get("category") or "일정").strip() or "일정",
        "description": (request.POST.get("description") or "").strip(),
        "link_url": (request.POST.get("link_url") or "").strip(),
        "is_public": True,
        "is_important": _cbl_overlay_cal_bool(request.POST.get("is_important")),
    }

    CalendarEvent = apps.get_model("core", "CalendarEvent")
    field_names = {f.name for f in CalendarEvent._meta.fields}

    if "is_all_day" in field_names:
        payload["is_all_day"] = is_all_day
    if "event_color" in field_names:
        payload["event_color"] = _cbl_overlay_cal_color(request.POST.get("event_color"))

    return payload

def _cbl_overlay_cal_date_label(ev):
    end_date = ev.end_date or ev.event_date

    if end_date == ev.event_date:
        return f"{ev.event_date.day}일"

    if end_date.month == ev.event_date.month:
        return f"{ev.event_date.day}일~{end_date.day}일"

    return f"{ev.event_date.month}/{ev.event_date.day}~{end_date.month}/{end_date.day}"

def _cbl_overlay_cal_event_dict(ev):
    end_date = ev.end_date or ev.event_date
    is_all_day = bool(getattr(ev, "is_all_day", False))

    return {
        "id": ev.id,
        "title": ev.title,
        "date": ev.event_date.isoformat(),
        "end_date": end_date.isoformat(),
        "day": ev.event_date.day,
        "end_day": end_date.day,
        "date_label": _cbl_overlay_cal_date_label(ev),
        "start_time": "" if is_all_day else (ev.start_time.strftime("%H:%M") if ev.start_time else ""),
        "end_time": "" if is_all_day else (ev.end_time.strftime("%H:%M") if ev.end_time else ""),
        "category": ev.category or "일정",
        "description": ev.description or "",
        "link_url": ev.link_url or "",
        "is_important": ev.is_important,
        "is_all_day": is_all_day,
        "event_color": getattr(ev, "event_color", "#2f9e97") or "#2f9e97",
    }

def calendar_events_month_api(request):
    from datetime import date
    from django.apps import apps
    from django.db.models import Q
    from django.http import JsonResponse
    from django.utils import timezone
    import calendar as py_calendar

    CalendarEvent = apps.get_model("core", "CalendarEvent")
    today = timezone.localdate()

    try:
        year = int(request.GET.get("year") or today.year)
        month = int(request.GET.get("month") or today.month)
    except Exception:
        year, month = today.year, today.month

    first_day = date(year, month, 1)
    last_day = date(year, month, py_calendar.monthrange(year, month)[1])

    qs = (
        CalendarEvent.objects
        .filter(is_public=True)
        .filter(
            Q(event_date__range=(first_day, last_day)) |
            Q(event_date__lte=last_day, end_date__gte=first_day)
        )
        .order_by("event_date", "start_time", "id")
    )

    events = []
    seen = set()

    for ev in qs:
        end_date = ev.end_date or ev.event_date
        key = (
            ev.title,
            ev.event_date,
            end_date,
            ev.start_time,
            ev.end_time,
            ev.category,
            getattr(ev, "is_all_day", False),
            getattr(ev, "event_color", "#2f9e97"),
        )
        if key in seen:
            continue
        seen.add(key)
        events.append(_cbl_overlay_cal_event_dict(ev))

    return JsonResponse({
        "year": year,
        "month": month,
        "today": today.isoformat(),
        "events": events,
    })

from django.views.decorators.csrf import csrf_exempt as cbl_overlay_cal_csrf_exempt

@cbl_overlay_cal_csrf_exempt
def calendar_event_create_api(request):
    from django.apps import apps
    from django.http import JsonResponse

    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "POST 요청만 가능합니다."}, status=405)

    if not _cbl_overlay_cal_staff(request):
        return JsonResponse({"ok": False, "message": "관리자만 등록할 수 있습니다."}, status=403)

    try:
        CalendarEvent = apps.get_model("core", "CalendarEvent")
        ev = CalendarEvent.objects.create(**_cbl_overlay_cal_payload(request))
        print(f"✅ CBL calendar create OVERLAY: id={ev.id}, title={ev.title}, color={getattr(ev, 'event_color', '')}, all_day={getattr(ev, 'is_all_day', False)}")
        return JsonResponse({
            "ok": True,
            "message": "일정이 등록되었습니다.",
            "event": _cbl_overlay_cal_event_dict(ev),
        })
    except Exception as error:
        print(f"❌ CBL calendar create OVERLAY error: {error}")
        return JsonResponse({"ok": False, "message": str(error)}, status=500)

@cbl_overlay_cal_csrf_exempt
def calendar_event_update_real_api(request, pk):
    from django.apps import apps
    from django.http import JsonResponse
    from django.shortcuts import get_object_or_404

    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "POST 요청만 가능합니다."}, status=405)

    if not _cbl_overlay_cal_staff(request):
        return JsonResponse({"ok": False, "message": "관리자만 수정할 수 있습니다."}, status=403)

    try:
        CalendarEvent = apps.get_model("core", "CalendarEvent")
        ev = get_object_or_404(CalendarEvent, pk=pk)
        payload = _cbl_overlay_cal_payload(request)

        for key, value in payload.items():
            setattr(ev, key, value)

        ev.save()
        print(f"✅ CBL calendar update OVERLAY: id={ev.id}, title={ev.title}, color={getattr(ev, 'event_color', '')}, all_day={getattr(ev, 'is_all_day', False)}")
        return JsonResponse({
            "ok": True,
            "message": "일정이 수정되었습니다.",
            "event": _cbl_overlay_cal_event_dict(ev),
        })
    except Exception as error:
        print(f"❌ CBL calendar update OVERLAY error: {error}")
        return JsonResponse({"ok": False, "message": str(error)}, status=500)
# CBL_CALENDAR_OVERLAY_BAR_FINAL_API_END


# CBL_WEBCAD_TOOL_START
@login_required
def webcad_tool(request):
    # The product free mode is explicit in the URL.  The environment flag is
    # still accepted for existing local development sessions.
    local_free_mode = _cbl_is_safe_local_free_dwg_request(request)
    browser_free_mode = _cbl_is_browser_free_dwg_request(request)
    free_mode = local_free_mode or browser_free_mode
    if (not free_mode and request.GET.get("mode") != "free-dwg"
            and bool(getattr(getattr(request, "user", None), "is_authenticated", False))):
        # Without the mode the editor opened and saved DWG through the ODA
        # routes, which are gone (cblcad_oda_removed_api): use the free mode.
        query = request.GET.copy()
        query["mode"] = "free-dwg"
        return redirect(request.path + "?" + query.urlencode())
    if free_mode:
        from pathlib import Path
        from django.conf import settings as _cbl_settings

        path = Path(_cbl_settings.BASE_DIR) / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html"
        if path.exists():
            html = path.read_text(encoding="utf-8", errors="ignore")
            runtime = json.dumps({
                "freeDwgLocal": True,
                "freeDwgSaveLocal": bool(local_free_mode),
                "freeDwgBrowser": bool(browser_free_mode),
            }, separators=(",", ":"))
            early_route = """
<script>
(function(){
  function isFreeDwgButton(el){
    if(!el) return false;
    var t=String(el.innerText||el.textContent||'').replace(/\\s+/g,'');
    var title=String(el.getAttribute&&el.getAttribute('title')||'').replace(/\\s+/g,'');
    return (/DWG열기|DWG\\/DXF열기|DWG파일/.test(t)||title==='DWG열기'||title==='DWG/DXF열기')&&!/V29|저장/.test(t+title);
  }
  function bindFreeDwgButton(){
    if(!window.CBL_CAD_RUNTIME_CONFIG||window.CBL_CAD_RUNTIME_CONFIG.freeDwgLocal!==true||window.CBL_CAD_RUNTIME_CONFIG.freeDwgBrowser===true) return;
    var nodes=Array.prototype.slice.call(document.querySelectorAll('button,a,[role="button"],input[type="button"]'));
    nodes.forEach(function(original){
      if(!isFreeDwgButton(original)||original.getAttribute('data-cbl-free-open-bound')==='1') return;
      var button=original.cloneNode(true);
      button.setAttribute('data-cbl-free-open-bound','1');
      button.onclick=null;
      button.removeAttribute('onclick');
      button.addEventListener('click',function(ev){
        ev.preventDefault(); ev.stopPropagation(); if(ev.stopImmediatePropagation) ev.stopImmediatePropagation();
        var input=document.getElementById('cblOpenDwgInput');
        if(!input||typeof window.cblFreeDwgLocalOpenFileV1!=='function') return;
        var clean=input.cloneNode(true); input.parentNode.replaceChild(clean,input); input=clean;
        var onChange=function(change){
          change.preventDefault(); if(change.stopImmediatePropagation) change.stopImmediatePropagation();
          input.removeEventListener('change',onChange,true);
          var file=input.files&&input.files[0]; if(file) window.cblFreeDwgLocalOpenFileV1(file);
        };
        input.addEventListener('change',onChange,true); input.click();
      },true);
      original.parentNode.replaceChild(button,original);
    });
  }
  document.addEventListener('DOMContentLoaded',bindFreeDwgButton,{once:true});
})();
</script>
"""
            html = html.replace(
                "<head>",
                "<head><script>window.CBL_CAD_RUNTIME_CONFIG=" + runtime + ";</script>",
                1,
            )
            beta_notice = r'''
<style id="cbl-cad-beta-notice-style">
#cblCadBetaNoticeV1{position:fixed;top:14px;right:18px;z-index:2147483000;display:none;max-width:min(420px,calc(100vw - 28px));box-sizing:border-box;padding:13px 15px;border:1px solid rgba(91,145,245,.55);border-radius:8px;background:rgba(18,27,43,.96);box-shadow:0 12px 32px rgba(0,0,0,.36);color:#d7e5fa;font:12px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
#cblCadBetaNoticeV1 .cbl-cad-beta-notice-row{display:flex;align-items:flex-start;gap:12px}
#cblCadBetaNoticeV1 .cbl-cad-beta-notice-copy{flex:1;min-width:0}
#cblCadBetaNoticeV1 .cbl-cad-beta-notice-title{margin:0 0 4px;color:#fff;font-weight:700;font-size:12px}
#cblCadBetaNoticeV1 .cbl-cad-beta-notice-text{margin:0;color:#aab5c5;word-break:keep-all}
#cblCadBetaNoticeV1 button{flex:0 0 auto;border:1px solid #3f78d8;border-radius:5px;background:rgba(60,120,230,.16);color:#d7e5fa;padding:4px 9px;cursor:pointer;font:inherit}
#cblCadBetaNoticeV1 button:hover{background:rgba(60,120,230,.3)}
@media(max-width:640px){#cblCadBetaNoticeV1{top:8px;right:8px;left:8px;max-width:none}}
</style>
<div id="cblCadBetaNoticeV1" role="status" aria-live="polite">
  <div class="cbl-cad-beta-notice-row">
    <div class="cbl-cad-beta-notice-copy"><p class="cbl-cad-beta-notice-title">ChickenBananaCAD 베타 안내</p><p class="cbl-cad-beta-notice-text">현재 ChickenBananaCAD는 베타 버전으로 운영 중입니다. 작업 전 원본 도면을 별도로 보관해 주세요.</p></div>
    <button type="button" id="cblCadBetaNoticeCloseV1" aria-label="베타 안내 닫기">확인</button>
  </div>
</div>
<script>
(function(){
  var key='cblcad-beta-notice-dismissed-v1',box=document.getElementById('cblCadBetaNoticeV1'),close=document.getElementById('cblCadBetaNoticeCloseV1');
  if(!box||!close)return;
  var dismissed=false;try{dismissed=sessionStorage.getItem(key)==='1';}catch(e){}
  if(!dismissed)box.style.display='block';
  close.addEventListener('click',function(){try{sessionStorage.setItem(key,'1');}catch(e){}box.style.display='none';});
})();
</script>
'''
            # The CAD HTML contains print-preview strings with literal </body>
            # fragments.  Inject into the document's final closing tag, not
            # the first string occurrence inside an inline script.
            html_parts = html.rsplit("</body>", 1)
            if len(html_parts) == 2:
                html = html_parts[0] + beta_notice + "</body>" + html_parts[1]
            response = HttpResponse(html, content_type="text/html; charset=utf-8")
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
            return response

    # /tools/cad/ without the explicit mode must still use the canonical CAD
    # document.  Falling back to the legacy cblcad_ver1.html made authenticated
    # navigation display an older editor after login.
    from pathlib import Path
    from django.conf import settings as _cbl_settings
    canonical_path = Path(_cbl_settings.BASE_DIR) / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html"
    if canonical_path.exists():
        response = HttpResponse(
            canonical_path.read_text(encoding="utf-8", errors="ignore"),
            content_type="text/html; charset=utf-8",
        )
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response
    return render(request, "core/tools/cblcad_ver1.html")
# CBL_WEBCAD_TOOL_END


# CBL_CAD_DIRECT_VIEW_START
@login_required
def cblcad_direct_view(request):
    # This page opened the editor without the free mode, i.e. on the ODA
    # routes, which are gone (cblcad_oda_removed_api).
    return redirect("/tools/cad/?mode=free-dwg")
from django.http import JsonResponse, FileResponse
import os


def _cbl_dwg_dxf_emit_log_v1(message, *args):
    """Emit only the new DWG cache diagnostics through the project stdout path."""
    try:
        print(str(message) % args if args else str(message), flush=True)
    except Exception:
        pass
# ============================================================

# CBL_DWG_SAVE_CSRF_FIX_V1
# 정적 CAD HTML에서 DWG 저장 POST 전 CSRF 쿠키를 발급받기 위한 endpoint.
from django.http import JsonResponse as CBLJsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie as cbl_ensure_csrf_cookie

@cbl_ensure_csrf_cookie
def cblcad_csrf(request):
    return CBLJsonResponse({"ok": True})


# CBL_VIDEO_LIBRARY_API_V1_START
def _cbl_video_file_url(field):
    try:
        return field.url if field and field.name else ""
    except (ValueError, AttributeError):
        return ""


def _cbl_video_group_and_label(post):
    key = str(getattr(post, "category", "") or "")
    try:
        effective = cbl_effective_category_key(post)
        if effective:
            key = effective
    except Exception:
        pass

    mapping = {
        "construction_work": ("architecture", "건설실무"),
        "construction_tech": ("architecture", "건설기술"),
        "construction_real": ("architecture", "건설부동산"),
        "bim": ("bim", "REVIT/BIM"),
        "dynamo_automation": ("bim", "Dynamo/자동화"),
        "four_d_five_d": ("bim", "4D/5D"),
        "tech_ai_development": ("tech", "AI·개발"),
        "tech_data_security": ("tech", "데이터·보안"),
        "tech_server_software": ("tech", "인터넷·서버·소프트"),
        "tech": ("tech", "테크"),
        "program": ("program", "업무용 프로그램"),
        "tool_recommend": ("program", "툴소개/툴추천"),
    }
    return mapping.get(key, ("architecture", "건설"))


def _cbl_video_description_plain_text(raw_content, limit=1200):
    """
    동영상 뷰어 설명란에 쓸 평문을 만듭니다.
    줄바꿈(문단)은 유지하고, 줄 안의 중복 공백만 정리합니다.
    (기존에는 전체를 공백 기준으로 합쳐서 여러 줄이 한 줄로 붙어버리는 문제가 있었습니다.)
    """
    import re

    text = str(raw_content or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = strip_tags(text)

    lines = [" ".join(line.split()) for line in text.splitlines()]
    plain = "\n".join(lines).strip()
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain[:limit]


def _cbl_video_payload(post):
    group, category_label = _cbl_video_group_and_label(post)

    shorts_url = _cbl_video_file_url(getattr(post, "shorts_video", None))
    file_url = _cbl_video_file_url(getattr(post, "video_file", None))
    embed_url = str(getattr(post, "youtube_embed_url", "") or "")
    youtube_url = str(getattr(post, "youtube_url", "") or "")

    if shorts_url:
        source_type = "file"
        source_url = shorts_url
        video_kind = "SHORTS"
        kind = "shorts"
    elif file_url:
        source_type = "file"
        source_url = file_url
        video_kind = "VIDEO"
        kind = "video"
    elif embed_url:
        source_type = "youtube"
        source_url = embed_url
        video_kind = "VIDEO"
        kind = "video"
    else:
        source_type = ""
        source_url = ""
        video_kind = "VIDEO"
        kind = "video"

    cover_url = _cbl_video_file_url(getattr(post, "shorts_cover", None))
    if not cover_url:
        cover_url = _cbl_video_file_url(getattr(post, "thumbnail", None))

    plain = _cbl_video_description_plain_text(getattr(post, "content", ""))

    return {
        "id": post.pk,
        "title": str(getattr(post, "title", "") or "제목 없는 영상"),
        "description": plain[:1200],
        "category_group": group,
        "category_label": category_label,
        "kind": kind,
        "video_kind": video_kind,
        "source_type": source_type,
        "source_url": source_url,
        "cover_url": cover_url,
        "created_at": post.created_at.strftime("%Y.%m.%d") if post.created_at else "",
        "views": int(getattr(post, "views", 0) or 0),
        "detail_url": post.get_absolute_url(),
        "youtube_url": youtube_url,
    }


def video_library_api(request):
    """동영상 모음과 쇼츠 모음을 종류·카테고리·검색어로 나눠 반환합니다."""
    queryset = (
        Post.objects.filter(is_published=True)
        .filter(cbl_video_post_q())
        .distinct()
        .order_by("-created_at")
    )

    requested_id = str(request.GET.get("id", "") or "").strip()
    if requested_id.isdigit():
        queryset = queryset.filter(pk=int(requested_id))

    query = str(request.GET.get("q", "") or "").strip()
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(content__icontains=query)
            | Q(tags__icontains=query)
        )

    category = str(request.GET.get("category", "all") or "all").strip()
    kind = str(request.GET.get("kind", "all") or "all").strip().lower()
    if kind not in {"all", "video", "shorts"}:
        kind = "all"

    items = []
    for post in queryset[:200]:
        payload = _cbl_video_payload(post)
        payload["can_manage"] = bool(
            request.user.is_authenticated
            and (request.user.is_staff or request.user.is_superuser)
        )
        if payload["can_manage"]:
            payload["edit_data_url"] = reverse("video_post_edit_data_api", kwargs={"pk": post.pk})
            payload["update_url"] = reverse("video_post_update_api", kwargs={"pk": post.pk})
            payload["delete_url"] = reverse("video_library_delete_api", kwargs={"pk": post.pk})
        if kind != "all" and payload["kind"] != kind:
            continue
        if category != "all" and payload["category_group"] != category:
            continue
        if not payload["source_url"]:
            continue
        items.append(payload)

    return JsonResponse({
        "ok": True,
        "items": items,
        "count": len(items),
        "kind": kind,
        "can_upload": bool(
            request.user.is_authenticated
            and (request.user.is_staff or request.user.is_superuser)
        ),
    })


@user_passes_test(admin_required)
@require_POST
def video_library_delete_api(request, pk):
    post = get_object_or_404(Post, pk=pk)
    if not Post.objects.filter(cbl_video_post_q(), pk=pk).exists():
        return JsonResponse({"ok": False, "error": "동영상/쇼츠 게시글이 아닙니다."}, status=400)
    post.delete()
    return JsonResponse({"ok": True, "id": pk})


@user_passes_test(admin_required)
def video_post_edit_data_api(request, pk):
    """동영상 올리기 팝업에서 기존 동영상 게시글을 수정할 때 초기값을 내려줍니다."""
    post = get_object_or_404(Post, pk=pk)
    if not Post.objects.filter(cbl_video_post_q(), pk=pk).exists():
        return JsonResponse({"ok": False, "error": "동영상/쇼츠 게시글이 아닙니다."}, status=400)

    file_url = _cbl_video_file_url(getattr(post, "video_file", None))
    shorts_url = _cbl_video_file_url(getattr(post, "shorts_video", None))
    video_url = file_url or shorts_url
    youtube_url = str(getattr(post, "youtube_url", "") or "")
    source_type = "file" if video_url else ("youtube" if youtube_url else "file")

    return JsonResponse({
        "ok": True,
        "id": post.pk,
        "title": str(getattr(post, "title", "") or ""),
        "category": str(getattr(post, "category", "") or ""),
        "content": strip_tags(str(getattr(post, "content", "") or "")),
        "tags": str(getattr(post, "tags", "") or ""),
        "is_published": bool(getattr(post, "is_published", False)),
        "source_type": source_type,
        "video_url": video_url,
        "youtube_url": youtube_url,
        "thumbnail_url": _cbl_video_file_url(getattr(post, "thumbnail", None)),
        "csrf_token": get_token(request),
    })


@user_passes_test(admin_required)
@require_POST
def video_post_update_api(request, pk):
    """동영상 올리기 팝업에서 기존 동영상 게시글을 그대로 수정 저장합니다."""
    post = get_object_or_404(Post, pk=pk)
    if not Post.objects.filter(cbl_video_post_q(), pk=pk).exists():
        return JsonResponse({"ok": False, "error": "동영상/쇼츠 게시글이 아닙니다."}, status=400)

    old_thumbnail_name = post.thumbnail.name if post.thumbnail else ""
    old_video_file_name = post.video_file.name if post.video_file else ""

    form_data = request.POST.copy()
    form_data["post_type"] = "video"
    if not (form_data.get("content") or "").strip():
        form_data["content"] = "<p>영상 설명이 아직 없습니다.</p>"

    form = PostForm(form_data, request.FILES, instance=post)

    if not form.is_valid():
        errors = []
        for field_errors in form.errors.values():
            errors.extend(str(error) for error in field_errors)
        return JsonResponse({
            "ok": False,
            "error": errors[0] if errors else "입력 내용을 확인해주세요.",
            "errors": form.errors.get_json_data(),
        }, status=400)

    post = form.save(commit=False)
    post.post_type = "video"
    post.content = normalize_html_spaces(post.content or "")
    post.save()
    form.save_m2m()

    if request.FILES.get("thumbnail") and old_thumbnail_name != (post.thumbnail.name if post.thumbnail else ""):
        delete_file_safely(old_thumbnail_name)

    if request.FILES.get("video_file") and old_video_file_name != (post.video_file.name if post.video_file else ""):
        delete_file_safely(old_video_file_name)

    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return redirect("post_detail", pk=post.pk)

    return JsonResponse({
        "ok": True,
        "post_id": post.pk,
        "redirect_url": f"/?video={post.pk}",
    })
# CBL_VIDEO_LIBRARY_API_V1_END


# CBL free-DWG local runtime imports
import os as _cbl_os
import re as _cbl_re
import json as _cbl_json
import shutil as _cbl_shutil
import tempfile as _cbl_tempfile
import subprocess as _cbl_subprocess
import base64 as _cbl_base64
import secrets as _cbl_secrets
import sys as _cbl_sys
from pathlib import Path as _cbl_Path
from django.http import HttpResponse as _cbl_HttpResponse
from django.http import JsonResponse as _cbl_JsonResponse
from django.http import FileResponse as _cbl_FileResponse
from django.core import signing as _cbl_signing
from django.views.decorators.csrf import csrf_exempt as _cbl_csrf_exempt
from django.views.decorators.gzip import gzip_page as _cbl_gzip_page

# CBL_FREE_DWG_LOCAL_INTEGRATION_V1_START
# Local-only LibreDWG -> structured/display-flat DXF path.
# This endpoint is feature-flagged and never calls the ODA converter.
# ============================================================
_CBL_FREE_DWG_LOCAL_SCHEMA_V1 = "cbl-free-dwg-local-v8-compact-layer-style-render-adapter"
_CBL_FREE_DWG_LOCAL_TTL_V1 = 7 * 24 * 60 * 60
_CBL_FREE_DWG_LOCAL_MAX_ENTRIES_V1 = 64
_CBL_FREE_DWG_LOCAL_MAX_BYTES_V1 = 2 * 1024 * 1024 * 1024


def _cbl_free_dwg_local_enabled_v1():
    return str(_cbl_os.environ.get("CBLCAD_FREE_DWG_LOCAL", "")).strip().lower() in {
        "1", "true", "yes", "on"
    }


def _cbl_is_safe_local_free_dwg_request(request):
    """Allow free DWG only from a debug loopback browser request."""
    if not bool(getattr(settings, "DEBUG", False)):
        return False
    remote = str(request.META.get("REMOTE_ADDR", "")).strip().lower()
    if remote not in {"127.0.0.1", "::1"}:
        return False
    from urllib.parse import urlsplit

    def is_local(value):
        raw = str(value or "").strip()
        if not raw:
            return False
        try:
            parsed = urlsplit("//" + raw if "://" not in raw else raw)
            return (parsed.hostname or "").strip("[]").lower() in {
                "127.0.0.1", "localhost", "::1"
            }
        except ValueError:
            return False

    if not is_local(request.META.get("HTTP_HOST")):
        return False
    origin = request.META.get("HTTP_ORIGIN")
    if origin and not is_local(origin):
        return False
    return _cbl_free_dwg_local_enabled_v1() or request.GET.get("mode") == "free-dwg"


def _cbl_is_browser_free_dwg_request(request):
    """Allow the authenticated production browser free-DWG route.

    Native Finder endpoints continue to use the stricter loopback/macOS
    helper above. The browser route never accepts a client filesystem path.
    """
    if request.GET.get("mode") != "free-dwg" or _cbl_is_safe_local_free_dwg_request(request):
        return False
    user = getattr(request, "user", None)
    if not user or not bool(getattr(user, "is_authenticated", False)):
        return False
    from urllib.parse import urlsplit

    def _host(value):
        raw = str(value or "").strip()
        try:
            parsed = urlsplit("//" + raw if "://" not in raw else raw)
            return (parsed.hostname or "").strip("[]").lower()
        except ValueError:
            return ""

    host = _host(request.META.get("HTTP_HOST"))
    if not host or host in {"127.0.0.1", "localhost", "::1"}:
        return False
    origin = request.META.get("HTTP_ORIGIN")
    if origin and _host(origin) != host:
        return False
    return True


def _cbl_is_free_dwg_request(request):
    return _cbl_is_safe_local_free_dwg_request(request) or _cbl_is_browser_free_dwg_request(request)


@_cbl_csrf_exempt
def cblcad_oda_removed_api(request, *args, **kwargs):
    """The routes that ran ODA File Converter.

    ODA is not part of ChickenBananaCAD (license): DWG goes through the free
    ACadSharp pipeline (?mode=free-dwg).  Nothing is read or started here.
    """
    return JsonResponse({
        "ok": False,
        "error": "oda_removed",
        "message": "이 DWG 변환 경로는 더 이상 제공하지 않습니다. 무료 DWG 모드(/tools/cad/?mode=free-dwg)를 사용해 주세요.",
    }, status=410, json_dumps_params={"ensure_ascii": False})


def _cbl_free_dwg_upload_limit_v1():
    try:
        return max(1, int(getattr(settings, "CBLCAD_FREE_DWG_MAX_UPLOAD_BYTES", 200 * 1024 * 1024)))
    except (TypeError, ValueError):
        return 200 * 1024 * 1024


def _cbl_local_file_fingerprint_v1(path):
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()}


def _cbl_local_file_tokens_v1(request):
    return request.session.get("cbl_free_dwg_local_files_v1", {})


def _cbl_store_local_file_token_v1(request, path, kind="source"):
    path = _cbl_Path(path).resolve()
    fingerprint = _cbl_local_file_fingerprint_v1(path) if path.exists() else {"size": 0, "mtime_ns": 0, "sha256": ""}
    token = _cbl_secrets.token_urlsafe(32)
    records = _cbl_local_file_tokens_v1(request)
    records[token] = {"path": str(path), "name": path.name, "extension": path.suffix.lower(),
                      "size": fingerprint["size"], "mtime_ns": fingerprint["mtime_ns"],
                      "sha256": fingerprint["sha256"], "created_at": _cbl_time.time(), "kind": kind}
    request.session["cbl_free_dwg_local_files_v1"] = records
    request.session.modified = True
    return token, records[token]


def _cbl_applescript_quote_v1(value):
    text = str(value or "")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _cbl_native_choose_path_v1(save_as=False, default_name="drawing.dwg"):
    if _cbl_sys.platform != "darwin":
        raise RuntimeError("macOS 로컬 파일 선택은 macOS에서만 지원합니다.")
    if save_as:
        script = ('POSIX path of (choose file name with prompt '
                  + _cbl_applescript_quote_v1("ChickenBananaCAD 저장")
                  + ' default name ' + _cbl_applescript_quote_v1(default_name) + ')')
    else:
        script = 'POSIX path of (choose file with prompt "ChickenBananaCAD DWG/DXF 열기")'
    run = _cbl_subprocess.run(["osascript", "-e", script], stdout=_cbl_subprocess.PIPE,
                              stderr=_cbl_subprocess.PIPE, timeout=300, check=False)
    if run.returncode != 0:
        detail = run.stderr.decode("utf-8", errors="replace").strip().lower()
        if "user canceled" in detail or "-128" in detail or not detail:
            return None
        raise RuntimeError("macOS 파일 선택창을 열지 못했습니다.")
    path = run.stdout.decode("utf-8", errors="replace").strip()
    return _cbl_Path(path).resolve() if path else None


@_cbl_csrf_exempt
def cblcad_free_dwg_native_open_api(request):
    if request.method != "POST" or not _cbl_is_safe_local_free_dwg_request(request) or _cbl_sys.platform != "darwin":
        return _cbl_JsonResponse({"ok": False, "error": "로컬 파일 열기를 사용할 수 없습니다."}, status=404)
    try:
        path = _cbl_native_choose_path_v1(False)
        if path is None:
            return _cbl_JsonResponse({"ok": True, "cancelled": True})
        if path.suffix.lower() not in {".dwg", ".dxf"} or not path.is_file():
            return _cbl_JsonResponse({"ok": False, "error": "DWG 또는 DXF 파일만 열 수 있습니다."}, status=400)
        stat = path.stat()
        if stat.st_size > _cbl_free_dwg_upload_limit_v1():
            return _cbl_JsonResponse({"ok": False, "error": "DWG 업로드 제한을 초과했습니다."}, status=413)
        token, record = _cbl_store_local_file_token_v1(request, path, "source")
        payload = path.read_bytes()
        return _cbl_JsonResponse({"ok": True, "token": token, "filename": record["name"],
                                  "size": len(payload), "data": _cbl_base64.b64encode(payload).decode("ascii")})
    except Exception as exc:
        return _cbl_JsonResponse({"ok": False, "error": str(exc)}, status=500)


@_cbl_csrf_exempt
def cblcad_free_dwg_native_save_path_api(request):
    if request.method != "POST" or not _cbl_is_safe_local_free_dwg_request(request) or _cbl_sys.platform != "darwin":
        return _cbl_JsonResponse({"ok": False, "error": "로컬 파일 저장을 사용할 수 없습니다."}, status=404)
    try:
        requested = _cbl_os.path.basename(str(request.POST.get("filename") or "drawing.dwg"))
        requested = _cbl_re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", requested).strip() or "drawing.dwg"
        if not requested.lower().endswith(".dwg"):
            requested += ".dwg"
        path = _cbl_native_choose_path_v1(True, requested)
        if path is None:
            return _cbl_JsonResponse({"ok": True, "cancelled": True})
        if path.suffix.lower() != ".dwg":
            path = path.with_suffix(".dwg")
        token, record = _cbl_store_local_file_token_v1(request, path, "target")
        return _cbl_JsonResponse({"ok": True, "token": token, "filename": record["name"], "exists": path.exists()})
    except Exception as exc:
        return _cbl_JsonResponse({"ok": False, "error": str(exc)}, status=500)


def _cbl_free_dwg_local_request_enabled_v1(request):
    """Allow the explicit product free-mode route without requiring a shell env var."""
    return _cbl_free_dwg_local_enabled_v1() or request.GET.get("mode") == "free-dwg"


def _cbl_free_dwg_local_root_v1():
    configured = _cbl_os.environ.get("CBLCAD_FREE_DWG_CACHE_DIR")
    root = _cbl_Path(configured) if configured else _cbl_Path(_cbl_tempfile.gettempdir()) / "cbl-free-dwg-local-cache-v1"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cbl_free_dwg_local_key_v1(data):
    file_sha256 = hashlib.sha256(data).hexdigest()
    descriptor = {
        "schema": _CBL_FREE_DWG_LOCAL_SCHEMA_V1,
        "file_sha256": file_sha256,
        "output": "structured.dxf+compact.json",
        "options": {"writer": "R2004-cp949", "display": "compact-v2-layer-style", "max_depth": 32},
    }
    key = hashlib.sha256(_cbl_json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return key, file_sha256


def _cbl_free_dwg_local_valid_dxf_v1(path):
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size < 500:
            return False
        with path.open("rb") as stream:
            head = stream.read(4096)
            stream.seek(max(0, stat.st_size - 16384))
            tail = stream.read(16384)
        return b"0SECTION" in b"".join(head.split()) and b"0EOF" in b"".join(tail.split())
    except OSError:
        return False


def _cbl_free_dwg_local_find_dwgread_v1():
    candidates = [
        _cbl_os.environ.get("LIBREDWG_DWGREAD"),
        _cbl_shutil.which("dwgread"),
        "/opt/homebrew/bin/dwgread",
        "/usr/local/bin/dwgread",
    ]
    for candidate in candidates:
        if candidate and _cbl_os.path.isfile(candidate) and _cbl_os.access(candidate, _cbl_os.X_OK):
            return candidate
    return None


def _cbl_free_dwg_local_find_minsert_helper_v1():
    root = _cbl_Path(__file__).resolve().parent.parent
    candidates = [
        root / "tools" / "cbl_free_dwg_poc" / "runtime" / "cbl_free_minsert_fields",
        root / "tools" / "cbl_free_dwg_poc" / "bin" / "cbl_free_minsert_fields",
    ]
    for candidate in candidates:
        if candidate.is_file() and _cbl_os.access(candidate, _cbl_os.X_OK):
            return candidate
    return None


# ============================================================
# CBL_ACADSHARP_SLOTS_V1
# A converter run takes up to ~700 MB on a large drawing and the server has
# 2 GB.  Every run takes one of CBLCAD_ACADSHARP_SLOTS cross-process slots
# (flock, released when the process dies); quantity jobs in the background
# use only the first slot so editor opens and saves keep the others.
# ============================================================
import contextlib as _cbl_contextlib
import fcntl as _cbl_fcntl


class _CBLAcadSharpBusy(RuntimeError):
    pass


_CBL_ACADSHARP_BUSY_MESSAGE_V1 = "DWG 변환기가 다른 도면을 처리하고 있습니다. 잠시 후 다시 시도해 주세요."
_CBL_ACADSHARP_SLOT_ROOT_V1 = _cbl_Path(_cbl_tempfile.gettempdir()) / "cbl-acadsharp-slots-v1"
_CBL_ACADSHARP_WAIT_V1 = 60


def _cbl_acadsharp_slot_count_v1():
    try:
        return max(1, int(getattr(settings, "CBLCAD_ACADSHARP_SLOTS", 2)))
    except (TypeError, ValueError):
        return 2


@_cbl_contextlib.contextmanager
def _cbl_acadsharp_slot_v1(wait=_CBL_ACADSHARP_WAIT_V1, background=False):
    """Hold a converter slot (its index) for the block; _CBLAcadSharpBusy after `wait` seconds."""
    root = _CBL_ACADSHARP_SLOT_ROOT_V1
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    slots = range(1 if background else _cbl_acadsharp_slot_count_v1())
    started = _cbl_time.monotonic()
    while True:
        for index in slots:
            handle = open(root / f"slot-{index}.lock", "a+")
            try:
                _cbl_fcntl.flock(handle, _cbl_fcntl.LOCK_EX | _cbl_fcntl.LOCK_NB)
            except OSError:
                handle.close()
                continue
            try:
                waited = _cbl_time.monotonic() - started
                if waited >= 1:
                    _cbl_dwg_dxf_emit_log_v1("CBLCAD_ACADSHARP_SLOT event=waited slot=%s wait_ms=%.0f background=%s",
                                             index, waited * 1000, int(background))
                yield index
            finally:
                _cbl_fcntl.flock(handle, _cbl_fcntl.LOCK_UN)
                handle.close()
            return
        if _cbl_time.monotonic() - started >= wait:
            _cbl_dwg_dxf_emit_log_v1("CBLCAD_ACADSHARP_SLOT event=busy wait_ms=%.0f background=%s",
                                     wait * 1000, int(background))
            raise _CBLAcadSharpBusy(_CBL_ACADSHARP_BUSY_MESSAGE_V1)
        _cbl_time.sleep(0.25)


def _cbl_acadsharp_run_v1(command, timeout, wait=_CBL_ACADSHARP_WAIT_V1, background=False):
    """subprocess.run of the ACadSharp converter inside a slot (stdout/stderr captured)."""
    with _cbl_acadsharp_slot_v1(wait=wait, background=background):
        return _cbl_subprocess.run(command, stdout=_cbl_subprocess.PIPE, stderr=_cbl_subprocess.PIPE,
                                   timeout=timeout, check=False)


def _cbl_free_dwg_local_acadsharp_metadata_v1(path):
    executable = _cbl_free_dwg_save_local_executable_v1()
    if executable is None:
        return None, {"status": "executable_missing"}
    try:
        result = _cbl_acadsharp_run_v1([str(executable), "--metadata", str(path)], timeout=300)
        if result.returncode != 0 or not result.stdout.strip():
            return None, {"status": "metadata_read_failed", "error": result.stderr.decode(errors="replace")[-500:]}
        return _cbl_json.loads(result.stdout.decode("utf-8", errors="replace"), strict=False), {"status": "read"}
    except Exception as exc:
        return None, {"status": "metadata_read_failed", "error": str(exc)[:500]}


def _cbl_free_dwg_local_cleanup_v1(root, protected=None):
    def removable(item):
        """Do not remove an entry while another request holds its lock."""
        lock_path = root / (item.name + ".lock")
        try:
            import fcntl
            with lock_path.open("a+") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return True
        except OSError:
            return False

    try:
        now = _cbl_time.time()
        # Lock files are intentionally persistent while an entry is live, but
        # stale empty locks must not accumulate forever.  Never unlink one
        # unless it is old, empty, and can be acquired non-blocking.
        for lock_path in root.glob("*.lock"):
            try:
                if now - lock_path.stat().st_mtime <= _CBL_FREE_DWG_LOCAL_TTL_V1 or lock_path.stat().st_size:
                    continue
                import fcntl
                with lock_path.open("a+") as lock:
                    try:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    if lock_path.stat().st_size == 0:
                        lock_path.unlink(missing_ok=True)
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            except OSError:
                continue
        entries = []
        for item in root.iterdir():
            if not item.is_dir() or item.name == protected:
                continue
            meta = item / "meta.json"
            mtime = meta.stat().st_mtime if meta.exists() else item.stat().st_mtime
            size = sum(p.stat().st_size for p in item.rglob("*") if p.is_file())
            if now - mtime > _CBL_FREE_DWG_LOCAL_TTL_V1:
                if removable(item):
                    _cbl_shutil.rmtree(item, ignore_errors=True)
                continue
            entries.append((mtime, size, item))
        total = sum(row[1] for row in entries)
        while len(entries) > _CBL_FREE_DWG_LOCAL_MAX_ENTRIES_V1 or total > _CBL_FREE_DWG_LOCAL_MAX_BYTES_V1:
            _, size, item = min(entries, key=lambda row: row[0])
            if not removable(item):
                entries = [row for row in entries if row[2] != item]
                continue
            _cbl_shutil.rmtree(item, ignore_errors=True)
            total -= size
            entries = [row for row in entries if row[2] != item]
    except Exception:
        pass


def _cbl_free_dwg_local_convert_v1(data, original_name):
    import fcntl
    from tools.cbl_free_dwg_poc.json_to_dxf import Writer, load_json
    from tools.cbl_free_dwg_poc.compact_display import CompactBuilder
    from tools.cbl_free_dwg_poc.minsert_supplement import supplement
    from tools.cbl_free_dwg_poc.metadata_bridge import apply_metadata

    root = _cbl_free_dwg_local_root_v1()
    key, file_sha256 = _cbl_free_dwg_local_key_v1(data)
    entry = root / key
    lock_path = root / (key + ".lock")
    waited = False
    started = _cbl_time.perf_counter()

    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            waited = True
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

        structured = entry / "structured.dxf"
        compact = entry / "compact.json"
        compact_valid = False
        try:
            compact_stat = compact.stat()
            compact_valid = compact_stat.st_size >= 100 and compact_stat.st_size <= 50 * 1024 * 1024
            if compact_valid:
                cached_compact = _cbl_json.loads(compact.read_text(encoding="utf-8"))
                compact_valid = all(isinstance(cached_compact.get(key), (dict, list))
                                    for key in ("blocks", "modelspace", "instances"))
        except (OSError, ValueError, TypeError):
            compact_valid = False
        if compact_valid and _cbl_free_dwg_local_valid_dxf_v1(structured):
            event = "cache_wait_hit" if waited else "cache_hit"
            cached_unresolved = 0
            try:
                cached_meta = _cbl_json.loads((entry / "meta.json").read_text(encoding="utf-8"))
                cached_unresolved = int((cached_meta.get("minsert") or {}).get("unresolved_minsert", 0) or 0)
            except (OSError, TypeError, ValueError):
                pass
            _cbl_dwg_dxf_emit_log_v1(
                "CBLCAD_FREE_DWG_LOCAL endpoint=free-dwg-local file_sha256=%s cache_key=%s event=%s oda_executed=0 compact_bytes=%s endpoint_ms=%.2f",
                file_sha256[:12], key[:12], event, compact.stat().st_size,
                (_cbl_time.perf_counter() - started) * 1000,
            )
            return {
                "cache_event": event, "cache_key": key, "file_sha256": file_sha256,
                "structured_bytes": structured.stat().st_size, "compact_bytes": compact.stat().st_size,
                "oda_executed": False, "minsert_unresolved": cached_unresolved,
                "compact": cached_compact,
            }

        with _cbl_tempfile.TemporaryDirectory(prefix=".cbl-free-dwg-", dir=str(root)) as tmp:
            tmp_path = _cbl_Path(tmp)
            dwg_path = tmp_path / "input.dwg"
            json_path = tmp_path / "decoded.json"
            stock_dxf = tmp_path / "stock-minsert.dxf"
            structured_tmp = tmp_path / "structured.dxf"
            compact_tmp = tmp_path / "compact.json"
            metadata_path = tmp_path / "acadsharp-metadata.json"
            minsert_records_path = tmp_path / "minsert-records.jsonl"
            dwg_path.write_bytes(data)
            dwgread = _cbl_free_dwg_local_find_dwgread_v1()
            if not dwgread:
                raise RuntimeError("LibreDWG dwgread를 찾지 못했습니다. 로컬 ODA는 자동 실행하지 않습니다.")

            oda_started = _cbl_time.perf_counter()
            decoded = _cbl_subprocess.run(
                [dwgread, "-O", "JSON", str(dwg_path)],
                stdout=_cbl_subprocess.PIPE, stderr=_cbl_subprocess.PIPE,
                timeout=300, check=False,
            )
            if decoded.returncode != 0 or not decoded.stdout.strip():
                raise RuntimeError("LibreDWG JSON 판독 실패: " + decoded.stderr.decode(errors="replace")[-500:])
            json_path.write_bytes(decoded.stdout)

            # Existing stock LibreDWG DXF is used only to recover verified
            # MINSERT 70/71/44/45 values; no ODA binary is ever considered.
            stock = _cbl_subprocess.run(
                [dwgread, "-O", "DXF", str(dwg_path)],
                stdout=_cbl_subprocess.PIPE, stderr=_cbl_subprocess.PIPE,
                timeout=300, check=False,
            )
            if stock.returncode == 0 and stock.stdout.strip():
                stock_dxf.write_bytes(stock.stdout)

            data_json = load_json(json_path)
            acad_metadata, metadata_report = _cbl_free_dwg_local_acadsharp_metadata_v1(dwg_path)
            if acad_metadata is not None:
                metadata_path.write_text(_cbl_json.dumps(acad_metadata, ensure_ascii=False), encoding="utf-8")
                data_json, metadata_merge_report = apply_metadata(data_json, acad_metadata)
            else:
                metadata_merge_report = metadata_report

            helper = _cbl_free_dwg_local_find_minsert_helper_v1()
            minsert_records = []
            helper_report = {"status": "helper_missing"}
            if helper is not None:
                helper_run = _cbl_subprocess.run(
                    [str(helper), str(dwg_path)], stdout=_cbl_subprocess.PIPE,
                    stderr=_cbl_subprocess.PIPE, timeout=300, check=False,
                )
                if helper_run.returncode == 0:
                    for line in helper_run.stdout.decode("utf-8", errors="replace").splitlines():
                        if line.strip().startswith("{"):
                            minsert_records.append(_cbl_json.loads(line))
                    helper_report = {"status": "read", "records": len(minsert_records),
                                     "stderr": helper_run.stderr.decode(errors="replace")[-300:]}
                else:
                    helper_report = {"status": "helper_failed", "error": helper_run.stderr.decode(errors="replace")[-500:]}
            minsert_records_path.write_text("".join(_cbl_json.dumps(item) + "\n" for item in minsert_records), encoding="utf-8")
            data_json, minsert_report = supplement(data_json, minsert_records, stock_dxf if stock_dxf.exists() else None)
            minsert_report["c_helper"] = helper_report
            minsert_report["acadsharp_metadata"] = metadata_merge_report
            json_path.write_text(_cbl_json.dumps(data_json, ensure_ascii=True), encoding="ascii")
            Writer(load_json(json_path)).write(structured_tmp)
            if not _cbl_free_dwg_local_valid_dxf_v1(structured_tmp):
                raise RuntimeError("변환 결과 structured DXF 검증 실패")
            compact_drawing = __import__("ezdxf").readfile(structured_tmp)
            compact_data = CompactBuilder(compact_drawing).build()
            compact_data["external_diagnostics"] = {"minsert": minsert_report}
            compact_tmp.write_text(_cbl_json.dumps(compact_data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            if compact_tmp.stat().st_size > 50 * 1024 * 1024 or not compact_data.get("blocks"):
                raise RuntimeError("compact 표시 결과가 비어 있거나 50MB 제한을 초과했습니다.")

            entry.mkdir(parents=True, exist_ok=True)
            _cbl_os.replace(structured_tmp, structured)
            _cbl_os.replace(compact_tmp, compact)
            (entry / "meta.json").write_text(_cbl_json.dumps({
                "schema": _CBL_FREE_DWG_LOCAL_SCHEMA_V1, "file_sha256": file_sha256,
                "original_name": original_name, "minsert": minsert_report,
                "oda_executed": False, "dwgread": dwgread,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        _cbl_free_dwg_local_cleanup_v1(root, protected=key)
        _cbl_dwg_dxf_emit_log_v1(
            "CBLCAD_FREE_DWG_LOCAL endpoint=free-dwg-local file_sha256=%s cache_key=%s event=cache_miss oda_executed=0 compact_bytes=%s endpoint_ms=%.2f unresolved_minsert=%s",
            file_sha256[:12], key[:12], compact.stat().st_size,
            (_cbl_time.perf_counter() - started) * 1000,
            minsert_report.get("unresolved_minsert", 117),
        )
        return {
            "cache_event": "cache_miss", "cache_key": key, "file_sha256": file_sha256,
            "structured_bytes": structured.stat().st_size, "compact_bytes": compact.stat().st_size,
            "oda_executed": False, "minsert_unresolved": minsert_report.get("unresolved_minsert", 117),
            "compact": compact_data,
        }


_CBL_DXF_UNICODE_ESCAPE_RE_V1 = _cbl_re.compile(r"\\U\+([0-9A-Fa-f]{4})")


def _cbl_decode_dxf_unicode_escapes_v1(text):
    """Turn \\U+XXXX back into characters for the editor.

    A drawing whose code page cannot hold a character (Korean in an
    ANSI_1252 DWG) stores it as \\U+XXXX, as AutoCAD does.  ASCII and
    surrogate values stay as written so no DXF syntax (newlines) appears.
    """
    def replace(match):
        code = int(match.group(1), 16)
        if code < 0x80 or 0xD800 <= code <= 0xDFFF:
            return match.group(0)
        return chr(code)
    return _CBL_DXF_UNICODE_ESCAPE_RE_V1.sub(replace, text)


_CBL_DXF_CODEPAGE_SPECIAL_V1 = {
    "kcs5601": "cp949", "ansi_949": "cp949", "johab": "johab", "gb2312": "gbk", "ansi_936": "gbk",
    "big5": "cp950", "ascii": "ascii", "mac-roman": "mac_roman",
}


def _cbl_dxf_codepage_codec_v1(name):
    """Python codec for a $DWGCODEPAGE name the runtime writes, or None."""
    import codecs as _cbl_codecs
    key = str(name or "").strip().lower()
    codec = _CBL_DXF_CODEPAGE_SPECIAL_V1.get(key)
    if codec is None:
        match = _cbl_re.match(r"^(?:ansi_?|dos)(\d{3,4})$", key)
        if match:
            codec = "cp" + match.group(1)
        else:
            match = _cbl_re.match(r"^iso8859-?(\d{1,2})$", key)
            codec = "iso8859-" + match.group(1) if match else None
    if codec is None:
        return None
    try:
        _cbl_codecs.lookup(codec)
    except LookupError:
        return None
    return codec


def _cbl_free_dwg_dxf_text_v1(dxf_bytes):
    """Decode the runtime's DXF for the editor.

    R2007+ DXF is UTF-8.  Older DXF is written in the drawing code page
    ($DWGCODEPAGE): KS C 5601 for Korean drawings, Windows-1252 for Western
    ones, whose "Ø ± ° é" used to become U+FFFD when read as UTF-8.  An
    unknown code page keeps the old UTF-8 reading.
    """
    end = dxf_bytes.find(b"ENDSEC", 0, 65536)
    head = dxf_bytes[:end if end > 0 else 65536].decode("latin-1")
    version = _cbl_re.search(r"\$ACADVER\s*\r?\n\s*1\s*\r?\n\s*(AC\d{4})", head)
    page = _cbl_re.search(r"\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n([^\r\n]*)", head)
    codec = "utf-8"
    if version and version.group(1) < "AC1021" and page:
        codec = _cbl_dxf_codepage_codec_v1(page.group(1)) or "utf-8"
    try:
        return dxf_bytes.decode(codec)
    except UnicodeDecodeError:
        return dxf_bytes.decode(codec, errors="replace")


_CBL_UNREADABLE_OBJECT_RE_V1 = _cbl_re.compile(r"^Could not read (\S+?)(?: number \d+)? with handle")


def _cbl_free_dwg_unreadable_objects_v1(notifications):
    """{type: count} of objects the ACadSharp reader skipped ("Could not read ...").

    Such objects are missing from the model, so the editor never shows them
    and a save would write the drawing without them.
    """
    counts = {}
    for item in notifications or []:
        if not isinstance(item, dict):
            continue
        match = _CBL_UNREADABLE_OBJECT_RE_V1.match(str(item.get("Message") or item.get("message") or ""))
        if match:
            counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts


# Start of the ACadSharp reader's notice for pre-2007 XRECORD/extended-data
# strings stored with their character count where the byte count belongs, as
# our writer did before 2026-10-01.  The reader reads them in full; AutoCAD and
# ODA stop on such a file unless they repair it, and a save writes byte counts.
_CBL_LEGACY_TEXT_LENGTH_PREFIX_V1 = "Legacy character-count string lengths read in"


def _cbl_free_dwg_legacy_text_lengths_v1(notifications):
    """Number of XRECORD/extended-data blocks read with character-count lengths."""
    return sum(1 for item in notifications or [] if isinstance(item, dict) and str(
        item.get("Message") or item.get("message") or "").startswith(_CBL_LEGACY_TEXT_LENGTH_PREFIX_V1))


# Notice of the runtime when strings stored as UTF-8 under a different code
# page (an older pipeline's S-501 saves) were read as UTF-8; a save writes them
# in the code page, which repairs them for AutoCAD.
_CBL_MISDECLARED_UTF8_PREFIX_V1 = "Misdeclared UTF-8 strings read:"


def _cbl_free_dwg_misdeclared_utf8_v1(notifications):
    """Number of strings the runtime read as UTF-8 against the drawing's code page."""
    total = 0
    for item in notifications or []:
        message = str(item.get("Message") or item.get("message") or "") if isinstance(item, dict) else ""
        if message.startswith(_CBL_MISDECLARED_UTF8_PREFIX_V1):
            try:
                total += int(message[len(_CBL_MISDECLARED_UTF8_PREFIX_V1):].strip())
            except ValueError:
                pass
    return total


def _cbl_free_dwg_unreadable_message_v1(counts):
    parts = ", ".join(f"{name} {count}개" for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    return (f"이 도면에는 무료 DWG 변환기가 읽지 못한 객체({parts})가 있어, 저장하면 이 객체들이 사라지므로 "
            "저장을 중단했습니다. 원본 파일은 바뀌지 않았습니다.")


def _cbl_build_original_source_layer_manifest_v1(report, dxf_text=""):
    """Build an independent, handle-keyed source manifest for browser audit.

    This is diagnostic/source metadata only.  It is deliberately derived from
    the ACadSharp metadata pass and never from browser shapes or array order.
    """
    report = report if isinstance(report, dict) else {}
    layer_rows = report.get("layers") if isinstance(report.get("layers"), list) else []
    entities = report.get("entities") if isinstance(report.get("entities"), list) else []
    semantic = report.get("semanticManifest") if isinstance(report.get("semanticManifest"), dict) else {}
    block_rows = semantic.get("blocks") if isinstance(semantic.get("blocks"), list) else []
    block_by_name = {str(x.get("name")): x for x in block_rows if isinstance(x, dict) and x.get("name")}

    # Signed layer ACI and table-only flags are read from the full DXF when it
    # is available.  Handles and all other values remain strings unless they
    # are explicitly documented numeric properties.
    dxf_layers = {}
    dimstyles = {}
    lines = str(dxf_text or "").replace("\r", "").split("\n")
    pairs = [(str(lines[i]).strip(), str(lines[i + 1]).strip())
             for i in range(0, max(0, len(lines) - 1), 2)]
    section = table = None
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value == "SECTION":
            # The SECTION name is the value in the pair immediately after
            # the 0/SECTION pair.  Using i+2 skipped the 2/section-name
            # pair, which silently dropped DIMSTYLE and layer records from
            # the independent source manifest.
            section = pairs[i + 1][1].upper() if i + 1 < len(pairs) and pairs[i + 1][0] == "2" else None
            i += 1
        elif code == "0" and value == "ENDSEC":
            section = table = None
        elif section == "TABLES" and code == "0" and value == "TABLE":
            table = pairs[i + 1][1].upper() if i + 1 < len(pairs) and pairs[i + 1][0] == "2" else None
        elif section == "TABLES" and code == "0" and value == "ENDTAB":
            table = None
        elif section == "TABLES" and table in {"LAYER", "DIMSTYLE"} and code == "0" and value in {"LAYER", "DIMSTYLE"}:
            fields = {}
            j = i + 1
            while j < len(pairs) and pairs[j][0] != "0":
                fields.setdefault(pairs[j][0], pairs[j][1])
                j += 1
            if value == "LAYER":
                name = fields.get("2", "0")
                try:
                    signed_aci = int(fields.get("62", "7"))
                except (TypeError, ValueError):
                    signed_aci = 7
                dxf_layers[str(name)] = {
                    "signedAci": signed_aci,
                    "rawAci": abs(signed_aci),
                    "trueColor": fields.get("420"),
                    "transparency": fields.get("440"),
                    "linetype": fields.get("6", "Continuous"),
                    "lineweight": fields.get("370"),
                    "flags": fields.get("70", "0"),
                    "plottable": fields.get("290", "1"),
                }
            else:
                name = fields.get("2", "STANDARD")
                dimstyles[str(name)] = {
                    "handle": fields.get("105"), "name": str(name),
                    "DIMCLRD": fields.get("176"), "DIMCLRE": fields.get("177"),
                    "DIMCLRT": fields.get("178"), "DIMTXSTY": fields.get("3"),
                }
            i = j - 1
        i += 1

    layers = []
    for layer in layer_rows:
        if not isinstance(layer, dict):
            continue
        name = str(layer.get("name") or "0")
        raw = dxf_layers.get(name, {})
        try:
            aci = int(raw.get("signedAci", layer.get("aci", 7)))
        except (TypeError, ValueError):
            aci = 7
        flags = int(raw.get("flags", 0) or 0)
        layers.append({
            "handle": str(layer.get("handle") or ""), "name": name,
            "signedAci": aci, "rawAci": abs(aci),
            "trueColor": raw.get("trueColor", layer.get("trueColor")),
            "transparency": raw.get("transparency"),
            "linetype": layer.get("linetype") or raw.get("linetype", "Continuous"),
            "lineweight": layer.get("lineweight") if layer.get("lineweight") is not None else raw.get("lineweight"),
            "off": aci < 0, "frozen": bool(flags & 1),
            "locked": bool(flags & 4), "plottable": str(raw.get("plottable", "1")) != "0",
            "ownerHandle": str(layer.get("owner") or ""),
        })
    layer_by_name = {x["name"].lower(): x for x in layers}

    entity_rows = []
    inserts = []
    for item in entities:
        if not isinstance(item, dict):
            continue
        layer = item.get("layer") if isinstance(item.get("layer"), dict) else {}
        block = item.get("block") if isinstance(item.get("block"), dict) else {}
        row = {
            "handle": str(item.get("handle") or ""), "ownerHandle": str(item.get("owner") or ""),
            "entityType": str(item.get("type") or ""), "space": str(item.get("space") or ""),
            "layerHandle": str(layer.get("handle") or ""), "layerName": str(layer.get("name") or "0"),
            "rawAci": item.get("aci"), "trueColor": item.get("trueColor"),
            "linetype": item.get("linetype"), "lineweight": item.get("lineweight"),
            "transparency": item.get("transparency"),
            "blockHandle": str(block.get("handle") or ""), "blockName": str(block.get("name") or ""),
        }
        entity_rows.append(row)
        if row["entityType"].upper() == "INSERT":
            ins = item.get("insert") if isinstance(item.get("insert"), dict) else {}
            inserts.append({
                "insertHandle": row["handle"], "referencedBlockHandle": row["blockHandle"],
                "referencedBlockName": row["blockName"], "parentInsertHandle": None,
                "layer": row["layerName"], "rawAci": row["rawAci"], "trueColor": row["trueColor"],
                "rotation": ins.get("rotation"), "scale": ins.get("scale"),
            })
    return {
        "schema": "cbl-original-source-layer-manifest-v1", "source": "acadsharp-original-dwg",
        "codePage": report.get("codePage"), "layers": layers, "entities": entity_rows,
        "inserts": inserts, "blocks": block_rows, "dimstyles": list(dimstyles.values()),
        "dimensionAnonymousBlocks": [x for x in block_rows if str(x.get("name", "")).startswith("*D")],
        "counts": {"layers": len(layers), "entities": len(entity_rows), "inserts": len(inserts),
                   "dimensions": sum(1 for x in entities if str(x.get("type", "")).upper().startswith("DIMENSION")),
                   "blocks": len(block_rows), "blockChildren": sum(int(x.get("childCount", 0) or 0) for x in block_rows)},
    }


# A DXF picked in the editor opens as a new AC1018 DWG made from it
# (--dwg-from-dxf): the editor edits and saves DWGs only, and its own DXF parse
# keeps neither block definitions nor dimensions.  The DWG goes back to the
# editor as the drawing's source, so the first save is a Save As of a new .dwg
# and never writes into the .dxf or the drawing that was open before.
_CBL_DXF_TEXT_START_RE_V1 = _cbl_re.compile(rb"\A(?:\xef\xbb\xbf)?\s*(?:0\s*\r?\n\s*SECTION\b|999\s*\r?\n)")


def _cbl_free_dwg_is_dxf_upload_v1(data, name):
    if data[:3] == b"AC1":  # a DWG starts with its version (AC1009 ... AC1032)
        return False
    return (data.startswith(b"AutoCAD Binary DXF") or bool(_CBL_DXF_TEXT_START_RE_V1.match(data[:256]))
            or str(name or "").lower().endswith(".dxf"))


def _cbl_free_dwg_dwg_from_dxf_v1(executable, dxf_path, dwg_path):
    """Write dwg_path (AC1018) from dxf_path; returns the converter report."""
    run = _cbl_acadsharp_run_v1([str(executable), "--dwg-from-dxf", str(dxf_path), str(dwg_path)], timeout=600)
    if run.returncode != 0 or not dwg_path.is_file() or dwg_path.read_bytes()[:6] != b"AC1018":
        detail = run.stderr.decode("utf-8", errors="replace")
        try:
            detail = str(_cbl_json.loads(detail, strict=False).get("error") or detail)
        except ValueError:
            pass
        raise RuntimeError("이 DXF 파일을 무료 변환기로 읽지 못했습니다. (" + detail.strip().splitlines()[0][:300] + ")"
                           if detail.strip() else "이 DXF 파일을 무료 변환기로 읽지 못했습니다.")
    return _cbl_json.loads(run.stdout.decode("utf-8", errors="replace"), strict=False)


def _cbl_free_dwg_converted_name_v1(name):
    stem = _cbl_os.path.splitext(_cbl_os.path.basename(str(name or "")))[0].strip()
    return (stem or "drawing") + ".dwg"


@_cbl_csrf_exempt
@_cbl_gzip_page
def cblcad_free_dwg_local_api(request):
    endpoint_started = _cbl_time.perf_counter()
    if request.method == "GET":
        enabled = _cbl_is_free_dwg_request(request)
        return _cbl_JsonResponse({
            "ok": True, "enabled": enabled, "converter": "free",
            "oda_used": False, "v29_used": False, "oda_executed": False,
        })
    if request.method != "POST":
        return _cbl_JsonResponse({"ok": False, "error": "POST 요청만 지원합니다."}, status=405)
    if not _cbl_is_free_dwg_request(request):
        return _cbl_JsonResponse({"ok": False, "enabled": False, "converter": "free",
                                  "oda_used": False, "v29_used": False,
                                  "error": "DWG 로컬 경로가 비활성화되어 있습니다."}, status=404)
    upload = next(iter(request.FILES.values()), None)
    if upload is None:
        return _cbl_JsonResponse({"ok": False, "error": "DWG 파일이 업로드되지 않았습니다."}, status=400)
    max_upload = _cbl_free_dwg_upload_limit_v1()
    if int(getattr(upload, "size", 0) or 0) > max_upload:
        _cbl_dwg_dxf_emit_log_v1(
            "CBLCAD_FREE_DWG_LOCAL endpoint=free-dwg-local event=upload_too_large bytes=%s limit=%s",
            getattr(upload, "size", 0), max_upload,
        )
        return _cbl_JsonResponse({"ok": False, "error": "DWG 업로드 제한을 초과했습니다.", "max_bytes": max_upload}, status=413)
    try:
        upload_data = b"".join(upload.chunks())
        # Local-only comparison path: ACadSharp DxfWriter output is fed into
        # the existing parseDXF/edit pipeline.  It is intentionally separate
        # from the compact response and never falls back to ODA.
        if request.GET.get("format") == "acadsharp-dxf":
            executable = _cbl_free_dwg_save_local_executable_v1()
            if executable is None:
                raise RuntimeError("로컬 ACadSharp DxfWriter 실행 파일이 설치되지 않았습니다.")
            upload_name = getattr(upload, "name", "") or "drawing.dwg"
            converted = None
            with _cbl_tempfile.TemporaryDirectory(prefix=".cbl-acadsharp-dxf-") as tmp:
                tmp_path = _cbl_Path(tmp)
                source = tmp_path / "input.dwg"
                output = tmp_path / "output.dxf"
                metadata_path = tmp_path / "metadata.json"
                convert_started = _cbl_time.perf_counter()
                if _cbl_free_dwg_is_dxf_upload_v1(upload_data, upload_name):
                    dxf_source = tmp_path / "input.dxf"
                    dxf_source.write_bytes(upload_data)
                    dxf_report = _cbl_free_dwg_dwg_from_dxf_v1(executable, dxf_source, source)
                    converted = {
                        "converted_dwg_base64": _cbl_base64.b64encode(source.read_bytes()).decode("ascii"),
                        "converted_dwg_name": _cbl_free_dwg_converted_name_v1(upload_name),
                        "dxf_dropped_objects": dxf_report.get("dropped") or {},
                    }
                else:
                    source.write_bytes(upload_data)
                # One runtime run writes the DXF and the --metadata JSON from a
                # single DWG read (a second read took ~2 s on large plans).
                run = _cbl_acadsharp_run_v1(
                    [str(executable), "--dxf", str(source), str(output), str(metadata_path)], timeout=600)
                if run.returncode != 0 or not output.is_file() or not _cbl_free_dwg_local_valid_dxf_v1(output):
                    detail = run.stderr.decode("utf-8", errors="replace")[-1200:]
                    raise RuntimeError("ACadSharp full DXF 변환 실패: " + detail)
                dxf_bytes = output.read_bytes()
                source_metadata = _cbl_json.loads(metadata_path.read_text(encoding="utf-8", errors="replace"), strict=False)
                if source_metadata.get("status") != "read":
                    raise RuntimeError("ACadSharp metadata 상태가 올바르지 않습니다.")
                dxf_text = _cbl_decode_dxf_unicode_escapes_v1(_cbl_free_dwg_dxf_text_v1(dxf_bytes))
            response = _cbl_JsonResponse({
                "ok": True, "format": "acadsharp-dxf", "converter": "free-acadsharp",
                "oda_used": False, "v29_used": False, "oda_executed": False,
                "file_sha256": hashlib.sha256(upload_data).hexdigest(),
                "dxf_bytes": len(dxf_text.encode("utf-8")), "dxf": dxf_text,
                "source_layer_manifest": _cbl_build_original_source_layer_manifest_v1(source_metadata, dxf_text),
                "unreadable_objects": _cbl_free_dwg_unreadable_objects_v1(source_metadata.get("notifications")),
                "legacy_text_lengths": _cbl_free_dwg_legacy_text_lengths_v1(source_metadata.get("notifications")),
                "misdeclared_utf8_texts": _cbl_free_dwg_misdeclared_utf8_v1(source_metadata.get("notifications")),
                "converted_from_dxf": converted is not None, **(converted or {}),
            }, json_dumps_params={"ensure_ascii": False})
            response["Server-Timing"] = "convert;dur=%.2f,response;dur=%.2f" % (
                (_cbl_time.perf_counter() - convert_started) * 1000,
                (_cbl_time.perf_counter() - endpoint_started) * 1000,
            )
            _cbl_dwg_dxf_emit_log_v1(
                "CBLCAD_FREE_DWG_LOCAL endpoint=free-dwg-local event=acadsharp_dxf upload_bytes=%s from_dxf=%s dxf_bytes=%s convert_ms=%.2f endpoint_ms=%.2f oda_executed=0",
                len(upload_data), int(converted is not None), len(dxf_text.encode("utf-8")),
                (_cbl_time.perf_counter() - convert_started) * 1000,
                (_cbl_time.perf_counter() - endpoint_started) * 1000,
            )
            return response
        payload = _cbl_free_dwg_local_convert_v1(upload_data, getattr(upload, "name", "drawing.dwg"))
        return _cbl_JsonResponse({"ok": True, "converter": "free-libredwg",
                                  "oda_used": False, "v29_used": False,
                                  **payload}, json_dumps_params={"ensure_ascii": False})
    except _CBLAcadSharpBusy as exc:
        return _cbl_JsonResponse({"ok": False, "busy": True, "oda_executed": False, "error": str(exc)}, status=503)
    except Exception as exc:
        _cbl_dwg_dxf_emit_log_v1("CBLCAD_FREE_DWG_LOCAL endpoint=free-dwg-local event=error oda_executed=0 error=%s", str(exc)[:300])
        return _cbl_JsonResponse({"ok": False, "oda_executed": False, "error": str(exc)}, status=500)
# CBL_FREE_DWG_LOCAL_INTEGRATION_V1_END


# ============================================================
# CBL_FREE_DWG_LOCAL_SAVE_V1
# Save As only.  This path is disabled by default and never falls back to ODA.
# ============================================================
_CBL_FREE_DWG_SAVE_LOCAL_SCHEMA_V1 = "cbl-free-dwg-save-local-v1-ac1018"


def _cbl_free_dwg_save_local_enabled_v1():
    """The save capability is independent from the local open capability."""
    return _cbl_os.environ.get("CBLCAD_FREE_DWG_SAVE_LOCAL", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _cbl_free_dwg_save_local_executable_v1():
    root = _cbl_Path(__file__).resolve().parent.parent
    candidates = [
        root / "tools" / "cbl_acadsharp_poc" / "runtime" / "CblAcadSharpPoc",
        root / "tools" / "cbl_acadsharp_poc" / "bin" / "Release" / "net8.0" / "CblAcadSharpPoc",
    ]
    for candidate in candidates:
        if candidate.is_file() and _cbl_os.access(candidate, _cbl_os.X_OK):
            return candidate
    return None


_CBL_FREE_DWG_DOWNLOAD_SALT_V1 = "cbl-free-dwg-download-v1"
_CBL_FREE_DWG_DOWNLOAD_MAX_AGE_V1 = 15 * 60
_CBL_FREE_DWG_DOWNLOAD_ROOT_V1 = _cbl_Path(_cbl_tempfile.gettempdir()) / "cbl-free-dwg-download-v1"
_CBL_FREE_DWG_HANDLE_MAP_ROOT_V1 = _cbl_Path(_cbl_tempfile.gettempdir()) / "cbl-free-dwg-handle-map-v1"
_CBL_FREE_DWG_HANDLE_MAP_SALT_V1 = "cbl-free-dwg-handle-map-v1"
_CBL_FREE_DWG_HANDLE_MAP_MAX_AGE_V1 = 15 * 60
_CBL_FREE_DWG_HANDLE_MAP_INLINE_LIMIT_V1 = 1024


def _cbl_free_dwg_download_cleanup_v1():
    root = _CBL_FREE_DWG_DOWNLOAD_ROOT_V1
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cutoff = _cbl_time.time() - 30 * 60
    for candidate in root.glob("*"):
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            continue


def _cbl_free_dwg_download_store_v1(payload, filename):
    _cbl_free_dwg_download_cleanup_v1()
    root = _CBL_FREE_DWG_DOWNLOAD_ROOT_V1
    file_id = uuid.uuid4().hex
    final_path = root / (file_id + ".dwg")
    temp_path = root / (file_id + ".tmp")
    with temp_path.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        _cbl_os.fsync(stream.fileno())
    _cbl_os.chmod(temp_path, 0o600)
    _cbl_os.replace(temp_path, final_path)
    token = _cbl_signing.dumps({"id": file_id, "name": filename}, salt=_CBL_FREE_DWG_DOWNLOAD_SALT_V1)
    return token


def _cbl_free_dwg_handle_map_cleanup_v1():
    root = _CBL_FREE_DWG_HANDLE_MAP_ROOT_V1
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cutoff = _cbl_time.time() - 30 * 60
    for candidate in root.glob("*"):
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            continue


def _cbl_free_dwg_handle_map_delivery_v1(output_handles):
    normalized = {
        str(key): _cbl_normalize_dwg_handle_v1(value)
        for key, value in (output_handles or {}).items()
    }
    normalized = {key: value for key, value in normalized.items() if value}
    encoded = _cbl_json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    encoded_bytes = len(encoded.encode("ascii"))
    if encoded_bytes <= _CBL_FREE_DWG_HANDLE_MAP_INLINE_LIMIT_V1:
        return encoded, encoded_bytes, False
    _cbl_free_dwg_handle_map_cleanup_v1()
    file_id = _cbl_secrets.token_hex(16)
    root = _CBL_FREE_DWG_HANDLE_MAP_ROOT_V1
    temp_path = root / (file_id + ".tmp")
    final_path = root / (file_id + ".json")
    with temp_path.open("w", encoding="ascii") as stream:
        stream.write(encoded)
        stream.flush()
        _cbl_os.fsync(stream.fileno())
    _cbl_os.chmod(temp_path, 0o600)
    _cbl_os.replace(temp_path, final_path)
    token = _cbl_signing.dumps({"id": file_id}, salt=_CBL_FREE_DWG_HANDLE_MAP_SALT_V1)
    return "handle-map-token:" + token, len(("handle-map-token:" + token).encode("ascii")), True


def _cbl_free_dwg_dxf_export_v1(dwg_path, work_dir):
    """(DXF bytes, {type: count} not written) of a saved AC1018 DWG.

    Written by the runtime the open uses: blocks, dimensions and styles come
    through as they are in the DWG; text is in the drawing's code page.
    ACadSharp names the Korean code page "kcs5601"; the header gets AutoCAD's
    name for it, ANSI_949.  ACadSharp's DXF writer has no REGION/3DSOLID;
    those stay in the DWG and are counted for the editor to tell.
    """
    executable = _cbl_free_dwg_save_local_executable_v1()
    if executable is None:
        raise RuntimeError("ACadSharp DXF runtime을 찾지 못했습니다.")
    output = _cbl_Path(work_dir) / "export.dxf"
    run = _cbl_acadsharp_run_v1([str(executable), "--dxf", str(dwg_path), str(output)], timeout=600)
    if run.returncode != 0 or not output.is_file() or not _cbl_free_dwg_local_valid_dxf_v1(output):
        raise RuntimeError("DXF 변환 실패: " + run.stderr.decode("utf-8", errors="replace")[-600:])
    skipped = {}
    try:
        notifications = _cbl_json.loads(run.stdout.decode("utf-8", errors="replace"), strict=False).get("notifications") or []
    except ValueError:
        notifications = []
    for item in notifications:
        message = str(item.get("Message") or "") if isinstance(item, dict) else ""
        match = _cbl_re.match(r"Entity type not implemented ACadSharp\.Entities\.(\w+)", message)
        if match:
            name = match.group(1).upper()
            skipped[name] = skipped.get(name, 0) + 1
    payload = _cbl_re.sub(rb"(\$DWGCODEPAGE\r?\n\s*3\r?\n)kcs5601(?=\r?\n)", rb"\1ANSI_949",
                          output.read_bytes(), count=1, flags=_cbl_re.IGNORECASE)
    return payload, skipped


def cblcad_free_dwg_download_api(request, token):
    if request.method != "GET" or not _cbl_is_free_dwg_request(request):
        return _cbl_JsonResponse({"ok": False, "error": "다운로드를 찾을 수 없습니다."}, status=404)
    if request.GET.get("handle-map") == "1":
        try:
            data = _cbl_signing.loads(
                token,
                salt=_CBL_FREE_DWG_HANDLE_MAP_SALT_V1,
                max_age=_CBL_FREE_DWG_HANDLE_MAP_MAX_AGE_V1,
            )
        except _cbl_signing.SignatureExpired:
            return _cbl_JsonResponse({"ok": False, "error": "handle mapping이 만료되었습니다."}, status=410)
        except _cbl_signing.BadSignature:
            return _cbl_JsonResponse({"ok": False, "error": "유효하지 않은 handle mapping입니다."}, status=404)
        file_id = str(data.get("id", ""))
        if not _cbl_re.fullmatch(r"[0-9a-f]{32}", file_id):
            return _cbl_JsonResponse({"ok": False, "error": "유효하지 않은 handle mapping입니다."}, status=404)
        map_path = _CBL_FREE_DWG_HANDLE_MAP_ROOT_V1 / (file_id + ".json")
        try:
            if not map_path.is_file():
                return _cbl_JsonResponse({"ok": False, "error": "handle mapping을 찾을 수 없습니다."}, status=404)
            mapping = _cbl_json.loads(map_path.read_text(encoding="ascii"))
            if not isinstance(mapping, dict):
                return _cbl_JsonResponse({"ok": False, "error": "handle mapping 형식이 올바르지 않습니다."}, status=404)
            return _cbl_JsonResponse({"ok": True, "output_handles": mapping}, json_dumps_params={"ensure_ascii": True})
        except (OSError, ValueError, TypeError, _cbl_json.JSONDecodeError):
            return _cbl_JsonResponse({"ok": False, "error": "handle mapping을 읽을 수 없습니다."}, status=404)
    try:
        data = _cbl_signing.loads(
            token,
            salt=_CBL_FREE_DWG_DOWNLOAD_SALT_V1,
            max_age=_CBL_FREE_DWG_DOWNLOAD_MAX_AGE_V1,
        )
    except _cbl_signing.SignatureExpired:
        return _cbl_JsonResponse({"ok": False, "error": "다운로드 링크가 만료되었습니다."}, status=410)
    except _cbl_signing.BadSignature:
        return _cbl_JsonResponse({"ok": False, "error": "유효하지 않은 다운로드 링크입니다."}, status=404)
    file_id = str(data.get("id", ""))
    if not _cbl_re.fullmatch(r"[0-9a-f]{32}", file_id):
        return _cbl_JsonResponse({"ok": False, "error": "유효하지 않은 다운로드 링크입니다."}, status=404)
    path = _CBL_FREE_DWG_DOWNLOAD_ROOT_V1 / (file_id + ".dwg")
    try:
        if not path.is_file() or path.stat().st_size < 6 or path.read_bytes()[:6] != b"AC1018":
            return _cbl_JsonResponse({"ok": False, "error": "다운로드 파일을 찾을 수 없습니다."}, status=404)
        filename = str(data.get("name") or "ChickenBananaCAD.dwg")
        filename = _cbl_os.path.basename(filename)
        filename = _cbl_re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", filename).strip() or "ChickenBananaCAD.dwg"
        if not filename.lower().endswith(".dwg"):
            filename += ".dwg"
        filename = _cbl_re.sub(r"(\.dwg)+$", ".dwg", filename, flags=_cbl_re.IGNORECASE)
        from urllib.parse import quote
        ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "ChickenBananaCAD.dwg"
        response = _cbl_FileResponse(path.open("rb"), content_type="application/acad", as_attachment=True, filename=ascii_name)
        response["Content-Disposition"] = (
            f'attachment; filename="{ascii_name.replace(chr(34), "")}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}"
        )
        response["Content-Length"] = str(path.stat().st_size)
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
    except OSError:
        return _cbl_JsonResponse({"ok": False, "error": "다운로드 파일을 읽을 수 없습니다."}, status=404)


def _cbl_free_dwg_acadsharp_metadata_v1(path):
    executable = _cbl_free_dwg_save_local_executable_v1()
    if executable is None:
        raise RuntimeError("ACadSharp metadata runtime을 찾지 못했습니다.")
    result = _cbl_acadsharp_run_v1([str(executable), "--metadata", str(path)], timeout=300)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("ACadSharp 저장본 metadata 재판독 실패: " + result.stderr.decode(errors="replace")[-800:])
    report = _cbl_json.loads(result.stdout.decode("utf-8", errors="replace"), strict=False)
    if report.get("status") != "read":
        raise RuntimeError("ACadSharp metadata 재판독 상태가 올바르지 않습니다.")
    return report


def _cbl_free_dwg_to_dxf_text_v1(path, timeout=600, wait=_CBL_ACADSHARP_WAIT_V1, background=False):
    """(DXF text, metadata) of a DWG through the free ACadSharp runtime.

    The text is what the editor gets from the open API: decoded with the
    drawing's code page and with \\U+XXXX turned back into characters.  The
    metadata comes from the same read.  Used by the quantity tool; `wait` and
    `background` go to the converter slot (_cbl_acadsharp_slot_v1).
    """
    executable = _cbl_free_dwg_save_local_executable_v1()
    if executable is None:
        raise RuntimeError("ACadSharp DXF runtime을 찾지 못했습니다.")
    with _cbl_tempfile.TemporaryDirectory(prefix=".cbl-acadsharp-dxf-") as tmp:
        output = _cbl_Path(tmp) / "output.dxf"
        metadata_path = _cbl_Path(tmp) / "metadata.json"
        run = _cbl_acadsharp_run_v1([str(executable), "--dxf", str(path), str(output), str(metadata_path)],
                                    timeout=timeout, wait=wait, background=background)
        if run.returncode != 0 or not output.is_file() or not _cbl_free_dwg_local_valid_dxf_v1(output):
            raise RuntimeError("ACadSharp DXF 변환 실패: " + run.stderr.decode("utf-8", errors="replace")[-800:])
        metadata = _cbl_json.loads(metadata_path.read_text(encoding="utf-8", errors="replace"), strict=False)
        return _cbl_decode_dxf_unicode_escapes_v1(_cbl_free_dwg_dxf_text_v1(output.read_bytes())), metadata


def _cbl_free_dwg_save_local_json_v1(path, dwgread):
    if not dwgread:
        return _cbl_free_dwg_acadsharp_json_v1(_cbl_free_dwg_acadsharp_metadata_v1(path))
    result = _cbl_subprocess.run(
        [dwgread, "-O", "JSON", str(path)],
        stdout=_cbl_subprocess.PIPE,
        stderr=_cbl_subprocess.PIPE,
        timeout=300,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("LibreDWG 저장본 재판독 실패: " + result.stderr.decode(errors="replace")[-800:])
    text = result.stdout.decode("utf-8", errors="replace")
    # LibreDWG can emit bare lowercase `nan` for undefined optional values.
    # Normalize only that token outside quoted strings so validation remains
    # strict for all other malformed output.
    text = _cbl_re.sub(r'(?<![A-Za-z0-9_\"])nan(?![A-Za-z0-9_\"])', "null", text)
    parsed = _cbl_json.loads(text, strict=False)
    # LibreDWG remains the source for its rich handle/object JSON, while the
    # ACadSharp metadata pass supplies the block/layout/style structure that
    # must survive repeated saves.
    parsed["semanticManifest"] = _cbl_free_dwg_acadsharp_metadata_v1(path).get("semanticManifest")
    return parsed


def _cbl_free_dwg_acadsharp_json_v1(report):
    """An ACadSharp metadata report in the LibreDWG JSON shape the save checks use."""
    type_map = {
        "DIMENSIONALIGNED": "DIMENSION_ALIGNED",
        "DIMENSIONLINEAR": "DIMENSION_LINEAR",
        "DIMENSIONANGULAR": "DIMENSION_ANGULAR",
        "DIMENSIONRADIUS": "DIMENSION_RADIUS",
        "DIMENSIONDIAMETER": "DIMENSION_DIAMETER",
    }
    objects = []
    for layer in report.get("layers", []):
        objects.append({
            "object": "LAYER", "handle": layer.get("handle"),
            "name": layer.get("name"), "ownerhandle": layer.get("owner"),
        })
    for item in report.get("entities", []):
        entity = str(item.get("type") or "").upper()
        entity = type_map.get(entity, entity)
        row = {
            "entity": entity, "handle": item.get("handle"),
            "ownerhandle": item.get("owner"),
            "space": item.get("space"),
        }
        layer = item.get("layer") or {}
        if layer.get("handle") is not None:
            row["layer"] = layer.get("handle")
        if "text" in item:
            row["text"] = item.get("text")
        if item.get("block"):
            row["block_header"] = (item.get("block") or {}).get("handle")
            row["block_name"] = (item.get("block") or {}).get("name")
        objects.append(row)
    return {
        "OBJECTS": objects,
        "semanticManifest": report.get("semanticManifest"),
        "acadsharpEntities": report.get("entities", []),
        "_cbl_validation_source": "acadsharp-metadata",
    }


def _cbl_free_dwg_reread_metadata_json_v1(path):
    """The validation JSON of the writer's own reread (--reread-metadata), or None.

    It is the --metadata report of the saved file, made by the read the writer
    does anyway, so the save needs no separate process to read it again.
    """
    try:
        report = _cbl_json.loads(_cbl_Path(path).read_text(encoding="utf-8", errors="replace"), strict=False)
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or report.get("status") != "read":
        return None
    return _cbl_free_dwg_acadsharp_json_v1(report)


class _CBLFreeDwgSaveValidationError(ValueError):
    """A client operation cannot be applied to the uploaded source DWG."""

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics if isinstance(diagnostics, dict) else None


class _CBLLocalFileConflict(ValueError):
    """The server-side source or target changed after the native picker."""


def _cbl_resolve_local_file_record_v1(request, token, require_exists=True):
    records = _cbl_local_file_tokens_v1(request)
    record = records.get(str(token or ""))
    if not isinstance(record, dict):
        raise _CBLFreeDwgSaveValidationError("로컬 파일 토큰이 만료되었거나 현재 세션에 없습니다.")
    path = _cbl_Path(str(record.get("path", ""))).resolve()
    if not path.is_absolute() or path.parent == _cbl_Path("/"):
        raise _CBLFreeDwgSaveValidationError("로컬 파일 경로가 올바르지 않습니다.")
    if not path.exists():
        if require_exists:
            raise _CBLLocalFileConflict("원본 파일이 사라졌습니다.")
        return path, record
    current = _cbl_local_file_fingerprint_v1(path)
    expected = {key: record.get(key) for key in ("size", "mtime_ns", "sha256")}
    if any(current.get(key) != expected.get(key) for key in expected):
        raise _CBLLocalFileConflict("외부 프로그램에서 파일이 변경되었습니다. 다시 열거나 다른 이름으로 저장하세요.")
    return path, record


def _cbl_atomic_replace_local_file_v1(path, payload):
    path = _cbl_Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    backup = path.with_name(path.name + ".cblcad.bak")
    backup_tmp = parent / ("." + backup.name + "." + _cbl_secrets.token_hex(8) + ".tmp")
    output_tmp = parent / ("." + path.name + "." + _cbl_secrets.token_hex(8) + ".tmp")
    try:
        if path.exists():
            _cbl_shutil.copy2(path, backup_tmp)
            with backup_tmp.open("rb") as stream:
                os.fsync(stream.fileno())
            _cbl_os.replace(backup_tmp, backup)
        with output_tmp.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            _cbl_os.fsync(stream.fileno())
        _cbl_os.chmod(output_tmp, mode)
        _cbl_os.replace(output_tmp, path)
        directory_fd = _cbl_os.open(str(parent), _cbl_os.O_RDONLY)
        try:
            _cbl_os.fsync(directory_fd)
        finally:
            _cbl_os.close(directory_fd)
        if _cbl_local_file_fingerprint_v1(path)["sha256"] != hashlib.sha256(payload).hexdigest():
            raise RuntimeError("저장 후 파일 검증에 실패했습니다.")
    except Exception:
        for candidate in (backup_tmp, output_tmp):
            try:
                candidate.unlink()
            except OSError:
                pass
        raise


def _cbl_normalize_dwg_handle_v1(value):
    """Canonical, lossless handle form shared with the browser save bridge."""
    if value is None:
        return ""
    if isinstance(value, list):
        value = value[-1] if value else ""
        # LibreDWG JSON encodes a handle as a chunk array whose final numeric
        # value is decimal.  Browser/ACadSharp operations use hexadecimal
        # strings; convert only this JSON-array numeric representation.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return format(int(value), "X")
    text = str(value).strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    text = text.upper()
    if not text or not _cbl_re.fullmatch(r"[0-9A-F]+", text):
        return ""
    text = text.lstrip("0")
    return text or "0"


def _cbl_normalize_free_dwg_ops_v1(original_json, ops):
    """Normalize and validate edit targets before ACadSharp is invoked.

    Display-only/block-child shapes must carry an owner/source handle.  They
    are never allowed to become independent DWG operations merely because a
    renderer assigned them an id.
    """
    entities = [item for item in original_json.get("OBJECTS", []) if item.get("entity")]
    index = {}
    packed_index = {}
    for item in entities:
        handle = _cbl_normalize_dwg_handle_v1(item.get("handle"))
        if handle:
            index.setdefault(handle, item)
        packed = item.get("handle")
        if isinstance(packed, (list, tuple)) and packed:
            try:
                packed_value = int(packed[-1])
            except (TypeError, ValueError):
                packed_value = None
            if packed_value is not None:
                packed_index.setdefault(packed_value & 0xFFFF, []).append(item)
    block_names = {
        _cbl_normalize_dwg_handle_v1(item.get("handle")): str(item.get("name") or "")
        for item in original_json.get("OBJECTS", [])
        if item.get("object") == "BLOCK_HEADER" and _cbl_normalize_dwg_handle_v1(item.get("handle"))
    }

    def point(value):
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return float(value[0]), float(value[1])
            except (TypeError, ValueError):
                return None
        return None

    def resolve_by_identity(raw):
        entity_type = str(raw.get("entity") or "").upper()
        if entity_type == "LINE":
            wanted_start, wanted_end = point(raw.get("start")), point(raw.get("end"))
            if wanted_start is None or wanted_end is None:
                return []
            result = []
            for item in entities:
                if str(item.get("entity") or "").upper() != "LINE":
                    continue
                actual_start, actual_end = point(item.get("start")), point(item.get("end"))
                if actual_start is None or actual_end is None:
                    continue
                direct = all(abs(actual_start[i] - wanted_start[i]) <= 1e-5 for i in (0, 1)) and all(abs(actual_end[i] - wanted_end[i]) <= 1e-5 for i in (0, 1))
                reverse = all(abs(actual_start[i] - wanted_end[i]) <= 1e-5 for i in (0, 1)) and all(abs(actual_end[i] - wanted_start[i]) <= 1e-5 for i in (0, 1))
                if direct or reverse:
                    result.append(item)
            return result
        if entity_type != "INSERT":
            return []
        wanted_name = str(raw.get("blockName") or raw.get("name") or "").strip().upper()
        wanted_point = point(raw.get("insert"))
        if not wanted_name or wanted_point is None:
            return []
        result = []
        for item in entities:
            if str(item.get("entity") or "").upper() != "INSERT":
                continue
            block_ref = _cbl_normalize_dwg_handle_v1(item.get("block_header"))
            actual_name = block_names.get(block_ref, "").strip().upper()
            actual_point = point(item.get("ins_pt") or item.get("insert"))
            if actual_name != wanted_name or actual_point is None:
                continue
            if abs(actual_point[0] - wanted_point[0]) <= 1e-5 and abs(actual_point[1] - wanted_point[1]) <= 1e-5:
                result.append(item)
        return result
    normalized = []
    seen_delete = set()
    mutation_index = {}
    for op_index, raw in enumerate(ops):
        if not isinstance(raw, dict):
            raise _CBLFreeDwgSaveValidationError(
                f"편집 명령 {op_index}의 형식이 올바르지 않습니다.")
        kind = str(raw.get("type", "")).strip().lower()
        if kind not in {"delete", "update", "move", "transform"}:
            normalized.append(raw)
            continue
        handle = _cbl_normalize_dwg_handle_v1(
            raw.get("sourceHandle") or raw.get("originalHandle") or raw.get("handle")
        )
        if not handle:
            raise _CBLFreeDwgSaveValidationError(
                f"편집 명령 {op_index}에 원본 handle이 없습니다: "
                f"{_cbl_json.dumps(raw, ensure_ascii=False, sort_keys=True)}"
            )
        # If a flattened display child was submitted, resolve it only through
        # an explicit owner/source handle; never infer ownership from id.
        source = index.get(handle)
        if source is None:
            owner = _cbl_normalize_dwg_handle_v1(
                raw.get("ownerSourceHandle") or raw.get("ownerHandle") or
                raw.get("parentSourceHandle") or raw.get("parentHandle") or
                raw.get("blockHandle")
            )
            if owner and owner in index:
                handle = owner
                source = index[owner]
        if source is None and handle:
            # LibreDWG's JSON handle representation can retain only the low
            # 16-bit component in a packed array, while the ACadSharp report
            # and browser operation carry the full canonical hex handle.  Use
            # this only when the low component maps to exactly one entity of
            # the requested type; ambiguous values remain rejected.
            try:
                packed_candidates = packed_index.get(int(handle, 16) & 0xFFFF, [])
            except ValueError:
                packed_candidates = []
            entity_type = str(raw.get("entity") or "").upper()
            if entity_type:
                packed_candidates = [
                    candidate for candidate in packed_candidates
                    if str(candidate.get("entity") or "").upper() == entity_type
                ]
            if len(packed_candidates) == 1:
                source = packed_candidates[0]
        if source is None:
            identity_matches = resolve_by_identity(raw)
            if len(identity_matches) == 1:
                source = identity_matches[0]
                # LibreDWG JSON may expose handles as a packed numeric array,
                # while ACadSharp and the browser use the canonical hex
                # string.  When the exact geometry identity match is unique,
                # retain the caller's canonical handle for ACadSharp instead
                # of converting that packed representation to an empty
                # handle.  Ambiguous or handle-less matches remain rejected.
                resolved_handle = _cbl_normalize_dwg_handle_v1(source.get("handle"))
                handle = resolved_handle or handle
        if source is None:
            fields = {
                key: raw.get(key)
                for key in ("handle", "sourceHandle", "originalHandle", "ownerHandle",
                            "parentHandle", "blockHandle", "entity", "type")
                if key in raw
            }
            raise _CBLFreeDwgSaveValidationError(
                "저장 검증 실패: 원본 DWG에 없는 handle입니다: "
                f"{_cbl_json.dumps({'op_index': op_index, 'fields': fields}, ensure_ascii=False, sort_keys=True)}"
            )
        item = dict(raw)
        item["handle"] = handle
        item["sourceHandle"] = handle
        if kind == "delete":
            if handle in seen_delete:
                continue
            seen_delete.add(handle)
            # Delete is the final state for this entity.  A stale update/move
            # collected before it must not be applied afterward.
            normalized = [x for x in normalized if _cbl_normalize_dwg_handle_v1(x.get("handle")) != handle]
            mutation_index.pop(handle, None)
            normalized.append(item)
            continue
        if handle in seen_delete or handle in mutation_index:
            continue
        mutation_index[handle] = len(normalized)
        normalized.append(item)
    return normalized


def _cbl_free_dwg_save_local_validate_v1(original, saved, dwgread, ops=None, acad_report=None,
                                         original_json=None, saved_json=None):
    """Refuse a saved DWG that lost or changed anything the edits do not explain.

    `original_json` / `saved_json` are reads the caller already has (the edit
    target read of the original; the writer's own reread of the saved file);
    without them both files are read here.
    """
    # The writer saves only what ACadSharp read; an object it could not read
    # would silently disappear, and the checks below (ACadSharp on both
    # sides) would not notice.
    unreadable = _cbl_free_dwg_unreadable_objects_v1((acad_report or {}).get("notifications"))
    if unreadable:
        raise RuntimeError(_cbl_free_dwg_unreadable_message_v1(unreadable))
    if original_json is None:
        original_json = _cbl_free_dwg_save_local_json_v1(original, dwgread)
    if saved_json is None:
        saved_json = _cbl_free_dwg_save_local_json_v1(saved, dwgread)

    def canonical_entity_type(value):
        raw = str(value or "").strip().upper()
        return {
            "LINEENTITY": "LINE",
            "TEXTENTITY": "TEXT",
            "MTEXTENTITY": "MTEXT",
            "DIMENSIONALIGNED": "DIMENSION_ALIGNED",
            "DIMENSIONLINEAR": "DIMENSION_LINEAR",
            "DIMENSIONANGULAR": "DIMENSION_ANGULAR",
            "DIMENSIONRADIUS": "DIMENSION_RADIUS",
            "DIMENSIONDIAMETER": "DIMENSION_DIAMETER",
        }.get(raw, raw)

    def entities(document):
        return [item for item in document.get("OBJECTS", []) if item.get("entity")]

    structural_entity_types = {
        "SECTION", "ENDSEC", "TABLE", "ENDTAB", "BLOCK", "ENDBLK",
        "BLOCK_RECORD", "EOF",
    }

    def is_model_entity(item):
        return canonical_entity_type(item.get("entity")) not in structural_entity_types

    def counts(document):
        result = {}
        for item in entities(document):
            if not is_model_entity(item):
                continue
            key = canonical_entity_type(item.get("entity"))
            result[key] = result.get(key, 0) + 1
        return result

    def canonical_ref(value):
        return _cbl_normalize_dwg_handle_v1(value)

    def handle(item):
        return canonical_ref(item.get("handle"))

    def normalize_text_value(value):
        # TEXT and MTEXT may encode the same line breaks differently.  Only
        # representation noise is normalized; actual content remains strict.
        return str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\\P", "\n")

    def number(value, default=None):
        try:
            if value is None:
                return default
            return round(float(value), 7)
        except (TypeError, ValueError):
            return default

    def point(value):
        if isinstance(value, dict):
            value = value.get("point", value.get("position", value.get("insert")))
        if isinstance(value, (list, tuple)):
            return tuple(number(value[index], 0.0) for index in range(min(3, len(value)))) + tuple(0.0 for _ in range(max(0, 3 - len(value))))
        return None

    def text_family(item):
        raw_type = str(item.get("entity", item.get("type", ""))).strip().upper()
        if raw_type in {"TEXT", "TEXTENTITY", "MTEXT", "MTEXTENTITY", "ATTRIB", "ATTDEF"}:
            insert = item.get("insert") or {}
            if not isinstance(insert, dict):
                insert = {"point": insert}
            return {
                "family": "TEXT",
                "text": normalize_text_value(item.get("text", item.get("value", ""))),
                "point": point(insert.get("point", insert.get("position"))),
                "rotation": number(insert.get("rotation", item.get("rotation")), 0.0),
                "height": number(insert.get("height", item.get("height", item.get("textHeight"))), None),
            }
        return None

    def text_fingerprints(document):
        values = []
        for item in entities(document):
            fingerprint = text_family(item)
            if fingerprint:
                values.append(fingerprint)
        return values

    def acad_text_fingerprints(report_document):
        values = []
        for item in (report_document or {}).get("ModelSpaceEntities", []):
            fingerprint = text_family(item)
            if fingerprint:
                values.append(fingerprint)
        return values

    def fingerprint_key(value):
        return _cbl_json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def fingerprint_counter(values):
        result = {}
        for value in values:
            key = fingerprint_key(value)
            result[key] = result.get(key, 0) + 1
        return result

    def named_objects(document, object_type):
        return sorted(item.get("name", "") for item in document.get("OBJECTS", []) if item.get("object") == object_type and item.get("name") is not None)

    # R2013+ drawings keep REGION ACIS data in the AcDs section, which LibreDWG
    # reads as empty; the save writes it into the entity.  Those payloads are
    # compared on the ACadSharp report (dataSha256) below instead.
    stored_region_payloads = {
        _cbl_json.dumps(item.get("handle")) for item in entities(original_json)
        if item.get("entity") == "REGION" and not any(item.get("acis_data") or [])
    }

    def region_signatures(document):
        values = []
        for item in entities(document):
            if item.get("entity") != "REGION":
                continue
            stored = _cbl_json.dumps(item.get("handle")) in stored_region_payloads
            values.append(_cbl_json.dumps({
                "layer": item.get("layer"),
                "ownerhandle": canonical_ref(item.get("ownerhandle")),
                "acis_data": None if stored else item.get("acis_data"),
            }, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        return sorted(values)

    before = counts(original_json)
    after = counts(saved_json)
    expected = dict(before)
    operation_deltas = {}
    operation_sources = []
    expected_texts = fingerprint_counter(text_fingerprints(original_json))
    expected_layers = named_objects(original_json, "LAYER")
    expected_blocks = named_objects(original_json, "BLOCK_HEADER")

    def semantic_type_counts(manifest):
        model = (manifest or {}).get("modelspace") or {}
        raw = model.get("typeCounts") or model.get("childTypeCounts") or {}
        result = {}
        aliases = {
            "TEXTENTITY": "TEXT",
            "DIMENSIONLINEAR": "DIMENSION_LINEAR",
            "DIMENSIONALIGNED": "DIMENSION_ALIGNED",
            "DIMENSIONANGULAR": "DIMENSION_ANGULAR",
            "DIMENSIONRADIUS": "DIMENSION_RADIUS",
            "DIMENSIONDIAMETER": "DIMENSION_DIAMETER",
        }
        for key, value in raw.items():
            canonical = aliases.get(str(key).upper(), str(key).upper())
            result[canonical] = result.get(canonical, 0) + int(value or 0)
        return result

    def semantic_index(manifest):
        return {
            "modelspace": semantic_type_counts(manifest),
            "paperspace": ((manifest or {}).get("paperspace") or {}).get("typeCounts") or {},
            "layouts": {
                str(item.get("name")): item for item in ((manifest or {}).get("layouts") or [])
            },
            "blocks": {
                str(item.get("name")): item for item in ((manifest or {}).get("blocks") or [])
            },
            "styles": (manifest or {}).get("styles") or {},
            "unsupported": (manifest or {}).get("unsupported") or {},
            "inserts": (manifest or {}).get("inserts") or {},
        }

    original_semantic = original_json.get("semanticManifest")
    saved_semantic = saved_json.get("semanticManifest")
    if not isinstance(original_semantic, dict) or not isinstance(saved_semantic, dict):
        raise _CBLFreeDwgSaveValidationError(
            "저장 검증 실패: DWG 구조 manifest를 생성하지 못했습니다.",
            {"semantic_manifest_available": False},
        )
    original_semantic_index = semantic_index(original_semantic)
    saved_semantic_index = semantic_index(saved_semantic)
    expected_semantic_counts = dict(original_semantic_index["modelspace"])
    semantic_deltas = {}
    structural_ops = {"add_dimension": False}
    # Kinds the manifest lists as unsupported that delete ops removed.
    deleted_unsupported = {}
    copied_unsupported = {}
    unsupported_names = {"_3DFACE": "FACE3D", "3DFACE": "FACE3D", "MLEADER": "MULTILEADER"}

    def add_delta(entity_type, amount, op_index, operation_type):
        entity_type = canonical_entity_type(entity_type)
        if not entity_type or not amount:
            return
        expected[entity_type] = expected.get(entity_type, 0) + int(amount)
        operation_deltas[entity_type] = operation_deltas.get(entity_type, 0) + int(amount)
        operation_sources.append({
            "op_index": op_index,
            "entity": entity_type,
            "operation_type": operation_type,
            "delta": int(amount),
        })

    def add_semantic_delta(entity_type, amount):
        canonical = canonical_entity_type(entity_type)
        expected_semantic_counts[canonical] = expected_semantic_counts.get(canonical, 0) + int(amount)
        semantic_deltas[canonical] = semantic_deltas.get(canonical, 0) + int(amount)

    operation_entity_types = {
        "add_line": "LINE",
        "add_test_line": "LINE",
        "add_circle": "CIRCLE",
        "add_arc": "ARC",
        "add_lwpolyline": "LWPOLYLINE",
        "add_polyline": "POLYLINE",
        "add_text": "TEXT",
        "add_mtext": "MTEXT",
        "add_hatch": "HATCH",
        "add_insert": "INSERT",
    }
    for op_index, op in enumerate(ops or []):
        kind = str(op.get("type", "")).lower()
        entity_type = operation_entity_types.get(kind)
        if entity_type:
            add_delta(entity_type, 1, op_index, kind)
            add_semantic_delta(entity_type, 1)
            if kind in ("add_text", "add_mtext"):
                expected_texts[fingerprint_key({
                    "family": "TEXT",
                    "text": normalize_text_value(op.get("text", op.get("value", ""))),
                    "point": point(op.get("insert")),
                    "rotation": number(op.get("rotation"), 0.0),
                    "height": number(op.get("height"), None),
                })] = expected_texts.get(fingerprint_key({
                    "family": "TEXT",
                    "text": normalize_text_value(op.get("text", op.get("value", ""))),
                    "point": point(op.get("insert")),
                    "rotation": number(op.get("rotation"), 0.0),
                    "height": number(op.get("height"), None),
                }), 0) + 1
        elif kind == "add_dimension":
            structural_ops["add_dimension"] = True
            # ACadSharp materializes a DIMENSION's anonymous definition block
            # when Dimension.UpdateBlock() is called.  LibreDWG therefore
            # reports the new dimension together with its generated BLOCK /
            # ENDBLK and visible helper geometry.  These are part of the one
            # requested dimension, not loss or mutation of source entities.
            dimension_kind = str(op.get("dimensionKind", "aligned")).lower()
            dimension_entity = "DIMENSION_LINEAR" if dimension_kind == "linear" else "DIMENSION_ALIGNED"
            generated = {
                dimension_entity: 1,
                "LINE": 3,
                "MTEXT": 1,
                "POINT": 4,
                "SOLID": 2,
            }
            for generated_type, amount in generated.items():
                add_delta(generated_type, amount, op_index, kind)
            add_semantic_delta(dimension_entity, 1)
        elif kind == "add_copy":
            # A copy of an existing entity: one more of its kind (a copied
            # DIMENSION also brings its own anonymous block).
            target = canonical_ref(op.get("copyOf"))
            source = next((item for item in entities(original_json) if handle(item) == target), None)
            if source is None:
                raise RuntimeError(f"저장 검증 실패: 복사 원본 handle을 찾지 못했습니다: {op.get('copyOf')}")
            source_type = source.get("entity")
            raw_type = str(source_type or "").upper()
            raw_type = unsupported_names.get(raw_type, raw_type)
            copied_unsupported[raw_type] = copied_unsupported.get(raw_type, 0) + 1
            add_delta(source_type, 1, op_index, kind)
            add_semantic_delta(source_type, 1)
            if canonical_entity_type(source_type).startswith("DIMENSION"):
                structural_ops["add_dimension"] = True
                # The writer names the source's block; its contents are counted
                # from the original file, once more for the copy.
                applied = ((acad_report or {}).get("editReport") or {}).get("applied") or []
                source_block = str((applied[op_index] or {}).get("sourceBlock") or "") if op_index < len(applied) and isinstance(applied[op_index], dict) else ""
                for item in (original_json.get("acadsharpEntities") or []):
                    if source_block and str(item.get("space") or "") == "block:" + source_block:
                        add_delta(item.get("type"), 1, op_index, kind)
        elif kind == "create_layer":
            name = str(op.get("name", ""))
            if name not in expected_layers:
                expected_layers = sorted(expected_layers + [name])
        elif kind == "delete":
            target = canonical_ref(op.get("handle"))
            source = next((item for item in entities(original_json) if handle(item) == target), None)
            if source is None:
                raise RuntimeError(f"저장 검증 실패: 삭제 대상 handle을 찾지 못했습니다: {op.get('handle')}")
            source_type = source.get("entity")
            raw_type = str(source_type or "").upper()
            raw_type = unsupported_names.get(raw_type, raw_type)
            deleted_unsupported[raw_type] = deleted_unsupported.get(raw_type, 0) + 1
            add_delta(source_type, -1, op_index, kind)
            source_space = str(source.get("space") or "").lower()
            if not source_space or source_space == "modelspace":
                add_semantic_delta(source_type, -1)
            if canonical_entity_type(source_type) in ("TEXT", "MTEXT"):
                source_text = text_family(source)
                if source_text:
                    key = fingerprint_key(source_text)
                    expected_texts[key] = expected_texts.get(key, 0) - 1
        elif kind == "update" and str(op.get("entity", "")).upper() in ("TEXT", "MTEXT"):
            target = canonical_ref(op.get("handle"))
            source = next((item for item in entities(original_json) if handle(item) == target), None)
            if source is None:
                # LibreDWG and ACadSharp can expose different handle
                # representations for the same top-level text entity.  The
                # writer has already applied the operation against the
                # ACadSharp model; use its modelspace handle report as a
                # strict fallback, never as permission to create an entity.
                acad_source = (acad_report or {}).get("source", {})
                target_text_type = str(op.get("entity", "")).upper()
                acad_type = "TextEntity" if target_text_type == "TEXT" else "MText"
                source = next((item for item in acad_source.get("ModelSpaceEntities", [])
                               if str(item.get("handle", "")).upper() == str(target or "").upper()
                               and str(item.get("entity", "")) == acad_type), None)
                if source is None:
                    raise RuntimeError(f"저장 검증 실패: 수정 대상 handle을 찾지 못했습니다: {op.get('handle')}")
            old_text = text_family(source)
            if old_text:
                old_key = fingerprint_key(old_text)
                expected_texts[old_key] = expected_texts.get(old_key, 0) - 1
                new_text = dict(old_text)
                if "text" in op or "value" in op:
                    new_text["text"] = normalize_text_value(op.get("text", op.get("value", old_text["text"])))
                if "insert" in op:
                    new_text["point"] = point(op.get("insert"))
                if "rotation" in op:
                    new_text["rotation"] = number(op.get("rotation"), 0.0)
                if "height" in op:
                    new_text["height"] = number(op.get("height"), None)
                new_key = fingerprint_key(new_text)
                expected_texts[new_key] = expected_texts.get(new_key, 0) + 1

    def semantic_mismatches():
        mismatches = {}
        # Deleting the last entity of a type leaves a 0 in the expectation, while
        # the output index lists only types that exist.
        expected_semantic_counts = {k: v for k, v in expected_counts_all.items() if v}
        if expected_semantic_counts != saved_semantic_index["modelspace"]:
            mismatches["modelspace.typeCounts"] = {
                "original": original_semantic_index["modelspace"],
                "expected": expected_semantic_counts,
                "output": saved_semantic_index["modelspace"],
            }
        if original_semantic_index["paperspace"] != saved_semantic_index["paperspace"]:
            mismatches["paperspace.typeCounts"] = {
                "original": original_semantic_index["paperspace"],
                "output": saved_semantic_index["paperspace"],
            }
        for name, before_block in original_semantic_index["blocks"].items():
            after_block = saved_semantic_index["blocks"].get(name)
            if after_block is None:
                mismatches[f"blocks.{name}"] = {"original": before_block, "output": None}
                continue
            if name == "*Model_Space":
                expected_model_count = sum(expected_semantic_counts.values())
                output_model_types = semantic_type_counts({"modelspace": after_block})
                if int(after_block.get("childCount", 0)) != expected_model_count or output_model_types != expected_semantic_counts:
                    mismatches[f"blocks.{name}.childCount"] = {
                        "original": before_block.get("childCount"),
                        "expected": expected_model_count,
                        "output": after_block.get("childCount"),
                        "expectedTypeCounts": expected_semantic_counts,
                        "outputTypeCounts": output_model_types,
                    }
                continue
            if int(after_block.get("childCount", 0)) < int(before_block.get("childCount", 0)):
                mismatches[f"blocks.{name}.childCount"] = {
                    "original": before_block.get("childCount"),
                    "output": after_block.get("childCount"),
                }
            before_types = before_block.get("childTypeCounts") or {}
            after_types = after_block.get("childTypeCounts") or {}
            for type_name, count in before_types.items():
                if int(after_types.get(type_name, 0)) < int(count):
                    mismatches[f"blocks.{name}.childTypeCounts.{type_name}"] = {
                        "original": count, "output": after_types.get(type_name, 0)
                    }
        if not ops and original_semantic_index["blocks"] != saved_semantic_index["blocks"]:
            mismatches["blocks.exact"] = {
                "original": original_semantic_index["blocks"],
                "output": saved_semantic_index["blocks"],
            }
        if not structural_ops["add_dimension"] and original_semantic_index["blocks"].keys() != saved_semantic_index["blocks"].keys():
            mismatches["blocks.names"] = {
                "original": sorted(original_semantic_index["blocks"]),
                "output": sorted(saved_semantic_index["blocks"]),
            }
        for name, before_layout in original_semantic_index["layouts"].items():
            after_layout = saved_semantic_index["layouts"].get(name)
            if after_layout is None:
                mismatches[f"layouts.{name}"] = {"original": before_layout, "output": after_layout}
                continue
            if name == "Model":
                output_layout_counts = semantic_type_counts({"modelspace": after_layout})
                expected_entity_count = sum(expected_semantic_counts.values())
                if output_layout_counts != expected_semantic_counts or int(after_layout.get("entityCount", 0)) != expected_entity_count:
                    mismatches[f"layouts.{name}"] = {
                        "original": before_layout,
                        "expected": {"entityCount": expected_entity_count, "typeCounts": expected_semantic_counts},
                        "output": after_layout,
                    }
            elif before_layout != after_layout:
                mismatches[f"layouts.{name}"] = {"original": before_layout, "output": after_layout}
        # add_dimension/update ops may create their named dimension style
        # (CBL_DIMSTYLE); any other style change is still a mismatch.
        expected_styles = dict(original_semantic_index["styles"] or {})
        dimension_styles = list(expected_styles.get("dimension") or [])
        known_dimension_styles = {str(name).casefold() for name in dimension_styles}
        for operation in ops or []:
            style_name = str((operation or {}).get("dimensionStyle") or "").strip() if isinstance(operation, dict) else ""
            if style_name and style_name.casefold() not in known_dimension_styles:
                dimension_styles.append(style_name)
                known_dimension_styles.add(style_name.casefold())
        saved_styles = dict(saved_semantic_index["styles"] or {})
        expected_styles["dimension"] = dimension_styles
        style_keys = set(expected_styles) | set(saved_styles)
        expected_style_sets = {key: sorted(expected_styles.get(key) or []) for key in style_keys}
        saved_style_sets = {key: sorted(saved_styles.get(key) or []) for key in style_keys}
        if expected_style_sets != saved_style_sets:
                mismatches["styles"] = {"original": original_semantic_index["styles"], "expected": expected_style_sets,
                                        "output": saved_semantic_index["styles"]}
        if not ops and original_semantic_index["inserts"] != saved_semantic_index["inserts"]:
            mismatches["inserts.exact"] = {
                "original": original_semantic_index["inserts"],
                "output": saved_semantic_index["inserts"],
            }
        if not ops and original_semantic_index["unsupported"] != saved_semantic_index["unsupported"]:
            mismatches["unsupported.exact"] = {
                "original": original_semantic_index["unsupported"],
                "output": saved_semantic_index["unsupported"],
            }
        for type_name, count in original_semantic_index["unsupported"].items():
            # A deleted SPLINE/ELLIPSE/LEADER... is gone on purpose.
            if int(saved_semantic_index["unsupported"].get(type_name, 0)) < int(count) - deleted_unsupported.get(str(type_name).upper(), 0) + copied_unsupported.get(str(type_name).upper(), 0):
                mismatches[f"unsupported.{type_name}"] = {"original": count, "output": saved_semantic_index["unsupported"].get(type_name, 0)}
        if int((saved_semantic_index["inserts"] or {}).get("unresolvedCount", 0)):
            mismatches["inserts.unresolvedCount"] = {"output": saved_semantic_index["inserts"].get("unresolvedCount")}
        return mismatches

    expected_counts_all = expected_semantic_counts
    semantic_structure_mismatches = semantic_mismatches()
    if semantic_structure_mismatches:
        raise _CBLFreeDwgSaveValidationError(
            "저장 검증 실패: DWG 구조 manifest가 변경되었습니다.",
            {
                "semantic_manifest_original": original_semantic,
                "semantic_manifest_expected": {
                    "modelspace": {"typeCounts": expected_semantic_counts},
                    "operation_deltas": semantic_deltas,
                },
                "semantic_manifest_output": saved_semantic,
                "semantic_mismatches": semantic_structure_mismatches,
                "operation_deltas": operation_deltas,
            },
        )
    # An empty model space is only acceptable when the ops removed every entity
    # and the saved file was read (it still has its tables); otherwise treat it
    # as a failed read, as before.
    expected_entity_total = sum(int(value) for value in expected.values() if value)
    saved_readable = bool((saved_json or {}).get("OBJECTS"))
    if not after and (expected_entity_total or not saved_readable):
        raise RuntimeError("저장 검증 실패: REGION 보존 수가 달라졌습니다.")
    if after.get("REGION", 0) != before.get("REGION", 0):
        raise RuntimeError("저장 검증 실패: REGION 보존 수가 달라졌습니다.")
    if after.get("MINSERT", 0) != before.get("MINSERT", 0):
        raise RuntimeError("저장 검증 실패: MINSERT 보존 수가 달라졌습니다.")
    # ACadSharp 3.6.51's DWG writer does not serialize the legacy SHAPE and
    # 3DSOLID payloads.  Do not silently accept loss of ordinary CAD
    # entities, dimensions, hatches, inserts, text, or regions; report these
    # two documented writer limitations separately instead of rejecting an
    # otherwise valid AC1018 file.
    writer_unsupported = {"SHAPE", "3DSOLID"}
    expected_without_writer_unsupported = {
        key: value for key, value in expected.items()
        if value and key not in writer_unsupported
    }
    actual_without_writer_unsupported = {
        key: value for key, value in after.items()
        if value and key not in writer_unsupported
    }
    if actual_without_writer_unsupported != expected_without_writer_unsupported:
        all_types = sorted(set(actual_without_writer_unsupported) | set(expected_without_writer_unsupported))
        mismatches = {
            key: {
                "original": int(before.get(key, 0)),
                "expected": int(expected_without_writer_unsupported.get(key, 0)),
                "output": int(actual_without_writer_unsupported.get(key, 0)),
                "delta": int(operation_deltas.get(key, 0)),
            }
            for key in all_types
            if int(actual_without_writer_unsupported.get(key, 0)) != int(expected_without_writer_unsupported.get(key, 0))
        }
        first = next(iter(mismatches.items()), (None, {}))
        entity, detail = first
        diagnostics = {
            "original_counts": {str(k): int(v) for k, v in before.items()},
            "expected_counts": {str(k): int(v) for k, v in expected_without_writer_unsupported.items()},
            "output_counts": {str(k): int(v) for k, v in after.items()},
            "operation_deltas": {str(k): int(v) for k, v in operation_deltas.items()},
            "mismatches": mismatches,
            "op_index": next((x["op_index"] for x in operation_sources if x["entity"] == entity), None),
            "entity": entity,
            "operation_type": next((x["operation_type"] for x in operation_sources if x["entity"] == entity), None),
            "mismatch_detail": detail,
            "operation_sources": operation_sources,
        }
        raise _CBLFreeDwgSaveValidationError(
            "저장 검증 실패: 엔티티 종류별 보존 수가 달라졌습니다.", diagnostics
        )
    def layer_records(document):
        return {
            canonical_ref(item.get("handle")): item
            for item in document.get("OBJECTS", [])
            if item.get("object") == "LAYER"
        }
    def layer_entity_refs(document):
        result = {}
        for item in entities(document):
            if item.get("entity") in {"SHAPE", "3DSOLID"}:
                continue
            ref = canonical_ref(item.get("layer"))
            result[ref] = result.get(ref, 0) + 1
        return result
    before_layers = layer_records(original_json)
    after_layers = layer_records(saved_json)
    # Every original layer keeps its handle; create_layer ops add exactly the
    # layers they name and nothing else.
    def layer_name_key(value):
        return str(value or "").strip().casefold()
    existing_layer_names = {layer_name_key(item.get("name")) for item in before_layers.values()}
    created_layer_names = {
        layer_name_key(operation.get("name"))
        for operation in ops or []
        if isinstance(operation, dict) and operation.get("type") == "create_layer" and layer_name_key(operation.get("name"))
    } - existing_layer_names
    added_layer_handles = set(after_layers) - set(before_layers)
    added_layer_names = {layer_name_key(after_layers[h].get("name")) for h in added_layer_handles}
    if (
        set(before_layers) - set(after_layers)
        or len(added_layer_names) != len(added_layer_handles)
        or added_layer_names != created_layer_names
    ):
        raise RuntimeError("저장 검증 실패: 레이어 handle이 달라졌습니다.")
    # Entity references can legitimately change for add/delete/update ops and
    # ACadSharp may normalize the owner payload while retaining the layer
    # table.  Critical layer names/handles above remain strict; reference
    # counts are reported, not used as a false failure gate.
    libre_layer_owner_differences = sum(
        1 for key in before_layers
        if before_layers[key].get("ownerhandle") != after_layers[key].get("ownerhandle")
    )
    original_text_counter = fingerprint_counter(text_fingerprints(original_json))
    saved_text_counter = fingerprint_counter(text_fingerprints(saved_json))
    libre_text_differences = {
        "missing": sorted([
            (_cbl_json.loads(key), count - saved_text_counter.get(key, 0))
            for key, count in original_text_counter.items()
            if count > saved_text_counter.get(key, 0)
        ], key=lambda item: fingerprint_key(item[0])),
        "added": sorted([
            (_cbl_json.loads(key), count - original_text_counter.get(key, 0))
            for key, count in saved_text_counter.items()
            if count > original_text_counter.get(key, 0)
        ], key=lambda item: fingerprint_key(item[0])),
    }
    libre_layer_name_differences = [
        {
            "handle": key,
            "before": before_layers[key].get("name"),
            "after": after_layers[key].get("name"),
        }
        for key in sorted(before_layers)
        if before_layers[key].get("name") != after_layers[key].get("name")
    ]
    if not isinstance(acad_report, dict) or not isinstance(acad_report.get("source"), dict) or not isinstance(acad_report.get("reread"), dict):
        raise RuntimeError("저장 검증 실패: ACadSharp 재판독 보고서가 없습니다.")
    acad_source = acad_report["source"]
    acad_reread = acad_report["reread"]
    # Every REGION keeps its ACIS payload, including one the writer took from
    # the AcDs section of an R2013+ drawing.
    reread_regions = {str(item.get("handle")): item for item in acad_reread.get("Regions") or []}
    for item in acad_source.get("Regions") or []:
        if item.get("dataSha256") and (reread_regions.get(str(item.get("handle"))) or {}).get("dataSha256") != item["dataSha256"]:
            raise RuntimeError("저장 검증 실패: REGION ACIS payload가 달라졌습니다.")
    # The writer snapshots "source" after ApplyOperations, so it already
    # contains the edits.  The TEXT expectations below start from the
    # pre-edit original read by the same ACadSharp reader instead; otherwise
    # every add_text/add_mtext is counted twice and rejected.
    original_entities = original_json.get("acadsharpEntities")
    if original_entities is None:
        original_entities = _cbl_free_dwg_acadsharp_metadata_v1(original).get("entities", [])
    acad_original = {"ModelSpaceEntities": [
        item for item in original_entities if str(item.get("space", "")).lower() == "modelspace"
    ]}
    # ACadSharp may expose the same source text as TextEntity on one read and
    # MText on another.  Compare one TEXT-family semantic multiset instead of
    # separate type/string lists, while retaining strict content, position,
    # rotation, and height checks.
    source_texts = acad_text_fingerprints(acad_original)
    reread_texts = acad_text_fingerprints(acad_reread)
    expected_text_counter = fingerprint_counter(source_texts)
    for op in ops or []:
        kind = str(op.get("type", "")).lower()
        if kind in ("add_text", "add_mtext"):
            value = {
                "family": "TEXT",
                "text": normalize_text_value(op.get("text", op.get("value", ""))),
                "point": point(op.get("insert")),
                "rotation": number(op.get("rotation"), 0.0),
                "height": number(op.get("height"), None),
            }
            key = fingerprint_key(value)
            expected_text_counter[key] = expected_text_counter.get(key, 0) + 1
        elif kind == "delete":
            target = canonical_ref(op.get("handle"))
            source = next((item for item in acad_original.get("ModelSpaceEntities", []) if canonical_ref(item.get("handle")) == target), None)
            value = text_family(source) if source else None
            if value:
                key = fingerprint_key(value)
                expected_text_counter[key] = expected_text_counter.get(key, 0) - 1
        elif kind == "update" and str(op.get("entity", "")).upper() in ("TEXT", "MTEXT"):
            target = canonical_ref(op.get("handle"))
            source = next((item for item in acad_original.get("ModelSpaceEntities", []) if canonical_ref(item.get("handle")) == target), None)
            value = text_family(source) if source else None
            if value:
                old_key = fingerprint_key(value)
                expected_text_counter[old_key] = expected_text_counter.get(old_key, 0) - 1
                # The updated value is what the writer applied in memory
                # (post-edit "source"); reread must then match it on disk.
                applied = next((item for item in acad_source.get("ModelSpaceEntities", []) if canonical_ref(item.get("handle")) == target), None)
                applied_value = text_family(applied) if applied else None
                if applied_value:
                    value = applied_value
                else:
                    value = dict(value)
                    if "text" in op or "value" in op:
                        value["text"] = normalize_text_value(op.get("text", op.get("value", value["text"])))
                    if "insert" in op:
                        value["point"] = point(op.get("insert"))
                    if "rotation" in op:
                        value["rotation"] = number(op.get("rotation"), 0.0)
                    if "height" in op:
                        value["height"] = number(op.get("height"), None)
                new_key = fingerprint_key(value)
                expected_text_counter[new_key] = expected_text_counter.get(new_key, 0) + 1
    expected_text_counter = {key: count for key, count in expected_text_counter.items() if count}
    actual_text_counter = fingerprint_counter(reread_texts)
    if actual_text_counter != expected_text_counter:
        missing = []
        extra = []
        for key, count in expected_text_counter.items():
            difference = count - actual_text_counter.get(key, 0)
            if difference > 0:
                missing.extend([_cbl_json.loads(key)] * difference)
        for key, count in actual_text_counter.items():
            difference = count - expected_text_counter.get(key, 0)
            if difference > 0:
                extra.extend([_cbl_json.loads(key)] * difference)
        raise _CBLFreeDwgSaveValidationError(
            "저장 검증 실패: ACadSharp reader 기준 TEXT/MTEXT 문자 계열이 달라졌습니다.",
            {
                "text_family": "TEXT+MTEXT",
                "original_count": len(source_texts),
                "expected_count": sum(expected_text_counter.values()),
                "output_count": len(reread_texts),
                "missing": missing,
                "extra": extra,
            },
        )
    expected_acad_layers = list(acad_source.get("Layers", []))
    for op in ops or []:
        if str(op.get("type", "")).lower() == "create_layer":
            name = str(op.get("name", ""))
            if name not in expected_acad_layers:
                expected_acad_layers.append(name)
    if sorted(acad_reread.get("Layers", [])) != sorted(expected_acad_layers):
        raise RuntimeError("저장 검증 실패: ACadSharp reader 기준 레이어 목록이 달라졌습니다.")
    if acad_reread.get("LayerRecords") != acad_source.get("LayerRecords"):
        raise RuntimeError("저장 검증 실패: ACadSharp reader 기준 레이어 handle/owner가 달라졌습니다.")
    if acad_reread.get("BlockDefinitions") != acad_source.get("BlockDefinitions"):
        raise RuntimeError("저장 검증 실패: ACadSharp reader 기준 블록 정의 수가 달라졌습니다.")
    if region_signatures(original_json) != region_signatures(saved_json):
        raise RuntimeError("저장 검증 실패: REGION ACIS/layer/owner payload가 달라졌습니다.")
    return {
        "libredwg": True,
        "before_entities": len(entities(original_json)),
        "after_entities": len(entities(saved_json)),
        "before_counts": before,
        "after_counts": after,
        "region_count": after.get("REGION", 0),
        "minsert_count": after.get("MINSERT", 0),
        "acadsharp_texts_validated": True,
        "acadsharp_layers_validated": True,
        "libredwg_layer_owner_differences": libre_layer_owner_differences,
        "libredwg_text_differences": libre_text_differences,
        "libredwg_layer_name_differences": libre_layer_name_differences,
        "semantic_manifest_validated": True,
        "semantic_modelspace_counts": semantic_index(saved_semantic)["modelspace"],
        "semantic_block_count": len(saved_semantic_index["blocks"]),
        "semantic_layout_count": len(saved_semantic_index["layouts"]),
        "semantic_insert_count": int((saved_semantic_index["inserts"] or {}).get("count", 0)),
        "semantic_unresolved_insert_count": int((saved_semantic_index["inserts"] or {}).get("unresolvedCount", 0)),
        "semantic_style_counts": {key: len(value) for key, value in (saved_semantic_index["styles"] or {}).items()},
        "writer_unsupported_entity_counts": {
            key: before.get(key, 0) - after.get(key, 0)
            for key in sorted(writer_unsupported)
            if before.get(key, 0) != after.get(key, 0)
        },
    }


def _cbl_free_dwg_output_handles_v1(acad_report, ops):
    """Expose ACadSharp's actual handles for newly-created operations."""
    report = acad_report if isinstance(acad_report, dict) else {}
    edit_report = report.get("editReport")
    applied = edit_report.get("applied") if isinstance(edit_report, dict) else None
    if not isinstance(applied, list) or not isinstance(ops, list):
        return {}
    output_handles = {}
    seen_handles = set()
    for op_index, operation in enumerate(ops):
        if not isinstance(operation, dict):
            continue
        operation_type = str(operation.get("type") or "").strip().lower()
        if not operation_type.startswith("add_"):
            continue
        if op_index >= len(applied) or not isinstance(applied[op_index], dict):
            raise _CBLFreeDwgSaveValidationError(
                "저장 검증 실패: 신규 operation의 출력 handle을 확인할 수 없습니다.",
                {"op_index": op_index, "operation_type": operation_type},
            )
        handle = _cbl_normalize_dwg_handle_v1(applied[op_index].get("handle"))
        if not handle:
            raise _CBLFreeDwgSaveValidationError(
                "저장 검증 실패: 신규 operation의 출력 handle이 비어 있습니다.",
                {"op_index": op_index, "operation_type": operation_type},
            )
        if handle in seen_handles:
            raise _CBLFreeDwgSaveValidationError(
                "저장 검증 실패: 신규 operation의 출력 handle이 중복됩니다.",
                {"op_index": op_index, "operation_type": operation_type, "handle": handle},
            )
        seen_handles.add(handle)
        output_handles[str(op_index)] = handle
    return output_handles


def _cbl_free_dwg_writer_error_message_v1(detail):
    """Explain writer failures the user can fix; keep the raw detail otherwise."""
    missing_linetype = _cbl_re.search(r"Linetype not found: ([^\\\"\r\n]+)", detail or "")
    if missing_linetype:
        return (f"도면에 없는 선종류({missing_linetype.group(1).strip()})는 저장할 수 없습니다. "
                "도면에 있는 선종류로 바꾸거나 변경을 되돌려 주세요.")
    if "REGION has no ACIS payload" in (detail or ""):
        # AutoCAD 2013+ keeps REGION geometry where ACadSharp cannot read it;
        # the writer refuses instead of dropping the REGION.
        return ("이 도면의 면 영역(REGION) 객체는 무료 DWG 저장에서 보존할 수 없어 저장을 중단했습니다. "
                "원본 파일은 바뀌지 않았습니다. (AutoCAD 2013 이후 형식의 REGION은 아직 지원하지 않습니다.)")
    refused = _cbl_re.search(r"NotSupportedException: (Move|Update|Transform) is not supported for ([A-Za-z0-9]+)(?=[\\\" (\r\n]|$)(?: \(([^)]*)\))?", detail or "")
    if refused:
        action = {"Move": "이동", "Update": "수정", "Transform": "회전·크기 변경·대칭"}[refused.group(1)]
        if refused.group(1) == "Transform":
            # The writer names which part of the transform it refused.
            action = {"mirrored pattern": "대칭(무늬 해치)", "scaled or mirrored": "크기 변경·대칭",
                      "mirrored or with block content": "대칭(또는 블록 내용)", "mirrored": "대칭",
                      "mirrored or with attributes": "대칭(또는 속성)", "shared block": "회전·크기 변경(공유 블록)",
                      "attributes not placed": "회전·크기 변경·대칭(블록 속성 위치 없음)"}.get(refused.group(3) or "", action)
        kind = _CBL_FREE_DWG_KIND_NAMES_V1.get(refused.group(2), refused.group(2))
        return (f"{kind} {action}은 아직 DWG로 저장할 수 없어 저장을 멈췄습니다. 원본 파일은 바뀌지 않았습니다. "
                "그 편집을 되돌린 뒤 다시 저장해 주세요.")
    if "NotSupportedException" in (detail or ""):
        return "이 편집은 아직 DWG로 저장할 수 없어 저장을 멈췄습니다. 원본 파일은 바뀌지 않았습니다. 그 편집을 되돌린 뒤 다시 저장해 주세요."
    return "ACadSharp Save As 실패: " + (detail or "")


_CBL_FREE_DWG_KIND_NAMES_V1 = {
    "Spline": "스플라인", "Ellipse": "타원", "Hatch": "해치", "Solid": "솔리드(SOLID)", "Face3D": "3D 면",
    "Point": "점", "Leader": "지시선", "MultiLeader": "다중 지시선", "Wipeout": "가림막(WIPEOUT)",
    "MLine": "다중선", "Ray": "반무한선", "XLine": "무한선", "Region": "영역(REGION)", "Solid3D": "3D 솔리드",
    "Dimension": "치수", "DimensionLinear": "치수", "DimensionAligned": "치수", "DimensionRadius": "반지름 치수",
    "DimensionDiameter": "지름 치수", "DimensionAngular2Line": "각도 치수", "DimensionAngular3Pt": "각도 치수",
    "DimensionOrdinate": "좌표 치수", "Arc": "호", "Circle": "원", "Line": "선", "LwPolyline": "폴리선",
    "Polyline2D": "폴리선", "TextEntity": "문자", "MText": "문자", "Insert": "블록",
}


@_cbl_csrf_exempt
def cblcad_free_dwg_save_local_api(request):
    if request.method == "GET":
        if not _cbl_is_free_dwg_request(request) or not (_cbl_free_dwg_save_local_enabled_v1() or request.GET.get("mode") == "free-dwg"):
            return _cbl_JsonResponse({"ok": False, "error": "저장 경로를 찾을 수 없습니다."}, status=404)
        return _cbl_JsonResponse({
            "ok": True,
            "enabled": _cbl_is_free_dwg_request(request) and (_cbl_free_dwg_save_local_enabled_v1() or request.GET.get("mode") == "free-dwg"),
            "mode": _CBL_FREE_DWG_SAVE_LOCAL_SCHEMA_V1,
            "converter": "free-acadsharp-dwg-writer",
            "target_version": "AC1018",
            "oda_used": False,
            "v29_used": False,
            "oda_executed": False,
        })
    if request.method != "POST":
        return _cbl_JsonResponse({"ok": False, "error": "POST 요청만 지원합니다."}, status=405)
    if not _cbl_is_free_dwg_request(request) or not (_cbl_free_dwg_save_local_enabled_v1() or request.GET.get("mode") == "free-dwg"):
        return _cbl_JsonResponse({"ok": False, "enabled": False, "error": "DWG Save As 경로가 비활성화되어 있습니다."}, status=404)

    # Touch FILES before POST parsing so an oversized original is rejected
    # before any save operation or converter subprocess can be reached.
    upload = request.FILES.get("original_dwg")
    source_token = str(request.POST.get("file_token") or request.POST.get("source_file_token") or "")
    target_token = str(request.POST.get("target_file_token") or source_token)
    local_source_path = None
    local_target_path = None
    local_target_record = None
    try:
        if source_token:
            local_source_path, _source_record = _cbl_resolve_local_file_record_v1(request, source_token, True)
        if target_token:
            local_target_path, local_target_record = _cbl_resolve_local_file_record_v1(request, target_token, False)
    except _CBLLocalFileConflict as exc:
        _cbl_dwg_dxf_emit_log_v1(
            "CBLCAD_FREE_DWG_SAVE_LOCAL endpoint=free-dwg-save event=local_conflict error=%s",
            str(exc)[:500],
        )
        return _cbl_JsonResponse({"ok": False, "error": str(exc)}, status=409)
    except _CBLFreeDwgSaveValidationError as exc:
        return _cbl_JsonResponse({"ok": False, "error": str(exc)}, status=409)
    max_upload = _cbl_free_dwg_upload_limit_v1()
    if upload is not None and int(getattr(upload, "size", 0) or 0) > max_upload:
        _cbl_dwg_dxf_emit_log_v1(
            "CBLCAD_FREE_DWG_SAVE_LOCAL endpoint=free-dwg-save event=upload_too_large bytes=%s limit=%s",
            getattr(upload, "size", 0), max_upload,
        )
        return _cbl_JsonResponse({"ok": False, "error": "DWG 업로드 제한을 초과했습니다.", "max_bytes": max_upload, "oda_executed": False}, status=413)

    target_version = str(request.POST.get("target_version", "AC1018")).strip().upper()
    if target_version not in {"AC1018", "AC2004"}:
        return _cbl_JsonResponse({"ok": False, "error": "DWG 저장은 AutoCAD 2004(AC1018)만 지원합니다."}, status=400)
    requested_name = _cbl_os.path.basename(str(request.POST.get("download_name") or request.POST.get("filename", "drawing.dwg")))
    if not requested_name or requested_name in {".", ".."}:
        requested_name = "drawing.dwg"
    if not requested_name.lower().endswith(".dwg"):
        requested_name += ".dwg"
    requested_name = _cbl_re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", requested_name).strip()[:180]
    if not requested_name:
        requested_name = "drawing.dwg"
    requested_name = _cbl_re.sub(r'(\.dwg)+$', ".dwg", requested_name, flags=_cbl_re.IGNORECASE)

    executable = _cbl_free_dwg_save_local_executable_v1()
    dwgread = _cbl_free_dwg_local_find_dwgread_v1()
    if executable is None:
        return _cbl_JsonResponse({"ok": False, "oda_executed": False, "error": "로컬 ACadSharp 실행 파일이 설치되지 않았습니다."}, status=503)
    # LibreDWG is optional on Linux: when dwgread is unavailable, the
    # self-contained ACadSharp runtime supplies strict metadata/handle
    # validation below.  Do not skip validation or invoke ODA as a fallback.

    try:
        operations_payload = _cbl_json.loads(request.POST.get("ops", "[]"))
        ops = operations_payload.get("ops", []) if isinstance(operations_payload, dict) else operations_payload
        if not isinstance(ops, list):
            raise ValueError("ops는 배열이어야 합니다.")
    except Exception as exc:
        return _cbl_JsonResponse({"ok": False, "error": f"편집 명령이 올바르지 않습니다: {exc}"}, status=400)

    started = _cbl_time.perf_counter()
    try:
        with _cbl_tempfile.TemporaryDirectory(prefix="cbl-free-dwg-save-") as temp:
            temp_root = _cbl_Path(temp)
            original = temp_root / "original.dwg"
            output = temp_root / "saved_AC1018.dwg"
            ops_path = temp_root / "ops.json"
            if upload is not None:
                original.write_bytes(b"".join(upload.chunks()))
                if original.stat().st_size < 1024:
                    raise ValueError("원본 DWG가 비어 있거나 비정상적으로 작습니다.")
                # Resolve every existing-entity target against the exact
                # uploaded source before starting the writer.  This prevents
                # a renderer/display id from becoming a DWG delete target.
                original_for_ops = _cbl_free_dwg_save_local_json_v1(original, dwgread)
                ops = _cbl_normalize_free_dwg_ops_v1(original_for_ops, ops)
                if isinstance(operations_payload, dict):
                    operations_payload = dict(operations_payload)
                    operations_payload["ops"] = ops
                else:
                    operations_payload = ops
            elif local_source_path is not None:
                _cbl_shutil.copyfile(local_source_path, original)
                if original.stat().st_size < 1024:
                    raise ValueError("원본 DWG가 비어 있거나 비정상적으로 작습니다.")
                original_for_ops = _cbl_free_dwg_save_local_json_v1(original, dwgread)
                ops = _cbl_normalize_free_dwg_ops_v1(original_for_ops, ops)
                if isinstance(operations_payload, dict):
                    operations_payload = dict(operations_payload)
                    operations_payload["ops"] = ops
                else:
                    operations_payload = ops
            ops_path.write_text(_cbl_json.dumps(operations_payload, ensure_ascii=False), encoding="utf-8")
            reread_metadata = temp_root / "saved_metadata.json"
            if upload is not None or local_source_path is not None:
                command = [str(executable), str(original), str(output), "AC1018", str(ops_path),
                           "--reread-metadata", str(reread_metadata)]
            else:
                # A new free document has no source DWG.  ACadSharp creates a
                # real AC1018 document from the editor operations; no DXF
                # extension trick or paid/ODA conversion is involved.
                command = [str(executable), "--create", str(output), "AC1018", str(ops_path)]
            run = _cbl_acadsharp_run_v1(command, timeout=900)
            if run.returncode != 0 or not output.is_file() or output.stat().st_size < 1024:
                detail = (run.stderr or run.stdout).decode("utf-8", errors="replace")[-1800:]
                raise RuntimeError(_cbl_free_dwg_writer_error_message_v1(detail))

            try:
                acad_report = _cbl_json.loads(run.stdout.decode("utf-8", errors="replace"), strict=False)
            except Exception as exc:
                raise RuntimeError("ACadSharp 재판독 보고서 파싱 실패: " + str(exc)) from exc
            # Without LibreDWG both sides are ACadSharp reads: the original's
            # was made for the edit targets, the saved file's by the writer.
            validation = (_cbl_free_dwg_save_local_validate_v1(
                              original, output, dwgread, ops, acad_report, original_json=original_for_ops,
                              saved_json=None if dwgread else _cbl_free_dwg_reread_metadata_json_v1(reread_metadata))
                          if upload is not None or local_source_path is not None else {
                              "before_entities": 0,
                              "after_entities": int((acad_report.get("reread") or {}).get("EntityTotal", 0)),
                              "before_counts": {},
                              "after_counts": (acad_report.get("reread") or {}).get("Counts", {}),
                              "region_count": 0, "minsert_count": 0,
                              "acadsharp_texts_validated": True,
                              "acadsharp_layers_validated": True,
                              "libredwg_layer_owner_differences": 0,
                              "libredwg_layer_name_differences": [],
                              "libredwg_text_differences": {"missing": [], "added": []},
                          })
            payload = output.read_bytes()
            output_handles = _cbl_free_dwg_output_handles_v1(acad_report, ops)
            output_handles_value, output_handles_size, output_handles_is_token = _cbl_free_dwg_handle_map_delivery_v1(output_handles)
            is_explicit_download_name = bool(request.POST.get("download_name"))
            base_name = _cbl_os.path.splitext(requested_name)[0] or "drawing"
            name = requested_name if is_explicit_download_name else base_name + "_ACADSHARP_AC1018.dwg"
            if str(request.POST.get("delivery", "")).strip().lower() == "dxf":
                # "DXF 저장": the validated DWG as DXF.  Nothing is written to a
                # local file and no download token is made; the editor keeps
                # its save target and baseline.
                dxf_payload, dxf_skipped = _cbl_free_dwg_dxf_export_v1(output, temp_root)
                dxf_name = base_name + ".dxf"
                from urllib.parse import quote
                response = _cbl_HttpResponse(dxf_payload, content_type="application/dxf")
                ascii_name = dxf_name.encode("ascii", "ignore").decode("ascii") or "drawing.dxf"
                response["Content-Disposition"] = (
                    f'attachment; filename="{ascii_name.replace(chr(34), "")}"; '
                    f"filename*=UTF-8''{quote(dxf_name, safe='')}"
                )
                response["Cache-Control"] = "no-store"
                response["Content-Length"] = str(len(dxf_payload))
                response["X-CBL-FREE-DWG-SAVE-VALIDATED"] = "1"
                response["X-CBL-ODA-USED"] = "0"
                response["X-CBL-DXF-SKIPPED"] = _cbl_json.dumps(dxf_skipped, separators=(",", ":"))
                _cbl_dwg_dxf_emit_log_v1(
                    "CBLCAD_FREE_DWG_SAVE_LOCAL endpoint=free-dwg-save event=dxf_export oda_executed=0 ops=%s bytes=%s endpoint_ms=%.2f",
                    len(ops), len(dxf_payload), (_cbl_time.perf_counter() - started) * 1000,
                )
                return response
            if local_target_path is not None:
                _cbl_atomic_replace_local_file_v1(local_target_path, payload)
                updated = _cbl_local_file_fingerprint_v1(local_target_path)
                records = _cbl_local_file_tokens_v1(request)
                if target_token in records:
                    records[target_token].update(updated)
                    records[target_token]["name"] = local_target_path.name
                    records[target_token]["extension"] = local_target_path.suffix.lower()
                    request.session["cbl_free_dwg_local_files_v1"] = records
                    request.session.modified = True
                return _cbl_JsonResponse({
                    "ok": True, "saved": True, "filename": local_target_path.name,
                    "size": len(payload), "version": "AC1018", "file_token": target_token,
                    "data": _cbl_base64.b64encode(payload).decode("ascii"),
                    "output_handles": output_handles_value,
                    "output_handles_size": output_handles_size,
                    "output_handles_is_token": output_handles_is_token,
                    "backup": str(local_target_path.name + ".cblcad.bak"),
                    "converter": "free-acadsharp-dwg-writer", "oda_used": False, "v29_used": False,
                }, json_dumps_params={"ensure_ascii": False})
            if str(request.POST.get("delivery", "")).strip().lower() == "token":
                token = _cbl_free_dwg_download_store_v1(payload, name)
                from urllib.parse import quote
                return _cbl_JsonResponse({
                    "ok": True,
                    "filename": name,
                    "size": len(payload),
                    "version": "AC1018",
                    "download_url": "/api/cblcad/free-dwg-download/" + quote(token, safe="") + "/?mode=free-dwg",
                    "output_handles": output_handles_value,
                    "output_handles_size": output_handles_size,
                    "output_handles_is_token": output_handles_is_token,
                    "converter": "free-acadsharp-dwg-writer",
                    "oda_used": False,
                    "v29_used": False,
            }, json_dumps_params={"ensure_ascii": False})
            response = _cbl_HttpResponse(payload, content_type="application/acad")
            from urllib.parse import quote
            ascii_name = name.encode("ascii", "ignore").decode("ascii") or "drawing.dwg"
            response["Content-Disposition"] = (
                f'attachment; filename="{ascii_name.replace(chr(34), "")}"; '
                f"filename*=UTF-8''{quote(name, safe='') }"
            )
            response["Cache-Control"] = "no-store"
            response["Content-Length"] = str(len(payload))
            response["X-CBL-FREE-DWG-SAVE"] = "ACADSHARP_AC1018"
            response["X-CBL-FREE-DWG-SAVE-VALIDATED"] = "1"
            response["X-CBL-FREE-DWG-CONVERTER"] = "free-acadsharp-dwg-writer"
            response["X-CBL-ODA-USED"] = "0"
            response["X-CBL-V29-USED"] = "0"
            if output_handles_value:
                response["X-CBL-FREE-DWG-OUTPUT-HANDLES"] = output_handles_value
                response["X-CBL-FREE-DWG-OUTPUT-HANDLES-SIZE"] = str(output_handles_size)
                response["X-CBL-FREE-DWG-OUTPUT-HANDLES-TOKEN"] = "1" if output_handles_is_token else "0"
            response["X-CBL-FREE-DWG-SAVE-MS"] = f"{(_cbl_time.perf_counter() - started) * 1000:.2f}"
            _cbl_dwg_dxf_emit_log_v1(
                "CBLCAD_FREE_DWG_SAVE_LOCAL endpoint=free-dwg-save event=success oda_executed=0 ops=%s region=%s minsert=%s acadsharp_texts_validated=%s acadsharp_layers_validated=%s libredwg_layer_owner_differences=%s libredwg_layer_name_differences=%s libredwg_text_differences=%s endpoint_ms=%.2f",
                len(ops), validation["region_count"], validation["minsert_count"],
                validation["acadsharp_texts_validated"], validation["acadsharp_layers_validated"],
                validation["libredwg_layer_owner_differences"],
                len(validation["libredwg_layer_name_differences"]),
                len(validation["libredwg_text_differences"]["missing"]) + len(validation["libredwg_text_differences"]["added"]),
                (_cbl_time.perf_counter() - started) * 1000,
            )
            return response
    except _CBLFreeDwgSaveValidationError as exc:
        _cbl_dwg_dxf_emit_log_v1(
            "CBLCAD_FREE_DWG_SAVE_LOCAL endpoint=free-dwg-save event=validation_error oda_executed=0 error=%s",
            str(exc)[:500],
        )
        payload = {"ok": False, "oda_executed": False, "error": str(exc)}
        if isinstance(getattr(exc, "diagnostics", None), dict):
            payload["validation"] = exc.diagnostics
        return _cbl_JsonResponse(payload, status=400, json_dumps_params={"ensure_ascii": False})
    except _CBLAcadSharpBusy as exc:
        return _cbl_JsonResponse({"ok": False, "busy": True, "oda_executed": False, "error": str(exc)},
                                 status=503, json_dumps_params={"ensure_ascii": False})
    except Exception as exc:
        _cbl_dwg_dxf_emit_log_v1(
            "CBLCAD_FREE_DWG_SAVE_LOCAL endpoint=free-dwg-save event=error oda_executed=0 error=%s",
            str(exc)[:500],
        )
        detail = str(exc)
        if "Linetype not found:" in detail:
            raw_name = detail.split("Linetype not found:", 1)[1].strip().split("\\n", 1)[0].splitlines()[0][:120]
            return _cbl_JsonResponse({
                "ok": False,
                "oda_executed": False,
                "error": "저장 검증 실패: 알 수 없는 선종류입니다.",
                "validation": {"code": "unknown_linetype", "linetype": raw_name},
            }, status=400, json_dumps_params={"ensure_ascii": False})
        return _cbl_JsonResponse({"ok": False, "oda_executed": False, "error": str(exc)}, status=500)
# CBL_FREE_DWG_LOCAL_SAVE_V1_END
