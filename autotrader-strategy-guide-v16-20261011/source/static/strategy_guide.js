/* Read-only rendering on the existing state refresh; no requests or AI calls. */
(() => {
  let previous = null;
  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  window.renderStrategyGuide = function (guide) {
    const body = document.getElementById('strategy-guide-body');
    if (!body || !guide || !Array.isArray(guide.cards)) return;
    const fingerprint = JSON.stringify(guide);
    if (fingerprint === previous) return; // Reading/scrolling survives ordinary polls.
    const open = new Set(Array.from(body.querySelectorAll('details[open]')).map(el => el.id));
    const grid = node('div', null, 'strategy-guide-grid');
    for (const card of guide.cards) {
      const article = node('article', null, 'strategy-guide-card');
      article.append(node('h3', card.title), node('p', card.summary, 'hint'));
      const dl = node('dl');
      for (const row of card.rows || []) dl.append(node('dt', row.label), node('dd', row.value));
      article.append(dl); grid.append(article);
    }
    const detail = (id, title, content) => {
      const el = node('details', null, 'strategy-guide-details');
      el.id = id; el.open = open.has(id); el.append(node('summary', title), ...content);
      return el;
    };
    const list = (tag, items) => {
      const el = node(tag);
      for (const item of items || []) el.append(node('li', item));
      return el;
    };
    body.replaceChildren(grid,
      detail('strategy-guide-operations', '운영 방법 · 시작부터 청산까지', [list('ol', guide.operations)]),
      detail('strategy-guide-costs', 'AI 비용 · 무엇을 호출하나', [node('p', guide.costs),
        node('p', '이 안내를 조회하는 데 AI를 호출하지 않습니다. 실제 비용은 위 AI 비용 현황에서 확인하세요.')]),
      detail('strategy-guide-updates', '최근 전략 변경', [list('ul', guide.updates),
        node('p', '전략 동작이 바뀌면 설명도 같은 업데이트에 포함해 검증합니다. 설정 수치와 감축 한도는 실제 설정·전략 코드에서 가져옵니다.')])
    );
    document.getElementById('strategy-guide-version').textContent =
      '설명 업데이트 ' + guide.revision + ' · 적용 코드 ' + (guide.applied_at || '적용일 확인 대기');
    previous = fingerprint;
  };
})();
