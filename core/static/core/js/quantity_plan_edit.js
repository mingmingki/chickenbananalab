/* AI 수량산출 평면 고치기: on a plan picture people add the members the reading missed (a concrete
   beam, a wall, a column) and take out what is not one.

   The picture is an SVG of one plan frame (1 unit = 10 mm, y down); a fix is kept in the sheet's own
   coordinates in metres (x right, y up from the frame's lower left), the coordinates the 3D model and
   the takeoff start from. Points snap to the drawn lines' ends and onto the lines; double-clicking a
   line puts the member along it (between the line and its parallel partner when there is one).

   Lines can be erased too (a stray line, a sign drawn on the wall layer): they are kept per plan in
   sheet metres and the drawing is read again without them, so the walls are measured again rather
   than guessed; lines the reading left out itself (dashed red) can be brought back the same way.

   window.cblQuantityPlanEdit.mount(stage, options) → controller
     stage: an element holding the plan <svg> (pan/zoom is the caller's)
     options: { planId, edits: shared array of every plan's fixes, onChange(edits), note: text,
                sizes: { beam: {w, d}, wall: {t}, column: {w, h}, opening: {h} } (metres),
                lines: shared {plan: {erase: [[x0, y0, x1, y1]], keep: [...]}}, onLines(lines, readAgain),
                linesPending(): true when lines changed since the last reading }
   window.cblQuantityPlanEdit.open(options) → a full-screen editor with its own pan/zoom:
     options as above plus { svg, title, onClose(edits) } */
