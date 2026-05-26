from django.db import models


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

    content = models.TextField(
        verbose_name="내용"
    )

    thumbnail = models.ImageField(
        upload_to="post_thumbnails/",
        blank=True,
        null=True,
        verbose_name="썸네일"
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="등록일"
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="수정일"
    )

    def __str__(self):
        return self.title