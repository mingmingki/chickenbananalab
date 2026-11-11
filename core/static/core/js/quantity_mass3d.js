/* AI 수량산출 3D: buildings stacked from the structural plans (walls, columns, slabs, beams),
   then rebar lines and formwork panels laid on them. Needs window.THREE (r128).
   window.cblQuantityMass3D.mount(container, model) → controller. */
(function () {
  "use strict";

  var COLOR = {
    wall: 0xfacc15, col: 0xeab308, slab: 0xe2e8f0, beam: 0xfb923c, rebar: 0xef4444,
    alWall: 0x38bdf8, gang: 0xa78bfa, euro: 0x2dd4bf, alSlab: 0x7dd3fc, beamForm: 0xfbbf24
  };
  var SPACING = 0.3;     // rebar drawing spacing (m), a sketch of the bars, not their count
  var BEAM_DEPTH = 0.45; // beams hang this far below the slab in the sketch

  function slabThickness(level) { return level.key.charAt(0) === "B" ? 0.25 : 0.21; }

  function addQuad(out, ax, az, bx, bz, y0, y1) {
    out.push(ax, y0, az, bx, y0, bz, bx, y1, bz, ax, y0, az, bx, y1, bz, ax, y1, az);
  }
  function addFlat(out, x0, z0, x1, z1, y) {
    out.push(x0, y, z0, x1, y, z0, x1, y, z1, x0, y, z0, x1, y, z1, x0, y, z1);
  }
  function mesh(THREE, positions, color, opacity) {
    var geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.computeVertexNormals();
    var material = new THREE.MeshStandardMaterial({
      color: color, roughness: 0.65, metalness: 0.05, side: THREE.DoubleSide,
      transparent: opacity < 1, opacity: opacity, depthWrite: opacity >= 0.6,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1
    });
    var m = new THREE.Mesh(geometry, material);
    m.userData.baseOpacity = opacity;
    return m;
  }
  function lines(THREE, positions, color, opacity) {
    var geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    return new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({
      color: color, transparent: true, opacity: opacity == null ? 0.9 : opacity
    }));
  }

  // spans of a horizontal (axis 'x') or vertical ('y') line through slab rectangles
  function spans(rects, axis, at) {
    var out = [];
    rects.forEach(function (r) {
      if (axis === "x" ? (r[1] <= at && at < r[3]) : (r[0] <= at && at < r[2])) {
        out.push(axis === "x" ? [r[0], r[2]] : [r[1], r[3]]);
      }
    });
    out.sort(function (a, b) { return a[0] - b[0]; });
    var merged = [];
    out.forEach(function (s) {
      var last = merged[merged.length - 1];
      if (last && s[0] <= last[1] + 0.01) last[1] = Math.max(last[1], s[1]);
      else merged.push([s[0], s[1]]);
    });
    return merged;
  }

  // The plan of floor N draws the walls standing on N and the slab and beams of floor N, so the
  // top of storey N comes from the plan of N+1 ("N층 = N층 수직재 + N+1층 바닥 수평재"). When that
  // plan's outline is open (a roof sheet) or set back, the storey's own outline covers it.
  function topPlans(b, index) {
    var own = b.plans[b.levels[index].plan];
    var next = index + 1 < b.levels.length ? b.plans[b.levels[index + 1].plan] : null;
    if (!next) return null;
    var ownArea = own && !own.slab_approximate ? (own.slab_area || 0) : 0;
    var nextOk = !next.slab_approximate && (next.slab || []).length;
    if (ownArea && (!nextOk || next.slab_area < 0.9 * ownArea)) return { slab: own, beams: next };
    return { slab: next, beams: next };
  }

  function slabBounds(plan) {
    var xs = [], ys = [];
    (plan.slab || []).forEach(function (r) { xs.push(r[0], r[2]); ys.push(r[1], r[3]); });
    (plan.slab_polygon || []).forEach(function (p) { xs.push(p[0]); ys.push(p[1]); });
    (plan.walls || []).forEach(function (w) { xs.push(w[0], w[2]); ys.push(w[1], w[3]); });
    if (!xs.length) return null;
    return [Math.min.apply(null, xs), Math.min.apply(null, ys), Math.max.apply(null, xs), Math.max.apply(null, ys)];
  }

  function mount(container, model, options) {
    var THREE = window.THREE;
    options = options || {};
    var width = container.clientWidth || 800, height = container.clientHeight || 480;
    var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height);
    container.appendChild(renderer.domElement);
    renderer.localClippingEnabled = true;
    var scene = new THREE.Scene();
    var perspective = new THREE.PerspectiveCamera(38, width / height, 0.5, 5000);
    var orthographic = new THREE.OrthographicCamera(-50, 50, 50, -50, -5000, 5000);
    var camera = perspective;
    var clipPlanes = [];  // section planes, shared by every material
    scene.add(new THREE.HemisphereLight(0xffffff, 0x334155, 0.95));
    var sun = new THREE.DirectionalLight(0xffffff, 0.75);
    sun.position.set(60, 120, 40);
    scene.add(sun);

    var buildings = [];
    var offset = 0;
    (model.buildings || []).forEach(function (b) {
      var bounds = null;
      b.levels.forEach(function (l) {
        var bb = slabBounds(b.plans[l.plan]);
        if (!bb) return;
        bounds = bounds ? [Math.min(bounds[0], bb[0]), Math.min(bounds[1], bb[1]), Math.max(bounds[2], bb[2]), Math.max(bounds[3], bb[3])] : bb;
      });
      if (!bounds) return;
      var group = new THREE.Group();
      group.position.set(offset - bounds[0], 0, bounds[3]); // plan y → -z, the plan's top edge at z = 0
      offset += (bounds[2] - bounds[0]) + 18;
      scene.add(group);
      var entry = { name: b.name, data: b, group: group, levels: [], bounds: bounds };
      b.levels.forEach(function (level, index) {
        var plan = b.plans[level.plan];
        var above = topPlans(b, index);
        var slabPlan = above ? above.slab : null;
        var t = slabThickness(level);
        var top = level.elev + level.height;
        var walls = [], cols = [], slab = [], beams = [];
        plan.walls.forEach(function (w) { addQuad(walls, w[0], -w[1], w[2], -w[3], level.elev, top - t); });
        (plan.cols || []).forEach(function (c) {
          for (var i = 0; i < c.length; i++) {
            var a = c[i], z = c[(i + 1) % c.length];
            addQuad(cols, a[0], -a[1], z[0], -z[1], level.elev, top - t);
          }
        });
        ((slabPlan && slabPlan.slab) || []).forEach(function (r) {
          addFlat(slab, r[0], -r[1], r[2], -r[3], top);
          addFlat(slab, r[0], -r[1], r[2], -r[3], top - t);
        });
        if (index === 0) (plan.slab || []).forEach(function (r) { addFlat(slab, r[0], -r[1], r[2], -r[3], level.elev); });
        ((above && above.beams.beams) || []).forEach(function (s) { addQuad(beams, s[0], -s[1], s[2], -s[3], top - t - BEAM_DEPTH, top - t); });
        var levelGroup = new THREE.Group();
        var massMeshes = [];
        function kind(m, k) { m.userData.kind = k; return m; }
        if (walls.length) massMeshes.push(kind(mesh(THREE, walls, COLOR.wall, 0.92), "wall"));
        if (cols.length) massMeshes.push(kind(mesh(THREE, cols, COLOR.col, 0.95), "col"));
        if (slab.length) massMeshes.push(kind(mesh(THREE, slab, COLOR.slab, 0.8), "slab"));
        var polygonPlan = slabPlan && slabPlan.slab_polygon ? slabPlan : (index === 0 && plan.slab_polygon ? plan : null);
        if (polygonPlan && polygonPlan.slab_polygon.length >= 3) {
          var shape = new THREE.Shape(polygonPlan.slab_polygon.map(function (p) { return new THREE.Vector2(p[0], p[1]); }));
          var geometry = new THREE.ShapeGeometry(shape);
          geometry.rotateX(-Math.PI / 2); // shape y → -z
          var flat = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: COLOR.slab, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false }));
          flat.position.y = polygonPlan === plan && !slabPlan ? level.elev : top;
          flat.userData.baseOpacity = 0.16;
          flat.userData.kind = "slab";
          massMeshes.push(flat);
        }
        if (beams.length) massMeshes.push(kind(mesh(THREE, beams, COLOR.beam, 0.9), "beam"));
        massMeshes.forEach(function (m) { levelGroup.add(m); });
        group.add(levelGroup);
        entry.levels.push({ index: index, data: level, plan: plan, top: above, group: levelGroup, mass: massMeshes, rebar: null, form: null });
      });
      buildings.push(entry);
    });

    // ── formwork & rebar, built on demand ──
    function formFor(entry, lv) {
      if (lv.form) return lv.form;
      var level = lv.data, plan = lv.plan, t = slabThickness(level), top = level.elev + level.height;
      var basement = level.key.charAt(0) === "B";
      var inner = [], outer = [], soffit = [], beamForm = [];
      var area = { wall: 0, gang: 0, slab: 0, beam: 0 };
      plan.walls.forEach(function (w) {
        var len = Math.hypot(w[2] - w[0], w[3] - w[1]);
        var face = len * (level.height - t);
        if (w[4] && !basement) { addQuad(outer, w[0], -w[1], w[2], -w[3], level.elev, top - t); area.gang += face; }
        else { addQuad(inner, w[0], -w[1], w[2], -w[3], level.elev, top - t); area.wall += face; }
      });
      var slabPlan = lv.top ? lv.top.slab : null, beamPlan = lv.top ? lv.top.beams : null;
      ((slabPlan && slabPlan.slab) || []).forEach(function (r) {
        addFlat(soffit, r[0], -r[1], r[2], -r[3], top - t - 0.01);
        area.slab += (r[2] - r[0]) * (r[3] - r[1]);
      });
      if (slabPlan && !(slabPlan.slab || []).length && slabPlan.slab_area) area.slab = slabPlan.slab_area;
      ((beamPlan && beamPlan.beams) || []).forEach(function (s) {
        addQuad(beamForm, s[0], -s[1], s[2], -s[3], top - t - BEAM_DEPTH, top - t);
        area.beam += Math.hypot(s[2] - s[0], s[3] - s[1]) * BEAM_DEPTH;
      });
      var group = new THREE.Group();
      if (inner.length) group.add(mesh(THREE, inner, basement ? COLOR.euro : COLOR.alWall, 0.55));
      if (outer.length) group.add(mesh(THREE, outer, COLOR.gang, 0.7));
      if (soffit.length) group.add(mesh(THREE, soffit, basement ? COLOR.euro : COLOR.alSlab, 0.5));
      if (beamForm.length) group.add(mesh(THREE, beamForm, COLOR.beamForm, 0.6));
      useClip(group);
      lv.group.add(group);
      lv.form = { group: group, area: area, basement: basement };
      return lv.form;
    }

    function rebarFor(lv) {
      if (lv.rebar) return lv.rebar;
      var level = lv.data, plan = lv.plan, t = slabThickness(level), top = level.elev + level.height;
      var pos = [];
      var bottom = level.elev + 0.05, ceiling = top - t - 0.05;
      plan.walls.forEach(function (w) {
        var dx = w[2] - w[0], dy = w[3] - w[1], len = Math.hypot(dx, dy);
        if (len < 0.2) return;
        var nx = dy / len * 0.06, ny = -dx / len * 0.06; // a little inside the face
        for (var s = 0.1; s < len; s += SPACING) {
          var x = w[0] + dx * s / len + nx, y = w[1] + dy * s / len + ny;
          pos.push(x, bottom, -y, x, ceiling, -y);
        }
        for (var h = bottom + 0.1; h < ceiling; h += SPACING) {
          pos.push(w[0] + nx, h, -(w[1] + ny), w[2] + nx, h, -(w[3] + ny));
        }
      });
      var wallEnd = pos.length;
      var slabPlan = lv.top ? lv.top.slab : null;
      var rects = (slabPlan && slabPlan.slab) || [];
      if (rects.length) {
        var bb = slabBounds(slabPlan);
        [top - 0.04, top - t + 0.04].forEach(function (y) {
          for (var at = bb[1] + 0.15; at < bb[3]; at += SPACING) {
            spans(rects, "x", at).forEach(function (s) { pos.push(s[0], y, -at, s[1], y, -at); });
          }
          for (var ax = bb[0] + 0.15; ax < bb[2]; ax += SPACING) {
            spans(rects, "y", ax).forEach(function (s) { pos.push(ax, y, -s[0], ax, y, -s[1]); });
          }
        });
      }
      ((lv.top && lv.top.beams.beams) || []).forEach(function (s) {
        [top - t - 0.06, top - t - BEAM_DEPTH + 0.06].forEach(function (y) { pos.push(s[0], y, -s[1], s[2], y, -s[3]); });
      });
      var segment = new THREE.Group();
      segment.add(lines(THREE, pos.slice(0, wallEnd), COLOR.rebar, 0.9));
      segment.add(lines(THREE, pos.slice(wallEnd), 0xf87171, 0.45));
      useClip(segment);
      lv.group.add(segment);
      lv.rebar = { object: segment, bars: pos.length / 6 };
      return lv.rebar;
    }

    // ── view state ──
    var state = { mode: "mass", building: null, level: null, kinds: {} }; // level: index in the chosen building, null = all
    function visibleEntries() {
      return buildings.filter(function (b) { return !state.building || b.name === state.building; });
    }
    function apply() {
      buildings.forEach(function (b) {
        var shown = !state.building || b.name === state.building;
        b.group.visible = shown;
        b.levels.forEach(function (lv, i) {
          var focus = state.level == null || (shown && state.building === b.name && i === state.level);
          var above = state.building === b.name && state.level != null && i > state.level;
          lv.group.visible = !above;
          var faint = state.mode !== "mass" || (state.level != null && !focus);
          lv.mass.forEach(function (m) {
            m.visible = state.kinds[m.userData.kind] !== false;
            m.material.opacity = faint ? Math.min(0.14, m.userData.baseOpacity) : m.userData.baseOpacity;
            m.material.transparent = true;
            m.material.depthWrite = !faint && m.userData.baseOpacity >= 0.6;
          });
          var wantRebar = state.mode === "rebar" && shown && (state.level == null ? i === 0 && false : i === state.level);
          if (wantRebar) rebarFor(lv);
          if (lv.rebar) lv.rebar.object.visible = wantRebar;
          var wantForm = state.mode === "form" && shown && (state.level == null || i === state.level);
          if (wantForm) formFor(b, lv);
          if (lv.form) lv.form.group.visible = wantForm;
        });
      });
      render();
    }

    // ── camera: drag to orbit, right/shift-drag to pan, wheel or pinch to zoom ──
    var orbit = { theta: -0.75, phi: 1.05, radius: 120, target: new THREE.Vector3() };
    function place() {
      var r = orbit.radius;
      camera.up.set(0, 1, 0);
      if (orbit.phi < 0.01) camera.up.set(-Math.cos(orbit.theta), 0, -Math.sin(orbit.theta)); // looking straight down
      camera.position.set(orbit.target.x + r * Math.sin(orbit.phi) * Math.cos(orbit.theta),
                          orbit.target.y + r * Math.cos(orbit.phi),
                          orbit.target.z + r * Math.sin(orbit.phi) * Math.sin(orbit.theta));
      if (camera === orthographic) {
        var aspect = (container.clientWidth || 800) / (container.clientHeight || 480);
        var half = orbit.radius * Math.tan(perspective.fov * Math.PI / 360);
        orthographic.left = -half * aspect; orthographic.right = half * aspect;
        orthographic.top = half; orthographic.bottom = -half;
        orthographic.updateProjectionMatrix();
      }
      camera.lookAt(orbit.target);
    }
    function fit() {
      scene.updateMatrixWorld(true); // boxes are read from world matrices; a new group has none yet
      var box = new THREE.Box3();
      visibleEntries().forEach(function (b) {
        b.levels.forEach(function (lv, i) {
          if (!lv.group.visible) return;
          if (state.level != null && state.mode !== "mass" && state.building === b.name && i !== state.level) return;
          box.expandByObject(lv.group);
        });
      });
      if (box.isEmpty()) return;
      var size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
      orbit.target.copy(center);
      var vfov = perspective.fov * Math.PI / 180;
      var hfov = 2 * Math.atan(Math.tan(vfov / 2) * perspective.aspect);
      orbit.radius = (size.length() / 2) / Math.sin(Math.min(vfov, hfov) / 2) * 0.8;
      place();
      render();
    }
    var frame = 0;
    function render() {
      if (frame) return;
      frame = window.requestAnimationFrame(function () { frame = 0; renderer.render(scene, camera); });
      if (document.hidden) { window.cancelAnimationFrame(frame); frame = 0; renderer.render(scene, camera); }
    }
    var el = renderer.domElement, drag = null, pinch = null;
    el.style.touchAction = "none";
    el.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    el.addEventListener("pointerdown", function (e) {
      el.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey, id: e.pointerId };
    });
    el.addEventListener("pointermove", function (e) {
      if (!drag || drag.id !== e.pointerId) return;
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.x = e.clientX; drag.y = e.clientY;
      if (drag.pan) {
        var scale = orbit.radius / 600;
        var right = new THREE.Vector3().subVectors(camera.position, orbit.target).cross(camera.up).normalize();
        orbit.target.addScaledVector(right, dx * scale);
        orbit.target.addScaledVector(camera.up, dy * scale);
      } else {
        orbit.theta += dx * 0.008;
        orbit.phi = Math.min(Math.PI / 2, Math.max(0.001, orbit.phi - dy * 0.008));
        markView(null);
      }
      place(); render();
    });
    el.addEventListener("pointerup", function () { drag = null; });
    el.addEventListener("wheel", function (e) {
      e.preventDefault();
      orbit.radius = Math.min(3000, Math.max(5, orbit.radius * (e.deltaY > 0 ? 1.12 : 1 / 1.12)));
      place(); render();
    }, { passive: false });
    el.addEventListener("touchstart", function (e) {
      if (e.touches.length === 2) {
        drag = null;
        pinch = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
      }
    }, { passive: true });
    el.addEventListener("touchmove", function (e) {
      if (e.touches.length === 2 && pinch) {
        var d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
        orbit.radius = Math.min(3000, Math.max(5, orbit.radius * pinch / d));
        pinch = d; place(); render();
      }
    }, { passive: true });

    function resize() {
      var w = container.clientWidth, h = container.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h);
      perspective.aspect = w / h;
      perspective.updateProjectionMatrix();
      place();
      render();
    }
    var observer = window.ResizeObserver ? new ResizeObserver(resize) : null;
    if (observer) observer.observe(container);

    // ── tools: views, sections, section sheet, member filter, measure ──
    function useClip(object) {
      object.traverse(function (o) {
        if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(function (mat) { mat.clippingPlanes = clipPlanes; });
      });
    }
    useClip(scene);

    var worldBox = new THREE.Box3();
    scene.updateMatrixWorld(true);
    buildings.forEach(function (b) { worldBox.expandByObject(b.group); });
    if (worldBox.isEmpty()) worldBox.set(new THREE.Vector3(-10, 0, -10), new THREE.Vector3(10, 10, 10));
    var AXES = { x: "좌우(X)", y: "높이(Y)", z: "앞뒤(Z)" };
    var section = { x: { on: false, value: 0, flip: false }, y: { on: false, value: 0, flip: false }, z: { on: false, value: 0, flip: false } };
    var sbox = { on: false, x: [worldBox.min.x, worldBox.max.x], y: [worldBox.min.y, worldBox.max.y], z: [worldBox.min.z, worldBox.max.z] };
    ["x", "y", "z"].forEach(function (a) { section[a].value = (worldBox.min[a] + worldBox.max[a]) / 2; });
    (function () { var middle = wallCenter(); if (middle) { section.x.value = middle.x; section.z.value = middle.z; } })();
    function visibleBox() {
      scene.updateMatrixWorld(true);
      var box = new THREE.Box3();
      visibleEntries().forEach(function (b) { box.expandByObject(b.group); });
      return box.isEmpty() ? worldBox : box;
    }
    // where the walls are (the median of their midpoints), so a stray far line does not pull a new cut off the building
    function wallCenter() {
      var xs = [], zs = [];
      visibleEntries().forEach(function (b) {
        var gx = b.group.position.x, gz = b.group.position.z;
        b.levels.forEach(function (lv) {
          lv.plan.walls.forEach(function (w) { xs.push(gx + (w[0] + w[2]) / 2); zs.push(gz - (w[1] + w[3]) / 2); });
        });
      });
      if (!xs.length) return null;
      function median(v) { v.sort(function (m, n) { return m - n; }); return v[v.length >> 1]; }
      return { x: median(xs), z: median(zs) };
    }
    function resetRanges() {
      var box = visibleBox(), middle = wallCenter();
      ["x", "y", "z"].forEach(function (a) {
        var lo = box.min[a], hi = box.max[a];
        section[a].value = middle && a !== "y" ? middle[a] : (lo + hi) / 2;
        if (!sbox.on) sbox[a] = [lo, hi];
        if (!ui) return;
        ui.querySelectorAll('[data-cut="' + a + '"], [data-boxlo="' + a + '"], [data-boxhi="' + a + '"]').forEach(function (input) {
          input.min = lo.toFixed(2); input.max = hi.toFixed(2);
        });
        ui.querySelector('[data-cut="' + a + '"]').value = section[a].value.toFixed(2);
        ui.querySelector('[data-boxlo="' + a + '"]').value = sbox[a][0].toFixed(2);
        ui.querySelector('[data-boxhi="' + a + '"]').value = sbox[a][1].toFixed(2);
      });
      updateClip();
    }
    var capGroup = new THREE.Group();
    scene.add(capGroup);
    var CAP = { wall: 0xd97706, col: 0xb45309, slab: 0xcbd5e1, beam: 0xea580c };

    // a section turned on keeps the far side, so its cut face looks at the camera
    function sectionOn(axis, on) {
      if (on && !section[axis].on) section[axis].flip = camera.position[axis] < section[axis].value;
      section[axis].on = on;
    }
    function updateClip() {
      var before = clipPlanes.length;
      clipPlanes.length = 0;
      ["x", "y", "z"].forEach(function (a) {
        var s = section[a];
        if (!s.on) return;
        var n = new THREE.Vector3(a === "x" ? 1 : 0, a === "y" ? 1 : 0, a === "z" ? 1 : 0);
        if (!s.flip) n.negate();  // keep the near side (x, y or z below the cut)
        clipPlanes.push(new THREE.Plane(n, s.flip ? -s.value : s.value));
      });
      if (sbox.on) {
        ["x", "y", "z"].forEach(function (a) {
          var lo = new THREE.Vector3(a === "x" ? 1 : 0, a === "y" ? 1 : 0, a === "z" ? 1 : 0);
          clipPlanes.push(new THREE.Plane(lo, -sbox[a][0]));
          clipPlanes.push(new THREE.Plane(lo.clone().negate(), sbox[a][1]));
        });
      }
      if (clipPlanes.length !== before) {
        scene.traverse(function (o) {
          if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(function (mat) { mat.needsUpdate = true; });
        });
      }
      buildCaps();
      render();
    }

    // where a plan line (x0, y0, x1, y1) crosses plan x = p (axis "x") or plan y = p (axis "y")
    function crossing(line, axis, p) {
      var a0 = axis === "x" ? line[0] : line[1], a1 = axis === "x" ? line[2] : line[3];
      var b0 = axis === "x" ? line[1] : line[0], b1 = axis === "x" ? line[3] : line[2];
      if (a0 === a1 || (a0 - p) * (a1 - p) > 0) return null;
      return b0 + (b1 - b0) * (p - a0) / (a1 - a0);
    }
    function pairs(values, min, max) {
      values.sort(function (a, b) { return a - b; });
      var out = [];
      for (var i = 0; i + 1 < values.length;) {
        var gap = values[i + 1] - values[i];
        if (gap >= min && gap <= max) { out.push([values[i], values[i + 1]]); i += 2; } else { i += 1; }
      }
      return out;
    }

    // members cut by a vertical plane: world x = value (axis "x") or world z = value (axis "z").
    // u is the horizontal world coordinate along the cut (z for an x cut, x for a z cut).
    function cutRects(axis, value) {
      var out = [];
      visibleEntries().forEach(function (b) {
        var gx = b.group.position.x, gz = b.group.position.z;
        var planAxis = axis === "x" ? "x" : "y";
        var p = axis === "x" ? value - gx : gz - value;
        function toU(range) {
          return axis === "x" ? [gz - range[1], gz - range[0]] : [gx + range[0], gx + range[1]];
        }
        b.levels.forEach(function (lv) {
          if (!lv.group.visible) return;
          var level = lv.data, t = slabThickness(level), top = level.elev + level.height;
          function add(range, y0, y1, kind, approx) {
            if (state.kinds[kind] === false) return;
            var u = toU(range);
            out.push({ u0: u[0], u1: u[1], y0: y0, y1: y1, kind: kind, building: b.name, approx: !!approx });
          }
          // a slab read as rectangles, else as its outline polygon (an approximate slab)
          function slabSpans(slabPlan, y0, y1) {
            if (!slabPlan) return;
            if ((slabPlan.slab || []).length) {
              spans(slabPlan.slab, planAxis === "x" ? "y" : "x", p).forEach(function (r) { add(r, y0, y1, "slab"); });
              return;
            }
            var poly = slabPlan.slab_polygon || [], hs = [];
            for (var i = 0; i < poly.length; i++) {
              var a = poly[i], z = poly[(i + 1) % poly.length];
              var c = crossing([a[0], a[1], z[0], z[1]], planAxis, p);
              if (c != null) hs.push(c);
            }
            hs.sort(function (m, n) { return m - n; });
            for (var k = 0; k + 1 < hs.length; k += 2) add([hs[k], hs[k + 1]], y0, y1, "slab", true);
          }
          var hits = [];
          lv.plan.walls.forEach(function (w) { var c = crossing(w, planAxis, p); if (c != null) hits.push(c); });
          pairs(hits, 0.08, 1.0).forEach(function (r) { add(r, level.elev, top - t, "wall"); });
          (lv.plan.cols || []).forEach(function (poly) {
            var hs = [];
            for (var i = 0; i < poly.length; i++) {
              var a = poly[i], z = poly[(i + 1) % poly.length];
              var c = crossing([a[0], a[1], z[0], z[1]], planAxis, p);
              if (c != null) hs.push(c);
            }
            if (hs.length >= 2) add([Math.min.apply(null, hs), Math.max.apply(null, hs)], level.elev, top - t, "col");
          });
          slabSpans(lv.top ? lv.top.slab : null, top - t, top);
          if (lv.index === 0) slabSpans(lv.plan, level.elev - t, level.elev);
          var beamPlan = lv.top ? lv.top.beams : null;
          if (beamPlan && beamPlan.beams) {
            var bh = [];
            beamPlan.beams.forEach(function (s) { var c = crossing(s, planAxis, p); if (c != null) bh.push(c); });
            pairs(bh, 0.15, 1.3).forEach(function (r) { add(r, top - t - BEAM_DEPTH, top - t, "beam"); });
          }
        });
      });
      return out;
    }

    function activeCut() {
      var on = ["x", "z", "y"].filter(function (a) { return section[a].on; });
      return on.length === 1 && !sbox.on ? on[0] : null;
    }

    function buildCaps() {
      while (capGroup.children.length) {
        var c = capGroup.children.pop();
        c.geometry.dispose(); c.material.dispose();
      }
      var box = visibleBox();
      ["x", "y", "z"].forEach(function (a) {
        if (!section[a].on) return;
        var v = section[a].value, lo = box.min, hi = box.max, pos;
        if (a === "x") pos = [v, lo.y, lo.z, v, lo.y, hi.z, v, lo.y, hi.z, v, hi.y, hi.z, v, hi.y, hi.z, v, hi.y, lo.z, v, hi.y, lo.z, v, lo.y, lo.z];
        else if (a === "z") pos = [lo.x, lo.y, v, hi.x, lo.y, v, hi.x, lo.y, v, hi.x, hi.y, v, hi.x, hi.y, v, lo.x, hi.y, v, lo.x, hi.y, v, lo.x, lo.y, v];
        else pos = [lo.x, v, lo.z, hi.x, v, lo.z, hi.x, v, lo.z, hi.x, v, hi.z, hi.x, v, hi.z, lo.x, v, hi.z, lo.x, v, hi.z, lo.x, v, lo.z];
        capGroup.add(lines(THREE, pos, 0x22d3ee, 0.9));
      });
      if (sbox.on) {
        var x = sbox.x, y = sbox.y, z = sbox.z, edges = [];
        [y[0], y[1]].forEach(function (h) {
          edges.push(x[0], h, z[0], x[1], h, z[0], x[1], h, z[0], x[1], h, z[1], x[1], h, z[1], x[0], h, z[1], x[0], h, z[1], x[0], h, z[0]);
        });
        [[x[0], z[0]], [x[1], z[0]], [x[1], z[1]], [x[0], z[1]]].forEach(function (c) { edges.push(c[0], y[0], c[1], c[0], y[1], c[1]); });
        capGroup.add(lines(THREE, edges, 0x22d3ee, 0.9));
      }
      var axis = activeCut();
      if (!axis || axis === "y") return;
      var value = section[axis].value, eps = section[axis].flip ? 0.003 : -0.003;
      var byKind = {};
      cutRects(axis, value).forEach(function (r) {
        var pos = byKind[r.kind] = byKind[r.kind] || [];
        var v = value + eps;
        if (axis === "x") {
          pos.push(v, r.y0, r.u0, v, r.y0, r.u1, v, r.y1, r.u1, v, r.y0, r.u0, v, r.y1, r.u1, v, r.y1, r.u0);
        } else {
          pos.push(r.u0, r.y0, v, r.u1, r.y0, v, r.u1, r.y1, v, r.u0, r.y0, v, r.u1, r.y1, v, r.u0, r.y1, v);
        }
      });
      Object.keys(byKind).forEach(function (k) {
        var m = mesh(THREE, byKind[k], CAP[k] || 0x475569, 1);
        m.material.polygonOffset = false;
        capGroup.add(m);
      });
    }

    function levelLines() {
      var seen = {}, out = [];
      visibleEntries().forEach(function (b) {
        b.levels.forEach(function (lv) {
          var key = lv.data.elev.toFixed(2);
          if (seen[key]) return;
          seen[key] = 1;
          out.push({ y: lv.data.elev, label: lv.data.label });
        });
      });
      return out.sort(function (a, b) { return a.y - b.y; });
    }

    // the drawing is seen from the kept side's opposite, like the 3D cut: an x cut from the right
    // (or left when flipped), a z cut from the front (or back)
    function sectionMirror(axis) { return axis === "x" ? !section[axis].flip : section[axis].flip; }
    function sectionSide(axis) {
      return axis === "x" ? (section.x.flip ? "좌측에서 봄" : "우측에서 봄") : (section.z.flip ? "배면에서 봄" : "정면에서 봄");
    }
    function sectionSvg(axis) {
      var rects = cutRects(axis, section[axis].value);
      if (sectionMirror(axis)) rects = rects.map(function (r) { return Object.assign({}, r, { u0: -r.u1, u1: -r.u0 }); });
      if (!rects.length) return '<p class="q3d-empty">이 위치를 지나는 부재가 없어요. 슬라이더를 움직여 보세요.</p>';
      var u0 = Math.min.apply(null, rects.map(function (r) { return r.u0; })) - 2;
      var u1 = Math.max.apply(null, rects.map(function (r) { return r.u1; })) + 2;
      var levels = levelLines();
      var y0 = Math.min(Math.min.apply(null, rects.map(function (r) { return r.y0; })), levels.length ? levels[0].y : 0) - 1;
      var y1 = Math.max.apply(null, rects.map(function (r) { return r.y1; })) + 1;
      var W = u1 - u0, H = y1 - y0, font = Math.max(W, H) / 60, left = font * 11, pad = font * 2;
      var fill = { wall: "#f59e0b", col: "#d97706", slab: "#e2e8f0", beam: "#fb923c" };
      var parts = levels.map(function (l) {
        var y = (y1 - l.y).toFixed(2);
        return '<line x1="' + (u0 - font * 0.5).toFixed(2) + '" y1="' + y + '" x2="' + u1.toFixed(2) + '" y2="' + y + '" class="lv"/>' +
          '<text x="' + (u0 - font).toFixed(2) + '" y="' + y + '" class="lt">' + escapeXml(l.label) + ' EL ' + l.y.toFixed(2) + '</text>';
      });
      rects.forEach(function (r) {
        parts.push('<rect x="' + r.u0.toFixed(3) + '" y="' + (y1 - r.y1).toFixed(3) + '" width="' + Math.max(0.02, r.u1 - r.u0).toFixed(3) +
          '" height="' + Math.max(0.02, r.y1 - r.y0).toFixed(3) + '" fill="' + fill[r.kind] + '"' + (r.approx ? ' fill-opacity=".45"' : '') + '/>');
      });
      return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="' + (u0 - left).toFixed(2) + ' ' + (-pad).toFixed(2) + ' ' + (W + left + pad).toFixed(2) + ' ' +
        (H + pad * 2).toFixed(2) + '" preserveAspectRatio="xMidYMid meet"><style>.lv{stroke:#475569;stroke-width:' + (font / 8).toFixed(3) +
        ';stroke-dasharray:' + font.toFixed(2) + ' ' + (font / 2).toFixed(2) + '}.lt{fill:#cbd5e1;font-size:' + font.toFixed(2) +
        'px;text-anchor:end;dominant-baseline:middle;font-family:sans-serif}</style>' + parts.join("") + '</svg>';
    }

    function escapeXml(text) {
      return String(text == null ? "" : text).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
    }

    function planAt(height) {
      var b = visibleEntries()[0];
      if (!b) return null;
      var hit = null;
      b.levels.forEach(function (lv) { if (lv.data.elev <= height + 1e-6) hit = lv; });
      return hit ? { building: b.name, level: hit.data } : null;
    }

    // measuring: two picked points on the model, the clipped-away part ignored
    var raycaster = new THREE.Raycaster();
    var measure = { on: false, points: [], marks: new THREE.Group() };
    scene.add(measure.marks);
    function pick(clientX, clientY) {
      var rect = el.getBoundingClientRect();
      var ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(ndc, camera);
      var targets = [];
      scene.traverse(function (o) { if (o.isMesh && shown(o) && o.parent !== measure.marks) targets.push(o); });
      var hits = raycaster.intersectObjects(targets, false);
      for (var i = 0; i < hits.length; i++) {
        var p = hits[i].point;
        if (clipPlanes.every(function (pl) { return pl.distanceToPoint(p) >= -0.01; })) return p;
      }
      return null;
    }
    function shown(o) {
      for (; o; o = o.parent) if (!o.visible) return false;
      return true;
    }
    function clearMeasure() {
      measure.points = [];
      while (measure.marks.children.length) measure.marks.remove(measure.marks.children[0]);
    }
    function addMeasurePoint(p) {
      if (measure.points.length >= 2) clearMeasure();
      measure.points.push(p.clone());
      var dot = new THREE.Mesh(new THREE.SphereGeometry(Math.max(0.08, orbit.radius / 250), 12, 8), new THREE.MeshBasicMaterial({ color: 0x22d3ee }));
      dot.position.copy(p);
      measure.marks.add(dot);
      if (measure.points.length === 2) {
        var a = measure.points[0], b2 = measure.points[1];
        measure.marks.add(lines(THREE, [a.x, a.y, a.z, b2.x, b2.y, b2.z], 0x22d3ee, 1));
        var d = a.distanceTo(b2), dy = Math.abs(a.y - b2.y), dh = Math.hypot(a.x - b2.x, a.z - b2.z);
        status("거리 " + d.toFixed(3) + "m · 수평 " + dh.toFixed(3) + "m · 높이 " + dy.toFixed(3) + "m");
      } else {
        status("두 번째 점을 누르세요");
      }
      render();
    }

    // views: 3D, plan and the four elevations (elevations and the plan are drawn without perspective)
    var VIEWS = {
      iso: { theta: -0.75, phi: 1.05, ortho: false }, top: { theta: Math.PI / 2, phi: 0.0005, ortho: true },
      front: { theta: Math.PI / 2, phi: Math.PI / 2, ortho: true }, back: { theta: -Math.PI / 2, phi: Math.PI / 2, ortho: true },
      left: { theta: Math.PI, phi: Math.PI / 2, ortho: true }, right: { theta: 0, phi: Math.PI / 2, ortho: true }
    };
    function setProjection(ortho) {
      camera = ortho ? orthographic : perspective;
      var button = ui && ui.querySelector("[data-proj]");
      if (button) button.textContent = ortho ? "평행" : "원근";
      place(); render();
    }
    function setView(name) {
      var v = VIEWS[name];
      if (!v) return;
      orbit.theta = v.theta; orbit.phi = v.phi;
      setProjection(v.ortho);
      markView(name);
      fit();
    }
    function markView(name) {
      if (!ui) return;
      ui.querySelectorAll("[data-view]").forEach(function (b) { b.classList.toggle("is-on", b.getAttribute("data-view") === name); });
    }

    var ui = null, statusEl = null, sheetEl = null;
    function status(text) { if (statusEl) statusEl.textContent = text || ""; }
    function rangeRow(axis, key, value, min, max) {
      return '<input type="range" data-' + key + '="' + axis + '" min="' + min.toFixed(2) + '" max="' + max.toFixed(2) + '" step="0.05" value="' + value.toFixed(2) + '">';
    }
    function buildUi() {
      if (options.tools === false) return;
      ui = document.createElement("div");
      ui.className = "q3d-ui";
      var lo = worldBox.min, hi = worldBox.max;
      ui.innerHTML =
        '<div class="q3d-bar q3d-views">' +
          '<button type="button" data-view="iso" class="is-on">3D</button><button type="button" data-view="top">평면</button>' +
          '<button type="button" data-view="front">정면</button><button type="button" data-view="back">배면</button>' +
          '<button type="button" data-view="left">좌측</button><button type="button" data-view="right">우측</button>' +
          '<button type="button" data-proj>원근</button>' +
        '</div>' +
        '<div class="q3d-bar q3d-tools">' +
          '<button type="button" data-tool="section">단면</button><button type="button" data-tool="box">단면 상자</button>' +
          '<button type="button" data-tool="kinds">부재</button><button type="button" data-tool="measure">측정</button>' +
          '<button type="button" data-tool="fit">맞춤</button><button type="button" data-tool="shot">저장</button>' +
        '</div>' +
        '<div class="q3d-panel" data-panel="section" hidden>' + ["x", "z", "y"].map(function (a) {
          return '<div class="q3d-row"><label><input type="checkbox" data-axis="' + a + '"> ' + AXES[a] + '</label>' +
            rangeRow(a, "cut", section[a].value, lo[a], hi[a]) + '<button type="button" data-flip="' + a + '" title="반대쪽 보기">⇄</button></div>';
        }).join("") + '<button type="button" class="q3d-wide" data-sheet>단면도 보기</button></div>' +
        '<div class="q3d-panel" data-panel="box" hidden><label><input type="checkbox" data-boxon> 단면 상자 켜기</label>' + ["x", "z", "y"].map(function (a) {
          return '<div class="q3d-row"><span>' + AXES[a] + '</span>' + rangeRow(a, "boxlo", sbox[a][0], lo[a], hi[a]) + rangeRow(a, "boxhi", sbox[a][1], lo[a], hi[a]) + '</div>';
        }).join("") + '</div>' +
        '<div class="q3d-panel" data-panel="kinds" hidden>' + [["wall", "벽"], ["col", "기둥"], ["beam", "보"], ["slab", "슬래브"]].map(function (k) {
          return '<label><input type="checkbox" data-kind="' + k[0] + '" checked> ' + k[1] + '</label>';
        }).join("") + '</div>' +
        '<div class="q3d-status"></div>' +
        '<div class="q3d-sheet" hidden><header><b></b><button type="button" data-close>닫기</button></header><div class="q3d-sheet-body"></div></div>';
      container.appendChild(ui);
      statusEl = ui.querySelector(".q3d-status");
      sheetEl = ui.querySelector(".q3d-sheet");
      sheetEvents();
      ui.addEventListener("pointerdown", function (e) { e.stopPropagation(); });
      ui.addEventListener("wheel", function (e) { e.stopPropagation(); }, { passive: true });
      ui.addEventListener("click", function (e) {
        var t = e.target.closest("button");
        if (!t) return;
        if (t.hasAttribute("data-view")) { setView(t.getAttribute("data-view")); return; }
        if (t.hasAttribute("data-proj")) { setProjection(camera !== orthographic); return; }
        if (t.hasAttribute("data-close")) { sheetEl.hidden = true; return; }
        if (t.hasAttribute("data-flip")) {
          var a = t.getAttribute("data-flip");
          section[a].flip = !section[a].flip;
          updateClip();
          if (!sheetEl.hidden) showSheet();
          return;
        }
        if (t.hasAttribute("data-sheet")) { showSheet(); return; }
        var tool = t.getAttribute("data-tool");
        if (tool === "fit") { fit(); return; }
        if (tool === "shot") {
          var link = document.createElement("a");
          link.href = renderer.domElement.toDataURL("image/png");
          link.download = "3D_검토.png";
          link.click();
          return;
        }
        if (tool === "measure") {
          measure.on = !measure.on;
          t.classList.toggle("is-on", measure.on);
          if (!measure.on) { clearMeasure(); status(""); render(); } else { status("첫 번째 점을 누르세요"); }
          return;
        }
        if (tool) {
          ui.querySelectorAll("[data-panel]").forEach(function (p) {
            var open = p.getAttribute("data-panel") === tool && p.hidden;
            p.hidden = !open;
          });
          ui.querySelectorAll("[data-tool]").forEach(function (b) {
            var panel = ui.querySelector('[data-panel="' + b.getAttribute("data-tool") + '"]');
            if (panel) b.classList.toggle("is-on", !panel.hidden);
          });
        }
      });
      var pending = 0;
      function later() { if (!pending) pending = setTimeout(function () { pending = 0; updateClip(); if (!sheetEl.hidden) showSheet(); }, 60); }
      ui.addEventListener("input", function (e) {
        var t = e.target;
        if (t.hasAttribute("data-cut")) { var a = t.getAttribute("data-cut"); section[a].value = Number(t.value); if (section[a].on) later(); }
        else if (t.hasAttribute("data-boxlo")) { sbox[t.getAttribute("data-boxlo")][0] = Number(t.value); if (sbox.on) later(); }
        else if (t.hasAttribute("data-boxhi")) { sbox[t.getAttribute("data-boxhi")][1] = Number(t.value); if (sbox.on) later(); }
      });
      ui.addEventListener("change", function (e) {
        var t = e.target;
        if (t.hasAttribute("data-axis")) { sectionOn(t.getAttribute("data-axis"), t.checked); updateClip(); }
        else if (t.hasAttribute("data-boxon")) { sbox.on = t.checked; updateClip(); }
        else if (t.hasAttribute("data-kind")) {
          state.kinds[t.getAttribute("data-kind")] = t.checked;
          apply(); buildCaps(); render();
          if (!sheetEl.hidden) showSheet();
        }
      });
    }

    function showSheet() {
      var axis = activeCut();
      var title = sheetEl.querySelector("b"), body = sheetEl.querySelector(".q3d-sheet-body");
      if (sheetEl.hidden) {
        sheetView = { s: 1, x: 0, y: 0 };
        ui.querySelectorAll("[data-panel]").forEach(function (p) { p.hidden = true; });
        ui.querySelectorAll("[data-tool]").forEach(function (b) { if (b.getAttribute("data-tool") !== "measure") b.classList.remove("is-on"); });
      }
      sheetEl.hidden = false;
      if (!axis) {
        title.textContent = "단면도";
        body.innerHTML = '<p class="q3d-empty">단면 하나만 켜고(단면 상자는 끄고) 다시 눌러 주세요.</p>';
        return;
      }
      var value = section[axis].value;
      if (axis === "y") {
        var at = planAt(value);
        title.textContent = "수평 단면 · 높이 " + value.toFixed(2) + "m" + (at ? " · " + at.building + " " + at.level.label : "");
        var svg = at && options.planSvg ? options.planSvg(at.building, at.level) : null;
        body.innerHTML = svg ? '<div class="qd-floor-canvas">' + svg + '</div>' : '<p class="q3d-empty">이 층의 평면 그림이 없어요.</p>';
        sheetTransform();
        return;
      }
      title.textContent = (axis === "x" ? "횡단면 · X " : "종단면 · Z ") + value.toFixed(2) + "m · " + sectionSide(axis);
      body.innerHTML = sectionSvg(axis);
      sheetTransform();
    }
    var sheetView = { s: 1, x: 0, y: 0 }, sheetDrag = null;
    function sheetTransform() {
      var svg = sheetEl && sheetEl.querySelector(".q3d-sheet-body svg");
      if (svg) { svg.style.transformOrigin = "0 0"; svg.style.transform = "translate(" + sheetView.x + "px," + sheetView.y + "px) scale(" + sheetView.s + ")"; }
    }
    function sheetEvents() {
      var body = sheetEl.querySelector(".q3d-sheet-body");
      body.addEventListener("wheel", function (e) {
        e.preventDefault();
        var r = body.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
        var k = e.deltaY > 0 ? 1 / 1.15 : 1.15, s = Math.min(40, Math.max(0.5, sheetView.s * k));
        k = s / sheetView.s;
        sheetView.x = px - (px - sheetView.x) * k; sheetView.y = py - (py - sheetView.y) * k; sheetView.s = s;
        sheetTransform();
      }, { passive: false });
      body.addEventListener("pointerdown", function (e) { body.setPointerCapture(e.pointerId); sheetDrag = { x: e.clientX, y: e.clientY }; });
      body.addEventListener("pointermove", function (e) {
        if (!sheetDrag) return;
        sheetView.x += e.clientX - sheetDrag.x; sheetView.y += e.clientY - sheetDrag.y;
        sheetDrag = { x: e.clientX, y: e.clientY };
        sheetTransform();
      });
      body.addEventListener("pointerup", function () { sheetDrag = null; });
      body.addEventListener("dblclick", function () { sheetView = { s: 1, x: 0, y: 0 }; sheetTransform(); });
    }

    var down = null;
    el.addEventListener("pointerdown", function (e) { down = { x: e.clientX, y: e.clientY }; });
    el.addEventListener("pointerup", function (e) {
      if (!measure.on || !down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return;
      var p = pick(e.clientX, e.clientY);
      if (p) addMeasurePoint(p); else status("모델 위를 눌러 주세요");
    });
    buildUi();


    place();
    apply();
    fit();

    return {
      buildings: function () { return buildings.map(function (b) { return b.name; }); },
      levels: function (name) {
        var b = buildings.filter(function (x) { return x.name === name; })[0];
        return b ? b.levels.map(function (lv) { return lv.data; }) : [];
      },
      setMode: function (mode) { state.mode = mode; apply(); },
      setBuilding: function (name) { state.building = name || null; state.level = null; apply(); fit(); resetRanges(); },
      setLevel: function (index) { state.level = index == null ? null : index; apply(); fit(); },
      fit: fit,
      resize: resize,
      // formwork area (m²) by level for one building, built on demand
      formwork: function (name) {
        var b = buildings.filter(function (x) { return x.name === name; })[0];
        if (!b) return [];
        return b.levels.map(function (lv) {
          var f = formFor(b, lv);
          f.group.visible = state.mode === "form" && (state.level == null || state.level === lv.index) &&
            (!state.building || state.building === b.name);
          return { label: lv.data.label, basement: f.basement, wall: f.area.wall, gang: f.area.gang, slab: f.area.slab,
                   beam: f.area.beam, approximate: f.basement || !lv.top || !!lv.top.slab.slab_approximate };
        });
      },
      rebarBars: function (name, index) {
        var b = buildings.filter(function (x) { return x.name === name; })[0];
        return b && b.levels[index] ? rebarFor(b.levels[index]).bars : 0;
      },
      summary: function () {
        var storeys = 0, basements = 0, slab = 0;
        buildings.forEach(function (b) {
          b.levels.forEach(function (lv) {
            if (lv.data.key.charAt(0) === "B") basements = Math.max(basements, Number(lv.data.key.slice(1)) || 0);
            else if (/^\d+$/.test(lv.data.key)) storeys = Math.max(storeys, Number(lv.data.key));
            slab += lv.plan.slab_area || 0;
          });
        });
        return { buildings: buildings.length, storeys: storeys, basements: basements, slab: slab };
      },
      snapshot: function () { renderer.render(scene, camera); return renderer.domElement.toDataURL("image/png"); },
      setView: setView,
      setSection: function (axis, value, on) {
        if (!section[axis]) return;
        if (value != null) section[axis].value = value;
        sectionOn(axis, on !== false);
        var box = ui && ui.querySelector('[data-axis="' + axis + '"]');
        if (box) box.checked = section[axis].on;
        updateClip();
      },
      cut: function (axis, value) { return cutRects(axis, value); },
      sectionSvg: function (axis) { return sectionSvg(axis); },
      clipCount: function () { return clipPlanes.length; },
      resetRanges: resetRanges,
      dispose: function () {
        if (observer) observer.disconnect();
        if (ui && ui.parentNode) ui.parentNode.removeChild(ui);
        renderer.dispose();
        if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
      }
    };
  }

  window.cblQuantityMass3D = { mount: mount };
})();
