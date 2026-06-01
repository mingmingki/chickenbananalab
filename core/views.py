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
from django.views.decorators.http import require_POST

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
    generate_post_topics,
    recommend_today_keywords,
    make_generated_image_file,
    save_inline_image,
    replace_image_placeholders,
)

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


def admin_required(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


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

    if post.is_published:
        post.views += 1
        post.save(update_fields=["views"])

    return render(request, "core/post_detail.html", {
        "post": post,
    })


def post_detail_by_slug(request, slug):
    post = get_object_or_404(Post, slug=slug)

    if not post.is_published and not admin_required(request.user):
        raise Http404("존재하지 않는 글입니다.")

    if post.is_published:
        post.views += 1
        post.save(update_fields=["views"])

    return render(request, "core/post_detail.html", {
        "post": post,
    })


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
def ai_post_generate(request):
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

    if not keywords:
        messages.error(request, "주요 이슈 키워드를 입력해주세요. 직접 입력하거나 추천 키워드를 선택해주세요.")
        return redirect("admin_dashboard")

    created_posts = []

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
                except Exception:
                    image_url = ""

                if image_url:
                    inline_image_blocks.append({
                        "url": image_url,
                        "caption": caption,
                    })

            content = replace_image_placeholders(content, inline_image_blocks)
            content = normalize_html_spaces(content)

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
                except Exception:
                    pass

            created_posts.append(post)

    except Exception as error:
        print("========== AI 글 생성 오류 ==========")
        print(error)
        traceback.print_exc()
        print("===================================")

        messages.error(request, f"AI 글 생성 중 오류가 발생했습니다: {error}")
        return redirect("admin_dashboard")

    if len(created_posts) == 1:
        return redirect("post_detail", pk=created_posts[0].pk)

    messages.success(request, f"AI 글 {len(created_posts)}개를 생성했습니다.")
    return redirect("admin_dashboard")


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