import hashlib

from django.conf import settings
from django.utils import timezone

from .models import VisitLog


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


class VisitLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        try:
            self.save_visit_log(request, response)
        except Exception:
            pass

        return response

    def save_visit_log(self, request, response):
        if request.method != "GET":
            return

        if response.status_code >= 400:
            return

        path = request.path or ""

        for prefix in EXCLUDE_PATH_PREFIXES:
            if path.startswith(prefix):
                return

        if hasattr(request, "user") and request.user.is_authenticated and request.user.is_staff:
            return

        user_agent = request.META.get("HTTP_USER_AGENT", "")
        user_agent_lower = user_agent.lower()

        is_bot = any(keyword in user_agent_lower for keyword in BOT_KEYWORDS)

        ip = self.get_client_ip(request)

        ip_hash = self.hash_value(ip)
        visitor_key = self.hash_value(f"{ip}|{user_agent}")

        referer = request.META.get("HTTP_REFERER", "")

        VisitLog.objects.create(
            path=path,
            method=request.method,
            visitor_key=visitor_key,
            ip_hash=ip_hash,
            user_agent=user_agent[:1000],
            referer=referer[:1000],
            is_bot=is_bot,
        )

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()

        return request.META.get("REMOTE_ADDR", "")

    def hash_value(self, value):
        secret = getattr(settings, "SECRET_KEY", "")
        raw = f"{secret}|{value}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()