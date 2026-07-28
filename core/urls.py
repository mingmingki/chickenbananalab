from . import quantity_views
from . import gemini_usage_views
from . import ai_topic_pool
from .ip_tools import ip_lookup_api
from django.urls import path
from . import views
from . import program_downloads
from . import home_program_downloads
from .crypto_market import crypto_market_api


from .job_views import (
    job_main,
    api_get_questions,
    api_save_answer,
    api_transcribe_answer,
    api_analyze_interview,
    api_download_report,
)

from django.urls import path as cbl_path
from . import views as cbl_views
from . import views as cbl_views_v21_2

urlpatterns = [
    path("api/cblcad/v29/open-session/", views.cblcad_v29_open_session, name="cblcad_v29_open_session"),
    path("api/cblcad/v29/save-ops/", views.cblcad_v29_save_ops, name="cblcad_v29_save_ops"),

cbl_path("api/cblcad/csrf/", cbl_views.cblcad_csrf, name="cblcad_csrf"),
    # CBL CAD DWG BEST DXF URLS V1 START
    # 기존 프론트가 /api/cblcad/dwg-to-dxf/ 를 호출하면 이 새 백엔드가 먼저 잡는다.
    path("api/cblcad/dwg-to-dxf/", views.cblcad_dwg_to_best_dxf_api, name="cblcad_dwg_to_dxf_best_v1"),
    # 직접 테스트용 별도 엔드포인트
    path("api/cblcad/dwg-to-best-dxf/", views.cblcad_dwg_to_best_dxf_api, name="cblcad_dwg_to_best_dxf_v1"),
    # CBL CAD DWG BEST DXF URLS V1 END

    path('quantity/', quantity_views.quantity_main, name='quantity_main'),
    path('api/quantity/check-zip/', quantity_views.api_check_zip, name='api_check_zip'),
    path('api/quantity/overview-check/', quantity_views.api_quantity_overview_check, name='api_quantity_overview_check'),
    path('api/quantity/overview-revise/', quantity_views.api_quantity_overview_revise, name='api_quantity_overview_revise'),
    path('api/quantity/basement-plan-check/', quantity_views.api_quantity_basement_plan_check, name='api_quantity_basement_plan_check'),
    path('api/quantity/review-confirm/', quantity_views.api_quantity_review_confirm, name='api_quantity_review_confirm'),
    path('api/quantity/run/', quantity_views.api_run_quantity, name='api_run_quantity'),
    path('api/quantity/progress/', quantity_views.api_quantity_progress, name='api_quantity_progress'),
    path('api/quantity/cancel/', quantity_views.api_quantity_cancel, name='api_quantity_cancel'),
    path('api/quantity/confirm-review/', quantity_views.api_quantity_confirm_review, name='api_quantity_confirm_review'),
    path('api/quantity/download-excel/', quantity_views.api_download_excel, name='api_download_excel'),

    path("job/", job_main, name="job_main"),
    path("ai-construction-jobs/", job_main, name="ai_construction_jobs"),
    path("api/job/questions/", api_get_questions, name="api_job_questions"),
    path("api/job/save-answer/", api_save_answer, name="api_job_save_answer"),
    path("api/job/transcribe-answer/", api_transcribe_answer, name="api_job_transcribe_answer"),
    path("api/job/analyze/", api_analyze_interview, name="api_job_analyze"),
    path("api/job/download-report/", api_download_report, name="api_job_download_report"),

    path("api/cblcad/dxf-to-dwg/", views.cblcad_dxf_to_dwg_save_api, name="cblcad_dxf_to_dwg_save_api"),
    path("api/cblcad/dxf-to-dwg", views.cblcad_dxf_to_dwg_save_api, name="cblcad_dxf_to_dwg_save_api_no_slash"),

    path("api/cblcad/dwg-to-dxf/", views.cblcad_dwg_to_dxf_clean_api, name="cblcad_dwg_to_dxf_clean_api"),
    path("api/cblcad/dwg-to-dxf", views.cblcad_dwg_to_best_dxf_api, name="cblcad_dwg_to_dxf_best_v21_5_no_slash"),



    path('calendar-delete/<int:pk>/', views.calendar_event_delete_now_view, name='calendar_event_delete_now_view'),
    # CBL_CALENDAR_REAL_ACTION_URL_START
    path('api/calendar-events/<int:pk>/delete-real/', views.calendar_event_delete_real_api, name='calendar_event_delete_real_api'),
    path('api/calendar-events/<int:pk>/update-real/', views.calendar_event_update_real_api, name='calendar_event_update_real_api'),
    # CBL_CALENDAR_REAL_ACTION_URL_END

    # CBL_CALENDAR_FORCE_DELETE_URL_START
    path('api/calendar-events/<int:pk>/force-delete/', views.calendar_event_force_delete_api, name='calendar_event_force_delete_api'),
    # CBL_CALENDAR_FORCE_DELETE_URL_END

    path('api/calendar-events/create/', views.calendar_event_create_api, name='calendar_event_create_api'),
    # CBL_CALENDAR_AI_SUGGEST_URL_START
    path('api/calendar-events/ai-suggest/', views.calendar_ai_suggest_api, name='calendar_ai_suggest_api'),
    path('api/calendar-events/ai-bulk-create/', views.calendar_ai_bulk_create_api, name='calendar_ai_bulk_create_api'),
    # CBL_CALENDAR_AI_SUGGEST_URL_END
    # CBL_CALENDAR_FINAL_DELETE_URL_START
    path('api/calendar-events/<int:pk>/delete-final/', views.calendar_event_delete_final_api, name='calendar_event_delete_final_api'),
    path('api/calendar-events/<int:pk>/update-final/', views.calendar_event_update_final_api, name='calendar_event_update_final_api'),
    # CBL_CALENDAR_FINAL_DELETE_URL_END

    # CBL_CALENDAR_CLEAN_DELETE_URL_START
    path('api/calendar-events/<int:pk>/update-clean/', views.calendar_event_update_clean_api, name='calendar_event_update_clean_api'),
    path('api/calendar-events/<int:pk>/delete-clean/', views.calendar_event_delete_clean_api, name='calendar_event_delete_clean_api'),
    # CBL_CALENDAR_CLEAN_DELETE_URL_END


    # CBL_CALENDAR_EDIT_DELETE_URL_V3_START
    path('api/calendar-events/<int:pk>/update-v3/', views.calendar_event_update_v3_api, name='calendar_event_update_v3_api'),
    path('api/calendar-events/<int:pk>/delete-v3/', views.calendar_event_delete_v3_api, name='calendar_event_delete_v3_api'),
    # CBL_CALENDAR_EDIT_DELETE_URL_V3_END


    # CBL_CALENDAR_EDIT_DELETE_URL_V2_START
    path('api/calendar-events/<int:pk>/update/', views.calendar_event_update_api, name='calendar_event_update_api'),
    path('api/calendar-events/<int:pk>/delete/', views.calendar_event_delete_api, name='calendar_event_delete_api'),
    # CBL_CALENDAR_EDIT_DELETE_URL_V2_END


    # CBL_CALENDAR_MANAGE_URLS_START
    # CBL_CALENDAR_MANAGE_URLS_END



    path('api/calendar-events/', views.calendar_events_month_api, name='calendar_events_month_api'),

    path(
        "api/program-downloads/status/",
        program_downloads.program_download_status,
        name="program_download_status",
    ),
    path(
        "api/program-downloads/<slug:slug>/<str:platform>/upload/",
        program_downloads.program_download_upload,
        name="program_download_upload",
    ),
    path(
        "api/program-downloads/<slug:slug>/<str:platform>/delete/",
        program_downloads.program_download_delete,
        name="program_download_delete",
    ),
    path(
        "api/program-downloads/<slug:slug>/<str:platform>/publish/",
        program_downloads.program_download_publish,
        name="program_download_publish",
    ),

    path("api/home-programs/status/", home_program_downloads.home_program_status, name="home_program_status"),
    path("api/home-programs/upload/", home_program_downloads.home_program_upload, name="home_program_upload"),
    path("api/home-programs/<int:pk>/toggle/", home_program_downloads.home_program_toggle_public, name="home_program_toggle_public"),
    path("api/home-programs/<int:pk>/delete/", home_program_downloads.home_program_delete, name="home_program_delete"),

    path("api/crypto-market/", crypto_market_api, name="crypto_market_api"),
    path("chickenbanana-cut/generate/", views.chickenbanana_cut_generate, name="chickenbanana_cut_generate"),
    path("chickenbanana-cut/ai-script/", views.chickenbanana_cut_ai_script, name="chickenbanana_cut_ai_script"),
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
    path("construction-work/", views.category_page, {"slug": "construction_work"}, name="construction_work"),
    path("construction-tech/", views.category_page, {"slug": "construction_tech"}, name="construction_tech"),
    path("construction-realestate/", views.category_page, {"slug": "construction_real"}, name="construction_real"),
    path("realestate/", views.category_page, {"slug": "realestate"}, name="realestate"),
    path("bim/", views.category_page, {"slug": "bim"}, name="bim"),
    path("finance/", views.category_page, {"slug": "finance"}, name="finance"),
    path("tech/", views.category_page, {"slug": "tech"}, name="tech"),
    path("program/", views.category_page, {"slug": "program"}, name="program"),
    path("dynamo-automation/", views.category_page, {"slug": "dynamo_automation"}, name="dynamo_automation"),
    path("four-d-five-d/", views.category_page, {"slug": "four_d_five_d"}, name="four_d_five_d"),
    path("tool-recommend/", views.category_page, {"slug": "tool_recommend"}, name="tool_recommend"),
    path("life/", views.category_page, {"slug": "life"}, name="life"),

    path("post/add/", views.post_create, name="post_create"),
    path("video/upload/", views.video_post_upload, name="video_post_upload"),

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
    path("dashboard/gemini-usage/", gemini_usage_views.gemini_usage_dashboard, name="gemini_usage_dashboard"),
    # CBL_AI_FALLBACK_TOPIC_POOL_V1_URL
    path("dashboard/ai-fallback-topics/", ai_topic_pool.ai_fallback_topic_manage, name="ai_fallback_topic_manage"),

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
urlpatterns += [
    path("community/", views.community, name="community"),
]
