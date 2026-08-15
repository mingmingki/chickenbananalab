import logging
from urllib.parse import urlsplit

from django.http import HttpResponseForbidden


_logger = logging.getLogger("cbl.csrf_diagnostics")
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(_handler)
    _logger.propagate = False


def _host(value):
    if not value:
        return ""
    return urlsplit(value).netloc or ""


def csrf_failure(request, reason=""):
    token = request.POST.get("csrfmiddlewaretoken", "")
    _logger.warning(
        "csrf_failure path=%s method=%s host=%s origin_host=%s referer_host=%s "
        "cookie_present=%s post_token_present=%s cookie_len=%d post_token_len=%d reason=%s",
        request.path,
        request.method,
        request.get_host(),
        _host(request.META.get("HTTP_ORIGIN")),
        _host(request.META.get("HTTP_REFERER")),
        bool(request.COOKIES.get("csrftoken")),
        bool(token),
        len(request.COOKIES.get("csrftoken", "")),
        len(token),
        reason,
    )
    return HttpResponseForbidden("CSRF 검증에 실패했습니다. 요청을 중단하였습니다.")
