from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0029_calendarevent_event_color_calendarevent_is_all_day"),
    ]

    operations = [
        migrations.CreateModel(
            name="GeminiUsageLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="호출 시각")),
                ("feature", models.CharField(choices=[("quantity_structural", "AI 수량산출 · 구조 부재"), ("quantity_architectural", "AI 수량산출 · 건축도면"), ("quantity_elevation", "AI 수량산출 · 입면/단면"), ("ai_post_body", "AI 글 · 본문 생성"), ("ai_recent_issue", "AI 글 · 최근 이슈 검색"), ("ai_headline", "AI 글 · 제목/썸네일 문구"), ("ai_factcheck", "AI 글 · 팩트체크"), ("ai_translation", "AI 글 · 번역/다국어"), ("ai_topic_planning", "AI 글 · 주제 기획"), ("ai_keyword_recommendation", "AI 글 · 키워드 추천"), ("ai_image", "AI 이미지 생성"), ("naver_keyword_search", "오늘의 키워드 · Gemini 검색"), ("other", "기타 Gemini 호출")], db_index=True, default="other", max_length=60, verbose_name="기능")),
                ("model", models.CharField(blank=True, db_index=True, max_length=160, verbose_name="모델")),
                ("prompt_tokens", models.PositiveBigIntegerField(default=0, verbose_name="입력 토큰")),
                ("output_tokens", models.PositiveBigIntegerField(default=0, verbose_name="출력 토큰")),
                ("total_tokens", models.PositiveBigIntegerField(db_index=True, default=0, verbose_name="전체 토큰")),
                ("cached_tokens", models.PositiveBigIntegerField(default=0, verbose_name="캐시 토큰")),
                ("thoughts_tokens", models.PositiveBigIntegerField(default=0, verbose_name="생각 토큰")),
                ("tool_tokens", models.PositiveBigIntegerField(default=0, verbose_name="도구 토큰")),
                ("input_characters", models.PositiveBigIntegerField(default=0, verbose_name="입력 문자 수")),
                ("image_inputs", models.PositiveIntegerField(default=0, verbose_name="입력 이미지 수")),
                ("duration_ms", models.PositiveIntegerField(default=0, verbose_name="소요시간(ms)")),
                ("is_success", models.BooleanField(db_index=True, default=True, verbose_name="성공")),
                ("error_type", models.CharField(blank=True, max_length=120, verbose_name="오류 종류")),
                ("error_message", models.TextField(blank=True, verbose_name="오류 내용")),
                ("callsite", models.CharField(blank=True, max_length=240, verbose_name="호출 위치")),
                ("batch_index", models.PositiveIntegerField(blank=True, null=True, verbose_name="현재 배치")),
                ("batch_total", models.PositiveIntegerField(blank=True, null=True, verbose_name="전체 배치")),
                ("metadata", models.JSONField(blank=True, default=dict, verbose_name="추가 정보")),
            ],
            options={
                "verbose_name": "Gemini 사용량",
                "verbose_name_plural": "Gemini 사용량",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="geminiusagelog",
            index=models.Index(fields=["feature", "created_at"], name="gem_usage_feat_time"),
        ),
        migrations.AddIndex(
            model_name="geminiusagelog",
            index=models.Index(fields=["model", "created_at"], name="gem_usage_model_time"),
        ),
    ]
