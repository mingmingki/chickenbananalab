"""Web Push(VAPID) 알림 채널(2026-08-28, 사용자 지시 - "텔레그램 말고 이 사이트에서
보내고 홈화면에 어플처럼 받을 수 있게").

기존 텔레그램 알림(trader.py/fast_engine.py/fast_scheduler.py의 _notify_telegram/
_notify_entry/_notify_exit 등)은 그대로 유지한다 - 이미 검증된 채널을 없애는 게
아니라, 홈 화면에 PWA/TWA로 설치한 브라우저(iOS 16.4+/Android Chrome)로도 똑같이
받을 수 있게 "추가" 채널을 만드는 것뿐이다. 구독이 없거나 발송이 실패해도 절대
예외를 위로 던지지 않는다(fail-open) - 이 함수들은 실제 매매 스레드(CORE/FAST
사이클)에서 호출되므로, 알림 발송 실패가 매매를 막으면 안 된다."""
import base64
import hashlib
import json
import logging
import os
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush

import config
import jsonl_cache

logger = logging.getLogger("trader.web_push")

VAPID_CLAIMS_SUB = "mailto:admin@chickenbananatrader.pe.kr"
SUBSCRIPTIONS_FILE_NAME = "push_subscriptions.json"
FAILURE_STATE_FILE_NAME = "push_delivery_failures.json"
REPEATED_WNS_BAD_REQUEST_PRUNE_THRESHOLD = 3
# 계정 하나가 여러 기기(폰+아이패드 등)에 설치할 수 있으므로 여러 구독을 허용하되,
# 무한정 쌓이지 않도록 상한을 둔다(오래된 것부터 밀어냄).
MAX_SUBSCRIPTIONS_PER_ACCOUNT = 10


def _vapid_key_path(project_dir: str) -> str:
    return os.path.join(project_dir, "vapid_private_key.pem")


def ensure_vapid_keys(project_dir: str) -> None:
    """프로세스 시작 시 한 번 호출 - 키가 없으면 새로 만든다. VAPID 키는 계정별이
    아니라 서버(애플리케이션) 전체에 하나만 있으면 된다(브라우저 푸시 서비스에
    "이 서버가 보낸 게 맞다"는 걸 증명하는 신원 증명이지, 계정 식별용이 아니다).
    이미 있으면 절대 재생성하지 않는다 - 재생성하면 기존에 등록된 모든 구독이
    무효화되어 전부 다시 구독해야 한다."""
    path = _vapid_key_path(project_dir)
    if os.path.exists(path):
        return
    os.makedirs(project_dir, exist_ok=True)
    vapid = Vapid01()
    vapid.generate_keys()
    vapid.save_key(path)
    logger.info("[web_push] VAPID 키 최초 생성: %s", path)


def get_vapid_public_key_b64(project_dir: str) -> str | None:
    """브라우저의 pushManager.subscribe({applicationServerKey: ...})에 그대로 넘길
    수 있는 형식(URL-safe base64, padding 없음, 65바이트 uncompressed EC point)으로
    반환한다. 키가 아직 없으면(ensure_vapid_keys를 안 거쳤으면) None."""
    path = _vapid_key_path(project_dir)
    if not os.path.exists(path):
        return None
    vapid = Vapid01.from_file(path)
    raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _subscriptions_path(user_dir: str) -> str:
    return os.path.join(user_dir, SUBSCRIPTIONS_FILE_NAME)


def _failure_state_path(user_dir: str) -> str:
    return os.path.join(user_dir, FAILURE_STATE_FILE_NAME)


