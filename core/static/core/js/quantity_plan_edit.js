/* AI 수량산출 평면 고치기: on a plan picture people add the members the reading missed (a concrete
   beam, a wall, a column) and take out what is not one.

   The picture is an SVG of one plan frame (1 unit = 10 mm, y down); a fix is kept in the sheet's own
   coordinates in metres (x right, y up from the frame's lower left), the coordinates the 3D model and
   the takeoff start from. Points snap to the drawn lines' ends and onto the lines; double-clicking a
   line puts the member along it (between the line and its parallel partner when there is one).

   window.cblQuantityPlanEdit.mount(stage, options) → controller
     stage: an element holding the plan <svg> (pan/zoom is the caller's)
     options: { planId, edits: shared array of every plan's fixes, onChange(edits), note: text,
                sizes: { beam: {w, d}, wall: {t}, column: {w, h} } (metres) }
   window.cblQuantityPlanEdit.open(options) → a full-screen editor with its own pan/zoom:
     options as above plus { svg, title, onClose(edits) } */
(function () {
  "use strict";

  var SNAP_PX = 10, PICK_PX = 8, PAIR_MAX = 1.3, PAIR_MIN = 0.05; // metres for pairs
  var LABEL = { beam: "보", wall: "벽", column: "기둥" };
  var TOOLS = [["view", "보기"], ["beam", "보 추가"], ["wall", "벽 추가"], ["column", "기둥 추가"], ["remove", "지우기"]];
  var HINT = {
    view: "드래그로 옮기고 휠로 확대해요. 고칠 도구를 골라 주세요.",
    beam: "보 추가: 시작점과 끝점을 누르세요. 선을 두 번 누르면 그 선을 따라 넣어요.",
    wall: "벽 추가: 시작점과 끝점을 누르세요. 벽 선을 두 번 누르면 그 벽을 따라 넣어요.",
    column: "기둥 추가: 기둥 가운데를 누르세요.",
    remove: "지우기: 잘못 잡힌 벽·보 선이나 고친 표시를 누르세요."
  };

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
      var layer = path.getAttribute("data-layer") || "";
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
    var tool = "view", start = null, down = null;

    // sheet metres ↔ picture units
    function toSheet(p) { return [Math.round(p.x) / 100, Math.round(H - p.y) / 100]; }
    function toPicture(m) { return { x: m[0] * 100, y: H - m[1] * 100 }; }

    var layer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    layer.setAttribute("class", "qpe-layer");
    svg.appendChild(layer);
    var preview = document.createElementNS("http://www.w3.org/2000/svg", "g");
    preview.setAttribute("class", "qpe-preview");
    svg.appendChild(preview);

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
      '</div>' +
      '<p class="qpe-hint"></p><p class="qpe-count"></p><div class="qpe-learn" hidden></div>';
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
      bar.querySelector(".qpe-count").textContent = (list.length ? "이 평면에서 고친 것 " + list.length + "건 (추가 " + added + " · 지움 " + (list.length - added) + ")" : "아직 고친 것이 없어요") +
        (options.note ? " · " + options.note : "");
    }
    function setTool(name) {
      tool = name;
      start = null;
      preview.innerHTML = "";
      bar.querySelectorAll("[data-qpe-tool]").forEach(function (b) { b.classList.toggle("is-on", b.getAttribute("data-qpe-tool") === name); });
      bar.querySelectorAll(".qpe-sizes [data-for]").forEach(function (l) { l.hidden = l.getAttribute("data-for") !== name; });
      stage.classList.toggle("qpe-drawing", name !== "view");
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
      var cls = "qpe-" + e.op + " qpe-" + e.kind;
      if (e.kind === "column") {
        var c = toPicture(e.c), w = e.w * 50, h = e.h * 50;
        return '<rect data-edit="' + esc(e.id) + '" class="' + cls + '" x="' + (c.x - w).toFixed(1) + '" y="' + (c.y - h).toFixed(1) +
          '" width="' + (2 * w).toFixed(1) + '" height="' + (2 * h).toFixed(1) + '"/>';
      }
      var a = toPicture(e.a), b = toPicture(e.b), width = (e.kind === "beam" ? e.w : e.t) * 50;
      return '<polygon data-edit="' + esc(e.id) + '" class="' + cls + '" points="' + rect(a, b, Math.max(width, 6)) + '"/>' +
        (e.op === "remove" ? '<line class="qpe-cross" x1="' + a.x.toFixed(1) + '" y1="' + a.y.toFixed(1) + '" x2="' + b.x.toFixed(1) + '" y2="' + b.y.toFixed(1) + '"/>' : "");
    }
    function draw() {
      layer.innerHTML = mine().map(shape).join("");
      count();
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
        (Math.hypot(edit.b[0] - edit.a[0], edit.b[1] - edit.a[1]).toFixed(2) + "m");
    }

    function onClick(e) {
      var p = at(e);
      if (!p) return;
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
        if (!seg) { status("지울 벽·보 선을 찾지 못했어요. 선 가까이 눌러 주세요."); return; }
        seg = extend(lines, seg);
        var kind = seg.cls === "beam" ? "beam" : "wall";
        var removal = memberAlong(kind, seg);
        removal.op = "remove";
        if (kind === "beam") removal.d = sizeOf("beam").d;
        add(removal);
        status(LABEL[kind] + " " + lengthText(removal) + "을(를) 지웠어요 (수량에서 빠져요).");
        return;
      }
      if (tool === "column") {
        var c = snap(p), col = { op: "add", kind: "column", c: toSheet(c), w: sizeOf("column").w, h: sizeOf("column").h };
        add(col);
        status("기둥 " + lengthText(col) + "을(를) 넣었어요.");
        return;
      }
      if (tool === "beam" || tool === "wall") {
        var q = snap(p);
        if (!start) { start = q; status(LABEL[tool] + " 끝점을 누르세요 (Esc 취소)."); return; }
        if (Math.hypot(q.x - start.x, q.y - start.y) < 5) return;
        var edit = { op: "add", kind: tool, a: toSheet(start), b: toSheet(q) };
        if (tool === "beam") { edit.w = sizeOf("beam").w; edit.d = sizeOf("beam").d; } else edit.t = sizeOf("wall").t;
        start = null;
        preview.innerHTML = "";
        add(edit);
        status(LABEL[tool] + " " + lengthText(edit) + "을(를) 넣었어요. 이어서 다음 시작점을 누르세요.");
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
      if (!start || (tool !== "beam" && tool !== "wall")) return;
      var p = at(e);
      if (!p) return;
      var q = snap(p), half = (tool === "beam" ? sizeOf("beam").w : sizeOf("wall").t) * 50;
      preview.innerHTML = '<polygon class="qpe-ghost" points="' + rect(start, q, Math.max(half, 6)) + '"/>';
    }

    stage.addEventListener("pointerdown", function (e) { down = { x: e.clientX, y: e.clientY, t: Date.now() }; }, true);
    function onUp(e) {
      if (tool === "view" || !down || e.button > 0) return;
      if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return; // a drag pans
      onClick(e);
    }
    stage.addEventListener("pointerup", onUp, true);
    stage.addEventListener("dblclick", onDouble);
    stage.addEventListener("pointermove", onMove);
    function onKey(e) {
      if (e.key === "Escape" && start) { start = null; preview.innerHTML = ""; status(); }
    }
    document.addEventListener("keydown", onKey);

    bar.addEventListener("click", function (e) {
      var t = e.target.closest("[data-qpe-tool]");
      if (t) { setTool(t.getAttribute("data-qpe-tool")); return; }
      if (e.target.closest("[data-qpe-undo]")) {
        for (var i = edits.length - 1; i >= 0; i--) {
          if (edits[i].plan === options.planId) { edits.splice(i, 1); status("마지막 고친 것을 되돌렸어요."); changed(); return; }
        }
        status("되돌릴 것이 없어요.");
      }
    });
    bar.addEventListener("input", function (e) {
      var key = (e.target.getAttribute("data-size") || "").split(".");
      var value = Number(e.target.value);
      if (key.length === 2 && value >= 50 && value <= 3000) sizes[key[0]][key[1]] = value / 1000;
    });

    setTool("view");
    draw();
    return {
      bar: bar,
      setTool: setTool,
      edits: function () { return edits; },
      destroy: function () {
        document.removeEventListener("keydown", onKey);
        stage.removeEventListener("pointerup", onUp, true);
        stage.removeEventListener("dblclick", onDouble);
        stage.removeEventListener("pointermove", onMove);
        stage.classList.remove("qpe-drawing");
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
    view = fitView(stage.querySelector(".qd-floor-canvas svg"));
    apply();
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
    zoomSvg: zoomSvg, fitView: fitView };
})();
