import traceback
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.ai_writer import (
    generate_ai_post,
    make_generated_image_file,
    save_inline_image,
    replace_image_placeholders,
)
from core.models import (
    Post,
    ExperienceVault,
    AIAutoWriterSetting,
    AIAutoKeywordQueue,
)


def get_post_field_names():
    return [field.name for field in Post._meta.fields]


def get_queue_field_names():
    return [field.name for field in AIAutoKeywordQueue._meta.fields]


def get_setting_field_names():
    return [field.name for field in AIAutoWriterSetting._meta.fields]


def safe_int(value, default=0, min_value=None, max_value=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default

    if min_value is not None:
        number = max(min_value, number)

    if max_value is not None:
        number = min(max_value, number)

    return number


def normalize_html_spaces(value):
    """
    에디터에서 생기는 &nbsp; / 특수 공백을 일반 공백으로 정리합니다.
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


def set_post_optional_seo_fields(post, ai_data):
    """
    Post 모델에 summary, meta_description, thumbnail_prompt 필드가 있을 때만 저장합니다.
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


def get_today_done_count():
    """
    오늘 자동 생성 완료된 키워드 수를 계산합니다.
    """
    queue_field_names = get_queue_field_names()

    if "updated_at" in queue_field_names:
        return AIAutoKeywordQueue.objects.filter(
            status="done",
            updated_at__date=timezone.localdate(),
        ).count()

    return AIAutoKeywordQueue.objects.filter(
        status="done",
        created_at__date=timezone.localdate(),
    ).count()


def get_next_day_time(hour=0, minute=10):
    """
    다음 날 지정 시간으로 next_run_at을 잡습니다.
    """
    current_tz = timezone.get_current_timezone()
    tomorrow = timezone.localdate() + timedelta(days=1)
    naive_datetime = datetime.combine(tomorrow, time(hour=hour, minute=minute))

    return timezone.make_aware(naive_datetime, current_tz)


def build_experience_vault_text():
    """
    경험창고가 활성화되어 있으면 자동글 생성 프롬프트에 반영합니다.
    """
    try:
        vault = ExperienceVault.objects.filter(pk=1, is_active=True).first()

        if vault and vault.content.strip():
            return vault.content.strip()[-12000:]

    except Exception:
        return ""

    return ""


def build_auto_extra_prompt(queue_item):
    """
    대기열 키워드와 관련 뉴스 내용을 자동글 생성 프롬프트에 넣습니다.
    """
    reason = (getattr(queue_item, "reason", "") or "").strip()
    news_context = (getattr(queue_item, "news_context", "") or "").strip()
    experience_vault_text = build_experience_vault_text()

    extra_prompt = f"""
이 글은 ChickenBanana Lab의 시간별 AI 자동글 생성 시스템에서 작성하는 글입니다.

이번 글의 핵심 키워드:
{queue_item.keyword}

관련 뉴스 / 추천 이유:
{reason or news_context or "관련 뉴스 요약 없음"}

작성 방향:
- 위 키워드와 관련 뉴스 흐름을 중심으로 작성
- 단순 일반론이 아니라 오늘 이슈에서 출발한 글처럼 작성
- 기사 제목을 그대로 베끼지 말고, 블로그 운영자가 해석해 정리한 글처럼 작성
- 확인되지 않은 사실은 단정하지 말고 "보도에 따르면", "업계에서는", "~로 보입니다"처럼 조심스럽게 표현
- 첫 문단은 너무 뻔한 "최근 ~가 주목받고 있습니다"로 시작하지 말 것
- 사람이 직접 정리한 개인 블로그 글처럼 자연스럽게 작성
- 검색자가 바로 이해할 수 있도록 핵심 요약, 체크포인트, 주의사항을 자연스럽게 포함
- 같은 표현과 문장 구조를 반복하지 말 것
- 본문에는 h2, h3, p, ul, li 태그를 적절히 사용
- 본문 최상단에 h1 태그는 절대 사용하지 말 것
""".strip()

    if experience_vault_text:
        extra_prompt += f"""

아래는 블로그 운영자가 직접 적어둔 경험창고 내용입니다.
이번 글과 관련 있는 부분만 자연스럽게 참고하세요.
관련 없는 내용은 억지로 넣지 마세요.
내용을 그대로 복사하지 말고, 운영자의 관점이 묻어나게 재해석하세요.

[경험창고]
{experience_vault_text}
"""

    return extra_prompt


def save_ai_data_to_post(ai_data, queue_item, setting):
    """
    generate_ai_post() 결과를 Post 모델로 저장합니다.
    대표 썸네일 / 본문 이미지 / 태그 / 공개 여부 설정을 반영합니다.
    """
    make_thumbnail = bool(getattr(setting, "make_thumbnail", False))
    include_tags = bool(getattr(setting, "include_tags", True))
    publish_immediately = bool(getattr(setting, "publish_immediately", True))

    content = ai_data.get("content", "")
    inline_image_blocks = []

    # 본문 이미지 저장
    for image_index, image_data in enumerate(ai_data.get("content_images", []), start=1):
        image_prompt = (image_data.get("prompt") or "").strip()
        caption = (image_data.get("caption") or "").strip()

        if not image_prompt:
            continue

        try:
            image_url = save_inline_image(
                prompt=image_prompt,
                prefix=f"auto-{queue_item.category}-{queue_item.id}-{image_index}",
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
        category=queue_item.category,
        title=ai_data.get("title", queue_item.keyword),
        thumbnail_text=ai_data.get("thumbnail_text", ""),
        content=content,
        tags=ai_data.get("tags", "") if include_tags else "",
        is_published=publish_immediately,
    )

    set_post_optional_seo_fields(post, ai_data)

    # 대표 썸네일 이미지 자동 생성
    thumbnail_prompt = (ai_data.get("thumbnail_prompt") or "").strip()

    if make_thumbnail and thumbnail_prompt:
        try:
            thumbnail_filename, thumbnail_file = make_generated_image_file(
                prompt=thumbnail_prompt,
                prefix=f"auto-thumbnail-{post.pk}",
            )

            if thumbnail_filename and thumbnail_file:
                post.thumbnail.save(
                    thumbnail_filename,
                    thumbnail_file,
                    save=True,
                )
        except Exception:
            # 썸네일 생성 실패해도 글 생성 자체는 성공 처리
            pass

    return post


def recover_stale_processing_items():
    """
    서버 재시작이나 오류로 processing 상태에 오래 남은 항목을 다시 waiting으로 복구합니다.
    """
    queue_field_names = get_queue_field_names()

    if "updated_at" not in queue_field_names:
        return 0

    stale_time = timezone.now() - timedelta(minutes=60)

    return AIAutoKeywordQueue.objects.filter(
        status="processing",
        updated_at__lt=stale_time,
    ).update(
        status="waiting",
        error_message="이전 실행이 중단되어 대기 상태로 자동 복구되었습니다.",
    )


class Command(BaseCommand):
    help = "AI 자동글 생성 대기열에서 키워드 1개를 꺼내 글을 자동 생성합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="자동 생성 중지 상태나 next_run_at과 관계없이 1회 실행합니다.",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="실제 글을 생성하지 않고 실행 대상만 확인합니다.",
        )

    def handle(self, *args, **options):
        force = options.get("force", False)
        dry_run = options.get("dry_run", False)

        now = timezone.now()

        recovered_count = recover_stale_processing_items()

        if recovered_count:
            self.stdout.write(
                self.style.WARNING(
                    f"processing 상태로 오래 남은 항목 {recovered_count}개를 waiting으로 복구했습니다."
                )
            )

        setting = AIAutoWriterSetting.load()

        setting_field_names = get_setting_field_names()

        make_thumbnail = bool(getattr(setting, "make_thumbnail", False))
        include_tags = bool(getattr(setting, "include_tags", True))
        publish_immediately = bool(getattr(setting, "publish_immediately", True))

        image_count = safe_int(
            getattr(setting, "image_count", 0),
            default=0,
            min_value=0,
            max_value=5,
        )

        if not force and not setting.is_enabled:
            self.stdout.write("AI 자동글 생성이 중지 상태입니다.")
            return

        if not force and setting.next_run_at and setting.next_run_at > now:
            self.stdout.write(
                f"아직 실행 시간이 아닙니다. 다음 실행 예정: {timezone.localtime(setting.next_run_at).strftime('%Y-%m-%d %H:%M')}"
            )
            return

        today_done_count = get_today_done_count()

        if today_done_count >= setting.daily_limit:
            setting.next_run_at = get_next_day_time(hour=0, minute=10)

            update_fields = ["next_run_at"]

            if "updated_at" in setting_field_names:
                update_fields.append("updated_at")

            setting.save(update_fields=update_fields)

            self.stdout.write(
                self.style.WARNING(
                    f"오늘 생성 한도 {setting.daily_limit}개에 도달했습니다. 다음 실행 예정: {timezone.localtime(setting.next_run_at).strftime('%Y-%m-%d %H:%M')}"
                )
            )
            return

        if AIAutoKeywordQueue.objects.filter(status="processing").exists():
            self.stdout.write(
                self.style.WARNING(
                    "이미 생성 중인 키워드가 있습니다. 중복 실행을 방지하기 위해 종료합니다."
                )
            )
            return

        with transaction.atomic():
            queue_item = (
                AIAutoKeywordQueue.objects
                .select_for_update()
                .filter(status="waiting")
                .order_by("order", "created_at")
                .first()
            )

            if not queue_item:
                setting.next_run_at = now + timedelta(minutes=setting.interval_minutes)

                update_fields = ["next_run_at"]

                if "updated_at" in setting_field_names:
                    update_fields.append("updated_at")

                setting.save(update_fields=update_fields)

                self.stdout.write(
                    self.style.WARNING(
                        "대기 중인 키워드가 없습니다. 먼저 '오늘의 추천키워드 가져오기'를 실행해주세요."
                    )
                )
                return

            if dry_run:
                publish_text = "공개" if publish_immediately else "비공개 초안"

                self.stdout.write(
                    self.style.SUCCESS(
                        "[DRY RUN] 생성 대상: "
                        f"{queue_item.get_category_display()} / {queue_item.keyword} / "
                        f"{publish_text} / "
                        f"썸네일 {'ON' if make_thumbnail else 'OFF'} / "
                        f"본문 이미지 {image_count}장 / "
                        f"태그 {'ON' if include_tags else 'OFF'}"
                    )
                )
                return

            queue_item.status = "processing"
            queue_item.error_message = ""

            queue_update_fields = ["status", "error_message"]

            if "updated_at" in get_queue_field_names():
                queue_update_fields.append("updated_at")

            queue_item.save(update_fields=queue_update_fields)

        self.stdout.write(
            self.style.WARNING(
                f"AI 자동글 생성을 시작합니다: {queue_item.get_category_display()} / {queue_item.keyword}"
            )
        )

        try:
            extra_prompt = build_auto_extra_prompt(queue_item)

            ai_data = generate_ai_post(
                category=queue_item.category,
                keywords=queue_item.keyword,
                writing_style="issue",
                extra_prompt=extra_prompt,
                include_tags=include_tags,
                make_thumbnail=make_thumbnail,
                image_count=image_count,
                planned_title=queue_item.keyword,
            )

            post = save_ai_data_to_post(
                ai_data=ai_data,
                queue_item=queue_item,
                setting=setting,
            )

            queue_item.status = "done"
            queue_item.error_message = ""

            queue_field_names = get_queue_field_names()
            queue_update_fields = ["status", "error_message"]

            if "generated_post" in queue_field_names:
                queue_item.generated_post = post
                queue_update_fields.append("generated_post")

            if "updated_at" in queue_field_names:
                queue_update_fields.append("updated_at")

            queue_item.save(update_fields=queue_update_fields)

            setting.last_run_at = timezone.now()
            setting.next_run_at = timezone.now() + timedelta(minutes=setting.interval_minutes)

            setting_update_fields = ["last_run_at", "next_run_at"]

            if "updated_at" in setting_field_names:
                setting_update_fields.append("updated_at")

            setting.save(update_fields=setting_update_fields)

            publish_text = "공개" if publish_immediately else "비공개 초안"

            self.stdout.write(
                self.style.SUCCESS(
                    f"AI 자동글 생성 완료: {post.title} / {publish_text} / "
                    f"본문 이미지 {image_count}장 / "
                    f"태그 {'ON' if include_tags else 'OFF'} / "
                    f"썸네일 {'ON' if make_thumbnail else 'OFF'} / "
                    f"다음 실행: {timezone.localtime(setting.next_run_at).strftime('%Y-%m-%d %H:%M')}"
                )
            )

        except Exception as error:
            traceback.print_exc()

            queue_item.status = "failed"
            queue_item.error_message = str(error)[:1000]

            queue_update_fields = ["status", "error_message"]

            if "updated_at" in get_queue_field_names():
                queue_update_fields.append("updated_at")

            queue_item.save(update_fields=queue_update_fields)

            setting.last_run_at = timezone.now()
            setting.next_run_at = timezone.now() + timedelta(minutes=setting.interval_minutes)

            setting_update_fields = ["last_run_at", "next_run_at"]

            if "updated_at" in setting_field_names:
                setting_update_fields.append("updated_at")

            setting.save(update_fields=setting_update_fields)

            self.stdout.write(
                self.style.ERROR(
                    f"AI 자동글 생성 실패: {error}"
                )
            )