from django.db import models
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.text import slugify
from django.utils.html import strip_tags
import re
from urllib.parse import parse_qs, urlparse
from .cbl_category_policy import CBL_MODEL_CATEGORY_CHOICES


class Post(models.Model):
    CATEGORY_CHOICES = CBL_MODEL_CATEGORY_CHOICES

    POST_TYPE_CHOICES = [
        ("article", "일반 글"),
        ("video", "영상 글"),
    ]

    post_type = models.CharField(
        max_length=20,
        choices=POST_TYPE_CHOICES,
        default="article",
        db_index=True,
        verbose_name="게시글 유형",
    )

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

    youtube_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="유튜브 주소",
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

    @property
    def youtube_embed_url(self):
        """일반 유튜브·공유·쇼츠 주소를 임베드 주소로 변환합니다."""
        raw_url = (self.youtube_url or "").strip()
        if not raw_url:
            return ""

        try:
            parsed = urlparse(raw_url)
            host = (parsed.netloc or "").lower().split(":", 1)[0]
            video_id = ""

            if host in {"youtu.be", "www.youtu.be"}:
                video_id = parsed.path.strip("/").split("/")[0]
            elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
                path_parts = [part for part in parsed.path.split("/") if part]
                if parsed.path == "/watch":
                    video_id = (parse_qs(parsed.query).get("v") or [""])[0]
                elif len(path_parts) >= 2 and path_parts[0] in {"embed", "shorts", "live"}:
                    video_id = path_parts[1]

            video_id = re.sub(r"[^A-Za-z0-9_-]", "", video_id)
            if video_id:
                return f"https://www.youtube-nocookie.com/embed/{video_id}"
        except (TypeError, ValueError):
            pass

        return ""

    def __str__(self):
        return self.title

    @property
    def tag_list(self):
        if not self.tags:
            return []
        return [tag.strip() for tag in self.tags.split(",") if tag.strip()]
    

# =========================================================
# Post comments
# =========================================================
class Comment(models.Model):
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="글",
    )

    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="post_comments",
        verbose_name="작성자",
    )

    content = models.TextField(
        max_length=1000,
        verbose_name="댓글 내용",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="작성일",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="수정일",
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name = "댓글"
        verbose_name_plural = "댓글"

    @property
    def author_name(self):
        """
        회원 프로필 닉네임을 우선 표시합니다.
        프로필이나 닉네임이 없을 때만 아이디를 표시합니다.
        """
        try:
            nickname = (self.author.profile.nickname or "").strip()
        except Exception:
            nickname = ""

        if nickname:
            return nickname

        return self.author.username or "회원"

    def __str__(self):
        return f"{self.author_name}: {self.content[:30]}"


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

    use_architecture = models.BooleanField(default=True, verbose_name="건설실무 사용")
    use_construction_tech = models.BooleanField(default=True, verbose_name="건설기술 사용")
    use_realestate = models.BooleanField(default=True, verbose_name="건설부동산 사용")
    use_finance = models.BooleanField(default=True, verbose_name="금융 사용")
    use_tech = models.BooleanField(default=True, verbose_name="테크 사용")
    use_life = models.BooleanField(default=True, verbose_name="일상 사용")

    # CBL_AI_AUTO_NEW_CATEGORY_FIELDS_START
    use_bim = models.BooleanField(default=True, verbose_name="REVIT/BIM 사용")
    use_dynamo_automation = models.BooleanField(default=True, verbose_name="Dynamo/자동화 사용")
    use_four_d_five_d = models.BooleanField(default=True, verbose_name="4D/5D 사용")
    use_tech_ai_development = models.BooleanField(default=True, verbose_name="AI·개발 사용")
    use_tech_data_security = models.BooleanField(default=True, verbose_name="데이터·보안 사용")
    use_tech_server_software = models.BooleanField(default=True, verbose_name="인터넷·서버·소프트 사용")
    use_program = models.BooleanField(default=True, verbose_name="업무용 프로그램 사용")
    use_tool_recommend = models.BooleanField(default=True, verbose_name="툴소개/툴추천 사용")
    # CBL_AI_AUTO_NEW_CATEGORY_FIELDS_END

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
    # Post와 동일한 단일 원본을 사용한다. legacy choices는 기존 DB 행 표시 호환용이며
    # 신규 자동글 저장은 cbl_resolve_auto_post_category()에서 public 목록만 허용한다.
    CATEGORY_CHOICES = CBL_MODEL_CATEGORY_CHOICES

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



# CBL_PROGRAM_DOWNLOAD_MODEL_START
class ProgramDownload(models.Model):
    PLATFORM_MAC = "mac"
    PLATFORM_WINDOWS = "windows"

    slug = models.SlugField(
        max_length=80,
        unique=True,
        verbose_name="프로그램 식별자",
    )

    name = models.CharField(
        max_length=120,
        verbose_name="프로그램명",
    )

    description = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name="설명",
    )

    mac_file = models.FileField(
        upload_to="program_downloads/mac/",
        blank=True,
        null=True,
        verbose_name="Mac용 파일",
    )

    windows_file = models.FileField(
        upload_to="program_downloads/windows/",
        blank=True,
        null=True,
        verbose_name="Windows용 파일",
    )

    mac_is_public = models.BooleanField(
        default=False,
        verbose_name="Mac 공개",
    )

    windows_is_public = models.BooleanField(
        default=False,
        verbose_name="Windows 공개",
    )

    order = models.PositiveIntegerField(
        default=0,
        verbose_name="표시 순서",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="수정일",
    )

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "프로그램 다운로드"
        verbose_name_plural = "프로그램 다운로드"

    def __str__(self):
        return self.name
