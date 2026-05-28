from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),

    # Django 기본 로그인/로그아웃
    path("accounts/", include("django.contrib.auth.urls")),

    # Google 로그인 / django-allauth
    path("accounts/", include("allauth.urls")),

    # 내 사이트
    path("", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)