from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        import core.signals
        from core.gemini_usage import install_gemini_usage_tracking

        install_gemini_usage_tracking()
