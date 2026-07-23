from django.contrib import admin
from .models import Post, CalendarEvent


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "created_at", "updated_at")
    list_filter = ("category", "created_at")
    search_fields = ("title", "content")

@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ("event_date", "end_date", "title", "category", "start_time", "is_public", "is_important")
    list_filter = ("is_public", "is_important", "category", "event_date")
    search_fields = ("title", "description", "category")
    ordering = ("event_date", "start_time", "id")


from .models import CommunityQuestion

@admin.register(CommunityQuestion)
class CommunityQuestionAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "author_name", "is_public", "created_at", "answered_at")
    list_filter = ("category", "is_public", "created_at")
    search_fields = ("title", "body", "answer", "author_name", "contact")
    readonly_fields = ("created_at", "answered_at")
    fieldsets = (
        ("문의 내용", {
            "fields": ("category", "title", "body", "author_name", "contact", "is_public")
        }),
        ("답변", {
            "fields": ("answer", "created_at", "answered_at")
        }),
    )


from .models import ProgramDownload


@admin.register(ProgramDownload)
class ProgramDownloadAdmin(admin.ModelAdmin):
    list_display = ("order", "name", "mac_is_public", "windows_is_public", "updated_at")
    list_editable = ("mac_is_public", "windows_is_public")
    search_fields = ("name", "description", "slug")
    list_filter = ("mac_is_public", "windows_is_public")
    ordering = ("order", "id")

from .models import GeminiUsageLog


@admin.register(GeminiUsageLog)
class GeminiUsageLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "feature", "model", "prompt_tokens", "output_tokens", "total_tokens", "image_inputs", "is_success")
    list_filter = ("feature", "model", "is_success", "created_at")
    search_fields = ("model", "callsite", "error_type", "error_message")
    readonly_fields = [field.name for field in GeminiUsageLog._meta.fields]
    ordering = ("-created_at", "-id")

    def has_add_permission(self, request):
        return False


# CBL_AI_FALLBACK_TOPIC_POOL_V1_ADMIN_START
from .models import AIFallbackTopic


@admin.register(AIFallbackTopic)
class AIFallbackTopicAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "category",
        "status",
        "content_format",
        "difficulty",
        "recommendation_count",
        "last_recommended_at",
        "created_at",
    )
    list_filter = (
        "category",
        "status",
        "content_format",
        "difficulty",
        "created_at",
    )
    search_fields = ("title", "note", "source_model")
    readonly_fields = (
        "normalized_title",
        "source_model",
        "recommendation_count",
        "last_recommended_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at", "-id")
# CBL_AI_FALLBACK_TOPIC_POOL_V1_ADMIN_END
