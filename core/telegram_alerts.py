import html
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


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
    "adsbot",
    "mediapartners-google",
]


EXCLUDE_PATH_PREFIXES = [
    "/admin/",
    "/static/",
    "/media/",
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/ads.txt",
]


def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR", "")


def get_user_agent(request):
    return request.META.get("HTTP_USER_AGENT", "")


def is_bot_request(request):
    user_agent = get_user_agent(request).lower()
    return any(keyword in user_agent for keyword in BOT_KEYWORDS)


def is_admin_request(request):
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_staff)


def send_telegram_message(message):
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = str(getattr(settings, "TELEGRAM_CHAT_ID", "")).strip()

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def notify_site_visit(request):
    if not getattr(settings, "TELEGRAM_NOTIFY_VISIT", True):
        return

    if request.method != "GET":
        return

    if is_admin_request(request) or is_bot_request(request):
        return

    path = request.path or ""

    for prefix in EXCLUDE_PATH_PREFIXES:
        if path.startswith(prefix):
            return

    if path.startswith("/post/"):
        return

    ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    full_url = request.build_absolute_uri()

    cache_key = f"telegram_visit:{ip}:{path}"

    if cache.get(cache_key):
        return

    cache.set(cache_key, True, 60)

    now = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")

    message = (
        "🟢 <b>치킨바나나랩 방문 알림</b>\n\n"
        f"시간: {html.escape(now)}\n"
        f"페이지: {html.escape(path)}\n"
        f"IP: {html.escape(ip)}\n"
        f"URL: {html.escape(full_url)}\n\n"
        f"User-Agent:\n{html.escape(user_agent[:300])}"
    )

    send_telegram_message(message)


def notify_post_view(request, post):
    if not getattr(settings, "TELEGRAM_NOTIFY_POST_VIEW", True):
        return

    if request.method != "GET":
        return

    if is_admin_request(request) or is_bot_request(request):
        return

    ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    full_url = request.build_absolute_uri()

    post_id = getattr(post, "id", "")
    cache_key = f"telegram_post_view:{ip}:{post_id}"

    if cache.get(cache_key):
        return

    cache.set(cache_key, True, 60)

    now = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    title = getattr(post, "title", "")
    views = getattr(post, "views", "")

    try:
        category = post.get_category_display()
    except Exception:
        category = getattr(post, "category", "")

    message = (
        "📖 <b>치킨바나나랩 글 조회 알림</b>\n\n"
        f"시간: {html.escape(now)}\n"
        f"제목: {html.escape(title)}\n"
        f"카테고리: {html.escape(str(category))}\n"
        f"조회수: {html.escape(str(views))}\n"
        f"IP: {html.escape(ip)}\n"
        f"URL: {html.escape(full_url)}\n\n"
        f"User-Agent:\n{html.escape(user_agent[:300])}"
    )

    send_telegram_message(message)


def notify_signup(request, user):
    if not getattr(settings, "TELEGRAM_NOTIFY_SIGNUP", True):
        return

    ip = get_client_ip(request)
    now = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")

    username = getattr(user, "username", "")
    email = getattr(user, "email", "")

    message = (
        "👤 <b>치킨바나나랩 회원가입 알림</b>\n\n"
        f"시간: {html.escape(now)}\n"
        f"아이디: {html.escape(username)}\n"
        f"이메일: {html.escape(email or '-')}\n"
        f"IP: {html.escape(ip)}"
    )

    send_telegram_message(message)
