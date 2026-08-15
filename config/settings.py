from pathlib import Path
import os
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent

# .env 파일 로드
load_dotenv(BASE_DIR / ".env", override=True)


# ================================
# 기본 설정
# ================================

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-j+*2gox^lvvm(@_*5s9d8e7^x3^jh&pv)$p$_pc4!m&v94puom"
)

# Production must be non-debug unless a local developer explicitly opts in.
DEBUG = os.getenv("DJANGO_DEBUG", "False") == "True"
CSRF_FAILURE_VIEW = "core.csrf_diagnostics.csrf_failure"

ALLOWED_HOSTS = [
    "*",
]


# ================================
# 카카오 지도 API / 네이버 API
# ================================

KAKAO_JAVASCRIPT_KEY = os.getenv("KAKAO_JAVASCRIPT_KEY", "")

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")


# ================================
# 텔레그램 알림
# 실제 토큰/채팅ID는 .env에만 넣고,
# settings.py에서는 읽어오기만 함
# ================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TELEGRAM_NOTIFY_VISIT = os.getenv("TELEGRAM_NOTIFY_VISIT", "1") == "1"
TELEGRAM_NOTIFY_POST_VIEW = os.getenv("TELEGRAM_NOTIFY_POST_VIEW", "1") == "1"
TELEGRAM_NOTIFY_SIGNUP = os.getenv("TELEGRAM_NOTIFY_SIGNUP", "1") == "1"


# ================================
# 앱 설정
# ================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "django.contrib.sites",
    "django.contrib.sitemaps",

    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",

    "core.apps.CoreConfig",
]


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "core.middleware.VisitLogMiddleware",

    # django-allauth 필수 미들웨어
    "allauth.account.middleware.AccountMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


ROOT_URLCONF = "config.urls"

WSGI_APPLICATION = "config.wsgi.application"


# ================================
# 템플릿 설정
# ================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ================================
# 데이터베이스
# ================================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# ================================
# 비밀번호 검증
# ================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# ================================
# 언어 / 시간
# ================================

LANGUAGE_CODE = "ko-kr"

TIME_ZONE = "Asia/Seoul"

USE_I18N = True

USE_TZ = True


# ================================
# Static / Media
# ================================

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# ================================
# 업로드 용량 제한
# ================================

DATA_UPLOAD_MAX_MEMORY_SIZE = 500 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 500 * 1024 * 1024

# Local free-DWG endpoint cap; enforced before converter subprocess execution.
CBLCAD_FREE_DWG_MAX_UPLOAD_BYTES = 200 * 1024 * 1024

FILE_UPLOAD_HANDLERS = [
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]


# ================================
# 기본 PK
# ================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ================================
# 로그인 / 로그아웃
# ================================

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/profile/setup/"
LOGOUT_REDIRECT_URL = "/"

ACCOUNT_SIGNUP_REDIRECT_URL = "/profile/setup/"
SOCIALACCOUNT_LOGIN_REDIRECT_URL = "/profile/setup/"


# ================================
# Google Login / django-allauth
# ================================

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]

SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_LOGIN_ON_GET = True

SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": [
            "profile",
            "email",
        ],
        "AUTH_PARAMS": {
            "access_type": "online",
            "prompt": "select_account",
        },
        "VERIFIED_EMAIL": True,
    }
}


# ================================
# 관리자 Google 이메일
# ================================

ADMIN_GOOGLE_EMAILS = [
    "pminki3@gmail.com",
]
# ================================
# 텔레그램 알림
# ================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TELEGRAM_NOTIFY_VISIT = os.getenv("TELEGRAM_NOTIFY_VISIT", "1") == "1"
TELEGRAM_NOTIFY_POST_VIEW = os.getenv("TELEGRAM_NOTIFY_POST_VIEW", "1") == "1"
TELEGRAM_NOTIFY_SIGNUP = os.getenv("TELEGRAM_NOTIFY_SIGNUP", "1") == "1"

# CBL_EXTERNAL_API_SHARED_CACHE_START
_CBL_EXTERNAL_API_CACHE_DIR = BASE_DIR / ".django_cache" / "external_api"

if "CACHES" not in globals():
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "cbl-default-cache",
        }
    }

CACHES["external_api"] = {
    "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
    "LOCATION": str(_CBL_EXTERNAL_API_CACHE_DIR),
    "TIMEOUT": 300,
    "OPTIONS": {
        "MAX_ENTRIES": 1000,
        "CULL_FREQUENCY": 3,
    },
}
# CBL_EXTERNAL_API_SHARED_CACHE_END

# CBL_QUANTITY_FILE_LOGGING_START
# 사업개요 자동판독(quantity_overview_locator 등 core.quantity_views 로거)이 콘솔에만
# 찍히고 파일로 남지 않아서, 실제 서버에서 실패한 원인을 나중에 재구성할 수 없었다
# (2026-07-27) — 로그 파일 핸들러를 추가해 다음에도 같은 문제가 생기면 페이지별
# 판독 근거(text_score/predicted_type/selection_reason 등)를 그대로 확인할 수 있게 한다.
_CBL_LOG_DIR = BASE_DIR / "logs"
_CBL_LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "cbl_verbose": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "cbl_quantity_overview_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(_CBL_LOG_DIR / "quantity_overview.log"),
            "maxBytes": 10 * 1024 * 1024,  # 10MB
            "backupCount": 3,
            "formatter": "cbl_verbose",
            "level": "INFO",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "cbl_verbose",
            "level": "INFO",
        },
    },
    "loggers": {
        "core.quantity_views": {
            "handlers": ["cbl_quantity_overview_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
# CBL_QUANTITY_FILE_LOGGING_END
