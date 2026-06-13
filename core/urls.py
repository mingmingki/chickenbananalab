from .ip_tools import ip_lookup_api
from django.urls import path
from . import views


urlpatterns = [
    path("mini-capcut/", views.mini_capcut_home, name="mini_capcut_home"),
    path("mini-capcut/post/<int:post_id>/", views.mini_capcut_editor, name="mini_capcut_editor"),
    path("mini-capcut/upload/", views.mini_capcut_upload, name="mini_capcut_upload"),
    path("mini-capcut/save/", views.mini_capcut_save, name="mini_capcut_save"),
    path("mini-capcut/export/", views.mini_capcut_export, name="mini_capcut_export"),

    path("tools/ip-lookup/", ip_lookup_api, name="ip_lookup_api"),
    path("", views.home, name="home"),
    path("experience-vault/", views.experience_vault, name="experience_vault"),
    path("search/", views.search, name="search"),

    path("architecture/", views.category_page, {"slug": "architecture"}, name="architecture"),
    path("realestate/", views.category_page, {"slug": "realestate"}, name="realestate"),
    path("finance/", views.category_page, {"slug": "finance"}, name="finance"),
    path("tech/", views.category_page, {"slug": "tech"}, name="tech"),
    path("life/", views.category_page, {"slug": "life"}, name="life"),

    path("post/add/", views.post_create, name="post_create"),

    # 기존 숫자 주소 유지
    path("post/<int:pk>/", views.post_detail_redirect, name="post_detail"),

    # SEO용 한글 slug 주소
    path("post/slug/<path:slug>/", views.post_detail_by_slug, name="post_detail_slug"),

    path(
        "post/<int:pk>/comments/add/",
        views.comment_create,
        name="comment_create",
    ),
    path(
        "comments/<int:comment_id>/delete/",
        views.comment_delete,
        name="comment_delete",
    ),

    path("post/<int:pk>/edit/", views.post_update, name="post_update"),
    path("post/<int:pk>/delete/", views.post_delete, name="post_delete"),
    path("post/<int:pk>/publish/", views.post_publish, name="post_publish"),
    path("post/<int:pk>/unpublish/", views.post_unpublish, name="post_unpublish"),
    path("post/<int:pk>/translate-en/", views.post_translate_english, name="post_translate_english"),
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
    path("dashboard/ai-auto-writer/", views.ai_auto_writer_manage, name="ai_auto_writer_manage"),
    path("post/<int:post_id>/generate-shorts/", views.post_generate_shorts, name="post_generate_shorts"),
]
