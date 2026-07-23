import glob
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
from django.utils import timezone
from django.utils.html import strip_tags
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
    """기존 글을 현재 운영 중인 8개 카테고리 중 가장 가까운 분류로 표시합니다."""
    current_categories = {
        "construction_work",
        "construction_tech",
        "construction_real",
        "bim",
        "dynamo_automation",
        "four_d_five_d",
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

    if original_category in {"tech", "life"} or any(word in text for word in [
        "앱", "툴", "도구", "추천", "리뷰", "비교", "노트북", "스마트폰", "태블릿",
        "인터넷", "ipv4", "ipv6", "클라우드", "보안", "ai", "인공지능",
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
        regular_published, ["tool_recommend"], 4
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
                "recent": ["tool_recommend"],
                "main": ["tool_recommend"],
                "sub": ["tool_recommend"],
                "third": ["tool_recommend"],
                "keyword_sections": ["main", "sub", "third"],
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
    if request.method == "POST":
        form = UserCreationForm(request.POST)

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
        form = UserCreationForm()

    return render(request, "core/signup.html", {
        "form": form,
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
        .select_related("profile")
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
from .cbl_category_policy import CBL_PUBLIC_CATEGORY_CHOICES, CBL_AI_CATEGORY_GUIDE, CBL_CATEGORY_LABELS


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

    def _cbl_row_normalize_category(value):
        value = str(value or "").strip()

        alias = {
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

        value = alias.get(value, value)

        if value not in {"architecture", "realestate", "finance", "tech", "life"}:
            value = "tech"

        return value

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
                        "category": _cbl_row_normalize_category(item.get("category")),
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
                    "category": _cbl_row_normalize_category(categories[idx] if idx < len(categories) else request.POST.get("category")),
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

    def _cbl_construction_norm_category(value):
        value = str(value or "").strip()
        alias = {
            "건설": "construction_work",
            "건축": "construction_work",
            "건설실무": "construction_work",
            "시공": "construction_work",
            "architecture": "construction_work",
            "construction_work": "construction_work",

            "건설기술": "construction_tech",
            "BIM": "construction_tech",
            "bim": "construction_tech",
            "construction_tech": "construction_tech",

            "부동산": "construction_real",
            "건설부동산": "construction_real",
            "건설 부동산": "construction_real",
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
            "라이프": "life",
            "생활": "life",
            "life": "life",
        }
        slug = alias.get(value, value)
        valid = {
            "construction_work",
            "construction_tech",
            "construction_real",
            "finance",
            "tech",
            "life",
        }
        return slug if slug in valid else "tech"

    def _cbl_construction_generation_category(value):
        target = _cbl_construction_norm_category(value)
        if target in ("construction_work", "construction_tech"):
            return "architecture"
        if target == "construction_real":
            return "realestate"
        return target

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
                    targets.append(_cbl_construction_norm_category(item.get("category")))
        if targets:
            return targets

        keywords = request.POST.getlist("cbl_row_keywords[]")
        categories = request.POST.getlist("cbl_row_categories[]")
        for idx, kw in enumerate(keywords):
            if not str(kw or "").strip():
                continue
            raw_cat = categories[idx] if idx < len(categories) else request.POST.get("category")
            targets.append(_cbl_construction_norm_category(raw_cat))
        if targets:
            return targets

        return [_cbl_construction_norm_category(
            request.POST.get("cbl_force_category")
            or request.POST.get("auto_category")
            or request.POST.get("selected_category")
            or request.POST.get("post_category")
            or request.POST.get("category")
            or "tech"
        )]

    def _cbl_construction_rewrite_post_for_generation(request):
        import json
        qd = request.POST.copy()

        for name in (
            "category", "post_category", "post_category_slug", "selected_category",
            "ai_category", "auto_category", "cbl_locked_category", "cbl_force_category",
        ):
            if qd.get(name):
                qd[name] = _cbl_construction_generation_category(qd.get(name))

        raw = str(qd.get("cbl_keyword_rows", "") or "").strip()
        if raw:
            try:
                rows = json.loads(raw)
            except Exception:
                rows = []
            if isinstance(rows, list):
                for item in rows:
                    if isinstance(item, dict):
                        item["category"] = _cbl_construction_generation_category(item.get("category"))
                qd["cbl_keyword_rows"] = json.dumps(rows, ensure_ascii=False)

        for list_name in (
            "cbl_row_categories[]", "auto_categories[]", "auto_categories",
            "categories[]", "categories",
        ):
            values = qd.getlist(list_name)
            if values:
                qd.setlist(list_name, [_cbl_construction_generation_category(v) for v in values])

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
                        if target in {"construction_work", "construction_tech", "construction_real", "finance", "tech", "life"}:
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
def webcad_tool(request):
    return render(request, "core/tools/cblcad_ver1.html")
# CBL_WEBCAD_TOOL_END


# CBL_CAD_DIRECT_VIEW_START
def cblcad_direct_view(request):
    from pathlib import Path
    from django.conf import settings
    from django.http import HttpResponse, Http404

    base = Path(settings.BASE_DIR)

    candidates = [
        base / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html",
        base / "static" / "core" / "tools" / "CBLCAD_VER2.html",
        base / "CBLCAD_VER2.html",
    ]

    for path in candidates:
        if path.exists():
            html = path.read_text(encoding="utf-8", errors="ignore")
            return HttpResponse(html, content_type="text/html; charset=utf-8")

    raise Http404("CBLCAD_VER2.html file not found")
# CBL_CAD_DIRECT_VIEW_END


# ============================================================
# ChickenBananaCAD DWG 업로드 API 준비 V1
# ============================================================

# ============================================================
# CBL_DWG_KOREAN_TEXT_REAL_FIX_V21_START
# DWG -> DXF 한글 TEXT 보존용 decode/json response helper
# ============================================================
def _cbl_decode_dxf_text_korean_v21(path_or_bytes):
    import re as _re
    from pathlib import Path as _Path

    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = bytes(path_or_bytes)
    else:
        raw = _Path(path_or_bytes).read_bytes()

    head = raw[:500000].decode('latin1', errors='ignore')
    codepage = ''
    try:
        m = _re.search(r'\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n([^\r\n]+)', head, _re.I)
        if m:
            codepage = (m.group(1) or '').strip()
    except Exception:
        codepage = ''

    # ODA DXF는 HEADER가 ANSI_949여도 실제 본문이 UTF-8인 경우가 많다.
    candidates = ['utf-8-sig', 'utf-8']
    cp = (codepage or '').upper()
    if any(x in cp for x in ['949', 'KOREA', 'KSC', 'HANGEUL', 'HANGUL']):
        candidates += ['cp949', 'euc-kr']
    candidates += ['cp949', 'euc-kr', 'latin1']

    unique = []
    for enc in candidates:
        if enc not in unique:
            unique.append(enc)

    def _score_text(text):
        hangul = sum(1 for ch in text if '가' <= ch <= '힣')
        hits = {
            '제1부패조': text.find('제1부패조'),
            '제2부패조': text.find('제2부패조'),
            '여과조': text.find('여과조'),
            '방류조': text.find('방류조'),
            '부패조': text.find('부패조'),
        }
        has_target = any(v >= 0 for v in hits.values())
        return has_target, hangul, hits

    results = []
    for enc in unique:
        try:
            text = raw.decode(enc)
        except Exception:
            continue
        has_target, hangul, hits = _score_text(text)
        results.append((has_target, hangul, enc.startswith('utf-8'), enc, text, hits, 'strict', 0))

    if not results:
        for enc in unique:
            try:
                text = raw.decode(enc, errors='replace')
            except Exception:
                continue
            has_target, hangul, hits = _score_text(text)
            results.append((has_target, hangul, enc.startswith('utf-8'), enc, text, hits, 'replace', text.count('\ufffd')))

    if results:
        results.sort(key=lambda x: (x[0], x[1], x[2], -x[7]), reverse=True)
        has_target, hangul, is_utf8, enc, text, hits, mode, repl = results[0]
        return text, {
            'version': 'CBL_DWG_KOREAN_TEXT_REAL_FIX_V21',
            'encoding': enc,
            'codepage': codepage,
            'decode_mode': mode,
            'replacement_count': repl,
            'hangul_count': hangul,
            'target_hits': hits,
            'raw_size': len(raw),
            'text_length': len(text),
        }

    text = raw.decode('latin1', errors='replace')
    return text, {
        'version': 'CBL_DWG_KOREAN_TEXT_REAL_FIX_V21',
        'encoding': 'latin1-final',
        'codepage': codepage,
        'decode_mode': 'final',
        'hangul_count': 0,
        'target_hits': {},
        'raw_size': len(raw),
        'text_length': len(text),
    }


def _cbl_json_response_korean_v21(payload, status=200):
    import json as _json
    from django.http import HttpResponse as _HttpResponse
    return _HttpResponse(
        _json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        status=status,
        content_type='application/json; charset=utf-8',
    )
# CBL_DWG_KOREAN_TEXT_REAL_FIX_V21_END
# ============================================================



# ============================================================
# CBL_DWG_KOREAN_TEXT_DIRECT_OPEN_V3_START
# ODA DXF 한글 보존: HEADER가 ANSI_949여도 실제 본문 UTF-8인 경우 우선 처리
# ============================================================
def _cbl_decode_dxf_text_korean_direct_v3(path_or_bytes):
    import re as _re
    from pathlib import Path as _Path

    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = bytes(path_or_bytes)
    else:
        raw = _Path(path_or_bytes).read_bytes()

    head = raw[:500000].decode('latin1', errors='ignore')
    codepage = ''
    try:
        m = _re.search(r'\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n([^\r\n]+)', head, _re.I)
        if m:
            codepage = (m.group(1) or '').strip()
    except Exception:
        codepage = ''

    # 실제 ODA 출력은 $DWGCODEPAGE=ANSI_949여도 UTF-8 본문인 경우가 있음.
    candidates = ['utf-8-sig', 'utf-8', 'cp949', 'euc-kr', 'latin1']
    targets = ['제1부패조', '제2부패조', '여과조', '방류조', '부패조', '여과', '방류']

    def score(text):
        hangul = sum(1 for ch in text if '가' <= ch <= '힣')
        replacement = text.count('\ufffd')
        hits = {k: text.find(k) for k in targets}
        target_count = sum(1 for v in hits.values() if v >= 0)
        # 한글 수, 타겟 적중, UTF-8 여부, 치환문자 적음을 기준으로 선택
        return (target_count, hangul, -replacement), hits

    best = None
    # strict 먼저. UTF-8 strict가 성공하면 보통 이게 정답이다.
    for enc in candidates:
        try:
            text = raw.decode(enc)
        except Exception:
            continue
        sc, hits = score(text)
        item = (sc, enc, 'strict', text, hits, text.count('\ufffd'))
        if best is None or item[0] > best[0]:
            best = item

    # strict가 모두 실패하면 replace로 보되, 점수로 결정
    if best is None:
        for enc in candidates:
            try:
                text = raw.decode(enc, errors='replace')
            except Exception:
                continue
            sc, hits = score(text)
            item = (sc, enc, 'replace', text, hits, text.count('\ufffd'))
            if best is None or item[0] > best[0]:
                best = item

    if best is None:
        text = raw.decode('latin1', errors='replace')
        hits = {k: text.find(k) for k in targets}
        return text, {
            'version': 'CBL_DWG_KOREAN_TEXT_DIRECT_OPEN_V3',
            'encoding': 'latin1-final',
            'decode_mode': 'final',
            'codepage': codepage,
            'hangul_count': 0,
            'target_hits': hits,
            'raw_size': len(raw),
            'text_length': len(text),
        }

    sc, enc, mode, text, hits, repl = best
    return text, {
        'version': 'CBL_DWG_KOREAN_TEXT_DIRECT_OPEN_V3',
        'encoding': enc,
        'decode_mode': mode,
        'codepage': codepage,
        'hangul_count': sum(1 for ch in text if '가' <= ch <= '힣'),
        'target_hits': hits,
        'replacement_count': repl,
        'raw_size': len(raw),
        'text_length': len(text),
    }


def _cbl_json_response_korean_direct_v3(payload, status=200):
    import json as _json
    from django.http import HttpResponse as _HttpResponse
    return _HttpResponse(
        _json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        status=status,
        content_type='application/json; charset=utf-8',
    )
# CBL_DWG_KOREAN_TEXT_DIRECT_OPEN_V3_END
# ============================================================


def cblcad_dwg_to_dxf_api(request):
    """
    ChickenBananaCAD DWG 업로드 API V1.

    현재 단계:
    - DWG 파일 업로드 수신
    - 서버 임시 위치 저장
    - 변환엔진 연결 전 준비 응답 반환

    다음 단계:
    - ODA File Converter 또는 다른 DWG 변환엔진으로 DWG -> DXF 변환
    - 변환된 DXF 텍스트를 JSON 응답으로 반환
    """
    import os
    import tempfile
    from pathlib import Path
    from django.http import JsonResponse
    from django.views.decorators.csrf import csrf_exempt

    if request.method != "POST":
        return JsonResponse({
            "ok": False,
            "message": "POST 요청만 지원합니다."
        }, status=405)

    upload = request.FILES.get("file") or request.FILES.get("dwg")

    if not upload:
        return JsonResponse({
            "ok": False,
            "message": "DWG 파일이 없습니다."
        }, status=400)

    name = upload.name or "uploaded.dwg"
    ext = Path(name).suffix.lower()

    if ext != ".dwg":
        return JsonResponse({
            "ok": False,
            "message": "DWG 파일만 업로드할 수 있습니다.",
            "filename": name
        }, status=400)

    temp_dir = Path(tempfile.gettempdir()) / "chickenbananacad_dwg"
    temp_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(name).name.replace("/", "_").replace("\\\\", "_")
    save_path = temp_dir / safe_name

    with open(save_path, "wb") as f:
        for chunk in upload.chunks():
            f.write(chunk)

    return JsonResponse({
        "ok": True,
        "ready": False,
        "stage": "DWG_UPLOAD_READY_V1",
        "filename": name,
        "saved_path": str(save_path),
        "message": "DWG 업로드는 성공했습니다. 다음 단계에서 DWG→DXF 변환엔진을 연결합니다.",
        "dxf_text": ""
    })


# Django csrf_exempt 적용
try:
    from django.views.decorators.csrf import csrf_exempt
    cblcad_dwg_to_dxf_api = csrf_exempt(cblcad_dwg_to_dxf_api)
except Exception:
    pass
# ============================================================
# /ChickenBananaCAD DWG 업로드 API 준비 V1
# ============================================================


# ============================================================
# ChickenBananaCAD DWG -> DXF 변환엔진 연결 V2
# 기존 cblcad_dwg_to_dxf_api를 V2로 재정의한다.
# ============================================================
def cblcad_dwg_to_dxf_api(request):
    import os
    import shutil
    import subprocess
    import tempfile
    import uuid
    from pathlib import Path
    from django.http import JsonResponse

    if request.method != "POST":
        return JsonResponse({
            "ok": False,
            "message": "POST 요청만 지원합니다."
        }, status=405)

    upload = request.FILES.get("file") or request.FILES.get("dwg")

    if not upload:
        return JsonResponse({
            "ok": False,
            "message": "DWG 파일이 없습니다."
        }, status=400)

    original_name = upload.name or "uploaded.dwg"
    ext = Path(original_name).suffix.lower()

    if ext != ".dwg":
        return JsonResponse({
            "ok": False,
            "message": "DWG 파일만 업로드할 수 있습니다.",
            "filename": original_name
        }, status=400)

    job_id = uuid.uuid4().hex[:12]
    base_dir = Path(tempfile.gettempdir()) / "chickenbananacad_dwg" / job_id
    input_dir = base_dir / "input"
    output_dir = base_dir / "output"

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(original_name).name.replace("/", "_").replace("\\", "_")
    dwg_path = input_dir / safe_name

    with open(dwg_path, "wb") as f:
        for chunk in upload.chunks():
            f.write(chunk)

    # ODA File Converter 후보 경로
    converter_candidates = [
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter 26.10.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter 25.12.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter 24.12.app/Contents/MacOS/ODAFileConverter",
        shutil.which("ODAFileConverter"),
        shutil.which("ODAFileConverter.exe"),
    ]

    converter = None
    for c in converter_candidates:
        if c and Path(c).exists():
            converter = str(c)
            break

    if not converter:
        return JsonResponse({
            "ok": True,
            "ready": False,
            "stage": "DWG_CONVERTER_NOT_FOUND",
            "filename": original_name,
            "saved_path": str(dwg_path),
            "message": "DWG 업로드는 성공했습니다. 다만 ODA File Converter를 찾지 못했습니다. ODA 설치 후 다시 시도하세요.",
            "dxf_text": "",
            "debug": {
                "checked_paths": [str(c) for c in converter_candidates if c]
            }
        })

    # ODA File Converter 명령 형식:
    # ODAFileConverter input_folder output_folder output_version output_type recurse audit
    # output_version 후보: ACAD2004
    # output_type: DXF
    cmd = [
        converter,
        str(input_dir),
        str(output_dir),
        "ACAD2004",
        "DXF",
        "0",
        "1",
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60
        )
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "ready": False,
            "stage": "DWG_CONVERT_EXCEPTION",
            "filename": original_name,
            "saved_path": str(dwg_path),
            "converter": converter,
            "message": f"DWG 변환 실행 중 오류가 발생했습니다: {e}",
            "dxf_text": ""
        }, status=500)

    dxf_files = list(output_dir.rglob("*.dxf"))

    if not dxf_files:
        return JsonResponse({
            "ok": False,
            "ready": False,
            "stage": "DWG_CONVERT_NO_DXF",
            "filename": original_name,
            "saved_path": str(dwg_path),
            "converter": converter,
            "command": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-3000:],
            "stderr": proc.stderr[-3000:],
            "message": "DWG 변환을 실행했지만 DXF 파일이 생성되지 않았습니다.",
            "dxf_text": ""
        }, status=500)

    dxf_path = dxf_files[0]

    # CBL_DWG_DXF_ENCODING_FIX_V1_START
    # ODA가 만든 DXF는 HEADER에 ANSI_949가 있어도 실제 바이트가 UTF-8일 수 있고,
    # 반대로 DWG 변환 결과는 CP949일 수 있다. 기존 errors="ignore" 방식은
    # CP949 한글 바이트를 조용히 삭제해서 TEXT는 있는데 한글 내용만 사라지는 문제가 있었다.
    def _cbl_decode_dxf_text_v1(dxf_file_path):
        raw = Path(dxf_file_path).read_bytes()

        head_latin = raw[:300000].decode("latin1", errors="ignore")
        codepage = ""
        try:
            m = re.search(r"\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n([^\r\n]+)", head_latin, re.I)
            if m:
                codepage = (m.group(1) or "").strip()
        except Exception:
            codepage = ""

        cp = (codepage or "").upper()
        candidates = ["utf-8-sig", "utf-8"]

        if "949" in cp or "KOREA" in cp or "KSC" in cp or "HANGEUL" in cp or "HANGUL" in cp:
            candidates += ["cp949", "euc-kr"]
        if "932" in cp or "SHIFT" in cp or "JIS" in cp:
            candidates += ["cp932", "shift_jis"]
        if "936" in cp or "GB" in cp:
            candidates += ["gbk", "cp936"]
        if "950" in cp or "BIG5" in cp:
            candidates += ["big5", "cp950"]

        candidates += ["cp949", "euc-kr", "gbk", "cp936", "cp932", "shift_jis", "big5", "cp950", "cp1252", "latin1"]

        unique = []
        for enc in candidates:
            if enc not in unique:
                unique.append(enc)

        # 1순위: strict decode. UTF-8 DXF는 UTF-8로, CP949 DXF는 CP949로 정확히 읽는다.
        for enc in unique:
            try:
                decoded = raw.decode(enc)
                return decoded, enc, codepage, 0
            except UnicodeDecodeError:
                continue
            except Exception:
                continue

        # 2순위: 모든 strict decode가 실패할 때만 replacement 점수로 선택한다.
        best = None
        for enc in unique:
            try:
                decoded = raw.decode(enc, errors="replace")
            except Exception:
                continue
            repl = decoded.count("�")
            hangul = sum(1 for ch in decoded[:2000000] if "가" <= ch <= "힣")
            # replacement가 적고 한글이 많이 살아나는 쪽 우선
            score = (repl, -hangul)
            if best is None or score < best[0]:
                best = (score, decoded, enc, repl)

        if best:
            return best[1], best[2], codepage, best[3]

        # 마지막 안전장치. 절대 한글 삭제용으로 ignore를 쓰지 않는다.
        decoded = raw.decode("latin1", errors="replace")
        return decoded, "latin1-replace", codepage, decoded.count("�")

    dxf_text, dxf_encoding, dxf_codepage, dxf_decode_replacements = _cbl_decode_dxf_text_v1(dxf_path)
    dxf_hangul_count = sum(1 for ch in dxf_text if "가" <= ch <= "힣")
    dxf_target_hits = {
        "제1부패조": dxf_text.find("제1부패조"),
        "제2부패조": dxf_text.find("제2부패조"),
        "여과조": dxf_text.find("여과조"),
        "방류조": dxf_text.find("방류조"),
        "부패조": dxf_text.find("부패조"),
    }
    # CBL_DWG_DXF_ENCODING_FIX_V1_END

    return JsonResponse({
        "ok": True,
        "ready": True,
        "stage": "DWG_CONVERTED_TO_DXF_V2",
        "filename": original_name,
        "saved_path": str(dwg_path),
        "dxf_path": str(dxf_path),
        "converter": converter,
        "message": "DWG를 DXF로 변환했습니다.",
        "dxf_text": dxf_text,
        "debug": {
            "returncode": proc.returncode,
            "stdout": proc.stdout[-1000:],
            "stderr": proc.stderr[-1000:],
            "dxf_encoding": dxf_encoding,
            "dxf_codepage": dxf_codepage,
            "dxf_decode_replacements": dxf_decode_replacements,
            "dxf_hangul_count": dxf_hangul_count,
            "dxf_target_hits": dxf_target_hits
        }
    })


try:
    from django.views.decorators.csrf import csrf_exempt
    cblcad_dwg_to_dxf_api = csrf_exempt(cblcad_dwg_to_dxf_api)
except Exception:
    pass
# ============================================================
# /ChickenBananaCAD DWG -> DXF 변환엔진 연결 V2
# ============================================================


# ============================================================
# ChickenBananaCAD DXF -> DWG 저장 API V1
# ============================================================
def cblcad_dxf_to_dwg_api(request):
    import json
    import shutil
    import subprocess
    import tempfile
    import uuid
    from pathlib import Path
    from django.http import JsonResponse, FileResponse

    if request.method != "POST":
        return JsonResponse({
            "ok": False,
            "message": "POST 요청만 지원합니다."
        }, status=405)

    # JSON 또는 FormData 둘 다 허용
    dxf_text = ""
    filename = "ChickenBananaCAD.dwg"

    try:
        if request.content_type and "application/json" in request.content_type:
            data = json.loads(request.body.decode("utf-8", errors="ignore") or "{}")
            dxf_text = data.get("dxf_text") or data.get("dxf") or ""
            filename = data.get("filename") or filename
        else:
            dxf_text = request.POST.get("dxf_text") or request.POST.get("dxf") or ""
            filename = request.POST.get("filename") or filename
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "message": f"요청 데이터를 읽지 못했습니다: {e}"
        }, status=400)

    if not dxf_text.strip():
        return JsonResponse({
            "ok": False,
            "message": "DXF 텍스트가 비어 있습니다."
        }, status=400)

    safe_base = Path(filename).stem or "ChickenBananaCAD"
    safe_base = safe_base.replace("/", "_").replace("\\", "_")
    if not safe_base:
        safe_base = "ChickenBananaCAD"

    job_id = uuid.uuid4().hex[:12]
    base_dir = Path(tempfile.gettempdir()) / "chickenbananacad_dwg_save" / job_id
    input_dir = base_dir / "input"
    output_dir = base_dir / "output"

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    dxf_path = input_dir / f"{safe_base}.dxf"

    # ODA가 ANSI_949 DXF도 읽지만, 우리가 생성하는 DXF는 우선 UTF-8로 저장
    dxf_path.write_text(dxf_text, encoding="utf-8", errors="ignore")

    converter_candidates = [
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter 26.10.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter 25.12.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter 24.12.app/Contents/MacOS/ODAFileConverter",
        shutil.which("ODAFileConverter"),
        shutil.which("ODAFileConverter.exe"),
    ]

    converter = None
    for c in converter_candidates:
        if c and Path(c).exists():
            converter = str(c)
            break

    if not converter:
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_CONVERTER_NOT_FOUND",
            "message": "ODA File Converter를 찾지 못했습니다.",
            "checked_paths": [str(c) for c in converter_candidates if c]
        }, status=500)

    # ODA File Converter 명령:
    # ODAFileConverter input_folder output_folder output_version output_type recurse audit
    cmd = [
        converter,
        str(input_dir),
        str(output_dir),
        "ACAD2004",
        "DWG",
        "0",
        "1",
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90
        )
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_EXCEPTION",
            "message": f"DXF→DWG 변환 실행 중 오류가 발생했습니다: {e}",
            "converter": converter
        }, status=500)

    dwg_files = list(output_dir.rglob("*.dwg")) + list(output_dir.rglob("*.DWG"))

    if not dwg_files:
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_NO_OUTPUT",
            "message": "DXF→DWG 변환을 실행했지만 DWG 파일이 생성되지 않았습니다.",
            "converter": converter,
            "command": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-3000:],
            "stderr": proc.stderr[-3000:],
            "input_dxf": str(dxf_path),
            "output_dir": str(output_dir)
        }, status=500)

    dwg_path = dwg_files[0]
    download_name = f"{safe_base}.dwg"

    response = FileResponse(
        open(dwg_path, "rb"),
        as_attachment=True,
        filename=download_name,
        content_type="application/acad"
    )
    response["X-CBL-CAD-STAGE"] = "DXF_TO_DWG_CONVERTED_V1"
    response["X-CBL-CAD-CONVERTER"] = converter
    return response


try:
    from django.views.decorators.csrf import csrf_exempt
    cblcad_dxf_to_dwg_api = csrf_exempt(cblcad_dxf_to_dwg_api)
except Exception:
    pass
# ============================================================
# /ChickenBananaCAD DXF -> DWG 저장 API V1
# ============================================================


# ============================================================
# ChickenBananaCAD DXF -> DWG 저장 안정화 V1.2
# 기존 cblcad_dxf_to_dwg_api를 V1.2로 재정의한다.
# ============================================================
def cblcad_dxf_to_dwg_api(request):
    import json
    import re
    import shutil
    import subprocess
    import tempfile
    import uuid
    from pathlib import Path
    from django.http import JsonResponse, FileResponse

    def find_converter():
        candidates = [
            "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
            "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
            "/Applications/ODAFileConverter 26.10.app/Contents/MacOS/ODAFileConverter",
            "/Applications/ODAFileConverter 25.12.app/Contents/MacOS/ODAFileConverter",
            "/Applications/ODAFileConverter 24.12.app/Contents/MacOS/ODAFileConverter",
            shutil.which("ODAFileConverter"),
            shutil.which("ODAFileConverter.exe"),
        ]
        for c in candidates:
            if c and Path(c).exists():
                return str(c), [str(x) for x in candidates if x]
        return None, [str(x) for x in candidates if x]

    def normalize_dxf_text(text):
        text = str(text or "")
        text = text.replace("\ufeff", "")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("undefined", "0").replace("NaN", "0").replace("Infinity", "0").replace("-Infinity", "0")

        lines = [line.rstrip() for line in text.split("\n")]

        # 앞뒤 빈 줄 제거
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()

        joined_upper = "\n".join(lines).upper()

        # HEADER가 있으면 DWGCODEPAGE 보강
        if "$DWGCODEPAGE" not in joined_upper:
            for i in range(len(lines) - 1):
                if lines[i].strip() == "2" and lines[i + 1].strip().upper() == "HEADER":
                    insert_at = i + 2
                    lines[insert_at:insert_at] = [
                        "  9",
                        "$DWGCODEPAGE",
                        "  3",
                        "ANSI_949",
                    ]
                    break

        # EOF 보강
        if not lines or lines[-1].strip().upper() != "EOF":
            if "ENDSEC" not in joined_upper[-200:]:
                lines += ["  0", "ENDSEC"]
            lines += ["  0", "EOF"]

        return "\r\n".join(lines) + "\r\n"

    def make_safe_base(filename, fallback):
        stem = Path(str(filename or "")).stem
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)
        stem = stem.strip("._-")
        if not stem:
            stem = fallback
        return stem[:80]

    def write_dxf(path, text, encoding_name):
        if encoding_name == "cp949":
            path.write_text(text, encoding="cp949", errors="ignore")
        else:
            path.write_text(text, encoding="utf-8", errors="ignore")

    if request.method != "POST":
        return JsonResponse({
            "ok": False,
            "message": "POST 요청만 지원합니다."
        }, status=405)

    dxf_text = ""
    filename = "ChickenBananaCAD.dwg"

    try:
        if request.content_type and "application/json" in request.content_type:
            data = json.loads(request.body.decode("utf-8", errors="ignore") or "{}")
            dxf_text = data.get("dxf_text") or data.get("dxf") or ""
            filename = data.get("filename") or filename
        else:
            dxf_text = request.POST.get("dxf_text") or request.POST.get("dxf") or ""
            filename = request.POST.get("filename") or filename
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_BAD_REQUEST",
            "message": f"요청 데이터를 읽지 못했습니다: {e}"
        }, status=400)

    if not dxf_text.strip():
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_EMPTY_DXF",
            "message": "DXF 텍스트가 비어 있습니다."
        }, status=400)

    converter, checked_paths = find_converter()

    if not converter:
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_CONVERTER_NOT_FOUND",
            "message": "ODA File Converter를 찾지 못했습니다.",
            "checked_paths": checked_paths
        }, status=500)

    job_id = uuid.uuid4().hex[:12]
    base_dir = Path(tempfile.gettempdir()) / "chickenbananacad_dwg_save" / job_id
    base_dir.mkdir(parents=True, exist_ok=True)

    original_base = make_safe_base(filename, f"cblcad_{job_id}")
    safe_base = f"cblcad_{job_id}"

    normalized = normalize_dxf_text(dxf_text)

    attempts = []
    output_versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP
    encodings = ["cp949", "utf-8"]
    audits = ["1", "0"]
    recurses = ["0", "1"]

    converter_cwd = str(Path(converter).parent)

    for enc in encodings:
        for ver in output_versions:
            for audit in audits:
                for recurse in recurses:
                    input_dir = base_dir / f"input_{enc}_{ver}_{audit}_{recurse}"
                    output_dir = base_dir / f"output_{enc}_{ver}_{audit}_{recurse}"
                    input_dir.mkdir(parents=True, exist_ok=True)
                    output_dir.mkdir(parents=True, exist_ok=True)

                    dxf_path = input_dir / f"{safe_base}.dxf"
                    write_dxf(dxf_path, normalized, enc)

                    cmd = [
                        converter,
                        str(input_dir),
                        str(output_dir),
                        ver,
                        "DWG",
                        recurse,
                        audit,
                    ]

                    try:
                        proc = subprocess.run(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=90,
                            cwd=converter_cwd
                        )
                        returncode = proc.returncode
                        stdout = proc.stdout or ""
                        stderr = proc.stderr or ""
                    except Exception as e:
                        attempts.append({
                            "encoding": enc,
                            "version": ver,
                            "audit": audit,
                            "recurse": recurse,
                            "input_dxf": str(dxf_path),
                            "output_dir": str(output_dir),
                            "exception": str(e)
                        })
                        continue

                    all_files = [str(p) for p in output_dir.rglob("*") if p.is_file()]
                    dwg_files = [
                        p for p in output_dir.rglob("*")
                        if p.is_file() and p.suffix.lower() == ".dwg"
                    ]

                    attempts.append({
                        "encoding": enc,
                        "version": ver,
                        "audit": audit,
                        "recurse": recurse,
                        "input_dxf": str(dxf_path),
                        "output_dir": str(output_dir),
                        "returncode": returncode,
                        "stdout": stdout[-1000:],
                        "stderr": stderr[-1000:],
                        "output_files": all_files[:30],
                    })

                    if dwg_files:
                        dwg_path = dwg_files[0]
                        download_name = f"{original_base or 'ChickenBananaCAD'}.dwg"

                        response = FileResponse(
                            open(dwg_path, "rb"),
                            as_attachment=True,
                            filename=download_name,
                            content_type="application/acad"
                        )
                        response["X-CBL-CAD-STAGE"] = "DXF_TO_DWG_CONVERTED_STABLE_V1_2"
                        response["X-CBL-CAD-CONVERTER"] = converter
                        response["X-CBL-CAD-ATTEMPT"] = f"{enc}/{ver}/audit{audit}/recurse{recurse}"
                        return response

    preview = normalized[:2000]

    return JsonResponse({
        "ok": False,
        "stage": "DXF_TO_DWG_NO_OUTPUT_STABLE_V1_2",
        "message": "여러 방식으로 DXF→DWG 변환을 재시도했지만 DWG 파일이 생성되지 않았습니다. DXF 구조 보강이 추가로 필요합니다.",
        "converter": converter,
        "base_dir": str(base_dir),
        "attempt_count": len(attempts),
        "attempts": attempts[-12:],
        "dxf_preview": preview
    }, status=500)


try:
    from django.views.decorators.csrf import csrf_exempt
    cblcad_dxf_to_dwg_api = csrf_exempt(cblcad_dxf_to_dwg_api)
except Exception:
    pass
# ============================================================
# /ChickenBananaCAD DXF -> DWG 저장 안정화 V1.2
# ============================================================


# ============================================================
# ChickenBananaCAD DXF -> DWG 저장 단일 실행 V1.4
# 기존 cblcad_dxf_to_dwg_api를 V1.4로 재정의한다.
# 중요: ODA 반복 실행 금지. 1회만 실행.
# ============================================================
def cblcad_dxf_to_dwg_api(request):
    import json
    import re
    import shutil
    import subprocess
    import tempfile
    import uuid
    from pathlib import Path
    from django.http import JsonResponse, FileResponse

    if request.method != "POST":
        return JsonResponse({
            "ok": False,
            "message": "POST 요청만 지원합니다."
        }, status=405)

    try:
        if request.content_type and "application/json" in request.content_type:
            data = json.loads(request.body.decode("utf-8", errors="ignore") or "{}")
            dxf_text = data.get("dxf_text") or data.get("dxf") or ""
            filename = data.get("filename") or "ChickenBananaCAD.dwg"
        else:
            dxf_text = request.POST.get("dxf_text") or request.POST.get("dxf") or ""
            filename = request.POST.get("filename") or "ChickenBananaCAD.dwg"
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_BAD_REQUEST_V1_4",
            "message": f"요청 데이터를 읽지 못했습니다: {e}"
        }, status=400)

    if not dxf_text.strip():
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_EMPTY_DXF_V1_4",
            "message": "DXF 텍스트가 비어 있습니다."
        }, status=400)

    converter_candidates = [
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
        shutil.which("ODAFileConverter"),
    ]

    converter = None
    for c in converter_candidates:
        if c and Path(c).exists():
            converter = str(c)
            break

    if not converter:
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_CONVERTER_NOT_FOUND_V1_4",
            "message": "ODA File Converter를 찾지 못했습니다.",
            "checked_paths": [str(c) for c in converter_candidates if c]
        }, status=500)

    job_id = uuid.uuid4().hex[:12]
    base_dir = Path(tempfile.gettempdir()) / "chickenbananacad_dwg_save_once" / job_id
    input_dir = base_dir / "input"
    output_dir = base_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    original_stem = Path(str(filename)).stem
    original_stem = re.sub(r"[^A-Za-z0-9가-힣_.-]+", "_", original_stem).strip("._-")
    if not original_stem:
        original_stem = "ChickenBananaCAD"

    # ODA 안정성을 위해 실제 변환용 파일명은 영문으로 고정
    dxf_path = input_dir / "cblcad_save.dxf"

    # DXF 정리
    text = str(dxf_text or "")
    text = text.replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("undefined", "0").replace("NaN", "0").replace("Infinity", "0").replace("-Infinity", "0")
    lines = [line.rstrip() for line in text.split("\n")]

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines or lines[-1].strip().upper() != "EOF":
        lines += ["0", "EOF"]

    text = "\r\n".join(lines) + "\r\n"

    # R12/ANSI 계열은 cp949가 ODA에서 더 안전
    dxf_path.write_text(text, encoding="cp949", errors="ignore")

    cmd = [
        converter,
        str(input_dir),
        str(output_dir),
        "ACAD2004",
        "DWG",
        "0",
        "0",
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            cwd=str(Path(converter).parent)
        )
    except subprocess.TimeoutExpired:
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_TIMEOUT_V1_4",
            "message": "ODA 변환이 60초 안에 끝나지 않았습니다. ODA 창이 떠 있으면 Stop을 누르세요.",
            "converter": converter,
            "input_dxf": str(dxf_path),
            "output_dir": str(output_dir)
        }, status=500)
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_EXCEPTION_V1_4",
            "message": f"DXF→DWG 변환 실행 중 오류가 발생했습니다: {e}",
            "converter": converter,
            "input_dxf": str(dxf_path),
            "output_dir": str(output_dir)
        }, status=500)

    dwg_files = [
        p for p in output_dir.rglob("*")
        if p.is_file() and p.suffix.lower() == ".dwg"
    ]

    all_files = [str(p) for p in output_dir.rglob("*") if p.is_file()]

    if not dwg_files:
        return JsonResponse({
            "ok": False,
            "stage": "DXF_TO_DWG_NO_OUTPUT_V1_4",
            "message": "ODA를 1회 실행했지만 DWG 파일이 생성되지 않았습니다. 반복 실행은 중단했습니다.",
            "converter": converter,
            "command": cmd,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-2000:],
            "stderr": (proc.stderr or "")[-2000:],
            "input_dxf": str(dxf_path),
            "output_dir": str(output_dir),
            "output_files": all_files[:50],
            "dxf_preview": text[:1500]
        }, status=500)

    dwg_path = dwg_files[0]

    response = FileResponse(
        open(dwg_path, "rb"),
        as_attachment=True,
        filename=f"{original_stem}.dwg",
        content_type="application/acad"
    )
    response["X-CBL-CAD-STAGE"] = "DXF_TO_DWG_CONVERTED_V1_4"
    response["X-CBL-CAD-CONVERTER"] = converter
    return response


try:
    from django.views.decorators.csrf import csrf_exempt
    cblcad_dxf_to_dwg_api = csrf_exempt(cblcad_dxf_to_dwg_api)
except Exception:
    pass
# ============================================================
# /ChickenBananaCAD DXF -> DWG 저장 단일 실행 V1.4
# ============================================================

# CBLCAD_DWG_API_RESTORE_V1_START
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, FileResponse
import os
import shutil
import tempfile
import subprocess
from pathlib import Path as _CBLCAD_Path


def _cblcad_find_oda_converter_restore_v1():
    env_path = os.environ.get("CBLCAD_ODA_CONVERTER")
    candidates = []

    if env_path:
        candidates.append(env_path)

    path_cmd = shutil.which("ODAFileConverter")
    if path_cmd:
        candidates.append(path_cmd)

    candidates += [
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/usr/local/bin/ODAFileConverter",
        "/usr/bin/ODAFileConverter",
        "/opt/ODAFileConverter/ODAFileConverter",
    ]

    for c in candidates:
        if c and os.path.exists(c):
            return c

    return None


def _cblcad_run_oda_restore_v1(input_dir, output_dir, output_format, input_filter):
    exe = _cblcad_find_oda_converter_restore_v1()

    if not exe:
        raise RuntimeError(
            "ODA File Converter를 찾지 못했습니다. "
            "Mac에 ODA File Converter가 설치되어 있는지 확인하세요. "
            "필요하면 CBLCAD_ODA_CONVERTER 환경변수를 지정하세요."
        )

    versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP
    last_log = ""

    for ver in versions:
        cmd = [
            exe,
            str(input_dir),
            str(output_dir),
            ver,
            output_format,
            "0",
            "1",
            input_filter,
        ]

        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
        )

        if p.returncode == 0:
            return True

        last_log = (p.stderr or p.stdout or "").strip()

    raise RuntimeError("ODA 변환 실패: " + last_log)



# ============================================================
# CBL_DWG_KOREAN_TEXT_REAL_FIX_V2_START
# DWG -> DXF 응답 한글 보존용 공통 함수
# - ODA 출력 DXF가 UTF-8인데 HEADER만 ANSI_949인 경우가 있다.
# - errors="ignore" 또는 잘못된 latin1/CP949 fallback은 한글 TEXT를 조용히 삭제한다.
# - JSON 응답은 ensure_ascii=False로 보내서 프론트에서 literal \uXXXX 중복 escape가 생기지 않게 한다.
def _cbl_decode_dxf_text_korean_v2(path_or_bytes):
    import re as _re
    from pathlib import Path as _Path

    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = bytes(path_or_bytes)
    else:
        raw = _Path(path_or_bytes).read_bytes()

    head = raw[:500000].decode('latin1', errors='ignore')
    codepage = ''
    try:
        m = _re.search(r'\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n([^\r\n]+)', head, _re.I)
        if m:
            codepage = (m.group(1) or '').strip()
    except Exception:
        codepage = ''

    # 실제 ODA DXF는 HEADER가 ANSI_949여도 본문이 UTF-8인 경우가 있으므로 UTF-8 strict 우선.
    candidates = ['utf-8-sig', 'utf-8']
    cp = (codepage or '').upper()
    if any(x in cp for x in ['949', 'KOREA', 'KSC', 'HANGEUL', 'HANGUL']):
        candidates += ['cp949', 'euc-kr']
    candidates += ['cp949', 'euc-kr', 'cp932', 'shift_jis', 'gbk', 'cp936', 'big5', 'cp950', 'cp1252', 'latin1']

    unique = []
    for enc in candidates:
        if enc not in unique:
            unique.append(enc)

    strict_results = []
    for enc in unique:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        except Exception:
            continue
        hangul = sum(1 for ch in text if '가' <= ch <= '힣')
        target_hits = {
            '제1부패조': text.find('제1부패조'),
            '제2부패조': text.find('제2부패조'),
            '여과조': text.find('여과조'),
            '방류조': text.find('방류조'),
            '부패조': text.find('부패조'),
        }
        strict_results.append((enc, text, hangul, target_hits))

    if strict_results:
        # 대상 한글이 있거나 한글 수가 가장 많은 strict 결과를 선택한다.
        strict_results.sort(key=lambda item: (max(item[3].values()) >= 0, item[2], item[0].startswith('utf-8')), reverse=True)
        enc, text, hangul, target_hits = strict_results[0]
        return text, {
            'encoding': enc,
            'codepage': codepage,
            'decode_mode': 'strict',
            'hangul_count': hangul,
            'target_hits': target_hits,
            'raw_size': len(raw),
        }

    # strict가 전부 실패할 때만 replace. ignore는 절대 쓰지 않는다.
    best = None
    for enc in unique:
        try:
            text = raw.decode(enc, errors='replace')
        except Exception:
            continue
        repl = text.count('\ufffd')
        hangul = sum(1 for ch in text if '가' <= ch <= '힣')
        target_hits = {
            '제1부패조': text.find('제1부패조'),
            '제2부패조': text.find('제2부패조'),
            '여과조': text.find('여과조'),
            '방류조': text.find('방류조'),
            '부패조': text.find('부패조'),
        }
        score = (max(target_hits.values()) >= 0, hangul, -repl)
        if best is None or score > best[0]:
            best = (score, enc, text, repl, hangul, target_hits)

    if best:
        _, enc, text, repl, hangul, target_hits = best
        return text, {
            'encoding': enc,
            'codepage': codepage,
            'decode_mode': 'replace',
            'replacement_count': repl,
            'hangul_count': hangul,
            'target_hits': target_hits,
            'raw_size': len(raw),
        }

    text = raw.decode('latin1', errors='replace')
    return text, {
        'encoding': 'latin1-replace-final',
        'codepage': codepage,
        'decode_mode': 'final',
        'replacement_count': text.count('\ufffd'),
        'hangul_count': 0,
        'target_hits': {},
        'raw_size': len(raw),
    }


def _cbl_json_response_korean_v2(payload, status=200):
    import json as _json
    from django.http import HttpResponse as _HttpResponse
    return _HttpResponse(
        _json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        status=status,
        content_type='application/json; charset=utf-8',
    )
# CBL_DWG_KOREAN_TEXT_REAL_FIX_V2_END
# ============================================================


@csrf_exempt
def cblcad_dwg_to_dxf(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "success": False, "error": "POST만 허용됩니다."}, status=405)

    uploaded = request.FILES.get("file") or request.FILES.get("dwg") or request.FILES.get("upload")

    if not uploaded:
        return JsonResponse({"ok": False, "success": False, "error": "DWG 파일이 없습니다."}, status=400)

    tmp = tempfile.mkdtemp(prefix="cblcad_dwg_to_dxf_")

    try:
        root = _CBLCAD_Path(tmp)
        in_dir = root / "in"
        out_dir = root / "out"
        in_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _CBLCAD_Path(uploaded.name).name
        if not safe_name.lower().endswith(".dwg"):
            safe_name += ".dwg"

        dwg_path = in_dir / safe_name

        with open(dwg_path, "wb") as f:
            for chunk in uploaded.chunks():
                f.write(chunk)

        _cblcad_run_oda_restore_v1(in_dir, out_dir, "DXF", "*.DWG")

        dxf_files = list(out_dir.rglob("*.dxf")) + list(out_dir.rglob("*.DXF"))

        if not dxf_files:
            return JsonResponse(
                {"ok": False, "success": False, "error": "DXF 변환 결과 파일을 찾지 못했습니다."},
                status=500,
            )

        dxf_path = dxf_files[0]
        dxf_text, cbl_dxf_decode_info_v2 = _cbl_decode_dxf_text_korean_v2(dxf_path)

        # 기존 JS가 어떤 키를 보더라도 최대한 맞도록 여러 필드 제공
        # CBL_DWG_KOREAN_TEXT_REAL_FIX_V2: ensure_ascii=False로 한글 TEXT를 그대로 보낸다.
        return _cbl_json_response_korean_v2({
            "ok": True,
            "success": True,
            "filename": safe_name,
            "name": safe_name,
            "dxf_filename": dxf_path.name,
            "dxf_name": dxf_path.name,
            "dxf_text": dxf_text,
            "dxf": dxf_text,
            "text": dxf_text,
            "content": dxf_text,
            "cbl_dxf_decode_info_v2": cbl_dxf_decode_info_v2,
        })

    except Exception as e:
        return JsonResponse({"ok": False, "success": False, "error": str(e)}, status=500)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@csrf_exempt
def cblcad_dxf_to_dwg(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "success": False, "error": "POST만 허용됩니다."}, status=405)

    tmp = tempfile.mkdtemp(prefix="cblcad_dxf_to_dwg_")

    try:
        root = _CBLCAD_Path(tmp)
        in_dir = root / "in"
        out_dir = root / "out"
        in_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        filename = request.POST.get("filename") or request.GET.get("filename") or "drawing.dwg"

        dxf_text = ""

        uploaded = request.FILES.get("file") or request.FILES.get("dxf") or request.FILES.get("upload")

        if uploaded:
            filename = request.POST.get("filename") or uploaded.name
            raw = b"".join(chunk for chunk in uploaded.chunks())
            dxf_text = raw.decode("utf-8", errors="replace")
        else:
            raw_body = request.body or b""
            body_text = raw_body.decode("utf-8", errors="replace")

            # JSON 또는 순수 DXF 텍스트 둘 다 허용
            if body_text.strip().startswith("{"):
                import json
                data = json.loads(body_text)
                filename = data.get("filename") or filename
                dxf_text = data.get("dxf_text") or data.get("dxf") or data.get("text") or ""
            else:
                dxf_text = body_text

        filename = _CBLCAD_Path(filename).name
        if not filename.lower().endswith(".dwg"):
            filename += ".dwg"

        dxf_name = _CBLCAD_Path(filename).with_suffix(".dxf").name
        dxf_path = in_dir / dxf_name
        dxf_path.write_text(dxf_text, encoding="utf-8", errors="replace")

        _cblcad_run_oda_restore_v1(in_dir, out_dir, "DWG", "*.DXF")

        dwg_files = list(out_dir.rglob("*.dwg")) + list(out_dir.rglob("*.DWG"))

        if not dwg_files:
            return JsonResponse(
                {"ok": False, "success": False, "error": "DWG 변환 결과 파일을 찾지 못했습니다."},
                status=500,
            )

        dwg_path = dwg_files[0]

        return FileResponse(
            open(dwg_path, "rb"),
            as_attachment=True,
            filename=filename,
            content_type="application/octet-stream",
        )

    except Exception as e:
        return JsonResponse({"ok": False, "success": False, "error": str(e)}, status=500)
# CBLCAD_DWG_API_RESTORE_V1_END


# ============================================================
# CBLCAD_DWG_TO_DXF_CLEAN_API_V2_START
# ChickenBananaCAD DWG -> DXF 깨끗한 변환 API
# ============================================================

def _cblcad_dwg_find_oda_binary_v2():
    candidates = []

    env = os.environ.get("ODA_FILE_CONVERTER")
    if env:
        candidates.append(env)

    candidates += [
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverterApp",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverterApp",
    ]

    whiches = [
        shutil.which("ODAFileConverter"),
        shutil.which("ODAFileConverterApp"),
        shutil.which("oda_file_converter"),
        shutil.which("dwg2dxf"),
    ]

    candidates += [x for x in whiches if x]

    # 앱 번들 내부 실행파일 자동 탐색
    app_roots = [
        "/Applications/ODAFileConverter.app",
        "/Applications/ODA File Converter.app",
    ]

    for root in app_roots:
        macos_dir = os.path.join(root, "Contents", "MacOS")
        if os.path.isdir(macos_dir):
            for name in os.listdir(macos_dir):
                candidates.append(os.path.join(macos_dir, name))

    seen = set()

    for c in candidates:
        if not c:
            continue

        c = os.path.abspath(c)

        if c in seen:
            continue

        seen.add(c)

        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c

    return None


def _cblcad_dwg_read_text_safe_v2(path):
    text, _info = _cbl_decode_dxf_text_korean_direct_v3(path)
    return text

def _cblcad_dwg_probably_dxf_v2(path):
    try:
        txt = _cblcad_dwg_read_text_safe_v2(path)[:10000].upper()
        return "SECTION" in txt and "ENTITIES" in txt
    except Exception:
        return False


def _cblcad_dwg_run_oda_v2(converter, input_dir, output_dir):
    versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP

    attempts = []

    for ver in versions:
        cmd = [
            converter,
            input_dir,
            output_dir,
            ver,
            "DXF",
            "0",
            "1",
            "*.dwg",
        ]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
            )

            dxf_files = []
            dxf_files += glob.glob(os.path.join(output_dir, "*.dxf"))
            dxf_files += glob.glob(os.path.join(output_dir, "*.DXF"))
            dxf_files += glob.glob(os.path.join(output_dir, "**", "*.dxf"), recursive=True)
            dxf_files += glob.glob(os.path.join(output_dir, "**", "*.DXF"), recursive=True)

            dxf_files = [x for x in dxf_files if os.path.isfile(x)]

            attempts.append({
                "version": ver,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-1200:],
                "stderr": (proc.stderr or "")[-1200:],
                "found": dxf_files,
                "cmd": cmd,
            })

            if dxf_files:
                dxf_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                return dxf_files[0], attempts

        except Exception as e:
            attempts.append({
                "version": ver,
                "error": str(e),
                "cmd": cmd,
            })

    return None, attempts


@csrf_exempt
def cblcad_dwg_to_dxf_clean_api(request):
    if request.method != "POST":
        return JsonResponse({
            "ok": False,
            "error": "POST only",
        }, status=405)

    upload = (
        request.FILES.get("file")
        or request.FILES.get("dwg")
        or request.FILES.get("upload")
    )

    if not upload:
        return JsonResponse({
            "ok": False,
            "error": "DWG 파일이 전달되지 않았습니다.",
            "files": list(request.FILES.keys()),
        }, status=400)

    original_name = getattr(upload, "name", "drawing.dwg")

    with tempfile.TemporaryDirectory() as td:
        input_dir = os.path.join(td, "input")
        output_dir = os.path.join(td, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # ODA가 한글/공백 파일명에서 실패하는 경우가 있어 안전한 파일명으로 저장
        input_path = os.path.join(input_dir, "input.dwg")

        with open(input_path, "wb") as f:
            for chunk in upload.chunks():
                f.write(chunk)

        size = os.path.getsize(input_path)

        if size <= 0:
            return JsonResponse({
                "ok": False,
                "error": "업로드된 DWG 파일 크기가 0입니다.",
            }, status=400)

        # DXF를 잘못 넣은 경우는 그대로 통과
        if _cblcad_dwg_probably_dxf_v2(input_path):
            return JsonResponse({
                "ok": True,
                "name": original_name,
                "dxf": _cblcad_dwg_read_text_safe_v2(input_path),
                "note": "입력 파일이 DXF로 감지되어 그대로 반환했습니다.",
            })

        converter = _cblcad_dwg_find_oda_binary_v2()

        if not converter:
            return JsonResponse({
                "ok": False,
                "error": "ODA File Converter 실행파일을 찾지 못했습니다.",
                "hint": "앱은 있어도 Contents/MacOS 안 실행파일 경로가 다를 수 있습니다.",
                "checked_app": [
                    "/Applications/ODAFileConverter.app",
                    "/Applications/ODA File Converter.app",
                ],
            }, status=500)

        dxf_path, attempts = _cblcad_dwg_run_oda_v2(converter, input_dir, output_dir)

        if not dxf_path or not os.path.exists(dxf_path):
            return JsonResponse({
                "ok": False,
                "error": "ODA 변환은 실행됐지만 DXF 파일이 생성되지 않았습니다.",
                "converter": converter,
                "input_size": size,
                "attempts": attempts[-3:],
            }, status=500)

        dxf_text = _cblcad_dwg_read_text_safe_v2(dxf_path)

        if not dxf_text.strip():
            return JsonResponse({
                "ok": False,
                "error": "생성된 DXF가 비어 있습니다.",
                "converter": converter,
                "dxf_path": dxf_path,
                "attempts": attempts[-3:],
            }, status=500)

        return JsonResponse({
            "ok": True,
            "name": original_name,
            "converter": converter,
            "dxf_size": len(dxf_text),
            "dxf": dxf_text,
        })


# CBLCAD_DWG_TO_DXF_CLEAN_API_V2_END
# ============================================================


# ============================================================
# CBLCAD_DXF_TO_DWG_SAVE_API_V1_START
# ChickenBananaCAD DXF -> DWG 저장 변환 API
# ============================================================

def _cblcad_find_oda_for_dxf_to_dwg_v1():
    candidates = []

    env = os.environ.get("ODA_FILE_CONVERTER")
    if env:
        candidates.append(env)

    candidates += [
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverterApp",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverterApp",
        shutil.which("ODAFileConverter"),
        shutil.which("ODAFileConverterApp"),
    ]

    for root in [
        "/Applications/ODAFileConverter.app",
        "/Applications/ODA File Converter.app",
    ]:
        macos_dir = os.path.join(root, "Contents", "MacOS")
        if os.path.isdir(macos_dir):
            for name in os.listdir(macos_dir):
                candidates.append(os.path.join(macos_dir, name))

    seen = set()

    for c in candidates:
        if not c:
            continue
        c = os.path.abspath(c)
        if c in seen:
            continue
        seen.add(c)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c

    return None


def _cblcad_run_oda_dxf_to_dwg_v1(converter, input_dir, output_dir):
    versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP
    attempts = []

    for ver in versions:
        cmd = [
            converter,
            input_dir,
            output_dir,
            ver,
            "DWG",
            "0",
            "1",
            "*.dxf",
        ]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
            )

            dwg_files = []
            dwg_files += glob.glob(os.path.join(output_dir, "*.dwg"))
            dwg_files += glob.glob(os.path.join(output_dir, "*.DWG"))
            dwg_files += glob.glob(os.path.join(output_dir, "**", "*.dwg"), recursive=True)
            dwg_files += glob.glob(os.path.join(output_dir, "**", "*.DWG"), recursive=True)
            dwg_files = [x for x in dwg_files if os.path.isfile(x)]

            attempts.append({
                "version": ver,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-1000:],
                "stderr": (proc.stderr or "")[-1000:],
                "found": dwg_files,
            })

            if dwg_files:
                dwg_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                return dwg_files[0], attempts

        except Exception as e:
            attempts.append({
                "version": ver,
                "error": str(e),
            })

    return None, attempts


@csrf_exempt

# ===== CBL ODA DXF WRITE CP949 V1 START =====
def _cbl_write_dxf_text_for_oda_v1(path, dxf_text):
    """
    Browser에서 넘어온 dxf_text는 Python 문자열이다.
    그런데 DXF 헤더가 ANSI_949이고 레이어명이 한글이면,
    ODA File Converter는 UTF-8 텍스트보다 CP949 바이트를 더 안정적으로 읽는다.
    """
    data = dxf_text or ""

    try:
        has_korean = any("\uac00" <= ch <= "\ud7a3" for ch in data)
        use_cp949 = ("ANSI_949" in data) or has_korean

        if use_cp949:
            path.write_bytes(data.encode("cp949", errors="replace"))
        else:
            path.write_bytes(data.encode("utf-8", errors="replace"))
    except Exception:
        path.write_text(data, encoding="utf-8", errors="replace")
# ===== CBL ODA DXF WRITE CP949 V1 END =====


# ===== CBL V22.2 RAW MERGE TEXT/UTF8 SAVE FIX START =====
def _cbl_v22_2_dxf_has_korean_or_non_ascii(data):
    try:
        return any(ord(ch) > 127 for ch in (data or ""))
    except Exception:
        return False


def _cbl_v22_2_normalize_dxf_newlines(data):
    data = str(data or "")
    data = data.replace("\r\n", "\n").replace("\r", "\n")
    # ODA가 DXF를 더 안정적으로 읽도록 CRLF로 저장
    return data.replace("\n", "\r\n")


def _cbl_v22_2_set_dwgcodepage_ansi_949(data):
    """한글 레이어명/문자 보존용. DXF HEADER의 $DWGCODEPAGE를 ANSI_949로 맞춘다."""
    data = str(data or "")
    norm = data.replace("\r\n", "\n").replace("\r", "\n")
    lines = norm.split("\n")

    # 이미 $DWGCODEPAGE가 있으면 바로 다음 group-code 3 값을 ANSI_949로 교체
    for i in range(0, len(lines) - 3):
        if lines[i].strip() == "9" and lines[i + 1].strip().upper() == "$DWGCODEPAGE":
            for j in range(i + 2, min(i + 8, len(lines) - 1)):
                if lines[j].strip() == "3":
                    lines[j + 1] = "ANSI_949"
                    return "\n".join(lines)

    # HEADER 섹션이 있으면 HEADER 시작 직후 삽입
    for i in range(0, len(lines) - 3):
        if (
            lines[i].strip() == "0"
            and lines[i + 1].strip().upper() == "SECTION"
            and lines[i + 2].strip() == "2"
            and lines[i + 3].strip().upper() == "HEADER"
        ):
            insert_at = i + 4
            lines[insert_at:insert_at] = ["9", "$DWGCODEPAGE", "3", "ANSI_949"]
            return "\n".join(lines)

    return norm


def _cbl_write_dxf_text_for_oda_v22_2(path, dxf_text, request=None):
    """
    V22.2.1 FIX:
    - V22.2에서 한글/RAW 보존 저장 시 CP949 bytes로 강제 저장하던 처리를 되돌린다.
    - 현재 열기 파이프라인은 ODA DXF 본문을 UTF-8 우선으로 해석한다.
      저장 입력도 UTF-8로 맞춰야 저장 후 다시 열었을 때 한글 TEXT/MTEXT와 한글 레이어명이 깨지지 않는다.
    - $DWGCODEPAGE 헤더는 원본 RAW 구조 보존을 위해 여기서 강제로 바꾸지 않는다.
    """
    data = str(dxf_text or "")
    raw_preserve = False
    try:
        raw_preserve = (
            request is not None and (
                request.POST.get("cbl_v21_15_force_front_new_layers") == "1"
                or request.POST.get("cbl_v22_preserve_raw") == "1"
                or request.POST.get("cbl_v22_save_mode") == "raw_preserve_new_layers"
            )
        )
    except Exception:
        raw_preserve = False

    # 중요: CP949 금지. ODA가 생성/소비하는 DXF 본문을 UTF-8 기준으로 맞춘다.
    out = _cbl_v22_2_normalize_dxf_newlines(data).encode("utf-8", errors="replace")
    path.write_bytes(out)
    try:
        print("✅ CBL V22.2.1 DXF write: utf-8 (cp949 forced write reverted)", {
            "raw_preserve": raw_preserve,
            "chars": len(data),
            "bytes": len(out),
        })
    except Exception:
        pass
# ===== CBL V22.2 RAW MERGE TEXT/UTF8 SAVE FIX END =====

def cblcad_dxf_to_dwg_save_api(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    upload = request.FILES.get("file") or request.FILES.get("dxf")
    dxf_text = request.POST.get("dxf_text") or ""

    with tempfile.TemporaryDirectory() as td:
        input_dir = os.path.join(td, "input")
        output_dir = os.path.join(td, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        input_path = os.path.join(input_dir, "input.dxf")

        # V22.2:
        # FormData에는 원래 client DXF blob(file)도 있고, V21.15가 request.POST에 주입한 merged_dxf도 있다.
        # 기존 코드는 upload(file)를 먼저 써서 merged_dxf를 무시했다.
        # raw-preserve/new-layer 병합 저장에서는 반드시 POST dxf_text를 우선 변환해야 한다.
        force_text_dxf_v22_2 = False
        try:
            force_text_dxf_v22_2 = (
                request.POST.get("cbl_v21_15_force_front_new_layers") == "1"
                or request.POST.get("cbl_v22_preserve_raw") == "1"
                or request.POST.get("cbl_v22_save_mode") == "raw_preserve_new_layers"
            )
        except Exception:
            force_text_dxf_v22_2 = False

        dxf_input_source_v22_2 = ""

        if force_text_dxf_v22_2 and dxf_text.strip():
            _cbl_write_dxf_text_for_oda_v22_2(input_path, dxf_text, request=request)
            dxf_input_source_v22_2 = "merged_post_dxf_text"
        elif upload:
            with open(input_path, "wb") as f:
                for chunk in upload.chunks():
                    f.write(chunk)
            dxf_input_source_v22_2 = "uploaded_file_blob"
        elif dxf_text.strip():
            _cbl_write_dxf_text_for_oda_v22_2(input_path, dxf_text, request=request)
            dxf_input_source_v22_2 = "post_dxf_text"
        else:
            return JsonResponse({
                "ok": False,
                "error": "DXF 데이터가 전달되지 않았습니다.",
            }, status=400)

        try:
            print("✅ CBL V22.2 DXF input source:", {
                "source": dxf_input_source_v22_2,
                "force_text": force_text_dxf_v22_2,
                "has_upload": bool(upload),
                "dxf_text_chars": len(dxf_text or ""),
                "input_bytes": os.path.getsize(input_path),
            })
        except Exception:
            pass

        if os.path.getsize(input_path) <= 0:
            return JsonResponse({
                "ok": False,
                "error": "DXF 파일 크기가 0입니다.",
            }, status=400)

        converter = _cblcad_find_oda_for_dxf_to_dwg_v1()

        if not converter:
            return JsonResponse({
                "ok": False,
                "error": "ODA File Converter 실행파일을 찾지 못했습니다.",
            }, status=500)

        dwg_path, attempts = _cblcad_run_oda_dxf_to_dwg_v1(converter, input_dir, output_dir)

        if not dwg_path or not os.path.exists(dwg_path):
            return JsonResponse({
                "ok": False,
                "error": "DXF -> DWG 변환 실패",
                "converter": converter,
                "attempts": attempts[-3:],
            }, status=500)

        with open(dwg_path, "rb") as f:
            data = f.read()

        if not data:
            return JsonResponse({
                "ok": False,
                "error": "생성된 DWG가 비어 있습니다.",
            }, status=500)

        res = HttpResponse(data, content_type="application/acad")
        res["Content-Disposition"] = 'attachment; filename="drawing.dwg"'
        res["X-CBL-Oda-Converter"] = converter
        try:
            res["X-CBL-DXF-INPUT-SOURCE"] = dxf_input_source_v22_2
            res["X-CBL-V22-2"] = "raw_merge_text_priority_utf8"
            res["Access-Control-Expose-Headers"] = (
                str(res.get("Access-Control-Expose-Headers", ""))
                + ", X-CBL-DXF-INPUT-SOURCE, X-CBL-V22-2"
            ).strip(", ")
        except Exception:
            pass
        return res


# CBLCAD_DXF_TO_DWG_SAVE_API_V1_END
# ============================================================

# CBLCAD_ODA_HIDE_WINDOW_SAFE_V2_START
# ChickenBananaCAD ODA File Converter 창 숨김 안전 패치 V2
# - 파일 맨 아래에서 subprocess.Popen만 감싼다.
# - 기존 변환 함수 내부 코드는 건드리지 않는다.
# - macOS에서는 ODA 프로세스를 발견하면 System Events로 숨김 시도한다.
# - Windows에서는 콘솔창 숨김 옵션을 시도한다.
def _cbl_install_oda_window_hide_safe_v2():
    try:
        import os
        import sys
        import time
        import threading
        import subprocess

        if getattr(subprocess, "_cbl_oda_hide_safe_v2_installed", False):
            return

        original_popen = subprocess.Popen
        subprocess._cbl_oda_hide_safe_v2_original_popen = original_popen
        subprocess._cbl_oda_hide_safe_v2_installed = True

        def is_oda_command(args):
            try:
                if isinstance(args, (list, tuple)):
                    cmd_text = " ".join(str(x) for x in args)
                else:
                    cmd_text = str(args)

                low = cmd_text.lower()

                return (
                    "odafileconverter" in low or
                    "oda file converter" in low or
                    "oda_file_converter" in low or
                    "oda" in low and "fileconverter" in low
                )
            except Exception:
                return False

        def hide_oda_macos_repeated():
            def worker():
                scripts = [
                    [
                        "osascript",
                        "-e", 'tell application "System Events"',
                        "-e", 'set odaProcs to every process whose name contains "ODA"',
                        "-e", 'repeat with p in odaProcs',
                        "-e", 'set visible of p to false',
                        "-e", 'end repeat',
                        "-e", 'end tell',
                    ],
                    [
                        "osascript",
                        "-e", 'tell application "System Events"',
                        "-e", 'if exists process "ODAFileConverter" then set visible of process "ODAFileConverter" to false',
                        "-e", 'end tell',
                    ],
                    [
                        "osascript",
                        "-e", 'tell application "System Events"',
                        "-e", 'if exists process "ODA File Converter" then set visible of process "ODA File Converter" to false',
                        "-e", 'end tell',
                    ],
                ]

                for _ in range(30):
                    for cmd in scripts:
                        try:
                            p = original_popen(
                                cmd,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            try:
                                p.communicate(timeout=1)
                            except Exception:
                                pass
                        except Exception:
                            pass

                    time.sleep(0.12)

            try:
                t = threading.Thread(target=worker, daemon=True)
                t.start()
            except Exception:
                pass

        def wrapped_popen(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args")
            oda = is_oda_command(cmd)

            if oda:
                try:
                    # macOS: open 명령으로 ODA 앱을 여는 경우 -j 옵션으로 숨김 실행 시도
                    if sys.platform == "darwin":
                        if isinstance(cmd, (list, tuple)) and len(cmd) > 0:
                            cmd_list = list(cmd)
                            if str(cmd_list[0]).endswith("/open") or cmd_list[0] == "open":
                                if "-j" not in cmd_list:
                                    cmd_list.insert(1, "-j")
                                    if args:
                                        args = (cmd_list,) + args[1:]
                                    else:
                                        kwargs["args"] = cmd_list

                        hide_oda_macos_repeated()

                    # Windows: 콘솔창 숨김
                    if os.name == "nt":
                        startupinfo = kwargs.get("startupinfo")
                        if startupinfo is None:
                            startupinfo = subprocess.STARTUPINFO()

                        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                        startupinfo.wShowWindow = 0
                        kwargs["startupinfo"] = startupinfo

                        if hasattr(subprocess, "CREATE_NO_WINDOW"):
                            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW

                except Exception:
                    pass

            proc = original_popen(*args, **kwargs)

            if oda and sys.platform == "darwin":
                hide_oda_macos_repeated()

            return proc

        subprocess.Popen = wrapped_popen

    except Exception:
        pass


_cbl_install_oda_window_hide_safe_v2()
# CBLCAD_ODA_HIDE_WINDOW_SAFE_V2_END


# ============================================================
# CBL CAD DWG BEST DXF SELECTOR V1 START
# 목적:
# - DWG를 브라우저가 직접 읽는 것이 아니라,
#   DWG -> ODAFileConverter -> DXF -> parseDXF 순서이므로
#   ODA DXF 변환 품질을 여러 버전으로 비교해 최고 DXF를 선택한다.
# - 프론트 버튼 / 블록 UI / 저장 기능은 건드리지 않는다.
# ============================================================

import os as _cbl_os
import re as _cbl_re
import json as _cbl_json
import glob as _cbl_glob
import shutil as _cbl_shutil
import tempfile as _cbl_tempfile
import subprocess as _cbl_subprocess
from pathlib import Path as _cbl_Path
from collections import Counter as _cbl_Counter

from django.http import HttpResponse as _cbl_HttpResponse
from django.http import JsonResponse as _cbl_JsonResponse
from django.views.decorators.csrf import csrf_exempt as _cbl_csrf_exempt


_CBL_DWG_DXF_VERSIONS_V1 = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP

# CBL_ODA_FAST_OPEN_V2
# 일반 DWG 열기에서는 ODAFileConverter.app이 계속 깜빡이지 않도록
# 우선순위 버전만 시도하고, 첫 사용 가능 DXF가 나오면 바로 멈춘다.
_CBL_DWG_DXF_FAST_VERSIONS_V2 = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP


_CBL_DXF_TRACK_ENTITY_TYPES_V1 = [
    "INSERT",
    "LINE",
    "LWPOLYLINE",
    "POLYLINE",
    "TEXT",
    "MTEXT",
    "DIMENSION",
    "HATCH",
    "ELLIPSE",
    "SPLINE",
    "ARC",
    "CIRCLE",
    "SOLID",
    "3DFACE",
    "LEADER",
    "MULTILEADER",
    "ATTDEF",
    "ATTRIB",
]


def _cbl_tail_v1(value, limit=3000):
    value = value or ""
    value = str(value)
    if len(value) <= limit:
        return value
    return value[-limit:]


def _cbl_find_oda_converter_v1():
    env_path = _cbl_os.environ.get("CBL_ODA_CONVERTER", "").strip()

    candidates = []
    if env_path:
        candidates.append(env_path)

    candidates.extend([
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
        "/usr/local/bin/ODAFileConverter",
        "/opt/homebrew/bin/ODAFileConverter",
    ])

    candidates.extend(_cbl_glob.glob("/Applications/*ODA*File*Converter*.app/Contents/MacOS/ODAFileConverter"))
    candidates.extend(_cbl_glob.glob("/Applications/*ODA*.app/Contents/MacOS/ODAFileConverter"))

    seen = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if _cbl_os.path.exists(c) and _cbl_os.access(c, _cbl_os.X_OK):
            return c

    return None


def _cbl_normalize_dxf_text_v1(raw_bytes):
    text, _info = _cbl_decode_dxf_text_korean_direct_v3(raw_bytes)
    return text

def _cbl_analyze_dxf_text_v1(dxf_text, dxf_size=0, target_block="XR-FORM-A(A3)"):
    """
    DXF 텍스트에서 정보량을 계산한다.
    원본에 없는 엔티티를 생성하지 않는다.
    단순히 변환 결과 후보 중 어떤 DXF가 더 많은 정보를 보존했는지만 판단한다.
    """
    target_block_norm = (target_block or "").strip().upper()

    lines = dxf_text.splitlines()
    entity_counts = _cbl_Counter()
    block_entity_counts = _cbl_Counter()

    current_section = None
    pending_section_name = False

    in_block = False
    current_block_name = None
    block_count = 0

    i = 0
    n = len(lines)

    while i + 1 < n:
        code = lines[i].strip()
        value = lines[i + 1].strip()
        i += 2

        if code == "0":
            upper_value = value.upper()

            if upper_value == "SECTION":
                pending_section_name = True
                continue

            if upper_value == "ENDSEC":
                current_section = None
                pending_section_name = False
                in_block = False
                current_block_name = None
                continue

            if current_section == "BLOCKS":
                if upper_value == "BLOCK":
                    block_count += 1
                    in_block = True
                    current_block_name = None
                    continue

                if upper_value == "ENDBLK":
                    in_block = False
                    current_block_name = None
                    continue

                if in_block and current_block_name:
                    # BLOCK/ENDBLK 자체가 아니라 블록 내부 엔티티만 카운트
                    block_entity_counts[current_block_name.upper()] += 1

            elif current_section == "ENTITIES":
                # 실제 모델/도면 공간 엔티티 카운트
                if upper_value not in {"SEQEND"}:
                    entity_counts[upper_value] += 1

        elif code == "2":
            if pending_section_name:
                current_section = value.upper()
                pending_section_name = False
                continue

            if current_section == "BLOCKS" and in_block and not current_block_name:
                current_block_name = value.strip()
                continue

    tracked = {k: int(entity_counts.get(k, 0)) for k in _CBL_DXF_TRACK_ENTITY_TYPES_V1}

    text_mtext_count = tracked.get("TEXT", 0) + tracked.get("MTEXT", 0)
    curve_count = (
        tracked.get("ELLIPSE", 0)
        + tracked.get("SPLINE", 0)
        + tracked.get("ARC", 0)
        + tracked.get("CIRCLE", 0)
    )

    target_block_entity_count = int(block_entity_counts.get(target_block_norm, 0)) if target_block_norm else 0

    total_entities = int(sum(entity_counts.values()))

    # 점수화 기준:
    # - 파일 크기는 보조 지표
    # - BLOCK/INSERT/TEXT/DIM/HATCH/곡선/도곽 블록 내부 엔티티를 더 강하게 본다
    # - 특정 도곽 블록 XR-FORM-A(A3)가 살아있으면 큰 가중치
    score_components = {
        "size": min(int(dxf_size), 80 * 1024 * 1024) / 1024.0 * 0.03,
        "block": block_count * 30,
        "insert": tracked.get("INSERT", 0) * 20,
        "line": tracked.get("LINE", 0) * 2,
        "lwpolyline": tracked.get("LWPOLYLINE", 0) * 5,
        "polyline": tracked.get("POLYLINE", 0) * 5,
        "text_mtext": text_mtext_count * 10,
        "dimension": tracked.get("DIMENSION", 0) * 15,
        "hatch": tracked.get("HATCH", 0) * 12,
        "curve": curve_count * 3,
        "target_block": target_block_entity_count * 200,
    }

    score = round(sum(score_components.values()), 3)

    top_entity_counts = dict(entity_counts.most_common(40))

    return {
        "score": score,
        "score_components": score_components,
        "dxf_size": int(dxf_size),
        "block_count": int(block_count),
        "insert_count": int(tracked.get("INSERT", 0)),
        "total_entities": total_entities,
        "entity_counts": tracked,
        "top_entity_counts": top_entity_counts,
        "target_block": target_block,
        "target_block_entity_count": target_block_entity_count,
        "XR-FORM-A(A3)_entityCount": target_block_entity_count,
    }


def _cbl_collect_dxf_files_v1(*dirs):
    found = []
    for d in dirs:
        if not d:
            continue
        p = _cbl_Path(d)
        if not p.exists():
            continue
        for child in p.rglob("*"):
            if child.is_file() and child.suffix.lower() == ".dxf":
                try:
                    found.append(child)
                except Exception:
                    pass
    return found


def _cbl_run_oda_to_dxf_version_v1(converter, src_dwg_path, version, work_root, target_block):
    src_dwg_path = _cbl_Path(src_dwg_path)
    in_dir = src_dwg_path.parent
    out_dir = _cbl_Path(work_root) / f"out_{version}"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        converter,
        str(in_dir),
        str(out_dir),
        version,
        "DXF",
        "0",
        "1",
        "*.dwg",
    ]

    attempt = {
        "version": version,
        "cmd": cmd,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "found": [],
        "selected_candidate": None,
        "analysis": None,
        "score": -1,
    }

    try:
        proc = _cbl_subprocess.run(
            cmd,
            cwd=str(work_root),
            text=True,
            capture_output=True,
            timeout=120,
        )
        attempt["returncode"] = proc.returncode
        attempt["stdout"] = _cbl_tail_v1(proc.stdout)
        attempt["stderr"] = _cbl_tail_v1(proc.stderr)
    except Exception as e:
        attempt["error"] = str(e)
        return attempt, None, None

    dxf_files = _cbl_collect_dxf_files_v1(out_dir)
    attempt["found"] = [
        {
            "path": str(p),
            "size": p.stat().st_size if p.exists() else 0,
        }
        for p in dxf_files
    ]

    best_text = None
    best_path = None
    best_analysis = None

    for dxf_path in dxf_files:
        try:
            raw = dxf_path.read_bytes()
            dxf_text = _cbl_normalize_dxf_text_v1(raw)
            analysis = _cbl_analyze_dxf_text_v1(
                dxf_text,
                dxf_size=len(raw),
                target_block=target_block,
            )
            if best_analysis is None or analysis["score"] > best_analysis["score"]:
                best_analysis = analysis
                best_text = dxf_text
                best_path = dxf_path
        except Exception as e:
            attempt.setdefault("candidate_errors", []).append({
                "path": str(dxf_path),
                "error": str(e),
            })

    if best_analysis:
        attempt["selected_candidate"] = str(best_path)
        attempt["analysis"] = best_analysis
        attempt["score"] = best_analysis["score"]

    return attempt, best_text, best_path


def _cbl_convert_dwg_to_best_dxf_v1(src_dwg_path, target_block="XR-FORM-A(A3)", mode="fast", versions=None):
    converter = _cbl_find_oda_converter_v1()

    result = {
        "ok": False,
        "converter": converter,
        "selected_version": None,
        "score": -1,
        "dxf_size": 0,
        "block_count": 0,
        "insert_count": 0,
        "entity_counts": {},
        "attempts": [],
        "target_block": target_block,
        "target_block_entityCount": 0,
    }

    if not converter:
        result["error"] = "ODAFileConverter 실행 파일을 찾지 못했습니다."
        return result, None

    best_text = None
    best_attempt = None

    work_root = _cbl_Path(src_dwg_path).parent

    mode = (mode or "fast").lower().strip()

    if versions is not None:
        versions_to_try = list(versions)
    elif mode in {"best", "full", "scan", "debug"}:
        versions_to_try = list(_CBL_DWG_DXF_VERSIONS_V1)
    else:
        versions_to_try = list(_CBL_DWG_DXF_FAST_VERSIONS_V2)

    result["mode"] = mode
    result["versions_requested"] = versions_to_try

    # fast 모드에서는 ODA 깜빡임을 줄이기 위해 첫 사용 가능 DXF에서 중단
    stop_after_first_usable = mode not in {"best", "full", "scan", "debug"}

    for version in versions_to_try:
        attempt, dxf_text, dxf_path = _cbl_run_oda_to_dxf_version_v1(
            converter=converter,
            src_dwg_path=src_dwg_path,
            version=version,
            work_root=work_root,
            target_block=target_block,
        )
        result["attempts"].append(attempt)

        if dxf_text and attempt.get("analysis"):
            if best_attempt is None or attempt["analysis"]["score"] > best_attempt["analysis"]["score"]:
                best_attempt = attempt
                best_text = dxf_text

            if stop_after_first_usable:
                result["fast_stop_reason"] = "first usable DXF selected to reduce repeated ODAFileConverter launches"
                break

    if not best_attempt or not best_text:
        result["error"] = "여러 DXF 버전 변환을 시도했지만 사용 가능한 DXF를 찾지 못했습니다."
        return result, None

    analysis = best_attempt["analysis"]

    result.update({
        "ok": True,
        "selected_version": best_attempt["version"],
        "score": analysis["score"],
        "dxf_size": analysis["dxf_size"],
        "block_count": analysis["block_count"],
        "insert_count": analysis["insert_count"],
        "entity_counts": analysis["entity_counts"],
        "top_entity_counts": analysis["top_entity_counts"],
        "total_entities": analysis["total_entities"],
        "score_components": analysis["score_components"],
        "target_block_entityCount": analysis["target_block_entity_count"],
        "XR-FORM-A(A3)_entityCount": analysis["XR-FORM-A(A3)_entityCount"],
    })

    return result, best_text


@_cbl_csrf_exempt
def cblcad_dwg_to_best_dxf_api(request):
    """
    DWG 열기용 백엔드 API.
    응답에는 프론트 호환성을 위해 dxf / dxf_text / text / content 키를 모두 넣는다.
    debug_only=1 로 호출하면 DXF 본문 없이 분석 정보만 반환한다.
    """
    if request.method == "GET":
        return _cbl_JsonResponse({
            "ok": True,
            "endpoint": "cblcad_dwg_to_best_dxf_api",
            "versions": _CBL_DWG_DXF_VERSIONS_V1,
            "message": "POST multipart/form-data file=@도면.dwg 로 호출하세요.",
        }, json_dumps_params={"ensure_ascii": False})

    if request.method != "POST":
        return _cbl_JsonResponse({
            "ok": False,
            "error": "POST 요청만 지원합니다.",
        }, status=405, json_dumps_params={"ensure_ascii": False})

    upload = None
    for key in ("file", "dwg", "dwg_file", "dwgFile", "upload"):
        if key in request.FILES:
            upload = request.FILES[key]
            break

    if upload is None and request.FILES:
        upload = list(request.FILES.values())[0]

    if upload is None:
        return _cbl_JsonResponse({
            "ok": False,
            "error": "DWG 파일이 업로드되지 않았습니다. file 필드로 업로드하세요.",
        }, status=400, json_dumps_params={"ensure_ascii": False})

    original_name = getattr(upload, "name", "upload.dwg") or "upload.dwg"
    safe_name = _cbl_re.sub(r"[^0-9A-Za-z가-힣_.() -]+", "_", original_name)
    if not safe_name.lower().endswith(".dwg"):
        safe_name += ".dwg"

    target_block = request.POST.get("target_block") or request.GET.get("target_block") or "XR-FORM-A(A3)"
    debug_only = str(request.GET.get("debug_only") or request.POST.get("debug_only") or "").lower() in {"1", "true", "yes", "y"}

    # CBL_ODA_FAST_OPEN_V2
    # 기본값 fast: 일반 열기는 ODA 1회 성공 시 중단
    # best=1 또는 full=1 또는 mode=best/full일 때만 7버전 전체 비교
    mode_raw = str(
        request.GET.get("mode")
        or request.POST.get("mode")
        or ""
    ).lower().strip()

    best_flag = str(
        request.GET.get("best")
        or request.POST.get("best")
        or request.GET.get("full")
        or request.POST.get("full")
        or ""
    ).lower().strip() in {"1", "true", "yes", "y"}

    cbl_oda_mode_v2 = "best" if best_flag or mode_raw in {"best", "full", "scan", "debug"} else "fast"

    with _cbl_tempfile.TemporaryDirectory(prefix="cblcad_dwg_best_dxf_") as tmp:
        tmp_path = _cbl_Path(tmp)
        input_dir = tmp_path / "input"
        input_dir.mkdir(parents=True, exist_ok=True)

        dwg_path = input_dir / safe_name

        with dwg_path.open("wb") as f:
            for chunk in upload.chunks():
                f.write(chunk)

        result, dxf_text = _cbl_convert_dwg_to_best_dxf_v1(
            src_dwg_path=dwg_path,
            target_block=target_block,
            mode=cbl_oda_mode_v2,
        )

    status = 200 if result.get("ok") else 500

    payload = {
        **result,
        "filename": original_name,
    }

    # CBL_DWG_DXF_ATTEMPTS_COMPACT_V1
    # 일반 응답에서는 ODA 실행 상세 stdout/stderr/cmd 전체를 줄이고
    # 화면에서 필요한 디버그 요약만 유지한다.
    def _cbl_compact_attempts_for_response_v1(attempts):
        compact = []
        for a in attempts or []:
            analysis = a.get("analysis") or {}
            compact.append({
                "version": a.get("version"),
                "returncode": a.get("returncode"),
                "score": a.get("score"),
                "found_count": len(a.get("found") or []),
                "selected_candidate": bool(a.get("selected_candidate")),
                "dxf_size": analysis.get("dxf_size"),
                "block_count": analysis.get("block_count"),
                "insert_count": analysis.get("insert_count"),
                "entity_counts": analysis.get("entity_counts"),
                "target_block_entityCount": analysis.get("target_block_entity_count"),
            })
        return compact

    if isinstance(payload.get("attempts"), list):
        payload["attempts"] = _cbl_compact_attempts_for_response_v1(payload.get("attempts"))

    if result.get("ok") and dxf_text is not None:
        if debug_only:
            payload["dxf_preview"] = dxf_text[:1000]
            payload["dxf_omitted"] = True
        else:
            # CBL_DWG_DXF_RESPONSE_LIGHT_V1
            # DXF 본문은 매우 클 수 있으므로 1번만 보낸다.
            # dxf_text/text/content 중복 전송은 브라우저 메모리를 크게 잡아먹는다.
            payload["dxf"] = dxf_text
            payload["dxf_payload_mode"] = "single_dxf_only"

    return _cbl_HttpResponse(
        _cbl_json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        status=status,
        content_type="application/json; charset=utf-8",
    )

# CBL CAD DWG BEST DXF SELECTOR V1 END
# ============================================================

# CBL_DWG_SAVE_CSRF_FIX_V1
# 정적 CAD HTML에서 DWG 저장 POST 전 CSRF 쿠키를 발급받기 위한 endpoint.
from django.http import JsonResponse as CBLJsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie as cbl_ensure_csrf_cookie

@cbl_ensure_csrf_cookie
def cblcad_csrf(request):
    return CBLJsonResponse({"ok": True})

# CBL_ODA_DWG_OUTPUT_FIX_GRID_OFF_V1
# DWG 저장용 DXF -> DWG 변환 안정화 view
# 핵심:
# - ODAFileConverter는 파일이 아니라 폴더 단위로 변환함
# - 한글 파일명 대신 cbl_input.dxf 영문 임시명 사용
# - 출력 폴더 전체를 재귀 탐색해서 DWG 결과물 검색
# - 실패 시 상세 attempts 반환해서 원인 추적 가능
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None

    # 1) multipart file
    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )
        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name
    except Exception:
        pass

    # 2) JSON body
    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)
                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )
                dxf_text = (
                    data.get("dxf")
                    or data.get("dxfText")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    # 3) form body
    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )
            dxf_text = (
                request.POST.get("dxf")
                or request.POST.get("dxfText")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )
            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    # 4) raw body fallback
    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({
            "ok": False,
            "error": "DXF 데이터가 비어 있습니다."
        }, status=400)

    # 파일명은 다운로드용만 유지. ODA 입력명은 영문 고정.
    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"

    attempts = []

    try:
        # DXF 텍스트 줄바꿈 안정화.
        # 너무 강하게 바꾸면 기존 DXF가 깨질 수 있으니 LF만 있는 경우에만 CRLF로 보정.
        if b"\r\n" not in dxf_bytes and b"\n" in dxf_bytes:
            dxf_bytes = dxf_bytes.replace(b"\n", b"\r\n")

        input_dxf.write_bytes(dxf_bytes)

        # 최근 포맷부터 시도. ACAD2004는 너무 오래되어 한글/MTEXT/치수 손상 가능성이 커서 제외.
        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP

        for version in versions:
            # 출력 폴더 초기화
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                found = []
                for p in output_dir.rglob("*"):
                    if p.is_file() and p.suffix.lower() == ".dwg":
                        found.append(str(p))

                # 혹시 ODA가 input/temp 쪽에 떨어뜨리는 경우까지 방어
                if not found:
                    for p in tmp_root.rglob("*"):
                        if p.is_file() and p.suffix.lower() == ".dwg":
                            found.append(str(p))

                attempts.append({
                    "version": version,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-2000:] if proc.stdout else "",
                    "stderr": proc.stderr[-2000:] if proc.stderr else "",
                    "found": found,
                    "input_exists": input_dxf.exists(),
                    "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                    "output_dir": str(output_dir),
                    "output_listing": [
                        str(x.relative_to(tmp_root))
                        for x in tmp_root.rglob("*")
                        if x.is_file()
                    ][:50],
                })

                if found:
                    # 가장 최근/큰 파일 우선
                    found_paths = [Path(x) for x in found]
                    found_paths.sort(key=lambda p: (p.stat().st_size, p.stat().st_mtime), reverse=True)
                    dwg_path = found_paths[0]
                    dwg_bytes = dwg_path.read_bytes()

                    if len(dwg_bytes) < 100:
                        continue

                    response = HttpResponse(dwg_bytes, content_type="application/octet-stream")
                    quoted = urllib.parse.quote(filename)
                    response["Content-Disposition"] = (
                        "attachment; "
                        f"filename=\"{filename.encode('ascii', 'ignore').decode() or 'drawing.dwg'}\"; "
                        f"filename*=UTF-8''{quoted}"
                    )
                    response["X-CBL-DWG-Version"] = version
                    response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                    return response

            except subprocess.TimeoutExpired as e:
                attempts.append({
                    "version": version,
                    "returncode": "timeout",
                    "stdout": "",
                    "stderr": str(e),
                    "found": [],
                })
            except Exception as e:
                attempts.append({
                    "version": version,
                    "returncode": "exception",
                    "stdout": "",
                    "stderr": repr(e),
                    "found": [],
                })

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "converter": converter,
            "attempts": attempts,
        }, status=500)

    finally:
        # 디버깅 중 임시폴더를 남기고 싶으면 아래 줄 주석 처리.
        shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_ODA_ERR_DEBUG_GRID_V2
# 목적:
# - ODA가 생성하는 *.dwg.err 내용을 직접 읽어서 반환
# - 실패한 DXF/ERR 파일을 _cblcad_oda_debug 폴더에 보관
# - ODA 깜빡임 줄이기 위해 시도 버전 축소
# - 렌더링/파서/한글/치수/블록 로직은 건드리지 않음
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None

    # 1) multipart
    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )
        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name
    except Exception:
        pass

    # 2) JSON
    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)
                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )
                dxf_text = (
                    data.get("dxf")
                    or data.get("dxfText")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    # 3) form
    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )
            dxf_text = (
                request.POST.get("dxf")
                or request.POST.get("dxfText")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )
            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    # 4) raw body
    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({
            "ok": False,
            "error": "DXF 데이터가 비어 있습니다."
        }, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    def cbl_prepare_dxf_bytes(raw):
        if raw is None:
            return b""

        # UTF-8 BOM 제거
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]

        # ODA가 싫어하는 NUL 제거
        raw = raw.replace(b"\x00", b"")

        # 줄바꿈 정리: CRLF 통일
        raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        raw = raw.replace(b"\n", b"\r\n")

        # 마지막 EOF 보강
        tail = raw[-200:].upper()
        if b"\r\nEOF" not in tail and b"\nEOF" not in tail:
            if not raw.endswith(b"\r\n"):
                raw += b"\r\n"
            raw += b"0\r\nEOF\r\n"

        return raw

    def cbl_read_text_file_limited(path, limit=8000):
        try:
            data = Path(path).read_bytes()
            if len(data) > limit:
                data = data[:limit] + b"\n\n--- TRUNCATED ---\n"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return "ERR_READ_FAILED: " + repr(e)

    def cbl_collect_listing(root):
        items = []
        try:
            root = Path(root)
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        items.append({
                            "path": str(p.relative_to(root)),
                            "size": p.stat().st_size,
                        })
                    except Exception:
                        items.append({
                            "path": str(p),
                            "size": None,
                        })
        except Exception:
            pass
        return items[:100]

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"
    attempts = []
    success = False

    try:
        dxf_bytes = cbl_prepare_dxf_bytes(dxf_bytes)
        input_dxf.write_bytes(dxf_bytes)

        # ODA 깜빡임 줄이기: 우선 2개만 시도
        # input 파싱 실패면 버전을 많이 바꿔도 대부분 동일하게 실패함.
        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23

        for version in versions:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                found_dwgs = []
                for p in tmp_root.rglob("*"):
                    if p.is_file() and p.suffix.lower() == ".dwg":
                        found_dwgs.append(str(p))

                err_files = []
                for p in tmp_root.rglob("*.err"):
                    err_files.append({
                        "path": str(p.relative_to(tmp_root)),
                        "size": p.stat().st_size,
                        "text": cbl_read_text_file_limited(p),
                    })

                attempts.append({
                    "version": version,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-2000:] if proc.stdout else "",
                    "stderr": proc.stderr[-2000:] if proc.stderr else "",
                    "found": found_dwgs,
                    "err_files": err_files,
                    "input_exists": input_dxf.exists(),
                    "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                    "output_dir": str(output_dir),
                    "output_listing": cbl_collect_listing(tmp_root),
                })

                if found_dwgs:
                    found_paths = [Path(x) for x in found_dwgs]
                    found_paths.sort(
                        key=lambda p: (p.stat().st_size, p.stat().st_mtime),
                        reverse=True
                    )

                    dwg_path = found_paths[0]
                    dwg_bytes = dwg_path.read_bytes()

                    if len(dwg_bytes) >= 100:
                        success = True
                        response = HttpResponse(
                            dwg_bytes,
                            content_type="application/octet-stream"
                        )
                        quoted = urllib.parse.quote(filename)
                        ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
                        response["Content-Disposition"] = (
                            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
                        )
                        response["X-CBL-DWG-Version"] = version
                        response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                        return response

            except subprocess.TimeoutExpired as e:
                attempts.append({
                    "version": version,
                    "returncode": "timeout",
                    "stdout": "",
                    "stderr": str(e),
                    "found": [],
                    "err_files": [],
                    "output_listing": cbl_collect_listing(tmp_root),
                })
            except Exception as e:
                attempts.append({
                    "version": version,
                    "returncode": "exception",
                    "stdout": "",
                    "stderr": repr(e),
                    "found": [],
                    "err_files": [],
                    "output_listing": cbl_collect_listing(tmp_root),
                })

        # 실패 디버그 폴더 보관
        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir_path = debug_root / f"fail_{stamp}"
            shutil.copytree(tmp_root, debug_dir_path, dirs_exist_ok=True)
            debug_dir = str(debug_dir_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "reason": "ODA가 DWG 대신 .dwg.err 파일을 생성했습니다. err_files.text 내용을 확인해야 합니다.",
            "converter": converter,
            "debug_dir": debug_dir,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            # 실패 시에는 debug_dir에 복사했으므로 원본 임시폴더는 삭제
            shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_ODA_DXF_TABLE_SANITIZER_V3
# 목적:
# - ODA 변환 직전에만 저장용 DXF TABLES 정리
# - 빈 LTYPE 이름 / 빈 LAYER 이름 / 빈 LAYER 선종류 참조 보정
# - 화면 렌더링, 파서, 한글, 치수, 블록 로직은 건드리지 않음
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None

    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )
        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name
    except Exception:
        pass

    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)
                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )
                dxf_text = (
                    data.get("dxf")
                    or data.get("dxfText")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )
            dxf_text = (
                request.POST.get("dxf")
                or request.POST.get("dxfText")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )
            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({
            "ok": False,
            "error": "DXF 데이터가 비어 있습니다."
        }, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    def cbl_pairs_from_dxf_text(text):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")

        while lines and lines[-1] == "":
            lines.pop()

        pairs = []
        i = 0
        while i < len(lines):
            code = lines[i].strip()
            value = lines[i + 1] if i + 1 < len(lines) else ""
            pairs.append([code, value])
            i += 2
        return pairs

    def cbl_pairs_to_bytes(pairs):
        out = []
        for code, value in pairs:
            out.append(str(code).strip())
            out.append("" if value is None else str(value))
        text = "\r\n".join(out)

        upper_tail = text[-300:].upper()
        if "\r\n0\r\nEOF" not in upper_tail and "\n0\nEOF" not in upper_tail:
            if not text.endswith("\r\n"):
                text += "\r\n"
            text += "0\r\nEOF\r\n"
        else:
            if not text.endswith("\r\n"):
                text += "\r\n"

        return text.encode("utf-8", errors="replace")

    def cbl_find_next_record(pairs, start):
        j = start + 1
        while j < len(pairs):
            if pairs[j][0].strip() == "0":
                return j
            j += 1
        return len(pairs)

    def cbl_find_group(pairs, start, end, group_code):
        group_code = str(group_code)
        for k in range(start + 1, end):
            if pairs[k][0].strip() == group_code:
                return k
        return None

    def cbl_ensure_continuous_ltype(pairs):
        found_ltype_table = False
        continuous_exists = False

        i = 0
        while i < len(pairs):
            if pairs[i][0].strip() == "0" and pairs[i][1].strip().upper() == "TABLE":
                j = i + 1
                table_name = ""
                if j < len(pairs) and pairs[j][0].strip() == "2":
                    table_name = pairs[j][1].strip().upper()

                if table_name == "LTYPE":
                    found_ltype_table = True
                    k = j + 1
                    endtab = None

                    while k < len(pairs):
                        if pairs[k][0].strip() == "0" and pairs[k][1].strip().upper() == "ENDTAB":
                            endtab = k
                            break

                        if pairs[k][0].strip() == "0" and pairs[k][1].strip().upper() == "LTYPE":
                            rec_end = cbl_find_next_record(pairs, k)
                            idx2 = cbl_find_group(pairs, k, rec_end, "2")
                            if idx2 is not None and pairs[idx2][1].strip().lower() == "continuous":
                                continuous_exists = True

                        k += 1

                    if endtab is not None and not continuous_exists:
                        continuous_record = [
                            ["0", "LTYPE"],
                            ["100", "AcDbSymbolTableRecord"],
                            ["100", "AcDbLinetypeTableRecord"],
                            ["2", "Continuous"],
                            ["70", "0"],
                            ["3", "Solid line"],
                            ["72", "65"],
                            ["73", "0"],
                            ["40", "0.0"],
                        ]
                        pairs[endtab:endtab] = continuous_record
                        continuous_exists = True
                    break
            i += 1

        return pairs, found_ltype_table, continuous_exists

    def cbl_sanitize_dxf_for_oda(raw):
        report = {
            "empty_ltype_names_fixed": 0,
            "empty_layer_names_fixed": 0,
            "empty_layer_linetype_fixed": 0,
            "invalid_layer_linetype_fixed": 0,
            "continuous_ltype_exists": False,
            "valid_ltypes_count": 0,
        }

        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]

        raw = raw.replace(b"\x00", b"")

        text = raw.decode("utf-8", errors="replace")
        pairs = cbl_pairs_from_dxf_text(text)

        # 1) Continuous 선종류 확보
        pairs, _, continuous_exists = cbl_ensure_continuous_ltype(pairs)
        report["continuous_ltype_exists"] = bool(continuous_exists)

        # 2) 빈 LTYPE 이름 보정
        empty_ltype_no = 1
        i = 0
        while i < len(pairs):
            if pairs[i][0].strip() == "0" and pairs[i][1].strip().upper() == "LTYPE":
                rec_end = cbl_find_next_record(pairs, i)
                idx2 = cbl_find_group(pairs, i, rec_end, "2")

                if idx2 is None:
                    pairs.insert(i + 1, ["2", f"CBL_EMPTY_LTYPE_{empty_ltype_no}"])
                    report["empty_ltype_names_fixed"] += 1
                    empty_ltype_no += 1
                    rec_end += 1
                elif not pairs[idx2][1].strip():
                    pairs[idx2][1] = f"CBL_EMPTY_LTYPE_{empty_ltype_no}"
                    report["empty_ltype_names_fixed"] += 1
                    empty_ltype_no += 1

                i = rec_end
            else:
                i += 1

        # 3) 유효 LTYPE 목록 수집
        valid_ltypes = set()
        i = 0
        while i < len(pairs):
            if pairs[i][0].strip() == "0" and pairs[i][1].strip().upper() == "LTYPE":
                rec_end = cbl_find_next_record(pairs, i)
                idx2 = cbl_find_group(pairs, i, rec_end, "2")
                if idx2 is not None:
                    name = pairs[idx2][1].strip()
                    if name:
                        valid_ltypes.add(name.lower())
                i = rec_end
            else:
                i += 1

        valid_ltypes.add("continuous")
        report["valid_ltypes_count"] = len(valid_ltypes)

        # 4) LAYER 이름과 LAYER 선종류 참조 보정
        empty_layer_no = 1
        i = 0
        while i < len(pairs):
            if pairs[i][0].strip() == "0" and pairs[i][1].strip().upper() == "LAYER":
                rec_end = cbl_find_next_record(pairs, i)

                idx2 = cbl_find_group(pairs, i, rec_end, "2")
                if idx2 is None:
                    pairs.insert(i + 1, ["2", f"CBL_EMPTY_LAYER_{empty_layer_no}"])
                    report["empty_layer_names_fixed"] += 1
                    empty_layer_no += 1
                    rec_end += 1
                elif not pairs[idx2][1].strip():
                    pairs[idx2][1] = f"CBL_EMPTY_LAYER_{empty_layer_no}"
                    report["empty_layer_names_fixed"] += 1
                    empty_layer_no += 1

                idx6 = cbl_find_group(pairs, i, rec_end, "6")
                if idx6 is None:
                    pairs.insert(rec_end, ["6", "Continuous"])
                    report["empty_layer_linetype_fixed"] += 1
                    rec_end += 1
                else:
                    lt = pairs[idx6][1].strip()
                    if not lt:
                        pairs[idx6][1] = "Continuous"
                        report["empty_layer_linetype_fixed"] += 1
                    elif lt.lower() not in valid_ltypes:
                        pairs[idx6][1] = "Continuous"
                        report["invalid_layer_linetype_fixed"] += 1

                i = rec_end
            else:
                i += 1

        return cbl_pairs_to_bytes(pairs), report

    def cbl_read_text_file_limited(path, limit=12000):
        try:
            data = Path(path).read_bytes()
            if len(data) > limit:
                data = data[:limit] + b"\n\n--- TRUNCATED ---\n"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return "ERR_READ_FAILED: " + repr(e)

    def cbl_collect_listing(root):
        items = []
        try:
            root = Path(root)
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        items.append({
                            "path": str(p.relative_to(root)),
                            "size": p.stat().st_size,
                        })
                    except Exception:
                        items.append({
                            "path": str(p),
                            "size": None,
                        })
        except Exception:
            pass
        return items[:100]

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"
    attempts = []
    success = False
    sanitize_report = {}

    try:
        dxf_bytes, sanitize_report = cbl_sanitize_dxf_for_oda(dxf_bytes)
        input_dxf.write_bytes(dxf_bytes)

        # 깜빡임 줄이기: 우선 ACAD2004 1회만 시도
        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23

        for version in versions:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                found_dwgs = []
                for p in tmp_root.rglob("*"):
                    if p.is_file() and p.suffix.lower() == ".dwg":
                        found_dwgs.append(str(p))

                err_files = []
                for p in tmp_root.rglob("*.err"):
                    err_files.append({
                        "path": str(p.relative_to(tmp_root)),
                        "size": p.stat().st_size,
                        "text": cbl_read_text_file_limited(p),
                    })

                attempts.append({
                    "version": version,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-3000:] if proc.stdout else "",
                    "stderr": proc.stderr[-3000:] if proc.stderr else "",
                    "found": found_dwgs,
                    "err_files": err_files,
                    "sanitize_report": sanitize_report,
                    "input_exists": input_dxf.exists(),
                    "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                    "output_dir": str(output_dir),
                    "output_listing": cbl_collect_listing(tmp_root),
                })

                if found_dwgs:
                    found_paths = [Path(x) for x in found_dwgs]
                    found_paths.sort(
                        key=lambda p: (p.stat().st_size, p.stat().st_mtime),
                        reverse=True
                    )

                    dwg_path = found_paths[0]
                    dwg_bytes = dwg_path.read_bytes()

                    if len(dwg_bytes) >= 100:
                        success = True
                        response = HttpResponse(
                            dwg_bytes,
                            content_type="application/octet-stream"
                        )
                        quoted = urllib.parse.quote(filename)
                        ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
                        response["Content-Disposition"] = (
                            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
                        )
                        response["X-CBL-DWG-Version"] = version
                        response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                        response["X-CBL-DXF-Sanitize"] = json.dumps(sanitize_report, ensure_ascii=False)
                        return response

            except subprocess.TimeoutExpired as e:
                attempts.append({
                    "version": version,
                    "returncode": "timeout",
                    "stdout": "",
                    "stderr": str(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": cbl_collect_listing(tmp_root),
                })
            except Exception as e:
                attempts.append({
                    "version": version,
                    "returncode": "exception",
                    "stdout": "",
                    "stderr": repr(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": cbl_collect_listing(tmp_root),
                })

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir_path = debug_root / f"fail_{stamp}"
            shutil.copytree(tmp_root, debug_dir_path, dirs_exist_ok=True)
            debug_dir = str(debug_dir_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "reason": "ODA 변환 직전 DXF TABLES Sanitizer 적용 후에도 실패했습니다. err_files.text를 확인하세요.",
            "converter": converter,
            "debug_dir": debug_dir,
            "sanitize_report": sanitize_report,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_ODA_LTYPE_TABLE_REBUILD_V4
# 목적:
# - ODA가 거부하는 빈 AcDbLinetypeTableRecord 문제 해결
# - DWG 변환 직전에만 LTYPE TABLE을 안전 기본값으로 통째 재작성
# - 기존 화면 렌더링/파서/한글/치수/블록 로직은 건드리지 않음
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None

    # multipart
    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )
        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name
    except Exception:
        pass

    # json
    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)
                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )
                dxf_text = (
                    data.get("dxf")
                    or data.get("dxfText")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    # form
    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )
            dxf_text = (
                request.POST.get("dxf")
                or request.POST.get("dxfText")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )
            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    # raw body
    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({
            "ok": False,
            "error": "DXF 데이터가 비어 있습니다."
        }, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    def normalize_name(value):
        if value is None:
            return ""
        s = str(value)
        s = s.replace("\ufeff", "")
        s = s.replace("\u200b", "")
        s = s.replace("\u200c", "")
        s = s.replace("\u200d", "")
        s = s.replace("\x00", "")
        return s.strip()

    def parse_pairs(text):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")

        while lines and lines[-1] == "":
            lines.pop()

        if len(lines) % 2 == 1:
            lines.append("")

        pairs = []
        for i in range(0, len(lines), 2):
            pairs.append([str(lines[i]).strip(), lines[i + 1]])
        return pairs

    def pairs_to_bytes(pairs):
        out = []
        for code, value in pairs:
            out.append(str(code).strip())
            out.append("" if value is None else str(value))

        text = "\r\n".join(out)

        tail = text[-400:].upper()
        if "\r\n0\r\nEOF" not in tail and "\n0\nEOF" not in tail:
            if not text.endswith("\r\n"):
                text += "\r\n"
            text += "0\r\nEOF\r\n"
        else:
            if not text.endswith("\r\n"):
                text += "\r\n"

        return text.encode("utf-8", errors="replace")

    def next_record(pairs, start):
        j = start + 1
        while j < len(pairs):
            if pairs[j][0].strip() == "0":
                return j
            j += 1
        return len(pairs)

    def find_group(pairs, start, end, code):
        code = str(code)
        for k in range(start + 1, end):
            if pairs[k][0].strip() == code:
                return k
        return None

    def safe_ltype_table():
        # handle/330 제거: 기존 도면 handle과 충돌 방지
        return [
            ["0", "TABLE"],
            ["2", "LTYPE"],
            ["70", "3"],

            ["0", "LTYPE"],
            ["100", "AcDbSymbolTableRecord"],
            ["100", "AcDbLinetypeTableRecord"],
            ["2", "ByBlock"],
            ["70", "0"],
            ["3", ""],
            ["72", "65"],
            ["73", "0"],
            ["40", "0.0"],

            ["0", "LTYPE"],
            ["100", "AcDbSymbolTableRecord"],
            ["100", "AcDbLinetypeTableRecord"],
            ["2", "ByLayer"],
            ["70", "0"],
            ["3", ""],
            ["72", "65"],
            ["73", "0"],
            ["40", "0.0"],

            ["0", "LTYPE"],
            ["100", "AcDbSymbolTableRecord"],
            ["100", "AcDbLinetypeTableRecord"],
            ["2", "Continuous"],
            ["70", "0"],
            ["3", "Solid line"],
            ["72", "65"],
            ["73", "0"],
            ["40", "0.0"],

            ["0", "ENDTAB"],
        ]

    def rebuild_ltype_table(pairs, report):
        i = 0
        replaced = False

        while i < len(pairs):
            if (
                pairs[i][0].strip() == "0"
                and normalize_name(pairs[i][1]).upper() == "TABLE"
            ):
                table_name = ""
                if i + 1 < len(pairs) and pairs[i + 1][0].strip() == "2":
                    table_name = normalize_name(pairs[i + 1][1]).upper()

                if table_name == "LTYPE":
                    j = i + 2
                    while j < len(pairs):
                        if (
                            pairs[j][0].strip() == "0"
                            and normalize_name(pairs[j][1]).upper() == "ENDTAB"
                        ):
                            old_len = j - i + 1
                            pairs[i:j + 1] = safe_ltype_table()
                            report["ltype_table_rebuilt"] = True
                            report["old_ltype_table_pair_count"] = old_len
                            report["new_ltype_table_pair_count"] = len(safe_ltype_table())
                            replaced = True
                            return pairs
                        j += 1
            i += 1

        if not replaced:
            # TABLES 섹션 안에 LTYPE TABLE이 없으면 TABLES 시작 직후 삽입
            i = 0
            while i < len(pairs):
                if (
                    pairs[i][0].strip() == "0"
                    and normalize_name(pairs[i][1]).upper() == "SECTION"
                    and i + 1 < len(pairs)
                    and pairs[i + 1][0].strip() == "2"
                    and normalize_name(pairs[i + 1][1]).upper() == "TABLES"
                ):
                    insert_at = i + 2
                    pairs[insert_at:insert_at] = safe_ltype_table()
                    report["ltype_table_inserted"] = True
                    return pairs
                i += 1

        return pairs

    def sanitize_layers(pairs, report):
        empty_layer_no = 1
        layer_count = 0

        i = 0
        while i < len(pairs):
            if (
                pairs[i][0].strip() == "0"
                and normalize_name(pairs[i][1]).upper() == "LAYER"
            ):
                layer_count += 1
                rec_end = next_record(pairs, i)

                idx2 = find_group(pairs, i, rec_end, "2")
                if idx2 is None:
                    pairs.insert(i + 1, ["2", f"CBL_EMPTY_LAYER_{empty_layer_no}"])
                    report["empty_layer_names_fixed"] += 1
                    empty_layer_no += 1
                    rec_end += 1
                elif not normalize_name(pairs[idx2][1]):
                    pairs[idx2][1] = f"CBL_EMPTY_LAYER_{empty_layer_no}"
                    report["empty_layer_names_fixed"] += 1
                    empty_layer_no += 1

                idx6 = find_group(pairs, i, rec_end, "6")
                if idx6 is None:
                    pairs.insert(rec_end, ["6", "Continuous"])
                    report["layer_linetype_to_continuous"] += 1
                    rec_end += 1
                else:
                    lt = normalize_name(pairs[idx6][1])
                    # LTYPE TABLE을 기본 3개로 재작성했으므로 레이어 참조도 안전하게 Continuous로 통일
                    if lt.lower() not in {"continuous", "bylayer", "byblock"}:
                        pairs[idx6][1] = "Continuous"
                        report["layer_linetype_to_continuous"] += 1
                    elif not lt:
                        pairs[idx6][1] = "Continuous"
                        report["layer_linetype_to_continuous"] += 1

                i = rec_end
            else:
                i += 1

        report["layer_count"] = layer_count
        return pairs

    def collect_ltype_names(pairs):
        names = []
        i = 0
        while i < len(pairs):
            if (
                pairs[i][0].strip() == "0"
                and normalize_name(pairs[i][1]).upper() == "LTYPE"
            ):
                rec_end = next_record(pairs, i)
                idx2 = find_group(pairs, i, rec_end, "2")
                names.append(normalize_name(pairs[idx2][1]) if idx2 is not None else "")
                i = rec_end
            else:
                i += 1
        return names

    def sanitize_dxf_for_oda(raw):
        report = {
            "mode": "CBL_ODA_LTYPE_TABLE_REBUILD_V4",
            "ltype_table_rebuilt": False,
            "ltype_table_inserted": False,
            "old_ltype_table_pair_count": 0,
            "new_ltype_table_pair_count": 0,
            "empty_layer_names_fixed": 0,
            "layer_linetype_to_continuous": 0,
            "layer_count": 0,
            "ltype_names_before": [],
            "ltype_names_after": [],
        }

        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]

        raw = raw.replace(b"\x00", b"")

        text = raw.decode("utf-8", errors="replace")
        pairs = parse_pairs(text)

        report["ltype_names_before"] = collect_ltype_names(pairs)[:30]

        pairs = rebuild_ltype_table(pairs, report)
        pairs = sanitize_layers(pairs, report)

        report["ltype_names_after"] = collect_ltype_names(pairs)[:30]

        return pairs_to_bytes(pairs), report

    def read_text_file_limited(path, limit=16000):
        try:
            data = Path(path).read_bytes()
            if len(data) > limit:
                data = data[:limit] + b"\n\n--- TRUNCATED ---\n"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return "ERR_READ_FAILED: " + repr(e)

    def collect_listing(root):
        items = []
        try:
            root = Path(root)
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        items.append({
                            "path": str(p.relative_to(root)),
                            "size": p.stat().st_size,
                        })
                    except Exception:
                        items.append({
                            "path": str(p),
                            "size": None,
                        })
        except Exception:
            pass
        return items[:150]

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"
    attempts = []
    success = False
    sanitize_report = {}

    try:
        dxf_bytes, sanitize_report = sanitize_dxf_for_oda(dxf_bytes)
        input_dxf.write_bytes(dxf_bytes)

        # 우선 1회만. 실패 원인 확인 후 버전 늘림.
        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23

        for version in versions:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                found_dwgs = [
                    str(p)
                    for p in tmp_root.rglob("*")
                    if p.is_file() and p.suffix.lower() == ".dwg"
                ]

                err_files = []
                for p in tmp_root.rglob("*.err"):
                    err_files.append({
                        "path": str(p.relative_to(tmp_root)),
                        "size": p.stat().st_size,
                        "text": read_text_file_limited(p),
                    })

                attempts.append({
                    "version": version,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-3000:] if proc.stdout else "",
                    "stderr": proc.stderr[-3000:] if proc.stderr else "",
                    "found": found_dwgs,
                    "err_files": err_files,
                    "sanitize_report": sanitize_report,
                    "input_exists": input_dxf.exists(),
                    "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                    "output_dir": str(output_dir),
                    "output_listing": collect_listing(tmp_root),
                })

                if found_dwgs:
                    found_paths = [Path(x) for x in found_dwgs]
                    found_paths.sort(
                        key=lambda p: (p.stat().st_size, p.stat().st_mtime),
                        reverse=True
                    )

                    dwg_path = found_paths[0]
                    dwg_bytes = dwg_path.read_bytes()

                    if len(dwg_bytes) >= 100:
                        success = True
                        response = HttpResponse(
                            dwg_bytes,
                            content_type="application/octet-stream"
                        )
                        quoted = urllib.parse.quote(filename)
                        ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
                        response["Content-Disposition"] = (
                            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
                        )
                        response["X-CBL-DWG-Version"] = version
                        response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                        response["X-CBL-DXF-Sanitize"] = json.dumps(sanitize_report, ensure_ascii=False)
                        return response

            except subprocess.TimeoutExpired as e:
                attempts.append({
                    "version": version,
                    "returncode": "timeout",
                    "stdout": "",
                    "stderr": str(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })
            except Exception as e:
                attempts.append({
                    "version": version,
                    "returncode": "exception",
                    "stdout": "",
                    "stderr": repr(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir_path = debug_root / f"fail_{stamp}"
            shutil.copytree(tmp_root, debug_dir_path, dirs_exist_ok=True)
            debug_dir = str(debug_dir_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "reason": "LTYPE TABLE을 ODA-safe 기본값으로 재작성했지만 변환 실패했습니다. err_files.text를 확인하세요.",
            "converter": converter,
            "debug_dir": debug_dir,
            "sanitize_report": sanitize_report,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_ODA_LTYPE_ENTITY_REFS_V5
# 목적:
# - DWG 변환 직전 저장용 DXF만 ODA-safe 정리
# - LTYPE TABLE을 ByBlock/ByLayer/Continuous/CEN/D2/HID 안전 정의로 재작성
# - ENTITY/BLOCK/LAYER 내부 group 6 선종류 참조 보정
# - group 8 빈 레이어 참조 보정
# - 렌더링/파서/한글/치수/블록 로직은 건드리지 않음
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None

    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )
        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name
    except Exception:
        pass

    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)
                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )
                dxf_text = (
                    data.get("dxf")
                    or data.get("dxfText")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )
            dxf_text = (
                request.POST.get("dxf")
                or request.POST.get("dxfText")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )
            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({
            "ok": False,
            "error": "DXF 데이터가 비어 있습니다."
        }, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    VALID_LTYPE_CANON = {
        "byblock": "ByBlock",
        "bylayer": "ByLayer",
        "continuous": "Continuous",
        "cen": "CEN",
        "center": "CEN",
        "d2": "D2",
        "hid": "HID",
        "hidden": "HID",
    }

    VALID_LTYPE_NAMES = set(VALID_LTYPE_CANON.keys())

    def clean_name(value):
        if value is None:
            return ""
        s = str(value)
        s = s.replace("\ufeff", "")
        s = s.replace("\u200b", "")
        s = s.replace("\u200c", "")
        s = s.replace("\u200d", "")
        s = s.replace("\x00", "")
        return s.strip()

    def canon_ltype(value, default="Continuous"):
        s = clean_name(value)
        if not s:
            return default
        key = s.lower()
        return VALID_LTYPE_CANON.get(key, default)

    def parse_pairs(text):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        while lines and lines[-1] == "":
            lines.pop()
        if len(lines) % 2 == 1:
            lines.append("")
        return [[str(lines[i]).strip(), lines[i + 1]] for i in range(0, len(lines), 2)]

    def pairs_to_bytes(pairs):
        out = []
        for code, value in pairs:
            out.append(str(code).strip())
            out.append("" if value is None else str(value))
        text = "\r\n".join(out)

        tail = text[-400:].upper()
        if "\r\n0\r\nEOF" not in tail and "\n0\nEOF" not in tail:
            if not text.endswith("\r\n"):
                text += "\r\n"
            text += "0\r\nEOF\r\n"
        else:
            if not text.endswith("\r\n"):
                text += "\r\n"

        return text.encode("utf-8", errors="replace")

    def next_record(pairs, start):
        j = start + 1
        while j < len(pairs):
            if pairs[j][0].strip() == "0":
                return j
            j += 1
        return len(pairs)

    def find_group(pairs, start, end, code):
        code = str(code)
        for k in range(start + 1, end):
            if pairs[k][0].strip() == code:
                return k
        return None

    def ltype_record(name, desc, pattern):
        # pattern: list of segment lengths. 양수=선, 음수=공백, 0=점
        total = sum(abs(float(x)) for x in pattern)
        rec = [
            ["0", "LTYPE"],
            ["100", "AcDbSymbolTableRecord"],
            ["100", "AcDbLinetypeTableRecord"],
            ["2", name],
            ["70", "0"],
            ["3", desc],
            ["72", "65"],
            ["73", str(len(pattern))],
            ["40", str(total)],
        ]
        for x in pattern:
            rec.append(["49", str(x)])
            rec.append(["74", "0"])
        return rec

    def safe_ltype_table():
        rows = [
            ["0", "TABLE"],
            ["2", "LTYPE"],
            ["70", "6"],
        ]

        rows += ltype_record("ByBlock", "", [])
        rows += ltype_record("ByLayer", "", [])
        rows += ltype_record("Continuous", "Solid line", [])
        rows += ltype_record("CEN", "Center ____ _ ____ _ ____", [1.25, -0.25, 0.25, -0.25])
        rows += ltype_record("D2", "Dashed __ __ __ __", [0.5, -0.25])
        rows += ltype_record("HID", "Hidden __ __ __ __", [0.25, -0.125])

        rows += [["0", "ENDTAB"]]
        return rows

    def rebuild_ltype_table(pairs, report):
        i = 0
        while i < len(pairs):
            if (
                pairs[i][0].strip() == "0"
                and clean_name(pairs[i][1]).upper() == "TABLE"
            ):
                table_name = ""
                if i + 1 < len(pairs) and pairs[i + 1][0].strip() == "2":
                    table_name = clean_name(pairs[i + 1][1]).upper()

                if table_name == "LTYPE":
                    j = i + 2
                    while j < len(pairs):
                        if (
                            pairs[j][0].strip() == "0"
                            and clean_name(pairs[j][1]).upper() == "ENDTAB"
                        ):
                            old_len = j - i + 1
                            new_table = safe_ltype_table()
                            pairs[i:j + 1] = new_table
                            report["ltype_table_rebuilt"] = True
                            report["old_ltype_table_pair_count"] = old_len
                            report["new_ltype_table_pair_count"] = len(new_table)
                            return pairs
                        j += 1
            i += 1

        # LTYPE TABLE이 없으면 TABLES 섹션에 삽입
        i = 0
        while i < len(pairs):
            if (
                pairs[i][0].strip() == "0"
                and clean_name(pairs[i][1]).upper() == "SECTION"
                and i + 1 < len(pairs)
                and pairs[i + 1][0].strip() == "2"
                and clean_name(pairs[i + 1][1]).upper() == "TABLES"
            ):
                pairs[i + 2:i + 2] = safe_ltype_table()
                report["ltype_table_inserted"] = True
                return pairs
            i += 1

        return pairs

    def collect_table_names(pairs, record_type):
        names = []
        i = 0
        record_type = record_type.upper()
        while i < len(pairs):
            if (
                pairs[i][0].strip() == "0"
                and clean_name(pairs[i][1]).upper() == record_type
            ):
                rec_end = next_record(pairs, i)
                idx2 = find_group(pairs, i, rec_end, "2")
                names.append(clean_name(pairs[idx2][1]) if idx2 is not None else "")
                i = rec_end
            else:
                i += 1
        return names

    def sanitize_layers_and_refs(pairs, report):
        # 레이어 이름 목록 수집
        layer_names = set()
        i = 0
        while i < len(pairs):
            if (
                pairs[i][0].strip() == "0"
                and clean_name(pairs[i][1]).upper() == "LAYER"
            ):
                rec_end = next_record(pairs, i)
                idx2 = find_group(pairs, i, rec_end, "2")
                name = clean_name(pairs[idx2][1]) if idx2 is not None else ""
                if name:
                    layer_names.add(name)
                i = rec_end
            else:
                i += 1

        if "0" not in layer_names:
            layer_names.add("0")

        empty_layer_no = 1

        # 모든 record 단위 group 6 / group 8 보정
        i = 0
        while i < len(pairs):
            if pairs[i][0].strip() == "0":
                rec_type = clean_name(pairs[i][1]).upper()
                rec_end = next_record(pairs, i)

                # LTYPE record 내부는 건드리지 않음
                if rec_type == "LTYPE":
                    i = rec_end
                    continue

                # LAYER record
                if rec_type == "LAYER":
                    report["layer_count"] += 1

                    idx2 = find_group(pairs, i, rec_end, "2")
                    if idx2 is None:
                        new_name = f"CBL_EMPTY_LAYER_{empty_layer_no}"
                        pairs.insert(i + 1, ["2", new_name])
                        layer_names.add(new_name)
                        empty_layer_no += 1
                        report["empty_layer_names_fixed"] += 1
                        rec_end += 1
                    elif not clean_name(pairs[idx2][1]):
                        new_name = f"CBL_EMPTY_LAYER_{empty_layer_no}"
                        pairs[idx2][1] = new_name
                        layer_names.add(new_name)
                        empty_layer_no += 1
                        report["empty_layer_names_fixed"] += 1

                    idx6 = find_group(pairs, i, rec_end, "6")
                    if idx6 is None:
                        pairs.insert(rec_end, ["6", "Continuous"])
                        report["missing_group6_fixed"] += 1
                        rec_end += 1
                    else:
                        old = clean_name(pairs[idx6][1])
                        new = canon_ltype(old, "Continuous")
                        if old != new:
                            pairs[idx6][1] = new
                            report["group6_ltype_refs_fixed"] += 1

                    i = rec_end
                    continue

                # 일반 엔티티/블록/객체 record 안의 group 6 / 8 정리
                k = i + 1
                while k < rec_end:
                    code = pairs[k][0].strip()

                    if code == "6":
                        old = clean_name(pairs[k][1])
                        if old:
                            new = canon_ltype(old, "ByLayer")
                            if old != new:
                                pairs[k][1] = new
                                report["group6_ltype_refs_fixed"] += 1
                        else:
                            pairs[k][1] = "ByLayer"
                            report["empty_group6_fixed"] += 1

                    elif code == "8":
                        old_layer = clean_name(pairs[k][1])
                        if not old_layer:
                            pairs[k][1] = "0"
                            report["empty_group8_layer_refs_fixed"] += 1

                    k += 1

                i = rec_end
            else:
                i += 1

        return pairs

    def remove_bad_empty_symbol_names(pairs, report):
        # LAYER/LTYPE/BLOCK_RECORD/STYLE/DIMSTYLE 등 symbol table record에 group 2가 비면 ODA가 싫어함
        table_record_types = {
            "LAYER": "CBL_EMPTY_LAYER",
            "STYLE": "CBL_EMPTY_STYLE",
            "DIMSTYLE": "CBL_EMPTY_DIMSTYLE",
            "BLOCK_RECORD": "CBL_EMPTY_BLOCK_RECORD",
            "APPID": "CBL_EMPTY_APPID",
            "UCS": "CBL_EMPTY_UCS",
            "VIEW": "CBL_EMPTY_VIEW",
            "VPORT": "CBL_EMPTY_VPORT",
        }

        counters = {k: 1 for k in table_record_types}

        i = 0
        while i < len(pairs):
            if pairs[i][0].strip() == "0":
                rec_type = clean_name(pairs[i][1]).upper()
                rec_end = next_record(pairs, i)

                if rec_type in table_record_types:
                    idx2 = find_group(pairs, i, rec_end, "2")
                    if idx2 is not None and not clean_name(pairs[idx2][1]):
                        prefix = table_record_types[rec_type]
                        pairs[idx2][1] = f"{prefix}_{counters[rec_type]}"
                        counters[rec_type] += 1
                        report["empty_symbol_names_fixed"] += 1

                i = rec_end
            else:
                i += 1

        return pairs

    def sanitize_dxf_for_oda(raw):
        report = {
            "mode": "CBL_ODA_LTYPE_ENTITY_REFS_V5",
            "ltype_table_rebuilt": False,
            "ltype_table_inserted": False,
            "old_ltype_table_pair_count": 0,
            "new_ltype_table_pair_count": 0,
            "layer_count": 0,
            "empty_layer_names_fixed": 0,
            "missing_group6_fixed": 0,
            "empty_group6_fixed": 0,
            "group6_ltype_refs_fixed": 0,
            "empty_group8_layer_refs_fixed": 0,
            "empty_symbol_names_fixed": 0,
            "ltype_names_before": [],
            "ltype_names_after": [],
            "layer_names_before_count": 0,
            "layer_names_after_count": 0,
        }

        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]

        raw = raw.replace(b"\x00", b"")

        text = raw.decode("utf-8", errors="replace")
        pairs = parse_pairs(text)

        report["ltype_names_before"] = collect_table_names(pairs, "LTYPE")[:50]
        report["layer_names_before_count"] = len([x for x in collect_table_names(pairs, "LAYER") if x])

        pairs = rebuild_ltype_table(pairs, report)
        pairs = remove_bad_empty_symbol_names(pairs, report)
        pairs = sanitize_layers_and_refs(pairs, report)

        report["ltype_names_after"] = collect_table_names(pairs, "LTYPE")[:50]
        report["layer_names_after_count"] = len([x for x in collect_table_names(pairs, "LAYER") if x])

        return pairs_to_bytes(pairs), report

    def read_text_file_limited(path, limit=30000):
        try:
            data = Path(path).read_bytes()
            if len(data) > limit:
                data = data[:limit] + b"\n\n--- TRUNCATED ---\n"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return "ERR_READ_FAILED: " + repr(e)

    def collect_listing(root):
        items = []
        try:
            root = Path(root)
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        items.append({
                            "path": str(p.relative_to(root)),
                            "size": p.stat().st_size,
                        })
                    except Exception:
                        items.append({
                            "path": str(p),
                            "size": None,
                        })
        except Exception:
            pass
        return items[:200]

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"
    attempts = []
    success = False
    sanitize_report = {}

    try:
        dxf_bytes, sanitize_report = sanitize_dxf_for_oda(dxf_bytes)
        input_dxf.write_bytes(dxf_bytes)

        # 깜빡임 줄이기 위해 1회만. 필요 시 다음 단계에서 버전 추가.
        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23

        for version in versions:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                found_dwgs = [
                    str(p)
                    for p in tmp_root.rglob("*")
                    if p.is_file() and p.suffix.lower() == ".dwg"
                ]

                err_files = []
                for p in tmp_root.rglob("*.err"):
                    err_files.append({
                        "path": str(p.relative_to(tmp_root)),
                        "size": p.stat().st_size,
                        "text": read_text_file_limited(p),
                    })

                attempts.append({
                    "version": version,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-3000:] if proc.stdout else "",
                    "stderr": proc.stderr[-3000:] if proc.stderr else "",
                    "found": found_dwgs,
                    "err_files": err_files,
                    "sanitize_report": sanitize_report,
                    "input_exists": input_dxf.exists(),
                    "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                    "output_dir": str(output_dir),
                    "output_listing": collect_listing(tmp_root),
                })

                if found_dwgs:
                    found_paths = [Path(x) for x in found_dwgs]
                    found_paths.sort(
                        key=lambda p: (p.stat().st_size, p.stat().st_mtime),
                        reverse=True
                    )

                    dwg_path = found_paths[0]
                    dwg_bytes = dwg_path.read_bytes()

                    if len(dwg_bytes) >= 100:
                        success = True
                        response = HttpResponse(
                            dwg_bytes,
                            content_type="application/octet-stream"
                        )
                        quoted = urllib.parse.quote(filename)
                        ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
                        response["Content-Disposition"] = (
                            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
                        )
                        response["X-CBL-DWG-Version"] = version
                        response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                        response["X-CBL-DXF-Sanitize"] = json.dumps(sanitize_report, ensure_ascii=False)
                        return response

            except subprocess.TimeoutExpired as e:
                attempts.append({
                    "version": version,
                    "returncode": "timeout",
                    "stdout": "",
                    "stderr": str(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })
            except Exception as e:
                attempts.append({
                    "version": version,
                    "returncode": "exception",
                    "stdout": "",
                    "stderr": repr(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir_path = debug_root / f"fail_{stamp}"
            shutil.copytree(tmp_root, debug_dir_path, dirs_exist_ok=True)

            # 보기 편한 에러 요약 파일 생성
            summary_path = debug_dir_path / "READ_ME_ODA_ERROR.txt"
            lines = []
            lines.append("ChickenBananaCAD ODA 변환 실패 디버그")
            lines.append("")
            lines.append("1) ODA ERR 파일:")
            for err in (debug_dir_path / "output").glob("*.err"):
                lines.append(f"   - {err}")
                lines.append("")
                lines.append(err.read_text(encoding="utf-8", errors="replace"))
                lines.append("")
            lines.append("")
            lines.append("2) sanitize_report:")
            lines.append(json.dumps(sanitize_report, ensure_ascii=False, indent=2))
            summary_path.write_text("\n".join(lines), encoding="utf-8")

            debug_dir = str(debug_dir_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "reason": "V5: LTYPE TABLE + ENTITY 선종류 참조를 정리했지만 변환 실패했습니다. debug_dir의 READ_ME_ODA_ERROR.txt를 확인하세요.",
            "converter": converter,
            "debug_dir": debug_dir,
            "sanitize_report": sanitize_report,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)



# CBL_ODA_AUDIT_OFF_V6

# CBL_ODA_REBUILD_HEADER_TABLES_V7
# 목적:
# - ODA가 line 736에서 TABLES를 읽다가 실패하는 문제 해결
# - DWG 변환 직전 저장용 DXF의 HEADER/TABLES를 완전히 새로 구성
# - 원본 BLOCKS / ENTITIES는 유지
# - 화면 렌더링/파서/한글/치수/블록 로직은 건드리지 않음
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None

    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )
        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name
    except Exception:
        pass

    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)
                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )
                dxf_text = (
                    data.get("dxf")
                    or data.get("dxfText")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )
            dxf_text = (
                request.POST.get("dxf")
                or request.POST.get("dxfText")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )
            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({"ok": False, "error": "DXF 데이터가 비어 있습니다."}, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    def clean_value(v):
        if v is None:
            return ""
        s = str(v)
        s = s.replace("\ufeff", "")
        s = s.replace("\u200b", "")
        s = s.replace("\u200c", "")
        s = s.replace("\u200d", "")
        s = s.replace("\x00", "")
        return s.strip()

    def safe_name(v, default):
        s = clean_value(v)
        if not s:
            return default
        bad = '<>/"\\:;?*|=`,'
        for ch in bad:
            s = s.replace(ch, "_")
        s = s.strip()
        return s or default

    def split_lines(raw):
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        raw = raw.replace(b"\x00", b"")
        text = raw.decode("utf-8", errors="replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        while lines and lines[-1] == "":
            lines.pop()
        return lines

    def find_sections(lines):
        sections = []
        i = 0
        n = len(lines)

        while i < n - 1:
            if lines[i].strip() == "0" and clean_value(lines[i + 1]).upper() == "SECTION":
                start = i
                sec_name = ""
                if i + 3 < n and lines[i + 2].strip() == "2":
                    sec_name = clean_value(lines[i + 3]).upper()

                j = i + 2
                end = None
                while j < n - 1:
                    if lines[j].strip() == "0" and clean_value(lines[j + 1]).upper() == "ENDSEC":
                        end = j + 2
                        break
                    j += 1

                if end:
                    sections.append({
                        "name": sec_name,
                        "start": start,
                        "end": end,
                        "lines": lines[start:end],
                    })
                    i = end
                    continue

            i += 1

        return sections

    VALID_LTYPE = {
        "byblock": "ByBlock",
        "bylayer": "ByLayer",
        "continuous": "Continuous",
        "cen": "CEN",
        "center": "CEN",
        "d2": "D2",
        "hid": "HID",
        "hidden": "HID",
    }

    def canon_ltype(v):
        s = clean_value(v)
        if not s:
            return "ByLayer"
        return VALID_LTYPE.get(s.lower(), "ByLayer")

    def clean_section_refs(section_lines, report):
        # BLOCKS/ENTITIES 섹션 내부 group 6, 7, 8만 안전하게 보정
        out = list(section_lines)

        i = 0
        while i < len(out) - 1:
            code = out[i].strip()

            if code == "6":
                old = clean_value(out[i + 1])
                new = canon_ltype(old)
                if old != new:
                    out[i + 1] = new
                    report["group6_refs_fixed"] += 1

            elif code == "7":
                # 텍스트 스타일 참조. TABLES는 Standard만 만들기 때문에 비어 있거나 이상하면 Standard.
                old = clean_value(out[i + 1])
                if not old:
                    out[i + 1] = "Standard"
                    report["empty_text_style_refs_fixed"] += 1

            elif code == "8":
                old = clean_value(out[i + 1])
                new = safe_name(old, "0")
                if old != new:
                    out[i + 1] = new
                    report["layer_refs_fixed"] += 1

            i += 2

        return out

    def collect_layer_names(section_lines):
        names = set(["0"])
        i = 0
        while i < len(section_lines) - 1:
            if section_lines[i].strip() == "8":
                nm = safe_name(section_lines[i + 1], "0")
                if nm:
                    names.add(nm)
            i += 1
        return names

    def collect_block_names(section_lines):
        names = set(["*Model_Space", "*Paper_Space"])
        i = 0
        while i < len(section_lines) - 3:
            if section_lines[i].strip() == "0" and clean_value(section_lines[i + 1]).upper() == "BLOCK":
                j = i + 2
                while j < len(section_lines) - 1:
                    if section_lines[j].strip() == "2":
                        nm = safe_name(section_lines[j + 1], "")
                        if nm:
                            names.add(nm)
                        break
                    if section_lines[j].strip() == "0":
                        break
                    j += 2
            i += 1
        return names

    def ltype_record(name, desc, pattern):
        total = sum(abs(float(x)) for x in pattern)
        rec = [
            "0", "LTYPE",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbLinetypeTableRecord",
            "2", name,
            "70", "0",
            "3", desc,
            "72", "65",
            "73", str(len(pattern)),
            "40", str(total),
        ]
        for x in pattern:
            rec += ["49", str(x), "74", "0"]
        return rec

    def build_header():
        return [
            "0", "SECTION",
            "2", "HEADER",
            "9", "$ACADVER",
            "1", "AC1027",
            "9", "$INSUNITS",
            "70", "0",
            "0", "ENDSEC",
        ]

    def build_tables(layer_names, block_names):
        layer_names = sorted(layer_names)
        block_names = sorted(block_names)

        out = [
            "0", "SECTION",
            "2", "TABLES",

            "0", "TABLE",
            "2", "VPORT",
            "70", "1",
            "0", "VPORT",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbViewportTableRecord",
            "2", "*ACTIVE",
            "70", "0",
            "10", "0.0",
            "20", "0.0",
            "11", "1.0",
            "21", "1.0",
            "12", "0.0",
            "22", "0.0",
            "13", "0.0",
            "23", "0.0",
            "14", "10.0",
            "24", "10.0",
            "15", "10.0",
            "25", "10.0",
            "16", "0.0",
            "26", "0.0",
            "36", "1.0",
            "17", "0.0",
            "27", "0.0",
            "37", "0.0",
            "40", "1000.0",
            "41", "1.0",
            "42", "50.0",
            "43", "0.0",
            "44", "0.0",
            "50", "0.0",
            "51", "0.0",
            "71", "0",
            "72", "100",
            "73", "1",
            "74", "3",
            "75", "0",
            "76", "0",
            "77", "0",
            "78", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "LTYPE",
            "70", "6",
        ]

        out += ltype_record("ByBlock", "", [])
        out += ltype_record("ByLayer", "", [])
        out += ltype_record("Continuous", "Solid line", [])
        out += ltype_record("CEN", "Center ____ _ ____ _ ____", [1.25, -0.25, 0.25, -0.25])
        out += ltype_record("D2", "Dashed __ __ __ __", [0.5, -0.25])
        out += ltype_record("HID", "Hidden __ __ __ __", [0.25, -0.125])
        out += ["0", "ENDTAB"]

        out += [
            "0", "TABLE",
            "2", "LAYER",
            "70", str(len(layer_names)),
        ]

        for name in layer_names:
            out += [
                "0", "LAYER",
                "100", "AcDbSymbolTableRecord",
                "100", "AcDbLayerTableRecord",
                "2", name,
                "70", "0",
                "62", "7",
                "6", "Continuous",
            ]

        out += ["0", "ENDTAB"]

        out += [
            "0", "TABLE",
            "2", "STYLE",
            "70", "1",
            "0", "STYLE",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbTextStyleTableRecord",
            "2", "Standard",
            "70", "0",
            "40", "0.0",
            "41", "1.0",
            "50", "0.0",
            "71", "0",
            "42", "2.5",
            "3", "txt",
            "4", "",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "VIEW",
            "70", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "UCS",
            "70", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "APPID",
            "70", "1",
            "0", "APPID",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbRegAppTableRecord",
            "2", "ACAD",
            "70", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "DIMSTYLE",
            "70", "1",
            "100", "AcDbDimStyleTable",
            "0", "DIMSTYLE",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbDimStyleTableRecord",
            "2", "Standard",
            "70", "0",
            "3", "",
            "4", "",
            "5", "",
            "6", "",
            "7", "",
            "40", "1.0",
            "41", "2.5",
            "42", "0.625",
            "43", "3.75",
            "44", "1.25",
            "140", "2.5",
            "141", "2.5",
            "142", "0.0",
            "143", "0.03937",
            "144", "1.0",
            "145", "0.0",
            "146", "1.0",
            "147", "0.625",
            "71", "0",
            "72", "0",
            "73", "0",
            "74", "0",
            "75", "0",
            "76", "0",
            "77", "1",
            "78", "8",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "BLOCK_RECORD",
            "70", str(len(block_names)),
        ]

        for name in block_names:
            out += [
                "0", "BLOCK_RECORD",
                "100", "AcDbSymbolTableRecord",
                "100", "AcDbBlockTableRecord",
                "2", name,
                "70", "0",
            ]

        out += [
            "0", "ENDTAB",
            "0", "ENDSEC",
        ]

        return out

    def rebuild_dxf(raw):
        report = {
            "mode": "CBL_ODA_REBUILD_HEADER_TABLES_V7",
            "sections_found": [],
            "kept_sections": [],
            "layer_count": 0,
            "block_record_count": 0,
            "group6_refs_fixed": 0,
            "layer_refs_fixed": 0,
            "empty_text_style_refs_fixed": 0,
            "tables_rebuilt": True,
        }

        lines = split_lines(raw)
        sections = find_sections(lines)
        report["sections_found"] = [s["name"] for s in sections]

        blocks_sections = []
        entities_sections = []

        for s in sections:
            if s["name"] == "BLOCKS":
                cleaned = clean_section_refs(s["lines"], report)
                blocks_sections.append(cleaned)
                report["kept_sections"].append("BLOCKS")
            elif s["name"] == "ENTITIES":
                cleaned = clean_section_refs(s["lines"], report)
                entities_sections.append(cleaned)
                report["kept_sections"].append("ENTITIES")

        if not entities_sections:
            raise ValueError("ENTITIES SECTION을 찾지 못했습니다.")

        layer_names = set(["0"])
        block_names = set(["*Model_Space", "*Paper_Space"])

        for sec in blocks_sections + entities_sections:
            layer_names |= collect_layer_names(sec)

        for sec in blocks_sections:
            block_names |= collect_block_names(sec)

        report["layer_count"] = len(layer_names)
        report["block_record_count"] = len(block_names)

        out = []
        out += build_header()
        out += build_tables(layer_names, block_names)

        for sec in blocks_sections:
            out += sec

        for sec in entities_sections:
            out += sec

        out += ["0", "EOF"]

        text = "\r\n".join(out) + "\r\n"
        return text.encode("utf-8", errors="replace"), report

    def read_text_file_limited(path, limit=40000):
        try:
            data = Path(path).read_bytes()
            if len(data) > limit:
                data = data[:limit] + b"\n\n--- TRUNCATED ---\n"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return "ERR_READ_FAILED: " + repr(e)

    def collect_listing(root):
        items = []
        try:
            root = Path(root)
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        items.append({
                            "path": str(p.relative_to(root)),
                            "size": p.stat().st_size,
                        })
                    except Exception:
                        items.append({
                            "path": str(p),
                            "size": None,
                        })
        except Exception:
            pass
        return items[:200]

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"
    attempts = []
    success = False
    sanitize_report = {}

    try:
        dxf_bytes, sanitize_report = rebuild_dxf(dxf_bytes)
        input_dxf.write_bytes(dxf_bytes)

        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23

        for version in versions:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                found_dwgs = [
                    str(p)
                    for p in tmp_root.rglob("*")
                    if p.is_file() and p.suffix.lower() == ".dwg"
                ]

                err_files = []
                for p in tmp_root.rglob("*.err"):
                    err_files.append({
                        "path": str(p.relative_to(tmp_root)),
                        "size": p.stat().st_size,
                        "text": read_text_file_limited(p),
                    })

                attempts.append({
                    "version": version,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-3000:] if proc.stdout else "",
                    "stderr": proc.stderr[-3000:] if proc.stderr else "",
                    "found": found_dwgs,
                    "err_files": err_files,
                    "sanitize_report": sanitize_report,
                    "input_exists": input_dxf.exists(),
                    "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                    "output_dir": str(output_dir),
                    "output_listing": collect_listing(tmp_root),
                })

                if found_dwgs:
                    found_paths = [Path(x) for x in found_dwgs]
                    found_paths.sort(
                        key=lambda p: (p.stat().st_size, p.stat().st_mtime),
                        reverse=True
                    )

                    dwg_path = found_paths[0]
                    dwg_bytes = dwg_path.read_bytes()

                    if len(dwg_bytes) >= 100:
                        success = True
                        response = HttpResponse(
                            dwg_bytes,
                            content_type="application/octet-stream"
                        )
                        quoted = urllib.parse.quote(filename)
                        ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
                        response["Content-Disposition"] = (
                            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
                        )
                        response["X-CBL-DWG-Version"] = version
                        response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                        response["X-CBL-DXF-Sanitize"] = json.dumps(sanitize_report, ensure_ascii=False)
                        return response

            except subprocess.TimeoutExpired as e:
                attempts.append({
                    "version": version,
                    "returncode": "timeout",
                    "stdout": "",
                    "stderr": str(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })
            except Exception as e:
                attempts.append({
                    "version": version,
                    "returncode": "exception",
                    "stdout": "",
                    "stderr": repr(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir_path = debug_root / f"fail_{stamp}"
            shutil.copytree(tmp_root, debug_dir_path, dirs_exist_ok=True)

            summary_path = debug_dir_path / "READ_ME_ODA_ERROR.txt"
            lines = []
            lines.append("ChickenBananaCAD ODA 변환 실패 디버그")
            lines.append("")
            lines.append("1) ODA ERR 파일:")
            for err in (debug_dir_path / "output").glob("*.err"):
                lines.append(f"   - {err}")
                lines.append("")
                lines.append(err.read_text(encoding="utf-8", errors="replace"))
                lines.append("")
            lines.append("")
            lines.append("2) sanitize_report:")
            lines.append(json.dumps(sanitize_report, ensure_ascii=False, indent=2))
            summary_path.write_text("\n".join(lines), encoding="utf-8")

            debug_dir = str(debug_dir_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "reason": "V7: HEADER/TABLES를 완전 재작성했지만 ODA 변환 실패. READ_ME_ODA_ERROR.txt 확인 필요.",
            "converter": converter,
            "debug_dir": debug_dir,
            "sanitize_report": sanitize_report,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_DWG_VISIBLE_SAVE_V8
# 목적:
# - V7에서 DWG 저장은 성공했지만 다시 열 때 안 보이는 문제 보정
# - ODA-safe HEADER/TABLES rebuild는 유지
# - 레이어/엔티티 색상 7번을 250번 진한 회색으로 바꿔 흰 배경에서 보이게 함
# - 화면 렌더링/파서/한글/치수/블록 로직은 건드리지 않음
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None

    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )
        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name
    except Exception:
        pass

    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)
                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )
                dxf_text = (
                    data.get("dxf")
                    or data.get("dxfText")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )
            dxf_text = (
                request.POST.get("dxf")
                or request.POST.get("dxfText")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )
            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({"ok": False, "error": "DXF 데이터가 비어 있습니다."}, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    def clean_value(v):
        if v is None:
            return ""
        s = str(v)
        s = s.replace("\ufeff", "")
        s = s.replace("\u200b", "")
        s = s.replace("\u200c", "")
        s = s.replace("\u200d", "")
        s = s.replace("\x00", "")
        return s.strip()

    def safe_name(v, default):
        s = clean_value(v)
        if not s:
            return default
        bad = '<>/"\\:;?*|=`,'
        for ch in bad:
            s = s.replace(ch, "_")
        s = s.strip()
        return s or default

    def safe_color(v, default="250"):
        s = clean_value(v)
        if not s:
            return default
        try:
            n = int(float(s))
        except Exception:
            return default

        # 0은 BYBLOCK, 256은 BYLAYER. 이 둘은 유지 가능.
        if n in (0, 256):
            return str(n)

        # 7번은 뷰어 흰 배경에서 안 보일 수 있으므로 진한 회색으로.
        if abs(n) == 7:
            return "250"

        if -255 <= n <= 255 and n != 0:
            return str(n)

        return default

    def split_lines(raw):
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        raw = raw.replace(b"\x00", b"")
        text = raw.decode("utf-8", errors="replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        while lines and lines[-1] == "":
            lines.pop()
        if len(lines) % 2 == 1:
            lines.append("")
        return lines

    def find_sections(lines):
        sections = []
        i = 0
        n = len(lines)

        while i < n - 1:
            if lines[i].strip() == "0" and clean_value(lines[i + 1]).upper() == "SECTION":
                start = i
                sec_name = ""
                if i + 3 < n and lines[i + 2].strip() == "2":
                    sec_name = clean_value(lines[i + 3]).upper()

                j = i + 2
                end = None
                while j < n - 1:
                    if lines[j].strip() == "0" and clean_value(lines[j + 1]).upper() == "ENDSEC":
                        end = j + 2
                        break
                    j += 2

                if end:
                    sections.append({
                        "name": sec_name,
                        "start": start,
                        "end": end,
                        "lines": lines[start:end],
                    })
                    i = end
                    continue

            i += 2

        return sections

    VALID_LTYPE = {
        "byblock": "ByBlock",
        "bylayer": "ByLayer",
        "continuous": "Continuous",
        "cen": "CEN",
        "center": "CEN",
        "d2": "D2",
        "hid": "HID",
        "hidden": "HID",
    }

    def canon_ltype(v):
        s = clean_value(v)
        if not s:
            return "ByLayer"
        return VALID_LTYPE.get(s.lower(), "ByLayer")

    def clean_section_refs(section_lines, report):
        out = list(section_lines)

        i = 0
        while i < len(out) - 1:
            code = out[i].strip()

            if code == "6":
                old = clean_value(out[i + 1])
                new = canon_ltype(old)
                if old != new:
                    out[i + 1] = new
                    report["group6_refs_fixed"] += 1

            elif code == "7":
                old = clean_value(out[i + 1])
                if not old:
                    out[i + 1] = "Standard"
                    report["empty_text_style_refs_fixed"] += 1

            elif code == "8":
                old = clean_value(out[i + 1])
                new = safe_name(old, "0")
                if old != new:
                    out[i + 1] = new
                    report["layer_refs_fixed"] += 1

            elif code == "62":
                old = clean_value(out[i + 1])
                new = safe_color(old, "250")
                if old != new:
                    out[i + 1] = new
                    report["color_refs_fixed"] += 1

            i += 2

        return out

    def collect_layer_names(section_lines):
        names = set(["0"])
        i = 0
        while i < len(section_lines) - 1:
            if section_lines[i].strip() == "8":
                nm = safe_name(section_lines[i + 1], "0")
                if nm:
                    names.add(nm)
            i += 2
        return names

    def collect_block_names(section_lines):
        names = set(["*Model_Space", "*Paper_Space"])
        i = 0
        while i < len(section_lines) - 3:
            if section_lines[i].strip() == "0" and clean_value(section_lines[i + 1]).upper() == "BLOCK":
                j = i + 2
                while j < len(section_lines) - 1:
                    if section_lines[j].strip() == "2":
                        nm = safe_name(section_lines[j + 1], "")
                        if nm:
                            names.add(nm)
                        break
                    if section_lines[j].strip() == "0":
                        break
                    j += 2
            i += 2
        return names

    def ltype_record(name, desc, pattern):
        total = sum(abs(float(x)) for x in pattern)
        rec = [
            "0", "LTYPE",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbLinetypeTableRecord",
            "2", name,
            "70", "0",
            "3", desc,
            "72", "65",
            "73", str(len(pattern)),
            "40", str(total),
        ]
        for x in pattern:
            rec += ["49", str(x), "74", "0"]
        return rec

    def build_header():
        return [
            "0", "SECTION",
            "2", "HEADER",
            "9", "$ACADVER",
            "1", "AC1027",
            "9", "$INSUNITS",
            "70", "0",
            "0", "ENDSEC",
        ]

    def build_tables(layer_names, block_names):
        layer_names = sorted(layer_names)
        block_names = sorted(block_names)

        out = [
            "0", "SECTION",
            "2", "TABLES",

            "0", "TABLE",
            "2", "VPORT",
            "70", "1",
            "0", "VPORT",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbViewportTableRecord",
            "2", "*ACTIVE",
            "70", "0",
            "10", "0.0",
            "20", "0.0",
            "11", "1.0",
            "21", "1.0",
            "12", "0.0",
            "22", "0.0",
            "13", "0.0",
            "23", "0.0",
            "14", "10.0",
            "24", "10.0",
            "15", "10.0",
            "25", "10.0",
            "16", "0.0",
            "26", "0.0",
            "36", "1.0",
            "17", "0.0",
            "27", "0.0",
            "37", "0.0",
            "40", "1000.0",
            "41", "1.0",
            "42", "50.0",
            "43", "0.0",
            "44", "0.0",
            "50", "0.0",
            "51", "0.0",
            "71", "0",
            "72", "100",
            "73", "1",
            "74", "3",
            "75", "0",
            "76", "0",
            "77", "0",
            "78", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "LTYPE",
            "70", "6",
        ]

        out += ltype_record("ByBlock", "", [])
        out += ltype_record("ByLayer", "", [])
        out += ltype_record("Continuous", "Solid line", [])
        out += ltype_record("CEN", "Center ____ _ ____ _ ____", [1.25, -0.25, 0.25, -0.25])
        out += ltype_record("D2", "Dashed __ __ __ __", [0.5, -0.25])
        out += ltype_record("HID", "Hidden __ __ __ __", [0.25, -0.125])
        out += ["0", "ENDTAB"]

        out += [
            "0", "TABLE",
            "2", "LAYER",
            "70", str(len(layer_names)),
        ]

        # 핵심 변경:
        # 색 7 대신 250 사용. 흰 배경에서 보이게.
        for name in layer_names:
            out += [
                "0", "LAYER",
                "100", "AcDbSymbolTableRecord",
                "100", "AcDbLayerTableRecord",
                "2", name,
                "70", "0",
                "62", "250",
                "6", "Continuous",
            ]

        out += ["0", "ENDTAB"]

        out += [
            "0", "TABLE",
            "2", "STYLE",
            "70", "1",
            "0", "STYLE",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbTextStyleTableRecord",
            "2", "Standard",
            "70", "0",
            "40", "0.0",
            "41", "1.0",
            "50", "0.0",
            "71", "0",
            "42", "2.5",
            "3", "txt",
            "4", "",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "VIEW",
            "70", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "UCS",
            "70", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "APPID",
            "70", "1",
            "0", "APPID",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbRegAppTableRecord",
            "2", "ACAD",
            "70", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "DIMSTYLE",
            "70", "1",
            "100", "AcDbDimStyleTable",
            "0", "DIMSTYLE",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbDimStyleTableRecord",
            "2", "Standard",
            "70", "0",
            "3", "",
            "4", "",
            "5", "",
            "6", "",
            "7", "",
            "40", "1.0",
            "41", "2.5",
            "42", "0.625",
            "43", "3.75",
            "44", "1.25",
            "140", "2.5",
            "141", "2.5",
            "142", "0.0",
            "143", "0.03937",
            "144", "1.0",
            "145", "0.0",
            "146", "1.0",
            "147", "0.625",
            "71", "0",
            "72", "0",
            "73", "0",
            "74", "0",
            "75", "0",
            "76", "0",
            "77", "1",
            "78", "8",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "BLOCK_RECORD",
            "70", str(len(block_names)),
        ]

        for name in block_names:
            out += [
                "0", "BLOCK_RECORD",
                "100", "AcDbSymbolTableRecord",
                "100", "AcDbBlockTableRecord",
                "2", name,
                "70", "0",
            ]

        out += [
            "0", "ENDTAB",
            "0", "ENDSEC",
        ]

        return out

    def rebuild_dxf(raw):
        report = {
            "mode": "CBL_DWG_VISIBLE_SAVE_V8",
            "sections_found": [],
            "kept_sections": [],
            "layer_count": 0,
            "block_record_count": 0,
            "group6_refs_fixed": 0,
            "layer_refs_fixed": 0,
            "color_refs_fixed": 0,
            "empty_text_style_refs_fixed": 0,
            "tables_rebuilt": True,
            "layer_color_default": "250",
        }

        lines = split_lines(raw)
        sections = find_sections(lines)
        report["sections_found"] = [s["name"] for s in sections]

        blocks_sections = []
        entities_sections = []

        for s in sections:
            if s["name"] == "BLOCKS":
                cleaned = clean_section_refs(s["lines"], report)
                blocks_sections.append(cleaned)
                report["kept_sections"].append("BLOCKS")
            elif s["name"] == "ENTITIES":
                cleaned = clean_section_refs(s["lines"], report)
                entities_sections.append(cleaned)
                report["kept_sections"].append("ENTITIES")

        if not entities_sections:
            raise ValueError("ENTITIES SECTION을 찾지 못했습니다.")

        layer_names = set(["0"])
        block_names = set(["*Model_Space", "*Paper_Space"])

        for sec in blocks_sections + entities_sections:
            layer_names |= collect_layer_names(sec)

        for sec in blocks_sections:
            block_names |= collect_block_names(sec)

        report["layer_count"] = len(layer_names)
        report["block_record_count"] = len(block_names)

        out = []
        out += build_header()
        out += build_tables(layer_names, block_names)

        for sec in blocks_sections:
            out += sec

        for sec in entities_sections:
            out += sec

        out += ["0", "EOF"]

        text = "\r\n".join(out) + "\r\n"
        return text.encode("utf-8", errors="replace"), report

    def read_text_file_limited(path, limit=40000):
        try:
            data = Path(path).read_bytes()
            if len(data) > limit:
                data = data[:limit] + b"\n\n--- TRUNCATED ---\n"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return "ERR_READ_FAILED: " + repr(e)

    def collect_listing(root):
        items = []
        try:
            root = Path(root)
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        items.append({
                            "path": str(p.relative_to(root)),
                            "size": p.stat().st_size,
                        })
                    except Exception:
                        items.append({
                            "path": str(p),
                            "size": None,
                        })
        except Exception:
            pass
        return items[:200]

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"
    attempts = []
    success = False
    sanitize_report = {}

    try:
        dxf_bytes, sanitize_report = rebuild_dxf(dxf_bytes)
        input_dxf.write_bytes(dxf_bytes)

        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23

        for version in versions:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                found_dwgs = [
                    str(p)
                    for p in tmp_root.rglob("*")
                    if p.is_file() and p.suffix.lower() == ".dwg"
                ]

                err_files = []
                for p in tmp_root.rglob("*.err"):
                    err_files.append({
                        "path": str(p.relative_to(tmp_root)),
                        "size": p.stat().st_size,
                        "text": read_text_file_limited(p),
                    })

                attempts.append({
                    "version": version,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-3000:] if proc.stdout else "",
                    "stderr": proc.stderr[-3000:] if proc.stderr else "",
                    "found": found_dwgs,
                    "err_files": err_files,
                    "sanitize_report": sanitize_report,
                    "input_exists": input_dxf.exists(),
                    "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                    "output_dir": str(output_dir),
                    "output_listing": collect_listing(tmp_root),
                })

                if found_dwgs:
                    found_paths = [Path(x) for x in found_dwgs]
                    found_paths.sort(
                        key=lambda p: (p.stat().st_size, p.stat().st_mtime),
                        reverse=True
                    )

                    dwg_path = found_paths[0]
                    dwg_bytes = dwg_path.read_bytes()

                    if len(dwg_bytes) >= 100:
                        success = True
                        response = HttpResponse(
                            dwg_bytes,
                            content_type="application/octet-stream"
                        )
                        quoted = urllib.parse.quote(filename)
                        ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
                        response["Content-Disposition"] = (
                            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
                        )
                        response["X-CBL-DWG-Version"] = version
                        response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                        response["X-CBL-DXF-Sanitize"] = json.dumps(sanitize_report, ensure_ascii=False)
                        return response

            except subprocess.TimeoutExpired as e:
                attempts.append({
                    "version": version,
                    "returncode": "timeout",
                    "stdout": "",
                    "stderr": str(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })
            except Exception as e:
                attempts.append({
                    "version": version,
                    "returncode": "exception",
                    "stdout": "",
                    "stderr": repr(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir_path = debug_root / f"fail_{stamp}"
            shutil.copytree(tmp_root, debug_dir_path, dirs_exist_ok=True)

            summary_path = debug_dir_path / "READ_ME_ODA_ERROR.txt"
            lines = []
            lines.append("ChickenBananaCAD ODA 변환 실패 디버그")
            lines.append("")
            lines.append("1) ODA ERR 파일:")
            for err in (debug_dir_path / "output").glob("*.err"):
                lines.append(f"   - {err}")
                lines.append("")
                lines.append(err.read_text(encoding="utf-8", errors="replace"))
                lines.append("")
            lines.append("")
            lines.append("2) sanitize_report:")
            lines.append(json.dumps(sanitize_report, ensure_ascii=False, indent=2))
            summary_path.write_text("\n".join(lines), encoding="utf-8")

            debug_dir = str(debug_dir_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "reason": "V8: Visible Save 보정 후에도 ODA 변환 실패. READ_ME_ODA_ERROR.txt 확인 필요.",
            "converter": converter,
            "debug_dir": debug_dir,
            "sanitize_report": sanitize_report,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_DWG_SAVE_PRESERVE_SECTIONS_V9
# 목적:
# - V7/V8의 전체 HEADER/TABLES 재작성으로 DWG는 저장되지만 다시 열 때 안 보이는 문제 해결
# - 원본 DXF 섹션 구조는 최대한 유지
# - ODA가 실패하던 TABLES 내부 LTYPE / LAYER / STYLE 테이블만 안전 재작성
# - BLOCKS / ENTITIES / OBJECTS / DIMSTYLE / BLOCK_RECORD 등은 유지
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None

    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )
        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name
    except Exception:
        pass

    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)
                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )
                dxf_text = (
                    data.get("dxf")
                    or data.get("dxfText")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )
            dxf_text = (
                request.POST.get("dxf")
                or request.POST.get("dxfText")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )
            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({"ok": False, "error": "DXF 데이터가 비어 있습니다."}, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    def clean_value(v):
        if v is None:
            return ""
        s = str(v)
        s = s.replace("\ufeff", "")
        s = s.replace("\u200b", "")
        s = s.replace("\u200c", "")
        s = s.replace("\u200d", "")
        s = s.replace("\x00", "")
        return s.strip()

    def safe_name(v, default):
        s = clean_value(v)
        if not s:
            return default
        bad = '<>/"\\:;?*|=`,'
        for ch in bad:
            s = s.replace(ch, "_")
        s = s.strip()
        return s or default

    def split_lines(raw):
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        raw = raw.replace(b"\x00", b"")
        text = raw.decode("utf-8", errors="replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        while lines and lines[-1] == "":
            lines.pop()
        if len(lines) % 2 == 1:
            lines.append("")
        return lines

    def pairs_to_bytes(lines):
        while lines and lines[-1] == "":
            lines.pop()

        tail = "\n".join(lines[-10:]).upper()
        if "EOF" not in tail:
            lines += ["0", "EOF"]

        text = "\r\n".join(str(x) for x in lines) + "\r\n"
        return text.encode("utf-8", errors="replace")

    VALID_LTYPE = {
        "byblock": "ByBlock",
        "bylayer": "ByLayer",
        "continuous": "Continuous",
        "cen": "CEN",
        "center": "CEN",
        "d2": "D2",
        "hid": "HID",
        "hidden": "HID",
    }

    def canon_ltype(v):
        s = clean_value(v)
        if not s:
            return "ByLayer"
        return VALID_LTYPE.get(s.lower(), "ByLayer")

    def ltype_record(name, desc, pattern):
        total = sum(abs(float(x)) for x in pattern)
        rec = [
            "0", "LTYPE",
            "100", "AcDbSymbolTableRecord",
            "100", "AcDbLinetypeTableRecord",
            "2", name,
            "70", "0",
            "3", desc,
            "72", "65",
            "73", str(len(pattern)),
            "40", str(total),
        ]
        for x in pattern:
            rec += ["49", str(x), "74", "0"]
        return rec

    def build_ltype_table():
        out = [
            "0", "TABLE",
            "2", "LTYPE",
            "70", "6",
        ]
        out += ltype_record("ByBlock", "", [])
        out += ltype_record("ByLayer", "", [])
        out += ltype_record("Continuous", "Solid line", [])
        out += ltype_record("CEN", "Center ____ _ ____ _ ____", [1.25, -0.25, 0.25, -0.25])
        out += ltype_record("D2", "Dashed __ __ __ __", [0.5, -0.25])
        out += ltype_record("HID", "Hidden __ __ __ __", [0.25, -0.125])
        out += ["0", "ENDTAB"]
        return out

    def build_layer_table(layer_names):
        layer_names = sorted(layer_names or {"0"})
        out = [
            "0", "TABLE",
            "2", "LAYER",
            "70", str(len(layer_names)),
        ]

        for name in layer_names:
            out += [
                "0", "LAYER",
                "100", "AcDbSymbolTableRecord",
                "100", "AcDbLayerTableRecord",
                "2", name,
                "70", "0",
                "62", "250",
                "6", "Continuous",
            ]

        out += ["0", "ENDTAB"]
        return out

    def build_style_table(style_names):
        clean_styles = []
        for s in style_names or set():
            nm = safe_name(s, "")
            if nm:
                clean_styles.append(nm)

        if "Standard" not in clean_styles:
            clean_styles.insert(0, "Standard")

        clean_styles = sorted(set(clean_styles), key=lambda x: (x != "Standard", x))

        out = [
            "0", "TABLE",
            "2", "STYLE",
            "70", str(len(clean_styles)),
        ]

        for name in clean_styles:
            out += [
                "0", "STYLE",
                "100", "AcDbSymbolTableRecord",
                "100", "AcDbTextStyleTableRecord",
                "2", name,
                "70", "0",
                "40", "0.0",
                "41", "1.0",
                "50", "0.0",
                "71", "0",
                "42", "2.5",
                "3", "txt",
                "4", "",
            ]

        out += ["0", "ENDTAB"]
        return out

    def find_sections(lines):
        sections = []
        i = 0
        n = len(lines)

        while i < n - 1:
            if lines[i].strip() == "0" and clean_value(lines[i + 1]).upper() == "SECTION":
                start = i
                sec_name = ""
                if i + 3 < n and lines[i + 2].strip() == "2":
                    sec_name = clean_value(lines[i + 3]).upper()

                j = i + 2
                end = None
                while j < n - 1:
                    if lines[j].strip() == "0" and clean_value(lines[j + 1]).upper() == "ENDSEC":
                        end = j + 2
                        break
                    j += 1

                if end:
                    sections.append({
                        "name": sec_name,
                        "start": start,
                        "end": end,
                    })
                    i = end
                    continue

            i += 1

        return sections

    def collect_layer_and_style_names(lines):
        layer_names = set(["0"])
        style_names = set(["Standard"])

        i = 0
        while i < len(lines) - 1:
            code = lines[i].strip()
            val = clean_value(lines[i + 1])

            if code == "8":
                layer_names.add(safe_name(val, "0"))
            elif code == "7":
                if val:
                    style_names.add(safe_name(val, "Standard"))

            i += 1

        return layer_names, style_names

    def clean_blocks_entities_refs(sec_lines, report):
        out = list(sec_lines)
        i = 0

        while i < len(out) - 1:
            code = out[i].strip()

            if code == "6":
                old = clean_value(out[i + 1])
                new = canon_ltype(old)
                if old != new:
                    out[i + 1] = new
                    report["group6_refs_fixed"] += 1

            elif code == "7":
                old = clean_value(out[i + 1])
                if not old:
                    out[i + 1] = "Standard"
                    report["empty_style_refs_fixed"] += 1

            elif code == "8":
                old = clean_value(out[i + 1])
                new = safe_name(old, "0")
                if old != new:
                    out[i + 1] = new
                    report["layer_refs_fixed"] += 1

            i += 2

        return out

    def replace_table_in_tables_section(tables_lines, layer_names, style_names, report):
        # tables_lines includes:
        # 0 SECTION 2 TABLES ... 0 ENDSEC
        out = []
        i = 0
        inserted = {
            "LTYPE": False,
            "LAYER": False,
            "STYLE": False,
        }

        while i < len(tables_lines) - 1:
            if (
                tables_lines[i].strip() == "0"
                and clean_value(tables_lines[i + 1]).upper() == "TABLE"
            ):
                table_start = i
                table_name = ""

                j = i + 2
                scan_limit = min(len(tables_lines) - 1, i + 20)
                while j < scan_limit:
                    if tables_lines[j].strip() == "2":
                        table_name = clean_value(tables_lines[j + 1]).upper()
                        break
                    if tables_lines[j].strip() == "0":
                        break
                    j += 2

                k = i + 2
                table_end = None
                while k < len(tables_lines) - 1:
                    if (
                        tables_lines[k].strip() == "0"
                        and clean_value(tables_lines[k + 1]).upper() == "ENDTAB"
                    ):
                        table_end = k + 2
                        break
                    k += 1

                if table_end and table_name in {"LTYPE", "LAYER", "STYLE"}:
                    if table_name == "LTYPE":
                        out += build_ltype_table()
                        report["ltype_table_rebuilt"] = True
                    elif table_name == "LAYER":
                        out += build_layer_table(layer_names)
                        report["layer_table_rebuilt"] = True
                    elif table_name == "STYLE":
                        out += build_style_table(style_names)
                        report["style_table_rebuilt"] = True

                    inserted[table_name] = True
                    i = table_end
                    continue

            out.append(tables_lines[i])
            i += 1

        while i < len(tables_lines):
            out.append(tables_lines[i])
            i += 1

        # 누락된 테이블은 TABLES 섹션 시작 직후 삽입
        insert_at = 4 if len(out) >= 4 and out[0].strip() == "0" and clean_value(out[1]).upper() == "SECTION" else 0
        extra = []
        if not inserted["LTYPE"]:
            extra += build_ltype_table()
            report["ltype_table_inserted"] = True
        if not inserted["LAYER"]:
            extra += build_layer_table(layer_names)
            report["layer_table_inserted"] = True
        if not inserted["STYLE"]:
            extra += build_style_table(style_names)
            report["style_table_inserted"] = True

        if extra:
            out[insert_at:insert_at] = extra

        return out

    def rebuild_dxf_preserve_sections(raw):
        report = {
            "mode": "CBL_DWG_SAVE_PRESERVE_SECTIONS_V9",
            "sections_found": [],
            "sections_preserved": [],
            "ltype_table_rebuilt": False,
            "layer_table_rebuilt": False,
            "style_table_rebuilt": False,
            "ltype_table_inserted": False,
            "layer_table_inserted": False,
            "style_table_inserted": False,
            "group6_refs_fixed": 0,
            "layer_refs_fixed": 0,
            "empty_style_refs_fixed": 0,
            "layer_count": 0,
            "style_count": 0,
            "objects_section_preserved": False,
        }

        lines = split_lines(raw)
        sections = find_sections(lines)

        if not sections:
            raise ValueError("DXF SECTION을 찾지 못했습니다.")

        report["sections_found"] = [s["name"] for s in sections]

        layer_names, style_names = collect_layer_and_style_names(lines)
        report["layer_count"] = len(layer_names)
        report["style_count"] = len(style_names)

        out = []
        cursor = 0

        for sec in sections:
            # 섹션 앞의 잡라인은 그대로 유지하되, EOF는 마지막에 다시 붙임
            if sec["start"] > cursor:
                pre = lines[cursor:sec["start"]]
                pre = [x for x in pre if clean_value(x).upper() != "EOF"]
                out += pre

            sec_lines = lines[sec["start"]:sec["end"]]
            name = sec["name"]

            if name == "TABLES":
                out += replace_table_in_tables_section(sec_lines, layer_names, style_names, report)
                report["sections_preserved"].append("TABLES_MODIFIED")

            elif name in {"BLOCKS", "ENTITIES"}:
                out += clean_blocks_entities_refs(sec_lines, report)
                report["sections_preserved"].append(name)

            else:
                out += sec_lines
                report["sections_preserved"].append(name)
                if name == "OBJECTS":
                    report["objects_section_preserved"] = True

            cursor = sec["end"]

        # 남은 뒤쪽 라인 처리
        if cursor < len(lines):
            rest = lines[cursor:]
            rest = [x for x in rest if clean_value(x).upper() != "EOF"]
            out += rest

        out += ["0", "EOF"]

        return pairs_to_bytes(out), report

    def read_text_file_limited(path, limit=50000):
        try:
            data = Path(path).read_bytes()
            if len(data) > limit:
                data = data[:limit] + b"\n\n--- TRUNCATED ---\n"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return "ERR_READ_FAILED: " + repr(e)

    def collect_listing(root):
        items = []
        try:
            root = Path(root)
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        items.append({
                            "path": str(p.relative_to(root)),
                            "size": p.stat().st_size,
                        })
                    except Exception:
                        items.append({
                            "path": str(p),
                            "size": None,
                        })
        except Exception:
            pass
        return items[:200]

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"
    attempts = []
    success = False
    sanitize_report = {}

    try:
        dxf_bytes, sanitize_report = rebuild_dxf_preserve_sections(dxf_bytes)
        input_dxf.write_bytes(dxf_bytes)

        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23

        for version in versions:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                found_dwgs = [
                    str(p)
                    for p in tmp_root.rglob("*")
                    if p.is_file() and p.suffix.lower() == ".dwg"
                ]

                err_files = []
                for p in tmp_root.rglob("*.err"):
                    err_files.append({
                        "path": str(p.relative_to(tmp_root)),
                        "size": p.stat().st_size,
                        "text": read_text_file_limited(p),
                    })

                attempts.append({
                    "version": version,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-3000:] if proc.stdout else "",
                    "stderr": proc.stderr[-3000:] if proc.stderr else "",
                    "found": found_dwgs,
                    "err_files": err_files,
                    "sanitize_report": sanitize_report,
                    "input_exists": input_dxf.exists(),
                    "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                    "output_dir": str(output_dir),
                    "output_listing": collect_listing(tmp_root),
                })

                if found_dwgs:
                    found_paths = [Path(x) for x in found_dwgs]
                    found_paths.sort(
                        key=lambda p: (p.stat().st_size, p.stat().st_mtime),
                        reverse=True
                    )

                    dwg_path = found_paths[0]
                    dwg_bytes = dwg_path.read_bytes()

                    if len(dwg_bytes) >= 100:
                        success = True
                        response = HttpResponse(
                            dwg_bytes,
                            content_type="application/octet-stream"
                        )
                        quoted = urllib.parse.quote(filename)
                        ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
                        response["Content-Disposition"] = (
                            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
                        )
                        response["X-CBL-DWG-Version"] = version
                        response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                        response["X-CBL-DXF-Sanitize"] = json.dumps(sanitize_report, ensure_ascii=False)
                        return response

            except subprocess.TimeoutExpired as e:
                attempts.append({
                    "version": version,
                    "returncode": "timeout",
                    "stdout": "",
                    "stderr": str(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })
            except Exception as e:
                attempts.append({
                    "version": version,
                    "returncode": "exception",
                    "stdout": "",
                    "stderr": repr(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir_path = debug_root / f"fail_{stamp}"
            shutil.copytree(tmp_root, debug_dir_path, dirs_exist_ok=True)

            summary_path = debug_dir_path / "READ_ME_ODA_ERROR.txt"
            lines2 = []
            lines2.append("ChickenBananaCAD ODA 변환 실패 디버그")
            lines2.append("")
            lines2.append("1) ODA ERR 파일:")
            for err in (debug_dir_path / "output").glob("*.err"):
                lines2.append(f"   - {err}")
                lines2.append("")
                lines2.append(err.read_text(encoding="utf-8", errors="replace"))
                lines2.append("")
            lines2.append("")
            lines2.append("2) sanitize_report:")
            lines2.append(json.dumps(sanitize_report, ensure_ascii=False, indent=2))
            summary_path.write_text("\n".join(lines2), encoding="utf-8")

            debug_dir = str(debug_dir_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "reason": "V9: 원본 섹션 보존 + TABLES 일부 교체 후에도 ODA 변환 실패. READ_ME_ODA_ERROR.txt 확인 필요.",
            "converter": converter,
            "debug_dir": debug_dir,
            "sanitize_report": sanitize_report,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_DWG_SAVE_R12_SAFE_V10
# 목적:
# - V7/V8: DWG 저장 성공하지만 다시 열 때 안 보임
# - V9: 원본 TABLES 일부 보존 시 ODA 실패
# - V10: R12-safe HEADER/TABLES로 단순화하고 BLOCKS/ENTITIES는 보존
# - BLOCK_RECORD/OBJECTS/AcDb subclass를 넣지 않아 INSERT-BLOCK 연결 손상 방지
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"
    if not os.path.exists(converter):
        return JsonResponse({"ok": False, "error": "ODAFileConverter를 찾지 못했습니다.", "converter": converter}, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None

    try:
        upload = request.FILES.get("file") or request.FILES.get("dxf") or request.FILES.get("dxf_file") or request.FILES.get("drawing")
        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name
    except Exception:
        pass

    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)
                filename = data.get("filename") or data.get("name") or data.get("output_filename") or data.get("outputName") or filename
                dxf_text = data.get("dxf") or data.get("dxfText") or data.get("text") or data.get("content") or data.get("raw")
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None:
        try:
            filename = request.POST.get("filename") or request.POST.get("name") or request.POST.get("output_filename") or request.POST.get("outputName") or filename
            dxf_text = request.POST.get("dxf") or request.POST.get("dxfText") or request.POST.get("text") or request.POST.get("content") or request.POST.get("raw")
            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({"ok": False, "error": "DXF 데이터가 비어 있습니다."}, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    def clean(v):
        if v is None:
            return ""
        s = str(v)
        s = s.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\x00", "")
        return s.strip()

    def safe_name(v, default):
        s = clean(v)
        if not s:
            return default
        for ch in '<>/"\\:;?*|=`,':
            s = s.replace(ch, "_")
        s = s.strip()
        return s or default

    def read_lines(raw):
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        raw = raw.replace(b"\x00", b"")
        text = raw.decode("utf-8", errors="replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        while lines and lines[-1] == "":
            lines.pop()
        if len(lines) % 2 == 1:
            lines.append("")
        return lines

    def find_sections(lines):
        sections = []
        i = 0
        n = len(lines)
        while i < n - 1:
            if lines[i].strip() == "0" and clean(lines[i + 1]).upper() == "SECTION":
                start = i
                name = ""
                if i + 3 < n and lines[i + 2].strip() == "2":
                    name = clean(lines[i + 3]).upper()

                j = i + 2
                end = None
                while j < n - 1:
                    if lines[j].strip() == "0" and clean(lines[j + 1]).upper() == "ENDSEC":
                        end = j + 2
                        break
                    j += 1

                if end:
                    sections.append({"name": name, "start": start, "end": end, "lines": lines[start:end]})
                    i = end
                    continue
            i += 1
        return sections

    valid_ltypes = {
        "byblock": "BYBLOCK",
        "bylayer": "BYLAYER",
        "continuous": "CONTINUOUS",
        "cen": "CEN",
        "center": "CEN",
        "d2": "D2",
        "hid": "HID",
        "hidden": "HID",
    }

    def canon_ltype(v):
        s = clean(v)
        if not s:
            return "BYLAYER"
        return valid_ltypes.get(s.lower(), "BYLAYER")

    def clean_blocks_entities(lines, report):
        out = list(lines)
        i = 0
        while i < len(out) - 1:
            code = out[i].strip()

            if code == "6":
                old = clean(out[i + 1])
                new = canon_ltype(old)
                if old != new:
                    out[i + 1] = new
                    report["group6_refs_fixed"] += 1

            elif code == "7":
                old = clean(out[i + 1])
                if not old:
                    out[i + 1] = "STANDARD"
                    report["empty_style_refs_fixed"] += 1

            elif code == "8":
                old = clean(out[i + 1])
                new = safe_name(old, "0")
                if old != new:
                    out[i + 1] = new
                    report["layer_refs_fixed"] += 1

            elif code == "62":
                old = clean(out[i + 1])
                if old in ("", "7", "-7"):
                    out[i + 1] = "250"
                    report["color_refs_fixed"] += 1

            i += 2
        return out

    def collect_names(lines):
        layers = set(["0"])
        styles = set(["STANDARD"])
        blocks = set()

        i = 0
        while i < len(lines) - 1:
            code = lines[i].strip()
            val = clean(lines[i + 1])

            if code == "8":
                layers.add(safe_name(val, "0"))
            elif code == "7" and val:
                styles.add(safe_name(val, "STANDARD").upper())

            if code == "0" and clean(lines[i + 1]).upper() == "BLOCK":
                j = i + 2
                while j < len(lines) - 1:
                    if lines[j].strip() == "2":
                        bn = safe_name(lines[j + 1], "")
                        if bn:
                            blocks.add(bn)
                        break
                    if lines[j].strip() == "0":
                        break
                    j += 2
            i += 1

        return layers, styles, blocks

    def ltype(name, desc, pattern):
        total = sum(abs(float(x)) for x in pattern)
        out = ["0", "LTYPE", "2", name, "70", "0", "3", desc, "72", "65", "73", str(len(pattern)), "40", str(total)]
        for x in pattern:
            out += ["49", str(x), "74", "0"]
        return out

    def build_header():
        return [
            "0", "SECTION",
            "2", "HEADER",
            "9", "$ACADVER",
            "1", "AC1009",
            "9", "$INSBASE",
            "10", "0.0",
            "20", "0.0",
            "30", "0.0",
            "9", "$EXTMIN",
            "10", "-1000000.0",
            "20", "-1000000.0",
            "30", "0.0",
            "9", "$EXTMAX",
            "10", "1000000.0",
            "20", "1000000.0",
            "30", "0.0",
            "9", "$LIMMIN",
            "10", "0.0",
            "20", "0.0",
            "9", "$LIMMAX",
            "10", "1000.0",
            "20", "1000.0",
            "0", "ENDSEC",
        ]

    def build_tables(layers, styles):
        layers = sorted(layers or {"0"})
        styles = sorted(styles or {"STANDARD"})

        out = [
            "0", "SECTION",
            "2", "TABLES",

            "0", "TABLE",
            "2", "VPORT",
            "70", "1",
            "0", "VPORT",
            "2", "*ACTIVE",
            "70", "0",
            "10", "0.0",
            "20", "0.0",
            "11", "1.0",
            "21", "1.0",
            "12", "0.0",
            "22", "0.0",
            "13", "0.0",
            "23", "0.0",
            "14", "10.0",
            "24", "10.0",
            "15", "10.0",
            "25", "10.0",
            "16", "0.0",
            "26", "0.0",
            "36", "1.0",
            "17", "0.0",
            "27", "0.0",
            "37", "0.0",
            "40", "1000.0",
            "41", "1.0",
            "42", "50.0",
            "43", "0.0",
            "44", "0.0",
            "50", "0.0",
            "51", "0.0",
            "71", "0",
            "72", "100",
            "73", "1",
            "74", "3",
            "75", "0",
            "76", "0",
            "77", "0",
            "78", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "LTYPE",
            "70", "6",
        ]

        out += ltype("BYBLOCK", "", [])
        out += ltype("BYLAYER", "", [])
        out += ltype("CONTINUOUS", "Solid line", [])
        out += ltype("CEN", "Center ____ _ ____ _ ____", [1.25, -0.25, 0.25, -0.25])
        out += ltype("D2", "Dashed __ __ __ __", [0.5, -0.25])
        out += ltype("HID", "Hidden __ __ __ __", [0.25, -0.125])
        out += ["0", "ENDTAB"]

        out += [
            "0", "TABLE",
            "2", "LAYER",
            "70", str(len(layers)),
        ]

        for name in layers:
            out += [
                "0", "LAYER",
                "2", name,
                "70", "0",
                "62", "250",
                "6", "CONTINUOUS",
            ]

        out += ["0", "ENDTAB"]

        out += [
            "0", "TABLE",
            "2", "STYLE",
            "70", str(len(styles)),
        ]

        for name in styles:
            out += [
                "0", "STYLE",
                "2", name,
                "70", "0",
                "40", "0.0",
                "41", "1.0",
                "50", "0.0",
                "71", "0",
                "42", "2.5",
                "3", "txt",
                "4", "",
            ]

        out += [
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "VIEW",
            "70", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "UCS",
            "70", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "APPID",
            "70", "1",
            "0", "APPID",
            "2", "ACAD",
            "70", "0",
            "0", "ENDTAB",

            "0", "TABLE",
            "2", "DIMSTYLE",
            "70", "1",
            "0", "DIMSTYLE",
            "2", "STANDARD",
            "70", "0",
            "0", "ENDTAB",

            "0", "ENDSEC",
        ]

        return out

    def rebuild_r12_safe(raw):
        report = {
            "mode": "CBL_DWG_SAVE_R12_SAFE_V10",
            "sections_found": [],
            "kept_sections": [],
            "layer_count": 0,
            "style_count": 0,
            "block_count": 0,
            "group6_refs_fixed": 0,
            "layer_refs_fixed": 0,
            "empty_style_refs_fixed": 0,
            "color_refs_fixed": 0,
            "acadver": "AC1009",
        }

        lines = read_lines(raw)
        sections = find_sections(lines)
        report["sections_found"] = [s["name"] for s in sections]

        blocks = []
        ents = []

        for s in sections:
            if s["name"] == "BLOCKS":
                blocks.append(clean_blocks_entities(s["lines"], report))
                report["kept_sections"].append("BLOCKS")
            elif s["name"] == "ENTITIES":
                ents.append(clean_blocks_entities(s["lines"], report))
                report["kept_sections"].append("ENTITIES")

        if not ents:
            raise ValueError("ENTITIES SECTION을 찾지 못했습니다.")

        all_kept = []
        for x in blocks + ents:
            all_kept += x

        layers, styles, block_names = collect_names(all_kept)
        report["layer_count"] = len(layers)
        report["style_count"] = len(styles)
        report["block_count"] = len(block_names)

        out = []
        out += build_header()
        out += build_tables(layers, styles)

        for b in blocks:
            out += b

        for e in ents:
            out += e

        out += ["0", "EOF"]

        text = "\r\n".join(out) + "\r\n"
        return text.encode("utf-8", errors="replace"), report

    def read_text_file_limited(path, limit=50000):
        try:
            data = Path(path).read_bytes()
            if len(data) > limit:
                data = data[:limit] + b"\n\n--- TRUNCATED ---\n"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return "ERR_READ_FAILED: " + repr(e)

    def collect_listing(root):
        items = []
        try:
            root = Path(root)
            for p in root.rglob("*"):
                if p.is_file():
                    items.append({"path": str(p.relative_to(root)), "size": p.stat().st_size})
        except Exception:
            pass
        return items[:200]

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"
    attempts = []
    success = False
    sanitize_report = {}

    try:
        dxf_bytes, sanitize_report = rebuild_r12_safe(dxf_bytes)
        input_dxf.write_bytes(dxf_bytes)

        # R12-safe DXF는 ACAD2004 DWG가 제일 안정적. 실패 시 2004/2013 순서.
        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP

        for version in versions:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [converter, str(input_dir), str(output_dir), version, "DWG", "0", "0"]

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)

                found_dwgs = [str(p) for p in tmp_root.rglob("*") if p.is_file() and p.suffix.lower() == ".dwg"]

                err_files = []
                for p in tmp_root.rglob("*.err"):
                    err_files.append({
                        "path": str(p.relative_to(tmp_root)),
                        "size": p.stat().st_size,
                        "text": read_text_file_limited(p),
                    })

                attempts.append({
                    "version": version,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-3000:] if proc.stdout else "",
                    "stderr": proc.stderr[-3000:] if proc.stderr else "",
                    "found": found_dwgs,
                    "err_files": err_files,
                    "sanitize_report": sanitize_report,
                    "input_exists": input_dxf.exists(),
                    "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                    "output_dir": str(output_dir),
                    "output_listing": collect_listing(tmp_root),
                })

                if found_dwgs:
                    found_paths = [Path(x) for x in found_dwgs]
                    found_paths.sort(key=lambda p: (p.stat().st_size, p.stat().st_mtime), reverse=True)

                    dwg_path = found_paths[0]
                    dwg_bytes = dwg_path.read_bytes()

                    if len(dwg_bytes) >= 100:
                        success = True
                        response = HttpResponse(dwg_bytes, content_type="application/octet-stream")
                        quoted = urllib.parse.quote(filename)
                        ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
                        response["Content-Disposition"] = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
                        response["X-CBL-DWG-Version"] = version
                        response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                        response["X-CBL-DXF-Sanitize"] = json.dumps(sanitize_report, ensure_ascii=False)
                        return response

            except subprocess.TimeoutExpired as e:
                attempts.append({
                    "version": version,
                    "returncode": "timeout",
                    "stderr": str(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })
            except Exception as e:
                attempts.append({
                    "version": version,
                    "returncode": "exception",
                    "stderr": repr(e),
                    "found": [],
                    "err_files": [],
                    "sanitize_report": sanitize_report,
                    "output_listing": collect_listing(tmp_root),
                })

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir_path = debug_root / f"fail_{stamp}"
            shutil.copytree(tmp_root, debug_dir_path, dirs_exist_ok=True)

            summary_path = debug_dir_path / "READ_ME_ODA_ERROR.txt"
            lines2 = ["ChickenBananaCAD ODA 변환 실패 디버그", "", "1) ODA ERR 파일:"]
            for err in (debug_dir_path / "output").glob("*.err"):
                lines2.append(f"   - {err}")
                lines2.append("")
                lines2.append(err.read_text(encoding="utf-8", errors="replace"))
                lines2.append("")
            lines2 += ["", "2) sanitize_report:", json.dumps(sanitize_report, ensure_ascii=False, indent=2)]
            summary_path.write_text("\n".join(lines2), encoding="utf-8")
            debug_dir = str(debug_dir_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "reason": "V10: R12-safe DXF로 재구성했지만 ODA 변환 실패. READ_ME_ODA_ERROR.txt 확인 필요.",
            "converter": converter,
            "debug_dir": debug_dir,
            "sanitize_report": sanitize_report,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_DWG_ORIGINAL_RAW_PASSTHROUGH_V13
# 목적:
# - 저장용 DXF를 재작성하지 않음
# - 브라우저가 가진 원본 DXF 원문을 그대로 ODA에 넣어 DWG 변환
# - 레이어/TABLES/BLOCKS/ENTITIES 원본 정보 보존 확인용
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None
    save_mode = ""

    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )

        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name

        save_mode = request.POST.get("cblSaveMode") or ""
    except Exception:
        pass

    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)

                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )

                dxf_text = (
                    data.get("dxfText")
                    or data.get("dxf")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )

                save_mode = data.get("cblSaveMode") or save_mode

                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )

            dxf_text = (
                request.POST.get("dxfText")
                or request.POST.get("dxf")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )

            save_mode = request.POST.get("cblSaveMode") or save_mode

            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({
            "ok": False,
            "error": "DXF 데이터가 비어 있습니다."
        }, status=400)

    # 최소 정리만 한다. TABLES/BLOCKS/ENTITIES 재작성 금지.
    if dxf_bytes.startswith(b"\xef\xbb\xbf"):
        dxf_bytes = dxf_bytes[3:]

    dxf_bytes = dxf_bytes.replace(b"\x00", b"")

    # 줄바꿈만 CRLF로 통일
    dxf_bytes = dxf_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")

    tail = dxf_bytes[-500:].upper()
    if b"\r\n0\r\nEOF" not in tail and b"\n0\nEOF" not in tail:
        if not dxf_bytes.endswith(b"\r\n"):
            dxf_bytes += b"\r\n"
        dxf_bytes += b"0\r\nEOF\r\n"

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_raw_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"
    input_dxf.write_bytes(dxf_bytes)

    attempts = []
    success = False

    def read_text_file_limited(path, limit=50000):
        try:
            data = Path(path).read_bytes()
            if len(data) > limit:
                data = data[:limit] + b"\n\n--- TRUNCATED ---\n"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return "ERR_READ_FAILED: " + repr(e)

    def collect_listing(root):
        items = []
        try:
            root = Path(root)
            for p in root.rglob("*"):
                if p.is_file():
                    items.append({
                        "path": str(p.relative_to(root)),
                        "size": p.stat().st_size
                    })
        except Exception:
            pass
        return items[:200]

    try:
        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23

        for version in versions:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

            found_dwgs = [
                str(p)
                for p in tmp_root.rglob("*")
                if p.is_file() and p.suffix.lower() == ".dwg"
            ]

            err_files = []
            for p in tmp_root.rglob("*.err"):
                err_files.append({
                    "path": str(p.relative_to(tmp_root)),
                    "size": p.stat().st_size,
                    "text": read_text_file_limited(p),
                })

            attempts.append({
                "version": version,
                "cmd": cmd,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-3000:] if proc.stdout else "",
                "stderr": proc.stderr[-3000:] if proc.stderr else "",
                "found": found_dwgs,
                "err_files": err_files,
                "save_mode": save_mode,
                "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                "output_listing": collect_listing(tmp_root),
            })

            if found_dwgs:
                found_paths = [Path(x) for x in found_dwgs]
                found_paths.sort(
                    key=lambda p: (p.stat().st_size, p.stat().st_mtime),
                    reverse=True
                )

                dwg_path = found_paths[0]
                dwg_bytes = dwg_path.read_bytes()

                if len(dwg_bytes) >= 100:
                    success = True
                    response = HttpResponse(
                        dwg_bytes,
                        content_type="application/octet-stream"
                    )
                    quoted = urllib.parse.quote(filename)
                    ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
                    response["Content-Disposition"] = (
                        f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
                    )
                    response["X-CBL-DWG-Version"] = version
                    response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                    response["X-CBL-Save-Mode"] = "original_raw_passthrough_v13"
                    return response

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir_path = debug_root / f"fail_raw_v13_{stamp}"
            shutil.copytree(tmp_root, debug_dir_path, dirs_exist_ok=True)

            summary_path = debug_dir_path / "READ_ME_ODA_ERROR.txt"
            lines = ["ChickenBananaCAD ODA RAW V13 변환 실패", "", "1) ODA ERR 파일:"]
            for err in (debug_dir_path / "output").glob("*.err"):
                lines.append(f"   - {err}")
                lines.append("")
                lines.append(err.read_text(encoding="utf-8", errors="replace"))
                lines.append("")
            lines += ["", "2) attempts:", json.dumps(attempts, ensure_ascii=False, indent=2)]
            summary_path.write_text("\n".join(lines), encoding="utf-8")

            debug_dir = str(debug_dir_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "reason": "V13: 원본 DXF RAW 그대로 ODA 변환 실패. READ_ME_ODA_ERROR.txt 확인 필요.",
            "converter": converter,
            "debug_dir": debug_dir,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_DWG_RAW_TABLE_SURGICAL_FIX_V15
# 목적:
# - V13에서 원본 DXF RAW는 정상 전송됨
# - ODA가 TABLES 안의 이름 없는 LTYPE/LAYER/STYLE 레코드 때문에 실패함
# - 원본 DXF 전체 구조는 보존하고, TABLES 내부 빈 symbol record만 제거/보정
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None
    save_mode = ""

    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )

        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name

        save_mode = request.POST.get("cblSaveMode") or ""
    except Exception:
        pass

    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)

                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )

                dxf_text = (
                    data.get("dxfText")
                    or data.get("dxf")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )

                save_mode = data.get("cblSaveMode") or save_mode

                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )

            dxf_text = (
                request.POST.get("dxfText")
                or request.POST.get("dxf")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )

            save_mode = request.POST.get("cblSaveMode") or save_mode

            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({"ok": False, "error": "DXF 데이터가 비어 있습니다."}, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    def clean(v):
        if v is None:
            return ""
        s = str(v)
        s = s.replace("\ufeff", "")
        s = s.replace("\u200b", "")
        s = s.replace("\u200c", "")
        s = s.replace("\u200d", "")
        s = s.replace("\x00", "")
        return s.strip()

    def read_lines(raw):
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        raw = raw.replace(b"\x00", b"")
        text = raw.decode("utf-8", errors="replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        while lines and lines[-1] == "":
            lines.pop()
        if len(lines) % 2 == 1:
            lines.append("")
        return lines

    def to_bytes(lines):
        while lines and clean(lines[-1]).upper() == "EOF":
            lines.pop()
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["0", "EOF"]
        return ("\r\n".join(str(x) for x in lines) + "\r\n").encode("utf-8", errors="replace")

    def safe_name(v, default):
        s = clean(v)
        if not s:
            return default
        for ch in '<>/"\\:;?*|=`,':
            s = s.replace(ch, "_")
        return s.strip() or default

    def find_sections(lines):
        sections = []
        i = 0
        n = len(lines)

        while i < n - 1:
            if lines[i].strip() == "0" and clean(lines[i + 1]).upper() == "SECTION":
                start = i
                name = ""

                if i + 3 < n and lines[i + 2].strip() == "2":
                    name = clean(lines[i + 3]).upper()

                j = i + 2
                end = None

                while j < n - 1:
                    if lines[j].strip() == "0" and clean(lines[j + 1]).upper() == "ENDSEC":
                        end = j + 2
                        break
                    j += 1

                if end:
                    sections.append({
                        "name": name,
                        "start": start,
                        "end": end,
                        "lines": lines[start:end],
                    })
                    i = end
                    continue

            i += 1

        return sections

    def get_group(record, code):
        code = str(code)
        i = 0
        while i < len(record) - 1:
            if record[i].strip() == code:
                return record[i + 1]
            i += 2
        return None

    def set_group(record, code, value):
        code = str(code)
        out = list(record)
        i = 0
        while i < len(out) - 1:
            if out[i].strip() == code:
                out[i + 1] = str(value)
                return out
            i += 2
        out += [code, str(value)]
        return out

    def normalize_ltype_name(v):
        s = clean(v)
        if not s:
            return ""
        low = s.lower()
        table = {
            "continuous": "CONTINUOUS",
            "solid": "CONTINUOUS",
            "bylayer": "BYLAYER",
            "byblock": "BYBLOCK",
            "cen": "CEN",
            "center": "CEN",
            "d2": "D2",
            "dash": "D2",
            "dashed": "D2",
            "hid": "HID",
            "hidden": "HID",
        }
        return table.get(low, safe_name(s, "CONTINUOUS").upper())

    def make_ltype(name):
        n = normalize_ltype_name(name) or "CONTINUOUS"
        patterns = {
            "BYBLOCK": ("", []),
            "BYLAYER": ("", []),
            "CONTINUOUS": ("Solid line", []),
            "CEN": ("Center ____ _ ____ _ ____", [1.25, -0.25, 0.25, -0.25]),
            "D2": ("Dashed __ __ __ __", [0.5, -0.25]),
            "HID": ("Hidden __ __ __ __", [0.25, -0.125]),
        }

        desc, pattern = patterns.get(n, ("User linetype", []))
        total = sum(abs(float(x)) for x in pattern)

        rec = [
            "0", "LTYPE",
            "2", n,
            "70", "0",
            "3", desc,
            "72", "65",
            "73", str(len(pattern)),
            "40", str(total),
        ]

        for x in pattern:
            rec += ["49", str(x), "74", "0"]

        return rec

    def make_layer(name):
        return [
            "0", "LAYER",
            "2", name,
            "70", "0",
            "62", "7",
            "6", "CONTINUOUS",
        ]

    def make_style(name="STANDARD"):
        return [
            "0", "STYLE",
            "2", name,
            "70", "0",
            "40", "0.0",
            "41", "1.0",
            "50", "0.0",
            "71", "0",
            "42", "2.5",
            "3", "txt",
            "4", "",
        ]

    def collect_entity_layer_names(lines):
        names = set(["0"])
        i = 0
        while i < len(lines) - 1:
            if lines[i].strip() == "8":
                nm = safe_name(lines[i + 1], "")
                if nm:
                    names.add(nm)
            i += 2
        return names

    def split_table_records(table_lines, rec_type):
        rec_type = rec_type.upper()
        header = []
        records = []
        tail = []

        i = 0
        first_record_found = False

        while i < len(table_lines) - 1:
            if table_lines[i].strip() == "0" and clean(table_lines[i + 1]).upper() == rec_type:
                first_record_found = True
                start = i
                j = i + 2
                while j < len(table_lines) - 1:
                    if table_lines[j].strip() == "0":
                        break
                    j += 2
                records.append(table_lines[start:j])
                i = j
                continue

            if first_record_found:
                tail.append(table_lines[i])
            else:
                header.append(table_lines[i])

            i += 1

        while i < len(table_lines):
            if first_record_found:
                tail.append(table_lines[i])
            else:
                header.append(table_lines[i])
            i += 1

        return header, records, tail

    def update_table_count(header, count):
        out = list(header)
        i = 0
        while i < len(out) - 1:
            if out[i].strip() == "70":
                out[i + 1] = str(count)
                return out
            i += 2

        # TABLE 이름 뒤쪽에 count 추가
        insert_at = min(len(out), 4)
        out[insert_at:insert_at] = ["70", str(count)]
        return out

    def clean_symbol_table(table_lines, table_name, entity_layers, report):
        table_name = table_name.upper()
        rec_type = {
            "LTYPE": "LTYPE",
            "LAYER": "LAYER",
            "STYLE": "STYLE",
        }.get(table_name)

        if not rec_type:
            return table_lines

        header, records, tail = split_table_records(table_lines, rec_type)

        cleaned = []
        seen = set()

        for rec in records:
            raw_name = get_group(rec, "2")
            name = clean(raw_name)

            if not name:
                report[f"{table_name.lower()}_empty_records_removed"] += 1
                continue

            if table_name == "LTYPE":
                name = normalize_ltype_name(name)
                if not name:
                    report[f"{table_name.lower()}_empty_records_removed"] += 1
                    continue
                rec = set_group(rec, "2", name)

            elif table_name == "LAYER":
                name = safe_name(name, "")
                if not name:
                    report[f"{table_name.lower()}_empty_records_removed"] += 1
                    continue

                rec = set_group(rec, "2", name)

                if not clean(get_group(rec, "62")):
                    rec = set_group(rec, "62", "7")
                    report["layer_missing_color_fixed"] += 1

                if not clean(get_group(rec, "6")):
                    rec = set_group(rec, "6", "CONTINUOUS")
                    report["layer_missing_ltype_fixed"] += 1
                else:
                    lt = normalize_ltype_name(get_group(rec, "6")) or "CONTINUOUS"
                    rec = set_group(rec, "6", lt)

            elif table_name == "STYLE":
                name = safe_name(name, "")
                if not name:
                    report[f"{table_name.lower()}_empty_records_removed"] += 1
                    continue
                rec = set_group(rec, "2", name)

            key = name.lower()
            if key in seen:
                report[f"{table_name.lower()}_duplicate_records_removed"] += 1
                continue

            seen.add(key)
            cleaned.append(rec)

        if table_name == "LTYPE":
            for need in ["BYBLOCK", "BYLAYER", "CONTINUOUS", "CEN", "D2", "HID"]:
                if need.lower() not in seen:
                    cleaned.insert(0 if need in ["BYBLOCK", "BYLAYER", "CONTINUOUS"] else len(cleaned), make_ltype(need))
                    seen.add(need.lower())
                    report["ltype_missing_basics_added"] += 1

        if table_name == "LAYER":
            for need in sorted(entity_layers):
                key = need.lower()
                if key and key not in seen:
                    cleaned.append(make_layer(need))
                    seen.add(key)
                    report["layer_missing_from_entities_added"] += 1

            if "0" not in seen:
                cleaned.insert(0, make_layer("0"))
                report["layer_zero_added"] += 1

        if table_name == "STYLE":
            if "standard" not in seen:
                cleaned.insert(0, make_style("STANDARD"))
                report["style_standard_added"] += 1

        header = update_table_count(header, len(cleaned))

        out = []
        out += header
        for rec in cleaned:
            out += rec

        # tail에서 ENDTAB만 남김. 이상한 잔여 symbol line 제거.
        if not any(x.strip() == "0" and i + 1 < len(tail) and clean(tail[i + 1]).upper() == "ENDTAB" for i, x in enumerate(tail)):
            out += ["0", "ENDTAB"]
        else:
            # 첫 ENDTAB만 보존
            i = 0
            added = False
            while i < len(tail) - 1:
                if tail[i].strip() == "0" and clean(tail[i + 1]).upper() == "ENDTAB":
                    out += ["0", "ENDTAB"]
                    added = True
                    break
                i += 1
            if not added:
                out += ["0", "ENDTAB"]

        report[f"{table_name.lower()}_final_record_count"] = len(cleaned)
        return out

    def process_tables_section(tables_lines, entity_layers, report):
        out = []
        i = 0

        while i < len(tables_lines) - 1:
            if tables_lines[i].strip() == "0" and clean(tables_lines[i + 1]).upper() == "TABLE":
                start = i
                table_name = ""

                j = i + 2
                while j < len(tables_lines) - 1 and j < i + 40:
                    if tables_lines[j].strip() == "2":
                        table_name = clean(tables_lines[j + 1]).upper()
                        break
                    if tables_lines[j].strip() == "0":
                        break
                    j += 2

                k = i + 2
                end = None
                while k < len(tables_lines) - 1:
                    if tables_lines[k].strip() == "0" and clean(tables_lines[k + 1]).upper() == "ENDTAB":
                        end = k + 2
                        break
                    k += 1

                if end:
                    table = tables_lines[start:end]

                    if table_name in {"LTYPE", "LAYER", "STYLE"}:
                        out += clean_symbol_table(table, table_name, entity_layers, report)
                        report["tables_surgically_fixed"].append(table_name)
                    else:
                        out += table

                    i = end
                    continue

            out.append(tables_lines[i])
            i += 1

        while i < len(tables_lines):
            out.append(tables_lines[i])
            i += 1

        return out

    def surgical_fix_dxf(raw):
        lines = read_lines(raw)
        sections = find_sections(lines)

        report = {
            "mode": "CBL_DWG_RAW_TABLE_SURGICAL_FIX_V15",
            "sections_found": [s["name"] for s in sections],
            "tables_surgically_fixed": [],
            "ltype_empty_records_removed": 0,
            "layer_empty_records_removed": 0,
            "style_empty_records_removed": 0,
            "ltype_duplicate_records_removed": 0,
            "layer_duplicate_records_removed": 0,
            "style_duplicate_records_removed": 0,
            "ltype_missing_basics_added": 0,
            "layer_missing_color_fixed": 0,
            "layer_missing_ltype_fixed": 0,
            "layer_missing_from_entities_added": 0,
            "layer_zero_added": 0,
            "style_standard_added": 0,
            "ltype_final_record_count": 0,
            "layer_final_record_count": 0,
            "style_final_record_count": 0,
            "raw_structure_preserved": True,
        }

        entity_layers = set(["0"])
        for s in sections:
            if s["name"] in {"BLOCKS", "ENTITIES"}:
                entity_layers |= collect_entity_layer_names(s["lines"])

        out = []
        cursor = 0

        for s in sections:
            if s["start"] > cursor:
                out += lines[cursor:s["start"]]

            if s["name"] == "TABLES":
                out += process_tables_section(s["lines"], entity_layers, report)
            else:
                out += s["lines"]

            cursor = s["end"]

        if cursor < len(lines):
            rest = [x for x in lines[cursor:] if clean(x).upper() != "EOF"]
            out += rest

        return to_bytes(out), report

    def read_text_file_limited(path, limit=50000):
        try:
            data = Path(path).read_bytes()
            if len(data) > limit:
                data = data[:limit] + b"\n\n--- TRUNCATED ---\n"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return "ERR_READ_FAILED: " + repr(e)

    def collect_listing(root):
        items = []
        try:
            root = Path(root)
            for p in root.rglob("*"):
                if p.is_file():
                    items.append({
                        "path": str(p.relative_to(root)),
                        "size": p.stat().st_size
                    })
        except Exception:
            pass
        return items[:200]

    try:
        dxf_bytes, sanitize_report = surgical_fix_dxf(dxf_bytes)
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "error": "DXF TABLE 수술 보정 실패",
            "exception": repr(e),
        }, status=500)

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_oda_v15_"))
    input_dir = tmp_root / "input"
    output_dir = tmp_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dxf = input_dir / "cbl_input.dxf"
    input_dxf.write_bytes(dxf_bytes)

    attempts = []
    success = False

    try:
        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23

        for version in versions:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

            found_dwgs = [
                str(p)
                for p in tmp_root.rglob("*")
                if p.is_file() and p.suffix.lower() == ".dwg"
            ]

            err_files = []
            for p in tmp_root.rglob("*.err"):
                err_files.append({
                    "path": str(p.relative_to(tmp_root)),
                    "size": p.stat().st_size,
                    "text": read_text_file_limited(p),
                })

            attempts.append({
                "version": version,
                "cmd": cmd,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-3000:] if proc.stdout else "",
                "stderr": proc.stderr[-3000:] if proc.stderr else "",
                "found": found_dwgs,
                "err_files": err_files,
                "save_mode": save_mode,
                "sanitize_report": sanitize_report,
                "input_size": input_dxf.stat().st_size if input_dxf.exists() else 0,
                "output_listing": collect_listing(tmp_root),
            })

            if found_dwgs:
                found_paths = [Path(x) for x in found_dwgs]
                found_paths.sort(
                    key=lambda p: (p.stat().st_size, p.stat().st_mtime),
                    reverse=True
                )

                dwg_path = found_paths[0]
                dwg_bytes = dwg_path.read_bytes()

                if len(dwg_bytes) >= 100:
                    success = True
                    response = HttpResponse(
                        dwg_bytes,
                        content_type="application/octet-stream"
                    )
                    quoted = urllib.parse.quote(filename)
                    ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
                    response["Content-Disposition"] = (
                        f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
                    )
                    response["X-CBL-DWG-Version"] = version
                    response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
                    response["X-CBL-Save-Mode"] = "raw_table_surgical_fix_v15"
                    response["X-CBL-Sanitize"] = json.dumps(sanitize_report, ensure_ascii=False)
                    return response

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_dir_path = debug_root / f"fail_raw_v15_{stamp}"
            shutil.copytree(tmp_root, debug_dir_path, dirs_exist_ok=True)

            summary_path = debug_dir_path / "READ_ME_ODA_ERROR.txt"
            lines2 = ["ChickenBananaCAD ODA RAW V15 변환 실패", "", "1) ODA ERR 파일:"]
            for err in (debug_dir_path / "output").glob("*.err"):
                lines2.append(f"   - {err}")
                lines2.append("")
                lines2.append(err.read_text(encoding="utf-8", errors="replace"))
                lines2.append("")
            lines2 += ["", "2) sanitize_report:", json.dumps(sanitize_report, ensure_ascii=False, indent=2)]
            lines2 += ["", "3) attempts:", json.dumps(attempts, ensure_ascii=False, indent=2)]
            summary_path.write_text("\n".join(lines2), encoding="utf-8")

            debug_dir = str(debug_dir_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "reason": "V15: 원본 RAW 유지 + 빈 TABLE 레코드 제거 후에도 ODA 변환 실패.",
            "converter": converter,
            "debug_dir": debug_dir,
            "sanitize_report": sanitize_report,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)


# CBL_DWG_SAVE_EZDXF_NORMALIZE_ODA_V17_START
# 목적:
# - HTML/열기/렌더링/속도 패치 건드리지 않음
# - 프론트에서 받은 DXF를 서버에서 ezdxf로 정규화
# - 정규화된 DXF를 ODAFileConverter로 DWG 변환
# - 기존 중복 cblcad_dxf_to_dwg_save_api 함수들보다 아래에 있으므로 이 함수가 최종 사용됨

_CBL_DWG_SAVE_PREV_API_V17 = globals().get("cblcad_dxf_to_dwg_save_api")

try:
    from django.views.decorators.csrf import csrf_exempt as _cbl_csrf_exempt_v17
except Exception:
    def _cbl_csrf_exempt_v17(fn):
        return fn


@_cbl_csrf_exempt_v17
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    MODE = "CBL_DWG_SAVE_EZDXF_NORMALIZE_ODA_V17"
    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only", "mode": MODE}, status=405)

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter를 찾지 못했습니다.",
            "converter": converter,
            "mode": MODE,
        }, status=500)

    filename = "drawing.dwg"
    dxf_bytes = None

    try:
        upload = (
            request.FILES.get("file")
            or request.FILES.get("dxf")
            or request.FILES.get("dxf_file")
            or request.FILES.get("drawing")
        )
        if upload:
            dxf_bytes = upload.read()
            raw_name = getattr(upload, "name", "") or ""
            if raw_name:
                filename = Path(raw_name).with_suffix(".dwg").name
    except Exception:
        pass

    if dxf_bytes is None:
        try:
            body_text = request.body.decode("utf-8", errors="replace")
            if body_text.strip().startswith("{"):
                data = json.loads(body_text)
                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )
                dxf_text = (
                    data.get("dxfText")
                    or data.get("dxf")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None:
        try:
            filename = (
                request.POST.get("filename")
                or request.POST.get("name")
                or request.POST.get("output_filename")
                or request.POST.get("outputName")
                or filename
            )
            dxf_text = (
                request.POST.get("dxfText")
                or request.POST.get("dxf")
                or request.POST.get("text")
                or request.POST.get("content")
                or request.POST.get("raw")
            )
            if dxf_text:
                dxf_bytes = dxf_text.encode("utf-8", errors="replace")
        except Exception:
            pass

    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({
            "ok": False,
            "error": "DXF 데이터가 비어 있습니다.",
            "mode": MODE,
        }, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    def read_text_limited(path, limit=12000):
        try:
            b = Path(path).read_bytes()
            if len(b) > limit:
                b = b[:limit] + b"\n--- TRUNCATED ---\n"
            return b.decode("utf-8", errors="replace")
        except Exception as e:
            return "READ_ERR: " + repr(e)

    def normalize_with_ezdxf(src_path, dst_path):
        report = {
            "enabled": True,
            "ok": False,
            "method": None,
            "error": None,
        }

        try:
            import ezdxf

            try:
                from ezdxf import recover
                doc, auditor = recover.readfile(str(src_path))
                report["method"] = "ezdxf.recover.readfile"
                try:
                    report["recover_errors"] = len(getattr(auditor, "errors", []) or [])
                    report["recover_fixes"] = len(getattr(auditor, "fixes", []) or [])
                except Exception:
                    pass
            except Exception:
                doc = ezdxf.readfile(str(src_path))
                report["method"] = "ezdxf.readfile"

            try:
                doc.audit()
            except Exception:
                pass

            try:
                doc.saveas(str(dst_path), encoding="utf-8")
            except TypeError:
                doc.saveas(str(dst_path))

            if Path(dst_path).exists() and Path(dst_path).stat().st_size > 1000:
                report["ok"] = True
                report["size"] = Path(dst_path).stat().st_size
                return True, report

            report["error"] = "normalized file too small or missing"
            return False, report

        except Exception as e:
            report["error"] = repr(e)
            return False, report

    def convert_with_oda(input_dxf, tmp_root):
        attempts = []
        input_dir = tmp_root / "input"
        output_dir = tmp_root / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        target_input = input_dir / "cbl_input.dxf"
        shutil.copy2(input_dxf, target_input)
        try:
            _cbl_v28a_debug_file_flow("oda_target_input_after_copy", target_input, {"input_dxf": str(input_dxf), "tmp_root": str(tmp_root)})
        except Exception:
            pass

        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23: Korean BigFont preserve

        for version in versions:
            shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
            except Exception as e:
                attempts.append({
                    "version": version,
                    "cmd": cmd,
                    "exception": repr(e),
                })
                continue

            found = [p for p in output_dir.rglob("*.dwg") if p.is_file()]
            try:
                print("🔎 CBL_V28A_ODA_OUTPUT_FOUND:", [{"path": str(p), "size": p.stat().st_size} for p in found])
            except Exception:
                pass

            err_files = []
            for ep in output_dir.rglob("*.err"):
                err_files.append({
                    "path": str(ep.relative_to(output_dir)),
                    "size": ep.stat().st_size,
                    "text": read_text_limited(ep),
                })

            attempts.append({
                "version": version,
                "cmd": cmd,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-3000:],
                "stderr": (proc.stderr or "")[-3000:],
                "found": [str(p) for p in found],
                "err_files": err_files,
            })

            if found:
                found.sort(key=lambda p: (p.stat().st_size, p.stat().st_mtime), reverse=True)
                dwg_path = found[0]
                if dwg_path.stat().st_size > 100:
                    return dwg_path, version, attempts

        return None, None, attempts

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_dwg_v17_"))
    success = False
    raw_input = tmp_root / "raw_input.dxf"
    norm_input = tmp_root / "normalized_by_ezdxf.dxf"

    try:
        raw_input.write_bytes(dxf_bytes)
        try:
            _cbl_v28a_debug_file_flow("raw_input_after_write", raw_input, {"dxf_bytes_len": len(dxf_bytes or b"")})
        except Exception:
            pass

        normalize_ok, normalize_report = normalize_with_ezdxf(raw_input, norm_input)
        source_for_oda = norm_input if normalize_ok else raw_input

        dwg_path, version, attempts = convert_with_oda(source_for_oda, tmp_root)

        if dwg_path and dwg_path.exists():
            dwg_bytes = dwg_path.read_bytes()
            success = True

            response = HttpResponse(dwg_bytes, content_type="application/octet-stream")
            quoted = urllib.parse.quote(filename)
            ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"

            response["Content-Disposition"] = (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
            )
            response["X-CBL-DWG-Version"] = version or ""
            response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
            response["X-CBL-Save-Mode"] = MODE
            response["X-CBL-Normalize"] = json.dumps(normalize_report, ensure_ascii=False)

            return response

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_path = debug_root / f"fail_dwg_save_v17_{stamp}"
            shutil.copytree(tmp_root, debug_path, dirs_exist_ok=True)

            (debug_path / "READ_ME_DWG_SAVE_V17.txt").write_text(
                "ChickenBananaCAD DWG SAVE V17 실패\n\n"
                + "normalize_report:\n"
                + json.dumps(normalize_report, ensure_ascii=False, indent=2)
                + "\n\nattempts:\n"
                + json.dumps(attempts, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            debug_dir = str(debug_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "mode": MODE,
            "converter": converter,
            "normalize_report": normalize_report,
            "debug_dir": debug_dir,
            "attempts": attempts,
        }, status=500)

    finally:
        if success:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_DWG_SAVE_EZDXF_NORMALIZE_ODA_V17_END

# CBL_DWG_SAVE_LWPOLYLINE_BLOCK_RECORD_V18_START
_CBL_DWG_SAVE_PREV_API_V18 = globals().get("cblcad_dxf_to_dwg_save_api")

try:
    from django.views.decorators.csrf import csrf_exempt as _cbl_csrf_exempt_v18
except Exception:
    def _cbl_csrf_exempt_v18(fn):
        return fn


@_cbl_csrf_exempt_v18
def cblcad_dxf_to_dwg_save_api(request):
    import os
    import json
    import shutil
    import tempfile
    import subprocess
    import urllib.parse
    import time
    from pathlib import Path
    from django.http import JsonResponse, HttpResponse
    from django.conf import settings

    MODE = "CBL_DWG_SAVE_LWPOLYLINE_BLOCK_RECORD_V18"
    converter = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only", "mode": MODE}, status=405)

    if not os.path.exists(converter):
        return JsonResponse({
            "ok": False,
            "error": "ODAFileConverter 없음",
            "converter": converter,
            "mode": MODE,
        }, status=500)

    def decode_dxf_bytes(b):
        for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin1"):
            try:
                return b.decode(enc)
            except Exception:
                pass
        return b.decode("utf-8", errors="replace")

    def parse_pairs(text):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        pairs = []
        i = 0
        while i + 1 < len(lines):
            code = lines[i].strip()
            value = lines[i + 1]
            pairs.append((code, value))
            i += 2
        return pairs

    def dump_pairs(pairs):
        out = []
        for c, v in pairs:
            out.append(str(c).strip())
            out.append("" if v is None else str(v))
        return "\n".join(out) + "\n"

    def find_section(pairs, name):
        name = name.upper()
        i = 0
        while i + 1 < len(pairs):
            if pairs[i][0].strip() == "0" and pairs[i][1].strip().upper() == "SECTION":
                if pairs[i + 1][0].strip() == "2" and pairs[i + 1][1].strip().upper() == name:
                    j = i + 2
                    while j < len(pairs):
                        if pairs[j][0].strip() == "0" and pairs[j][1].strip().upper() == "ENDSEC":
                            return i, j
                        j += 1
            i += 1
        return None

    def next_handles(pairs, count):
        used = set()
        for c, v in pairs:
            if c.strip() == "5":
                try:
                    used.add(int(str(v).strip(), 16))
                except Exception:
                    pass
        n = max(used) + 1 if used else 0xA000
        result = []
        while len(result) < count:
            while n in used:
                n += 1
            used.add(n)
            result.append(format(n, "X"))
            n += 1
        return result

    def remove_empty_table_records(pairs, report):
        out = []
        i = 0
        bad_types = {"LTYPE", "LAYER", "STYLE", "BLOCK_RECORD"}
        while i < len(pairs):
            c, v = pairs[i]
            up = str(v).strip().upper()
            if c.strip() == "0" and up in bad_types:
                j = i + 1
                while j < len(pairs) and pairs[j][0].strip() != "0":
                    j += 1
                chunk = pairs[i:j]
                name = ""
                for cc, vv in chunk:
                    if cc.strip() == "2":
                        name = str(vv).strip()
                        break
                if not name:
                    report["empty_table_records_removed"] += 1
                    i = j
                    continue
            out.append(pairs[i])
            i += 1
        return out

    def repair_lwpolylines(pairs, report):
        out = []
        i = 0
        while i < len(pairs):
            c, v = pairs[i]
            if c.strip() == "0" and str(v).strip().upper() == "LWPOLYLINE":
                j = i + 1
                while j < len(pairs) and pairs[j][0].strip() != "0":
                    j += 1

                chunk = pairs[i:j]
                values100 = [str(vv).strip() for cc, vv in chunk if cc.strip() == "100"]
                has_entity = "AcDbEntity" in values100
                has_poly = "AcDbPolyline" in values100
                has_layer = any(cc.strip() == "8" and str(vv).strip() for cc, vv in chunk)
                has_90 = any(cc.strip() == "90" for cc, vv in chunk)
                vertex_count = sum(1 for cc, vv in chunk if cc.strip() == "10")

                new_chunk = [chunk[0]]

                if not has_entity:
                    new_chunk.append(("100", "AcDbEntity"))
                    report["lwpolyline_acdbentity_added"] += 1

                if not has_layer:
                    new_chunk.append(("8", "0"))
                    report["lwpolyline_layer_added"] += 1

                if not has_poly:
                    new_chunk.append(("100", "AcDbPolyline"))
                    report["lwpolyline_acdbpolyline_added"] += 1

                if not has_90 and vertex_count > 0:
                    new_chunk.append(("90", str(vertex_count)))
                    report["lwpolyline_vertex_count_added"] += 1

                new_chunk.extend(chunk[1:])
                out.extend(new_chunk)
                i = j
            else:
                out.append(pairs[i])
                i += 1
        return out

    def ensure_tables_and_blocks(pairs, report):
        # TABLES 섹션 없으면 ENTITIES 앞에 추가
        if find_section(pairs, "TABLES") is None:
            insert_at = len(pairs)
            ent = find_section(pairs, "ENTITIES")
            if ent:
                insert_at = ent[0]
            else:
                for idx, (c, v) in enumerate(pairs):
                    if c.strip() == "0" and str(v).strip().upper() == "EOF":
                        insert_at = idx
                        break
            pairs[insert_at:insert_at] = [
                ("0", "SECTION"),
                ("2", "TABLES"),
                ("0", "ENDSEC"),
            ]
            report["tables_section_added"] = True

        sec = find_section(pairs, "TABLES")
        if sec:
            s, e = sec
            tables_chunk = pairs[s:e + 1]
            has_block_record = any(
                c.strip() == "0" and str(v).strip().upper() == "BLOCK_RECORD"
                for c, v in tables_chunk
            )
            if not has_block_record:
                h_table, h_model, h_paper = next_handles(pairs, 3)
                block_record_table = [
                    ("0", "TABLE"),
                    ("2", "BLOCK_RECORD"),
                    ("5", h_table),
                    ("100", "AcDbSymbolTable"),
                    ("70", "2"),

                    ("0", "BLOCK_RECORD"),
                    ("5", h_model),
                    ("330", h_table),
                    ("100", "AcDbSymbolTableRecord"),
                    ("100", "AcDbBlockTableRecord"),
                    ("2", "*Model_Space"),
                    ("340", "0"),

                    ("0", "BLOCK_RECORD"),
                    ("5", h_paper),
                    ("330", h_table),
                    ("100", "AcDbSymbolTableRecord"),
                    ("100", "AcDbBlockTableRecord"),
                    ("2", "*Paper_Space"),
                    ("340", "0"),

                    ("0", "ENDTAB"),
                ]
                pairs[e:e] = block_record_table
                report["block_record_table_added"] = True
                report["model_record_handle"] = h_model
                report["paper_record_handle"] = h_paper

        # BLOCKS 섹션 없으면 ENTITIES 앞에 추가
        if find_section(pairs, "BLOCKS") is None:
            insert_at = len(pairs)
            ent = find_section(pairs, "ENTITIES")
            if ent:
                insert_at = ent[0]
            else:
                for idx, (c, v) in enumerate(pairs):
                    if c.strip() == "0" and str(v).strip().upper() == "EOF":
                        insert_at = idx
                        break
            pairs[insert_at:insert_at] = [
                ("0", "SECTION"),
                ("2", "BLOCKS"),
                ("0", "ENDSEC"),
            ]
            report["blocks_section_added"] = True

        sec = find_section(pairs, "BLOCKS")
        if sec:
            s, e = sec
            blocks_chunk = pairs[s:e + 1]
            names = [
                str(v).strip()
                for c, v in blocks_chunk
                if c.strip() in {"2", "3"}
            ]

            need_model = "*Model_Space" not in names
            need_paper = "*Paper_Space" not in names

            add = []
            handles = next_handles(pairs, 8)
            hi = 0

            model_rec = report.get("model_record_handle", "0")
            paper_rec = report.get("paper_record_handle", "0")

            def block_def(name, rec_handle):
                nonlocal hi
                h_block = handles[hi]; hi += 1
                h_end = handles[hi]; hi += 1
                return [
                    ("0", "BLOCK"),
                    ("5", h_block),
                    ("330", rec_handle),
                    ("100", "AcDbEntity"),
                    ("8", "0"),
                    ("100", "AcDbBlockBegin"),
                    ("2", name),
                    ("70", "0"),
                    ("10", "0.0"),
                    ("20", "0.0"),
                    ("30", "0.0"),
                    ("3", name),
                    ("1", ""),

                    ("0", "ENDBLK"),
                    ("5", h_end),
                    ("330", rec_handle),
                    ("100", "AcDbEntity"),
                    ("8", "0"),
                    ("100", "AcDbBlockEnd"),
                ]

            if need_model:
                add.extend(block_def("*Model_Space", model_rec))
                report["model_space_block_added"] = True

            if need_paper:
                add.extend(block_def("*Paper_Space", paper_rec))
                report["paper_space_block_added"] = True

            if add:
                pairs[e:e] = add

        return pairs

    def sanitize_dxf_for_oda_v18(dxf_bytes):
        text = decode_dxf_bytes(dxf_bytes)
        pairs = parse_pairs(text)

        report = {
            "mode": MODE,
            "input_bytes": len(dxf_bytes),
            "input_pairs": len(pairs),
            "empty_table_records_removed": 0,
            "lwpolyline_acdbentity_added": 0,
            "lwpolyline_acdbpolyline_added": 0,
            "lwpolyline_layer_added": 0,
            "lwpolyline_vertex_count_added": 0,
            "tables_section_added": False,
            "blocks_section_added": False,
            "block_record_table_added": False,
            "model_space_block_added": False,
            "paper_space_block_added": False,
        }

        pairs = remove_empty_table_records(pairs, report)
        pairs = repair_lwpolylines(pairs, report)
        pairs = ensure_tables_and_blocks(pairs, report)

        # EOF 보장
        if not any(c.strip() == "0" and str(v).strip().upper() == "EOF" for c, v in pairs):
            pairs.append(("0", "EOF"))
            report["eof_added"] = True
        else:
            report["eof_added"] = False

        out_text = dump_pairs(pairs)
        report["output_chars"] = len(out_text)
        report["output_pairs"] = len(pairs)
        return out_text.encode("utf-8", errors="replace"), report

    def read_text_limited(path, limit=12000):
        try:
            b = Path(path).read_bytes()
            if len(b) > limit:
                b = b[:limit] + b"\n--- TRUNCATED ---\n"
            return b.decode("utf-8", errors="replace")
        except Exception as e:
            return "READ_ERR: " + repr(e)

    def normalize_with_ezdxf(src_path, dst_path):
        report = {"enabled": True, "ok": False, "method": None, "error": None}
        try:
            import ezdxf
            try:
                from ezdxf import recover
                doc, auditor = recover.readfile(str(src_path))
                report["method"] = "ezdxf.recover.readfile"
                try:
                    report["recover_errors"] = len(getattr(auditor, "errors", []) or [])
                    report["recover_fixes"] = len(getattr(auditor, "fixes", []) or [])
                except Exception:
                    pass
            except Exception:
                doc = ezdxf.readfile(str(src_path))
                report["method"] = "ezdxf.readfile"

            try:
                doc.audit()
            except Exception:
                pass

            try:
                doc.saveas(str(dst_path), encoding="utf-8")
            except TypeError:
                doc.saveas(str(dst_path))

            if Path(dst_path).exists() and Path(dst_path).stat().st_size > 1000:
                report["ok"] = True
                report["size"] = Path(dst_path).stat().st_size
                return True, report

            report["error"] = "normalized file too small or missing"
            return False, report

        except Exception as e:
            report["error"] = repr(e)
            return False, report

    def convert_with_oda(input_dxf, tmp_root):
        attempts = []
        input_dir = tmp_root / "input"
        output_dir = tmp_root / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        target_input = input_dir / "cbl_input.dxf"
        shutil.copy2(input_dxf, target_input)
        try:
            _cbl_v28a_debug_file_flow("oda_target_input_after_copy", target_input, {"input_dxf": str(input_dxf), "tmp_root": str(tmp_root)})
        except Exception:
            pass

        versions = ["ACAD2004"]  # CBL_V27_ACAD2004_SWEEP  # CBL_DWG_SAVE_ACAD2004_V23: Korean BigFont preserve

        for version in versions:
            shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                converter,
                str(input_dir),
                str(output_dir),
                version,
                "DWG",
                "0",
                "0",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
            except Exception as e:
                attempts.append({"version": version, "cmd": cmd, "exception": repr(e)})
                continue

            found = [p for p in output_dir.rglob("*.dwg") if p.is_file()]
            try:
                print("🔎 CBL_V28A_ODA_OUTPUT_FOUND:", [{"path": str(p), "size": p.stat().st_size} for p in found])
            except Exception:
                pass

            err_files = []
            for ep in output_dir.rglob("*.err"):
                err_files.append({
                    "path": str(ep.relative_to(output_dir)),
                    "size": ep.stat().st_size,
                    "text": read_text_limited(ep),
                })

            attempts.append({
                "version": version,
                "cmd": cmd,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-3000:],
                "stderr": (proc.stderr or "")[-3000:],
                "found": [str(p) for p in found],
                "err_files": err_files,
            })

            if found:
                found.sort(key=lambda p: (p.stat().st_size, p.stat().st_mtime), reverse=True)
                dwg_path = found[0]
                if dwg_path.stat().st_size > 100:
                    return dwg_path, version, attempts

        return None, None, attempts

    filename = "drawing.dwg"
    dxf_bytes = None

    upload = (
        request.FILES.get("file")
        or request.FILES.get("dxf")
        or request.FILES.get("dxf_file")
        or request.FILES.get("drawing")
    )

    if upload:
        dxf_bytes = upload.read()
        raw_name = getattr(upload, "name", "") or ""
        if raw_name:
            filename = Path(raw_name).with_suffix(".dwg").name

    if dxf_bytes is None:
        body = request.body or b""
        body_text = body.decode("utf-8", errors="replace")
        if body_text.strip().startswith("{"):
            try:
                data = json.loads(body_text)
                filename = (
                    data.get("filename")
                    or data.get("name")
                    or data.get("output_filename")
                    or data.get("outputName")
                    or filename
                )
                dxf_text = (
                    data.get("dxfText")
                    or data.get("dxf")
                    or data.get("text")
                    or data.get("content")
                    or data.get("raw")
                )
                if dxf_text is not None:
                    dxf_bytes = str(dxf_text).encode("utf-8", errors="replace")
            except Exception:
                pass

    if dxf_bytes is None:
        filename = (
            request.POST.get("filename")
            or request.POST.get("name")
            or request.POST.get("output_filename")
            or request.POST.get("outputName")
            or filename
        )
        dxf_text = (
            request.POST.get("dxfText")
            or request.POST.get("dxf")
            or request.POST.get("text")
            or request.POST.get("content")
            or request.POST.get("raw")
        )
        if dxf_text:
            dxf_bytes = dxf_text.encode("utf-8", errors="replace")

    if dxf_bytes is None and request.body:
        dxf_bytes = request.body

    if not dxf_bytes:
        return JsonResponse({"ok": False, "error": "DXF 데이터 비어 있음", "mode": MODE}, status=400)

    filename = Path(str(filename)).with_suffix(".dwg").name
    if not filename or filename == ".dwg":
        filename = "drawing.dwg"

    tmp_root = Path(tempfile.mkdtemp(prefix="cblcad_dwg_v18_"))
    success = False

    try:
        raw_input = tmp_root / "raw_input.dxf"
        sanitized_input = tmp_root / "sanitized_v18.dxf"
        normalized_input = tmp_root / "normalized_by_ezdxf_v18.dxf"

        raw_input.write_bytes(dxf_bytes)
        try:
            _cbl_v28a_debug_file_flow("raw_input_after_write", raw_input, {"dxf_bytes_len": len(dxf_bytes or b"")})
        except Exception:
            pass

        sanitized_bytes, sanitize_report = sanitize_dxf_for_oda_v18(dxf_bytes)
        sanitized_input.write_bytes(sanitized_bytes)
        try:
            _cbl_v28a_debug_file_flow("sanitized_input_after_write", sanitized_input, {"sanitized_bytes_len": len(sanitized_bytes or b""), "sanitize_report": sanitize_report})
        except Exception:
            pass

        normalize_ok, normalize_report = normalize_with_ezdxf(sanitized_input, normalized_input)
        source_for_oda = normalized_input if normalize_ok else sanitized_input
        try:
            _cbl_v28a_debug_file_flow("source_for_oda_selected", source_for_oda, {"normalize_ok": normalize_ok, "normalize_report": normalize_report})
        except Exception:
            pass

        dwg_path, version, attempts = convert_with_oda(source_for_oda, tmp_root)

        if dwg_path and dwg_path.exists():
            dwg_bytes = dwg_path.read_bytes()
            success = True

            response = HttpResponse(dwg_bytes, content_type="application/octet-stream")
            quoted = urllib.parse.quote(filename)
            ascii_name = filename.encode("ascii", "ignore").decode() or "drawing.dwg"
            response["Content-Disposition"] = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
            response["X-CBL-Save-Mode"] = MODE
            response["X-CBL-DWG-Version"] = version or ""
            response["X-CBL-DWG-Size"] = str(len(dwg_bytes))
            response["X-CBL-Sanitize"] = json.dumps(sanitize_report, ensure_ascii=False)
            response["X-CBL-Normalize"] = json.dumps(normalize_report, ensure_ascii=False)
            return response

        debug_dir = None
        try:
            base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
            debug_root = base_dir / "_cblcad_oda_debug"
            debug_root.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            debug_path = debug_root / f"fail_dwg_save_v18_{stamp}"
            shutil.copytree(tmp_root, debug_path, dirs_exist_ok=True)

            (debug_path / "READ_ME_DWG_SAVE_V18.txt").write_text(
                "ChickenBananaCAD DWG SAVE V18 실패\n\n"
                + "sanitize_report:\n"
                + json.dumps(sanitize_report, ensure_ascii=False, indent=2)
                + "\n\nnormalize_report:\n"
                + json.dumps(normalize_report, ensure_ascii=False, indent=2)
                + "\n\nattempts:\n"
                + json.dumps(attempts, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            debug_dir = str(debug_path)
        except Exception as e:
            debug_dir = "DEBUG_SAVE_FAILED: " + repr(e)

        return JsonResponse({
            "ok": False,
            "error": "DXF -> DWG 변환 실패",
            "mode": MODE,
            "converter": converter,
            "sanitize_report": sanitize_report,
            "normalize_report": normalize_report,
            "debug_dir": debug_dir,
            "attempts": attempts,
        }, status=500)

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

# CBL_DWG_SAVE_LWPOLYLINE_BLOCK_RECORD_V18_END



# CBL_DWG_SERVER_RAW_CACHE_V21_START
# 목적:
# - DWG 열기 때 생성된 원본 DXF를 서버 캐시에 저장
# - 브라우저는 raw_id만 들고 있음
# - DWG 저장 때 raw_id + 작은 client DXF를 서버에서 병합 후 기존 V18 저장 API로 전달
try:
    _CBL_V21_PREV_DWG_TO_DXF_API = cblcad_dwg_to_dxf_api
except Exception:
    _CBL_V21_PREV_DWG_TO_DXF_API = None

try:
    _CBL_V21_PREV_DXF_TO_DWG_API = cblcad_dxf_to_dwg_save_api
except Exception:
    _CBL_V21_PREV_DXF_TO_DWG_API = None


def _cbl_v21_cache_dir():
    from pathlib import Path
    import os
    try:
        from django.conf import settings
        base = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    except Exception:
        base = Path.cwd()

    d = base / "tmp" / "cblcad_raw_cache_v21"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cbl_v21_safe_raw_id(raw_id):
    import re
    raw_id = str(raw_id or "").strip()
    if not re.match(r"^[0-9a-fA-F]{16,64}$", raw_id):
        return ""
    return raw_id


def _cbl_v21_cache_paths(raw_id):
    raw_id = _cbl_v21_safe_raw_id(raw_id)
    if not raw_id:
        return None, None
    d = _cbl_v21_cache_dir()
    return d / (raw_id + ".dxf"), d / (raw_id + ".json")


def _cbl_v21_cleanup_cache(max_age_hours=12):
    import time
    d = _cbl_v21_cache_dir()
    now = time.time()
    max_age = float(max_age_hours) * 3600.0

    for path in d.glob("*"):
        try:
            if now - path.stat().st_mtime > max_age:
                path.unlink(missing_ok=True)
        except Exception:
            pass


def _cbl_v21_decode_dxf_bytes(data):
    data = data or b""
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(enc), enc
        except Exception:
            pass
    return data.decode("latin1", errors="replace"), "latin1"


def _cbl_v21_encode_dxf_text(text, enc):
    enc = enc or "utf-8"
    try:
        if enc == "utf-8-sig":
            enc = "utf-8"
        return str(text or "").encode(enc, errors="replace")
    except Exception:
        return str(text or "").encode("utf-8", errors="replace")


def _cbl_v21_norm(text):
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _cbl_v21_lines(text):
    return _cbl_v21_norm(text).split("\n")


def _cbl_v21_trim(v):
    return str(v or "").strip()


def _cbl_v21_get_pair_value(block_lines, code):
    code = str(code)
    for i in range(0, len(block_lines) - 1):
        if _cbl_v21_trim(block_lines[i]) == code:
            return _cbl_v21_trim(block_lines[i + 1])
    return ""


def _cbl_v21_parse_layer_names(dxf_text):
    lines = _cbl_v21_lines(dxf_text)
    layers = set()

    for i in range(0, len(lines) - 1):
        if _cbl_v21_trim(lines[i]) == "0" and _cbl_v21_trim(lines[i + 1]).upper() == "LAYER":
            block = lines[i:i + 80]
            name = _cbl_v21_get_pair_value(block, "2")
            if name:
                layers.add(name.upper())

    layers.add("0")
    return layers


def _cbl_v21_extract_layer_records(dxf_text):
    lines = _cbl_v21_lines(dxf_text)
    records = {}

    i = 0
    while i < len(lines) - 1:
        if _cbl_v21_trim(lines[i]) == "0" and _cbl_v21_trim(lines[i + 1]).upper() == "LAYER":
            start = i
            j = i + 2
            while j < len(lines) - 1:
                if _cbl_v21_trim(lines[j]) == "0":
                    break
                j += 1

            block = lines[start:j]
            name = _cbl_v21_get_pair_value(block, "2")
            if name:
                records[name.upper()] = "\n".join(block).rstrip() + "\n"
            i = j
            continue
        i += 1

    return records


def _cbl_v21_make_layer_record(name):
    import uuid
    name = str(name or "0").strip() or "0"
    handle = uuid.uuid4().hex[:8].upper()
    return "\n".join([
        "0", "LAYER",
        "5", handle,
        "330", "2",
        "100", "AcDbSymbolTableRecord",
        "100", "AcDbLayerTableRecord",
        "2", name,
        "70", "0",
        "62", "7",
        "6", "Continuous",
    ]) + "\n"


def _cbl_v21_insert_missing_layers(base_text, missing_layers, client_layer_records):
    base_text = _cbl_v21_norm(base_text)
    if not missing_layers:
        return base_text, 0

    add = ""
    for layer_upper, layer_name in missing_layers.items():
        rec = client_layer_records.get(layer_upper)
        if not rec:
            rec = _cbl_v21_make_layer_record(layer_name)
        add += rec.rstrip() + "\n"

    lines = _cbl_v21_lines(base_text)
    insert_at = -1

    # TABLE/LAYER 내부 ENDTAB 직전
    for i in range(0, len(lines) - 5):
        if (
            _cbl_v21_trim(lines[i]) == "0"
            and _cbl_v21_trim(lines[i + 1]).upper() == "TABLE"
            and _cbl_v21_trim(lines[i + 2]) == "2"
            and _cbl_v21_trim(lines[i + 3]).upper() == "LAYER"
        ):
            for j in range(i + 4, len(lines) - 1):
                if _cbl_v21_trim(lines[j]) == "0" and _cbl_v21_trim(lines[j + 1]).upper() == "ENDTAB":
                    insert_at = j
                    break
            break

    if insert_at >= 0:
        lines.insert(insert_at, add.rstrip())
        return "\n".join(lines), len(missing_layers)

    # LAYER TABLE이 없으면 ENTITIES 앞에 최소 TABLES 추가
    table = "\n".join([
        "0", "SECTION",
        "2", "TABLES",
        "0", "TABLE",
        "2", "LAYER",
        "70", str(len(missing_layers) + 1),
        _cbl_v21_make_layer_record("0").rstrip(),
        add.rstrip(),
        "0", "ENDTAB",
        "0", "ENDSEC",
    ]) + "\n"

    idx = base_text.upper().find("\n2\nENTITIES")
    if idx >= 0:
        sec_idx = base_text.rfind("\n0\nSECTION", 0, idx)
        if sec_idx >= 0:
            return base_text[:sec_idx] + "\n" + table + base_text[sec_idx:], len(missing_layers)

    return table + base_text, len(missing_layers)


def _cbl_v21_extract_entities(dxf_text):
    lines = _cbl_v21_lines(dxf_text)

    # ENTITIES 섹션 범위 찾기
    ent_start = -1
    ent_end = -1

    for i in range(0, len(lines) - 3):
        if (
            _cbl_v21_trim(lines[i]) == "0"
            and _cbl_v21_trim(lines[i + 1]).upper() == "SECTION"
            and _cbl_v21_trim(lines[i + 2]) == "2"
            and _cbl_v21_trim(lines[i + 3]).upper() == "ENTITIES"
        ):
            ent_start = i + 4
            break

    if ent_start < 0:
        return []

    for j in range(ent_start, len(lines) - 1):
        if _cbl_v21_trim(lines[j]) == "0" and _cbl_v21_trim(lines[j + 1]).upper() == "ENDSEC":
            ent_end = j
            break

    if ent_end < 0:
        ent_end = len(lines)

    entity_types = {
        "LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC",
        "TEXT", "MTEXT", "DIMENSION", "INSERT", "HATCH",
        "ELLIPSE", "SPLINE", "POINT", "SOLID", "TRACE",
        "3DFACE", "LEADER", "MLEADER", "XLINE", "RAY",
        "IMAGE", "WIPEOUT"
    }

    out = []
    i = ent_start
    while i < ent_end - 1:
        if _cbl_v21_trim(lines[i]) == "0":
            typ = _cbl_v21_trim(lines[i + 1]).upper()
            if typ in entity_types:
                start = i
                k = i + 2
                while k < ent_end - 1:
                    if _cbl_v21_trim(lines[k]) == "0" and _cbl_v21_trim(lines[k + 1]).upper() in entity_types.union({"ENDSEC"}):
                        break
                    k += 1

                block = lines[start:k]
                layer = _cbl_v21_get_pair_value(block, "8") or "0"
                out.append({
                    "type": typ,
                    "layer": layer,
                    "layer_upper": layer.upper(),
                    "text": "\n".join(block).rstrip() + "\n",
                })
                i = k
                continue
        i += 1

    return out


def _cbl_v21_insert_entities(base_text, entity_text):
    base_text = _cbl_v21_norm(base_text)
    entity_text = _cbl_v21_norm(entity_text or "").strip()
    if not entity_text:
        return base_text, 0

    lines = _cbl_v21_lines(base_text)
    ent_start = -1
    insert_at = -1

    for i in range(0, len(lines) - 3):
        if (
            _cbl_v21_trim(lines[i]) == "0"
            and _cbl_v21_trim(lines[i + 1]).upper() == "SECTION"
            and _cbl_v21_trim(lines[i + 2]) == "2"
            and _cbl_v21_trim(lines[i + 3]).upper() == "ENTITIES"
        ):
            ent_start = i + 4
            break

    if ent_start >= 0:
        for j in range(ent_start, len(lines) - 1):
            if _cbl_v21_trim(lines[j]) == "0" and _cbl_v21_trim(lines[j + 1]).upper() == "ENDSEC":
                insert_at = j
                break

    if insert_at >= 0:
        lines.insert(insert_at, entity_text)
        return "\n".join(lines), entity_text.count("\n0\n")

    extra = "\n".join([
        "0", "SECTION",
        "2", "ENTITIES",
        entity_text,
        "0", "ENDSEC",
    ]) + "\n"

    eof = base_text.upper().rfind("\n0\nEOF")
    if eof >= 0:
        return base_text[:eof] + "\n" + extra + base_text[eof:], entity_text.count("\n0\n")

    return base_text.rstrip() + "\n" + extra + "0\nEOF\n", entity_text.count("\n0\n")


def _cbl_v21_read_client_dxf_from_request(request):
    try:
        f = request.FILES.get("file") or request.FILES.get("dxf")
        if f:
            data = f.read()
            try:
                f.seek(0)
            except Exception:
                pass
            return data
    except Exception:
        pass

    try:
        txt = request.POST.get("dxf_text") or request.POST.get("dxf") or ""
        if txt:
            return str(txt).encode("utf-8", errors="replace")
    except Exception:
        pass

    return b""


def _cbl_v21_merge_raw_and_client(base_bytes, client_bytes):
    base_text, base_enc = _cbl_v21_decode_dxf_bytes(base_bytes)
    client_text, _client_enc = _cbl_v21_decode_dxf_bytes(client_bytes)

    base_text = _cbl_v21_norm(base_text)
    client_text = _cbl_v21_norm(client_text)

    base_layers = _cbl_v21_parse_layer_names(base_text)
    client_layers = _cbl_v21_parse_layer_names(client_text)
    client_layer_records = _cbl_v21_extract_layer_records(client_text)

    # 원본에 없는 레이어만 새 레이어로 판단
    missing = {}
    for lu in client_layers:
        if lu not in base_layers:
            # 원래 대소문자 복구
            rec = client_layer_records.get(lu, "")
            name = _cbl_v21_get_pair_value(rec.split("\n"), "2") if rec else ""
            missing[lu] = name or lu

    # client DXF 중 새 레이어에 있는 엔티티만 append
    client_entities = _cbl_v21_extract_entities(client_text)
    add_entities = []

    for ent in client_entities:
        if ent["layer_upper"] in missing:
            add_entities.append(ent["text"])

    merged, added_layers = _cbl_v21_insert_missing_layers(base_text, missing, client_layer_records)
    merged, _added_entity_est = _cbl_v21_insert_entities(merged, "".join(add_entities))

    if "\n0\nEOF" not in merged.upper():
        merged = merged.rstrip() + "\n0\nEOF\n"

    merged_bytes = _cbl_v21_encode_dxf_text(merged, base_enc)

    return merged_bytes, {
        "base_encoding": base_enc,
        "base_layers": len(base_layers),
        "client_layers": len(client_layers),
        "missing_layers": list(missing.values()),
        "added_layers": added_layers,
        "client_entities": len(client_entities),
        "added_entities": len(add_entities),
        "base_bytes": len(base_bytes or b""),
        "client_bytes": len(client_bytes or b""),
        "merged_bytes": len(merged_bytes),
    }


def cblcad_dwg_to_dxf_api(request, *args, **kwargs):
    """
    V21 wrapper:
    기존 DWG→DXF API 결과를 그대로 반환하되,
    같은 DXF bytes를 서버 캐시에 저장하고 X-CBL-RAW-ID 헤더를 붙인다.
    """
    import uuid
    import json
    import time

    if _CBL_V21_PREV_DWG_TO_DXF_API is None:
        from django.http import JsonResponse
        return JsonResponse({"ok": False, "error": "previous dwg_to_dxf api missing", "mode": "CBL_DWG_SERVER_RAW_CACHE_V21"}, status=500)

    response = _CBL_V21_PREV_DWG_TO_DXF_API(request, *args, **kwargs)

    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
        content = bytes(getattr(response, "content", b"") or b"")

        if status_code == 200 and len(content) > 500:
            _cbl_v21_cleanup_cache()

            raw_id = uuid.uuid4().hex
            raw_path, meta_path = _cbl_v21_cache_paths(raw_id)

            raw_path.write_bytes(content)

            meta = {
                "mode": "CBL_DWG_SERVER_RAW_CACHE_V21",
                "raw_id": raw_id,
                "bytes": len(content),
                "created_at": time.time(),
                "filename": "",
            }

            try:
                f = request.FILES.get("file") or request.FILES.get("dwg")
                if f:
                    meta["filename"] = getattr(f, "name", "") or ""
            except Exception:
                pass

            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

            response["X-CBL-RAW-ID"] = raw_id
            response["X-CBL-RAW-BYTES"] = str(len(content))
            response["X-CBL-RAW-CACHE"] = "CBL_DWG_SERVER_RAW_CACHE_V21"
            response["Access-Control-Expose-Headers"] = "X-CBL-RAW-ID, X-CBL-RAW-BYTES, X-CBL-RAW-CACHE"

            print("✅ CBL_DWG_SERVER_RAW_CACHE_V21 cached:", raw_id, len(content))

    except Exception as e:
        try:
            print("⚠️ CBL_DWG_SERVER_RAW_CACHE_V21 cache failed:", repr(e))
        except Exception:
            pass

    return response


def cblcad_dxf_to_dwg_save_api(request, *args, **kwargs):
    """
    V21 wrapper:
    cbl_raw_id가 있으면 서버 캐시 RAW DXF와 client DXF를 병합해서
    기존 V18 dxf-to-dwg API에 merged DXF 파일로 넘긴다.
    """
    if _CBL_V21_PREV_DXF_TO_DWG_API is None:
        from django.http import JsonResponse
        return JsonResponse({"ok": False, "error": "previous dxf_to_dwg api missing", "mode": "CBL_DWG_SERVER_RAW_CACHE_V21"}, status=500)

    try:
        raw_id = ""
        try:
            raw_id = request.POST.get("cbl_raw_id") or request.POST.get("raw_id") or request.GET.get("cbl_raw_id") or ""
        except Exception:
            raw_id = ""

        raw_id = _cbl_v21_safe_raw_id(raw_id)

        if not raw_id:
            return _CBL_V21_PREV_DXF_TO_DWG_API(request, *args, **kwargs)

        raw_path, meta_path = _cbl_v21_cache_paths(raw_id)
        if not raw_path or not raw_path.exists():
            print("⚠️ CBL_DWG_SERVER_RAW_CACHE_V21 raw cache missing:", raw_id)
            return _CBL_V21_PREV_DXF_TO_DWG_API(request, *args, **kwargs)

        base_bytes = raw_path.read_bytes()
        client_bytes = _cbl_v21_read_client_dxf_from_request(request)

        if not client_bytes:
            print("⚠️ CBL_DWG_SERVER_RAW_CACHE_V21 client dxf missing:", raw_id)
            return _CBL_V21_PREV_DXF_TO_DWG_API(request, *args, **kwargs)

        merged_bytes, report = _cbl_v21_merge_raw_and_client(base_bytes, client_bytes)

        # 기존 V18 API가 request.FILES['file'] 또는 ['dxf']를 읽도록 request를 교체
        from django.core.files.uploadedfile import SimpleUploadedFile

        merged_file = SimpleUploadedFile(
            "input_merged_v21.dxf",
            merged_bytes,
            content_type="application/dxf"
        )

        try:
            files = request.FILES.copy()
        except Exception:
            from django.utils.datastructures import MultiValueDict
            files = MultiValueDict()

        try:
            files.setlist("file", [merged_file])
            files.setlist("dxf", [merged_file])
        except Exception:
            files["file"] = merged_file
            files["dxf"] = merged_file

        try:
            post = request.POST.copy()
            # 기존 dxf_text가 있으면 기존 API가 파일 대신 텍스트를 잡을 수 있어서 제거
            for k in ["dxf_text", "dxf"]:
                if k in post:
                    del post[k]
            post["cbl_raw_id"] = raw_id
            post["cbl_raw_merge_mode"] = "CBL_DWG_SERVER_RAW_CACHE_V21"
        except Exception:
            post = None

        request._files = files
        if post is not None:
            request._post = post

        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21 merge:", {
            "raw_id": raw_id,
            **report,
        })

        response = _CBL_V21_PREV_DXF_TO_DWG_API(request, *args, **kwargs)

        try:
            response["X-CBL-RAW-MERGE"] = "CBL_DWG_SERVER_RAW_CACHE_V21"
            response["X-CBL-RAW-ID"] = raw_id
            response["X-CBL-ADDED-LAYERS"] = str(report.get("added_layers", 0))
            response["X-CBL-ADDED-ENTITIES"] = str(report.get("added_entities", 0))
            response["Access-Control-Expose-Headers"] = "X-CBL-RAW-MERGE, X-CBL-RAW-ID, X-CBL-ADDED-LAYERS, X-CBL-ADDED-ENTITIES"
        except Exception:
            pass

        return response

    except Exception as e:
        try:
            print("❌ CBL_DWG_SERVER_RAW_CACHE_V21 failed:", repr(e))
        except Exception:
            pass

        return _CBL_V21_PREV_DXF_TO_DWG_API(request, *args, **kwargs)

# CBL_DWG_SERVER_RAW_CACHE_V21_END



# CBL_DWG_SERVER_RAW_CACHE_V21_1_STREAM_FIX_START
# 목적:
# - V21 dwg-to-dxf raw_id 캐시 실패 보정
# - response.content가 없는 FileResponse/StreamingHttpResponse도 bytes로 재구성해서 캐시
# - 기존 V21 helper 함수들을 그대로 사용

try:
    _CBL_V21_1_PREV_DWG_TO_DXF_API = cblcad_dwg_to_dxf_api
except Exception:
    _CBL_V21_1_PREV_DWG_TO_DXF_API = None


def _cbl_v21_1_response_to_bytes_and_rebuild(response):
    """
    response에서 DXF bytes를 추출한다.
    Streaming/FileResponse면 content를 소비하므로 새 HttpResponse로 재구성한다.
    """
    from django.http import HttpResponse

    status = int(getattr(response, "status_code", 0) or 0)
    headers = {}

    try:
        for k, v in response.items():
            headers[str(k)] = str(v)
    except Exception:
        pass

    content = b""
    rebuilt = response

    # 1) 일반 HttpResponse
    try:
        content = bytes(getattr(response, "content", b"") or b"")
        if content:
            return content, response
    except Exception:
        pass

    # 2) Streaming/FileResponse
    try:
        chunks = []
        streaming_content = getattr(response, "streaming_content", None)
        if streaming_content is not None:
            for chunk in streaming_content:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                chunks.append(bytes(chunk or b""))
            content = b"".join(chunks)
    except Exception as e:
        try:
            print("⚠️ CBL V21.1 streaming read failed:", repr(e))
        except Exception:
            pass
        content = b""

    if content:
        content_type = headers.get("Content-Type") or headers.get("content-type") or "application/dxf"
        rebuilt = HttpResponse(content, status=status or 200, content_type=content_type)

        for k, v in headers.items():
            lk = k.lower()
            if lk in {"content-type", "content-length"}:
                continue
            try:
                rebuilt[k] = v
            except Exception:
                pass

    return content, rebuilt


def _cbl_v21_1_cache_raw_bytes(content, request, response):
    import uuid
    import json
    import time

    if not content or len(content) < 500:
        return response, ""

    # DXF인지 최소 확인. 바이너리 DWG면 캐시하지 않음.
    head = content[:2000]
    if b"SECTION" not in head and b"SECTION" not in content[:200000]:
        try:
            print("⚠️ CBL V21.1 skip cache: response does not look like DXF", len(content))
        except Exception:
            pass
        return response, ""

    _cbl_v21_cleanup_cache()

    raw_id = uuid.uuid4().hex
    raw_path, meta_path = _cbl_v21_cache_paths(raw_id)

    raw_path.write_bytes(content)

    filename = ""
    try:
        f = request.FILES.get("file") or request.FILES.get("dwg")
        if f:
            filename = getattr(f, "name", "") or ""
    except Exception:
        pass

    meta = {
        "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_1_STREAM_FIX",
        "raw_id": raw_id,
        "bytes": len(content),
        "created_at": time.time(),
        "filename": filename,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    response["X-CBL-RAW-ID"] = raw_id
    response["X-CBL-RAW-BYTES"] = str(len(content))
    response["X-CBL-RAW-CACHE"] = "CBL_DWG_SERVER_RAW_CACHE_V21_1_STREAM_FIX"
    response["Access-Control-Expose-Headers"] = "X-CBL-RAW-ID, X-CBL-RAW-BYTES, X-CBL-RAW-CACHE, X-CBL-RAW-MERGE, X-CBL-ADDED-LAYERS, X-CBL-ADDED-ENTITIES"

    try:
        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_1 cached:", {
            "raw_id": raw_id,
            "bytes": len(content),
            "filename": filename,
            "status": getattr(response, "status_code", None),
        })
    except Exception:
        pass

    return response, raw_id


def cblcad_dwg_to_dxf_api(request, *args, **kwargs):
    """
    V21.1 최종 dwg-to-dxf wrapper.
    기존 V21 wrapper 위에 한 번 더 감싸서 raw_id 헤더 누락을 보정한다.
    """
    if _CBL_V21_1_PREV_DWG_TO_DXF_API is None:
        from django.http import JsonResponse
        return JsonResponse({
            "ok": False,
            "error": "previous dwg_to_dxf api missing",
            "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_1_STREAM_FIX"
        }, status=500)

    response = _CBL_V21_1_PREV_DWG_TO_DXF_API(request, *args, **kwargs)

    try:
        # 이미 V21에서 raw_id가 붙었으면 그대로 반환
        try:
            existing = response.get("X-CBL-RAW-ID", "")
        except Exception:
            existing = ""

        if existing:
            try:
                print("✅ CBL V21.1 existing raw header:", existing)
            except Exception:
                pass
            return response

        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code != 200:
            return response

        content, rebuilt = _cbl_v21_1_response_to_bytes_and_rebuild(response)
        rebuilt, raw_id = _cbl_v21_1_cache_raw_bytes(content, request, rebuilt)
        return rebuilt

    except Exception as e:
        try:
            print("❌ CBL_DWG_SERVER_RAW_CACHE_V21_1 failed:", repr(e))
        except Exception:
            pass
        return response

# CBL_DWG_SERVER_RAW_CACHE_V21_1_STREAM_FIX_END



# CBL_DWG_SERVER_RAW_CACHE_V21_2_FORCE_ROUTE_START
# 목적:
# - URL 라우트가 기존 함수를 물고 있어 raw_id 헤더가 안 붙는 문제 강제 해결
# - 원본 DWG→DXF API 결과를 받아 서버 캐시에 저장하고 X-CBL-RAW-ID 헤더를 붙임
# - 저장 API는 기존 V21 merge wrapper를 직접 호출

def cblcad_dwg_to_dxf_api_v21_2_force_route(request, *args, **kwargs):
    import uuid
    import json
    import time
    from django.http import HttpResponse, JsonResponse

    MODE = "CBL_DWG_SERVER_RAW_CACHE_V21_2_FORCE_ROUTE"

    # V21 적용 전에 잡아둔 원본 dwg-to-dxf 함수를 최우선 사용
    base_api = None
    for name in [
        "_CBL_V21_PREV_DWG_TO_DXF_API",
        "_CBL_V21_1_PREV_DWG_TO_DXF_API",
    ]:
        try:
            fn = globals().get(name)
            if callable(fn):
                base_api = fn
                break
        except Exception:
            pass

    if base_api is None:
        try:
            # 최후 fallback. 단, 현재 함수 자신이면 재귀 방지.
            fn = globals().get("cblcad_dwg_to_dxf_api")
            if callable(fn) and fn is not cblcad_dwg_to_dxf_api_v21_2_force_route:
                base_api = fn
        except Exception:
            pass

    if base_api is None:
        return JsonResponse({
            "ok": False,
            "error": "base dwg_to_dxf api missing",
            "mode": MODE
        }, status=500)

    response = base_api(request, *args, **kwargs)

    try:
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            return response

        headers = {}
        try:
            for k, v in response.items():
                headers[str(k)] = str(v)
        except Exception:
            pass

        content = b""
        rebuilt = response

        # 일반 HttpResponse
        try:
            content = bytes(getattr(response, "content", b"") or b"")
        except Exception:
            content = b""

        # Streaming/FileResponse
        if not content:
            try:
                streaming_content = getattr(response, "streaming_content", None)
                if streaming_content is not None:
                    chunks = []
                    for chunk in streaming_content:
                        if isinstance(chunk, str):
                            chunk = chunk.encode("utf-8", errors="replace")
                        chunks.append(bytes(chunk or b""))
                    content = b"".join(chunks)

                    content_type = headers.get("Content-Type") or headers.get("content-type") or "application/dxf"
                    rebuilt = HttpResponse(content, status=status, content_type=content_type)

                    for k, v in headers.items():
                        if k.lower() in {"content-type", "content-length"}:
                            continue
                        try:
                            rebuilt[k] = v
                        except Exception:
                            pass

            except Exception as e:
                try:
                    print("❌ CBL V21.2 streaming read failed:", repr(e))
                except Exception:
                    pass
                content = b""

        if not content or len(content) < 500:
            try:
                print("⚠️ CBL V21.2 no content to cache:", len(content or b""))
            except Exception:
                pass
            return response

        # DXF 최소 판정
        head = content[:300000]
        if b"SECTION" not in head:
            try:
                print("⚠️ CBL V21.2 response not DXF-like:", {
                    "bytes": len(content),
                    "head": head[:80],
                })
            except Exception:
                pass
            return rebuilt

        try:
            _cbl_v21_cleanup_cache()
        except Exception:
            pass

        raw_id = uuid.uuid4().hex

        try:
            raw_path, meta_path = _cbl_v21_cache_paths(raw_id)
        except Exception:
            import os
            from pathlib import Path
            d = Path("tmp") / "cblcad_raw_cache_v21"
            d.mkdir(parents=True, exist_ok=True)
            raw_path = d / (raw_id + ".dxf")
            meta_path = d / (raw_id + ".json")

        raw_path.write_bytes(content)

        filename = ""
        try:
            f = request.FILES.get("file") or request.FILES.get("dwg")
            if f:
                filename = getattr(f, "name", "") or ""
        except Exception:
            pass

        meta = {
            "mode": MODE,
            "raw_id": raw_id,
            "bytes": len(content),
            "created_at": time.time(),
            "filename": filename,
        }

        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        rebuilt["X-CBL-RAW-ID"] = raw_id
        rebuilt["X-CBL-RAW-BYTES"] = str(len(content))
        rebuilt["X-CBL-RAW-CACHE"] = MODE
        rebuilt["Access-Control-Expose-Headers"] = (
            "X-CBL-RAW-ID, X-CBL-RAW-BYTES, X-CBL-RAW-CACHE, "
            "X-CBL-RAW-MERGE, X-CBL-ADDED-LAYERS, X-CBL-ADDED-ENTITIES"
        )

        try:
            print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_2_FORCE_ROUTE cached:", {
                "raw_id": raw_id,
                "bytes": len(content),
                "filename": filename,
                "status": status,
            })
        except Exception:
            pass

        return rebuilt

    except Exception as e:
        try:
            print("❌ CBL_DWG_SERVER_RAW_CACHE_V21_2_FORCE_ROUTE failed:", repr(e))
        except Exception:
            pass
        return response


def cblcad_dxf_to_dwg_save_api_v21_2_force_route(request, *args, **kwargs):
    MODE = "CBL_DWG_SERVER_RAW_CACHE_V21_2_FORCE_ROUTE_SAVE"

    try:
        fn = globals().get("cblcad_dxf_to_dwg_save_api")
        if callable(fn) and fn is not cblcad_dxf_to_dwg_save_api_v21_2_force_route:
            try:
                raw_id = request.POST.get("cbl_raw_id") or request.POST.get("raw_id") or ""
                print("✅ CBL V21.2 save route hit:", {
                    "raw_id": raw_id,
                    "mode": MODE,
                })
            except Exception:
                pass
            return fn(request, *args, **kwargs)
    except Exception as e:
        try:
            print("❌ CBL V21.2 save route wrapper failed:", repr(e))
        except Exception:
            pass

    from django.http import JsonResponse
    return JsonResponse({
        "ok": False,
        "error": "base dxf_to_dwg api missing",
        "mode": MODE
    }, status=500)

# CBL_DWG_SERVER_RAW_CACHE_V21_2_FORCE_ROUTE_END



# CBL_DWG_SERVER_RAW_CACHE_V21_3_WRAP_BEST_START
# 실제 /api/cblcad/dwg-to-dxf/ 라우트가 물고 있는 cblcad_dwg_to_best_dxf_api를 감싼다.

try:
    _CBL_V21_3_ORIGINAL_BEST_DWG_TO_DXF_API = cblcad_dwg_to_best_dxf_api
except Exception:
    _CBL_V21_3_ORIGINAL_BEST_DWG_TO_DXF_API = None


def _cbl_v21_3_response_to_bytes(response):
    from django.http import HttpResponse

    status = int(getattr(response, "status_code", 0) or 0)
    headers = {}

    try:
        for k, v in response.items():
            headers[str(k)] = str(v)
    except Exception:
        pass

    content = b""
    rebuilt = response

    try:
        content = bytes(getattr(response, "content", b"") or b"")
        if content:
            return content, response
    except Exception:
        pass

    try:
        streaming_content = getattr(response, "streaming_content", None)
        if streaming_content is not None:
            chunks = []
            for chunk in streaming_content:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                chunks.append(bytes(chunk or b""))
            content = b"".join(chunks)

            content_type = headers.get("Content-Type") or headers.get("content-type") or "application/dxf"
            rebuilt = HttpResponse(content, status=status or 200, content_type=content_type)

            for k, v in headers.items():
                if k.lower() in {"content-type", "content-length"}:
                    continue
                try:
                    rebuilt[k] = v
                except Exception:
                    pass

            return content, rebuilt
    except Exception as e:
        try:
            print("❌ CBL V21.3 streaming read failed:", repr(e))
        except Exception:
            pass

    return b"", response


def _cbl_v21_3_looks_like_dxf_bytes(content):
    if not content or len(content) < 500:
        return False

    head = content[:500000]

    # ASCII DXF
    if b"SECTION" in head and (b"ENTITIES" in content[:5000000] or b"TABLES" in content[:5000000]):
        return True

    # UTF-16 계열 대응
    if b"S\x00E\x00C\x00T\x00I\x00O\x00N" in head:
        return True

    return False


def _cbl_v21_3_cache_content(content, request, response):
    import uuid
    import json
    import time
    from pathlib import Path

    MODE = "CBL_DWG_SERVER_RAW_CACHE_V21_3_WRAP_BEST"

    raw_id = uuid.uuid4().hex

    try:
        raw_path, meta_path = _cbl_v21_cache_paths(raw_id)
    except Exception:
        d = Path("tmp") / "cblcad_raw_cache_v21"
        d.mkdir(parents=True, exist_ok=True)
        raw_path = d / (raw_id + ".dxf")
        meta_path = d / (raw_id + ".json")

    try:
        _cbl_v21_cleanup_cache()
    except Exception:
        pass

    raw_path.write_bytes(content)

    filename = ""
    try:
        f = request.FILES.get("file") or request.FILES.get("dwg")
        if f:
            filename = getattr(f, "name", "") or ""
    except Exception:
        pass

    meta = {
        "mode": MODE,
        "raw_id": raw_id,
        "bytes": len(content),
        "created_at": time.time(),
        "filename": filename,
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    response["X-CBL-RAW-ID"] = raw_id
    response["X-CBL-RAW-BYTES"] = str(len(content))
    response["X-CBL-RAW-CACHE"] = MODE
    response["Access-Control-Expose-Headers"] = (
        "X-CBL-RAW-ID, X-CBL-RAW-BYTES, X-CBL-RAW-CACHE, "
        "X-CBL-RAW-MERGE, X-CBL-ADDED-LAYERS, X-CBL-ADDED-ENTITIES"
    )

    try:
        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_3_WRAP_BEST cached:", {
            "raw_id": raw_id,
            "bytes": len(content),
            "filename": filename,
        })
    except Exception:
        pass

    return response


def cblcad_dwg_to_dxf_best_v21_3_cache(request, *args, **kwargs):
    """
    실제 open route용 V21.3 wrapper.
    기존 best 변환 결과는 그대로 브라우저로 보내고,
    같은 bytes를 서버 raw cache에 저장한 뒤 header만 붙인다.
    """
    from django.http import JsonResponse

    MODE = "CBL_DWG_SERVER_RAW_CACHE_V21_3_WRAP_BEST"

    if _CBL_V21_3_ORIGINAL_BEST_DWG_TO_DXF_API is None:
        return JsonResponse({
            "ok": False,
            "error": "original best dwg_to_dxf api missing",
            "mode": MODE,
        }, status=500)

    response = _CBL_V21_3_ORIGINAL_BEST_DWG_TO_DXF_API(request, *args, **kwargs)

    try:
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            return response

        content, rebuilt = _cbl_v21_3_response_to_bytes(response)

        if not _cbl_v21_3_looks_like_dxf_bytes(content):
            try:
                print("⚠️ CBL V21.3 response not DXF-like:", {
                    "bytes": len(content or b""),
                    "head": (content or b"")[:80],
                })
            except Exception:
                pass
            return rebuilt

        return _cbl_v21_3_cache_content(content, request, rebuilt)

    except Exception as e:
        try:
            print("❌ CBL_DWG_SERVER_RAW_CACHE_V21_3_WRAP_BEST failed:", repr(e))
        except Exception:
            pass
        return response

# CBL_DWG_SERVER_RAW_CACHE_V21_3_WRAP_BEST_END



# CBL_DWG_SERVER_RAW_CACHE_V21_4_INPLACE_WRAP_BEST_START
# 실제 함수명 자체를 감싼다.
# URLConf가 views.cblcad_dwg_to_best_dxf_api를 잡으면 이 wrapper가 바로 실행된다.

try:
    _CBL_V21_4_ORIGINAL_BEST_DWG_TO_DXF_API = cblcad_dwg_to_best_dxf_api
except Exception:
    _CBL_V21_4_ORIGINAL_BEST_DWG_TO_DXF_API = None


def _cbl_v21_4_cache_dir():
    from pathlib import Path
    try:
        from django.conf import settings
        base = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    except Exception:
        base = Path.cwd()

    d = base / "tmp" / "cblcad_raw_cache_v21"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cbl_v21_4_cache_paths(raw_id):
    d = _cbl_v21_4_cache_dir()
    return d / (raw_id + ".dxf"), d / (raw_id + ".json")


def _cbl_v21_4_cleanup_cache(max_age_hours=12):
    import time
    d = _cbl_v21_4_cache_dir()
    now = time.time()
    max_age = float(max_age_hours) * 3600.0

    for path in d.glob("*"):
        try:
            if now - path.stat().st_mtime > max_age:
                path.unlink(missing_ok=True)
        except Exception:
            pass


def _cbl_v21_4_response_to_bytes(response):
    from django.http import HttpResponse

    status = int(getattr(response, "status_code", 0) or 0)
    headers = {}

    try:
        for k, v in response.items():
            headers[str(k)] = str(v)
    except Exception:
        pass

    content = b""
    rebuilt = response

    # 일반 HttpResponse
    try:
        content = bytes(getattr(response, "content", b"") or b"")
        if content:
            return content, response
    except Exception:
        pass

    # Streaming/FileResponse
    try:
        streaming_content = getattr(response, "streaming_content", None)
        if streaming_content is not None:
            chunks = []
            for chunk in streaming_content:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                chunks.append(bytes(chunk or b""))

            content = b"".join(chunks)

            content_type = headers.get("Content-Type") or headers.get("content-type") or "application/dxf"
            rebuilt = HttpResponse(content, status=status or 200, content_type=content_type)

            for k, v in headers.items():
                if k.lower() in {"content-type", "content-length"}:
                    continue
                try:
                    rebuilt[k] = v
                except Exception:
                    pass

            return content, rebuilt
    except Exception as e:
        try:
            print("❌ CBL V21.4 streaming read failed:", repr(e))
        except Exception:
            pass

    return b"", response


def _cbl_v21_4_looks_like_dxf(content):
    if not content or len(content) < 500:
        return False

    head = content[:500000]
    first5m = content[:5000000]

    if b"SECTION" in head and (b"ENTITIES" in first5m or b"TABLES" in first5m):
        return True

    # UTF-16 LE 흔적
    if b"S\x00E\x00C\x00T\x00I\x00O\x00N" in head:
        return True

    return False


def _cbl_v21_4_cache_content(content, request, response):
    import uuid
    import json
    import time

    MODE = "CBL_DWG_SERVER_RAW_CACHE_V21_4_INPLACE_WRAP_BEST"

    raw_id = uuid.uuid4().hex

    try:
        _cbl_v21_4_cleanup_cache()
    except Exception:
        pass

    try:
        raw_path, meta_path = _cbl_v21_cache_paths(raw_id)
    except Exception:
        raw_path, meta_path = _cbl_v21_4_cache_paths(raw_id)

    raw_path.write_bytes(content)

    filename = ""
    try:
        f = request.FILES.get("file") or request.FILES.get("dwg")
        if f:
            filename = getattr(f, "name", "") or ""
    except Exception:
        pass

    meta = {
        "mode": MODE,
        "raw_id": raw_id,
        "bytes": len(content),
        "created_at": time.time(),
        "filename": filename,
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    response["X-CBL-RAW-ID"] = raw_id
    response["X-CBL-RAW-BYTES"] = str(len(content))
    response["X-CBL-RAW-CACHE"] = MODE
    response["Access-Control-Expose-Headers"] = (
        "X-CBL-RAW-ID, X-CBL-RAW-BYTES, X-CBL-RAW-CACHE, "
        "X-CBL-RAW-MERGE, X-CBL-ADDED-LAYERS, X-CBL-ADDED-ENTITIES"
    )

    try:
        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_4_INPLACE_WRAP_BEST cached:", {
            "raw_id": raw_id,
            "bytes": len(content),
            "filename": filename,
            "path": str(raw_path),
        })
    except Exception:
        pass

    return response


def cblcad_dwg_to_best_dxf_api(request, *args, **kwargs):
    """
    V21.4 inplace wrapper.
    기존 best DXF 변환 결과를 그대로 반환하면서,
    같은 DXF bytes를 서버 raw cache에 저장하고 header만 붙인다.
    """
    from django.http import JsonResponse

    MODE = "CBL_DWG_SERVER_RAW_CACHE_V21_4_INPLACE_WRAP_BEST"

    if _CBL_V21_4_ORIGINAL_BEST_DWG_TO_DXF_API is None:
        return JsonResponse({
            "ok": False,
            "error": "original cblcad_dwg_to_best_dxf_api missing",
            "mode": MODE,
        }, status=500)

    response = _CBL_V21_4_ORIGINAL_BEST_DWG_TO_DXF_API(request, *args, **kwargs)

    try:
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            return response

        content, rebuilt = _cbl_v21_4_response_to_bytes(response)

        if not _cbl_v21_4_looks_like_dxf(content):
            try:
                print("⚠️ CBL V21.4 response not DXF-like:", {
                    "bytes": len(content or b""),
                    "head": (content or b"")[:100],
                })
            except Exception:
                pass
            return rebuilt

        return _cbl_v21_4_cache_content(content, request, rebuilt)

    except Exception as e:
        try:
            print("❌ CBL_DWG_SERVER_RAW_CACHE_V21_4_INPLACE_WRAP_BEST failed:", repr(e))
        except Exception:
            pass
        return response

# CBL_DWG_SERVER_RAW_CACHE_V21_4_INPLACE_WRAP_BEST_END



# CBL_DWG_SERVER_RAW_CACHE_V21_5_CSRF_FIX_START
# V21 raw cache wrapper 적용 후 POST /api/cblcad/dwg-to-dxf/ 가 403 CSRF로 막히는 문제 보정
try:
    from django.views.decorators.csrf import csrf_exempt

    _CBL_V21_5_CSRF_TARGETS = [
        "cblcad_dwg_to_best_dxf_api",
        "cblcad_dwg_to_dxf_best_v21_3_cache",
        "cblcad_dwg_to_dxf_api_v21_2_force_route",
        "cblcad_dwg_to_dxf_api",
        "cblcad_dxf_to_dwg_save_api",
        "cblcad_dxf_to_dwg_save_api_v21_2_force_route",
    ]

    for _name in _CBL_V21_5_CSRF_TARGETS:
        try:
            _fn = globals().get(_name)
            if callable(_fn):
                _wrapped = csrf_exempt(_fn)
                globals()[_name] = _wrapped
                try:
                    _wrapped.csrf_exempt = True
                except Exception:
                    pass
                print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_5 csrf_exempt:", _name)
        except Exception as _e:
            try:
                print("⚠️ CBL_DWG_SERVER_RAW_CACHE_V21_5 csrf_exempt failed:", _name, repr(_e))
            except Exception:
                pass

except Exception as _e:
    try:
        print("❌ CBL_DWG_SERVER_RAW_CACHE_V21_5 init failed:", repr(_e))
    except Exception:
        pass
# CBL_DWG_SERVER_RAW_CACHE_V21_5_CSRF_FIX_END



# CBL_DWG_SERVER_RAW_CACHE_V21_6_SAFE_LAYER_MERGE_START
# V21.6:
# - 기존 _cbl_v21_merge_raw_and_client()를 안전 병합 버전으로 덮어씀
# - client DXF의 LAYER record를 복사하지 않음
# - 새 레이어는 서버에서 표준 LAYER record를 생성
# - LAYER TABLE 삽입은 문자열 통째 삽입 금지, line-by-line 삽입

def _cbl_v21_6_norm(text):
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _cbl_v21_6_trim(v):
    return str(v or "").strip()


def _cbl_v21_6_lines(text):
    return _cbl_v21_6_norm(text).split("\n")


def _cbl_v21_6_pair_value(block_lines, code):
    code = str(code)
    for i in range(0, len(block_lines) - 1):
        if _cbl_v21_6_trim(block_lines[i]) == code:
            return _cbl_v21_6_trim(block_lines[i + 1])
    return ""


def _cbl_v21_6_parse_layer_names(dxf_text):
    lines = _cbl_v21_6_lines(dxf_text)
    out = {}

    i = 0
    while i < len(lines) - 1:
        if _cbl_v21_6_trim(lines[i]) == "0" and _cbl_v21_6_trim(lines[i + 1]).upper() == "LAYER":
            block = lines[i:i + 80]
            name = _cbl_v21_6_pair_value(block, "2")
            if name:
                out[name.upper()] = name
        i += 1

    out.setdefault("0", "0")
    return out


def _cbl_v21_6_make_safe_layer_record(name):
    import uuid

    name = str(name or "0").strip() or "0"
    handle = uuid.uuid4().hex[:8].upper()

    # 가장 보수적인 AcDbLayerTableRecord
    return [
        "0", "LAYER",
        "5", handle,
        "100", "AcDbSymbolTableRecord",
        "100", "AcDbLayerTableRecord",
        "2", name,
        "70", "0",
        "62", "7",
        "6", "Continuous",
    ]


def _cbl_v21_6_find_layer_table(lines):
    """
    return: (table_start, endtab_index, count_value_index)
    count_value_index는 group code 70의 value line index.
    """
    for i in range(0, len(lines) - 5):
        if (
            _cbl_v21_6_trim(lines[i]) == "0"
            and _cbl_v21_6_trim(lines[i + 1]).upper() == "TABLE"
            and _cbl_v21_6_trim(lines[i + 2]) == "2"
            and _cbl_v21_6_trim(lines[i + 3]).upper() == "LAYER"
        ):
            count_idx = -1
            for c in range(i + 4, min(i + 20, len(lines) - 1)):
                if _cbl_v21_6_trim(lines[c]) == "70":
                    count_idx = c + 1
                    break

            for j in range(i + 4, len(lines) - 1):
                if _cbl_v21_6_trim(lines[j]) == "0" and _cbl_v21_6_trim(lines[j + 1]).upper() == "ENDTAB":
                    return i, j, count_idx

    return -1, -1, -1


def _cbl_v21_6_find_section_start(lines, section_name):
    section_name = str(section_name or "").upper()

    for i in range(0, len(lines) - 3):
        if (
            _cbl_v21_6_trim(lines[i]) == "0"
            and _cbl_v21_6_trim(lines[i + 1]).upper() == "SECTION"
            and _cbl_v21_6_trim(lines[i + 2]) == "2"
            and _cbl_v21_6_trim(lines[i + 3]).upper() == section_name
        ):
            return i

    return -1


def _cbl_v21_6_insert_missing_layers(base_text, missing_layers):
    base_text = _cbl_v21_6_norm(base_text)
    lines = _cbl_v21_6_lines(base_text)

    if not missing_layers:
        return base_text, 0

    add_lines = []
    for _upper, name in missing_layers.items():
        add_lines.extend(_cbl_v21_6_make_safe_layer_record(name))

    table_start, endtab_idx, count_idx = _cbl_v21_6_find_layer_table(lines)

    if table_start >= 0 and endtab_idx >= 0:
        # ENDTAB 직전에 line-by-line 삽입
        lines[endtab_idx:endtab_idx] = add_lines

        # 70 count는 틀려도 대부분 읽지만, 가능하면 증가
        if count_idx >= 0 and count_idx < len(lines):
            try:
                old = int(_cbl_v21_6_trim(lines[count_idx]) or "0")
                lines[count_idx] = str(old + len(missing_layers))
            except Exception:
                pass

        return "\n".join(lines), len(missing_layers)

    # LAYER TABLE이 없을 때만 최소 TABLES 섹션 생성
    table_lines = [
        "0", "SECTION",
        "2", "TABLES",
        "0", "TABLE",
        "2", "LAYER",
        "70", str(len(missing_layers) + 1),
    ]
    table_lines.extend(_cbl_v21_6_make_safe_layer_record("0"))
    table_lines.extend(add_lines)
    table_lines.extend(["0", "ENDTAB", "0", "ENDSEC"])

    # ENTITIES 섹션 직전 삽입
    ent_idx = _cbl_v21_6_find_section_start(lines, "ENTITIES")
    if ent_idx >= 0:
        lines[ent_idx:ent_idx] = table_lines
        return "\n".join(lines), len(missing_layers)

    # 최후 fallback: 파일 앞쪽 삽입
    return "\n".join(table_lines) + "\n" + base_text, len(missing_layers)


def _cbl_v21_6_extract_entities(dxf_text):
    lines = _cbl_v21_6_lines(dxf_text)

    ent_start = -1
    ent_end = -1

    for i in range(0, len(lines) - 3):
        if (
            _cbl_v21_6_trim(lines[i]) == "0"
            and _cbl_v21_6_trim(lines[i + 1]).upper() == "SECTION"
            and _cbl_v21_6_trim(lines[i + 2]) == "2"
            and _cbl_v21_6_trim(lines[i + 3]).upper() == "ENTITIES"
        ):
            ent_start = i + 4
            break

    if ent_start < 0:
        return []

    for j in range(ent_start, len(lines) - 1):
        if _cbl_v21_6_trim(lines[j]) == "0" and _cbl_v21_6_trim(lines[j + 1]).upper() == "ENDSEC":
            ent_end = j
            break

    if ent_end < 0:
        ent_end = len(lines)

    entity_types = {
        "LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC",
        "TEXT", "MTEXT", "DIMENSION", "INSERT", "HATCH",
        "ELLIPSE", "SPLINE", "POINT", "SOLID", "TRACE",
        "3DFACE", "LEADER", "MLEADER", "XLINE", "RAY",
        "IMAGE", "WIPEOUT"
    }

    out = []
    i = ent_start

    while i < ent_end - 1:
        if _cbl_v21_6_trim(lines[i]) == "0":
            typ = _cbl_v21_6_trim(lines[i + 1]).upper()

            if typ in entity_types:
                start = i
                k = i + 2

                while k < ent_end - 1:
                    if _cbl_v21_6_trim(lines[k]) == "0":
                        nxt = _cbl_v21_6_trim(lines[k + 1]).upper()
                        if nxt in entity_types or nxt == "ENDSEC":
                            break
                    k += 1

                block = lines[start:k]
                layer = _cbl_v21_6_pair_value(block, "8") or "0"

                out.append({
                    "type": typ,
                    "layer": layer,
                    "layer_upper": layer.upper(),
                    "lines": block,
                })

                i = k
                continue

        i += 1

    return out


def _cbl_v21_6_insert_entities(base_text, entities):
    base_text = _cbl_v21_6_norm(base_text)
    lines = _cbl_v21_6_lines(base_text)

    if not entities:
        return base_text, 0

    add_lines = []
    for ent in entities:
        block = list(ent.get("lines") or [])
        if block:
            add_lines.extend(block)

    if not add_lines:
        return base_text, 0

    ent_start = _cbl_v21_6_find_section_start(lines, "ENTITIES")
    insert_at = -1

    if ent_start >= 0:
        for j in range(ent_start + 4, len(lines) - 1):
            if _cbl_v21_6_trim(lines[j]) == "0" and _cbl_v21_6_trim(lines[j + 1]).upper() == "ENDSEC":
                insert_at = j
                break

    if insert_at >= 0:
        lines[insert_at:insert_at] = add_lines
        return "\n".join(lines), len(entities)

    # ENTITIES 섹션이 없으면 EOF 직전 생성
    sec_lines = ["0", "SECTION", "2", "ENTITIES"]
    sec_lines.extend(add_lines)
    sec_lines.extend(["0", "ENDSEC"])

    eof_idx = -1
    for i in range(len(lines) - 2, -1, -1):
        if _cbl_v21_6_trim(lines[i]) == "0" and i + 1 < len(lines) and _cbl_v21_6_trim(lines[i + 1]).upper() == "EOF":
            eof_idx = i
            break

    if eof_idx >= 0:
        lines[eof_idx:eof_idx] = sec_lines
    else:
        lines.extend(sec_lines)
        lines.extend(["0", "EOF"])

    return "\n".join(lines), len(entities)


def _cbl_v21_6_validate_first_pairs(dxf_text, max_lines=120):
    """
    초반 DXF pair 구조 최소 검증.
    짝수 위치의 group code가 숫자가 아니면 report.
    """
    lines = _cbl_v21_6_lines(dxf_text)
    bad = []

    limit = min(len(lines) - 1, max_lines)

    for i in range(0, limit, 2):
        code = _cbl_v21_6_trim(lines[i])
        if code == "":
            continue
        try:
            int(code)
        except Exception:
            bad.append({
                "line": i + 1,
                "value": lines[i][:80],
                "next": lines[i + 1][:80] if i + 1 < len(lines) else "",
            })
            if len(bad) >= 5:
                break

    return bad


def _cbl_v21_merge_raw_and_client(base_bytes, client_bytes):
    """
    V21.6 safe override.
    """
    base_text, base_enc = _cbl_v21_decode_dxf_bytes(base_bytes)
    client_text, _client_enc = _cbl_v21_decode_dxf_bytes(client_bytes)

    base_text = _cbl_v21_6_norm(base_text)
    client_text = _cbl_v21_6_norm(client_text)

    base_layers = _cbl_v21_6_parse_layer_names(base_text)
    client_layers = _cbl_v21_6_parse_layer_names(client_text)

    # 원본에 없는 client layer만 신규 레이어 후보
    missing = {}
    for lu, name in client_layers.items():
        if lu not in base_layers:
            missing[lu] = name

    client_entities = _cbl_v21_6_extract_entities(client_text)

    # 신규 레이어에 속한 엔티티만 원본 RAW에 append
    add_entities = []
    for ent in client_entities:
        if ent.get("layer_upper") in missing:
            add_entities.append(ent)

    merged, added_layers = _cbl_v21_6_insert_missing_layers(base_text, missing)
    merged, added_entities = _cbl_v21_6_insert_entities(merged, add_entities)

    if "\n0\nEOF" not in merged.upper():
        merged = merged.rstrip() + "\n0\nEOF\n"

    bad_pairs = _cbl_v21_6_validate_first_pairs(merged, 160)

    merged_bytes = _cbl_v21_encode_dxf_text(merged, base_enc)

    report = {
        "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_6_SAFE_LAYER_MERGE",
        "base_encoding": base_enc,
        "base_layers": len(base_layers),
        "client_layers": len(client_layers),
        "missing_layers": list(missing.values()),
        "added_layers": added_layers,
        "client_entities": len(client_entities),
        "added_entities": added_entities,
        "base_bytes": len(base_bytes or b""),
        "client_bytes": len(client_bytes or b""),
        "merged_bytes": len(merged_bytes),
        "first_pair_errors": bad_pairs,
    }

    try:
        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_6 merge report:", report)
    except Exception:
        pass

    return merged_bytes, report

try:
    print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_6_SAFE_LAYER_MERGE installed")
except Exception:
    pass
# CBL_DWG_SERVER_RAW_CACHE_V21_6_SAFE_LAYER_MERGE_END



# CBL_DWG_SERVER_RAW_CACHE_V21_7_JSON_EXTRACT_DXF_START
# V21.7:
# - 기존 best open API 응답이 JSON이면 JSON 전체가 아니라 실제 DXF 문자열만 raw cache에 저장
# - response는 원본 그대로 브라우저에 반환하고 header만 붙임
# - save 쪽 V21.6 safe merge는 그대로 사용

def _cbl_v21_7_response_to_bytes_keep_response(response):
    """
    response body bytes를 읽되, 브라우저 반환 response는 원래 객체를 최대한 유지한다.
    Streaming이면 어쩔 수 없이 HttpResponse로 재구성한다.
    """
    from django.http import HttpResponse

    status = int(getattr(response, "status_code", 0) or 0)
    headers = {}

    try:
        for k, v in response.items():
            headers[str(k)] = str(v)
    except Exception:
        pass

    try:
        content = bytes(getattr(response, "content", b"") or b"")
        if content:
            return content, response
    except Exception:
        pass

    try:
        streaming_content = getattr(response, "streaming_content", None)
        if streaming_content is not None:
            chunks = []
            for chunk in streaming_content:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                chunks.append(bytes(chunk or b""))

            content = b"".join(chunks)

            content_type = headers.get("Content-Type") or headers.get("content-type") or "application/json"
            rebuilt = HttpResponse(content, status=status or 200, content_type=content_type)

            for k, v in headers.items():
                if k.lower() in {"content-type", "content-length"}:
                    continue
                try:
                    rebuilt[k] = v
                except Exception:
                    pass

            return content, rebuilt
    except Exception as e:
        try:
            print("❌ CBL V21.7 streaming read failed:", repr(e))
        except Exception:
            pass

    return b"", response


def _cbl_v21_7_is_dxf_text(txt):
    txt = str(txt or "")
    if len(txt) < 500:
        return False

    head = txt[:500000].upper()
    first = txt[:5000000].upper()

    return ("SECTION" in head and ("ENTITIES" in first or "TABLES" in first))


def _cbl_v21_7_find_largest_dxf_string(obj):
    """
    JSON 객체 안에서 실제 DXF 문자열을 찾는다.
    키 이름이 dxf/dxf_text/text 등으로 달라도 가장 큰 DXF-like 문자열을 선택.
    """
    best = ""

    def walk(x):
        nonlocal best

        if isinstance(x, str):
            if len(x) > len(best) and _cbl_v21_7_is_dxf_text(x):
                best = x
            return

        if isinstance(x, dict):
            # 흔한 키 먼저 확인
            for k in [
                "dxf",
                "dxf_text",
                "dxfText",
                "text",
                "content",
                "result",
                "data",
                "raw_dxf",
                "rawDxf",
            ]:
                if k in x:
                    walk(x.get(k))

            # 나머지도 전체 탐색
            for v in x.values():
                walk(v)
            return

        if isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(obj)
    return best


def _cbl_v21_7_extract_dxf_bytes(response_bytes):
    """
    /dwg-to-dxf/ 응답에서 실제 DXF bytes만 추출.
    1) 이미 DXF면 그대로 사용
    2) JSON이면 json.loads 후 가장 큰 DXF 문자열 추출
    """
    import json

    b = bytes(response_bytes or b"")
    if len(b) < 500:
        return b"", {
            "kind": "empty",
            "response_bytes": len(b),
            "dxf_chars": 0,
            "dxf_bytes": 0,
        }

    # 이미 ASCII DXF인 경우
    head = b[:500000].upper()
    first = b[:5000000].upper()
    if b"SECTION" in head and (b"ENTITIES" in first or b"TABLES" in first):
        return b, {
            "kind": "raw-dxf-bytes",
            "response_bytes": len(b),
            "dxf_chars": None,
            "dxf_bytes": len(b),
        }

    # JSON인 경우
    text = None
    enc = "utf-8"

    for cand in ["utf-8-sig", "utf-8", "cp949", "euc-kr", "latin1"]:
        try:
            text = b.decode(cand)
            enc = cand
            break
        except Exception:
            pass

    if not text:
        return b"", {
            "kind": "decode-failed",
            "response_bytes": len(b),
            "dxf_chars": 0,
            "dxf_bytes": 0,
        }

    stripped = text.lstrip()
    if not stripped.startswith("{") and not stripped.startswith("["):
        return b"", {
            "kind": "not-json-not-dxf",
            "response_bytes": len(b),
            "head": stripped[:120],
            "dxf_chars": 0,
            "dxf_bytes": 0,
        }

    try:
        data = json.loads(text)
    except Exception as e:
        return b"", {
            "kind": "json-load-failed",
            "response_bytes": len(b),
            "error": repr(e),
            "head": stripped[:120],
            "dxf_chars": 0,
            "dxf_bytes": 0,
        }

    dxf_text = _cbl_v21_7_find_largest_dxf_string(data)

    if not dxf_text:
        keys = []
        try:
            if isinstance(data, dict):
                keys = list(data.keys())[:50]
        except Exception:
            pass

        return b"", {
            "kind": "json-no-dxf-string",
            "response_bytes": len(b),
            "json_keys": keys,
            "dxf_chars": 0,
            "dxf_bytes": 0,
        }

    # JSON 문자열로 온 DXF는 이미 파이썬 str이므로 UTF-8로 캐시
    out = dxf_text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8", errors="replace")

    return out, {
        "kind": "json-extracted-dxf",
        "response_bytes": len(b),
        "json_encoding": enc,
        "dxf_chars": len(dxf_text),
        "dxf_bytes": len(out),
    }


def _cbl_v21_7_cache_dxf_bytes(dxf_bytes, request, response, extract_report):
    import uuid
    import json
    import time

    MODE = "CBL_DWG_SERVER_RAW_CACHE_V21_7_JSON_EXTRACT_DXF"

    if not dxf_bytes or len(dxf_bytes) < 500:
        try:
            print("⚠️ CBL V21.7 skip cache: no dxf bytes", extract_report)
        except Exception:
            pass
        return response

    raw_id = uuid.uuid4().hex

    try:
        _cbl_v21_4_cleanup_cache()
    except Exception:
        try:
            _cbl_v21_cleanup_cache()
        except Exception:
            pass

    try:
        raw_path, meta_path = _cbl_v21_cache_paths(raw_id)
    except Exception:
        try:
            raw_path, meta_path = _cbl_v21_4_cache_paths(raw_id)
        except Exception:
            from pathlib import Path
            d = Path("tmp") / "cblcad_raw_cache_v21"
            d.mkdir(parents=True, exist_ok=True)
            raw_path = d / (raw_id + ".dxf")
            meta_path = d / (raw_id + ".json")

    raw_path.write_bytes(dxf_bytes)

    filename = ""
    try:
        f = request.FILES.get("file") or request.FILES.get("dwg")
        if f:
            filename = getattr(f, "name", "") or ""
    except Exception:
        pass

    meta = {
        "mode": MODE,
        "raw_id": raw_id,
        "bytes": len(dxf_bytes),
        "created_at": time.time(),
        "filename": filename,
        "extract_report": extract_report,
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    response["X-CBL-RAW-ID"] = raw_id
    response["X-CBL-RAW-BYTES"] = str(len(dxf_bytes))
    response["X-CBL-RAW-CACHE"] = MODE
    response["Access-Control-Expose-Headers"] = (
        "X-CBL-RAW-ID, X-CBL-RAW-BYTES, X-CBL-RAW-CACHE, "
        "X-CBL-RAW-MERGE, X-CBL-ADDED-LAYERS, X-CBL-ADDED-ENTITIES"
    )

    try:
        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_7_JSON_EXTRACT_DXF cached:", {
            "raw_id": raw_id,
            "bytes": len(dxf_bytes),
            "filename": filename,
            "extract_report": extract_report,
            "path": str(raw_path),
        })
    except Exception:
        pass

    return response


def cblcad_dwg_to_best_dxf_api(request, *args, **kwargs):
    """
    V21.7 inplace wrapper.
    기존 best open API를 호출하되, raw cache에는 JSON 전체가 아닌 실제 DXF만 저장한다.
    """
    from django.http import JsonResponse

    MODE = "CBL_DWG_SERVER_RAW_CACHE_V21_7_JSON_EXTRACT_DXF"

    base = None

    # 가장 원본에 가까운 best API를 우선 사용
    for name in [
        "_CBL_V21_4_ORIGINAL_BEST_DWG_TO_DXF_API",
        "_CBL_V21_3_ORIGINAL_BEST_DWG_TO_DXF_API",
    ]:
        try:
            fn = globals().get(name)
            if callable(fn):
                base = fn
                break
        except Exception:
            pass

    if base is None:
        return JsonResponse({
            "ok": False,
            "error": "original best dwg_to_dxf api missing",
            "mode": MODE,
        }, status=500)

    response = base(request, *args, **kwargs)

    try:
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            return response

        response_bytes, rebuilt = _cbl_v21_7_response_to_bytes_keep_response(response)
        dxf_bytes, extract_report = _cbl_v21_7_extract_dxf_bytes(response_bytes)

        return _cbl_v21_7_cache_dxf_bytes(dxf_bytes, request, rebuilt, extract_report)

    except Exception as e:
        try:
            print("❌ CBL_DWG_SERVER_RAW_CACHE_V21_7_JSON_EXTRACT_DXF failed:", repr(e))
        except Exception:
            pass
        return response


# 새로 정의한 wrapper에 즉시 csrf_exempt 적용
try:
    from django.views.decorators.csrf import csrf_exempt as _cbl_v21_7_csrf_exempt
    cblcad_dwg_to_best_dxf_api = _cbl_v21_7_csrf_exempt(cblcad_dwg_to_best_dxf_api)
    try:
        cblcad_dwg_to_best_dxf_api.csrf_exempt = True
    except Exception:
        pass
    print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_7 csrf_exempt applied")
except Exception as _e:
    try:
        print("⚠️ CBL_DWG_SERVER_RAW_CACHE_V21_7 csrf_exempt failed:", repr(_e))
    except Exception:
        pass

# CBL_DWG_SERVER_RAW_CACHE_V21_7_JSON_EXTRACT_DXF_END



# CBL_DWG_SERVER_RAW_CACHE_V21_8_PURE_DXF_EXTRACT_START
# V21.8:
# - JSON 전체 또는 JSON 문자열을 DXF로 오판하지 않음
# - 반드시 첫 유효 pair가 0 / SECTION인 순수 DXF만 캐시
# - 중첩 JSON 문자열도 재귀 파싱해서 내부 DXF만 추출

def _cbl_v21_8_decode_bytes(b):
    b = bytes(b or b"")
    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr", "latin1"]:
        try:
            return b.decode(enc), enc
        except Exception:
            pass
    return b.decode("utf-8", errors="replace"), "utf-8"


def _cbl_v21_8_norm(s):
    return str(s or "").replace("\r\n", "\n").replace("\r", "\n")


def _cbl_v21_8_pure_dxf_score(s):
    """
    순수 DXF만 통과.
    JSON처럼 { 로 시작하거나, 앞쪽에 metadata가 있으면 실패.
    """
    s = _cbl_v21_8_norm(s)
    if len(s) < 500:
        return 0

    ss = s.lstrip("\ufeff \t\n")
    if ss.startswith("{") or ss.startswith("["):
        return 0

    lines = ss.split("\n")
    vals = []
    for line in lines[:80]:
        t = line.strip()
        if t != "":
            vals.append(t)
        if len(vals) >= 8:
            break

    if len(vals) < 4:
        return 0

    # DXF 정상 시작: 0 / SECTION / 2 / HEADER or TABLES or BLOCKS or ENTITIES
    if vals[0] != "0" or vals[1].upper() != "SECTION":
        return 0

    head = ss[:1000000].upper()
    first5m = ss[:5000000].upper()

    score = 100
    if "\n2\nHEADER" in head or "\n2\nTABLES" in head:
        score += 100
    if "ENTITIES" in first5m:
        score += 100
    if "BLOCKS" in first5m:
        score += 50

    score += min(len(ss) // 100000, 1000)
    return score


def _cbl_v21_8_try_unescape_string(s):
    """
    JSON 안에 DXF가 한 번 더 escaped 된 문자열로 들어간 경우 보정.
    예: '0\\nSECTION\\n2\\nHEADER...'
    """
    s = str(s or "")
    if "\\n" not in s and "\\r" not in s:
        return ""

    try:
        # unicode_escape는 한글을 깨뜨릴 수 있으므로, 순수 DXF 판정용으로만 사용
        return s.encode("utf-8", errors="replace").decode("unicode_escape", errors="replace")
    except Exception:
        return ""


def _cbl_v21_8_find_best_dxf_in_obj(obj, depth=0):
    import json

    if depth > 12:
        return "", {
            "reason": "max-depth",
            "score": 0
        }

    best = ""
    best_info = {
        "reason": "none",
        "score": 0
    }

    def consider(candidate, reason):
        nonlocal best, best_info

        candidate = str(candidate or "")
        score = _cbl_v21_8_pure_dxf_score(candidate)

        if score > best_info.get("score", 0):
            best = _cbl_v21_8_norm(candidate)
            best_info = {
                "reason": reason,
                "score": score,
                "chars": len(best)
            }

    if isinstance(obj, str):
        s = obj

        # 1) 바로 순수 DXF인지
        consider(s, "string-pure-dxf")

        # 2) escaped DXF인지
        unescaped = _cbl_v21_8_try_unescape_string(s)
        if unescaped:
            consider(unescaped, "string-escaped-dxf")

        # 3) 문자열 자체가 JSON이면 내부 재탐색
        st = s.strip()
        if st.startswith("{") or st.startswith("["):
            try:
                nested = json.loads(st)
                dxf, info = _cbl_v21_8_find_best_dxf_in_obj(nested, depth + 1)
                if dxf and info.get("score", 0) > best_info.get("score", 0):
                    best = dxf
                    best_info = {
                        **info,
                        "reason": "nested-json-string/" + str(info.get("reason", ""))
                    }
            except Exception:
                pass

        return best, best_info

    if isinstance(obj, dict):
        # 흔한 키를 먼저 본다
        priority_keys = [
            "dxf",
            "dxf_text",
            "dxfText",
            "dxf_data",
            "dxfData",
            "raw_dxf",
            "rawDxf",
            "content",
            "text",
            "result",
            "data",
            "output",
            "body",
        ]

        for k in priority_keys:
            if k in obj:
                dxf, info = _cbl_v21_8_find_best_dxf_in_obj(obj.get(k), depth + 1)
                if dxf and info.get("score", 0) > best_info.get("score", 0):
                    best = dxf
                    best_info = {
                        **info,
                        "key": k
                    }

        for k, v in obj.items():
            dxf, info = _cbl_v21_8_find_best_dxf_in_obj(v, depth + 1)
            if dxf and info.get("score", 0) > best_info.get("score", 0):
                best = dxf
                best_info = {
                    **info,
                    "key": k
                }

        return best, best_info

    if isinstance(obj, (list, tuple)):
        for idx, v in enumerate(obj):
            dxf, info = _cbl_v21_8_find_best_dxf_in_obj(v, depth + 1)
            if dxf and info.get("score", 0) > best_info.get("score", 0):
                best = dxf
                best_info = {
                    **info,
                    "index": idx
                }

        return best, best_info

    return best, best_info


def _cbl_v21_8_extract_pure_dxf_bytes(response_bytes):
    import json

    b = bytes(response_bytes or b"")

    report = {
        "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_8_PURE_DXF_EXTRACT",
        "response_bytes": len(b),
        "kind": "",
    }

    if len(b) < 500:
        report["kind"] = "empty"
        return b"", report

    txt, enc = _cbl_v21_8_decode_bytes(b)
    report["response_encoding"] = enc

    # 1) 응답 자체가 순수 DXF인 경우
    score = _cbl_v21_8_pure_dxf_score(txt)
    if score > 0:
        out = _cbl_v21_8_norm(txt).encode("utf-8", errors="replace")
        report.update({
            "kind": "response-is-pure-dxf",
            "score": score,
            "dxf_bytes": len(out),
            "dxf_chars": len(_cbl_v21_8_norm(txt)),
        })
        return out, report

    # 2) JSON이면 내부에서 순수 DXF 문자열만 추출
    stripped = txt.lstrip()
    if not stripped.startswith("{") and not stripped.startswith("["):
        report.update({
            "kind": "not-json-not-pure-dxf",
            "head": stripped[:160],
        })
        return b"", report

    try:
        data = json.loads(txt)
    except Exception as e:
        report.update({
            "kind": "json-load-failed",
            "error": repr(e),
            "head": stripped[:160],
        })
        return b"", report

    dxf_text, info = _cbl_v21_8_find_best_dxf_in_obj(data)

    if not dxf_text:
        keys = []
        if isinstance(data, dict):
            keys = list(data.keys())[:80]

        report.update({
            "kind": "json-no-pure-dxf",
            "json_keys": keys,
            "best_info": info,
        })
        return b"", report

    # 마지막 방어: 순수 DXF 아니면 저장 금지
    final_score = _cbl_v21_8_pure_dxf_score(dxf_text)
    if final_score <= 0:
        report.update({
            "kind": "extracted-but-not-pure-dxf",
            "best_info": info,
            "head": str(dxf_text or "")[:160],
        })
        return b"", report

    out = _cbl_v21_8_norm(dxf_text).encode("utf-8", errors="replace")

    report.update({
        "kind": "json-extracted-pure-dxf",
        "best_info": info,
        "score": final_score,
        "dxf_chars": len(_cbl_v21_8_norm(dxf_text)),
        "dxf_bytes": len(out),
    })

    return out, report


def _cbl_v21_8_response_to_bytes_keep_response(response):
    from django.http import HttpResponse

    status = int(getattr(response, "status_code", 0) or 0)
    headers = {}

    try:
        for k, v in response.items():
            headers[str(k)] = str(v)
    except Exception:
        pass

    try:
        content = bytes(getattr(response, "content", b"") or b"")
        if content:
            return content, response
    except Exception:
        pass

    try:
        streaming_content = getattr(response, "streaming_content", None)
        if streaming_content is not None:
            chunks = []
            for chunk in streaming_content:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                chunks.append(bytes(chunk or b""))

            content = b"".join(chunks)

            content_type = headers.get("Content-Type") or headers.get("content-type") or "application/json"
            rebuilt = HttpResponse(content, status=status or 200, content_type=content_type)

            for k, v in headers.items():
                if k.lower() in {"content-type", "content-length"}:
                    continue
                try:
                    rebuilt[k] = v
                except Exception:
                    pass

            return content, rebuilt
    except Exception as e:
        try:
            print("❌ CBL V21.8 streaming read failed:", repr(e))
        except Exception:
            pass

    return b"", response


def _cbl_v21_8_cache_pure_dxf_bytes(dxf_bytes, request, response, report):
    import uuid
    import json
    import time
    from pathlib import Path

    MODE = "CBL_DWG_SERVER_RAW_CACHE_V21_8_PURE_DXF_EXTRACT"

    if not dxf_bytes or len(dxf_bytes) < 500:
        try:
            print("⚠️ CBL V21.8 skip cache:", report)
        except Exception:
            pass

        # 혹시 이전 wrapper가 붙인 헤더가 있으면 제거 시도
        for h in ["X-CBL-RAW-ID", "X-CBL-RAW-BYTES", "X-CBL-RAW-CACHE"]:
            try:
                if h in response:
                    del response[h]
            except Exception:
                pass

        return response

    # 캐시 직전 최종 검사
    txt, _enc = _cbl_v21_8_decode_bytes(dxf_bytes[:2000000])
    if _cbl_v21_8_pure_dxf_score(txt + "\nENTITIES") <= 0:
        try:
            print("❌ CBL V21.8 refused non-pure DXF cache:", {
                "head": txt[:200],
                "report": report,
            })
        except Exception:
            pass
        return response

    raw_id = uuid.uuid4().hex

    try:
        _cbl_v21_4_cleanup_cache()
    except Exception:
        try:
            _cbl_v21_cleanup_cache()
        except Exception:
            pass

    try:
        raw_path, meta_path = _cbl_v21_cache_paths(raw_id)
    except Exception:
        try:
            raw_path, meta_path = _cbl_v21_4_cache_paths(raw_id)
        except Exception:
            d = Path("tmp") / "cblcad_raw_cache_v21"
            d.mkdir(parents=True, exist_ok=True)
            raw_path = d / (raw_id + ".dxf")
            meta_path = d / (raw_id + ".json")

    raw_path.write_bytes(dxf_bytes)

    filename = ""
    try:
        f = request.FILES.get("file") or request.FILES.get("dwg")
        if f:
            filename = getattr(f, "name", "") or ""
    except Exception:
        pass

    meta = {
        "mode": MODE,
        "raw_id": raw_id,
        "bytes": len(dxf_bytes),
        "created_at": time.time(),
        "filename": filename,
        "extract_report": report,
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    response["X-CBL-RAW-ID"] = raw_id
    response["X-CBL-RAW-BYTES"] = str(len(dxf_bytes))
    response["X-CBL-RAW-CACHE"] = MODE
    response["Access-Control-Expose-Headers"] = (
        "X-CBL-RAW-ID, X-CBL-RAW-BYTES, X-CBL-RAW-CACHE, "
        "X-CBL-RAW-MERGE, X-CBL-ADDED-LAYERS, X-CBL-ADDED-ENTITIES"
    )

    try:
        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_8_PURE_DXF_EXTRACT cached:", {
            "raw_id": raw_id,
            "bytes": len(dxf_bytes),
            "filename": filename,
            "report": report,
            "path": str(raw_path),
        })
    except Exception:
        pass

    return response


def cblcad_dwg_to_best_dxf_api(request, *args, **kwargs):
    """
    V21.8 inplace wrapper.
    기존 best API 응답에서 순수 DXF만 추출해서 raw cache에 저장한다.
    JSON 전체는 절대 캐시하지 않는다.
    """
    from django.http import JsonResponse

    MODE = "CBL_DWG_SERVER_RAW_CACHE_V21_8_PURE_DXF_EXTRACT"

    base = None

    for name in [
        "_CBL_V21_4_ORIGINAL_BEST_DWG_TO_DXF_API",
        "_CBL_V21_3_ORIGINAL_BEST_DWG_TO_DXF_API",
    ]:
        try:
            fn = globals().get(name)
            if callable(fn):
                base = fn
                break
        except Exception:
            pass

    if base is None:
        return JsonResponse({
            "ok": False,
            "error": "original best dwg_to_dxf api missing",
            "mode": MODE,
        }, status=500)

    response = base(request, *args, **kwargs)

    try:
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            return response

        response_bytes, rebuilt = _cbl_v21_8_response_to_bytes_keep_response(response)
        dxf_bytes, report = _cbl_v21_8_extract_pure_dxf_bytes(response_bytes)

        return _cbl_v21_8_cache_pure_dxf_bytes(dxf_bytes, request, rebuilt, report)

    except Exception as e:
        try:
            print("❌ CBL_DWG_SERVER_RAW_CACHE_V21_8_PURE_DXF_EXTRACT failed:", repr(e))
        except Exception:
            pass
        return response


try:
    from django.views.decorators.csrf import csrf_exempt as _cbl_v21_8_csrf_exempt
    cblcad_dwg_to_best_dxf_api = _cbl_v21_8_csrf_exempt(cblcad_dwg_to_best_dxf_api)
    try:
        cblcad_dwg_to_best_dxf_api.csrf_exempt = True
    except Exception:
        pass
    print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_8 csrf_exempt applied")
except Exception as _e:
    try:
        print("⚠️ CBL_DWG_SERVER_RAW_CACHE_V21_8 csrf_exempt failed:", repr(_e))
    except Exception:
        pass

# CBL_DWG_SERVER_RAW_CACHE_V21_8_PURE_DXF_EXTRACT_END



# CBL_DWG_SERVER_RAW_CACHE_V21_9_SAFE_ENTITY_REBUILD_START
# V21.9:
# - _cbl_v21_merge_raw_and_client()를 다시 덮어씀
# - client DXF 엔티티 raw block을 그대로 붙이지 않음
# - 새 레이어 엔티티만 서버에서 안전한 pair 구조로 재생성
# - ENDSEC pair alignment 깨짐 방지

def _cbl_v21_9_norm(text):
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _cbl_v21_9_trim(v):
    return str(v or "").strip()


def _cbl_v21_9_lines(text):
    return _cbl_v21_9_norm(text).split("\n")


def _cbl_v21_9_pair_value(block_lines, code, default=""):
    code = str(code)
    for i in range(0, len(block_lines) - 1):
        if _cbl_v21_9_trim(block_lines[i]) == code:
            return _cbl_v21_9_trim(block_lines[i + 1])
    return default


def _cbl_v21_9_pair_values(block_lines, code):
    code = str(code)
    out = []
    for i in range(0, len(block_lines) - 1):
        if _cbl_v21_9_trim(block_lines[i]) == code:
            out.append(_cbl_v21_9_trim(block_lines[i + 1]))
    return out


def _cbl_v21_9_float(v, default="0.0"):
    try:
        if v is None or str(v).strip() == "":
            return str(default)
        float(str(v).strip())
        return str(v).strip()
    except Exception:
        return str(default)


def _cbl_v21_9_int(v, default="0"):
    try:
        if v is None or str(v).strip() == "":
            return str(default)
        return str(int(float(str(v).strip())))
    except Exception:
        return str(default)


def _cbl_v21_9_clean_text(v):
    v = str(v or "")
    v = v.replace("\r", " ").replace("\n", " ")
    return v


def _cbl_v21_9_parse_layer_names(dxf_text):
    lines = _cbl_v21_9_lines(dxf_text)
    out = {}

    i = 0
    while i < len(lines) - 1:
        if _cbl_v21_9_trim(lines[i]) == "0" and _cbl_v21_9_trim(lines[i + 1]).upper() == "LAYER":
            block = lines[i:i + 80]
            name = _cbl_v21_9_pair_value(block, "2")
            if name:
                out[name.upper()] = name
        i += 1

    out.setdefault("0", "0")
    return out


def _cbl_v21_9_make_safe_layer_record(name):
    import uuid

    name = str(name or "0").strip() or "0"
    handle = uuid.uuid4().hex[:8].upper()

    return [
        "0", "LAYER",
        "5", handle,
        "100", "AcDbSymbolTableRecord",
        "100", "AcDbLayerTableRecord",
        "2", name,
        "70", "0",
        "62", "7",
        "6", "Continuous",
    ]


def _cbl_v21_9_find_section_start(lines, section_name):
    section_name = str(section_name or "").upper()

    for i in range(0, len(lines) - 3):
        if (
            _cbl_v21_9_trim(lines[i]) == "0"
            and _cbl_v21_9_trim(lines[i + 1]).upper() == "SECTION"
            and _cbl_v21_9_trim(lines[i + 2]) == "2"
            and _cbl_v21_9_trim(lines[i + 3]).upper() == section_name
        ):
            return i

    return -1


def _cbl_v21_9_find_layer_table(lines):
    for i in range(0, len(lines) - 5):
        if (
            _cbl_v21_9_trim(lines[i]) == "0"
            and _cbl_v21_9_trim(lines[i + 1]).upper() == "TABLE"
            and _cbl_v21_9_trim(lines[i + 2]) == "2"
            and _cbl_v21_9_trim(lines[i + 3]).upper() == "LAYER"
        ):
            count_idx = -1

            for c in range(i + 4, min(i + 30, len(lines) - 1)):
                if _cbl_v21_9_trim(lines[c]) == "70":
                    count_idx = c + 1
                    break

            for j in range(i + 4, len(lines) - 1):
                if _cbl_v21_9_trim(lines[j]) == "0" and _cbl_v21_9_trim(lines[j + 1]).upper() == "ENDTAB":
                    return i, j, count_idx

    return -1, -1, -1


def _cbl_v21_9_insert_missing_layers(base_text, missing_layers):
    base_text = _cbl_v21_9_norm(base_text)
    lines = _cbl_v21_9_lines(base_text)

    if not missing_layers:
        return base_text, 0

    add_lines = []
    for _upper, name in missing_layers.items():
        add_lines.extend(_cbl_v21_9_make_safe_layer_record(name))

    table_start, endtab_idx, count_idx = _cbl_v21_9_find_layer_table(lines)

    if table_start >= 0 and endtab_idx >= 0:
        lines[endtab_idx:endtab_idx] = add_lines

        if count_idx >= 0 and count_idx < len(lines):
            try:
                old = int(_cbl_v21_9_trim(lines[count_idx]) or "0")
                lines[count_idx] = str(old + len(missing_layers))
            except Exception:
                pass

        return "\n".join(lines), len(missing_layers)

    table_lines = [
        "0", "SECTION",
        "2", "TABLES",
        "0", "TABLE",
        "2", "LAYER",
        "70", str(len(missing_layers) + 1),
    ]
    table_lines.extend(_cbl_v21_9_make_safe_layer_record("0"))
    table_lines.extend(add_lines)
    table_lines.extend(["0", "ENDTAB", "0", "ENDSEC"])

    ent_idx = _cbl_v21_9_find_section_start(lines, "ENTITIES")

    if ent_idx >= 0:
        lines[ent_idx:ent_idx] = table_lines
        return "\n".join(lines), len(missing_layers)

    return "\n".join(table_lines) + "\n" + base_text, len(missing_layers)


def _cbl_v21_9_extract_client_entity_blocks(dxf_text):
    lines = _cbl_v21_9_lines(dxf_text)

    ent_start = -1
    ent_end = -1

    for i in range(0, len(lines) - 3):
        if (
            _cbl_v21_9_trim(lines[i]) == "0"
            and _cbl_v21_9_trim(lines[i + 1]).upper() == "SECTION"
            and _cbl_v21_9_trim(lines[i + 2]) == "2"
            and _cbl_v21_9_trim(lines[i + 3]).upper() == "ENTITIES"
        ):
            ent_start = i + 4
            break

    if ent_start < 0:
        return []

    for j in range(ent_start, len(lines) - 1):
        if _cbl_v21_9_trim(lines[j]) == "0" and _cbl_v21_9_trim(lines[j + 1]).upper() == "ENDSEC":
            ent_end = j
            break

    if ent_end < 0:
        ent_end = len(lines)

    entity_types = {
        "LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC",
        "TEXT", "MTEXT"
    }

    blocks = []
    i = ent_start

    while i < ent_end - 1:
        if _cbl_v21_9_trim(lines[i]) == "0":
            typ = _cbl_v21_9_trim(lines[i + 1]).upper()

            if typ in entity_types:
                start = i
                k = i + 2

                while k < ent_end - 1:
                    if _cbl_v21_9_trim(lines[k]) == "0":
                        nxt = _cbl_v21_9_trim(lines[k + 1]).upper()
                        if nxt in entity_types or nxt == "ENDSEC":
                            break
                    k += 1

                block = lines[start:k]
                layer = _cbl_v21_9_pair_value(block, "8", "0") or "0"

                blocks.append({
                    "type": typ,
                    "layer": layer,
                    "layer_upper": layer.upper(),
                    "block": block,
                })

                i = k
                continue

        i += 1

    return blocks


def _cbl_v21_9_entity_base_pairs(typ, layer, block):
    out = ["0", typ, "8", layer or "0"]

    color = _cbl_v21_9_pair_value(block, "62")
    ltype = _cbl_v21_9_pair_value(block, "6")

    if color:
        out.extend(["62", _cbl_v21_9_int(color, "7")])
    if ltype:
        out.extend(["6", ltype])

    return out


def _cbl_v21_9_rebuild_line(ent):
    b = ent["block"]
    layer = ent["layer"]
    out = _cbl_v21_9_entity_base_pairs("LINE", layer, b)

    out.extend([
        "10", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "10"), "0"),
        "20", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "20"), "0"),
        "30", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "30"), "0"),
        "11", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "11"), "0"),
        "21", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "21"), "0"),
        "31", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "31"), "0"),
    ])

    return out


def _cbl_v21_9_rebuild_circle(ent, typ="CIRCLE"):
    b = ent["block"]
    layer = ent["layer"]
    out = _cbl_v21_9_entity_base_pairs(typ, layer, b)

    out.extend([
        "10", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "10"), "0"),
        "20", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "20"), "0"),
        "30", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "30"), "0"),
        "40", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "40"), "1"),
    ])

    if typ == "ARC":
        out.extend([
            "50", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "50"), "0"),
            "51", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "51"), "360"),
        ])

    return out


def _cbl_v21_9_rebuild_lwpolyline(ent):
    b = ent["block"]
    layer = ent["layer"]
    xs = _cbl_v21_9_pair_values(b, "10")
    ys = _cbl_v21_9_pair_values(b, "20")

    n = min(len(xs), len(ys))

    if n <= 0:
        return []

    out = _cbl_v21_9_entity_base_pairs("LWPOLYLINE", layer, b)

    out.extend([
        "100", "AcDbEntity",
        "100", "AcDbPolyline",
        "90", str(n),
        "70", _cbl_v21_9_int(_cbl_v21_9_pair_value(b, "70"), "0"),
    ])

    for i in range(n):
        out.extend([
            "10", _cbl_v21_9_float(xs[i], "0"),
            "20", _cbl_v21_9_float(ys[i], "0"),
        ])

    return out


def _cbl_v21_9_rebuild_polyline_as_lw(ent):
    b = ent["block"]
    layer = ent["layer"]
    xs = _cbl_v21_9_pair_values(b, "10")
    ys = _cbl_v21_9_pair_values(b, "20")

    n = min(len(xs), len(ys))

    if n <= 0:
        return []

    out = _cbl_v21_9_entity_base_pairs("LWPOLYLINE", layer, b)

    out.extend([
        "100", "AcDbEntity",
        "100", "AcDbPolyline",
        "90", str(n),
        "70", _cbl_v21_9_int(_cbl_v21_9_pair_value(b, "70"), "0"),
    ])

    for i in range(n):
        out.extend([
            "10", _cbl_v21_9_float(xs[i], "0"),
            "20", _cbl_v21_9_float(ys[i], "0"),
        ])

    return out


def _cbl_v21_9_rebuild_text(ent, typ="TEXT"):
    b = ent["block"]
    layer = ent["layer"]
    out = _cbl_v21_9_entity_base_pairs(typ, layer, b)

    text_value = _cbl_v21_9_pair_value(b, "1", "")
    if not text_value:
        text_value = _cbl_v21_9_pair_value(b, "3", "")

    out.extend([
        "10", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "10"), "0"),
        "20", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "20"), "0"),
        "30", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "30"), "0"),
        "40", _cbl_v21_9_float(_cbl_v21_9_pair_value(b, "40"), "250"),
        "1", _cbl_v21_9_clean_text(text_value),
    ])

    rot = _cbl_v21_9_pair_value(b, "50")
    if rot:
        out.extend(["50", _cbl_v21_9_float(rot, "0")])

    style = _cbl_v21_9_pair_value(b, "7")
    if style:
        out.extend(["7", style])

    return out


def _cbl_v21_9_rebuild_entity(ent):
    typ = ent.get("type", "").upper()

    try:
        if typ == "LINE":
            out = _cbl_v21_9_rebuild_line(ent)
        elif typ == "LWPOLYLINE":
            out = _cbl_v21_9_rebuild_lwpolyline(ent)
        elif typ == "POLYLINE":
            out = _cbl_v21_9_rebuild_polyline_as_lw(ent)
        elif typ == "CIRCLE":
            out = _cbl_v21_9_rebuild_circle(ent, "CIRCLE")
        elif typ == "ARC":
            out = _cbl_v21_9_rebuild_circle(ent, "ARC")
        elif typ == "TEXT":
            out = _cbl_v21_9_rebuild_text(ent, "TEXT")
        elif typ == "MTEXT":
            out = _cbl_v21_9_rebuild_text(ent, "MTEXT")
        else:
            return []

        if not out:
            return []

        # 최종 pair 정렬 방어
        if len(out) % 2 != 0:
            try:
                print("⚠️ CBL V21.9 odd entity rebuilt, dropped:", typ, len(out))
            except Exception:
                pass
            return []

        # group code 위치는 숫자여야 함
        for i in range(0, len(out), 2):
            try:
                int(str(out[i]).strip())
            except Exception:
                try:
                    print("⚠️ CBL V21.9 invalid group code in rebuilt entity, dropped:", {
                        "type": typ,
                        "index": i,
                        "value": out[i],
                    })
                except Exception:
                    pass
                return []

        return out

    except Exception as e:
        try:
            print("⚠️ CBL V21.9 rebuild entity failed:", typ, repr(e))
        except Exception:
            pass
        return []


def _cbl_v21_9_insert_rebuilt_entities(base_text, rebuilt_entities):
    base_text = _cbl_v21_9_norm(base_text)
    lines = _cbl_v21_9_lines(base_text)

    add_lines = []

    for ent_lines in rebuilt_entities:
        if ent_lines and len(ent_lines) % 2 == 0:
            add_lines.extend(ent_lines)

    if not add_lines:
        return base_text, 0

    # 전체 add_lines도 반드시 짝수여야 함
    if len(add_lines) % 2 != 0:
        try:
            print("❌ CBL V21.9 add_lines odd, skip all entities:", len(add_lines))
        except Exception:
            pass
        return base_text, 0

    ent_start = _cbl_v21_9_find_section_start(lines, "ENTITIES")
    insert_at = -1

    if ent_start >= 0:
        for j in range(ent_start + 4, len(lines) - 1):
            if _cbl_v21_9_trim(lines[j]) == "0" and _cbl_v21_9_trim(lines[j + 1]).upper() == "ENDSEC":
                insert_at = j
                break

    if insert_at >= 0:
        lines[insert_at:insert_at] = add_lines
        return "\n".join(lines), len(rebuilt_entities)

    sec_lines = ["0", "SECTION", "2", "ENTITIES"]
    sec_lines.extend(add_lines)
    sec_lines.extend(["0", "ENDSEC"])

    eof_idx = -1

    for i in range(len(lines) - 2, -1, -1):
        if _cbl_v21_9_trim(lines[i]) == "0" and i + 1 < len(lines) and _cbl_v21_9_trim(lines[i + 1]).upper() == "EOF":
            eof_idx = i
            break

    if eof_idx >= 0:
        lines[eof_idx:eof_idx] = sec_lines
    else:
        lines.extend(sec_lines)
        lines.extend(["0", "EOF"])

    return "\n".join(lines), len(rebuilt_entities)


def _cbl_v21_9_find_pair_errors(dxf_text, around_only=False, max_errors=10):
    lines = _cbl_v21_9_lines(dxf_text)
    bad = []

    # 전체 검사. 50MB라도 저장 시 1회라 감수 가능.
    limit = len(lines) - 1

    for i in range(0, limit, 2):
        code = _cbl_v21_9_trim(lines[i])
        if code == "":
            continue
        try:
            int(code)
        except Exception:
            bad.append({
                "line": i + 1,
                "value": lines[i][:120],
                "next": lines[i + 1][:120] if i + 1 < len(lines) else "",
            })
            if len(bad) >= max_errors:
                break

    return bad


def _cbl_v21_merge_raw_and_client(base_bytes, client_bytes):
    """
    V21.9 safe entity rebuild override.
    """
    base_text, base_enc = _cbl_v21_decode_dxf_bytes(base_bytes)
    client_text, _client_enc = _cbl_v21_decode_dxf_bytes(client_bytes)

    base_text = _cbl_v21_9_norm(base_text)
    client_text = _cbl_v21_9_norm(client_text)

    base_layers = _cbl_v21_9_parse_layer_names(base_text)
    client_layers = _cbl_v21_9_parse_layer_names(client_text)

    missing = {}

    for lu, name in client_layers.items():
        if lu not in base_layers:
            missing[lu] = name

    client_blocks = _cbl_v21_9_extract_client_entity_blocks(client_text)

    selected = []
    rebuilt = []

    for ent in client_blocks:
        if ent.get("layer_upper") in missing:
            selected.append(ent)
            rb = _cbl_v21_9_rebuild_entity(ent)
            if rb:
                rebuilt.append(rb)

    merged, added_layers = _cbl_v21_9_insert_missing_layers(base_text, missing)
    merged, added_entities = _cbl_v21_9_insert_rebuilt_entities(merged, rebuilt)

    if "\n0\nEOF" not in merged.upper():
        merged = merged.rstrip() + "\n0\nEOF\n"

    pair_errors = _cbl_v21_9_find_pair_errors(merged, max_errors=10)

    # pair가 깨지면 엔티티 삽입 없이 레이어만 반영한 버전으로 fallback
    fallback_used = False

    if pair_errors:
        fallback_used = True
        try:
            print("⚠️ CBL V21.9 pair errors after entity insert, fallback layer-only:", pair_errors[:3])
        except Exception:
            pass

        merged, added_layers = _cbl_v21_9_insert_missing_layers(base_text, missing)
        added_entities = 0

        if "\n0\nEOF" not in merged.upper():
            merged = merged.rstrip() + "\n0\nEOF\n"

        pair_errors = _cbl_v21_9_find_pair_errors(merged, max_errors=10)

    merged_bytes = _cbl_v21_encode_dxf_text(merged, base_enc)

    report = {
        "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_9_SAFE_ENTITY_REBUILD",
        "base_encoding": base_enc,
        "base_layers": len(base_layers),
        "client_layers": len(client_layers),
        "missing_layers": list(missing.values()),
        "added_layers": added_layers,
        "client_entities": len(client_blocks),
        "selected_new_layer_entities": len(selected),
        "rebuilt_entities": len(rebuilt),
        "added_entities": added_entities,
        "fallback_layer_only": fallback_used,
        "pair_errors": pair_errors,
        "base_bytes": len(base_bytes or b""),
        "client_bytes": len(client_bytes or b""),
        "merged_bytes": len(merged_bytes),
    }

    try:
        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_9 merge report:", report)
    except Exception:
        pass

    return merged_bytes, report


try:
    print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_9_SAFE_ENTITY_REBUILD installed")
except Exception:
    pass

# CBL_DWG_SERVER_RAW_CACHE_V21_9_SAFE_ENTITY_REBUILD_END



# CBL_DWG_SERVER_RAW_CACHE_V21_12_LAYER_PARSE_GUARD_START
# V21.12:
# - 원본 RAW DXF 레이어 파싱을 더 강하게 수행
# - base_layers가 2개로 오판되어 기존 도형 6500개를 새 객체로 재병합하는 문제 차단
# - 전체 pair scan은 생략해서 저장 속도 개선

def _cbl_v21_12_norm(text):
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _cbl_v21_12_clean_lines(text):
    out = []
    for line in _cbl_v21_12_norm(text).split("\n"):
        t = str(line or "").strip()
        if t != "":
            out.append(t)
    return out


def _cbl_v21_12_parse_layer_names(dxf_text):
    """
    더 강한 LAYER parser.
    DXF pair 정렬이 조금 틀어져도 0/LAYER 블록 기준으로 레이어명을 찾는다.
    """
    import re

    txt = _cbl_v21_12_norm(dxf_text)
    layers = {}

    # 1) line pair 방식
    lines = _cbl_v21_12_clean_lines(txt)

    i = 0
    while i < len(lines) - 1:
        if lines[i] == "0" and lines[i + 1].upper() == "LAYER":
            block = lines[i:i + 120]

            name = ""
            for j in range(2, len(block) - 1):
                if block[j] == "2":
                    name = block[j + 1].strip()
                    break

            if name:
                layers[name.upper()] = name

            i += 2
            continue

        i += 1

    # 2) regex fallback
    try:
        pattern = re.compile(
            r"(?:^|\n)\s*0\s*\n\s*LAYER\s*\n(?P<body>.*?)(?=\n\s*0\s*\n)",
            re.I | re.S
        )

        for m in pattern.finditer(txt):
            body = m.group("body") or ""
            mm = re.search(r"(?:^|\n)\s*2\s*\n\s*([^\n\r]+)", body, re.I)
            if mm:
                name = mm.group(1).strip()
                if name:
                    layers[name.upper()] = name
    except Exception:
        pass

    # 3) 최소 기본 레이어
    layers.setdefault("0", "0")
    return layers


# V21.9 내부 함수도 새 parser로 교체
try:
    _cbl_v21_9_parse_layer_names = _cbl_v21_12_parse_layer_names
except Exception:
    pass


# V21.11 speed tune 포함: 50MB 전체 pair scan 생략
def _cbl_v21_9_find_pair_errors(dxf_text, around_only=False, max_errors=10):
    return []


def _cbl_v21_merge_raw_and_client(base_bytes, client_bytes):
    """
    V21.12 safe merge.
    - 원본 RAW의 레이어를 제대로 읽는다.
    - 그래도 base layer 파싱이 이상하면 대량 병합을 막는다.
    """
    base_text, base_enc = _cbl_v21_decode_dxf_bytes(base_bytes)
    client_text, _client_enc = _cbl_v21_decode_dxf_bytes(client_bytes)

    base_text = _cbl_v21_9_norm(base_text)
    client_text = _cbl_v21_9_norm(client_text)

    base_layers = _cbl_v21_12_parse_layer_names(base_text)
    client_layers = _cbl_v21_12_parse_layer_names(client_text)

    missing = {}
    for lu, name in client_layers.items():
        if lu not in base_layers:
            missing[lu] = name

    client_blocks = _cbl_v21_9_extract_client_entity_blocks(client_text)

    selected = []
    rebuilt = []

    for ent in client_blocks:
        if ent.get("layer_upper") in missing:
            selected.append(ent)
            rb = _cbl_v21_9_rebuild_entity(ent)
            if rb:
                rebuilt.append(rb)

    mass_guard = False
    mass_guard_reason = ""

    # 핵심 방어:
    # 원본 레이어가 너무 적게 읽혔는데 client 레이어가 많으면 파싱 실패로 판단.
    if len(client_layers) >= 10 and len(base_layers) <= 5:
        mass_guard = True
        mass_guard_reason = "base_layer_parse_suspicious"

    # 새 레이어가 지나치게 많으면 기존 레이어를 새 레이어로 오판한 것일 가능성이 큼.
    if len(client_layers) >= 10 and len(missing) > max(5, int(len(client_layers) * 0.30)):
        mass_guard = True
        mass_guard_reason = "too_many_missing_layers"

    # 새 객체가 수천 개면 실제 사용자가 그린 신규 객체가 아니라 전체 재삽입일 가능성이 큼.
    if len(client_blocks) >= 1000 and len(selected) > max(50, int(len(client_blocks) * 0.10)):
        mass_guard = True
        mass_guard_reason = "too_many_selected_entities"

    if mass_guard:
        try:
            print("⚠️ CBL V21.12 MASS MERGE GUARD activated:", {
                "reason": mass_guard_reason,
                "base_layers": len(base_layers),
                "client_layers": len(client_layers),
                "missing_layers": len(missing),
                "client_entities": len(client_blocks),
                "selected_entities": len(selected),
            })
        except Exception:
            pass

        # 대량 중복 삽입 방지.
        # 원본 RAW만 유지해서 DWG 변환 성공/원본 보존을 우선한다.
        missing = {}
        selected = []
        rebuilt = []

    merged, added_layers = _cbl_v21_9_insert_missing_layers(base_text, missing)
    merged, added_entities = _cbl_v21_9_insert_rebuilt_entities(merged, rebuilt)

    if "\n0\nEOF" not in merged.upper():
        merged = merged.rstrip() + "\n0\nEOF\n"

    pair_errors = []

    merged_bytes = _cbl_v21_encode_dxf_text(merged, base_enc)

    report = {
        "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_12_LAYER_PARSE_GUARD",
        "base_encoding": base_enc,
        "base_layers": len(base_layers),
        "client_layers": len(client_layers),
        "missing_layers": list(missing.values()),
        "added_layers": added_layers,
        "client_entities": len(client_blocks),
        "selected_new_layer_entities": len(selected),
        "rebuilt_entities": len(rebuilt),
        "added_entities": added_entities,
        "mass_guard": mass_guard,
        "mass_guard_reason": mass_guard_reason,
        "pair_errors": pair_errors,
        "base_bytes": len(base_bytes or b""),
        "client_bytes": len(client_bytes or b""),
        "merged_bytes": len(merged_bytes),
        "base_layer_sample": list(base_layers.values())[:20],
        "client_layer_sample": list(client_layers.values())[:20],
    }

    try:
        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_12 merge report:", report)
    except Exception:
        pass

    return merged_bytes, report


try:
    print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_12_LAYER_PARSE_GUARD installed")
except Exception:
    pass

# CBL_DWG_SERVER_RAW_CACHE_V21_12_LAYER_PARSE_GUARD_END



# CBL_DWG_SERVER_RAW_CACHE_V21_13_SINGLE_SAVE_AUTO_START
# 버튼 추가 없음.
# 기존 DWG 저장 버튼 하나에서 내부 자동 판정:
# - 안전하면 RAW 병합 저장
# - 대량 오판 위험이면 client DXF로 빠른 저장

def _cbl_v21_merge_raw_and_client(base_bytes, client_bytes):
    base_text, base_enc = _cbl_v21_decode_dxf_bytes(base_bytes)
    client_text, _client_enc = _cbl_v21_decode_dxf_bytes(client_bytes)

    try:
        base_layers = _cbl_v21_12_parse_layer_names(base_text)
    except Exception:
        base_layers = {}

    try:
        client_layers = _cbl_v21_12_parse_layer_names(client_text)
    except Exception:
        client_layers = {}

    try:
        client_blocks = _cbl_v21_9_extract_client_entity_blocks(client_text)
    except Exception:
        client_blocks = []

    missing = {}
    for lu, name in client_layers.items():
        if lu not in base_layers:
            missing[lu] = name

    selected_count = 0
    try:
        for ent in client_blocks:
            if ent.get("layer_upper") in missing:
                selected_count += 1
    except Exception:
        selected_count = 0

    auto_fast = False
    reason = ""

    if len(client_layers) >= 10 and len(base_layers) <= 5:
        auto_fast = True
        reason = "base_layer_parse_suspicious"

    if len(client_layers) >= 10 and len(missing) > max(5, int(len(client_layers) * 0.30)):
        auto_fast = True
        reason = "too_many_missing_layers"

    if len(client_blocks) >= 1000 and selected_count > max(50, int(len(client_blocks) * 0.10)):
        auto_fast = True
        reason = "too_many_selected_entities"

    if auto_fast:
        report = {
            "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_13_SINGLE_SAVE_AUTO_FAST",
            "reason": reason,
            "base_layers": len(base_layers),
            "client_layers": len(client_layers),
            "missing_layers_count": len(missing),
            "client_entities": len(client_blocks),
            "selected_entities": selected_count,
            "base_bytes": len(base_bytes or b""),
            "client_bytes": len(client_bytes or b""),
            "merged_bytes": len(client_bytes or b""),
            "single_button": True,
            "auto_fast_save": True,
            "raw_merge_skipped": True,
        }

        try:
            print("⚡ CBL_DWG_SERVER_RAW_CACHE_V21_13 single-save auto fast:", report)
        except Exception:
            pass

        return client_bytes, report

    base_text = _cbl_v21_9_norm(base_text)
    client_text = _cbl_v21_9_norm(client_text)

    selected = []
    rebuilt = []

    for ent in client_blocks:
        if ent.get("layer_upper") in missing:
            selected.append(ent)
            rb = _cbl_v21_9_rebuild_entity(ent)
            if rb:
                rebuilt.append(rb)

    merged, added_layers = _cbl_v21_9_insert_missing_layers(base_text, missing)
    merged, added_entities = _cbl_v21_9_insert_rebuilt_entities(merged, rebuilt)

    if "\n0\nEOF" not in merged.upper():
        merged = merged.rstrip() + "\n0\nEOF\n"

    merged_bytes = _cbl_v21_encode_dxf_text(merged, base_enc)

    report = {
        "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_13_SINGLE_SAVE_AUTO_RAW",
        "base_encoding": base_enc,
        "base_layers": len(base_layers),
        "client_layers": len(client_layers),
        "missing_layers": list(missing.values()),
        "added_layers": added_layers,
        "client_entities": len(client_blocks),
        "selected_new_layer_entities": len(selected),
        "rebuilt_entities": len(rebuilt),
        "added_entities": added_entities,
        "base_bytes": len(base_bytes or b""),
        "client_bytes": len(client_bytes or b""),
        "merged_bytes": len(merged_bytes),
        "single_button": True,
        "auto_fast_save": False,
    }

    try:
        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_13 single-save auto raw:", report)
    except Exception:
        pass

    return merged_bytes, report


try:
    print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_13_SINGLE_SAVE_AUTO installed")
except Exception:
    pass

# CBL_DWG_SERVER_RAW_CACHE_V21_13_SINGLE_SAVE_AUTO_END



# CBL_DWG_SERVER_RAW_CACHE_V21_14_LAYER_TRACKING_START
# 버튼 추가 없음.
# 기존 저장 버튼 하나에서:
# - 프론트가 알려준 new_layers가 있으면 원본 RAW에 새 레이어 엔티티만 병합
# - 대량 오판 방지
# - new_layers가 없으면 기존 V21.13 자동 빠른 저장 흐름 유지

try:
    import contextvars as _cbl_v21_14_contextvars
    _CBL_V21_14_SAVE_CONTEXT = _cbl_v21_14_contextvars.ContextVar("CBL_V21_14_SAVE_CONTEXT", default={})
except Exception:
    _CBL_V21_14_SAVE_CONTEXT = None


def _cbl_v21_14_json_loads(v, default=None):
    import json
    if default is None:
        default = []
    try:
        if not v:
            return default
        return json.loads(v)
    except Exception:
        return default


def _cbl_v21_14_get_context():
    try:
        if _CBL_V21_14_SAVE_CONTEXT is not None:
            return _CBL_V21_14_SAVE_CONTEXT.get({})
    except Exception:
        pass
    return {}


try:
    _CBL_V21_14_ORIGINAL_SAVE_API = cblcad_dxf_to_dwg_save_api
except Exception:
    _CBL_V21_14_ORIGINAL_SAVE_API = None


def cblcad_dxf_to_dwg_save_api(request, *args, **kwargs):
    """
    Save API wrapper: request.POST의 V21.14 layer tracking 데이터를 context에 보관.
    """
    ctx = {}

    try:
        ctx = {
            "enabled": request.POST.get("cbl_v21_14_layer_tracking") == "1",
            "original_layers": _cbl_v21_14_json_loads(request.POST.get("cbl_v21_14_original_layers_json"), []),
            "current_layers": _cbl_v21_14_json_loads(request.POST.get("cbl_v21_14_current_layers_json"), []),
            "new_layers": _cbl_v21_14_json_loads(request.POST.get("cbl_v21_14_new_layers_json"), []),
            "layer_styles": _cbl_v21_14_json_loads(request.POST.get("cbl_v21_14_layer_styles_json"), []),
        }
    except Exception as e:
        try:
            print("⚠️ CBL V21.14 context parse failed:", repr(e))
        except Exception:
            pass
        ctx = {}

    token = None
    try:
        if _CBL_V21_14_SAVE_CONTEXT is not None:
            token = _CBL_V21_14_SAVE_CONTEXT.set(ctx)

        if callable(_CBL_V21_14_ORIGINAL_SAVE_API):
            return _CBL_V21_14_ORIGINAL_SAVE_API(request, *args, **kwargs)

        from django.http import JsonResponse
        return JsonResponse({"ok": False, "error": "V21.14 original save api missing"}, status=500)

    finally:
        try:
            if _CBL_V21_14_SAVE_CONTEXT is not None and token is not None:
                _CBL_V21_14_SAVE_CONTEXT.reset(token)
        except Exception:
            pass


try:
    from django.views.decorators.csrf import csrf_exempt as _cbl_v21_14_csrf_exempt
    cblcad_dxf_to_dwg_save_api = _cbl_v21_14_csrf_exempt(cblcad_dxf_to_dwg_save_api)
    try:
        cblcad_dxf_to_dwg_save_api.csrf_exempt = True
    except Exception:
        pass
except Exception:
    pass


def _cbl_v21_14_upper(v):
    return str(v or "").strip().upper()


def _cbl_v21_14_clean_name(v):
    return str(v or "").strip()


def _cbl_v21_14_layer_style_map(styles):
    out = {}

    if not isinstance(styles, list):
        return out

    for item in styles:
        if not isinstance(item, dict):
            continue

        name = item.get("name") or item.get("layer") or item.get("layerName") or ""
        name = _cbl_v21_14_clean_name(name)

        if not name:
            continue

        out[_cbl_v21_14_upper(name)] = item

    return out


def _cbl_v21_14_color_from_style(style):
    """
    return: (aci, true_color)
    aci는 1~255, true_color는 420 정수 or None
    """
    aci = "7"
    true_color = None

    if not isinstance(style, dict):
        return aci, true_color

    keys = [
        "aci", "colorIndex", "dxfColor", "colorNumber",
        "autocadColor", "color", "stroke", "lineColor"
    ]

    val = None
    for k in keys:
        if k in style and style.get(k) not in [None, ""]:
            val = style.get(k)
            break

    try:
        if isinstance(val, (int, float)):
            n = int(val)
            if 1 <= n <= 255:
                aci = str(n)
            return aci, true_color
    except Exception:
        pass

    s = str(val or "").strip()

    if s.startswith("#") and len(s) in [4, 7]:
        try:
            if len(s) == 4:
                r = int(s[1] * 2, 16)
                g = int(s[2] * 2, 16)
                b = int(s[3] * 2, 16)
            else:
                r = int(s[1:3], 16)
                g = int(s[3:5], 16)
                b = int(s[5:7], 16)

            true_color = str((r << 16) + (g << 8) + b)
        except Exception:
            true_color = None
        return aci, true_color

    try:
        n = int(float(s))
        if 1 <= n <= 255:
            aci = str(n)
    except Exception:
        pass

    return aci, true_color


def _cbl_v21_14_linetype_from_style(style):
    if not isinstance(style, dict):
        return "Continuous"

    for k in ["linetype", "lineType", "currentLineType", "dash", "dxfLineType"]:
        v = style.get(k)
        if v:
            s = str(v).strip()
            if s:
                if s.lower() in ["solid", "none"]:
                    return "Continuous"
                return s

    return "Continuous"


def _cbl_v21_14_make_layer_record(name, style=None):
    import uuid

    name = _cbl_v21_14_clean_name(name) or "0"
    handle = uuid.uuid4().hex[:8].upper()

    aci, true_color = _cbl_v21_14_color_from_style(style or {})
    ltype = _cbl_v21_14_linetype_from_style(style or {})

    out = [
        "0", "LAYER",
        "5", handle,
        "100", "AcDbSymbolTableRecord",
        "100", "AcDbLayerTableRecord",
        "2", name,
        "70", "0",
        "62", aci,
    ]

    if true_color:
        out.extend(["420", true_color])

    out.extend(["6", ltype])

    return out


def _cbl_v21_14_find_layer_table(lines):
    for i in range(0, len(lines) - 5):
        if (
            _cbl_v21_9_trim(lines[i]) == "0"
            and _cbl_v21_9_trim(lines[i + 1]).upper() == "TABLE"
            and _cbl_v21_9_trim(lines[i + 2]) == "2"
            and _cbl_v21_9_trim(lines[i + 3]).upper() == "LAYER"
        ):
            count_idx = -1

            for c in range(i + 4, min(i + 30, len(lines) - 1)):
                if _cbl_v21_9_trim(lines[c]) == "70":
                    count_idx = c + 1
                    break

            for j in range(i + 4, len(lines) - 1):
                if _cbl_v21_9_trim(lines[j]) == "0" and _cbl_v21_9_trim(lines[j + 1]).upper() == "ENDTAB":
                    return i, j, count_idx

    return -1, -1, -1


def _cbl_v21_14_insert_layers(base_text, new_layers, style_map):
    base_text = _cbl_v21_9_norm(base_text)
    lines = _cbl_v21_9_lines(base_text)

    layer_names = []
    seen = set()

    for name in new_layers:
        name = _cbl_v21_14_clean_name(name)
        if not name:
            continue

        u = _cbl_v21_14_upper(name)
        if u in seen:
            continue

        seen.add(u)
        layer_names.append(name)

    if not layer_names:
        return base_text, 0

    add_lines = []

    for name in layer_names:
        style = style_map.get(_cbl_v21_14_upper(name), {})
        add_lines.extend(_cbl_v21_14_make_layer_record(name, style))

    table_start, endtab_idx, count_idx = _cbl_v21_14_find_layer_table(lines)

    if table_start >= 0 and endtab_idx >= 0:
        lines[endtab_idx:endtab_idx] = add_lines

        if count_idx >= 0 and count_idx < len(lines):
            try:
                old = int(_cbl_v21_9_trim(lines[count_idx]) or "0")
                lines[count_idx] = str(old + len(layer_names))
            except Exception:
                pass

        return "\n".join(lines), len(layer_names)

    # fallback: 기존 V21.9 방식 사용
    missing = {}
    for name in layer_names:
        missing[_cbl_v21_14_upper(name)] = name

    return _cbl_v21_9_insert_missing_layers(base_text, missing)


def _cbl_v21_merge_raw_and_client(base_bytes, client_bytes):
    """
    V21.14 single-button layer tracking merge.
    """
    ctx = _cbl_v21_14_get_context()

    base_text, base_enc = _cbl_v21_decode_dxf_bytes(base_bytes)
    client_text, _client_enc = _cbl_v21_decode_dxf_bytes(client_bytes)

    base_text = _cbl_v21_9_norm(base_text)
    client_text = _cbl_v21_9_norm(client_text)

    explicit_new_layers = []
    if ctx.get("enabled"):
        for name in ctx.get("new_layers") or []:
            name = _cbl_v21_14_clean_name(name)
            if name:
                explicit_new_layers.append(name)

    style_map = _cbl_v21_14_layer_style_map(ctx.get("layer_styles") or [])

    if explicit_new_layers:
        new_upper = set(_cbl_v21_14_upper(n) for n in explicit_new_layers if _cbl_v21_14_upper(n))

        client_blocks = _cbl_v21_9_extract_client_entity_blocks(client_text)

        selected = []
        rebuilt = []

        for ent in client_blocks:
            if ent.get("layer_upper") in new_upper:
                selected.append(ent)
                rb = _cbl_v21_9_rebuild_entity(ent)
                if rb:
                    rebuilt.append(rb)

        # 새 레이어가 너무 많거나 선택 엔티티가 너무 많으면 다시 빠른 저장으로 회피
        unsafe = False
        unsafe_reason = ""

        if len(explicit_new_layers) > 20:
            unsafe = True
            unsafe_reason = "too_many_explicit_new_layers"

        if len(selected) > 500:
            unsafe = True
            unsafe_reason = "too_many_explicit_new_layer_entities"

        if unsafe:
            report = {
                "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_14_AUTO_FAST_UNSAFE_EXPLICIT",
                "reason": unsafe_reason,
                "explicit_new_layers": explicit_new_layers,
                "selected_entities": len(selected),
                "client_bytes": len(client_bytes or b""),
                "base_bytes": len(base_bytes or b""),
                "merged_bytes": len(client_bytes or b""),
                "single_button": True,
                "auto_fast_save": True,
            }
            try:
                print("⚡ CBL_DWG_SERVER_RAW_CACHE_V21_14 auto fast unsafe explicit:", report)
            except Exception:
                pass
            return client_bytes, report

        merged, added_layers = _cbl_v21_14_insert_layers(base_text, explicit_new_layers, style_map)
        merged, added_entities = _cbl_v21_9_insert_rebuilt_entities(merged, rebuilt)

        if "\n0\nEOF" not in merged.upper():
            merged = merged.rstrip() + "\n0\nEOF\n"

        merged_bytes = _cbl_v21_encode_dxf_text(merged, base_enc)

        report = {
            "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_14_LAYER_TRACKING_RAW_MERGE",
            "explicit_new_layers": explicit_new_layers,
            "added_layers": added_layers,
            "client_entities": len(client_blocks),
            "selected_new_layer_entities": len(selected),
            "rebuilt_entities": len(rebuilt),
            "added_entities": added_entities,
            "base_bytes": len(base_bytes or b""),
            "client_bytes": len(client_bytes or b""),
            "merged_bytes": len(merged_bytes),
            "single_button": True,
            "auto_fast_save": False,
            "preserve_raw": True,
        }

        try:
            print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_14 layer tracking raw merge:", report)
        except Exception:
            pass

        return merged_bytes, report

    # 새 레이어 정보가 없으면 기존 V21.13 자동 빠른 저장 판정 유지
    try:
        base_layers = _cbl_v21_12_parse_layer_names(base_text)
    except Exception:
        base_layers = {}

    try:
        client_layers = _cbl_v21_12_parse_layer_names(client_text)
    except Exception:
        client_layers = {}

    try:
        client_blocks = _cbl_v21_9_extract_client_entity_blocks(client_text)
    except Exception:
        client_blocks = []

    missing = {}
    for lu, name in client_layers.items():
        if lu not in base_layers:
            missing[lu] = name

    selected_count = 0
    try:
        for ent in client_blocks:
            if ent.get("layer_upper") in missing:
                selected_count += 1
    except Exception:
        selected_count = 0

    auto_fast = False
    reason = ""

    if len(client_layers) >= 10 and len(base_layers) <= 5:
        auto_fast = True
        reason = "base_layer_parse_suspicious"

    if len(client_layers) >= 10 and len(missing) > max(5, int(len(client_layers) * 0.30)):
        auto_fast = True
        reason = "too_many_missing_layers"

    if len(client_blocks) >= 1000 and selected_count > max(50, int(len(client_blocks) * 0.10)):
        auto_fast = True
        reason = "too_many_selected_entities"

    if auto_fast:
        report = {
            "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_14_SINGLE_SAVE_AUTO_FAST",
            "reason": reason,
            "base_layers": len(base_layers),
            "client_layers": len(client_layers),
            "missing_layers_count": len(missing),
            "client_entities": len(client_blocks),
            "selected_entities": selected_count,
            "base_bytes": len(base_bytes or b""),
            "client_bytes": len(client_bytes or b""),
            "merged_bytes": len(client_bytes or b""),
            "single_button": True,
            "auto_fast_save": True,
            "raw_merge_skipped": True,
        }

        try:
            print("⚡ CBL_DWG_SERVER_RAW_CACHE_V21_14 single-save auto fast:", report)
        except Exception:
            pass

        return client_bytes, report

    # 안전 케이스만 RAW 병합
    selected = []
    rebuilt = []

    for ent in client_blocks:
        if ent.get("layer_upper") in missing:
            selected.append(ent)
            rb = _cbl_v21_9_rebuild_entity(ent)
            if rb:
                rebuilt.append(rb)

    merged, added_layers = _cbl_v21_9_insert_missing_layers(base_text, missing)
    merged, added_entities = _cbl_v21_9_insert_rebuilt_entities(merged, rebuilt)

    if "\n0\nEOF" not in merged.upper():
        merged = merged.rstrip() + "\n0\nEOF\n"

    merged_bytes = _cbl_v21_encode_dxf_text(merged, base_enc)

    report = {
        "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_14_SAFE_RAW_MERGE",
        "base_encoding": base_enc,
        "base_layers": len(base_layers),
        "client_layers": len(client_layers),
        "missing_layers": list(missing.values()),
        "added_layers": added_layers,
        "client_entities": len(client_blocks),
        "selected_new_layer_entities": len(selected),
        "rebuilt_entities": len(rebuilt),
        "added_entities": added_entities,
        "base_bytes": len(base_bytes or b""),
        "client_bytes": len(client_bytes or b""),
        "merged_bytes": len(merged_bytes),
        "single_button": True,
        "auto_fast_save": False,
    }

    try:
        print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_14 safe raw merge:", report)
    except Exception:
        pass

    return merged_bytes, report


try:
    print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_14_LAYER_TRACKING installed")
except Exception:
    pass

# CBL_DWG_SERVER_RAW_CACHE_V21_14_LAYER_TRACKING_END



# CBL_DWG_SERVER_RAW_CACHE_V21_15_LAYER_TRACKING_SERVER_FIX_START
# 목적:
# - 프론트 V21.14.1 이 보낸 cbl_new_layers_v21 값을 서버 저장 병합 기준으로 사용
# - base_layers 가 2로 잘못 읽혀도 새 레이어 1개만 병합 가능하게 함
# - 기존 V21.13 single save auto wrapper 이후 최종 저장 함수 재래핑
import json as _cbl_v21_15_json
import time as _cbl_v21_15_time
import traceback as _cbl_v21_15_traceback
from pathlib import Path as _cbl_v21_15_Path

try:
    _CBL_V21_15_PREV_SAVE = cblcad_dxf_to_dwg_save_api
except Exception:
    _CBL_V21_15_PREV_SAVE = None


def _cbl_v21_15_req_get(request, key, default=None):
    try:
        if hasattr(request, "POST") and key in request.POST:
            return request.POST.get(key, default)
    except Exception:
        pass

    try:
        if hasattr(request, "META"):
            meta_key = "HTTP_" + key.upper().replace("-", "_")
            if meta_key in request.META:
                return request.META.get(meta_key, default)
    except Exception:
        pass

    return default


def _cbl_v21_15_parse_json_list(value):
    if not value:
        return []

    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]

    try:
        data = _cbl_v21_15_json.loads(value)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass

    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]

    return []


def _cbl_v21_15_extract_client_dxf(request):
    # 기존 저장 payload 명칭들이 섞여 있을 수 있어서 넓게 탐색
    keys = [
        "dxf",
        "dxf_text",
        "dxfText",
        "content",
        "data",
        "file",
    ]

    for key in keys:
        try:
            v = request.POST.get(key)
            if v and "SECTION" in v and "ENTITIES" in v:
                return v
        except Exception:
            pass

    try:
        body = request.body.decode("utf-8", errors="ignore")
        if body and "SECTION" in body and "ENTITIES" in body:
            return body
    except Exception:
        pass

    return ""


def _cbl_v21_15_split_pairs(dxf_text):
    lines = str(dxf_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(lines) % 2 == 1:
        lines = lines[:-1]

    pairs = []
    for i in range(0, len(lines), 2):
        code = lines[i].strip()
        val = lines[i + 1] if i + 1 < len(lines) else ""
        pairs.append((code, val))
    return pairs


def _cbl_v21_15_pairs_to_text(pairs):
    out = []
    for code, val in pairs:
        out.append(str(code))
        out.append(str(val))
    return "\n".join(out) + "\n"


def _cbl_v21_15_find_section_pairs(pairs, section_name):
    start = None
    end = None
    target = str(section_name).upper()

    i = 0
    while i < len(pairs) - 1:
        code, val = pairs[i]
        if code == "0" and str(val).upper() == "SECTION":
            if i + 1 < len(pairs) and pairs[i + 1][0] == "2" and str(pairs[i + 1][1]).upper() == target:
                start = i
                j = i + 2
                while j < len(pairs):
                    c, v = pairs[j]
                    if c == "0" and str(v).upper() == "ENDSEC":
                        end = j
                        return start, end
                    j += 1
        i += 1

    return None, None


def _cbl_v21_15_iter_entities(pairs):
    s, e = _cbl_v21_15_find_section_pairs(pairs, "ENTITIES")
    if s is None or e is None:
        return []

    ents = []
    cur = []

    for code, val in pairs[s + 2:e]:
        if code == "0":
            if cur:
                ents.append(cur)
            cur = [(code, val)]
        else:
            if cur:
                cur.append((code, val))

    if cur:
        ents.append(cur)

    return ents


def _cbl_v21_15_entity_layer(ent):
    for code, val in ent:
        if str(code).strip() == "8":
            return str(val).strip()
    return "0"


def _cbl_v21_15_entity_type(ent):
    if ent and str(ent[0][0]).strip() == "0":
        return str(ent[0][1]).strip().upper()
    return ""


def _cbl_v21_15_sanitize_entity(ent, fallback_layer):
    # 안전한 기본 pair만 유지. handle/owner/reactor 등 충돌 위험 제거.
    deny = {
        "5",    # handle
        "330",  # owner
        "331", "332", "333", "334", "335",
        "360", "361",
        "102", "1001", "1002", "1003", "1004", "1005",
    }

    cleaned = []
    has_layer = False

    etype = _cbl_v21_15_entity_type(ent)
    if not etype:
        return []

    for code, val in ent:
        c = str(code).strip()

        if c in deny:
            continue

        # subclass marker 는 일부 깨진 DXF에서 ODA 오류 원인이 되므로 최소화
        if c == "100":
            continue

        if c == "8":
            has_layer = True
            cleaned.append((c, str(val).strip() or fallback_layer))
        else:
            cleaned.append((c, val))

    if not has_layer:
        cleaned.insert(1, ("8", fallback_layer or "0"))

    return cleaned


def _cbl_v21_15_merge_new_layer_entities(base_dxf, client_dxf, new_layers):
    base_pairs = _cbl_v21_15_split_pairs(base_dxf)
    client_pairs = _cbl_v21_15_split_pairs(client_dxf)

    ent_start, ent_end = _cbl_v21_15_find_section_pairs(base_pairs, "ENTITIES")
    if ent_start is None or ent_end is None:
        return None, {
            "ok": False,
            "reason": "base_entities_section_not_found",
        }

    new_set = {str(x).strip() for x in new_layers if str(x).strip()}
    new_upper_set = {str(x).strip().upper() for x in new_layers if str(x).strip()}
    client_entities = _cbl_v21_15_iter_entities(client_pairs)

    selected = []
    for ent in client_entities:
        layer = _cbl_v21_15_entity_layer(ent)
        layer_upper = str(layer or "").strip().upper()
        if layer in new_set or layer_upper in new_upper_set:
            safe = _cbl_v21_15_sanitize_entity(ent, layer)
            if safe:
                selected.append(safe)

    insert_pairs = []
    for ent in selected:
        insert_pairs.extend(ent)

    merged_pairs = base_pairs[:ent_end] + insert_pairs + base_pairs[ent_end:]

    report = {
        "ok": True,
        "mode": "CBL_DWG_SERVER_RAW_CACHE_V21_15_LAYER_TRACKING_SERVER_FIX",
        "new_layers_from_front": list(new_layers),
        "client_entities": len(client_entities),
        "selected_new_layer_entities": len(selected),
        "added_entities": len(selected),
        "base_pairs": len(base_pairs),
        "client_pairs": len(client_pairs),
        "merged_pairs": len(merged_pairs),
    }

    return _cbl_v21_15_pairs_to_text(merged_pairs), report


def _cbl_v21_15_get_raw_cache_path(raw_id):
    try:
        base = _cbl_v21_15_Path(settings.BASE_DIR) / "tmp" / "cblcad_raw_cache_v21"
    except Exception:
        base = _cbl_v21_15_Path("tmp") / "cblcad_raw_cache_v21"

    p = base / (str(raw_id).strip() + ".dxf")
    if p.exists():
        return p

    return None


@csrf_exempt
def cblcad_dxf_to_dwg_save_api(request, *args, **kwargs):
    started = _cbl_v21_15_time.time()

    raw_id = (
        _cbl_v21_15_req_get(request, "cbl_raw_id")
        or _cbl_v21_15_req_get(request, "raw_id")
        or _cbl_v21_15_req_get(request, "X-CBL-DWG-RAW-ID")
    )

    new_layers_raw = (
        _cbl_v21_15_req_get(request, "cbl_new_layers_v21")
        or _cbl_v21_15_req_get(request, "cbl_v21_14_new_layers_json")
        or _cbl_v21_15_req_get(request, "cbl_v21_16_new_layers")
        or _cbl_v21_15_req_get(request, "X-CBL-DWG-NEW-LAYERS")
    )

    original_layers_raw = _cbl_v21_15_req_get(request, "cbl_original_layers_v21")
    tracking_flag = _cbl_v21_15_req_get(request, "cbl_layer_tracking_v21")

    new_layers = _cbl_v21_15_parse_json_list(new_layers_raw)
    original_layers = _cbl_v21_15_parse_json_list(original_layers_raw)

    print("🚀 CBL V21.15 save request start:", {
        "raw_id": raw_id,
        "tracking_flag": tracking_flag,
        "new_layers": new_layers,
        "new_layers_count": len(new_layers),
        "original_layers_count": len(original_layers),
        "method": getattr(request, "method", None),
    })

    # 새 레이어가 프론트에서 정확히 넘어온 경우: 서버가 그 값만 신뢰해서 RAW 병합용 DXF를 request에 주입
    if raw_id and new_layers:
        try:
            raw_path = _cbl_v21_15_get_raw_cache_path(raw_id)
            if raw_path:
                base_bytes = raw_path.read_bytes()
                base_dxf = ""
                for _enc in ("utf-8-sig", "cp949", "euc-kr", "utf-16"):
                    try:
                        base_dxf = base_bytes.decode(_enc)
                        break
                    except Exception:
                        base_dxf = ""
                if not base_dxf:
                    base_dxf = base_bytes.decode("utf-8", errors="ignore")

                client_dxf = _cbl_v21_15_extract_client_dxf(request)

                merged_dxf, merge_report = _cbl_v21_15_merge_new_layer_entities(
                    base_dxf,
                    client_dxf,
                    new_layers,
                )

                # V22 보강: 신규 레이어 엔티티만 넣으면 LAYER TABLE에 레이어 레코드가 없을 수 있다.
                # 기존 V21.14의 레이어 삽입기를 재사용해서 레이어 표까지 먼저 보강한다.
                if merged_dxf:
                    try:
                        _styles_raw = request.POST.get("cbl_v21_14_layer_styles_json") or "[]"
                        _styles = _cbl_v21_14_json_loads(_styles_raw, [])
                        _style_map = _cbl_v21_14_layer_style_map(_styles)
                        merged_dxf, _added_layers_v22 = _cbl_v21_14_insert_layers(merged_dxf, new_layers, _style_map)
                        try:
                            merge_report["added_layers_v22"] = _added_layers_v22
                        except Exception:
                            pass
                    except Exception as _e:
                        print("⚠️ CBL V22 layer table insert failed:", repr(_e))

                print("✅ CBL V21.15 front-new-layer raw merge:", merge_report)

                if merged_dxf and merge_report.get("selected_new_layer_entities", 0) > 0:
                    # QueryDict는 immutable일 수 있으므로 copy해서 교체
                    try:
                        post = request.POST.copy()
                        post["dxf"] = merged_dxf
                        post["dxf_text"] = merged_dxf
                        post["dxfText"] = merged_dxf
                        post["cbl_v21_15_force_front_new_layers"] = "1"
                        post["cbl_v21_15_merge_report"] = _cbl_v21_15_json.dumps(merge_report, ensure_ascii=False)
                        request.POST = post
                    except Exception as e:
                        print("⚠️ CBL V21.15 request.POST replace failed:", repr(e))

                    # raw_id를 지워서 하위 V21.13 mass_guard/fast_auto가 다시 개입하지 못하게 함
                    try:
                        post = request.POST.copy()
                        post["cbl_raw_id"] = ""
                        post["raw_id"] = ""
                        request.POST = post
                    except Exception:
                        pass

                else:
                    print("⚠️ CBL V21.15 no selected entities, fallback prev save:", merge_report)
            else:
                print("⚠️ CBL V21.15 raw cache path not found:", raw_id)

        except Exception as e:
            print("❌ CBL V21.15 merge exception:", repr(e))
            print(_cbl_v21_15_traceback.format_exc())

    try:
        if _CBL_V21_15_PREV_SAVE is None:
            from django.http import JsonResponse
            return JsonResponse({
                "ok": False,
                "error": "V21.15 previous save handler missing",
            }, status=500)

        resp = _CBL_V21_15_PREV_SAVE(request, *args, **kwargs)

        elapsed = round(_cbl_v21_15_time.time() - started, 2)
        try:
            status = getattr(resp, "status_code", None)
            size = len(getattr(resp, "content", b"") or b"")
        except Exception:
            status = None
            size = None

        print("✅ CBL V21.15 save request end:", {
            "elapsed_sec": elapsed,
            "status": status,
            "response_bytes": size,
            "new_layers": new_layers,
        })

        return resp

    except Exception as e:
        elapsed = round(_cbl_v21_15_time.time() - started, 2)
        print("❌ CBL V21.15 save exception:", {
            "elapsed_sec": elapsed,
            "error": repr(e),
            "new_layers": new_layers,
            "raw_id": raw_id,
        })
        print(_cbl_v21_15_traceback.format_exc())
        raise


try:
    cblcad_dxf_to_dwg_save_api = csrf_exempt(cblcad_dxf_to_dwg_save_api)
    print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_15_LAYER_TRACKING_SERVER_FIX installed")
except Exception as e:
    print("❌ CBL_DWG_SERVER_RAW_CACHE_V21_15 install failed:", e)

# CBL_DWG_SERVER_RAW_CACHE_V21_15_LAYER_TRACKING_SERVER_FIX_END



# CBL_DWG_SERVER_RAW_CACHE_V21_16_FAST_SAVE_DEFAULT_START
# 목적:
# - 큰 원본 RAW DXF(예: 51MB)를 매번 ODA로 재변환하지 않음
# - 기존 DWG 저장 버튼은 빠른 client DXF 저장 우선
# - V21.14/V21.15 preserve_raw 경로를 기본 저장에서 우회
import time as _cbl_v21_16_time
import traceback as _cbl_v21_16_traceback
import json as _cbl_v21_16_json

try:
    _CBL_V21_16_PREV_SAVE = cblcad_dxf_to_dwg_save_api
except Exception:
    _CBL_V21_16_PREV_SAVE = None


def _cbl_v21_16_get_post(request, key, default=""):
    try:
        return request.POST.get(key, default)
    except Exception:
        return default


def _cbl_v21_16_get_body_text(request):
    try:
        body = request.body or b""
        return body.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _cbl_v21_16_client_dxf_from_request(request):
    keys = [
        "dxf",
        "dxf_text",
        "dxfText",
        "content",
        "data",
    ]

    for key in keys:
        try:
            v = request.POST.get(key)
            if v and "SECTION" in v and "ENTITIES" in v:
                return v
        except Exception:
            pass

    body = _cbl_v21_16_get_body_text(request)
    if body and "SECTION" in body and "ENTITIES" in body:
        return body

    return ""


def _cbl_v21_16_raw_id_present(request):
    keys = [
        "cbl_raw_id",
        "raw_id",
        "cbl_dwg_raw_id",
    ]

    for key in keys:
        try:
            v = request.POST.get(key)
            if v:
                return v
        except Exception:
            pass

    try:
        return request.META.get("HTTP_X_CBL_DWG_RAW_ID", "")
    except Exception:
        return ""


def _cbl_v21_16_new_layers(request):
    raw = ""

    for key in ["cbl_new_layers_v21", "cbl_v21_14_new_layers_json", "cbl_v21_16_new_layers", "new_layers", "cblNewLayers"]:
        try:
            raw = request.POST.get(key)
            if raw:
                break
        except Exception:
            pass

    if not raw:
        try:
            raw = request.META.get("HTTP_X_CBL_DWG_NEW_LAYERS", "")
        except Exception:
            raw = ""

    if not raw:
        return []

    try:
        data = _cbl_v21_16_json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass

    return [x.strip() for x in str(raw).split(",") if x.strip()]


@csrf_exempt
def cblcad_dxf_to_dwg_save_api(request, *args, **kwargs):
    started = _cbl_v21_16_time.time()

    raw_id = _cbl_v21_16_raw_id_present(request)
    new_layers = _cbl_v21_16_new_layers(request)
    client_dxf = _cbl_v21_16_client_dxf_from_request(request)
    client_bytes = len(client_dxf.encode("utf-8", errors="ignore")) if client_dxf else 0

    # 핵심:
    # 기본은 빠른 client DXF 저장이다.
    # 단, 프론트 V22가 신규 레이어 원본유지 병합을 명시하면 raw_id를 지우지 않는다.
    # 이렇게 해야 V21.15가 원본 RAW DXF에 신규 레이어 엔티티를 병합할 수 있다.
    force_fast = not bool(new_layers)  # CBL_V27: fast_client unless new layers exist
    try:
        if (
            request.POST.get("cbl_v22_preserve_raw") == "1"
            or request.POST.get("cbl_v22_save_mode") == "raw_preserve_new_layers"
        ):
            force_fast = False
    except Exception:
        pass

    if force_fast and raw_id:
        try:
            post = request.POST.copy()
            post["cbl_v21_16_fast_save_default"] = "1"
            post["cbl_v21_16_original_raw_id"] = str(raw_id)
            post["cbl_v21_16_new_layers"] = _cbl_v21_16_json.dumps(new_layers, ensure_ascii=False)

            # raw preserve 진입 조건 제거
            post["cbl_raw_id"] = ""
            post["raw_id"] = ""
            post["cbl_dwg_raw_id"] = ""

            # client dxf를 명확히 유지
            if client_dxf:
                post["dxf"] = client_dxf
                post["dxf_text"] = client_dxf
                post["dxfText"] = client_dxf

            request.POST = post

            try:
                request.META["HTTP_X_CBL_DWG_RAW_ID"] = ""
            except Exception:
                pass

            print("⚡ CBL V21.16 FAST SAVE DEFAULT activated:", {
                "reason": "skip_51mb_raw_preserve_conversion",
                "raw_id_removed": True,
                "original_raw_id": raw_id,
                "new_layers": new_layers,
                "client_bytes": client_bytes,
                "expected": "small_client_dxf_to_dwg",
            })

        except Exception as e:
            print("⚠️ CBL V21.16 fast mode setup failed:", repr(e))

    try:
        if _CBL_V21_16_PREV_SAVE is None:
            from django.http import JsonResponse
            return JsonResponse({
                "ok": False,
                "error": "V21.16 previous save handler missing",
            }, status=500)

        resp = _CBL_V21_16_PREV_SAVE(request, *args, **kwargs)

        elapsed = round(_cbl_v21_16_time.time() - started, 2)
        status = getattr(resp, "status_code", None)
        size = None
        try:
            size = len(getattr(resp, "content", b"") or b"")
        except Exception:
            pass

        print("✅ CBL V21.16 FAST SAVE DEFAULT end:", {
            "elapsed_sec": elapsed,
            "status": status,
            "response_bytes": size,
            "new_layers": new_layers,
            "client_bytes": client_bytes,
            "force_fast": force_fast,
        })

        try:
            resp["X-CBL-V22-SAVE"] = "fast_client" if force_fast else "raw_preserve_new_layers"
            resp["Access-Control-Expose-Headers"] = (
                str(resp.get("Access-Control-Expose-Headers", ""))
                + ", X-CBL-V22-SAVE, X-CBL-RAW-MERGE, X-CBL-ADDED-LAYERS, X-CBL-ADDED-ENTITIES"
            ).strip(", ")
        except Exception:
            pass

        return resp

    except Exception as e:
        elapsed = round(_cbl_v21_16_time.time() - started, 2)
        print("❌ CBL V21.16 FAST SAVE DEFAULT exception:", {
            "elapsed_sec": elapsed,
            "error": repr(e),
            "raw_id": raw_id,
            "new_layers": new_layers,
        })
        print(_cbl_v21_16_traceback.format_exc())
        raise


try:
    cblcad_dxf_to_dwg_save_api = csrf_exempt(cblcad_dxf_to_dwg_save_api)
    print("✅ CBL_DWG_SERVER_RAW_CACHE_V21_16_FAST_SAVE_DEFAULT installed")
except Exception as e:
    print("❌ CBL_DWG_SERVER_RAW_CACHE_V21_16 install failed:", e)

# CBL_DWG_SERVER_RAW_CACHE_V21_16_FAST_SAVE_DEFAULT_END



# CBL_DWG_SAVE_ACAD2004_V23_MARKER
print('✅ CBL_DWG_SAVE_ACAD2004_V23 active: DXF->DWG ODA output version locked to ACAD2004 for Korean BigFont test')


# CBL_V27_ACAD2004_SWEEP_NEW_LAYERS_MARKER
print('✅ CBL_V27 active: ACAD2004 sweep + force_fast by new_layers')


# CBL_DWG_RAW_TO_ODA_FILE_FLOW_DEBUG_V28A_START
# 목적:
# - merged_pairs 숫자가 아니라 실제 디스크에 써진 DXF 크기 확인
# - raw_input / sanitized / normalized / ODA input / ODA output 흐름을 콘솔과 _cblcad_oda_debug/v28a_file_flow 에 남김
# - 기능 변경 없음. 디버그 로그만 추가.
def _cbl_v28a_debug_file_flow(stage, file_path, extra=None):
    try:
        import os as _os
        import json as _json
        import time as _time
        import shutil as _shutil
        from pathlib import Path as _Path
        try:
            from django.conf import settings as _settings
            _base_dir = _Path(getattr(_settings, 'BASE_DIR', _Path.cwd()))
        except Exception:
            _base_dir = _Path.cwd()

        p = _Path(file_path)
        exists = p.exists()
        size = p.stat().st_size if exists and p.is_file() else None

        pair_est = None
        head = ''
        tail = ''
        section_hits = {}

        if exists and p.is_file():
            try:
                b = p.read_bytes()
                sample_head = b[:2048]
                sample_tail = b[-2048:] if len(b) > 2048 else b
                head = sample_head.decode('utf-8', errors='replace')[:800]
                tail = sample_tail.decode('utf-8', errors='replace')[-800:]

                # 120MB 이하는 라인 수를 세서 DXF pair 추정. S-501 원본 DXF 46MB 수준은 충분히 가능.
                if len(b) <= 120 * 1024 * 1024:
                    txt = b.decode('utf-8', errors='ignore')
                    lines = txt.replace('\r\n', '\n').replace('\r', '\n').split('\n')
                    pair_est = len(lines) // 2
                    upper = txt.upper()
                    for sec in ['HEADER', 'CLASSES', 'TABLES', 'BLOCKS', 'ENTITIES', 'OBJECTS']:
                        section_hits[sec] = upper.count('\n2\n' + sec)
                    section_hits['EOF'] = upper.count('\n0\nEOF')
            except Exception as _e:
                head = 'READ_ERR: ' + repr(_e)

        report = {
            'stage': str(stage),
            'path': str(p),
            'exists': exists,
            'size_bytes': size,
            'size_mb': round((size or 0) / 1024 / 1024, 3) if size is not None else None,
            'pair_est': pair_est,
            'sections': section_hits,
            'extra': extra or {},
        }

        print('🔎 CBL_V28A_ODA_FILE_FLOW:', report)

        # 성공 시 tmp_root가 삭제되어도 나중에 확인할 수 있게 별도 복사본 보관
        try:
            debug_root = _base_dir / '_cblcad_oda_debug' / 'v28a_file_flow'
            debug_root.mkdir(parents=True, exist_ok=True)
            stamp = _time.strftime('%Y%m%d_%H%M%S')
            safe_stage = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in str(stage))[:80]
            meta_path = debug_root / f'{stamp}_{safe_stage}.json'
            meta_path.write_text(_json.dumps({**report, 'head': head, 'tail': tail}, ensure_ascii=False, indent=2), encoding='utf-8')
            if exists and p.is_file() and size is not None and size <= 160 * 1024 * 1024:
                dst = debug_root / f'{stamp}_{safe_stage}_{p.name}'
                _shutil.copy2(p, dst)
                print('🔎 CBL_V28A_ODA_FILE_COPY:', str(dst), 'size=', dst.stat().st_size)
        except Exception as _e:
            print('⚠️ CBL_V28A debug copy failed:', repr(_e))

        return report
    except Exception as _e:
        try:
            print('⚠️ CBL_V28A_ODA_FILE_FLOW failed:', repr(_e))
        except Exception:
            pass
        return None
# CBL_DWG_RAW_TO_ODA_FILE_FLOW_DEBUG_V28A_END

# ===== CBL CAD V29 CLEAN API START =====

from django.views.decorators.csrf import csrf_exempt as _cbl_v29_csrf_exempt

def _cbl_v29_root():
    from pathlib import Path
    from django.conf import settings
    root = Path(settings.BASE_DIR) / "tmp" / "cblcad_v29_sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root

def _cbl_v29_find_oda():
    from pathlib import Path
    candidates = [
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None

def _cbl_v29_oda_convert(src_file, out_dir, version="ACAD2004", output_type="DWG"):
    import subprocess
    import shutil
    from pathlib import Path

    oda = _cbl_v29_find_oda()
    if not oda:
        raise RuntimeError("ODAFileConverter not found")

    src_file = Path(src_file)
    out_dir = Path(out_dir)
    in_dir = out_dir / "_oda_in"
    result_dir = out_dir / "_oda_out"

    if in_dir.exists():
        shutil.rmtree(in_dir)
    if result_dir.exists():
        shutil.rmtree(result_dir)

    in_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)

    safe_src = in_dir / src_file.name
    shutil.copy2(src_file, safe_src)

    cmd = [oda, str(in_dir), str(result_dir), version, output_type, "0", "1"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    ext = "." + output_type.lower()
    found = list(result_dir.rglob("*" + ext))
    if not found:
        raise RuntimeError(
            "ODA output not found / "
            f"returncode={proc.returncode} / "
            f"stdout={proc.stdout[:1000]} / "
            f"stderr={proc.stderr[:1000]}"
        )

    found = sorted(found, key=lambda p: p.stat().st_size, reverse=True)[0]
    final = out_dir / f"converted{ext}"
    shutil.copy2(found, final)

    return {
        "returncode": proc.returncode,
        "output": str(final),
        "output_bytes": final.stat().st_size,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }

def _cbl_v29_safe_layer_name(name):
    name = str(name or "").strip()
    if not name:
        name = "CBL_V29_LAYER"
    bad = '<>/\\":;?*|=`,'
    for ch in bad:
        name = name.replace(ch, "_")
    return name[:255]

def _cbl_v29_collect_bbox(msp):
    import math
    pts = []

    def add_point(p):
        try:
            x = float(p[0])
            y = float(p[1])
            if math.isfinite(x) and math.isfinite(y):
                pts.append((x, y))
        except Exception:
            pass

    for e in msp:
        try:
            t = e.dxftype()
            if t == "LINE":
                add_point(e.dxf.start)
                add_point(e.dxf.end)
            elif t == "LWPOLYLINE":
                for p in e.get_points("xy"):
                    add_point(p)
            elif t == "POLYLINE":
                for v in e.vertices:
                    add_point(v.dxf.location)
            elif t in {"CIRCLE", "ARC"}:
                c = e.dxf.center
                r = float(e.dxf.radius)
                add_point((c[0] - r, c[1] - r))
                add_point((c[0] + r, c[1] + r))
            elif t in {"TEXT", "MTEXT", "INSERT"}:
                if hasattr(e.dxf, "insert"):
                    add_point(e.dxf.insert)
        except Exception:
            continue

    if not pts:
        return {
            "minx": 0.0,
            "miny": 0.0,
            "maxx": 10000.0,
            "maxy": 10000.0,
            "width": 10000.0,
            "height": 10000.0,
        }

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)

    return {
        "minx": minx,
        "miny": miny,
        "maxx": maxx,
        "maxy": maxy,
        "width": max(maxx - minx, 1.0),
        "height": max(maxy - miny, 1.0),
    }

@_cbl_v29_csrf_exempt
def cblcad_v29_open_session(request):
    import json
    import uuid
    import shutil
    from django.http import JsonResponse

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    upload = request.FILES.get("file") or request.FILES.get("dwg")
    if not upload:
        return JsonResponse({"ok": False, "error": "file field required"}, status=400)

    session_id = uuid.uuid4().hex
    session_dir = _cbl_v29_root() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    original_name = upload.name or "input.dwg"
    original_path = session_dir / "original.dwg"

    with original_path.open("wb") as f:
        for chunk in upload.chunks():
            f.write(chunk)

    try:
        conv_dir = session_dir / "open_convert"
        conv_dir.mkdir(parents=True, exist_ok=True)

        result = _cbl_v29_oda_convert(
            original_path,
            conv_dir,
            version="ACAD2004",
            output_type="DXF",
        )

        base_dxf = session_dir / "base.dxf"
        shutil.copy2(result["output"], base_dxf)

        meta = {
            "session_id": session_id,
            "original_name": original_name,
            "original_bytes": original_path.stat().st_size,
            "base_dxf_bytes": base_dxf.stat().st_size,
            "base_dxf": str(base_dxf),
        }

        (session_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("[CBL_V29_OPEN_SESSION]", meta)

        return JsonResponse({
            "ok": True,
            "session_id": session_id,
            "original_name": original_name,
            "original_bytes": original_path.stat().st_size,
            "base_dxf_bytes": base_dxf.stat().st_size,
        })

    except Exception as e:
        return JsonResponse({
            "ok": False,
            "error": repr(e),
            "session_id": session_id,
        }, status=500)

@_cbl_v29_csrf_exempt
def cblcad_v29_save_ops(request):
    import json
    import shutil
    import time
    from django.http import JsonResponse, FileResponse

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception as e:
        return JsonResponse({"ok": False, "error": "invalid json", "detail": repr(e)}, status=400)

    session_id = str(body.get("session_id") or "").strip()
    ops = body.get("ops") or []

    if not session_id:
        return JsonResponse({"ok": False, "error": "session_id required"}, status=400)

    session_dir = _cbl_v29_root() / session_id
    base_dxf = session_dir / "base.dxf"

    if not base_dxf.exists():
        return JsonResponse({
            "ok": False,
            "error": "base.dxf not found",
            "session_id": session_id,
        }, status=404)

    try:
        import ezdxf
        from ezdxf import recover

        try:
            doc = ezdxf.readfile(base_dxf)
            read_mode = "normal"
        except Exception:
            doc, auditor = recover.readfile(base_dxf)
            read_mode = "recover"

        msp = doc.modelspace()
        applied = []

        for op in ops:
            if not isinstance(op, dict):
                continue

            typ = op.get("type")

            if typ == "create_layer":
                name = _cbl_v29_safe_layer_name(op.get("name"))
                color = int(op.get("color") or 7)
                linetype = str(op.get("linetype") or "Continuous")

                if not doc.layers.has_entry(name):
                    doc.layers.new(
                        name=name,
                        dxfattribs={
                            "color": color,
                            "linetype": linetype,
                        },
                    )
                    applied.append({"type": typ, "name": name, "created": True})
                else:
                    layer = doc.layers.get(name)
                    layer.dxf.color = color
                    layer.dxf.linetype = linetype
                    applied.append({"type": typ, "name": name, "created": False, "updated": True})

            elif typ == "add_test_line":
                layer_name = _cbl_v29_safe_layer_name(op.get("layer") or "CBL_V29_API_LAYER")
                color = int(op.get("color") or 1)

                if not doc.layers.has_entry(layer_name):
                    doc.layers.new(
                        name=layer_name,
                        dxfattribs={
                            "color": color,
                            "linetype": "Continuous",
                        },
                    )

                bbox = _cbl_v29_collect_bbox(msp)
                x1 = bbox["minx"] + bbox["width"] * 0.08
                x2 = bbox["minx"] + bbox["width"] * 0.28
                y = bbox["maxy"] - bbox["height"] * 0.08

                msp.add_line(
                    (x1, y, 0),
                    (x2, y, 0),
                    dxfattribs={
                        "layer": layer_name,
                        "color": color,
                        "linetype": "Continuous",
                    },
                )

                applied.append({
                    "type": typ,
                    "layer": layer_name,
                    "start": [x1, y, 0],
                    "end": [x2, y, 0],
                })


            elif typ == "add_lwpolyline":
                layer_name = _cbl_v29_safe_layer_name(op.get("layer") or "CBL_V29_LAYER")
                color = int(op.get("color") or 256)
                linetype = str(op.get("linetype") or "Continuous")
                points = op.get("points") or []
                closed = bool(op.get("closed"))

                if not doc.layers.has_entry(layer_name):
                    doc.layers.new(
                        name=layer_name,
                        dxfattribs={
                            "color": 7,
                            "linetype": "Continuous",
                        },
                    )

                clean_points = []
                for p in points:
                    if not isinstance(p, (list, tuple)) or len(p) < 2:
                        continue
                    clean_points.append((float(p[0]), float(p[1])))

                if len(clean_points) >= 2:
                    msp.add_lwpolyline(
                        clean_points,
                        close=closed,
                        dxfattribs={
                            "layer": layer_name,
                            "color": color,
                            "linetype": linetype,
                        },
                    )
                    applied.append({
                        "type": typ,
                        "layer": layer_name,
                        "points_count": len(clean_points),
                        "closed": closed,
                    })

            elif typ == "add_circle":
                layer_name = _cbl_v29_safe_layer_name(op.get("layer") or "CBL_V29_LAYER")
                color = int(op.get("color") or 256)
                center = op.get("center") or [0, 0, 0]
                radius = float(op.get("radius") or 1)

                if not doc.layers.has_entry(layer_name):
                    doc.layers.new(
                        name=layer_name,
                        dxfattribs={
                            "color": 7,
                            "linetype": "Continuous",
                        },
                    )

                cx = float(center[0])
                cy = float(center[1])
                cz = float(center[2]) if len(center) > 2 else 0.0

                if radius > 0:
                    msp.add_circle(
                        (cx, cy, cz),
                        radius,
                        dxfattribs={
                            "layer": layer_name,
                            "color": color,
                        },
                    )
                    applied.append({
                        "type": typ,
                        "layer": layer_name,
                        "center": [cx, cy, cz],
                        "radius": radius,
                    })

            elif typ == "add_text":
                layer_name = _cbl_v29_safe_layer_name(op.get("layer") or "CBL_V29_LAYER")
                color = int(op.get("color") or 256)
                text_value = str(op.get("text") or "")
                insert = op.get("insert") or [0, 0, 0]
                height = float(op.get("height") or 250)
                rotation = float(op.get("rotation") or 0)

                if not doc.layers.has_entry(layer_name):
                    doc.layers.new(
                        name=layer_name,
                        dxfattribs={
                            "color": 7,
                            "linetype": "Continuous",
                        },
                    )

                ix = float(insert[0])
                iy = float(insert[1])
                iz = float(insert[2]) if len(insert) > 2 else 0.0

                if text_value:
                    ent = msp.add_text(
                        text_value,
                        dxfattribs={
                            "layer": layer_name,
                            "color": color,
                            "height": height,
                            "rotation": rotation,
                        },
                    )
                    ent.dxf.insert = (ix, iy, iz)
                    applied.append({
                        "type": typ,
                        "layer": layer_name,
                        "text": text_value,
                        "insert": [ix, iy, iz],
                        "height": height,
                        "rotation": rotation,
                    })


            elif typ == "add_line":
                layer_name = _cbl_v29_safe_layer_name(op.get("layer") or "CBL_V29_LAYER")
                color = int(op.get("color") or 256)
                linetype = str(op.get("linetype") or "Continuous")
                start = op.get("start") or [0, 0, 0]
                end = op.get("end") or [1000, 0, 0]

                if not doc.layers.has_entry(layer_name):
                    doc.layers.new(
                        name=layer_name,
                        dxfattribs={
                            "color": 7,
                            "linetype": "Continuous",
                        },
                    )

                sx, sy = float(start[0]), float(start[1])
                sz = float(start[2]) if len(start) > 2 else 0.0
                ex, ey = float(end[0]), float(end[1])
                ez = float(end[2]) if len(end) > 2 else 0.0

                msp.add_line(
                    (sx, sy, sz),
                    (ex, ey, ez),
                    dxfattribs={
                        "layer": layer_name,
                        "color": color,
                        "linetype": linetype,
                    },
                )

                applied.append({
                    "type": typ,
                    "layer": layer_name,
                    "start": [sx, sy, sz],
                    "end": [ex, ey, ez],
                })

        stamp = time.strftime("%Y%m%d_%H%M%S")
        edited_dxf = session_dir / f"edited_{stamp}.dxf"
        doc.saveas(edited_dxf)

        conv_dir = session_dir / f"save_convert_{stamp}"
        conv_dir.mkdir(parents=True, exist_ok=True)

        result = _cbl_v29_oda_convert(
            edited_dxf,
            conv_dir,
            version="ACAD2004",
            output_type="DWG",
        )

        out_dwg = session_dir / f"cblcad_v29_saved_{stamp}.dwg"
        shutil.copy2(result["output"], out_dwg)

        log = {
            "session_id": session_id,
            "read_mode": read_mode,
            "ops_count": len(ops),
            "applied": applied,
            "base_dxf_bytes": base_dxf.stat().st_size,
            "edited_dxf_bytes": edited_dxf.stat().st_size,
            "out_dwg_bytes": out_dwg.stat().st_size,
            "out_dwg": str(out_dwg),
        }

        (session_dir / f"save_log_{stamp}.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("[CBL_V29_SAVE_OPS]", log)

        response = FileResponse(
            open(out_dwg, "rb"),
            as_attachment=True,
            filename="cblcad_v29_saved.dwg",
        )
        response["X-CBL-V29-Session"] = session_id
        response["X-CBL-V29-DWG-Bytes"] = str(out_dwg.stat().st_size)
        return response

    except Exception as e:
        return JsonResponse({
            "ok": False,
            "error": repr(e),
            "session_id": session_id,
        }, status=500)

# ===== CBL CAD V29 CLEAN API END =====
