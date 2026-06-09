from django.db import models
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.text import slugify
from django.utils.html import strip_tags
import re


class Post(models.Model):
    CATEGORY_CHOICES = [
        ("architecture", "건축"),
        ("realestate", "부동산"),
        ("finance", "금융"),
        ("tech", "테크"),
        ("life", "일상"),
    ]

    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        verbose_name="카테고리"
    )

    title = models.CharField(
        max_length=200,
        verbose_name="제목"
    )

    slug = models.SlugField(
        max_length=220,
        unique=False,
        blank=True,
        allow_unicode=True,
        verbose_name="주소 슬러그"
    )

    summary = models.TextField(
        blank=True,
        verbose_name="요약"
    )

    meta_description = models.CharField(
        max_length=160,
        blank=True,
        verbose_name="SEO 설명"
    )

    thumbnail = models.ImageField(
        upload_to="post_thumbnails/",
        blank=True,
        null=True,
        verbose_name="썸네일"
    )

    thumbnail_text = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="썸네일 문구"
    )

    content_image = models.ImageField(
        upload_to="post_content_images/",
        blank=True,
        null=True,
        verbose_name="본문 사진"
    )

    video_file = models.FileField(

        upload_to="post_videos/",
        blank=True,
        null=True,
        verbose_name="본문 동영상"
    )

    # 쇼츠 자동 생성 결과
    shorts_video = models.FileField(upload_to="shorts/", blank=True, null=True)
    shorts_cover = models.ImageField(upload_to="shorts_covers/", blank=True, null=True)
    shorts_status = models.CharField(max_length=20, default="none", blank=True)
    shorts_error = models.TextField(blank=True, default="")
    shorts_created_at = models.DateTimeField(blank=True, null=True)

    program_file = models.FileField(
        upload_to="post_program_files/",
        blank=True,
        null=True,
        verbose_name="첨부 프로그램"
    )

    location = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="위치정보"
    )

    content = models.TextField(
        verbose_name="내용"
    )

    tags = models.CharField(
        max_length=300,
        blank=True,
        verbose_name="태그"
    )

    is_published = models.BooleanField(
        default=True,
        verbose_name="공개 여부"
    )

    views = models.PositiveIntegerField(
        default=0,
        verbose_name="조회수"
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="등록일"
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="수정일"
    )

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title, allow_unicode=True)[:180]

            if not base_slug:
                base_slug = f"post-{self.pk or 'new'}"

            slug = base_slug
            counter = 1

            while Post.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1

            self.slug = slug

        if not self.summary:
            plain_content = strip_tags(self.content or "")
            plain_content = re.sub(r"\s+", " ", plain_content).strip()
            self.summary = plain_content[:300]

        if not self.meta_description:
            source_text = self.summary or self.content or ""
            plain_text = strip_tags(source_text)
            plain_text = re.sub(r"\s+", " ", plain_text).strip()
            self.meta_description = plain_text[:150]

        super().save(*args, **kwargs)

    def get_absolute_url(self):
        if self.slug:
            return reverse("post_detail_slug", kwargs={"slug": self.slug})

        return reverse("post_detail", kwargs={"pk": self.pk})

    def __str__(self):
        return self.title

    @property
    def tag_list(self):
        if not self.tags:
            return []
        return [tag.strip() for tag in self.tags.split(",") if tag.strip()]
    
class ExperienceVault(models.Model):
    content = models.TextField("경험창고 내용", blank=True)
    is_active = models.BooleanField("AI 글 생성에 사용", default=True)
    updated_at = models.DateTimeField("수정일", auto_now=True)

    class Meta:
        verbose_name = "경험창고"
        verbose_name_plural = "경험창고"

    def __str__(self):
        return "경험창고"


class UserProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="사용자"
    )

    nickname = models.CharField(
        max_length=30,
        blank=True,
        verbose_name="닉네임"
    )

    is_sub_admin = models.BooleanField(
        default=False,
        verbose_name="부관리자 여부"
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="가입일"
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="수정일"
    )

    def __str__(self):
        if self.nickname:
            return self.nickname
        return self.user.email or self.user.username

    @property
    def display_name(self):
        if self.nickname:
            return self.nickname
        return self.user.email or self.user.username
    
class VisitLog(models.Model):
    path = models.CharField(max_length=500)
    method = models.CharField(max_length=10, default="GET")

    visitor_key = models.CharField(max_length=64, db_index=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    user_agent = models.TextField(blank=True)

    referer = models.TextField(blank=True)
    is_bot = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["visitor_key"]),
            models.Index(fields=["path"]),
        ]

    def __str__(self):
        return f"{self.created_at} / {self.path}"
    
