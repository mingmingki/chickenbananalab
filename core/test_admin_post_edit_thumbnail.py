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


@override_settings(MEDIA_ROOT=Path(tempfile.mkdtemp(prefix="cbl-admin-edit-thumbnail-")))
class AdminPostEditThumbnailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="admin-post-edit",
            password="test-password",
            is_staff=True,
        )
        self.client = Client(enforce_csrf_checks=True)
        self.post = Post.objects.create(
            post_type="video",
            category="construction_work",
            title="실제 목록 관리 수정 경로",
            content="기존 본문",
            youtube_url="https://www.youtube.com/watch?v=existing",
            thumbnail=SimpleUploadedFile("old.png", _png((220, 20, 20)), content_type="image/png"),
            is_published=True,
        )
        self.url = f"/post/{self.post.pk}/edit/"

    def _form_token(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return self.client.cookies["csrftoken"].value

    def _payload(self, token, thumbnail=None, title=None):
        payload = {
            "csrfmiddlewaretoken": token,
            "post_type": "video",
            "category": "construction_work",
            "title": title or self.post.title,
            "thumbnail_text": "",
            "youtube_url": self.post.youtube_url,
            "content": self.post.content,
            "tags": "",
            "is_published": "on",
        }
        if thumbnail is not None:
            payload["thumbnail"] = thumbnail
        return payload

    def test_actual_edit_form_contract_and_missing_csrf(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertIn("csrftoken", self.client.cookies)
        self.assertIn('id="cblPostEditorForm"', html)
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn('id="id_thumbnail"', html)
        self.assertIn('id="thumbnailPreviewImage"', html)
        self.assertIn('URL.createObjectURL(file)', html)
        self.assertIn('thumbnailInput.addEventListener("change"', html)
        self.assertIn('cblCurrentCsrfCookie', html)
        self.assertNotIn('cblPostSubmitFetch', html)

        old_name = self.post.thumbnail.name
        response = self.client.post(
            self.url,
            self._payload("", SimpleUploadedFile("new.png", _png((20, 40, 220)), content_type="image/png")),
        )
        self.assertEqual(response.status_code, 403)
        self.post.refresh_from_db()
        self.assertEqual(self.post.thumbnail.name, old_name)

    def test_csrf_multipart_replaces_thumbnail_on_actual_edit_endpoint(self):
        self.client.force_login(self.user)
        token = self._form_token()
        old_name = self.post.thumbnail.name
        response = self.client.post(
            self.url,
            self._payload(
                token,
                SimpleUploadedFile("new.png", _png((20, 40, 220)), content_type="image/png"),
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertNotEqual(self.post.thumbnail.name, old_name)
        self.assertTrue(self.post.thumbnail.storage.exists(self.post.thumbnail.name))
        self.assertFalse(self.post.thumbnail.storage.exists(old_name))

    def test_actual_edit_without_file_preserves_thumbnail(self):
        self.client.force_login(self.user)
        token = self._form_token()
        old_name = self.post.thumbnail.name
        response = self.client.post(self.url, self._payload(token, title="본문만 수정"))
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.title, "본문만 수정")
        self.assertEqual(self.post.thumbnail.name, old_name)

    def test_actual_edit_rejects_non_image_without_replacing_thumbnail(self):
        self.client.force_login(self.user)
        token = self._form_token()
        old_name = self.post.thumbnail.name
        response = self.client.post(
            self.url,
            self._payload(
                token,
                SimpleUploadedFile("not-image.txt", b"not an image", content_type="text/plain"),
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.post.refresh_from_db()
        self.assertEqual(self.post.thumbnail.name, old_name)
