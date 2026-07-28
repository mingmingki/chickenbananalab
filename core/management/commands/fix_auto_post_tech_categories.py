from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from core.models import Post


TARGET_TITLES = (
    "Ray를 TPU에서 실행하는 방법: 기본 원리부터 핵심까지",
    "Tunix로 에이전트 강화 학습 확장: 고성능 훈련의 효율을 높이는 방법",
)
TARGET_CATEGORY = "tech_ai_development"


def fix_target_auto_post_categories():
    """정확한 두 비공개 초안의 legacy tech 값만 보정한다."""
    with transaction.atomic():
        queryset = Post.objects.filter(
            title__in=TARGET_TITLES,
            is_published=False,
        ).filter(
            Q(category__iexact="tech") | Q(category="테크")
        )
        return queryset.update(category=TARGET_CATEGORY)


class Command(BaseCommand):
    help = "정확히 지정된 두 자동글 초안의 legacy 테크 카테고리를 AI·개발로 보정합니다."

    def handle(self, *args, **options):
        updated = fix_target_auto_post_categories()
        self.stdout.write(
            self.style.SUCCESS(
                f"자동글 카테고리 보정 완료: updated={updated}"
            )
        )
