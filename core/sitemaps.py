from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Post


class StaticViewSitemap(Sitemap):
    priority = 0.8
    changefreq = "weekly"

    def items(self):
        return ["home", "contact"]

    def location(self, item):
        return reverse(item)


class PostSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.9

    def items(self):
        return Post.objects.all().order_by("-created_at")

    def lastmod(self, obj):
        return obj.updated_at if hasattr(obj, "updated_at") else obj.created_at

    def location(self, obj):
        return reverse("post_detail", args=[obj.id])