/* AI 수량산출 진행 화면: 진행 단계, 안내 원, 자막, 브라우저 음성(Web Speech, 무료).
   The workflow script calls window.cblQuantityStage.{intro, botSaid, userSaid, step, stop}. */
(function () {
  "use strict";

  var modal = document.getElementById("cblQuantityMessengerModal");
  if (!modal) return;
  var screen = modal.querySelector(".qty-screen");
  var header = modal.querySelector(".qty-header");
  var body = modal.querySelector(".qty-body");
  if (!screen || !header || !body) return;

  var STEPS = ["프로젝트 확인", "도면 업로드", "AI 판독·검토", "수량산출·엑셀"];
  var GREETING = "안녕하세요. 지금부터 저와 함께 AI 수량산출을 진행해볼게요.";
  var STORAGE_KEY = "cblQtyVoice";
  var SAY = [[/\bCAD\b/g, "캐드"], [/\bPDF\b/g, "피디에프"], [/\bZIP\b/gi, "집"], [/\bDWG\b/g, "디더블유지"],
             [/\bDXF\b/g, "디엑스에프"], [/\bExcel\b/gi, "엑셀"], [/@/g, " 간격 "]];
  var synth = "speechSynthesis" in window ? window.speechSynthesis : null;
  var voice = null;
  var pending = 0;
  var currentStep = 0;

  modal.classList.add("qty-stage");

  function readVoicePref() {
    try { return window.localStorage.getItem(STORAGE_KEY) !== "off"; } catch (e) { return true; }
  }
  function writeVoicePref(on) {
    try { window.localStorage.setItem(STORAGE_KEY, on ? "on" : "off"); } catch (e) { /* private mode */ }
  }
  var voiceOn = !!synth && readVoicePref();

  function orbHtml() { return '<div class="qty-stage-orb" aria-hidden="true"><span></span></div>'; }

  var head = document.createElement("div");
  head.className = "qty-stage-head";
  head.innerHTML =
    '<ol class="qty-stage-steps" aria-label="진행 단계">' +
      STEPS.map(function (name, i) { return '<li data-step="' + (i + 1) + '"><b>' + (i + 1) + '</b>' + name + '</li>'; }).join("") +
    '</ol>' +
    '<div class="qty-stage-talk">' + orbHtml() +
      '<p class="qty-stage-caption" aria-live="polite"></p>' +
    '</div>';
  screen.insertBefore(head, body);
  var caption = head.querySelector(".qty-stage-caption");
  var orbs = [head.querySelector(".qty-stage-orb")];

  var intro = document.createElement("div");
  intro.className = "qty-stage-intro";
  intro.hidden = true;
  intro.innerHTML =
    '<p class="qty-stage-intro-text">안녕하세요.<br>지금부터 저와 함께 AI 수량산출을 진행해볼게요.</p>' +
    orbHtml() +
    '<button type="button" class="qty-stage-start">시작하기</button>' +
    '<p class="qty-stage-intro-hint"></p>';
  screen.appendChild(intro);
  orbs.push(intro.querySelector(".qty-stage-orb"));
  var startButton = intro.querySelector(".qty-stage-start");
  var hint = intro.querySelector(".qty-stage-intro-hint");

  var toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "qty-voice-toggle";
  var closeButton = header.querySelector(".qty-close");
  header.insertBefore(toggle, closeButton || null);
  if (!synth) toggle.hidden = true;

  function renderToggle() {
    toggle.setAttribute("aria-pressed", voiceOn ? "true" : "false");
    toggle.innerHTML = (voiceOn
      ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4z" fill="currentColor"/><path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4z" fill="currentColor"/><path d="M17 9l5 6M22 9l-5 6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>') +
      '<span>' + (voiceOn ? "음성 안내 켜짐" : "음성 안내 꺼짐") + '</span>';
    toggle.setAttribute("aria-label", voiceOn ? "음성 안내 끄기" : "음성 안내 켜기");
    hint.textContent = synth
      ? (voiceOn ? "음성 안내가 함께 나와요. 오른쪽 위 버튼으로 끌 수 있어요." : "음성 안내가 꺼져 있어요. 자막으로 안내해 드려요.")
      : "이 브라우저는 음성 안내를 지원하지 않아 자막으로 안내해 드려요.";
  }

  function setSpeaking(on) {
    orbs.forEach(function (orb) { orb.classList.toggle("is-speaking", on); });
  }

  function pulse() {
    setSpeaking(true);
    window.setTimeout(function () { if (!pending) setSpeaking(false); }, 1200);
  }

  function pickVoice() {
    if (!synth) return;
    var korean = synth.getVoices().filter(function (v) { return /^ko([-_]|$)/i.test(v.lang || ""); });
    voice = korean.filter(function (v) { return /Yuna|유나|Sora|Google|Heami|SunHi|Neural|Natural/i.test(v.name); })[0] || korean[0] || null;
  }
  if (synth) {
    pickVoice();
    if (typeof synth.addEventListener === "function") synth.addEventListener("voiceschanged", pickVoice);
    else synth.onvoiceschanged = pickVoice;
  }

  function plain(html) {
    var box = document.createElement("div");
    box.innerHTML = String(html || "").replace(/<br\s*\/?>/gi, " ");
    return (box.textContent || "").replace(/\s+/g, " ").trim();
  }

  function spoken(text) {
    var out = text.length > 170 ? (text.slice(0, 170).replace(/[^.!?。]*$/, "") || text.slice(0, 170)) : text;
    SAY.forEach(function (pair) { out = out.replace(pair[0], pair[1]); });
    return out;
  }

  function speak(text, onDone) {
    if (!voiceOn || !synth || !text) {
      pulse();
      if (onDone) window.setTimeout(onDone, text ? 1400 : 0);
      return;
    }
    var utterance = new SpeechSynthesisUtterance(spoken(text));
    utterance.lang = "ko-KR";
    if (voice) utterance.voice = voice;
    utterance.rate = 1.05;
    var finished = false;
    function done() {
      if (finished) return;
      finished = true;
      pending = Math.max(0, pending - 1);
      if (!pending) setSpeaking(false);
      if (onDone) onDone();
    }
    utterance.onstart = function () { setSpeaking(true); };
    utterance.onend = done;
    utterance.onerror = done;
    pending += 1;
    synth.speak(utterance);
    // Some browsers never fire onend for a cancelled or blocked utterance.
    window.setTimeout(done, Math.min(20000, 2500 + text.length * 120));
  }

  function stop() {
    pending = 0;
    if (synth) synth.cancel();
    setSpeaking(false);
  }

  function step(n) {
    currentStep = Math.max(currentStep, n);
    head.querySelectorAll(".qty-stage-steps li").forEach(function (li) {
      var k = Number(li.getAttribute("data-step"));
      li.classList.toggle("is-done", k < currentStep);
      li.classList.toggle("is-current", k === currentStep);
      if (k === currentStep) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
  }

  function botSaid(html) {
    var text = plain(html);
    if (!text) return;
    caption.textContent = text;
    if (intro.hidden) speak(text);
  }

  var introStart = null;
  function dismissIntro() {
    if (intro.hidden) return;
    intro.classList.add("is-leaving");
    window.setTimeout(function () { intro.hidden = true; intro.classList.remove("is-leaving"); }, 450);
    var go = introStart;
    introStart = null;
    if (go) go();
  }

  function introduce(startFlow) {
    introStart = startFlow;
    caption.textContent = GREETING;
    intro.style.top = header.offsetHeight + "px";
    intro.hidden = false;
    startButton.disabled = false;
    renderToggle();
    window.setTimeout(function () { startButton.focus(); }, 60);
  }

  startButton.addEventListener("click", function () {
    startButton.disabled = true;
    speak(GREETING, function () { window.setTimeout(dismissIntro, 250); });
  });

  toggle.addEventListener("click", function () {
    voiceOn = !voiceOn;
    writeVoicePref(voiceOn);
    if (!voiceOn) stop();
    renderToggle();
  });

  var resume = document.getElementById("qty-resume");
  if (resume) resume.addEventListener("click", function () { stop(); dismissIntro(); });
  modal.addEventListener("cbl:quantity-resume", function (event) {
    var status = event.detail && event.detail.status;
    step(status === "completed" || status === "partial" ? 4 : 3);
  });

  renderToggle();
  window.cblQuantityStage = {
    intro: introduce,
    botSaid: botSaid,
    userSaid: function () { if (synth) synth.cancel(); pending = 0; setSpeaking(false); },
    step: step,
    stop: stop,
    dismissIntro: dismissIntro
  };
})();
