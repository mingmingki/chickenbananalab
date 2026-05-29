from django.contrib import admin
from django.urls import path, include
from . import views
from django.conf import settings
from django.conf.urls.static import static

from django.contrib.sitemaps.views import sitemap
from core.sitemaps import StaticViewSitemap, PostSitemap
from core.views import robots_txt


sitemaps = {
    "static": StaticViewSitemap,
    "posts": PostSitemap,
}

urlpatterns = [
    path("", views.home, name="home"),

    path("search/", views.search, name="search"),

    path("architecture/", views.category_page, {"slug": "architecture"}, name="architecture"),
    path("realestate/", views.category_page, {"slug": "realestate"}, name="realestate"),
    path("finance/", views.category_page, {"slug": "finance"}, name="finance"),
    path("tech/", views.category_page, {"slug": "tech"}, name="tech"),
    path("life/", views.category_page, {"slug": "life"}, name="life"),

    path("post/add/", views.post_create, name="post_create"),
    path("post/<int:pk>/", views.post_detail, name="post_detail"),
    path("post/<int:pk>/edit/", views.post_update, name="post_update"),
    path("post/<int:pk>/delete/", views.post_delete, name="post_delete"),
    path("post/<int:pk>/publish/", views.post_publish, name="post_publish"),
    path("post/<int:pk>/unpublish/", views.post_unpublish, name="post_unpublish"),

    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("dashboard/stats/", views.site_stats, name="site_stats"),
    path("ai-post/generate/", views.ai_post_generate, name="ai_post_generate"),
    path("ai-keywords/recommend/", views.ai_keyword_recommend, name="ai_keyword_recommend"),
    path("signup/", views.signup, name="signup"),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="django.contrib.sitemaps.views.sitemap"),
    path("robots.txt", robots_txt, name="robots_txt"),
]