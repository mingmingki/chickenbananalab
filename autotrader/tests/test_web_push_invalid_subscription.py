from types import SimpleNamespace

import web_push


class Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class PushError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.response = Response(status, text)


def _cfg(tmp_path):
    return SimpleNamespace(user_dir=str(tmp_path))


def test_permanent_400_invalid_registration_is_pruned(tmp_path, monkeypatch):
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/bad", "keys": {"p256dh": "x", "auth": "y"}}
    removed = []
    monkeypatch.setattr(web_push, "WebPushException", PushError)
    monkeypatch.setattr(web_push, "load_subscriptions", lambda user_dir: [sub])
    monkeypatch.setattr(web_push.os.path, "exists", lambda path: True)
    monkeypatch.setattr(web_push, "webpush", lambda **kwargs: (_ for _ in ()).throw(PushError(400, "InvalidRegistration")))
    monkeypatch.setattr(web_push, "_remove_stale_endpoints", lambda user_dir, endpoints: removed.extend(endpoints))

    web_push.send_push_notification(_cfg(tmp_path), "t", "b")

    assert removed == [sub["endpoint"]]


def test_unexplained_400_is_retained(tmp_path, monkeypatch):
    sub = {"endpoint": "https://example.invalid/push", "keys": {"p256dh": "x", "auth": "y"}}
    removed = []
    monkeypatch.setattr(web_push, "WebPushException", PushError)
    monkeypatch.setattr(web_push, "load_subscriptions", lambda user_dir: [sub])
    monkeypatch.setattr(web_push.os.path, "exists", lambda path: True)
    monkeypatch.setattr(web_push, "webpush", lambda **kwargs: (_ for _ in ()).throw(PushError(400, "Bad Request")))
    monkeypatch.setattr(web_push, "_remove_stale_endpoints", lambda user_dir, endpoints: removed.extend(endpoints))

    web_push.send_push_notification(_cfg(tmp_path), "t", "b")

    assert removed == []


def test_repeated_empty_wns_400_is_pruned_after_three_failures(tmp_path, monkeypatch):
    sub = {
        "endpoint": "https://wns2-pn1p.notify.windows.com/w/dead",
        "keys": {"p256dh": "x", "auth": "y"},
    }
    removed = []
    monkeypatch.setattr(web_push, "WebPushException", PushError)
    monkeypatch.setattr(web_push, "load_subscriptions", lambda user_dir: [sub])
    monkeypatch.setattr(web_push.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        web_push, "webpush",
        lambda **kwargs: (_ for _ in ()).throw(PushError(400, "")),
    )
    monkeypatch.setattr(
        web_push, "_remove_stale_endpoints",
        lambda user_dir, endpoints: removed.extend(endpoints),
    )

    web_push.send_push_notification(_cfg(tmp_path), "t1", "b")
    assert removed == []
    web_push.send_push_notification(_cfg(tmp_path), "t2", "b")
    assert removed == []
    web_push.send_push_notification(_cfg(tmp_path), "t3", "b")
    assert removed == [sub["endpoint"]]