(function () {
  "use strict";

  var SNAP_PX = 10, PICK_PX = 8, PAIR_MAX = 1.3, PAIR_MIN = 0.05; // metres for pairs
  var LABEL = { beam: "보", wall: "벽", column: "기둥", opening: "개구부" };
  var TOOLS = [["view", "선택"], ["beam", "보 추가"], ["wall", "벽 추가"], ["column", "기둥 추가"], ["opening", "개구부"],
               ["remove", "부재 빼기"], ["erase", "선 지우기"], ["keep", "되살리기"], ["measure", "거리 재기"], ["mark", "표시·AI"]];
  var TYPED = "길이를 숫자(mm)로 치고 Enter로 정할 수 있어요. Shift를 누르면 수평·수직.";
  var HINT = {
    view: "선택: 고친 것을 누르면 크기를 바꾸거나 지울 수 있어요 (Delete). 드래그로 옮기고 휠로 확대해요.",
    beam: "보 추가: 시작점과 끝점을 누르세요. " + TYPED + " 선을 두 번 누르면 그 선을 따라 넣어요.",
    wall: "벽 추가: 시작점과 끝점을 누르세요. " + TYPED + " 벽 선을 두 번 누르면 그 벽을 따라 넣어요.",
    column: "기둥 추가: 기둥 가운데를 누르세요.",
    opening: "개구부: 벽 선 위에서 시작점과 끝점을 누르세요. 두께는 맞은편 벽 선에서 재요. " + TYPED,
    remove: "부재 빼기: 잘못 잡힌 벽·보를 누르면 수량에서 바로 빠져요 (고친 표시를 누르면 그 표시가 지워져요).",
    erase: "선 지우기: 쓸데없는 선을 누르거나, 끌어서 상자 안의 선을 한꺼번에 고르세요. '다시 읽기'를 누르면 그 선을 빼고 다시 재요.",
    keep: "되살리기: 빨간 점선(자동으로 뺐거나 지운 선)을 누르면 다시 넣어요.",
    measure: "거리 재기: 두 점을 누르세요.",
    mark: "표시·AI: 이상한 곳을 끌어서 동그라미로 감싸고 무엇이 이상한지 적으면, AI가 고칠 안을 내요 (보내기 전에 비용을 보여줘요)."
  };
  // colours of the picture's classes, for the image of a marked part sent to the AI
  var PICTURE_STYLE = "path{fill:none;stroke-linecap:round}.fv-frame{stroke:#facc15;stroke-width:2.4}.fv-beam{stroke:#fb923c;stroke-width:1.8;stroke-dasharray:6 4}" +
    ".fv-masonry{stroke:#4ade80;stroke-width:2}.fv-alc{stroke:#bef264;stroke-width:1.8}.fv-dry{stroke:#7dd3fc;stroke-width:1.4}.fv-opening{stroke:#c4b5fd;stroke-width:1.2}" +
    ".fv-other{stroke:#94a3b8;stroke-width:1;stroke-opacity:.7}.fv-removed{stroke:#f87171;stroke-width:1.4;stroke-dasharray:5 4}" +
    ".qpe-add{fill:rgba(34,211,238,.45);stroke:#22d3ee}.qpe-remove{fill:rgba(248,113,113,.2);stroke:#f87171}.qpe-opening{fill:rgba(192,132,252,.45);stroke:#c084fc}" +
    ".qpe-erased{stroke:#f87171;stroke-dasharray:5 4}.qpe-erased-under{stroke:#0b1238;stroke-width:4}" +
    ".qpe-mark{fill:rgba(244,114,182,.08);stroke:#f472b6;stroke-width:3;stroke-dasharray:10 6}";

  function uid() { return Math.random().toString(36).slice(2, 10); }
  function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c];
    });
  }

  // ── the picture's lines, indexed for snapping and picking ──
  function readLines(svg) {
    var segs = [], cell = 200; // 2 m buckets
    var grid = {};
    svg.querySelectorAll("path").forEach(function (path) {
      var cls = (path.getAttribute("class") || "").replace("fv-", "");
      if (cls.indexOf("qpe-") === 0) return; // the editor's own drawing
      var layer = path.getAttribute("data-layer") || path.getAttribute("data-why") || "";
      var nums = (path.getAttribute("d") || "").match(/[ML]|-?\d+(?:\.\d+)?/g) || [];
      var last = null, i = 0;
      while (i < nums.length) {
        var op = nums[i];
        if (op === "M" || op === "L") {
          var x = Number(nums[i + 1]), y = Number(nums[i + 2]);
          i += 3;
          if (op === "L" && last) {
            var seg = { x0: last[0], y0: last[1], x1: x, y1: y, cls: cls, layer: layer };
            var k = segs.push(seg) - 1;
            var gx0 = Math.floor(Math.min(seg.x0, seg.x1) / cell), gx1 = Math.floor(Math.max(seg.x0, seg.x1) / cell);
            var gy0 = Math.floor(Math.min(seg.y0, seg.y1) / cell), gy1 = Math.floor(Math.max(seg.y0, seg.y1) / cell);
            for (var gx = gx0; gx <= gx1; gx++) for (var gy = gy0; gy <= gy1; gy++) {
              (grid[gx + "," + gy] = grid[gx + "," + gy] || []).push(k);
            }
          }
          last = [x, y];
        } else {
          i += 1;
        }
      }
    });
    function near(x, y, r) {
      var out = [], seen = {};
      for (var gx = Math.floor((x - r) / cell); gx <= Math.floor((x + r) / cell); gx++) {
        for (var gy = Math.floor((y - r) / cell); gy <= Math.floor((y + r) / cell); gy++) {
          (grid[gx + "," + gy] || []).forEach(function (k) { if (!seen[k]) { seen[k] = 1; out.push(segs[k]); } });
        }
      }
      return out;
    }
    return { segs: segs, near: near };
  }

  function project(seg, x, y) {
    var dx = seg.x1 - seg.x0, dy = seg.y1 - seg.y0, len2 = dx * dx + dy * dy;
    var t = len2 ? Math.max(0, Math.min(1, ((x - seg.x0) * dx + (y - seg.y0) * dy) / len2)) : 0;
    var px = seg.x0 + dx * t, py = seg.y0 + dy * t;
    return { x: px, y: py, d: Math.hypot(x - px, y - py), t: t };
  }

  // CAD often draws one wall face as many short pieces: the run of pieces on one straight line,
  // touching end to end (a gap such as a window ends it), as one line
  function extend(lines, seg) {
    var dx = seg.x1 - seg.x0, dy = seg.y1 - seg.y0, len = Math.hypot(dx, dy);
    if (!len) return seg;
    var ux = dx / len, uy = dy / len, lo = 0, hi = len, grew = true, guard = 0;
    function along(x, y) { return (x - seg.x0) * ux + (y - seg.y0) * uy; }
    function off(x, y) { return Math.abs((x - seg.x0) * uy - (y - seg.y0) * ux); }
    while (grew && guard++ < 400) {
      grew = false;
      [[lo, -1], [hi, 1]].forEach(function (end) {
        var ex = seg.x0 + ux * end[0], ey = seg.y0 + uy * end[0];
        lines.near(ex, ey, 4).forEach(function (o) {
          if (o.cls !== seg.cls || off(o.x0, o.y0) > 1.5 || off(o.x1, o.y1) > 1.5) return;
          var t0 = along(o.x0, o.y0), t1 = along(o.x1, o.y1), a = Math.min(t0, t1), b = Math.max(t0, t1);
          if (end[1] > 0 && a <= hi + 3 && b > hi + 0.5) { hi = b; grew = true; }
          if (end[1] < 0 && b >= lo - 3 && a < lo - 0.5) { lo = a; grew = true; }
        });
      });
    }
    return { x0: seg.x0 + ux * lo, y0: seg.y0 + uy * lo, x1: seg.x0 + ux * hi, y1: seg.y0 + uy * hi, cls: seg.cls, layer: seg.layer, base: seg };
  }

  // the parallel line beside a picked one (the other face of a wall or a beam), with the width
  function partner(lines, seg) {
    var dx = seg.x1 - seg.x0, dy = seg.y1 - seg.y0, len = Math.hypot(dx, dy);
    if (!len) return null;
    var ux = dx / len, uy = dy / len, best = null;
    var mx = (seg.x0 + seg.x1) / 2, my = (seg.y0 + seg.y1) / 2;
    lines.near(mx, my, PAIR_MAX * 100 + len / 2).forEach(function (o) {
      if (o === seg || o === seg.base) return;
      var ox = o.x1 - o.x0, oy = o.y1 - o.y0, olen = Math.hypot(ox, oy);
      if (!olen || Math.abs(ux * oy / olen - uy * ox / olen) > 0.03) return; // not parallel
      var gap = Math.abs((o.x0 - seg.x0) * uy - (o.y0 - seg.y0) * ux);
      if (gap < PAIR_MIN * 100 || gap > PAIR_MAX * 100) return;
      var t0 = (o.x0 - seg.x0) * ux + (o.y0 - seg.y0) * uy, t1 = (o.x1 - seg.x0) * ux + (o.y1 - seg.y0) * uy;
      if (Math.max(t0, t1) < 0 || Math.min(t0, t1) > len) return;
      if (!best || gap < best.gap) best = { seg: o, gap: gap, side: (o.x0 - seg.x0) * uy - (o.y0 - seg.y0) * ux };
    });
    if (!best) return null;
    // the other face as a whole run too; the member spans where both faces run side by side
    var other = extend(lines, best.seg);
    var s0 = (other.x0 - seg.x0) * ux + (other.y0 - seg.y0) * uy, s1 = (other.x1 - seg.x0) * ux + (other.y1 - seg.y0) * uy;
    best.lo = Math.max(0, Math.min(s0, s1));
    best.hi = Math.min(len, Math.max(s0, s1));
    if (best.hi - best.lo < 5) return null;
    // the centre line over the shared run, in picture units
    var nx = uy, ny = -ux, half = best.side / 2;
    return {
      width: best.gap / 100,
      a: { x: seg.x0 + ux * best.lo + nx * half, y: seg.y0 + uy * best.lo + ny * half },
      b: { x: seg.x0 + ux * best.hi + nx * half, y: seg.y0 + uy * best.hi + ny * half }
    };
  }

  // Plan pictures are zoomed through their viewBox, not a CSS transform: the browser draws the lines
  // again at every zoom, so they stay sharp and keep their width (vector-effect: non-scaling-stroke);
  // a CSS scale blew a 1.7 px line up to a blurred 8 px band at 5x. view = {s, x, y}: the scale and
  // the offset in screen pixels, as if the whole picture at s = 1 were scaled and moved.
  function baseBox(svg) {
    var vb = svg.getAttribute("data-vb");
    if (!vb) {
      vb = svg.getAttribute("viewBox") || "0 0 1 1";
      svg.setAttribute("data-vb", vb);
    }
    return vb.split(/[\s,]+/).map(Number);
  }
  function frame(svg) {
    var b = baseBox(svg);
    svg.style.transform = "";
    var W = svg.clientWidth || svg.getBoundingClientRect().width, H = svg.clientHeight || svg.getBoundingClientRect().height;
    var k = Math.min(W / b[2], H / b[3]);
    return { b: b, W: W, H: H, k: k, ox: (W - b[2] * k) / 2, oy: (H - b[3] * k) / 2, ok: W > 0 && H > 0 && k > 0 && isFinite(k) };
  }
  function zoomSvg(svg, view) {
    if (!svg) return;
    var f = frame(svg);
    if (!f.ok) return;
    var s = view.s * f.k;
    svg.setAttribute("viewBox", [f.b[0] + (-view.x / view.s - f.ox) / f.k, f.b[1] + (-view.y / view.s - f.oy) / f.k, f.W / s, f.H / s]
      .map(function (n) { return +n.toFixed(2); }).join(" "));
  }
  // the view that fills the stage with what is drawn (the plan), not the whole sheet frame: the
  // range of the line ends, a few stray ends trimmed (long lines running off the plan), kept on the sheet
  var FIT_TRIM = 0.004, FIT_MAX_POINTS = 200000;
  function fitView(svg, margin) {
    var f = svg && frame(svg);
    if (!f || !f.ok) return { s: 1, x: 0, y: 0 };
    var xs = [], ys = [];
    Array.prototype.forEach.call(svg.querySelectorAll("path"), function (p) {
      if (xs.length >= FIT_MAX_POINTS || getComputedStyle(p).display === "none") return;
      var nums = (p.getAttribute("d") || "").match(/-?\d+(?:\.\d+)?/g) || [];
      for (var i = 0; i + 1 < nums.length && xs.length < FIT_MAX_POINTS; i += 2) {
        xs.push(+nums[i]);
        ys.push(+nums[i + 1]);
      }
    });
    if (xs.length < 2) return { s: 1, x: 0, y: 0 };
    function range(values, lo, hi) {
      values.sort(function (a, b) { return a - b; });
      var cut = Math.floor(values.length * FIT_TRIM);
      return [Math.max(lo, values[cut]), Math.min(hi, values[values.length - 1 - cut])];
    }
    var rx = range(xs, f.b[0], f.b[0] + f.b[2]), ry = range(ys, f.b[1], f.b[1] + f.b[3]);
    if (!(rx[1] > rx[0]) || !(ry[1] > ry[0])) return { s: 1, x: 0, y: 0 };
    var w = Math.max((rx[1] - rx[0]) * f.k, 1), h = Math.max((ry[1] - ry[0]) * f.k, 1);
    var s = Math.max(1, Math.min(40, (1 - (margin == null ? 0.08 : margin)) * Math.min(f.W / w, f.H / h)));
    var cx = ((rx[0] + rx[1]) / 2 - f.b[0]) * f.k + f.ox, cy = ((ry[0] + ry[1]) / 2 - f.b[1]) * f.k + f.oy;
    return { s: s, x: f.W / 2 - s * cx, y: f.H / 2 - s * cy };
  }

  // a close view centred on one point of the sheet (metres), e.g. a member picked in 3D
  function focusView(svg, point, scale) {
    var f = svg && frame(svg);
    if (!f || !f.ok || !point) return { s: 1, x: 0, y: 0 };
    var x = point[0] * 100, y = f.b[3] - point[1] * 100;
    var cx = (x - f.b[0]) * f.k + f.ox, cy = (y - f.b[1]) * f.k + f.oy, s = scale || 6;
    return { s: s, x: f.W / 2 - s * cx, y: f.H / 2 - s * cy };
  }

  function mount(stage, options) {
    options = options || {};
    var svg = stage.querySelector("svg");
    var box = baseBox(svg);
    var H = box[3];
    var lines = readLines(svg);
    var edits = options.edits || [];
    var sizes = options.sizes || {};
    sizes.beam = sizes.beam || { w: 0.4, d: 0.6 };
    sizes.wall = sizes.wall || { t: 0.2 };
    sizes.column = sizes.column || { w: 0.5, h: 0.5 };
    sizes.opening = sizes.opening || { h: 2.1 };
    var tool = "view", start = null, down = null, box = null, lastP = null, typed = "", selected = null, opening = null, mark = null;
    var linesState = options.lines || {};

    // sheet metres ↔ picture units
    function toSheet(p) { return [Math.round(p.x) / 100, Math.round(H - p.y) / 100]; }
    function toPicture(m) { return { x: m[0] * 100, y: H - m[1] * 100 }; }

    var layer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    layer.setAttribute("class", "qpe-layer");
    svg.appendChild(layer);
    var preview = document.createElementNS("http://www.w3.org/2000/svg", "g");
    preview.setAttribute("class", "qpe-preview");
    svg.appendChild(preview);
    var linesLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    linesLayer.setAttribute("class", "qpe-lines");
    svg.insertBefore(linesLayer, layer);

    var bar = document.createElement("div");
    bar.className = "qpe-bar";
    bar.innerHTML =
      '<div class="qpe-tools">' + TOOLS.map(function (t) {
        return '<button type="button" data-qpe-tool="' + t[0] + '"' + (t[0] === tool ? ' class="is-on"' : '') + '>' + t[1] + '</button>';
      }).join("") + '<button type="button" data-qpe-undo>되돌리기</button></div>' +
      '<div class="qpe-sizes">' +
        '<label data-for="beam">보 폭<input type="number" data-size="beam.w" step="10" min="50"> 춤<input type="number" data-size="beam.d" step="10" min="50">mm</label>' +
        '<label data-for="wall">벽 두께<input type="number" data-size="wall.t" step="10" min="50">mm</label>' +
        '<label data-for="column">기둥<input type="number" data-size="column.w" step="10" min="50">×<input type="number" data-size="column.h" step="10" min="50">mm</label>' +
        '<label data-for="opening">개구부 높이<input type="number" data-size="opening.h" step="50" min="300">mm</label>' +
      '</div>' +
      '<div class="qpe-props" hidden></div>' +
      '<div class="qpe-ask" hidden></div>' +
      '<p class="qpe-hint"></p><p class="qpe-count"></p>' +
      '<div class="qpe-reread" hidden><span></span><button type="button" class="qd-btn primary" data-qpe-reread>지운 선 빼고 다시 읽기</button></div>' +
      '<div class="qpe-learn" hidden></div>';
    if (!options.onAsk) { var markTool = bar.querySelector('[data-qpe-tool="mark"]'); if (markTool) markTool.remove(); }
    bar.querySelectorAll("[data-size]").forEach(function (input) {
      var key = input.getAttribute("data-size").split(".");
      input.value = Math.round(sizes[key[0]][key[1]] * 1000);
    });

    function mine() { return edits.filter(function (e) { return e.plan === options.planId; }); }
    function changed() {
      draw();
      suggest();
      if (options.onChange) options.onChange(edits);
    }
    // the same layer fixed the same way twice: offer to learn the layer's role for the next drawings
    var ROLE_OF = { beam: "beam", wall: "wall", column: "column" };
    var learned = {};
    function suggest() {
      var box = bar.querySelector(".qpe-learn");
      if (!options.onLearn) { box.hidden = true; return; }
      var votes = {};
      edits.forEach(function (e) {
        if (!e.layer) return;
        var role = e.op === "add" ? ROLE_OF[e.kind] : "";
        var key = e.layer + "\u0000" + role;
        votes[key] = (votes[key] || 0) + 1;
      });
      var best = Object.keys(votes).filter(function (k) { return votes[k] >= 2 && !learned[k]; })
        .sort(function (a, b) { return votes[b] - votes[a]; })[0];
      if (!best) { box.hidden = true; return; }
      var parts = best.split("\u0000"), layerName = parts[0], role = parts[1];
      var what = role ? LABEL[role === "column" ? "column" : role] : "부재 아님";
      box.hidden = false;
      box.innerHTML = '<span>‘' + esc(layerName) + '’ 레이어 선을 ' + votes[best] + '번 ' + (role ? what + "(으)로 넣었어요" : "지웠어요") +
        '. 이 레이어를 모두 ' + what + '(으)로 배울까요?</span><button type="button" data-qpe-learn>배우기</button><button type="button" data-qpe-nolearn>아니요</button>';
      box.querySelector("[data-qpe-learn]").onclick = function () {
        learned[best] = true;
        box.innerHTML = "<span>기억하는 중…</span>";
        Promise.resolve(options.onLearn(layerName, role || null)).then(function (text) {
          box.innerHTML = "<span>" + esc(text || "기억했어요. 이 사무소 도면을 다음에 읽을 때 써요.") + "</span>";
        }, function () { box.innerHTML = "<span>저장하지 못했어요.</span>"; });
      };
      box.querySelector("[data-qpe-nolearn]").onclick = function () { learned[best] = true; box.hidden = true; };
    }
    function status(text) { bar.querySelector(".qpe-hint").textContent = text || HINT[tool]; }
    function count() {
      var list = mine(), added = list.filter(function (e) { return e.op === "add"; }).length;
      var item = linesState[options.planId] || {}, erased = (item.erase || []).length, kept = (item.keep || []).length;
      bar.querySelector(".qpe-count").textContent = (list.length ? "이 평면에서 고친 것 " + list.length + "건 (추가 " + added + " · 뺌 " + (list.length - added) + ")" : "아직 고친 것이 없어요") +
        (erased || kept ? " · 지운 선 " + erased + "개" + (kept ? " · 되살린 선 " + kept + "개" : "") : "") +
        (options.note ? " · " + options.note : "");
      var reread = bar.querySelector(".qpe-reread");
      var pending = options.linesPending ? options.linesPending() : false;
      reread.hidden = !pending;
      if (pending) reread.querySelector("span").textContent = "지운 선·되살린 선은 다시 읽어야 수량에 들어가요 (전체 평면을 다시 읽어 1~4분 걸려요).";
    }
    function setTool(name) {
      tool = name;
      start = null;
      preview.innerHTML = "";
      bar.querySelectorAll("[data-qpe-tool]").forEach(function (b) { b.classList.toggle("is-on", b.getAttribute("data-qpe-tool") === name); });
      bar.querySelectorAll(".qpe-sizes [data-for]").forEach(function (l) { l.hidden = l.getAttribute("data-for") !== name; });
      stage.classList.toggle("qpe-drawing", name !== "view");
      stage.classList.toggle("qpe-erasing", name === "erase" || name === "mark");
      if (name !== "mark") closeMark();
      typed = "";
      opening = null;
      box = null;
      if (name !== "view") select(null);
      status();
    }

    // ── drawing the fixes ──
    function rect(a, b, half) {
      var dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
      var nx = -dy / len * half, ny = dx / len * half;
      return [[a.x + nx, a.y + ny], [b.x + nx, b.y + ny], [b.x - nx, b.y - ny], [a.x - nx, a.y - ny]]
        .map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" ");
    }
    function shape(e) {
      var cls = "qpe-" + e.op + " qpe-" + e.kind + (selected === e.id ? " is-selected" : "");
      if (e.kind === "column") {
        var c = toPicture(e.c), w = e.w * 50, h = e.h * 50;
        return '<rect data-edit="' + esc(e.id) + '" class="' + cls + '" x="' + (c.x - w).toFixed(1) + '" y="' + (c.y - h).toFixed(1) +
          '" width="' + (2 * w).toFixed(1) + '" height="' + (2 * h).toFixed(1) + '"/>';
      }
      var a = toPicture(e.a), b = toPicture(e.b), width = (e.kind === "beam" ? e.w : e.t) * 50;
      if (e.kind === "opening") cls = "qpe-opening" + (selected === e.id ? " is-selected" : "");
      return '<polygon data-edit="' + esc(e.id) + '" class="' + cls + '" points="' + rect(a, b, Math.max(width, 6)) + '"/>' +
        (e.op === "remove" ? '<line class="qpe-cross" x1="' + a.x.toFixed(1) + '" y1="' + a.y.toFixed(1) + '" x2="' + b.x.toFixed(1) + '" y2="' + b.y.toFixed(1) + '"/>' : "");
    }
    function draw() {
      layer.innerHTML = mine().map(shape).join("");
      drawLines();
      count();
    }

    // ── erased and brought-back lines (sheet metres, as the picture rounds them) ──
    function planLines() {
      var item = linesState[options.planId] = linesState[options.planId] || {};
      item.erase = item.erase || [];
      item.keep = item.keep || [];
      return item;
    }
    function sheetSeg(s) {
      var a = toSheet({ x: s.x0, y: s.y0 }), b = toSheet({ x: s.x1, y: s.y1 });
      return [a[0], a[1], b[0], b[1]];
    }
    function sameSeg(p, q) {
      var t = 0.03;
      function near(i, j) { return Math.abs(p[i] - q[j]) <= t; }
      return (near(0, 0) && near(1, 1) && near(2, 2) && near(3, 3)) || (near(0, 2) && near(1, 3) && near(2, 0) && near(3, 1));
    }
    function findSeg(list, seg) {
      for (var i = 0; i < list.length; i++) if (sameSeg(list[i], seg)) return i;
      return -1;
    }
    function linesChanged() {
      drawLines();
      count();
      if (options.onLines) options.onLines(linesState, false);
    }
    function eraseSegs(segs) {
      var item = planLines(), n = 0;
      segs.forEach(function (s) {
        var key = sheetSeg(s), k = findSeg(item.keep, key);
        if (k >= 0) { item.keep.splice(k, 1); n++; return; } // it was brought back: out again
        if (s.cls === "removed" || findSeg(item.erase, key) >= 0) return;
        item.erase.push(key);
        n++;
      });
      if (n) linesChanged();
      return n;
    }
    function keepSegs(segs) {
      var item = planLines(), n = 0;
      segs.forEach(function (s) {
        var key = sheetSeg(s), k = findSeg(item.erase, key);
        if (k >= 0) { item.erase.splice(k, 1); n++; return; } // erased by a person: just not erased
        if (s.cls === "removed" && findSeg(item.keep, key) < 0) { item.keep.push(key); n++; }
      });
      if (n) linesChanged();
      return n;
    }
    function drawLines() {
      var item = linesState[options.planId] || {};
      function d(list) {
        return (list || []).map(function (q) {
          var a = toPicture([q[0], q[1]]), b = toPicture([q[2], q[3]]);
          return "M" + a.x.toFixed(1) + " " + a.y.toFixed(1) + "L" + b.x.toFixed(1) + " " + b.y.toFixed(1);
        }).join("");
      }
      var erased = d(item.erase), kept = d(item.keep);
      linesLayer.innerHTML = (erased ? '<path class="qpe-erased-under" d="' + erased + '"/><path class="qpe-erased" d="' + erased + '"/>' : "") +
        (kept ? '<path class="qpe-kept" d="' + kept + '"/>' : "");
    }
    // the drawn pieces making up one straight run (as extend() joins them), for erasing a whole line
    function runOf(seg) {
      var run = extend(lines, seg);
      var dx = run.x1 - run.x0, dy = run.y1 - run.y0, len = Math.hypot(dx, dy);
      if (!len) return [seg];
      var ux = dx / len, uy = dy / len, out = [], seen = [];
      for (var t = 0; t <= len + 150; t += 150) {
        var at = Math.min(t, len);
        lines.near(run.x0 + ux * at, run.y0 + uy * at, 160).forEach(function (o) {
          if (seen.indexOf(o) >= 0 || o.cls !== seg.cls) return;
          if (Math.abs((o.x0 - run.x0) * uy - (o.y0 - run.y0) * ux) > 1.5 || Math.abs((o.x1 - run.x0) * uy - (o.y1 - run.y0) * ux) > 1.5) return;
          var a = (o.x0 - run.x0) * ux + (o.y0 - run.y0) * uy, b = (o.x1 - run.x0) * ux + (o.y1 - run.y0) * uy;
          if (Math.min(a, b) < -2 || Math.max(a, b) > len + 2) return;
          seen.push(o);
          out.push(o);
        });
      }
      return out.length ? out : [seg];
    }
    function inBox(b) {
      var x0 = Math.min(b.x0, b.x1), x1 = Math.max(b.x0, b.x1), y0 = Math.min(b.y0, b.y1), y1 = Math.max(b.y0, b.y1);
      return lines.segs.filter(function (s) {
        return s.cls !== "removed" && Math.min(s.x0, s.x1) >= x0 && Math.max(s.x0, s.x1) <= x1 && Math.min(s.y0, s.y1) >= y0 && Math.max(s.y0, s.y1) <= y1;
      });
    }

    // ── the selected fix: its sizes, or out ──
    function editById(id) {
      for (var i = 0; i < edits.length; i++) if (edits[i].id === id) return edits[i];
      return null;
    }
    function select(id) {
      selected = id && editById(id) ? id : null;
      var panel = bar.querySelector(".qpe-props"), e = selected && editById(selected);
      panel.hidden = !e;
      if (e) {
        var fields = { beam: [["w", "폭"], ["d", "춤"]], wall: [["t", "두께"]], column: [["w", "가로"], ["h", "세로"]], opening: [["t", "벽 두께"], ["h", "높이"]] }[e.kind] || [];
        panel.innerHTML = '<b>' + (e.op === "remove" ? "뺀 " : "") + LABEL[e.kind] + '</b><span>' + esc(lengthText(e)) + '</span>' +
          fields.map(function (f) {
            return '<label>' + f[1] + '<input type="number" data-prop="' + f[0] + '" step="10" min="50" value="' + Math.round(e[f[0]] * 1000) + '">mm</label>';
          }).join("") + '<button type="button" class="qd-btn" data-qpe-delete>이것 지우기</button>';
      }
      layer.querySelectorAll("[data-edit]").forEach(function (el) { el.classList.toggle("is-selected", el.getAttribute("data-edit") === selected); });
    }
    function deleteSelected() {
      if (!selected) return false;
      for (var i = edits.length - 1; i >= 0; i--) if (edits[i].id === selected) edits.splice(i, 1);
      select(null);
      status("고친 것을 지웠어요.");
      changed();
      return true;
    }

    // ── pointer: picture units under the pointer, snapping ──
    function unitsPerPx() {
      var m = svg.getScreenCTM();
      return m ? 1 / Math.hypot(m.a, m.b) : 1;
    }
    function at(e) {
      var m = svg.getScreenCTM();
      if (!m) return null;
      var p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse());
      return { x: p.x, y: p.y };
    }
    function snap(p) {
      var u = unitsPerPx(), r = SNAP_PX * u, best = null;
      lines.near(p.x, p.y, r).forEach(function (s) {
        [[s.x0, s.y0], [s.x1, s.y1]].forEach(function (q) {
          var d = Math.hypot(q[0] - p.x, q[1] - p.y);
          if (d <= r && (!best || d < best.d)) best = { x: q[0], y: q[1], d: d, end: true };
        });
      });
      if (best) return best;
      lines.near(p.x, p.y, PICK_PX * u).forEach(function (s) {
        var q = project(s, p.x, p.y);
        if (q.d <= PICK_PX * u && (!best || q.d < best.d)) best = { x: q.x, y: q.y, d: q.d };
      });
      if (best) return best;
      if (start) { // straight along x or y from the start when nearly so
        if (Math.abs(p.x - start.x) < 4 * u) return { x: start.x, y: p.y };
        if (Math.abs(p.y - start.y) < 4 * u) return { x: p.x, y: start.y };
      }
      return p;
    }
    function pick(p, kinds) {
      var r = PICK_PX * unitsPerPx(), best = null;
      lines.near(p.x, p.y, r).forEach(function (s) {
        if (kinds && kinds.indexOf(s.cls) < 0) return;
        var q = project(s, p.x, p.y);
        if (q.d <= r && (!best || q.d < best.d)) best = { seg: s, d: q.d };
      });
      return best && best.seg;
    }

    function add(edit) {
      edit.id = uid();
      edit.plan = options.planId;
      edits.push(edit);
      changed();
    }
    function sizeOf(kind) { return sizes[kind]; }
    function memberAlong(kind, seg) {
      var pair = partner(lines, seg);
      var a = pair ? pair.a : { x: seg.x0, y: seg.y0 }, b = pair ? pair.b : { x: seg.x1, y: seg.y1 };
      var edit = { op: "add", kind: kind, a: toSheet(a), b: toSheet(b), layer: seg.layer || undefined };
      if (kind === "beam") { edit.w = pair ? Math.round(pair.width * 1000) / 1000 : sizeOf("beam").w; edit.d = sizeOf("beam").d; }
      else edit.t = pair ? Math.round(pair.width * 1000) / 1000 : sizeOf("wall").t;
      return edit;
    }
    function lengthText(edit) {
      return edit.kind === "column" ? (Math.round(edit.w * 1000) + "×" + Math.round(edit.h * 1000)) :
        (Math.hypot(edit.b[0] - edit.a[0], edit.b[1] - edit.a[1]).toFixed(2) + "m" + (edit.kind === "opening" ? " × " + edit.h.toFixed(2) + "m" : ""));
    }
    // Shift: straight across or up from the start
    function ortho(q, e) {
      if (!start || !e || !e.shiftKey) return q;
      return Math.abs(q.x - start.x) >= Math.abs(q.y - start.y) ? { x: q.x, y: start.y } : { x: start.x, y: q.y };
    }
    // the end point a typed length (mm) away from the start, towards the pointer
    function typedEnd() {
      var mm = Number(typed);
      if (!start || !(mm > 0)) return null;
      var dx = lastP ? lastP.x - start.x : 1, dy = lastP ? lastP.y - start.y : 0, len = Math.hypot(dx, dy) || 1;
      if (!lastP || len < 1) { dx = 1; dy = 0; len = 1; }
      return { x: start.x + dx / len * mm / 10, y: start.y + dy / len * mm / 10 };
    }
    // an opening lies on the wall picked first: its points go onto that wall's centre line
    function onWall(p) {
      if (!opening) return p;
      var a = opening.a, b = opening.b, dx = b.x - a.x, dy = b.y - a.y, len2 = dx * dx + dy * dy || 1;
      var t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
      return { x: a.x + dx * t, y: a.y + dy * t };
    }
    function finish(q) {
      if (tool === "measure") {
        var dx = (q.x - start.x) / 100, dy = (q.y - start.y) / 100;
        drawMeasure(start, q);
        status("거리 " + Math.hypot(dx, dy).toFixed(3) + "m (가로 " + Math.abs(dx).toFixed(2) + " · 세로 " + Math.abs(dy).toFixed(2) + ")");
        start = null;
        typed = "";
        return;
      }
      if (Math.hypot(q.x - start.x, q.y - start.y) < 5) return;
      var edit = { op: "add", kind: tool, a: toSheet(start), b: toSheet(q) };
      if (tool === "beam") { edit.w = sizeOf("beam").w; edit.d = sizeOf("beam").d; }
      else if (tool === "opening") { edit.t = opening ? opening.t : sizeOf("wall").t; edit.h = sizeOf("opening").h; if (opening && opening.layer) edit.layer = opening.layer; }
      else edit.t = sizeOf("wall").t;
      start = null;
      opening = null;
      typed = "";
      preview.innerHTML = "";
      add(edit);
      status(LABEL[tool] + " " + lengthText(edit) + "을(를) 넣었어요. 이어서 다음 시작점을 누르세요.");
    }
    function drawMeasure(a, b) {
      var u = unitsPerPx(), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      var text = (Math.hypot(b.x - a.x, b.y - a.y) / 100).toFixed(2) + "m";
      preview.innerHTML = '<line class="qpe-measure" x1="' + a.x.toFixed(1) + '" y1="' + a.y.toFixed(1) + '" x2="' + b.x.toFixed(1) + '" y2="' + b.y.toFixed(1) + '"/>' +
        '<text class="qpe-measure-text" x="' + mx.toFixed(1) + '" y="' + (my - 6 * u).toFixed(1) + '" font-size="' + (13 * u).toFixed(1) + '">' + text + '</text>';
    }

    function onClick(e) {
      var p = at(e);
      if (!p) return;
      if (tool === "view") {
        var picked = e.target.closest && e.target.closest("[data-edit]");
        select(picked ? picked.getAttribute("data-edit") : null);
        if (picked) status(LABEL[(editById(selected) || {}).kind] + "을(를) 골랐어요. 크기를 바꾸거나 지울 수 있어요 (Delete).");
        return;
      }
      if (tool === "erase") {
        var line = pick(p, null);
        if (!line) { status("지울 선을 찾지 못했어요. 선 가까이 누르거나 끌어서 상자로 고르세요."); return; }
        if (line.cls === "removed") { status("이미 빠진 선이에요 (되살리기로 다시 넣을 수 있어요)."); return; }
        var n = eraseSegs(runOf(line));
        status(n ? "선 " + n + "조각을 지웠어요. '다시 읽기'를 누르면 수량에서 빠져요." : "이미 지운 선이에요.");
        return;
      }
      if (tool === "keep") {
        var back = pick(p, null);
        if (!back) { status("되살릴 빨간 점선을 눌러 주세요."); return; }
        var m = keepSegs(runOf(back));
        status(m ? "선 " + m + "조각을 되살렸어요. '다시 읽기'를 누르면 수량에 들어가요." : "자동으로 뺐거나 지운 선이 아니에요.");
        return;
      }
      if (tool === "remove") {
        var hit = e.target.closest && e.target.closest("[data-edit]");
        if (hit) {
          var id = hit.getAttribute("data-edit");
          for (var i = edits.length - 1; i >= 0; i--) if (edits[i].id === id) edits.splice(i, 1);
          status("고친 표시를 지웠어요.");
          changed();
          return;
        }
        var seg = pick(p, ["frame", "beam"]);
        if (!seg) { status("뺄 벽·보 선을 찾지 못했어요. 선 가까이 눌러 주세요."); return; }
        seg = extend(lines, seg);
        var kind = seg.cls === "beam" ? "beam" : "wall";
        var removal = memberAlong(kind, seg);
        removal.op = "remove";
        if (kind === "beam") removal.d = sizeOf("beam").d;
        add(removal);
        status(LABEL[kind] + " " + lengthText(removal) + "을(를) 뺐어요 (수량에서 빠져요).");
        return;
      }
      if (tool === "column") {
        var c = snap(p), col = { op: "add", kind: "column", c: toSheet(c), w: sizeOf("column").w, h: sizeOf("column").h };
        add(col);
        status("기둥 " + lengthText(col) + "을(를) 넣었어요.");
        return;
      }
      if (tool === "opening" && !start) {
        var wall = pick(p, ["frame"]);
        if (!wall) { status("개구부를 낼 벽 선 위를 눌러 주세요."); return; }
        var run = extend(lines, wall), pair = partner(lines, run);
        opening = pair ? { a: pair.a, b: pair.b, t: Math.round(pair.width * 1000) / 1000, layer: wall.layer }
          : { a: { x: run.x0, y: run.y0 }, b: { x: run.x1, y: run.y1 }, t: sizeOf("wall").t, layer: wall.layer };
        start = onWall(snap(p));
        status("개구부 끝점을 누르거나 폭(mm)을 치고 Enter (벽 두께 " + Math.round(opening.t * 1000) + "mm).");
        return;
      }
      if (tool === "beam" || tool === "wall" || tool === "opening" || tool === "measure") {
        var q = tool === "opening" ? onWall(snap(p)) : ortho(snap(p), e);
        if (!start) { start = q; typed = ""; status(tool === "measure" ? "끝점을 누르세요." : LABEL[tool] + " 끝점을 누르거나 길이(mm)를 치고 Enter (Esc 취소)."); return; }
        finish(q);
      }
    }
    function onDouble(e) {
      if (tool !== "beam" && tool !== "wall") return;
      var p = at(e);
      var seg = p && pick(p, null);
      if (!seg) return;
      seg = extend(lines, seg);
      start = null;
      preview.innerHTML = "";
      var edit = memberAlong(tool, seg);
      add(edit);
      status("선을 따라 " + LABEL[tool] + " " + lengthText(edit) + "을(를) 넣었어요" + (edit.layer ? " (레이어 " + edit.layer + ")" : "") + ".");
    }
    function onMove(e) {
      var p = at(e);
      if (!p) return;
      lastP = p;
      if (box) {
        box.x1 = p.x;
        box.y1 = p.y;
        var x = Math.min(box.x0, box.x1), y = Math.min(box.y0, box.y1);
        preview.innerHTML = tool === "mark" ? ellipse(box, "qpe-mark") : '<rect class="qpe-box" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + Math.abs(box.x1 - box.x0).toFixed(1) +
          '" height="' + Math.abs(box.y1 - box.y0).toFixed(1) + '"/>';
        return;
      }
      if (!start || ["beam", "wall", "opening", "measure"].indexOf(tool) < 0) return;
      var q = tool === "opening" ? onWall(snap(p)) : ortho(snap(p), e);
      if (tool === "measure") { drawMeasure(start, q); return; }
      var half = (tool === "beam" ? sizeOf("beam").w : tool === "opening" ? (opening ? opening.t : sizeOf("wall").t) : sizeOf("wall").t) * 50;
      preview.innerHTML = '<polygon class="qpe-ghost' + (tool === "opening" ? " qpe-ghost-opening" : "") + '" points="' + rect(start, q, Math.max(half, 6)) + '"/>';
    }

    // ── a marked spot and the AI's proposals for it ──
    function ellipse(b, cls) {
      var cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2, rx = Math.abs(b.x1 - b.x0) / 2, ry = Math.abs(b.y1 - b.y0) / 2;
      return '<ellipse class="' + cls + '" cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" rx="' + rx.toFixed(1) + '" ry="' + ry.toFixed(1) + '"/>';
    }
    function markBox() {
      var b = mark.box, a = toSheet({ x: Math.min(b.x0, b.x1), y: Math.max(b.y0, b.y1) }), z = toSheet({ x: Math.max(b.x0, b.x1), y: Math.min(b.y0, b.y1) });
      return [a[0], a[1], z[0], z[1]];
    }
    function closeMark() {
      mark = null;
      var panel = bar.querySelector(".qpe-ask");
      if (panel) { panel.hidden = true; panel.innerHTML = ""; }
      if (tool === "mark") preview.innerHTML = "";
    }
    function drawMark() {
      if (!mark) return;
      var shapes = (mark.fixes || []).map(function (f, i) {
        var e = Object.assign({ id: "p" + i }, f), html = shape(e);
        return html.replace(/class="[^"]*"/, 'class="qpe-proposal' + (mark.pick && mark.pick[i] === false ? " is-off" : "") + '"');
      }).join("");
      preview.innerHTML = ellipse(mark.box, "qpe-mark") + shapes;
    }
    // the marked part of the picture as an image (drawn here, from the picture itself)
    function markImage() {
      var b = mark.box, pad = 0.15;
      var x0 = Math.min(b.x0, b.x1), y0 = Math.min(b.y0, b.y1), w = Math.abs(b.x1 - b.x0), h = Math.abs(b.y1 - b.y0);
      var vb = [x0 - w * pad, y0 - h * pad, w * (1 + 2 * pad), h * (1 + 2 * pad)];
      var copy = svg.cloneNode(true);
      copy.setAttribute("viewBox", vb.map(function (v) { return v.toFixed(1); }).join(" "));
      copy.removeAttribute("style");
      copy.querySelectorAll(".qpe-proposal, .qpe-measure, .qpe-measure-text, .qpe-ghost").forEach(function (n) { n.parentNode.removeChild(n); });  // the mark stays
      var width = 1024, height = Math.max(256, Math.min(1024, Math.round(width * vb[3] / vb[2])));
      copy.setAttribute("width", width);
      copy.setAttribute("height", height);
      var style = document.createElementNS("http://www.w3.org/2000/svg", "style");
      style.textContent = PICTURE_STYLE;
      copy.insertBefore(style, copy.firstChild);
      var bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      ["x", "y", "width", "height"].forEach(function (k, i) { bg.setAttribute(k, vb[i]); });
      bg.setAttribute("fill", "#0b1238");
      copy.insertBefore(bg, style.nextSibling);
      var text = new XMLSerializer().serializeToString(copy);
      return new Promise(function (resolve) {
        var img = new Image();
        img.onload = function () {
          var canvas = document.createElement("canvas");
          canvas.width = width;
          canvas.height = height;
          canvas.getContext("2d").drawImage(img, 0, 0, width, height);
          resolve({ image: canvas.toDataURL("image/png"), width: width, height: height });
        };
        img.onerror = function () { resolve({ image: null, width: 0, height: 0 }); };
        img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(text);
      });
    }
    function askPanel(html) {
      var panel = bar.querySelector(".qpe-ask");
      panel.hidden = false;
      panel.innerHTML = html;
      return panel;
    }
    function markWrite() {
      var b = markBox();
      var panel = askPanel('<b>표시한 곳 ' + Math.abs(b[2] - b[0]).toFixed(1) + '×' + Math.abs(b[1] - b[3]).toFixed(1) + 'm</b>' +
        '<textarea data-qpe-ask-text rows="2" placeholder="무엇이 이상한가요? 예: 여기 벽이 빠졌어요 / 이 보는 400×700이에요 / 이 선들은 벽이 아니에요">' + esc(mark.text || "") + '</textarea>' +
        '<div class="qpe-ask-row"><button type="button" class="qd-btn primary" data-qpe-ask="estimate">AI에게 물어보기 (비용 먼저 보기)</button>' +
        '<button type="button" class="qd-btn" data-qpe-ask="close">닫기</button></div>');
      setTimeout(function () { var t = panel.querySelector("textarea"); if (t) t.focus(); }, 0);
    }
    function markRequest(confirm) {
      return markImage().then(function (pic) {
        mark.pic = pic;
        return options.onAsk({ plan: options.planId, box: markBox(), text: mark.text, image: pic.image, width: pic.width, height: pic.height, confirm: !!confirm });
      });
    }
    function money(est) {
      return (est.krw != null ? "약 " + est.krw.toLocaleString() + "원 · " : "") + "토큰 입력 약 " + est.input_tokens.toLocaleString() + " · 출력 최대 약 " + est.output_tokens.toLocaleString() +
        (est.krw == null ? " (단가가 설정되지 않아 금액은 표시하지 않아요)" : "");
    }
    function markAsk(step) {
      if (!mark) return;
      if (step === "close") { closeMark(); status(); return; }
      if (step === "estimate") {
        var field = bar.querySelector("[data-qpe-ask-text]");
        mark.text = field ? field.value.trim() : mark.text;
        if (!mark.text) { status("무엇이 이상한지 적어 주세요."); return; }
        askPanel('<span>보낼 것을 정리하는 중…</span>');
        markRequest(false).then(function (body) {
          if (!body || !body.ok) { askPanel('<span class="qpe-ask-error">' + esc((body && body.error) || "비용을 계산하지 못했어요.") + '</span><div class="qpe-ask-row"><button type="button" class="qd-btn" data-qpe-ask="write">다시</button></div>'); return; }
          var s = body.sending || {};
          askPanel('<b>AI에게 보낼 것</b><span>표시한 곳 그림 1장(약 ' + (s.image_kb || 0) + 'KB) · 그 안의 선 ' + (s.lines || 0) + '개(좌표·레이어) · 적은 글 ' + (s.text || 0) + '자</span>' +
            '<span class="qpe-ask-cost">예상 비용: ' + esc(money(body.estimate)) + '</span>' +
            '<div class="qpe-ask-row"><button type="button" class="qd-btn primary" data-qpe-ask="send">보내기 (유료)</button><button type="button" class="qd-btn" data-qpe-ask="write">글 고치기</button><button type="button" class="qd-btn" data-qpe-ask="close">그만두기</button></div>');
        });
        return;
      }
      if (step === "write") { markWrite(); return; }
      if (step === "send") {
        askPanel('<span>AI가 표시한 곳을 보고 있어요… (1분 안팎)</span>');
        markRequest(true).then(function (body) {
          if (!body || !body.ok) { askPanel('<span class="qpe-ask-error">' + esc((body && body.error) || "AI에게 묻지 못했어요.") + '</span><div class="qpe-ask-row"><button type="button" class="qd-btn" data-qpe-ask="write">다시</button><button type="button" class="qd-btn" data-qpe-ask="close">닫기</button></div>'); return; }
          mark.answer = body.answer || "";
          mark.fixes = body.fixes || [];
          mark.pick = mark.fixes.map(function () { return true; });
          var used = body.usage ? " (쓴 토큰 입력 " + (body.usage.input_tokens || 0).toLocaleString() + " · 출력 " + (body.usage.output_tokens || 0).toLocaleString() + (body.usage.krw != null ? " · 약 " + body.usage.krw.toLocaleString() + "원" : "") + ")" : "";
          showAnswer(used);
          drawMark();
        });
        return;
      }
      if (step === "take") {
        var taken = [];
        (mark.fixes || []).forEach(function (f, i) {
          if (mark.pick[i] === false) return;
          var e = Object.assign({}, f);
          delete e.reason; delete e.by; delete e.id;
          e.id = uid();
          e.plan = options.planId;
          edits.push(e);
          taken.push(e);
        });
        if (options.onAsk) options.onAsk({ plan: options.planId, text: mark.text, answer: mark.answer, accepted: taken, rejected: (mark.fixes || []).length - taken.length });
        closeMark();
        changed();
        status(taken.length ? "AI가 낸 안 " + taken.length + "개를 넣었어요. 선택 도구로 고칠 수 있어요." : "넣은 것이 없어요.");
      }
    }
    function showAnswer(used) {
      var label = { wall: "벽", beam: "보", column: "기둥", opening: "개구부" };
      askPanel('<b>AI 답</b><span>' + esc(mark.answer || "고칠 안이 없어요.") + esc(used || "") + '</span>' +
        (mark.fixes.length ? '<ul class="qpe-ask-list">' + mark.fixes.map(function (f, i) {
          var size = f.kind === "column" ? Math.round(f.w * 1000) + "×" + Math.round(f.h * 1000) : Math.hypot(f.b[0] - f.a[0], f.b[1] - f.a[1]).toFixed(2) + "m";
          return '<li><label><input type="checkbox" data-qpe-pick="' + i + '"' + (mark.pick[i] !== false ? " checked" : "") + '> ' +
            (f.op === "remove" ? "빼기 " : "") + label[f.kind] + ' ' + size + '</label> <small>' + esc(f.reason || "") + '</small></li>';
        }).join("") + '</ul>' : '') +
        '<div class="qpe-ask-row">' + (mark.fixes.length ? '<button type="button" class="qd-btn primary" data-qpe-ask="take">고른 것 넣기</button>' : '') +
        '<button type="button" class="qd-btn" data-qpe-ask="write">다시 묻기</button><button type="button" class="qd-btn" data-qpe-ask="close">버리기</button></div>');
    }

    // erase: a drag draws a box (no panning), a click picks one line
    function onDown(e) {
      down = { x: e.clientX, y: e.clientY, t: Date.now() };
      if ((tool !== "erase" && tool !== "mark") || e.button > 0 || (e.target.closest && e.target.closest("button"))) return;
      var p = at(e);
      if (!p) return;
      e.stopImmediatePropagation();
      box = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
      try { stage.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    }
    stage.addEventListener("pointerdown", onDown, true);
    function onUp(e) {
      if (box) {
        var b = box, moved = down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6;
        box = null;
        preview.innerHTML = "";
        if (moved && tool === "mark") {
          mark = { box: b, text: "" };
          drawMark();
          markWrite();
          status("무엇이 이상한지 적고 'AI에게 물어보기'를 누르세요.");
          return;
        }
        if (tool === "mark") { if (mark) drawMark(); status("이상한 곳을 끌어서 동그라미로 감싸 주세요."); return; }
        if (moved) {
          var picked = inBox(b), n = eraseSegs(picked);
          status(picked.length ? "상자 안 선 " + n + "조각을 지웠어요. '다시 읽기'를 누르면 수량에서 빠져요." : "상자 안에 지울 선이 없어요 (상자에 완전히 들어온 선만 골라요).");
          return;
        }
      }
      if (!down || e.button > 0) return;
      if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return; // a drag pans
      onClick(e);
    }
    stage.addEventListener("pointerup", onUp, true);
    stage.addEventListener("dblclick", onDouble);
    stage.addEventListener("pointermove", onMove);
    function onKey(e) {
      if (!stage.isConnected) return;
      var inField = e.target && /input|textarea|select/i.test(e.target.tagName || "");
      if (e.key === "Escape" && (start || box)) { start = null; box = null; opening = null; typed = ""; preview.innerHTML = ""; status(); return; }
      if (inField) return;
      if ((e.key === "Delete" || e.key === "Backspace") && !start && selected) { e.preventDefault(); deleteSelected(); return; }
      if (!start || ["beam", "wall", "opening", "measure"].indexOf(tool) < 0) return;
      if (/^[0-9]$/.test(e.key)) { typed += e.key; e.preventDefault(); }
      else if (e.key === "Backspace" && typed) { typed = typed.slice(0, -1); e.preventDefault(); }
      else if (e.key === "Enter" && typed) {
        e.preventDefault();
        var end = typedEnd();
        if (end) finish(tool === "opening" ? onWall(end) : end);
        return;
      } else return;
      status(typed ? "길이 " + typed + "mm — Enter로 정해요 (마우스 쪽 방향)." : HINT[tool]);
    }
    document.addEventListener("keydown", onKey);

    bar.addEventListener("click", function (e) {
      var t = e.target.closest("[data-qpe-tool]");
      if (t) { setTool(t.getAttribute("data-qpe-tool")); return; }
      if (e.target.closest("[data-qpe-delete]")) { deleteSelected(); return; }
      var askStep = e.target.closest("[data-qpe-ask]");
      if (askStep) { markAsk(askStep.getAttribute("data-qpe-ask")); return; }
      if (e.target.closest("[data-qpe-reread]")) {
        if (options.onLines) options.onLines(linesState, true);
        return;
      }
      if (e.target.closest("[data-qpe-undo]")) {
        for (var i = edits.length - 1; i >= 0; i--) {
          if (edits[i].plan === options.planId) { edits.splice(i, 1); status("마지막 고친 것을 되돌렸어요."); changed(); return; }
        }
        status("되돌릴 것이 없어요.");
      }
    });
    bar.addEventListener("change", function (e) {
      var pickIndex = e.target.getAttribute("data-qpe-pick");
      if (pickIndex != null && mark) { mark.pick[Number(pickIndex)] = e.target.checked; drawMark(); }
    });
    bar.addEventListener("input", function (e) {
      if (e.target.hasAttribute("data-qpe-ask-text") && mark) { mark.text = e.target.value; return; }
      var prop = e.target.getAttribute("data-prop"), chosen = selected && editById(selected);
      if (prop && chosen) {
        var mm = Number(e.target.value);
        var limit = chosen.kind === "opening" && prop === "h" ? [300, 6000] : [50, 3000];
        if (mm >= limit[0] && mm <= limit[1]) {
          chosen[prop] = mm / 1000;
          draw();
          select(chosen.id);
          if (options.onChange) options.onChange(edits);
        }
        return;
      }
      var key = (e.target.getAttribute("data-size") || "").split(".");
      var value = Number(e.target.value);
      if (key.length === 2 && value >= 50 && value <= 6000) sizes[key[0]][key[1]] = value / 1000;
    });

    setTool("view");
    draw();
    return {
      bar: bar,
      setTool: setTool,
      edits: function () { return edits; },
      refresh: function () { draw(); },
      destroy: function () {
        document.removeEventListener("keydown", onKey);
        stage.removeEventListener("pointerdown", onDown, true);
        stage.removeEventListener("pointerup", onUp, true);
        stage.removeEventListener("dblclick", onDouble);
        stage.removeEventListener("pointermove", onMove);
        stage.classList.remove("qpe-drawing");
        stage.classList.remove("qpe-erasing");
        if (linesLayer.parentNode) linesLayer.parentNode.removeChild(linesLayer);
        if (layer.parentNode) layer.parentNode.removeChild(layer);
        if (preview.parentNode) preview.parentNode.removeChild(preview);
        if (bar.parentNode) bar.parentNode.removeChild(bar);
      }
    };
  }

  // a full-screen editor with its own pan and zoom (used from the 3D review)
  function open(options) {
    var shade = document.createElement("div");
    shade.className = "qpe-modal";
    shade.innerHTML =
      '<div class="qpe-sheet"><header><b>' + esc(options.title || "평면 고치기") + '</b>' +
        '<button type="button" class="qd-btn primary" data-qpe-done>다 고쳤어요</button></header>' +
        '<div class="qpe-slot"></div>' +
        '<div class="qd-floor-stage qpe-stage"><div class="qd-floor-canvas">' + options.svg + '</div>' +
          '<div class="qd-floor-zoom"><button type="button" data-z="in" aria-label="확대">＋</button><button type="button" data-z="out" aria-label="축소">－</button>' +
          '<button type="button" data-z="fit">맞춤</button></div></div></div>';
    // inside the deck, so its colours and sizes apply
    (options.host || document.getElementById("cblQuantityMessengerModal") || document.body).appendChild(shade);
    var stage = shade.querySelector(".qpe-stage");
    var view = { s: 1, x: 0, y: 0 }, drag = null;
    function apply() {
      zoomSvg(stage.querySelector(".qd-floor-canvas svg"), view);
    }
    function zoom(factor, mx, my) {
      var s = Math.min(60, Math.max(0.6, view.s * factor));
      view.x = mx - (mx - view.x) * (s / view.s);
      view.y = my - (my - view.y) * (s / view.s);
      view.s = s;
      apply();
    }
    stage.addEventListener("wheel", function (e) {
      e.preventDefault();
      var r = stage.getBoundingClientRect();
      zoom(e.deltaY < 0 ? 1.18 : 1 / 1.18, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    stage.addEventListener("pointerdown", function (e) {
      if (e.target.closest("button")) return;
      drag = { x: e.clientX - view.x, y: e.clientY - view.y };
      stage.setPointerCapture(e.pointerId);
    });
    stage.addEventListener("pointermove", function (e) {
      if (!drag) return;
      view.x = e.clientX - drag.x;
      view.y = e.clientY - drag.y;
      apply();
    });
    stage.addEventListener("pointerup", function () { drag = null; });
    stage.querySelector(".qd-floor-zoom").addEventListener("click", function (e) {
      var b = e.target.closest("[data-z]");
      if (!b) return;
      var mode = b.getAttribute("data-z");
      if (mode === "fit") { view = fitView(stage.querySelector(".qd-floor-canvas svg")); apply(); return; }
      zoom(mode === "in" ? 1.5 : 1 / 1.5, stage.clientWidth / 2, stage.clientHeight / 2);
    });
    var editor = mount(stage, options);
    shade.querySelector(".qpe-slot").appendChild(editor.bar);
    editor.setTool(options.tool || "beam");
    view = options.focus ? focusView(stage.querySelector(".qd-floor-canvas svg"), options.focus, 8) : fitView(stage.querySelector(".qd-floor-canvas svg"));
    apply();
    if (options.focus) {
      var mark = document.createElementNS("http://www.w3.org/2000/svg", "circle"), H0 = baseBox(stage.querySelector(".qd-floor-canvas svg"))[3];
      mark.setAttribute("class", "qpe-focus");
      mark.setAttribute("cx", options.focus[0] * 100);
      mark.setAttribute("cy", H0 - options.focus[1] * 100);
      mark.setAttribute("r", 60);
      stage.querySelector(".qd-floor-canvas svg").appendChild(mark);
    }
    function close() {
      editor.destroy();
      if (shade.parentNode) shade.parentNode.removeChild(shade);
      document.removeEventListener("keydown", onEsc);
      if (options.onClose) options.onClose(editor.edits());
    }
    function onEsc(e) { if (e.key === "Escape" && e.shiftKey) close(); }
    document.addEventListener("keydown", onEsc);
    shade.querySelector("[data-qpe-done]").addEventListener("click", close);
    return { close: close, editor: editor };
  }

  window.cblQuantityPlanEdit = { mount: mount, open: open, partner: partner, extend: extend, readLines: readLines,
    zoomSvg: zoomSvg, fitView: fitView, focusView: focusView };
})();
