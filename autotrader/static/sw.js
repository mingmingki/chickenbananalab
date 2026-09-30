// PWA/TWA 설치 조건(installability)을 만족시키기 위한 최소 서비스워커.
//
// 매매 데이터(포지션/잔고/거래기록)는 절대 캐시하지 않는다 - 실거래 앱에서 오프라인
// 캐시나 stale 응답을 실시간 데이터처럼 보여주면 실제 계좌 상태와 다른 값을 보고
// 잘못된 판단(예: 이미 청산된 포지션을 아직 열려있다고 착각)을 할 위험이 있다.
// 그래서 fetch를 가로채기만 하고 캐싱 없이 항상 네트워크로 그대로 전달한다.

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});

// Web Push(2026-08-28, 사용자 지시 - "텔레그램 말고 이 사이트에서 보내고 홈화면에
// 어플처럼 받을 수 있게") - 서버(web_push.py)가 보낸 진입/청산/kill switch 알림을
// 실제 OS 알림으로 띄운다. payload가 없거나 JSON이 아니면 조용히 무시한다(실거래
// 데이터가 아니라 알림 표시일 뿐이라 fail-open이어도 안전).
self.addEventListener("push", (event) => {
  let payload = { title: "치킨바나나랩", body: "" };
  try {
    if (event.data) payload = event.data.json();
  } catch (e) {
    // 무시 - 기본 payload로 대체
  }
  event.waitUntil(
    self.registration.showNotification(payload.title || "치킨바나나랩", {
      body: payload.body || "",
      icon: "/static/img/icon-192.png",
      badge: "/static/img/icon-192.png",
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ("focus" in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow("/");
    })
  );
});
