/* AI 수량산출: one card at a time instead of a chat log.
   The workflow script writes through window.cblQuantityDeck.create(flowEl). A card collects
   everything said and shown until the user acts (an answer, or a click on a flow button);
   whatever comes after that opens the next card, which slides in from the right. */
(function () {
  "use strict";

  var ART = {
    building: '<path d="M64 150V62l34-16 34 16v88"/><path d="M98 46v104"/><path d="M76 74h10M110 74h10M76 92h10M110 92h10M76 110h10M110 110h10M76 128h10M110 128h10"/><path d="M52 150h92"/>',
    frame: '<path d="M58 58h80M58 98h80M58 138h80"/><path d="M66 50v96M98 50v96M130 50v96"/><path d="M66 58l32 40M98 58l32 40" stroke-dasharray="3 5"/>',
    ground: '<path d="M40 82h44l14 52h0l14-52h44"/><path d="M84 82l14 52 14-52" stroke-dasharray="3 5"/><path d="M40 82v-16M156 82v-16"/><path d="M52 98l-8 8M60 110l-8 8M144 98l8 8M136 110l8 8"/>',
    cad: '<rect x="56" y="54" width="84" height="88" rx="6"/><path d="M68 120l18-22 14 14 14-20 14 28"/><path d="M68 70h36M68 80h22"/><circle cx="124" cy="74" r="7"/>',
    pdf: '<path d="M70 50h44l20 20v76H70z"/><path d="M114 50v20h20"/><path d="M62 60v94h58" stroke-dasharray="3 5"/><path d="M82 92h36M82 104h36M82 116h24"/>',
    check: '<rect x="58" y="50" width="70" height="92" rx="6"/><path d="M70 74l6 6 10-12M70 98l6 6 10-12M70 122l6 6 10-12"/><path d="M94 76h22M94 100h22M94 124h14"/><circle cx="130" cy="126" r="16"/><path d="M142 138l12 12"/>',
    ai: '<rect x="66" y="66" width="64" height="64" rx="10"/><path d="M84 66V54M98 66V54M112 66V54M84 142v-12M98 142v-12M112 142v-12M66 84H54M66 98H54M66 112H54M142 84h-12M142 98h-12M142 112h-12"/><path d="M84 112l8-28h12l8 28M88 102h20"/>',
    review: '<rect x="54" y="56" width="88" height="84" rx="4"/><path d="M54 84h88M54 112h88M84 56v84M114 56v84" stroke-dasharray="3 5"/><path d="M62 64h16v14H62zM92 92h16v14H92z"/>',
    sheet: '<rect x="58" y="48" width="80" height="100" rx="6"/><path d="M58 72h80M58 92h80M58 112h80M58 132h80M84 48v100M110 48v100"/>',
    mass: '<path d="M98 46l44 22v58l-44 22-44-22V68z"/><path d="M54 68l44 22 44-22M98 90v58"/><path d="M76 57l44 22M120 57l-44 22" stroke-dasharray="3 5"/>'
  };

  function artSvg(name) {
    var glyph = ART[name] || ART.ai;
    return '<svg viewBox="0 0 196 196" aria-hidden="true" focusable="false">' +
      '<g class="qd-art-frame"><path d="M28 54V30h24M144 30h24v24M168 142v24h-24M52 166H28v-24"/>' +
      '<circle cx="98" cy="98" r="66"/><path d="M98 14v28M98 154v28M14 98h28M154 98h28"/></g>' +
      '<g class="qd-art-glyph">' + glyph + '</g></svg>';
  }

  function plain(html) {
    var box = document.createElement("div");
    box.innerHTML = String(html || "").replace(/<br\s*\/?>/gi, " ");
    return (box.textContent || "").replace(/\s+/g, " ").trim();
  }

  function create(root, options) {
    options = options || {};
    var stepNames = options.steps || [];
    var current = null;
    var nextMeta = null;
    var stepNo = 1;
    var perStep = {};

    root.classList.add("qd-deck");
    root.setAttribute("aria-live", "polite");

    var dotsEl = document.createElement("div");
    dotsEl.className = "qd-dots";
    dotsEl.setAttribute("aria-hidden", "true");
    root.parentNode.insertBefore(dotsEl, root.nextSibling);

    function renderDots(meta) {
      var total = meta.total || 0;
      var index = meta.index || 0;
      if (!total || total < 2) { dotsEl.innerHTML = ""; return; }
      var html = "";
      for (var i = 1; i <= total; i++) {
        html += '<span class="' + (i === index ? "is-on" : (i < index ? "is-past" : "")) + '"></span>';
      }
      dotsEl.innerHTML = html;
    }

    function defaultMeta() {
      var count = (perStep[stepNo] || 0) + 1;
      var labels = ["", "PROJECT", "DRAWINGS", "AI READING", "TAKEOFF"];
      var arts = ["", "building", "cad", "ai", "sheet"];
      return { label: options.labels ? options.labels[stepNo] : labels[stepNo] || "AI",
               art: arts[stepNo] || "ai", eyebrow: String(count).padStart(2, "0") };
    }

    function retire(card) {
      if (!card || !card.el.parentNode) return;
      card.el.classList.remove("is-current");
      card.el.classList.add("is-leaving");
      card.el.setAttribute("aria-hidden", "true");
      window.setTimeout(function () { if (card.el.parentNode) card.el.parentNode.removeChild(card.el); }, 520);
    }

    function openCard(meta) {
      meta = meta || nextMeta || defaultMeta();
      nextMeta = null;
      perStep[stepNo] = (perStep[stepNo] || 0) + 1;
      var el = document.createElement("section");
      el.className = "qd-card is-entering" + (meta.wide ? " is-wide" : "") + (meta.kind ? " is-" + meta.kind : "");
      el.innerHTML =
        '<div class="qd-card-main">' +
          '<div class="qd-eyebrow"><span class="qd-orb" aria-hidden="true"></span>' +
            '<span class="qd-num">' + (meta.eyebrow != null ? meta.eyebrow : String(perStep[stepNo]).padStart(2, "0")) + '</span>' +
            '<span class="qd-label">' + (meta.label || "") + '</span></div>' +
          '<h2 class="qd-title" hidden></h2>' +
          '<div class="qd-body"></div>' +
        '</div>' +
        (meta.art === false ? "" : '<div class="qd-art">' + artSvg(meta.art) + '</div>');
      var card = { el: el, main: el.querySelector(".qd-card-main"), title: el.querySelector(".qd-title"),
                   body: el.querySelector(".qd-body"), answered: false, meta: meta, opened: Date.now() };
      var previous = current;
      current = card;
      root.appendChild(el);
      retire(previous);
      renderDots(meta);
      // let the entering state paint first so the slide runs; the timer covers a page whose
      // frames are paused (background tab), so a card is never left invisible
      var shown = false;
      function show() {
        if (shown) return;
        shown = true;
        el.classList.remove("is-entering");
        el.classList.add("is-current");
      }
      window.requestAnimationFrame(function () { window.requestAnimationFrame(show); });
      window.setTimeout(show, 90);
      return card;
    }

    function ensure() {
      if (!current || current.answered) openCard();
      else nextMeta = null; // still writing on the open card: a pending "next card" label no longer applies
      return current;
    }

    function scrollLatest(node) {
      if (!current) return;
      // what a card shows as it opens stays at its top; only later additions scroll into view
      if (Date.now() - current.opened < 1500) return;
      var main = current.main;
      window.requestAnimationFrame(function () {
        if (node && node.getBoundingClientRect && main.contains(node)) {
          var a = main.getBoundingClientRect();
          var b = node.getBoundingClientRect();
          if (b.bottom > a.bottom - 8) main.scrollTop += (b.bottom - a.bottom) + 24;
        }
      });
    }

    // A click on a flow button (not on upload widgets marked data-qd-keep) closes the card:
    // what the flow shows next belongs on a new card.
    root.addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest("button") : null;
      if (!button || !current || !current.el.contains(button)) return;
      if (button.closest("[data-qd-keep]")) return;
      current.answered = true;
    }, true);

    var api = {
      next: function (meta) { nextMeta = meta || null; },
      step: function (n) { if (n && n !== stepNo) stepNo = n; },
      card: function () { return ensure(); },
      say: function (html) {
        var card = ensure();
        if (card.title.hidden) {
          card.title.innerHTML = html;
          card.title.hidden = false;
          card.opened = Date.now(); // a card that waited on a spinner really opens with its title
          card.main.scrollTop = 0;
          var len = plain(html).length;
          card.title.classList.toggle("is-long", len > 46);
          card.title.classList.toggle("is-very-long", len > 110);
        } else {
          var p = document.createElement("p");
          p.className = "qd-line";
          p.innerHTML = html;
          card.body.appendChild(p);
          scrollLatest(p);
        }
        return card;
      },
      line: function (html, cls) {
        var card = ensure();
        var p = document.createElement("p");
        p.className = "qd-sub" + (cls ? " " + cls : "");
        p.innerHTML = html;
        card.body.appendChild(p);
        return p;
      },
      append: function (node) {
        var card = ensure();
        card.body.appendChild(node);
        scrollLatest(node);
        return node;
      },
      answer: function (html) {
        if (!current) return;
        if (html) {
          var chip = document.createElement("div");
          chip.className = "qd-answer";
          chip.innerHTML = '<span aria-hidden="true">✓</span>' + html;
          current.body.appendChild(chip);
          scrollLatest(chip);
        }
        current.answered = true;
      },
      close: function () { if (current) current.answered = true; },
      scrollLatest: function (node) { scrollLatest(node || (current && current.body.lastElementChild)); },
      // "프로젝트 확인이 끝났습니다. 다음 …" between two steps: a full card with the step number,
      // spoken; resolves when the voice is done (or after a short pause without voice).
      transition: function (n, title, text) {
        stepNo = n;
        nextMeta = { kind: "transition", art: false, label: "STEP " + String(n).padStart(2, "0"), eyebrow: "" };
        var card = openCard();
        card.title.hidden = false;
        card.title.innerHTML = title;
        var p = document.createElement("p");
        p.className = "qd-transition-text";
        p.innerHTML = text;
        card.body.appendChild(p);
        var bar = document.createElement("div");
        bar.className = "qd-transition-bar";
        bar.innerHTML = "<span></span>";
        card.body.appendChild(bar);
        renderDots({});
        card.answered = true;
        perStep[n] = 0;
        var stage = window.cblQuantityStage;
        return new Promise(function (resolve) {
          var finished = false;
          function done() {
            if (finished) return;
            finished = true;
            window.setTimeout(resolve, 350);
          }
          if (stage && stage.say) stage.say(plain(text), done);
          else window.setTimeout(done, 2200);
          window.setTimeout(done, 9000);
        });
      },
      stepName: function (n) { return stepNames[n - 1] || ""; },
      artSvg: artSvg
    };
    return api;
  }

  window.cblQuantityDeck = { create: create, artSvg: artSvg };
})();
