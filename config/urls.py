from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse

from django.contrib.sitemaps.views import sitemap
from core.sitemaps import StaticViewSitemap, PostSitemap
from core.views import robots_txt


sitemaps = {
    "static": StaticViewSitemap,
    "posts": PostSitemap,
}


def ads_txt(request):
    content = "google.com, pub-7026110887847114, DIRECT, f08c47fec0942fa0\n"
    return HttpResponse(content, content_type="text/plain")


urlpatterns = [
    path("admin/", admin.site.urls),

    # AdSense ads.txt
    path("ads.txt", ads_txt, name="ads_txt"),

    # sitemap / robots
    path(
        "sitemap.xml",
        sitemap,
        {"sitemaps": sitemaps},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    path("robots.txt", robots_txt, name="robots_txt"),

    # Django 기본 로그인/로그아웃
    path("accounts/", include("django.contrib.auth.urls")),

    # Google 로그인 / django-allauth
    path("accounts/", include("allauth.urls")),

    # 내 사이트
    path("", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)