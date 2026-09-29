from django.apps import AppConfig
from django.contrib.staticfiles.apps import StaticFilesConfig


class CblStaticFilesConfig(StaticFilesConfig):
    # nginx serves STATIC_ROOT without a login check, so the CAD app HTML must
    # never be collected there; it is served by the login-protected CAD views.
    ignore_patterns = StaticFilesConfig.ignore_patterns + ["CBLCAD_VER2.html*"]


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        import core.signals
        from core.gemini_usage import install_gemini_usage_tracking

        install_gemini_usage_tracking()
