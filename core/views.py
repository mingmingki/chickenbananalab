from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Q
from django.contrib import messages

from .models import Post
from .forms import PostForm
from .ai_writer import generate_ai_post

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
    posts = Post.objects.all().order_by("-created_at")[:6]

    return render(request, "core/home.html", {
        "posts": posts,
    })


def category_page(request, slug):
    page = CATEGORY_PAGES.get(slug)

    if page is None:
        raise Http404("존재하지 않는 페이지입니다.")

    posts = Post.objects.filter(category=slug).order_by("-created_at")[:15]

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

        results = Post.objects.filter(search_filter).order_by("-created_at")

    return render(request, "core/search.html", {
        "query": query,
        "results": results,
    })


def post_detail(request, pk):
    post = get_object_or_404(Post, pk=pk)

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
    })


@user_passes_test(admin_required)
def post_delete(request, pk):
    post = get_object_or_404(Post, pk=pk)
    category = post.category

    if request.method == "POST":
        post.delete()
        return redirect(category)

    return redirect("post_detail", pk=post.pk)


def about(request):
    return render(request, "core/about.html")


def contact(request):
    return render(request, "core/contact.html")


@user_passes_test(admin_required)
def admin_dashboard(request):
    posts = Post.objects.all().order_by("-created_at")

    return render(request, "core/admin_dashboard.html", {
        "posts": posts,
    })

@user_passes_test(admin_required)
def ai_post_generate(request):
    if request.method != "POST":
        return redirect("home")

    category = request.POST.get("category", "tech")
    keywords = request.POST.get("keywords", "").strip()
    writing_style = request.POST.get("writing_style", "practical")
    extra_prompt = request.POST.get("extra_prompt", "").strip()

    try:
        count = int(request.POST.get("count", 1))
    except ValueError:
        count = 1

    count = max(1, min(count, 10))

    make_thumbnail = request.POST.get("make_thumbnail") == "on"
    include_tags = request.POST.get("include_tags") == "on"

    if not keywords:
        messages.error(request, "주요 이슈 키워드를 입력해주세요.")
        return redirect("home")

    created_posts = []

    try:
        for index in range(count):
            ai_data = generate_ai_post(
                category=category,
                keywords=keywords,
                writing_style=writing_style,
                extra_prompt=extra_prompt,
                include_tags=include_tags,
                make_thumbnail=make_thumbnail,
            )

            thumbnail_text = ai_data.get("thumbnail_text", "")

            thumbnail_prompt = ai_data.get("thumbnail_prompt", "")
            content = ai_data.get("content", "")

            if thumbnail_prompt:
                content += f"""
<hr>
<h3>썸네일 이미지 프롬프트</h3>
<p>{thumbnail_prompt}</p>
"""

            post = Post.objects.create(
                category=category,
                title=ai_data.get("title", f"{keywords} 정리"),
                thumbnail_text=thumbnail_text,
                content=content,
                tags=ai_data.get("tags", ""),
            )

            created_posts.append(post)

    except Exception as error:
        messages.error(request, f"AI 글 생성 중 오류가 발생했습니다: {error}")
        return redirect("home")

    if len(created_posts) == 1:
        return redirect("post_detail", pk=created_posts[0].pk)

    messages.success(request, f"AI 글 {len(created_posts)}개를 생성했습니다.")
    return redirect("admin_dashboard")

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