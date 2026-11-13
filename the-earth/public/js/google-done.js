// 구글 로그인에서 돌아온 화면.
// 로그인을 시작한 그 앱(같은 저장소)이면 바로 게임으로 돌아가고,
// 다른 브라우저로 열렸으면(아이폰 홈 화면 앱 → Safari 등) 앱으로 돌아가라고 안내한다.
(function () {
  const q = new URLSearchParams(location.search);
  const $ = id => document.getElementById(id);
  let mine = null;
  try { mine = JSON.parse(localStorage.getItem('theEarth.glogin') || 'null'); } catch { /* */ }
  const fresh = mine && Date.now() - mine.at < 15 * 60_000;
  // 홈 화면 앱이 연 창이면 여기서 게임을 열지 않고, 기다리는 앱에게 넘긴다
  const handBack = fresh && (mine.popup || window.opener);
  if (q.get('err')) {
    $('title').textContent = '로그인하지 못했어요';
    $('msg').textContent = q.get('err');
    $('home').classList.remove('hidden');
    return;
  }
  if (q.get('ok')) {
    if (fresh && !handBack) { location.replace('/#glogin'); return; }
    $('title').textContent = '로그인되었어요 ✅';
    $('msg').textContent = 'The Earth 앱(또는 처음 로그인을 누른 창)으로 돌아가 주세요. 자동으로 이어집니다.';
    setTimeout(() => { try { window.close(); } catch { /* */ } }, 1500);
    return;
  }
  const code = (q.get('code') || '').replace(/\D/g, '');
  if (code) {
    if (fresh && !handBack) { location.replace('/#glogin=' + code); return; }
    if (handBack) {
      // 같은 저장소를 쓰는 창이면 코드를 앱에 바로 건넨다 (앱이 알아서 입력)
      try { localStorage.setItem('theEarth.glogin.code', code); } catch { /* */ }
      $('title').textContent = '로그인되었어요 ✅';
      $('msg').textContent = 'The Earth 앱으로 돌아가 주세요. 자동으로 이어집니다. 혹시 코드를 물어보면 아래 숫자를 입력하세요.';
      $('code').textContent = code.slice(0, 3) + ' ' + code.slice(3);
      $('code').classList.remove('hidden');
      setTimeout(() => { try { window.close(); } catch { /* */ } }, 2500);
      return;
    }
    $('title').textContent = '거의 다 됐어요';
    $('msg').textContent = 'The Earth 앱으로 돌아가서 이 확인 코드를 입력해 주세요.';
    $('code').textContent = code.slice(0, 3) + ' ' + code.slice(3);
    $('code').classList.remove('hidden');
    $('sub').textContent = '10분 안에 입력해야 합니다. 이 코드를 다른 사람에게 알려 주지 마세요.';
  }
})();
