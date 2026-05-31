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
    path("post/<int:pk>/publish/", views.post_publish, name="post_publish"),
    path("post/<int:pk>/unpublish/", views.post_unpublish, name="post_unpublish"),

    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),

    path("dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("dashboard/stats/", views.site_stats, name="site_stats"),

    path("ai-post/generate/", views.ai_post_generate, name="ai_post_generate"),
    path("ai-keywords/recommend/", views.ai_keyword_recommend, name="ai_keyword_recommend"),

    path("signup/", views.signup, name="signup"),
    path("profile/setup/", views.profile_setup, name="profile_setup"),
    path("profile/update/", views.profile_update, name="profile_update"),

    path("dashboard/members/", views.member_manage, name="member_manage"),
    path("dashboard/members/<int:user_id>/role/", views.member_role_update, name="member_role_update"),
    path("dashboard/members/<int:user_id>/delete/", views.member_delete, name="member_delete"),
    path("upload/editor-image/", views.editor_image_upload, name="editor_image_upload"),
    path("terms/", views.terms, name="terms"),
    path("privacy/", views.privacy, name="privacy"),

]