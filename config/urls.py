from core import views as core_views
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
    # CBL CAD DWG BEST DXF URLS V1 START
    # 기존 프론트가 /api/cblcad/dwg-to-dxf/ 를 호출하면 이 새 백엔드가 먼저 잡는다.
    path("api/cblcad/dwg-to-dxf/", core_views.cblcad_dwg_to_best_dxf_api, name="cblcad_dwg_to_dxf_best_v1"),
    # 직접 테스트용 별도 엔드포인트
    path("api/cblcad/dwg-to-best-dxf/", core_views.cblcad_dwg_to_best_dxf_api, name="cblcad_dwg_to_best_dxf_v1"),
    # CBL CAD DWG BEST DXF URLS V1 END

    path("cblcad/", core_views.cblcad_direct_view, name="cblcad_direct"),

    path("tools/cad/", core_views.webcad_tool, name="webcad_tool"),

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
