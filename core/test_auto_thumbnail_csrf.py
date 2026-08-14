import io
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image

from .models import Post


def _png_1x1():
    stream = io.BytesIO()
    Image.new("RGB", (1, 1), (20, 40, 60)).save(stream, format="PNG")
    return stream.getvalue()


@override_settings(MEDIA_ROOT=Path(tempfile.mkdtemp(prefix="cbl-thumbnail-csrf-test-")))
class VideoThumbnailCsrfTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="thumbnail-admin",
            password="test-password",
            is_staff=True,
        )
        self.client = Client(enforce_csrf_checks=True)
        self.url = "/video/upload/"
        self.payload = {
            "post_type": "video",
            "category": "construction_work",
            "title": "썸네일 CSRF 테스트",
            "youtube_url": "https://www.youtube.com/watch?v=test-thumbnail",
            "content": "테스트 설명",
            "is_published": "on",
        }

    def _image(self):
        return SimpleUploadedFile(
            "thumbnail.png",
            _png_1x1(),
            content_type="image/png",
        )

    def _csrf_token(self):
        self.client.get("/")
        return self.client.cookies["csrftoken"].value

    def test_thumbnail_post_without_csrf_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {**self.payload, "thumbnail": self._image()})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Post.objects.filter(title=self.payload["title"]).exists())

    def test_authenticated_multipart_thumbnail_upload_succeeds_with_csrf(self):
        self.client.force_login(self.user)
        token = self._csrf_token()
        response = self.client.post(
            self.url,
            {**self.payload, "thumbnail": self._image(), "csrfmiddlewaretoken": token},
            HTTP_X_CSRFTOKEN=token,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data["ok"])
        post = Post.objects.get(pk=data["post_id"])
        self.assertTrue(post.thumbnail.name)
        self.assertTrue(post.thumbnail.storage.exists(post.thumbnail.name))
        self.assertEqual(data["redirect_url"], post.get_absolute_url())

    def test_anonymous_thumbnail_upload_is_rejected(self):
        token = self._csrf_token()
        response = self.client.post(
            self.url,
            {**self.payload, "thumbnail": self._image(), "csrfmiddlewaretoken": token},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertIn(response.status_code, {302, 403})
        self.assertFalse(Post.objects.filter(title=self.payload["title"]).exists())

    def test_non_image_thumbnail_is_rejected(self):
        self.client.force_login(self.user)
        token = self._csrf_token()
        invalid = SimpleUploadedFile("thumbnail.txt", b"not an image", content_type="text/plain")
        response = self.client.post(
            self.url,
            {**self.payload, "thumbnail": invalid, "csrfmiddlewaretoken": token},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Post.objects.filter(title=self.payload["title"]).exists())
