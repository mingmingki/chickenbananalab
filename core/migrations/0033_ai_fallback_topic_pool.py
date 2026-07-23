import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0032_tech_content_categories"),
    ]

    operations = [
        migrations.CreateModel(
            name="AIFallbackTopic",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("construction_work", "건설실무"),
                            ("construction_tech", "건설기술"),
                            ("construction_real", "건설부동산"),
                            ("bim", "REVIT/BIM"),
                            ("dynamo_automation", "Dynamo/자동화"),
                            ("four_d_five_d", "4D/5D"),
                            ("tech_ai_development", "AI·개발"),
                            ("tech_data_security", "데이터·보안"),
                            (
                                "tech_server_software",
                                "인터넷·서버·소프트",
                            ),
                            ("program", "업무용 프로그램"),
                            ("tool_recommend", "툴소개/툴추천"),
                        ],
                        db_index=True,
                        max_length=30,
                        verbose_name="카테고리",
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        max_length=220,
                        verbose_name="글감 제목",
                    ),
                ),
                (
                    "normalized_title",
                    models.CharField(
                        db_index=True,
                        editable=False,
                        max_length=220,
                        verbose_name="중복 확인용 제목",
                    ),
                ),
                (
                    "content_format",
                    models.CharField(
                        choices=[
                            ("workflow", "실무 절차"),
                            ("checklist", "체크리스트"),
                            ("troubleshooting", "문제 해결"),
                            ("comparison", "비교·선택"),
                            ("automation", "자동화·생산성"),
                            ("case", "사례·트렌드"),
                        ],
                        default="workflow",
                        max_length=30,
                        verbose_name="글 형식",
                    ),
                ),
                (
                    "difficulty",
                    models.CharField(
                        choices=[
                            ("beginner", "입문"),
                            ("practical", "실무"),
                            ("advanced", "심화"),
                        ],
                        default="practical",
                        max_length=20,
                        verbose_name="난이도",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "검토대기"),
                            ("approved", "승인"),
                            ("rejected", "제외"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                        verbose_name="상태",
                    ),
                ),
                (
                    "note",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=500,
                        verbose_name="AI 설명",
                    ),
                ),
                (
                    "source_model",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=160,
                        verbose_name="생성 모델",
                    ),
                ),
                (
                    "recommendation_count",
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name="추천 노출 수",
                    ),
                ),
                (
                    "last_recommended_at",
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        null=True,
                        verbose_name="최근 추천 시각",
                    ),
                ),
                (
                    "approved_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="승인 시각",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="생성일",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name="수정일",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_ai_fallback_topics",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="생성 요청자",
                    ),
                ),
            ],
            options={
                "verbose_name": "AI 기본글감",
                "verbose_name_plural": "AI 기본글감",
                "ordering": [
                    "last_recommended_at",
                    "recommendation_count",
                    "created_at",
                    "id",
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="aifallbacktopic",
            constraint=models.UniqueConstraint(
                fields=("category", "normalized_title"),
                name="uniq_ai_fallback_topic_category_title",
            ),
        ),
        migrations.AddIndex(
            model_name="aifallbacktopic",
            index=models.Index(
                fields=["category", "status"],
                name="ai_topic_cat_status",
            ),
        ),
        migrations.AddIndex(
            model_name="aifallbacktopic",
            index=models.Index(
                fields=["status", "last_recommended_at"],
                name="ai_topic_status_last",
            ),
        ),
        migrations.AlterField(
            model_name="geminiusagelog",
            name="feature",
            field=models.CharField(
                choices=[
                    ("quantity_structural", "AI 수량산출 · 구조 부재"),
                    (
                        "quantity_architectural",
                        "AI 수량산출 · 건축도면",
                    ),
                    ("quantity_elevation", "AI 수량산출 · 입면/단면"),
                    ("ai_post_body", "AI 글 · 본문 생성"),
                    ("ai_recent_issue", "AI 글 · 최근 이슈 검색"),
                    ("ai_headline", "AI 글 · 제목/썸네일 문구"),
                    ("ai_factcheck", "AI 글 · 팩트체크"),
                    ("ai_translation", "AI 글 · 번역/다국어"),
                    ("ai_topic_planning", "AI 글 · 주제 기획"),
                    (
                        "ai_keyword_recommendation",
                        "AI 글 · 키워드 추천",
                    ),
                    ("ai_fallback_topics", "AI 기본글감 생성"),
                    ("ai_image", "AI 이미지 생성"),
                    (
                        "naver_keyword_search",
                        "오늘의 키워드 · Gemini 검색",
                    ),
                    ("other", "기타 Gemini 호출"),
                ],
                db_index=True,
                default="other",
                max_length=60,
                verbose_name="기능",
            ),
        ),
    ]