# CBL_PROGRAM_DOWNLOAD_MODEL_END



# CBL_HOME_PROGRAM_DOWNLOAD_MODEL_START
class HomeProgramDownload(models.Model):
    title = models.CharField(max_length=120, verbose_name="표시명")
    badge = models.CharField(max_length=20, blank=True, default="", verbose_name="짧은 라벨")
    description = models.CharField(max_length=200, blank=True, default="", verbose_name="설명")
    file = models.FileField(upload_to="home_programs/", blank=True, null=True, verbose_name="다운로드 파일")
    is_public = models.BooleanField(default=False, verbose_name="공개 여부")
    order = models.PositiveIntegerField(default=0, verbose_name="표시 순서")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="등록일")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="수정일")

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "홈 인기 프로그램"
        verbose_name_plural = "홈 인기 프로그램"

    def __str__(self):
        return self.title
# CBL_HOME_PROGRAM_DOWNLOAD_MODEL_END

class CalendarEvent(models.Model):
    title = models.CharField("일정명", max_length=120)
    event_date = models.DateField("시작 날짜")
    end_date = models.DateField("종료 날짜", blank=True, null=True)
    start_time = models.TimeField("시작 시간", blank=True, null=True)
    end_time = models.TimeField("종료 시간", blank=True, null=True)
    category = models.CharField(
        "분류",
        max_length=30,
        blank=True,
        default="일정",
        help_text="예: 업데이트, 강의, 배포, 공지, 개인일정"
    )
    description = models.TextField("설명", blank=True)
    link_url = models.URLField("연결 링크", blank=True)
    is_public = models.BooleanField("공개", default=True)
    is_important = models.BooleanField("중요 일정", default=False)
    is_all_day = models.BooleanField("온종일", default=False)
    event_color = models.CharField("이벤트 바 색상", max_length=7, default="#2f9e97")
    created_at = models.DateTimeField("등록일", auto_now_add=True)
    updated_at = models.DateTimeField("수정일", auto_now=True)

    class Meta:
        verbose_name = "주요 일정"
        verbose_name_plural = "주요 일정"
        ordering = ["event_date", "start_time", "id"]

    def __str__(self):
        return f"{self.event_date} - {self.title}"


class CommunityQuestion(models.Model):
    CATEGORY_CHOICES = [
        ("question", "질문/답변"),
        ("error", "오류 제보"),
        ("request", "프로그램 요청"),
        ("faq", "자주하는 질문"),
    ]

    category = models.CharField("분류", max_length=30, choices=CATEGORY_CHOICES, default="question")
    title = models.CharField("제목", max_length=200)
    body = models.TextField("문의 내용")
    author_name = models.CharField("작성자", max_length=40, blank=True, default="익명")
    contact = models.CharField("연락처/이메일", max_length=120, blank=True)
    answer = models.TextField("답변", blank=True)
    is_public = models.BooleanField("공개 여부", default=True)
    created_at = models.DateTimeField("등록일", auto_now_add=True)
    answered_at = models.DateTimeField("답변일", blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "커뮤니티 문의"
        verbose_name_plural = "커뮤니티 문의"

    def save(self, *args, **kwargs):
        if self.answer and not self.answered_at:
            from django.utils import timezone
            self.answered_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.get_category_display()}] {self.title}"