# ================================
# AI Auto Writer Models
# ================================

class AIAutoWriterSetting(models.Model):
    INTERVAL_CHOICES = [
        (10, "10분마다 1개"),
        (30, "30분마다 1개"),
        (60, "1시간마다 1개"),
        (120, "2시간마다 1개"),
    ]

    is_enabled = models.BooleanField(
        default=False,
        verbose_name="자동 생성 실행 여부"
    )

    interval_minutes = models.PositiveIntegerField(
        choices=INTERVAL_CHOICES,
        default=30,
        verbose_name="자동 생성 간격"
    )

    keyword_count_per_category = models.PositiveIntegerField(
        default=7,
        verbose_name="카테고리별 추천키워드 수"
    )

    publish_immediately = models.BooleanField(
        default=True,
        verbose_name="생성 후 바로 공개"
    )

    make_thumbnail = models.BooleanField(
        default=False,
        verbose_name="대표 썸네일 이미지 자동 생성"
    )

    image_count = models.PositiveIntegerField(
    default=0,
    verbose_name="본문 이미지 개수"
    )
    
    include_tags = models.BooleanField(
        default=True,
        verbose_name="태그 자동 생성"
    )

    daily_limit = models.PositiveIntegerField(
        default=30,
        verbose_name="하루 최대 생성 글 수"
    )

    use_architecture = models.BooleanField(default=True, verbose_name="건축 사용")
    use_realestate = models.BooleanField(default=True, verbose_name="부동산 사용")
    use_finance = models.BooleanField(default=True, verbose_name="금융 사용")
    use_tech = models.BooleanField(default=True, verbose_name="테크 사용")
    use_life = models.BooleanField(default=True, verbose_name="일상 사용")

    last_run_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="마지막 실행 시간"
    )

    next_run_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="다음 실행 예정 시간"
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="생성일"
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="수정일"
    )

    class Meta:
        verbose_name = "AI 자동글 생성 설정"
        verbose_name_plural = "AI 자동글 생성 설정"

    def __str__(self):
        status = "실행 중" if self.is_enabled else "중지"
        return f"AI 자동글 생성 설정 - {status}"

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


class AIAutoKeywordQueue(models.Model):
    CATEGORY_CHOICES = [
        ("architecture", "건축"),
        ("realestate", "부동산"),
        ("finance", "금융"),
        ("tech", "테크"),
        ("life", "일상"),
    ]

    STATUS_CHOICES = [
        ("waiting", "대기"),
        ("processing", "생성 중"),
        ("done", "완료"),
        ("failed", "실패"),
    ]

    category = models.CharField(
        max_length=30,
        choices=CATEGORY_CHOICES,
        verbose_name="카테고리"
    )

    keyword = models.CharField(
        max_length=200,
        verbose_name="추천키워드"
    )

    reason = models.TextField(
        blank=True,
        default="",
        verbose_name="추천 이유 / 관련 뉴스 요약"
    )

    news_context = models.TextField(
        blank=True,
        default="",
        verbose_name="뉴스 기반 참고 내용"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="waiting",
        verbose_name="상태"
    )

    order = models.PositiveIntegerField(
        default=0,
        verbose_name="생성 순서"
    )

    generated_post = models.ForeignKey(
        "Post",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_auto_keyword_items",
        verbose_name="생성된 글"
    )

    error_message = models.TextField(
        blank=True,
        default="",
        verbose_name="오류 메시지"
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="대기열 등록일"
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="생성 시작 시간"
    )

    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="생성 완료 시간"
    )

    class Meta:
        verbose_name = "AI 자동글 키워드 대기열"
        verbose_name_plural = "AI 자동글 키워드 대기열"
        ordering = ["order", "created_at"]

    def __str__(self):
        return f"[{self.get_category_display()}] {self.keyword} - {self.get_status_display()}"


# ==============================
# Mini CapCut editor project
# ==============================
class MiniCapcutProject(models.Model):
    post = models.ForeignKey(
        "Post",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mini_capcut_projects",
        verbose_name="연결 글",
    )
    title = models.CharField(max_length=200, blank=True, default="", verbose_name="프로젝트명")
    data = models.JSONField(default=dict, blank=True, verbose_name="편집 데이터")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="생성일")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="수정일")

    class Meta:
        verbose_name = "미니 CapCut 프로젝트"
        verbose_name_plural = "미니 CapCut 프로젝트"

    def __str__(self):
        return self.title or f"MiniCapcutProject #{self.pk}"
