from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, JsonResponse
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import user_passes_test
from django.contrib import messages
from django.db.models import Q, Count, Sum
import json
from datetime import date
from django.conf import settings
from django.http import HttpResponse


from .models import Post
from .forms import PostForm
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


def home(request):
    posts = Post.objects.filter(is_published=True).order_by("-created_at")[:6]

    return render(request, "core/home.html", {
        "posts": posts,
    })


def category_page(request, slug):
    page = CATEGORY_PAGES.get(slug)

    if page is None:
        raise Http404("존재하지 않는 페이지입니다.")

    posts = Post.objects.filter(
    category=slug,
    is_published=True
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
            Q(category__icontains=query)
        )

        if category_slug:
            search_filter = search_filter | Q(category=category_slug)

        results = Post.objects.filter(
    search_filter,
    is_published=True
).order_by("-created_at")

    return render(request, "core/search.html", {
        "query": query,
        "results": results,
    })


def post_detail(request, pk):
    post = get_object_or_404(Post, pk=pk)

    # 비공개 초안은 관리자만 볼 수 있음
    if not post.is_published and not admin_required(request.user):
        raise Http404("존재하지 않는 글입니다.")

    # 공개 글만 조회수 증가
    if post.is_published:
        post.views += 1
        post.save(update_fields=["views"])

    return render(request, "core/post_detail.html", {
        "post": post,
    })


@user_passes_test(admin_required)
def post_create(request):
    initial_category = request.GET.get("category", "")

    if request.method == "POST":
        form = PostForm(request.POST, request.FILES)

        if form.is_valid():
            post = form.save()
            return redirect("post_detail", pk=post.pk)
    else:
        form = PostForm(initial={
            "category": initial_category,
        })

    return render(request, "core/post_form.html", {
        "form": form,
        "mode": "create",
        "post": None,
        "kakao_javascript_key": settings.KAKAO_JAVASCRIPT_KEY,
    })


@user_passes_test(admin_required)
def post_update(request, pk):
    post = get_object_or_404(Post, pk=pk)

    if request.method == "POST":
        form = PostForm(request.POST, request.FILES, instance=post)

        if form.is_valid():
            post = form.save()
            return redirect("post_detail", pk=post.pk)
    else:
        form = PostForm(instance=post)

    return render(request, "core/post_form.html", {
        "form": form,
        "mode": "update",
        "post": post,
        "kakao_javascript_key": settings.KAKAO_JAVASCRIPT_KEY,
    })


@user_passes_test(admin_required)
def post_delete(request, pk):
    post = get_object_or_404(Post, pk=pk)
    category = post.category

    if request.method == "POST":
        post.delete()
        return redirect(category)

    return redirect("post_detail", pk=post.pk)

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
def site_stats(request):
    total_posts = Post.objects.count()
    published_posts = Post.objects.filter(is_published=True).count()
    draft_posts = Post.objects.filter(is_published=False).count()

    total_views = Post.objects.aggregate(
        total=Sum("views")
    )["total"] or 0

    program_file_count = Post.objects.exclude(
        program_file=""
    ).count()

    video_file_count = Post.objects.exclude(
        video_file=""
    ).count()

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
    })

@user_passes_test(admin_required)
def ai_post_generate(request):
    if request.method != "POST":
        return redirect("home")

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
    ]

    selected_keywords = selected_keywords[:10]

    if selected_keywords:
        keywords = ", ".join(selected_keywords)

    writing_style = request.POST.get("writing_style", "practical")
    extra_prompt = request.POST.get("extra_prompt", "").strip()

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
        messages.error(request, "주요 이슈 키워드를 입력해주세요.")
        return redirect("home")

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

중요:
- 이 세부 주제에서 벗어나지 말 것
- 같은 키워드의 다른 글과 내용이 겹치지 않게 작성할 것
- 제목, 도입부, 표, 결론이 다른 글과 비슷하지 않게 작성할 것
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

            post = Post.objects.create(
                category=category,
                title=ai_data.get("title", topic_title),
                thumbnail_text=ai_data.get("thumbnail_text", ""),
                content=content,
                tags=ai_data.get("tags", ""),
                is_published=not save_draft,
            )

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
        messages.error(request, f"AI 글 생성 중 오류가 발생했습니다: {error}")
        return redirect("home")

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

    category = request.POST.get("category", "all")
    today = date.today().strftime("%Y-%m-%d")

    try:
        keywords = recommend_today_keywords(
            category=category,
            today=today,
            count=7,
        )

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
            auth_login(request, user)
            return redirect("home")
    else:
        form = UserCreationForm()

    return render(request, "registration/signup.html", {
        "form": form,
    })

def robots_txt(request):
    content = """User-agent: *
Allow: /

Sitemap: https://www.chickenbananalab.com/sitemap.xml
"""
    return HttpResponse(content, content_type="text/plain")