def _endpoint_key(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _load_failure_state(user_dir: str) -> dict:
    path = _failure_state_path(user_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_failure_state(user_dir: str, data: dict) -> None:
    path = _failure_state_path(user_dir)
    os.makedirs(user_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _record_bad_request(user_dir: str, endpoint: str) -> int:
    path = _failure_state_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load_failure_state(user_dir)
        key = _endpoint_key(endpoint)
        count = int(data.get(key, 0) or 0) + 1
        data[key] = count
        _save_failure_state(user_dir, data)
        return count


def _clear_bad_request(user_dir: str, endpoint: str) -> None:
    path = _failure_state_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load_failure_state(user_dir)
        key = _endpoint_key(endpoint)
        if key not in data:
            return
        data.pop(key, None)
        _save_failure_state(user_dir, data)


def load_subscriptions(user_dir: str) -> list[dict]:
    path = _subscriptions_path(user_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        logger.exception("[web_push] 구독 목록 로드 실패 - 손상된 것으로 간주하고 빈 목록 취급: %s", path)
        return []


def _save_subscriptions(user_dir: str, subscriptions: list[dict]) -> None:
    os.makedirs(user_dir, exist_ok=True)
    path = _subscriptions_path(user_dir)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(subscriptions, f, ensure_ascii=False)
    os.replace(tmp_path, path)


def add_subscription(user_dir: str, subscription: dict) -> None:
    """endpoint를 기준으로 중복 제거한다(같은 기기가 재구독하면 갱신) - 상한
    초과 시 가장 오래된 구독부터 밀어낸다."""
    path = _subscriptions_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        subs = load_subscriptions(user_dir)
        endpoint = subscription.get("endpoint")
        subs = [s for s in subs if s.get("endpoint") != endpoint]
        subs.append(subscription)
        if len(subs) > MAX_SUBSCRIPTIONS_PER_ACCOUNT:
            subs = subs[-MAX_SUBSCRIPTIONS_PER_ACCOUNT:]
        _save_subscriptions(user_dir, subs)


def remove_subscription(user_dir: str, endpoint: str) -> None:
    path = _subscriptions_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        subs = load_subscriptions(user_dir)
        subs = [s for s in subs if s.get("endpoint") != endpoint]
        _save_subscriptions(user_dir, subs)


def _remove_stale_endpoints(user_dir: str, stale_endpoints: set) -> None:
    if not stale_endpoints:
        return
    path = _subscriptions_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        subs = load_subscriptions(user_dir)
        subs = [s for s in subs if s.get("endpoint") not in stale_endpoints]
        _save_subscriptions(user_dir, subs)


def send_push_notification(cfg, title: str, body: str) -> None:
    """구독된 모든 기기로 알림을 보낸다. 구독이 없으면(대부분의 경우) 아무 것도
    안 하고 즉시 반환한다 - VAPID 키 파일도 읽지 않는다. 실패해도(만료된 구독,
    네트워크 오류 등) 예외를 던지지 않는다(fail-open, 텔레그램 알림과 동일 원칙) -
    호출부는 매매 사이클 스레드이므로 알림 실패가 매매를 막으면 안 된다. 브라우저
    푸시 서비스가 410/404(Gone/NotFound)로 응답하면 그 구독은 만료된 것이므로
    자동으로 정리한다."""
    try:
        subs = load_subscriptions(cfg.user_dir)
    except Exception:
        logger.exception("[web_push] 구독 목록 로드 중 예상치 못한 오류")
        return
    if not subs:
        return

    key_path = _vapid_key_path(config.PROJECT_DIR)
    if not os.path.exists(key_path):
        logger.warning("[web_push] VAPID 키가 없어 발송 스킵")
        return

    payload = json.dumps({"title": title, "body": body})
    stale_endpoints = set()
    for sub in subs:
        endpoint = sub.get("endpoint") or ""
        host = urlsplit(endpoint).netloc.lower()
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=key_path,
                vapid_claims={"sub": VAPID_CLAIMS_SUB},
            )
            if endpoint:
                _clear_bad_request(cfg.user_dir, endpoint)
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            response_text = ""
            if exc.response is not None:
                try:
                    response_text = exc.response.text or ""
                except Exception:
                    response_text = ""
            body_lower = response_text.lower()
            permanent_400_markers = (
                "invalidregistration",
                "notregistered",
                "registration-token-not-registered",
                "invalid registration",
                "invalid subscription",
                "invalidargument",
                "invalid argument",
                "expired subscription",
            )
            permanently_invalid = status in (404, 410) or (
                status == 400 and any(marker in body_lower for marker in permanent_400_markers)
            )

            # WNS has been observed to return an empty HTTP 400 for a dead browser
            # endpoint while Apple/FCM subscriptions in the same account return 201.
            # Do not delete on the first ambiguous 400: require three consecutive
            # failures for the same endpoint, persisted across notifications.
            repeated_wns_bad_request = False
            bad_request_count = None
            if (
                status == 400
                and not permanently_invalid
                and host.endswith("notify.windows.com")
                and endpoint
            ):
                bad_request_count = _record_bad_request(cfg.user_dir, endpoint)
                repeated_wns_bad_request = (
                    bad_request_count >= REPEATED_WNS_BAD_REQUEST_PRUNE_THRESHOLD
                )

            if permanently_invalid or repeated_wns_bad_request:
                if endpoint:
                    stale_endpoints.add(endpoint)
                logger.warning(
                    "[web_push] 영구 무효 구독 정리 예약(host=%s status=%s"
                    "%s)",
                    host or "-", status,
                    f" repeated400={bad_request_count}" if bad_request_count is not None else "",
                )
            else:
                diagnostic = response_text[:300].replace("\n", " ") if response_text else str(exc)
                logger.warning(
                    "[web_push] 발송 실패(host=%s status=%s%s, 구독 유지): %s",
                    host or "-", status,
                    f" repeated400={bad_request_count}" if bad_request_count is not None else "",
                    diagnostic,
                )
        except Exception:
            logger.exception("[web_push] 발송 중 예상치 못한 오류(host=%s)", host or "-")

    if stale_endpoints:
        try:
            _remove_stale_endpoints(cfg.user_dir, stale_endpoints)
            for endpoint in stale_endpoints:
                _clear_bad_request(cfg.user_dir, endpoint)
        except Exception:
            logger.exception("[web_push] 만료 구독 정리 실패(치명적이지 않음)")
