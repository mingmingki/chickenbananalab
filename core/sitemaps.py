from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Post


class StaticViewSitemap(Sitemap):
    priority = 0.8
    changefreq = "weekly"

    def items(self):
        return [
            "home",
            "architecture",
            "realestate",
            "finance",
            "tech",
            "life",
            "about",
            "contact",
            "terms",
            "privacy",
        ]

    def location(self, item):
        return reverse(item)


class PostSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.9

    def items(self):
        return Post.objects.filter(is_published=True).order_by("-updated_at", "-created_at")

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()