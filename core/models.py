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