class GeminiUsageLog(models.Model):
    """One row per Gemini API attempt. Prompts and responses are not stored."""

    FEATURE_CHOICES = [
        ("quantity_structural", "AI 수량산출 · 구조 부재"),
        ("quantity_architectural", "AI 수량산출 · 건축도면"),
        ("quantity_elevation", "AI 수량산출 · 입면/단면"),
        ("ai_post_body", "AI 글 · 본문 생성"),
        ("ai_recent_issue", "AI 글 · 최근 이슈 검색"),
        ("ai_headline", "AI 글 · 제목/썸네일 문구"),
        ("ai_factcheck", "AI 글 · 팩트체크"),
        ("ai_translation", "AI 글 · 번역/다국어"),
        ("ai_topic_planning", "AI 글 · 주제 기획"),
        ("ai_keyword_recommendation", "AI 글 · 키워드 추천"),
        ("ai_fallback_topics", "AI 기본글감 생성"),
        ("ai_image", "AI 이미지 생성"),
        ("naver_keyword_search", "오늘의 키워드 · Gemini 검색"),
        ("other", "기타 Gemini 호출"),
    ]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="호출 시각")
    feature = models.CharField(max_length=60, choices=FEATURE_CHOICES, default="other", db_index=True, verbose_name="기능")
    model = models.CharField(max_length=160, blank=True, db_index=True, verbose_name="모델")
    prompt_tokens = models.PositiveBigIntegerField(default=0, verbose_name="입력 토큰")
    output_tokens = models.PositiveBigIntegerField(default=0, verbose_name="출력 토큰")
    total_tokens = models.PositiveBigIntegerField(default=0, db_index=True, verbose_name="전체 토큰")
    cached_tokens = models.PositiveBigIntegerField(default=0, verbose_name="캐시 토큰")
    thoughts_tokens = models.PositiveBigIntegerField(default=0, verbose_name="생각 토큰")
    tool_tokens = models.PositiveBigIntegerField(default=0, verbose_name="도구 토큰")
    input_characters = models.PositiveBigIntegerField(default=0, verbose_name="입력 문자 수")
    image_inputs = models.PositiveIntegerField(default=0, verbose_name="입력 이미지 수")
    duration_ms = models.PositiveIntegerField(default=0, verbose_name="소요시간(ms)")
    is_success = models.BooleanField(default=True, db_index=True, verbose_name="성공")
    error_type = models.CharField(max_length=120, blank=True, verbose_name="오류 종류")
    error_message = models.TextField(blank=True, verbose_name="오류 내용")
    callsite = models.CharField(max_length=240, blank=True, verbose_name="호출 위치")
    batch_index = models.PositiveIntegerField(blank=True, null=True, verbose_name="현재 배치")
    batch_total = models.PositiveIntegerField(blank=True, null=True, verbose_name="전체 배치")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="추가 정보")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["feature", "created_at"], name="gem_usage_feat_time"),
            models.Index(fields=["model", "created_at"], name="gem_usage_model_time"),
        ]
        verbose_name = "Gemini 사용량"
        verbose_name_plural = "Gemini 사용량"

    def __str__(self):
        return f"{self.get_feature_display()} · {self.model} · {self.total_tokens:,} tokens"


# CBL_AI_FALLBACK_TOPIC_POOL_V1_MODEL_START
class AIFallbackTopic(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    STATUS_CHOICES = [
        (STATUS_PENDING, "검토대기"),
        (STATUS_APPROVED, "승인"),
        (STATUS_REJECTED, "제외"),
    ]

    CATEGORY_CHOICES = [
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

    FORMAT_CHOICES = [
        ("workflow", "실무 절차"),
        ("checklist", "체크리스트"),
        ("troubleshooting", "문제 해결"),
        ("comparison", "비교·선택"),
        ("automation", "자동화·생산성"),
        ("case", "사례·트렌드"),
    ]

    DIFFICULTY_CHOICES = [
        ("beginner", "입문"),
        ("practical", "실무"),
        ("advanced", "심화"),
    ]

    category = models.CharField(
        max_length=30,
        choices=CATEGORY_CHOICES,
        db_index=True,
        verbose_name="카테고리",
    )
    title = models.CharField(
        max_length=220,
        verbose_name="글감 제목",
    )
    normalized_title = models.CharField(
        max_length=220,
        db_index=True,
        editable=False,
        verbose_name="중복 확인용 제목",
    )
    content_format = models.CharField(
        max_length=30,
        choices=FORMAT_CHOICES,
        default="workflow",
        verbose_name="글 형식",
    )
    difficulty = models.CharField(
        max_length=20,
        choices=DIFFICULTY_CHOICES,
        default="practical",
        verbose_name="난이도",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name="상태",
    )
    note = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="AI 설명",
    )
    source_model = models.CharField(
        max_length=160,
        blank=True,
        default="",
        verbose_name="생성 모델",
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_ai_fallback_topics",
        verbose_name="생성 요청자",
    )
    recommendation_count = models.PositiveIntegerField(
        default=0,
        verbose_name="추천 노출 수",
    )
    last_recommended_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="최근 추천 시각",
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="승인 시각",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="생성일",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="수정일",
    )

    class Meta:
        ordering = [
            "last_recommended_at",
            "recommendation_count",
            "created_at",
            "id",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["category", "normalized_title"],
                name="uniq_ai_fallback_topic_category_title",
            ),
        ]
        indexes = [
            models.Index(
                fields=["category", "status"],
                name="ai_topic_cat_status",
            ),
            models.Index(
                fields=["status", "last_recommended_at"],
                name="ai_topic_status_last",
            ),
        ]
        verbose_name = "AI 기본글감"
        verbose_name_plural = "AI 기본글감"

    def save(self, *args, **kwargs):
        value = re.sub(r"\s+", " ", str(self.title or "")).strip()
        self.title = value
        self.normalized_title = re.sub(
            r"[^0-9a-z가-힣]+", "", value.lower()
        )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.get_category_display()}] {self.title}"
# CBL_AI_FALLBACK_TOPIC_POOL_V1_MODEL_END
