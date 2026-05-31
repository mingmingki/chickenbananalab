from django.db import models
from django.contrib.auth.models import User


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

    def __str__(self):
        return self.title

    @property
    def tag_list(self):
        if not self.tags:
            return []
        return [tag.strip() for tag in self.tags.split(",") if tag.strip()]


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