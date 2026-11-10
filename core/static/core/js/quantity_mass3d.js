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
    var scene = new THREE.Scene();
    var camera = new THREE.PerspectiveCamera(38, width / height, 0.5, 5000);
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
        if (walls.length) massMeshes.push(mesh(THREE, walls, COLOR.wall, 0.92));
        if (cols.length) massMeshes.push(mesh(THREE, cols, COLOR.col, 0.95));
        if (slab.length) massMeshes.push(mesh(THREE, slab, COLOR.slab, 0.8));
        var polygonPlan = slabPlan && slabPlan.slab_polygon ? slabPlan : (index === 0 && plan.slab_polygon ? plan : null);
        if (polygonPlan && polygonPlan.slab_polygon.length >= 3) {
          var shape = new THREE.Shape(polygonPlan.slab_polygon.map(function (p) { return new THREE.Vector2(p[0], p[1]); }));
          var geometry = new THREE.ShapeGeometry(shape);
          geometry.rotateX(-Math.PI / 2); // shape y → -z
          var flat = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: COLOR.slab, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false }));
          flat.position.y = polygonPlan === plan && !slabPlan ? level.elev : top;
          flat.userData.baseOpacity = 0.16;
          massMeshes.push(flat);
        }
        if (beams.length) massMeshes.push(mesh(THREE, beams, COLOR.beam, 0.9));
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
      lv.group.add(segment);
      lv.rebar = { object: segment, bars: pos.length / 6 };
      return lv.rebar;
    }

    // ── view state ──
    var state = { mode: "mass", building: null, level: null }; // level: index in the chosen building, null = all
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
      camera.position.set(orbit.target.x + r * Math.sin(orbit.phi) * Math.cos(orbit.theta),
                          orbit.target.y + r * Math.cos(orbit.phi),
                          orbit.target.z + r * Math.sin(orbit.phi) * Math.sin(orbit.theta));
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
      var vfov = camera.fov * Math.PI / 180;
      var hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
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
        orbit.target.y += dy * scale;
      } else {
        orbit.theta += dx * 0.008;
        orbit.phi = Math.min(1.52, Math.max(0.15, orbit.phi - dy * 0.008));
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
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      render();
    }
    var observer = window.ResizeObserver ? new ResizeObserver(resize) : null;
    if (observer) observer.observe(container);

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
      setBuilding: function (name) { state.building = name || null; state.level = null; apply(); fit(); },
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
      dispose: function () {
        if (observer) observer.disconnect();
        renderer.dispose();
        if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
      }
    };
  }

  window.cblQuantityMass3D = { mount: mount };
})();
