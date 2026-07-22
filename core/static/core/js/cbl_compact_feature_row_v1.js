
(function () {
  function normalizeText(s) {
    return (s || "").replace(/\s+/g, " ").trim();
  }

  function textContains(el, txt) {
    return normalizeText(el.textContent).includes(txt);
  }

  function findTargetCard(keyword, secondaryKeyword) {
    const candidates = Array.from(document.querySelectorAll('section, article, div'));
    let best = null;
    let bestScore = -1;

    candidates.forEach(el => {
      const text = normalizeText(el.textContent);
      if (!text || !text.includes(keyword)) return;

      let score = 0;
      if (secondaryKeyword && text.includes(secondaryKeyword)) score += 5;
      if (text.length > 40) score += 2;

      const rect = el.getBoundingClientRect();
      if (rect.width > 400 && rect.height > 180) score += 8;
      if (rect.width > 700) score += 4;

      if (el.querySelector('a, button')) score += 4;
      if (score > bestScore) {
        best = el;
        bestScore = score;
      }
    });

    return best;
  }

  function createCalendarCard() {
    const card = document.createElement('section');
    card.className = 'cbl-compact-calendar-card';
    card.innerHTML = `
      <div class="cbl-compact-calendar-top">
        <div>
          <span class="cbl-compact-calendar-kicker">주요 일정</span>
          <strong class="cbl-compact-calendar-title" data-cbl-cal-title>이번 달</strong>
        </div>
        <div class="cbl-compact-calendar-nav">
          <button type="button" data-cbl-cal-prev>‹</button>
          <button type="button" data-cbl-cal-today>오늘</button>
          <button type="button" data-cbl-cal-next>›</button>
        </div>
      </div>

      <div class="cbl-compact-calendar-body">
        <div class="cbl-compact-calendar-grid">
          <div class="cbl-compact-calendar-week">
            <span>일</span><span>월</span><span>화</span><span>수</span><span>목</span><span>금</span><span>토</span>
          </div>
          <div class="cbl-compact-calendar-days" data-cbl-cal-days></div>
        </div>

        <div class="cbl-compact-calendar-list">
          <div class="cbl-compact-calendar-list-title">이벤트</div>
          <div class="cbl-compact-calendar-events" data-cbl-cal-events>
            <p class="cbl-compact-calendar-empty">등록된 일정이 없습니다.</p>
          </div>
        </div>
      </div>
    `;

    if (document.body.classList.contains('staff') || document.querySelector('body[data-is-staff="true"]')) {
      const adminLink = document.createElement('a');
      adminLink.className = 'cbl-compact-calendar-admin-link';
      adminLink.href = '/admin/core/calendarevent/add/';
      adminLink.textContent = '+ 일정 등록';
      card.appendChild(adminLink);
    }

    return card;
  }

  function wrapForScale(target) {
    const shell = document.createElement('div');
    shell.className = 'cbl-feature-scale-shell';

    const inner = document.createElement('div');
    inner.className = 'cbl-feature-scale-inner';

    target.parentNode.insertBefore(shell, target);
    shell.appendChild(inner);
    inner.appendChild(target);

    return { shell, inner, target };
  }

  function scaleWrap(wrap, scale) {
    const { shell, inner, target } = wrap;
    inner.style.transform = `scale(${scale})`;
    inner.style.width = `${100 / scale}%`;

    requestAnimationFrame(() => {
      const h = target.offsetHeight || target.getBoundingClientRect().height || 0;
      shell.style.height = `${Math.max(40, h * scale)}px`;
    });
  }

  function initCalendar(card) {
    const titleEl = card.querySelector('[data-cbl-cal-title]');
    const daysEl = card.querySelector('[data-cbl-cal-days]');
    const eventsEl = card.querySelector('[data-cbl-cal-events]');
    const prevBtn = card.querySelector('[data-cbl-cal-prev]');
    const nextBtn = card.querySelector('[data-cbl-cal-next]');
    const todayBtn = card.querySelector('[data-cbl-cal-today]');

    const now = new Date();
    let year = now.getFullYear();
    let month = now.getMonth() + 1;
    let selectedDay = null;
    let monthEvents = [];

    function pad(n) {
      return String(n).padStart(2, '0');
    }

    function dateKey(y, m, d) {
      return `${y}-${pad(m)}-${pad(d)}`;
    }

    function eventsForDay(day) {
      return monthEvents.filter(ev => ev.date === dateKey(year, month, day));
    }

    function renderEvents(day) {
      const list = day ? eventsForDay(day) : monthEvents.slice(0, 6);
      if (!list.length) {
        eventsEl.innerHTML = '<p class="cbl-compact-calendar-empty">등록된 일정이 없습니다.</p>';
        return;
      }

      eventsEl.innerHTML = list.map(ev => {
        const time = ev.start_time ? `${ev.start_time}${ev.end_time ? ' - ' + ev.end_time : ''}` : '시간 미정';
        const title = ev.link_url
          ? `<div class="cbl-compact-calendar-event-title"><a href="${ev.link_url}" target="_blank" rel="noopener">${ev.title}</a></div>`
          : `<div class="cbl-compact-calendar-event-title">${ev.title}</div>`;

        return `
          <div class="cbl-compact-calendar-event">
            <div class="cbl-compact-calendar-event-date">${ev.day}일 · ${ev.category || '일정'}</div>
            ${title}
            <div class="cbl-compact-calendar-event-meta">${time}</div>
          </div>
        `;
      }).join('');
    }

    function renderCalendar() {
      titleEl.textContent = `${month}월`;

      const first = new Date(year, month - 1, 1);
      const last = new Date(year, month, 0);
      const firstDay = first.getDay();
      const lastDate = last.getDate();
      const todayKey = dateKey(now.getFullYear(), now.getMonth() + 1, now.getDate());

      daysEl.innerHTML = '';

      for (let i = 0; i < firstDay; i++) {
        const blank = document.createElement('button');
        blank.type = 'button';
        blank.className = 'cbl-compact-calendar-day is-muted';
        blank.disabled = true;
        daysEl.appendChild(blank);
      }

      for (let day = 1; day <= lastDate; day++) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'cbl-compact-calendar-day';
        btn.textContent = day;

        const key = dateKey(year, month, day);
        if (key === todayKey) btn.classList.add('is-today');
        if (eventsForDay(day).length) btn.classList.add('has-event');
        if (selectedDay === day) btn.classList.add('is-selected');

        btn.addEventListener('click', function () {
          selectedDay = selectedDay === day ? null : day;
          renderCalendar();
          renderEvents(selectedDay);
        });

        daysEl.appendChild(btn);
      }

      renderEvents(selectedDay);
    }

    async function loadEvents() {
      selectedDay = null;
      eventsEl.innerHTML = '<p class="cbl-compact-calendar-empty">일정을 불러오는 중입니다.</p>';

      try {
        const res = await fetch(`/api/calendar-events/?year=${year}&month=${month}`, {
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        const data = await res.json();
        monthEvents = Array.isArray(data.events) ? data.events : [];
      } catch (e) {
        monthEvents = [];
      }

      renderCalendar();
    }

    prevBtn.addEventListener('click', function () {
      month -= 1;
      if (month < 1) {
        month = 12;
        year -= 1;
      }
      loadEvents();
    });

    nextBtn.addEventListener('click', function () {
      month += 1;
      if (month > 12) {
        month = 1;
        year += 1;
      }
      loadEvents();
    });

    todayBtn.addEventListener('click', function () {
      year = now.getFullYear();
      month = now.getMonth() + 1;
      selectedDay = now.getDate();
      loadEvents();
    });

    loadEvents();
  }

  function init() {
    if (document.querySelector('.cbl-compact-feature-row')) return;

    const takeoffCard = findTargetCard('수량산출 자동화', '자세히 보기');
    const programCard = findTargetCard('ChickenBananaLab 프로그램', '프로그램 보기')
      || findTargetCard('ChickenBanana 프로그램', '프로그램 보기');

    if (!takeoffCard || !programCard || takeoffCard === programCard) {
      console.warn('CBL compact row: 기존 카드 2개를 찾지 못했습니다.');
      return;
    }

    const row = document.createElement('section');
    row.className = 'cbl-compact-feature-row';

    takeoffCard.parentNode.insertBefore(row, takeoffCard);

    const takeoffWrap = wrapForScale(takeoffCard);
    const programWrap = wrapForScale(programCard);

    row.appendChild(takeoffWrap.shell);
    row.appendChild(programWrap.shell);

    const calendarCard = createCalendarCard();
    row.appendChild(calendarCard);

    function applyScale() {
      const scale = window.innerWidth <= 1080 ? 1 : 0.7;
      scaleWrap(takeoffWrap, scale);
      scaleWrap(programWrap, scale);
    }

    applyScale();
    window.addEventListener('resize', applyScale);

    initCalendar(calendarCard);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
