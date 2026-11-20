/* 2D·3D drawing review of a free CAD takeoff (core/quantity_review.py makes the data).

   window.cblQuantityReview.mount(root, data, options)
     data: {title, model, buildings: [{name, storeys: [...], totals, foundation}], pictures: [{svg, sheet}],
            assumed, editable, feedback}
     options: {save_url, floor_job_id, csrf} on the web (fixes are saved and the takeoff runs again),
              {} for the offline file (fixes are downloaded as a feedback file for the folder command).

   The 3D (window.cblQuantityMass3D) turns freely; the plan of the chosen storey, its section (storey
   heights) and its quantities sit beside it. Storey heights, the values the takeoff assumed, the
   foundation thickness and the members fixed in the 3D are the feedback. */
(function () {
  "use strict";

  var KIND = {
    basement: { color: "#9a7b54", name: "지하" },
    ground: { color: "#10b981", name: "1층" },
    typical: { color: "#60a5fa", name: "기준층" },
    typicalB: { color: "#2563eb", name: "기준층" },
    roof: { color: "#9ca3af", name: "지붕·옥탑" }
  };
  var MEMBER = [["#facc15", "벽·기둥"], ["#e2e8f0", "슬래브"], ["#fb923c", "보"], ["#94a3b8", "지하주차장"]];
  var PLAN_KEY = [["frame", "#facc15", "콘크리트 벽·기둥"], ["beam", "#fb923c", "보"], ["masonry", "#4ade80", "조적"],
                  ["alc", "#bef264", "ALC"], ["dry", "#7dd3fc", "건식벽"], ["opening", "#c4b5fd", "창·문"], ["other", "#94a3b8", "기타"]];

  function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmt(value, digits) {
    if (value == null || isNaN(value)) return "-";
    return Number(value).toLocaleString("ko-KR", { minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0 });
  }
  function storeyColor(s) {
    if (s.kind === "typical") return s.index % 2 ? KIND.typicalB.color : KIND.typical.color;
    return (KIND[s.kind] || KIND.typical).color;
  }
  function el(tag, cls, html) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  }

  function mount(root, data, options) {
    options = options || {};
    var web = !!options.save_url;
    var buildings = data.buildings || [];
    var pictures = data.pictures || [];
    var feedback = JSON.parse(JSON.stringify(data.feedback || {}));
    ["storey_heights", "overrides", "foundations"].forEach(function (k) { if (!feedback[k] || typeof feedback[k] !== "object") feedback[k] = {}; });
    if (!Array.isArray(feedback.edits)) feedback.edits = [];
    var savedFeedback = JSON.stringify(feedback);
    var pick = { building: buildings.length === 1 ? buildings[0].name : null, storey: null, tab: "plan" };
    var viewer = null;

    root.innerHTML =
      '<header class="rv-head">' +
        '<div class="rv-title"><b>2D·3D 도면 검토</b><span>' + esc(data.title || "") + '</span></div>' +
        '<nav class="rv-buildings" data-buildings></nav>' +
        '<div class="rv-totals" data-totals></div>' +
      '</header>' +
      '<main class="rv-main">' +
        '<aside class="rv-storeys"><div class="rv-panel-head"><b>층 구성</b><span data-storey-hint></span></div>' +
          '<div class="rv-storey-list" data-storeys></div></aside>' +
        '<section class="rv-3d"><div class="rv-3d-stage" data-stage></div>' +
          '<div class="rv-3d-foot"><div class="rv-legend" data-legend3d></div>' +
          '<div class="rv-quick">' +
            '<button type="button" data-v="iso">3D</button><button type="button" data-v="top">평면</button>' +
            '<button type="button" data-v="front">입면(정면)</button><button type="button" data-v="right">입면(측면)</button>' +
            '<button type="button" data-section>단면</button>' +
          '</div></div></section>' +
        '<section class="rv-2d">' +
          '<div class="rv-tabs" data-tabs>' +
            '<button type="button" data-tab="plan" class="is-on">구조 평면</button>' +
            '<button type="button" data-tab="arch">건축 평면</button>' +
            '<button type="button" data-tab="section">단면 (층고)</button>' +
          '</div>' +
          '<div class="rv-2d-stage qd-floor-canvas-host" data-plan></div>' +
          '<div class="rv-plan-key" data-plankey></div>' +
          '<div class="rv-card" data-card></div>' +
        '</section>' +
      '</main>' +
      '<section class="rv-feedback" data-feedback></section>';

    var $ = function (sel) { return root.querySelector(sel); };

    // ---------- buildings and totals ----------
    function building() { return buildings.filter(function (b) { return b.name === pick.building; })[0] || null; }
    function storey() {
      var b = building();
      return b && pick.storey != null ? b.storeys[pick.storey] : null;
    }
    function renderBuildings() {
      var nav = $("[data-buildings]");
      nav.innerHTML = (buildings.length > 1 ? '<button type="button" data-b=""' + (!pick.building ? ' class="is-on"' : '') + '>전체 현장</button>' : '') +
        buildings.map(function (b) {
          return '<button type="button" data-b="' + esc(b.name) + '"' + (pick.building === b.name ? ' class="is-on"' : '') + '>' + esc(b.name) + '</button>';
        }).join("");
    }
    function renderTotals() {
      var list = pick.building ? [building()] : buildings;
      var sum = { concrete: 0, formwork: 0, rebar_t: 0, rough: 0 };
      list.forEach(function (b) {
        sum.concrete += b.totals.concrete || 0; sum.formwork += b.totals.formwork || 0;
        sum.rebar_t += b.totals.rebar_t || 0; sum.rough += b.totals.approximate_concrete || 0;
      });
      $("[data-totals]").innerHTML =
        '<span><i>콘크리트</i><b>' + fmt(sum.concrete) + '</b>㎥</span>' +
        '<span><i>거푸집</i><b>' + fmt(sum.formwork) + '</b>㎡</span>' +
        '<span><i>철근</i><b>' + fmt(sum.rebar_t, 1) + '</b>t</span>' +
        (sum.rough ? '<span class="rv-rough" title="지하 등 근사로 본 층은 합계에서 뺐어요"><i>근사(합계 제외)</i><b>' + fmt(sum.rough) + '</b>㎥</span>' : '');
    }

    // ---------- storey list (top floor first) ----------
    function heightOf(b, s) {
      var given = (feedback.storey_heights[b.name] || {})[s.key];
      return given != null ? Number(given) : drawingHeight(s);
    }
    function elevOf(b, s) {
      // the storeys below moved by the heights fed back
      var shift = 0;
      for (var i = 0; i < s.index; i++) shift += heightOf(b, b.storeys[i]) - b.storeys[i].height;
      return s.elev + shift;
    }
    function drawingHeight(s) { return s.height_from === "person" && s.height_before != null ? s.height_before : s.height; }
    function renderStoreys() {
      var box = $("[data-storeys]");
      var list = pick.building ? [building()] : buildings;
      $("[data-storey-hint]").textContent = pick.building ? "층고를 고치면 3D와 수량에 반영돼요" : "동을 고르면 층고를 고칠 수 있어요";
      box.innerHTML = list.map(function (b) {
        var rows = b.storeys.slice().reverse().map(function (s) {
          var h = heightOf(b, s), changed = Math.abs(h - drawingHeight(s)) > 1e-6;
          var on = pick.building === b.name && pick.storey === s.index;
          var c = s.concrete || {};
          return '<div class="rv-storey' + (on ? " is-on" : "") + (s.approximate ? " is-rough" : "") + '" data-pick="' + esc(b.name) + '|' + s.index + '">' +
            '<i class="rv-sw" style="background:' + storeyColor(s) + '"></i>' +
            '<b class="rv-st-name">' + esc(s.label) + '</b>' +
            '<span class="rv-st-el">EL ' + fmt(elevOf(b, s), 2) + '</span>' +
            (pick.building ?
              '<label class="rv-st-h' + (changed ? " is-changed" : "") + '" title="도면 층고 ' + fmt(drawingHeight(s), 2) + 'm">' +
                '<input type="number" step="0.01" min="1.5" max="12" value="' + h.toFixed(2) + '" data-height="' + esc(b.name) + '|' + esc(s.key) + '">m</label>'
              : '<span class="rv-st-h">' + fmt(h, 2) + 'm</span>') +
            '<span class="rv-st-q">' + fmt(c.total, 0) + '㎥' + (s.rebar_kg ? ' · ' + fmt(s.rebar_kg / 1000, 1) + 't' : '') + (s.approximate ? ' <em>근사</em>' : '') + '</span>' +
          '</div>';
        }).join("");
        var ft = b.foundation ? '<div class="rv-storey rv-ft"><i class="rv-sw" style="background:#57534e"></i><b class="rv-st-name">기초</b>' +
          '<span class="rv-st-el"></span><span class="rv-st-h">' + fmt(b.foundation.height, 2) + 'm</span><span class="rv-st-q">' + fmt(b.foundation.concrete, 0) + '㎥</span></div>' : '';
        return (list.length > 1 ? '<div class="rv-st-building" data-pick="' + esc(b.name) + '|">' + esc(b.name) + '</div>' : '') + rows + ft;
      }).join("");
    }

    // ---------- 3D ----------
    function liveModel() {
      // storey heights fed back move the storeys above, as the takeoff will
      var model = JSON.parse(JSON.stringify(data.model || {}));
      (model.buildings || []).forEach(function (b) {
        var given = feedback.storey_heights[b.name] || {}, shift = 0;
        var review = buildings.filter(function (r) { return r.name === b.name; })[0];
        (b.levels || []).forEach(function (level, i) {
          if (shift && level.elev != null) level.elev = level.elev + shift;
          var h = given[level.key];
          if (h == null && review && review.storeys[i] && review.storeys[i].height_from === "person") h = review.storeys[i].height_before;
          if (h == null) return;
          shift += Number(h) - Number(level.height || 0);
          level.height = Number(h);
        });
      });
      return model;
    }
    function planSvgFor(name, level) {
      var b = buildings.filter(function (x) { return x.name === name; })[0];
      var s = b && b.storeys.filter(function (x) { return x.key === level.key; })[0];
      return s && s.plan != null && pictures[s.plan] ? pictures[s.plan].svg : null;
    }
    function startViewer() {
      var stage = $("[data-stage]");
      if (!window.THREE || !window.cblQuantityMass3D || !(data.model && data.model.buildings && data.model.buildings.length)) {
        stage.innerHTML = '<p class="rv-empty">3D로 그릴 구조 평면이 없어요.</p>';
        return;
      }
      viewer = window.cblQuantityMass3D.mount(stage, liveModel(), {
        colorBy: "storey", planSvg: planSvgFor, edits: feedback.edits.slice(),
        onEdit: function (edits) { feedback.edits = (edits || []).slice(); renderFeedback(); }
      });
      if (viewer.setColorBy) viewer.setColorBy("storey");
      window.addEventListener("resize", function () { if (viewer && viewer.resize) viewer.resize(); });
    }
    function syncViewer() {
      if (!viewer) return;
      var state = viewer.getState ? viewer.getState() : {};
      if ((state.building || null) !== (pick.building || null)) viewer.setBuilding(pick.building);
      viewer.setLevel(pick.building && pick.storey != null ? pick.storey : null);
    }
    function render3dLegend() {
      var state = viewer && viewer.getState ? viewer.getState() : { colorBy: "storey" };
      var items = state.colorBy === "member" ? MEMBER :
        [[KIND.basement.color, "지하"], [KIND.ground.color, "1층"], [KIND.typical.color, "기준층"], [KIND.typicalB.color, "기준층(번갈아)"],
         [KIND.roof.color, "지붕·옥탑"], ["#e2e8f0", "슬래브"]];
      $("[data-legend3d]").innerHTML = items.map(function (l) { return '<span><i style="background:' + l[0] + '"></i>' + l[1] + '</span>'; }).join("") +
        '<span class="rv-help">드래그: 회전 · 우클릭/Shift: 이동 · 휠: 확대</span>';
    }

    // ---------- 2D: plan with zoom, section of the storeys ----------
    var planView = null;
    function setPlanSvg(svg) {
      var host = $("[data-plan]");
      host.innerHTML = "";
      planView = null;
      if (!svg) { host.innerHTML = '<p class="rv-empty">' + (storey() ? "이 층의 평면 그림이 없어요." : "왼쪽에서 층을 고르면 그 층 평면이 보여요.") + '</p>'; return; }
      var canvas = el("div", "qd-floor-canvas rv-plan-canvas", svg);
      host.appendChild(canvas);
      var node = canvas.querySelector("svg");
      if (!node) return;
      var vb = fitBox(node) || (node.getAttribute("viewBox") || "0 0 100 100").split(/[\s,]+/).map(Number);
      node.setAttribute("viewBox", vb.join(" "));
      planView = { node: node, base: vb.slice(), vb: vb.slice() };
      host.appendChild(el("div", "rv-plan-help", "휠: 확대 · 드래그: 이동 · 더블클릭: 처음대로"));
    }
    // the plan opens on the drawn members, not the whole sheet: the points of the member lines,
    // their 3rd to 97th percentile, so a small key plan in a corner does not count
    function fitBox(node) {
      // member lines as segments; each adds its length to the grid cell of its middle. The connected
      // block of cells with the most line length is the plan (a key plan is drawn small, a title
      // block has few member lines)
      var segs = [];
      node.querySelectorAll("path.fv-frame, path.fv-beam, path.fv-masonry, path.fv-alc, path.fv-dry").forEach(function (p) {
        var tokens = (p.getAttribute("d") || "").match(/[ML]|-?\d+(?:\.\d+)?/g) || [], last = null, i = 0;
        while (i < tokens.length && segs.length < 100000) {
          var op = tokens[i];
          if (op !== "M" && op !== "L") { i++; continue; }
          var x = +tokens[i + 1], y = +tokens[i + 2];
          i += 3;
          if (op === "L" && last) segs.push([last[0], last[1], x, y]);
          last = [x, y];
        }
      });
      if (segs.length < 5) return null;
      var N = 40, lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
      segs.forEach(function (sg) {
        lo[0] = Math.min(lo[0], sg[0], sg[2]); hi[0] = Math.max(hi[0], sg[0], sg[2]);
        lo[1] = Math.min(lo[1], sg[1], sg[3]); hi[1] = Math.max(hi[1], sg[1], sg[3]);
      });
      var cw = Math.max((hi[0] - lo[0]) / N, 1e-6), ch = Math.max((hi[1] - lo[1]) / N, 1e-6), grid = {};
      function cell(x, y) { return Math.min(N - 1, Math.floor((x - lo[0]) / cw)) + "," + Math.min(N - 1, Math.floor((y - lo[1]) / ch)); }
      segs.forEach(function (sg) {
        var len = Math.hypot(sg[2] - sg[0], sg[3] - sg[1]), steps = Math.max(1, Math.ceil(len / Math.min(cw, ch)));
        for (var t = 0; t < steps; t++) {  // a long line counts in every cell it crosses
          var f = (t + 0.5) / steps, key = cell(sg[0] + (sg[2] - sg[0]) * f, sg[1] + (sg[3] - sg[1]) * f);
          grid[key] = (grid[key] || 0) + len / steps;
        }
      });
      var seen = {}, best = null;
      Object.keys(grid).forEach(function (start) {
        if (seen[start]) return;
        var stack = [start], cells = [], weight = 0;
        seen[start] = true;
        while (stack.length) {
          var c = stack.pop(), p = c.split(",").map(Number);
          cells.push(p); weight += grid[c];
          for (var dx = -1; dx <= 1; dx++) for (var dy = -1; dy <= 1; dy++) {
            var n = (p[0] + dx) + "," + (p[1] + dy);
            if (grid[n] && !seen[n]) { seen[n] = true; stack.push(n); }
          }
        }
        if (!best || weight > best.weight) best = { cells: cells, weight: weight };
      });
      var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      best.cells.forEach(function (c) { x0 = Math.min(x0, c[0]); x1 = Math.max(x1, c[0]); y0 = Math.min(y0, c[1]); y1 = Math.max(y1, c[1]); });
      x0 = lo[0] + x0 * cw; x1 = lo[0] + (x1 + 1) * cw; y0 = lo[1] + y0 * ch; y1 = lo[1] + (y1 + 1) * ch;
      var w = Math.max(x1 - x0, 10), h = Math.max(y1 - y0, 10), pad = 0.05;
      return [x0 - w * pad, y0 - h * pad, w * (1 + 2 * pad), h * (1 + 2 * pad)];
    }
    function zoomPlan(factor, fx, fy) {
      if (!planView) return;
      var v = planView.vb, w = v[2] * factor, h = v[3] * factor;
      v[0] = v[0] + (v[2] - w) * fx; v[1] = v[1] + (v[3] - h) * fy; v[2] = w; v[3] = h;
      planView.node.setAttribute("viewBox", v.join(" "));
    }
    (function planEvents() {
      var host = $("[data-plan]"), drag = null;
      host.addEventListener("wheel", function (e) {
        if (!planView) return;
        e.preventDefault();
        var r = host.getBoundingClientRect();
        zoomPlan(e.deltaY > 0 ? 1.18 : 1 / 1.18, (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
      }, { passive: false });
      host.addEventListener("pointerdown", function (e) { if (planView) { drag = { x: e.clientX, y: e.clientY }; host.setPointerCapture(e.pointerId); } });
      host.addEventListener("pointermove", function (e) {
        if (!drag || !planView) return;
        var r = host.getBoundingClientRect(), v = planView.vb;
        var scale = Math.max(v[2] / r.width, v[3] / r.height);
        v[0] -= (e.clientX - drag.x) * scale; v[1] -= (e.clientY - drag.y) * scale;
        drag = { x: e.clientX, y: e.clientY };
        planView.node.setAttribute("viewBox", v.join(" "));
      });
      host.addEventListener("pointerup", function () { drag = null; });
      host.addEventListener("dblclick", function () { if (planView) { planView.vb = planView.base.slice(); planView.node.setAttribute("viewBox", planView.vb.join(" ")); } });
    })();

    function sectionSvg() {
      // every building's storeys stacked at their elevations, like a section through the site
      var list = pick.building ? [building()] : buildings;
      var lo = Infinity, hi = -Infinity;
      list.forEach(function (b) {
        b.storeys.forEach(function (s) { var h = heightOf(b, s), z = elevOf(b, s); lo = Math.min(lo, z); hi = Math.max(hi, z + h); });
        if (b.foundation && b.storeys.length) lo = Math.min(lo, b.storeys[0].elev - (b.foundation.height || 0));
      });
      if (!isFinite(lo)) return null;
      var colW = 150, gap = 70, left = 64, top = 24, scale = Math.min(18, 560 / Math.max(hi - lo, 1));
      var width = left + list.length * (colW + gap), height = top * 2 + (hi - lo) * scale + 20;
      var y = function (z) { return top + (hi - z) * scale; };
      var parts = [];
      for (var z = Math.ceil(lo / 5) * 5; z <= hi; z += 5) {
        parts.push('<line x1="' + (left - 6) + '" x2="' + (width - 10) + '" y1="' + y(z) + '" y2="' + y(z) + '" class="rv-sec-grid"/>' +
          '<text x="' + (left - 10) + '" y="' + (y(z) + 4) + '" class="rv-sec-axis" text-anchor="end">' + z + '</text>');
      }
      list.forEach(function (b, n) {
        var x = left + n * (colW + gap) + 10;
        parts.push('<text x="' + (x + colW / 2) + '" y="14" class="rv-sec-name" text-anchor="middle">' + esc(b.name) + '</text>');
        b.storeys.forEach(function (s) {
          var h = heightOf(b, s), base = elevOf(b, s);
          var on = pick.building === b.name && pick.storey === s.index;
          var changed = Math.abs(h - drawingHeight(s)) > 1e-6;
          parts.push('<g class="rv-sec-st' + (on ? " is-on" : "") + '" data-pick="' + esc(b.name) + '|' + s.index + '">' +
            '<rect x="' + x + '" y="' + y(base + h) + '" width="' + colW + '" height="' + Math.max(h * scale - 1, 1) + '" fill="' + storeyColor(s) + '"' + (s.approximate ? ' fill-opacity=".45"' : '') + '/>' +
            '<rect x="' + x + '" y="' + y(base + h) + '" width="' + colW + '" height="' + Math.max(Math.min(0.2 * scale, 4), 1.5) + '" class="rv-sec-slab"/>' +
            (h * scale >= 11 ? '<text x="' + (x + 6) + '" y="' + (y(base + h / 2) + 4) + '" class="rv-sec-label">' + esc(s.label) + '</text>' +
              '<text x="' + (x + colW - 6) + '" y="' + (y(base + h / 2) + 4) + '" class="rv-sec-label' + (changed ? " is-changed" : "") + '" text-anchor="end">' + h.toFixed(2) + 'm</text>' : '') +
            '<title>' + esc(b.name + " " + s.label + " · EL " + base.toFixed(2) + " · 층고 " + h.toFixed(2) + "m" + (s.sheet ? " · " + s.sheet : "")) + '</title></g>');
        });
        if (b.foundation && b.storeys.length) {
          var fb = b.storeys[0].elev, fh = b.foundation.height || 0;
          parts.push('<rect x="' + x + '" y="' + y(fb) + '" width="' + colW + '" height="' + Math.max(fh * scale, 1) + '" fill="#57534e"><title>기초 ' + fh.toFixed(2) + 'm</title></rect>');
        }
      });
      return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + width + ' ' + height + '" width="' + width + '" height="' + Math.round(height) +
        '" class="rv-section">' + parts.join("") + '</svg>';
    }

    function render2d() {
      root.querySelectorAll("[data-tab]").forEach(function (b) { b.classList.toggle("is-on", b.getAttribute("data-tab") === pick.tab); });
      var s = storey();
      if (pick.tab === "section") {
        setPlanSvg(null);
        var svg = sectionSvg();
        $("[data-plan]").innerHTML = svg ? '<div class="rv-section-wrap">' + svg + '</div>' : '<p class="rv-empty">층이 없어요.</p>';
        $("[data-plankey]").innerHTML = '<span><i style="background:' + KIND.basement.color + '"></i>지하</span><span><i style="background:' + KIND.ground.color + '"></i>1층</span>' +
          '<span><i style="background:' + KIND.typical.color + '"></i>기준층</span><span><i style="background:' + KIND.roof.color + '"></i>지붕·옥탑</span>' +
          '<span><i style="background:#57534e"></i>기초</span><span class="rv-help">층을 누르면 그 층으로 가요 · 노란 숫자는 고친 층고</span>';
      } else {
        var index = s ? (pick.tab === "arch" ? s.arch_plan : s.plan) : null;
        setPlanSvg(index != null && pictures[index] ? pictures[index].svg : null);
        $("[data-plankey]").innerHTML = PLAN_KEY.map(function (k) { return '<span><i style="background:' + k[1] + '"></i>' + k[2] + '</span>'; }).join("") +
          (index != null && pictures[index] ? '<span class="rv-help">도면 ' + esc(pictures[index].sheet) + (pictures[index].title ? " · " + esc(pictures[index].title) : "") + '</span>' : '');
      }
      renderCard();
    }

    function renderCard() {
      var b = building(), s = storey(), card = $("[data-card]");
      if (!s) {
        var list = pick.building ? [b] : buildings;
        card.innerHTML = '<table class="rv-table"><thead><tr><th>동</th><th>층</th><th>콘크리트㎥</th><th>거푸집㎡</th><th>철근t</th></tr></thead><tbody>' +
          list.map(function (x) {
            return '<tr data-pick="' + esc(x.name) + '|"><td>' + esc(x.name) + '</td><td>' + x.storeys.length + '</td><td>' + fmt(x.totals.concrete) + '</td><td>' +
              fmt(x.totals.formwork) + '</td><td>' + fmt(x.totals.rebar_t, 1) + '</td></tr>';
          }).join("") + '</tbody></table>';
        return;
      }
      var c = s.concrete || {}, f = s.formwork || {}, a = s.arch;
      var h = heightOf(b, s), changed = Math.abs(h - drawingHeight(s)) > 1e-6;
      card.innerHTML =
        '<div class="rv-card-head"><i class="rv-sw" style="background:' + storeyColor(s) + '"></i><b>' + esc(b.name) + ' ' + esc(s.label) + '</b>' +
          (s.approximate ? '<em class="rv-badge">근사 — 합계에서 뺌</em>' : '') + (changed ? '<em class="rv-badge is-changed">층고 고침 — 다시 계산 필요</em>' : '') + '</div>' +
        '<dl class="rv-facts">' +
          '<dt>바닥 EL</dt><dd>' + fmt(elevOf(b, s), 2) + 'm</dd><dt>층고</dt><dd>' + h.toFixed(2) + 'm' + (changed ? ' (도면 ' + fmt(drawingHeight(s), 2) + ')' : '') + '</dd>' +
          '<dt>벽·기둥 도면</dt><dd>' + esc((s.sheets && s.sheets.vertical) || s.sheet || "-") + '</dd><dt>슬래브·보 도면</dt><dd>' + esc((s.sheets && s.sheets.horizontal) || "-") + '</dd>' +
          '<dt>슬래브 면적</dt><dd>' + fmt(s.slab_area, 1) + '㎡</dd><dt>오픈(개구부·계단)</dt><dd>' + fmt(s.openings, 1) + '㎡</dd>' +
          '<dt>벽 선 / 기둥 / 보</dt><dd>' + s.walls + ' / ' + s.columns + ' / ' + s.beams + '</dd>' +
        '</dl>' +
        '<table class="rv-table"><thead><tr><th></th><th>벽</th><th>기둥</th><th>보</th><th>슬래브</th><th>계단</th><th>합계</th></tr></thead><tbody>' +
          '<tr><td>콘크리트㎥</td><td>' + fmt(c.wall, 1) + '</td><td>' + fmt(c.column, 1) + '</td><td>' + fmt(c.beam, 1) + '</td><td>' + fmt(c.slab, 1) + '</td><td>' + fmt(c.stair, 1) + '</td><td><b>' + fmt(c.total, 1) + '</b></td></tr>' +
          '<tr><td>거푸집㎡</td><td colspan="5" class="rv-small">AL 수직 ' + fmt(f.al_vertical) + ' · AL 수평 ' + fmt(f.al_horizontal) + ' · 갱폼 ' + fmt(f.gang) + ' · 유로폼 ' + fmt(f.euro) + ' · 계단 ' + fmt(f.stair) + '</td><td><b>' + fmt(f.total) + '</b></td></tr>' +
          '<tr><td>철근</td><td colspan="5"></td><td><b>' + fmt((s.rebar_kg || 0) / 1000, 2) + 't</b></td></tr>' +
        '</tbody></table>' +
        (a ? '<p class="rv-small">건축: 조적 ' + fmt(a.masonry, 1) + '㎡ · ALC ' + fmt(a.alc, 1) + '㎡ · 건식벽 ' + fmt(a.dry, 1) + '㎡ · 창 ' + fmt(a.windows) + ' · 문 ' + fmt(a.doors) + '</p>' : '') +
        ((s.notes || []).length ? '<ul class="rv-notes">' + s.notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join("") + '</ul>' : '');
    }

    // ---------- feedback ----------
    function changes() {
      var out = [];
      Object.keys(feedback.storey_heights).forEach(function (b) {
        var review = buildings.filter(function (x) { return x.name === b; })[0];
        Object.keys(feedback.storey_heights[b]).forEach(function (k) {
          var s = review && review.storeys.filter(function (x) { return x.key === k; })[0];
          out.push(b + " " + (s ? s.label : k) + " 층고 " + Number(feedback.storey_heights[b][k]).toFixed(2) + "m" + (s ? " (도면 " + drawingHeight(s).toFixed(2) + ")" : ""));
        });
      });
      Object.keys(feedback.overrides).forEach(function (k) {
        var spec = (data.editable || {})[k] || {};
        out.push((spec.label || k) + " = " + feedback.overrides[k] + (spec.unit || ""));
      });
      Object.keys(feedback.foundations).forEach(function (b) { out.push(b + " 기초 " + feedback.foundations[b] + "m"); });
      if (feedback.edits.length) out.push("3D에서 고친 부재 " + feedback.edits.length + "건");
      return out;
    }
    function editInput(key, spec, value) {
      var name = 'data-override="' + esc(key) + '"';
      if (spec.kind === "toggle") return '<label class="rv-check"><input type="checkbox" ' + name + (value ? " checked" : "") + '>' + esc(spec.on || "") + '</label>';
      if (spec.kind === "choice") return '<select ' + name + '>' + (spec.choices || []).map(function (c) { return '<option' + (c === value ? " selected" : "") + '>' + esc(c) + '</option>'; }).join("") + '</select>';
      if (spec.kind === "number") return '<input type="number" ' + name + ' min="' + spec.min + '" max="' + spec.max + '" step="' + spec.step + '" value="' + esc(value) + '"> ' + esc(spec.unit || "");
      return '<input type="text" ' + name + ' placeholder="' + esc(spec.hint || "") + '" value="' + esc(value) + '">';
    }
    function renderFeedback() {
      var box = $("[data-feedback]");
      var list = changes(), dirty = JSON.stringify(feedback) !== savedFeedback;
      var assumed = (data.assumed || []).filter(function (a) { return a.edit; });
      var shown = {};
      var rows = assumed.map(function (a) {
        shown[a.key] = true;
        var value = feedback.overrides[a.key] != null ? feedback.overrides[a.key] : a.edit.value;
        return '<tr><td>' + esc(a.label) + '</td><td class="rv-small">' + esc(a.value) + '</td><td>' + (a.amount ? fmt(a.amount, 1) + esc(a.unit || "") : "") + '</td><td>' + editInput(a.key, a.edit, value) + '</td></tr>';
      }).join("");
      var others = Object.keys(data.editable || {}).filter(function (k) { return !shown[k]; });
      var foundationRows = buildings.map(function (b) {
        var value = feedback.foundations[b.name] != null ? feedback.foundations[b.name] : (b.foundation ? b.foundation.height : "");
        return '<label class="rv-ft-in">' + esc(b.name) + ' <input type="number" min="0.1" max="5" step="0.05" value="' + esc(value) + '" data-foundation="' + esc(b.name) + '">m</label>';
      }).join("");
      box.innerHTML =
        '<div class="rv-fb-head"><b>피드백 — 고치면 수량에 반영돼요</b>' +
          '<span class="rv-fb-count' + (dirty ? " is-dirty" : "") + '">' + (list.length ? "고친 것 " + list.length + "가지" + (dirty ? " (아직 반영 안 됨)" : " (반영됨)") : "고친 것 없음") + '</span>' +
          (web ? '<button type="button" class="rv-primary" data-save' + (dirty ? "" : " disabled") + '>저장하고 다시 계산</button>'
               : '<button type="button" class="rv-primary" data-download' + (list.length ? "" : " disabled") + '>피드백 파일 받기</button>') +
          '<button type="button" data-reset' + (list.length ? "" : " disabled") + '>고친 것 지우기</button>' +
        '</div>' +
        (list.length ? '<ul class="rv-changes">' + list.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join("") + '</ul>' : '') +
        (!web ? '<p class="rv-small">받은 파일로 다시 계산: <code>python manage.py quantity_takeoff &lt;도면 폴더&gt; --cache &lt;캐시&gt; --feedback 피드백.json --review 검토.html --out 수량.xlsx</code> (캐시가 있으면 도면을 다시 읽지 않아 몇 초 만에 끝나요)</p>' : '') +
        '<div class="rv-fb-grid">' +
          '<div><h4>도면에 없어 정한 값</h4><table class="rv-table rv-assumed"><thead><tr><th>항목</th><th>지금 값</th><th>걸린 양</th><th>바꾸기</th></tr></thead><tbody>' + rows +
            others.map(function (k) {
              var spec = data.editable[k];
              return '<tr class="rv-unused"><td>' + esc(spec.label) + '</td><td class="rv-small">이번 도면엔 안 쓰임</td><td></td><td>' + editInput(k, spec, feedback.overrides[k] != null ? feedback.overrides[k] : "") + '</td></tr>';
            }).join("") + '</tbody></table></div>' +
          '<div><h4>기초 두께</h4><div class="rv-ft-list">' + foundationRows + '</div>' +
            '<h4>층고</h4><p class="rv-small">왼쪽 층 목록의 층고 칸을 고치면 위층 레벨이 같이 움직이고, 다시 계산하면 벽 높이·거푸집·철근에 반영돼요.</p>' +
            '<h4>부재 고치기</h4><p class="rv-small">3D 오른쪽 위 <b>고치기</b>로 벽·보·기둥·개구부를 더하거나 빼면 여기에 쌓여요.</p></div>' +
        '</div>';
    }
    function setOverride(input) {
      var key = input.getAttribute("data-override"), spec = (data.editable || {})[key] || {};
      var value = spec.kind === "toggle" ? input.checked : (spec.kind === "number" ? (input.value === "" ? null : Number(input.value)) : input.value.trim());
      var original = (data.assumed || []).filter(function (a) { return a.key === key && a.edit; })[0];
      if (value === null || value === "" || (original && String(original.edit.value) === String(value) && !original.set)) delete feedback.overrides[key];
      else feedback.overrides[key] = value;
      renderFeedback();
    }

    // ---------- events ----------
    function choose(name, index) {
      pick.building = name || null;
      pick.storey = index == null || index === "" ? null : Number(index);
      renderBuildings(); renderTotals(); renderStoreys(); syncViewer(); render2d(); render3dLegend();
      var on = root.querySelector(".rv-storey.is-on");
      if (on && on.scrollIntoView) on.scrollIntoView({ block: "nearest" });
    }
    root.addEventListener("click", function (e) {
      var t = e.target.closest ? e.target.closest("button, [data-pick]") : null;
      if (!t || !root.contains(t)) return;
      if (t.tagName === "INPUT") return;
      if (t.hasAttribute("data-b")) { choose(t.getAttribute("data-b"), null); return; }
      if (t.hasAttribute("data-tab")) { pick.tab = t.getAttribute("data-tab"); render2d(); return; }
      if (t.hasAttribute("data-v") && viewer) {
        viewer.setView(t.getAttribute("data-v"));
        root.querySelectorAll("[data-v]").forEach(function (b) { b.classList.toggle("is-on", b === t); });
        return;
      }
      if (t.hasAttribute("data-section")) { var s = $('[data-tool="section"]'); if (s) s.click(); t.classList.toggle("is-on"); return; }
      if (t.hasAttribute("data-download")) { download(); return; }
      if (t.hasAttribute("data-save")) { save(t); return; }
      if (t.hasAttribute("data-reset")) {
        feedback = { storey_heights: {}, overrides: {}, foundations: {}, edits: [] };
        if (viewer) { viewer.setModel(liveModel()); if (viewer.setEdits) viewer.setEdits([]); }
        renderStoreys(); render2d(); renderFeedback();
        return;
      }
      if (t.hasAttribute("data-pick") && !(e.target.tagName === "INPUT" || e.target.tagName === "LABEL")) {
        var parts = t.getAttribute("data-pick").split("|");
        var same = pick.building === parts[0] && String(pick.storey) === parts[1];
        choose(parts[0], same ? null : parts[1]);
      }
    });
    // the 3D toolbar's colour button changes the legend
    root.addEventListener("click", function (e) {
      if (e.target && e.target.hasAttribute && e.target.hasAttribute("data-color")) setTimeout(render3dLegend, 0);
    });
    root.addEventListener("change", function (e) {
      var t = e.target;
      if (t.hasAttribute("data-height")) {
        var parts = t.getAttribute("data-height").split("|"), value = Number(t.value);
        var b = buildings.filter(function (x) { return x.name === parts[0]; })[0];
        var s = b && b.storeys.filter(function (x) { return x.key === parts[1]; })[0];
        if (!s || !(value >= 1.5 && value <= 12)) { renderStoreys(); return; }
        feedback.storey_heights[b.name] = feedback.storey_heights[b.name] || {};
        if (Math.abs(value - drawingHeight(s)) < 1e-6 && s.height_from !== "person") delete feedback.storey_heights[b.name][s.key];
        else feedback.storey_heights[b.name][s.key] = Math.round(value * 1000) / 1000;
        if (!Object.keys(feedback.storey_heights[b.name]).length) delete feedback.storey_heights[b.name];
        if (viewer) viewer.setModel(liveModel());
        renderStoreys(); render2d(); renderFeedback();
        return;
      }
      if (t.hasAttribute("data-override")) { setOverride(t); return; }
      if (t.hasAttribute("data-foundation")) {
        var name = t.getAttribute("data-foundation"), v = Number(t.value);
        var current = (buildings.filter(function (x) { return x.name === name; })[0] || {}).foundation;
        if (!(v >= 0.1 && v <= 5) || (current && Math.abs(current.height - v) < 1e-6 && feedback.foundations[name] == null)) delete feedback.foundations[name];
        else feedback.foundations[name] = v;
        renderFeedback();
      }
    });
    root.addEventListener("keydown", function (e) {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
      var b = building();
      if (!b) return;
      if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        var next = pick.storey == null ? 0 : pick.storey + (e.key === "ArrowUp" ? 1 : -1);
        if (next >= 0 && next < b.storeys.length) choose(b.name, next);
      }
    });
    root.tabIndex = 0;

    function download() {
      var body = JSON.stringify(feedback, null, 2);
      var blob = new Blob([body], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "피드백_" + (data.title || "수량산출").replace(/[\\/:*?"<>|\s]+/g, "_") + ".json";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
    }
    function save(button) {
      button.disabled = true;
      button.textContent = "저장하고 계산하는 중…";
      fetch(options.save_url, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": options.csrf || "" },
        body: JSON.stringify({ floor_job_id: options.floor_job_id, edits: feedback.edits, overrides: feedback.overrides,
                               foundations: feedback.foundations, storey_heights: feedback.storey_heights })
      }).then(function (res) { return res.json().then(function (body) { return { ok: res.ok, body: body }; }); })
        .then(function (r) {
          if (!r.ok) throw new Error((r.body && r.body.error) || "저장하지 못했어요");
          window.location.reload();
        })
        .catch(function (err) { button.disabled = false; button.textContent = "저장하고 다시 계산"; alert(err.message || "저장하지 못했어요"); });
    }

    renderBuildings(); renderTotals(); renderStoreys();
    startViewer();
    syncViewer(); render2d(); render3dLegend(); renderFeedback();
    return {
      choose: choose, feedback: function () { return JSON.parse(JSON.stringify(feedback)); },
      viewer: function () { return viewer; }, setTab: function (tab) { pick.tab = tab; render2d(); }
    };
  }

  window.cblQuantityReview = { mount: mount };
})();
