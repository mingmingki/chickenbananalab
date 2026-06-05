import json
import ipaddress
import urllib.parse
import urllib.request

from django.http import JsonResponse


def ip_lookup_api(request):
    ip = (request.GET.get("ip") or "").strip()

    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return JsonResponse(
            {"ok": False, "error": "올바른 IP 주소를 입력해주세요."},
            status=400,
            json_dumps_params={"ensure_ascii": False},
        )

    url = (
        "http://ip-api.com/json/"
        + urllib.parse.quote(ip)
        + "?fields=status,message,query,country,regionName,city,isp,timezone,lat,lon"
    )

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ChickenBananaLab-IP-Checker/1.0"},
        )

        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode("utf-8", "ignore"))

        if data.get("status") != "success":
            return JsonResponse(
                {"ok": False, "error": data.get("message") or "IP 조회에 실패했습니다."},
                status=502,
                json_dumps_params={"ensure_ascii": False},
            )

        return JsonResponse(
            {
                "ok": True,
                "ip": data.get("query") or ip,
                "country": data.get("country") or "-",
                "region": data.get("regionName") or "-",
                "city": data.get("city") or "-",
                "isp": data.get("isp") or "-",
                "timezone": data.get("timezone") or "-",
                "latitude": data.get("lat") or "-",
                "longitude": data.get("lon") or "-",
            },
            json_dumps_params={"ensure_ascii": False},
        )

    except Exception:
        return JsonResponse(
            {"ok": False, "error": "서버에서 IP 정보를 조회하지 못했습니다."},
            status=502,
            json_dumps_params={"ensure_ascii": False},
        )
