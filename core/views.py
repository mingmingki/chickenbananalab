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
from .models import (
    Post,
    UserProfile,
    ExperienceVault,
    VisitLog,
    AIAutoWriterSetting,
    AIAutoKeywordQueue,
)
from .forms import PostForm, NicknameForm, ExperienceVaultForm
from .naver_news import recommend_keywords_from_news
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
        "description": "Django, Python, AI, 클라우드, 웹앱 개발 기록을 다룹니다.",
        "theme": "tech",
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


def validate_generated_content_or_raise(content, title="", min_length=500):
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


def home(request):
    posts = Post.objects.filter(is_published=True).order_by("-created_at")[:32]
    market_data = get_market_data()

    return render(request, "core/home.html", {
        "posts": posts,
        "market_data": market_data,
    })


def category_page(request, slug):
    page = CATEGORY_PAGES.get(slug)

    if page is None:
        raise Http404("존재하지 않는 페이지입니다.")

    posts = Post.objects.filter(
        category=slug,
        is_published=True,
    ).order_by("-created_at")[:15]

    return render(request, "core/category.html", {
        "page": page,
        "slug": slug,
        "posts": posts,
    })


def search(request):
    query = request.GET.get("q", "").strip()
    results = Post.objects.none()

    category_keywords = {
        "건축": "architecture",
        "부동산": "realestate",
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

@user_passes_test(can_write_post)
def post_create(request):
    initial_category = request.GET.get("category", "")

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
            min_length=500,
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

    category = request.POST.get("category", "tech")
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
건축·부동산·건설 관련 주제는 현장 실무자 관점에서 해석을 넣어줘.
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
- 중간중간 “실무적으로 보면”, “현장에서는”, “개인적으로는”, “조금 더 현실적으로 보면” 같은 자연스러운 연결 문장을 사용할 것
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
- 중간에는 단순 요약보다 “현장에서 보면 어떤 의미인지” 해석
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
                min_length=500,
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
                    min_length=500,
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

    category = request.POST.get("category", "tech")

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
    ("architecture", "건축"),
    ("realestate", "부동산"),
    ("finance", "금융"),
    ("tech", "테크"),
    ("life", "일상"),
]


def get_enabled_ai_auto_categories(setting):
    categories = []

    if setting.use_architecture:
        categories.append(("architecture", "건축"))

    if setting.use_realestate:
        categories.append(("realestate", "부동산"))

    if setting.use_finance:
        categories.append(("finance", "금융"))

    if setting.use_tech:
        categories.append(("tech", "테크"))

    if setting.use_life:
        categories.append(("life", "일상"))

    return categories


def refill_ai_auto_keyword_queue(setting):
    """
    기존 AI 자동글 생성 모달의 '오늘자 키워드 추천'과 동일한
    recommend_keywords_from_news(category) 로직을 사용해서
    시간별 자동글 대기열을 채운다.
    """

    try:
        keyword_count = int(setting.keyword_count_per_category or 7)
    except ValueError:
        keyword_count = 7

    keyword_count = max(1, min(keyword_count, 7))

    enabled_categories = get_enabled_ai_auto_categories(setting)

    if not enabled_categories:
        raise ValueError("사용할 카테고리를 1개 이상 선택해주세요.")

    recommended_by_category = {}

    for category, category_label in enabled_categories:
        # 기존 '오늘자 키워드 추천'과 같은 뉴스 기반 추천 함수 사용
        raw_keywords = recommend_keywords_from_news(category)

        cleaned_items = []
        seen_keywords = set()

        for item in raw_keywords:
            if isinstance(item, dict):
                keyword = str(item.get("keyword", "")).strip()
                reason = str(item.get("reason", "")).strip()
                item_category_label = str(item.get("category", "")).strip()
            else:
                keyword = str(item).strip()
                reason = ""
                item_category_label = category_label

            if not keyword:
                continue

            keyword_key = keyword.replace(" ", "").lower()

            if keyword_key in seen_keywords:
                continue

            cleaned_items.append({
                "keyword": keyword,
                "reason": reason,
                "news_context": reason,
                "category_label": item_category_label or category_label,
            })

            seen_keywords.add(keyword_key)

            if len(cleaned_items) >= keyword_count:
                break

        recommended_by_category[category] = cleaned_items

    with transaction.atomic():
        # 아직 생성하지 않은 대기 키워드는 오늘자 뉴스 기반 키워드로 교체
        AIAutoKeywordQueue.objects.filter(status="waiting").delete()

        created_count = 0
        order = 1

        # 건축1 → 부동산1 → 금융1 → 테크1 → 일상1 순서로 저장
        for keyword_index in range(keyword_count):
            for category, category_label in enabled_categories:
                category_items = recommended_by_category.get(category, [])

                if keyword_index >= len(category_items):
                    continue

                item = category_items[keyword_index]

                AIAutoKeywordQueue.objects.create(
                    category=category,
                    keyword=item["keyword"],
                    reason=item.get("reason", ""),
                    news_context=item.get("news_context", ""),
                    status="waiting",
                    order=order,
                )

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
        setting.use_architecture = bool(request.POST.get("use_architecture"))
        setting.use_realestate = bool(request.POST.get("use_realestate"))
        setting.use_finance = bool(request.POST.get("use_finance"))
        setting.use_tech = bool(request.POST.get("use_tech"))
        setting.use_life = bool(request.POST.get("use_life"))
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

    category = request.POST.get("category", "tech")
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
건축·부동산·건설 관련 주제는 현장 실무자 관점에서 해석을 넣어줘.
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
                    min_length=500,
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

