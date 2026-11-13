# 휴대폰 앱으로 만들기 (Capacitor)

지금의 웹 화면을 그대로 앱 껍데기에 넣어 아이폰·안드로이드 앱을 만든다. 게임(시뮬레이션)은 계속 서버에서 돈다.

## 이미 준비된 것

- **서버 주소 분리**: 앱 안의 화면은 `<meta name="earth-server">` 에 적힌 서버로 접속한다 (`public/js/net.js`). 웹에서는 비어 있어 지금처럼 같은 주소를 쓴다.
- **CORS**: 서버는 `capacitor://localhost`, `https://localhost`, `http://localhost`, `ionic://localhost` 에서 오는 요청을 허락한다 (`CORS_ORIGINS` 로 바꿀 수 있음).
- **휴대폰 자체 로그인 검증**: `POST /api/auth/google/native`, `POST /api/auth/apple/native` 가 앱이 받은 ID 토큰의 서명(구글·애플 공개키)·발급자·대상·만료·nonce 를 확인한다. 처음이면 아이디 정하기로 이어진다.
- **앱용 화면 파일 만들기**: `node scripts/build-app.mjs https://내서버주소` → `app/www/`
- **기본 설정**: `app/capacitor.config.json` (appId 는 바꿔야 함)
- 앱 안에서 회원 탈퇴, 이름 필터·신고 (스토어 심사에서 보는 것들)

## 만드는 순서

준비물: 맥(아이폰 앱 빌드에 Xcode 필요), Android Studio, 애플 개발자 계정(연 99달러), 구글 플레이 개발자 계정(한 번 25달러).

```bash
cd the-earth
node scripts/build-app.mjs https://the-earth.fly.dev
cd app
npm init -y
npm i @capacitor/core @capacitor/cli @capacitor/ios @capacitor/android @capacitor/push-notifications
# 휴대폰 자체 로그인 플러그인 (예시 — 쓸 때 최신 문서를 확인)
npm i @capgo/capacitor-social-login
npx cap add ios && npx cap add android
npx cap sync
npx cap open ios        # 또는 npx cap open android
```

## 로그인 다리 (`window.EarthNative`)

화면 코드는 앱 껍데기가 아래 모양의 객체를 넣어 주면 휴대폰 자체 로그인을 쓴다 (`public/js/ui/extras.js` 의 `nativeLogin`).

```js
window.EarthNative = {
  apple: true, // 애플 로그인 버튼을 보여 줄지
  async signIn(kind) {          // kind: 'google' | 'apple'
    const nonce = crypto.randomUUID();
    // 플러그인으로 로그인하고 ID 토큰을 받는다 (플러그인마다 함수 이름이 다르다)
    const idToken = await /* 플러그인 호출 */ null;
    return { idToken, nonce };  // 취소하면 { idToken: null }
  },
};
```

구글은 앱 안의 웹 화면에서 하는 로그인을 막아 두었으므로(`disallowed_useragent`) 반드시 휴대폰 자체 로그인 창을 써야 한다.

## 서버 환경 변수 (앱용 추가분)

| 이름 | 뜻 |
|---|---|
| `GOOGLE_NATIVE_CLIENT_IDS` | 안드로이드·iOS용 구글 클라이언트 ID (쉼표로). 웹 클라이언트 ID(`GOOGLE_CLIENT_ID`)도 함께 인정 |
| `APPLE_CLIENT_IDS` | 앱 번들 ID (예: `com.example.theearth`) — 있어야 애플 로그인이 켜짐 |
| `CORS_ORIGINS` | 앱 화면의 출처 (기본값으로 충분) |

## 아직 남은 것 (앱 만들 때)

1. **앱 푸시 알림**: 지금 서버는 웹 푸시만 보낸다. 앱에서는 애플(APNs)·구글(FCM)로 보내야 하므로 `server/push.js` 에 FCM 발송을 더하고, 앱은 `@capacitor/push-notifications` 로 받은 기기 토큰을 서버에 등록한다. (어떤 소식을 모아 보낼지는 그대로 쓴다)
2. **애플 로그인 필수**: 앱스토어 심사 지침 4.8 — 구글 로그인을 넣으면 개인정보 보호 수준이 같은 로그인(사실상 'Apple로 로그인')도 함께 넣어야 한다.
3. **최소 기능 심사(4.2)**: 웹사이트를 감싼 앱은 거절될 수 있다. 화면 파일을 앱 안에 넣고(위 순서가 그렇게 함), 인터넷이 끊겼을 때 화면, 진동·앱 알림 같은 휴대폰 기능을 쓴다.
4. 한국 게임 등급 분류, 유료 아이템을 팔면 애플·구글 결제 사용.
