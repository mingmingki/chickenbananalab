import io
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image

from .models import Post


def _png(color):
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(stream, format="PNG")
    return stream.getvalue()


@override_settings(MEDIA_ROOT=Path(tempfile.mkdtemp(prefix="cbl-video-edit-thumbnail-")))
class VideoPostEditThumbnailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="video-edit-admin",
            password="test-password",
            is_staff=True,
        )
        self.client = Client(enforce_csrf_checks=True)
        self.post = Post.objects.create(
            post_type="video",
            category="construction_work",
            title="기존 자동글쓰기 게시글",
            content="기존 본문",
            youtube_url="https://www.youtube.com/watch?v=existing",
            thumbnail=SimpleUploadedFile("old.png", _png((220, 20, 20)), content_type="image/png"),
            is_published=True,
        )
        self.edit_data_url = f"/api/videos/{self.post.pk}/edit-data/"
        self.update_url = f"/api/videos/{self.post.pk}/update/"

    def _csrf_token(self):
        self.client.get("/")
        return self.client.cookies["csrftoken"].value

    def _payload(self, token, **extra):
        payload = {
            "post_type": "video",
            "category": "construction_work",
            "title": self.post.title,
            "content": self.post.content,
            "youtube_url": self.post.youtube_url,
            "is_published": "on",
            "csrfmiddlewaretoken": token,
        }
        payload.update(extra)
        return payload

    def test_edit_data_returns_fresh_csrf_token(self):
        self.client.force_login(self.user)
        response = self.client.get(self.edit_data_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("csrf_token"))
        self.assertTrue(response.json().get("thumbnail_url"))

    def test_edit_thumbnail_without_csrf_is_rejected(self):
        self.client.force_login(self.user)
        old_name = self.post.thumbnail.name
        response = self.client.post(
            self.update_url,
            self._payload("", thumbnail=SimpleUploadedFile("new.png", _png((20, 40, 220)), content_type="image/png")),
        )
        self.assertEqual(response.status_code, 403)
        self.post.refresh_from_db()
        self.assertEqual(self.post.thumbnail.name, old_name)

    def test_edit_thumbnail_replacement_succeeds_once_with_csrf(self):
        self.client.force_login(self.user)
        token = self._csrf_token()
        old_name = self.post.thumbnail.name
        response = self.client.post(
            self.update_url,
            self._payload(
                token,
                thumbnail=SimpleUploadedFile("new.png", _png((20, 40, 220)), content_type="image/png"),
            ),
            HTTP_X_CSRFTOKEN=token,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.post.refresh_from_db()
        self.assertNotEqual(self.post.thumbnail.name, old_name)
        self.assertTrue(self.post.thumbnail.storage.exists(self.post.thumbnail.name))
        self.assertFalse(self.post.thumbnail.storage.exists(old_name))

    def test_native_form_edit_redirects_after_thumbnail_replacement(self):
        self.client.force_login(self.user)
        token = self._csrf_token()
        response = self.client.post(
            self.update_url,
            self._payload(
                token,
                thumbnail=SimpleUploadedFile("native.png", _png((30, 180, 60)), content_type="image/png"),
            ),
            HTTP_REFERER="/",
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/post/"))

    def test_edit_without_thumbnail_preserves_existing_file(self):
        self.client.force_login(self.user)
        token = self._csrf_token()
        old_name = self.post.thumbnail.name
        response = self.client.post(
            self.update_url,
            self._payload(token, title="본문만 수정"),
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.title, "본문만 수정")
        self.assertEqual(self.post.thumbnail.name, old_name)
        self.assertTrue(self.post.thumbnail.storage.exists(old_name))

    def test_non_image_thumbnail_is_rejected(self):
        self.client.force_login(self.user)
        token = self._csrf_token()
        old_name = self.post.thumbnail.name
        response = self.client.post(
            self.update_url,
            self._payload(
                token,
                thumbnail=SimpleUploadedFile("new.txt", b"not an image", content_type="text/plain"),
            ),
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 400)
        self.post.refresh_from_db()
        self.assertEqual(self.post.thumbnail.name, old_name)
