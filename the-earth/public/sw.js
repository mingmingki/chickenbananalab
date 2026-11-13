// 서비스 워커: 휴대폰 알림(웹 푸시)을 받고, 다시 열 때 빨리 뜨도록 화면 파일을 보관한다.
const CACHE = 'earth-v2';
const SHELL = ['/', '/css/style.css', '/js/main.js', '/manifest.webmanifest', '/icons/icon-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  // 실시간 데이터는 손대지 않는다 (지구 지도와 게임 설명만 보관)
  if (url.pathname.startsWith('/api/') && url.pathname !== '/api/geo' && url.pathname !== '/api/meta') return;
  if (url.pathname.startsWith('/ws') || url.pathname.startsWith('/admin')) return;
  // three.js 같은 라이브러리는 바뀌지 않으니 보관본부터
  if (url.pathname.startsWith('/vendor/') || url.pathname.startsWith('/textures/')) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => { if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); } return res; })));
    return;
  }
  // 나머지는 언제나 최신 것을 먼저, 인터넷이 안 될 때만 보관본
  e.respondWith(fetch(req).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req).then(hit => hit || caches.match('/'))));
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data?.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || 'The Earth', {
    body: d.body || '', tag: d.tag || 'earth', renotify: true, icon: '/icons/icon-192.png', badge: '/icons/icon-192.png', data: { url: d.url || '/' },
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = e.notification.data?.url || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const c of list) if ('focus' in c) return c.focus();
    return self.clients.openWindow(url);
  }));
});
