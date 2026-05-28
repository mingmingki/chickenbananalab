from django.urls import path
from . import views

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

    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("ai-post/generate/", views.ai_post_generate, name="ai_post_generate"),

    path("signup/", views.signup, name="signup"),
]