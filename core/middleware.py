import hashlib

from django.conf import settings
from django.http import JsonResponse

from .models import VisitLog

try:
    from .telegram_alerts import notify_site_visit
except Exception:
    notify_site_visit = None


BOT_KEYWORDS = [
    "bot",
    "spider",
    "crawl",
    "slurp",
    "bingpreview",
    "facebookexternalhit",
    "kakaotalk",
    "naver",
    "googlebot",
]


EXCLUDE_PATH_PREFIXES = [
    "/admin/",
    "/static/",
    "/media/",
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
]


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR", "")


def is_bot_request(request):
    user_agent = request.META.get("HTTP_USER_AGENT", "").lower()
    return any(keyword in user_agent for keyword in BOT_KEYWORDS)


def is_internal_user(user):
    if not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True

    try:
        return bool(user.profile.is_sub_admin)
    except Exception:
        return False


class VisitLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        try:
            self.save_visit_log(request, response)
        except Exception:
            pass

        try:
            if notify_site_visit and self.should_track(request, response) and not is_bot_request(request):
                notify_site_visit(request)
        except Exception:
            pass

        return response

    def should_track(self, request, response):
        if request.method != "GET":
            return False

        if response.status_code >= 400:
            return False

        path = request.path or ""

        for prefix in EXCLUDE_PATH_PREFIXES:
            if path.startswith(prefix):
                return False

        if is_internal_user(getattr(request, "user", None)):
            return False

        return True

    def save_visit_log(self, request, response):
        if not self.should_track(request, response):
            return

        path = request.path or ""
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        referer = request.META.get("HTTP_REFERER", "")
        ip_address = get_client_ip(request)
        is_bot = is_bot_request(request)

        visitor_source = f"{ip_address}|{user_agent}|{settings.SECRET_KEY}"
        visitor_key = hashlib.sha256(visitor_source.encode("utf-8")).hexdigest()

        field_names = {field.name for field in VisitLog._meta.fields}

        data = {}

        if "path" in field_names:
            data["path"] = path

        if "user_agent" in field_names:
            data["user_agent"] = user_agent[:500]

        if "ip_address" in field_names:
            data["ip_address"] = ip_address

        if "referer" in field_names:
            data["referer"] = referer[:500]

        if "visitor_key" in field_names:
            data["visitor_key"] = visitor_key

        if "is_bot" in field_names:
            data["is_bot"] = is_bot

        VisitLog.objects.create(**data)


CAD_API_PREFIX = "/api/cblcad/"


class CadLoginRequiredMiddleware:
    """ChickenBananaCAD is members-only: reject anonymous calls to every CAD API.

    Enforced by URL prefix rather than per view because the CAD views are
    redefined and re-wrapped many times in views.py and routed from both
    config/urls.py and core/urls.py.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info.startswith(CAD_API_PREFIX) and not request.user.is_authenticated:
            return JsonResponse(
                {"ok": False, "error": "login_required", "message": "로그인 후 이용할 수 있습니다."},
                status=401,
                json_dumps_params={"ensure_ascii": False},
            )
        return self.get_response(request)
