// 3D 세계: 렌더러, 우주, 지구, 도시, 카메라, 선택, 인트로 연출
import * as THREE from 'three';
import { R, D2R, vecToLL, llToVec } from './geo.js';
import { buildEarthTextures, makeEarth, paintLights, countryIndexAt } from './earth.js';
import { makeSpace, glowTexture } from './space.js';
import { CameraRig } from './camera.js';
import { Cities } from './cities.js';
import { Labels } from './labels.js';
import { Weather } from './weather.js';
import { PHONE, TOUCH } from '../device.js';

export class World3D {
  constructor(container, meta, countries) {
    this.meta = meta;
    this.countries = countries;
    const renderer = this.renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true, powerPreference: 'high-performance' });
    // 휴대폰은 화면 밀도가 3배라 그대로 그리면 느리고 뜨거워진다 → 1.5배까지만
    renderer.setPixelRatio(Math.min(PHONE ? 1.5 : 2, devicePixelRatio));
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x000000);
    this.camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.001, 3e10);

    this.space = makeSpace();
    this.scene.add(this.space.sky, this.space.equatorial, this.space.ambient);
    this.earthRoot = new THREE.Group();
    this.scene.add(this.earthRoot);
    this.tex = buildEarthTextures(countries, renderer.capabilities.maxTextureSize);
    this.earth = makeEarth(this.tex);
    this.earthRoot.add(this.earth.group);
    this.cities = new Cities(this.earthRoot, meta, this.earth.mesh);
    this.weather = new Weather(this.scene);
    this.rig = new CameraRig(this.camera, renderer.domElement);
    this.labels = new Labels(document.getElementById('labels'), sid => this.onTownClick?.(sid));
    this.ray = new THREE.Raycaster();
    this.mode = 'idle';
    this.state = null;
    this.clockOverride = null;
    this.hoverCountry = -1;
    this.lastLightsKey = '';
    this.clock = new THREE.Clock();
    this.time = 0;

    addEventListener('resize', () => {
      this.camera.aspect = container.clientWidth / container.clientHeight;
      this.camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    });
    const el = renderer.domElement;
    el.addEventListener('pointermove', e => this.onMove(e));
    el.addEventListener('pointerup', e => { if (!this.rig.dragged && e.button === 0) this.onClick(e); });
    el.addEventListener('dblclick', e => this.onDblClick(e));
    this.rig.onDoubleTap = e => this.onDblClick(e);
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }

  setState(state) { this.state = state; }

  earthHit(e) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1);
    this.ray.setFromCamera(ndc, this.camera);
    const hit = this.ray.intersectObject(this.earth.mesh, false)[0];
    return hit ? vecToLL(hit.point.clone().sub(this.earthRoot.position)) : null;
  }

  onMove(e) {
    if (this.mode !== 'pick-country') return;
    const ll = this.earthHit(e);
    const idx = ll ? countryIndexAt(this.tex, ll.lat, ll.lon) : -1;
    this.hoverCountry = idx;
    this.earth.uniforms.hoverId.value = idx >= 0 && idx !== this.selectedCountry ? idx + 1 : -1;
    this.onCountryHover?.(idx, e.clientX, e.clientY);
  }

  onClick(e) {
    if (this.mode === 'pick-country') {
      const ll = this.earthHit(e);
      const idx = ll ? countryIndexAt(this.tex, ll.lat, ll.lon) : -1;
      if (idx >= 0) this.onCountryPick?.(idx);
      return;
    }
    if (this.mode !== 'observe') return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const ndc = new THREE.Vector2(x / rect.width * 2 - 1, -y / rect.height * 2 + 1);
    this.ray.setFromCamera(ndc, this.camera);
    const hit = this.cities.pick(this.ray);
    if (hit?.annId) return this.onAnnPick?.(hit.annId);
    const near = this.cities.nearestAnnOnScreen(this.state, this.camera, x, y, rect.width, rect.height);
    if (near) return this.onAnnPick?.(near);
    if (hit?.bid) return this.onBuildingPick?.(hit.bid, e.clientX, e.clientY);
    this.onEmptyClick?.();
  }

  onDblClick(e) {
    if (this.mode !== 'observe' && this.mode !== 'pick-country') return;
    const ll = this.earthHit(e);
    if (ll) this.rig.flyTo({ lat: ll.lat, lon: ll.lon, alt: this.rig.alt * 0.35 });
  }

  /** 나라 강조: 내 나라(파랑), 선택(노랑) */
  setMyCountry(code) {
    this.myCountry = this.countries.findIndex(c => c.code === code);
    this.tex.paintOverlay(this.selectedCountry ?? -1, this.myCountry);
  }
  setSelectedCountry(idx) {
    if (idx === this.selectedCountry) return;
    this.selectedCountry = idx;
    this.earth.uniforms.hoverId.value = -1;
    this.tex.paintOverlay(idx ?? -1, this.myCountry ?? -1);
  }

  flyToCountry(c, alt = 3200) {
    this.rig.flyTo({ lat: c.centroid[1], lon: c.centroid[0], alt, heading: 0 });
  }

  // ── 인트로: 위에서 지구가 떨어진다 ──
  playIntro(onLanded) {
    this.mode = 'intro';
    this.rig.enabled = false;
    this.clockOverride = 172 * 1440 + 395; // 북반구 여름 아침 무렵의 빛
    this.rig.jump({ lat: 22, lon: 118, alt: R * 2.9, tilt: 90, heading: 0 });
    this.rig.apply();
    this.lift = this.liftTarget = 0.13;
    const startY = R * 9;
    this.earthRoot.position.set(0, startY, 0);
    this.earthRoot.rotation.y = -1.4;
    const trail = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture('rgba(255,230,190,1)', 'rgba(255,140,60,0.35)'), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0 }));
    this.scene.add(trail);
    const ring = new THREE.Mesh(new THREE.RingGeometry(1, 1.15, 96), new THREE.MeshBasicMaterial({ color: 0x9fd0ff, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }));
    this.scene.add(ring);
    const camBase = this.camera.position.clone();
    const flash = document.getElementById('flash');
    const T_FALL = 2.3, T_SETTLE = 1.6;
    const t0 = performance.now();
    let landed = false;
    this.introSkip = () => { this.introT = 99; };
    this.introStep = () => {
      const t = this.introT ?? (performance.now() - t0) / 1000;
      if (t < 0.35) { this.earthRoot.position.y = startY; return; }
      const tf = t - 0.35;
      if (tf < T_FALL) {
        const k = tf / T_FALL;
        this.earthRoot.position.y = startY * (1 - k * k * k);
        this.earthRoot.rotation.y = -1.4 + k * 1.2;
        trail.material.opacity = Math.min(1, k * 1.6) * 0.85;
        trail.position.set(0, this.earthRoot.position.y + R * 1.6, 0);
        trail.scale.set(R * 1.4, R * 4.2 * (0.5 + k), 1);
      } else {
        const ts = tf - T_FALL;
        if (!landed) {
          landed = true;
          flash.style.transition = 'none'; flash.style.opacity = '1';
          requestAnimationFrame(() => { flash.style.transition = 'opacity 1.6s ease'; flash.style.opacity = '0'; });
        }
        const k = Math.min(1, ts / T_SETTLE);
        // 쿵 — 살짝 튀어 오르고 가라앉는다
        this.earthRoot.position.y = -Math.sin(k * Math.PI * 2.2) * Math.exp(-k * 4.5) * R * 0.18;
        this.earthRoot.rotation.y = -0.2 + k * 0.2;
        trail.material.opacity = Math.max(0, 0.85 - ts * 3);
        ring.position.set(0, 0, 0);
        ring.lookAt(this.camera.position);
        const rr = R * (1.02 + ts * 2.2);
        ring.scale.set(rr, rr, 1);
        ring.material.opacity = Math.max(0, 0.75 - ts * 0.55);
        const shake = Math.exp(-ts * 5) * R * 0.04;
        this.camera.position.copy(camBase).add(new THREE.Vector3((Math.random() - 0.5) * shake, (Math.random() - 0.5) * shake, 0));
        if (ts > 2.4) {
          this.earthRoot.position.set(0, 0, 0); this.earthRoot.rotation.y = 0;
          this.scene.remove(trail, ring);
          this.introStep = null;
          this.mode = 'menu';
          this.rig.jump({ lat: 22, lon: 118, alt: R * 2.9, tilt: 90 });
          onLanded?.();
        }
      }
    };
  }

  /** 로켓 발사 연출: 정착지에서 하늘로 솟아 우주로 */
  launchRocket(sid) {
    const t = this.cities.towns.get(sid);
    if (!t) return;
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.5, 4, 12).translate(0, 2, 0), new THREE.MeshStandardMaterial({ color: 0xf2f4f7 }));
    const nose = new THREE.Mesh(new THREE.ConeGeometry(0.5, 1.4, 12).translate(0, 4.7, 0), new THREE.MeshStandardMaterial({ color: 0xd33b3b }));
    const fire = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture('rgba(255,250,220,1)', 'rgba(255,140,40,0.6)'), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
    fire.position.y = -1; fire.scale.set(3, 6, 1);
    const far = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture('rgba(255,240,200,1)', 'rgba(255,150,60,0.5)', 64), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: false }));
    far.scale.set(0.02, 0.02, 1);
    g.add(body, nose, fire, far);
    g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), t.up);
    this.earthRoot.add(g);
    const start = performance.now();
    (this.rockets ||= []).push({ g, t, start, fire });
  }
  updateRockets() {
    if (!this.rockets?.length) return;
    const now = performance.now();
    this.rockets = this.rockets.filter(r => {
      const s = (now - r.start) / 1000;
      const h = 0.02 * s * s * s + 0.01 * s; // km, 점점 빨라진다
      const scale = Math.max(0.03, Math.min(40, h * 0.004 + 0.012));
      r.g.position.copy(r.t.up).multiplyScalar(r.t.base + h);
      r.g.scale.setScalar(scale);
      r.fire.scale.set(3 + Math.random(), 6 + Math.random() * 3, 1);
      if (s > 40) { this.earthRoot.remove(r.g); return false; }
      return true;
    });
  }

  /** 메뉴 배경: 천천히 도는 지구 (제목 위로 살짝 띄운다) */
  menuSpin(on) { this.spin = on; this.setLift(on ? 0.13 : 0); }

  /** 화면 속 지구를 위로 띄우기 (0~1, 화면 높이 비율) */
  setLift(v) { this.liftTarget = v; }
  applyLift(dt) {
    this.lift = (this.lift ?? 0) + ((this.liftTarget ?? 0) - (this.lift ?? 0)) * Math.min(1, dt * 3);
    const el = this.renderer.domElement, w = el.clientWidth, h = el.clientHeight;
    if (Math.abs(this.lift) < 0.001) { if (this.camera.view) this.camera.clearViewOffset(); return; }
    this.camera.setViewOffset(w, h, 0, this.lift * h, w, h);
  }

  loop(ts = 0) {
    requestAnimationFrame(this.loop);
    // 휴대폰은 초당 30장이면 충분하다 (배터리·발열)
    if (PHONE && ts - (this.lastTs || 0) < 30) return;
    this.lastTs = ts;
    const dt = Math.min(0.1, this.clock.getDelta());
    this.time += dt;
    const st = this.state;
    if (this.clockOverride != null && this.mode !== 'observe') this.clockOverride += dt * (this.spin ? 3 : 0.5);
    const minutes = this.mode === 'observe' && st?.world ? st.now() : (this.clockOverride ?? 0);
    this.applyLift(dt);
    if (this.introStep) this.introStep();
    else {
      if (this.spin && !this.rig.pointers.size) this.rig.tgt.lon += dt * 4;
      this.rig.update(dt);
    }
    this.space.update(minutes, this.camera, this.time);
    this.earth.uniforms.sunDir.value.copy(this.space.sunDir);
    const alt = this.rig.alt;
    // 도시 불빛 텍스처는 우주에서 볼 때만 (가까이서는 실제 건물 창문 불빛)
    this.earth.uniforms.lightsFade.value = THREE.MathUtils.clamp((alt - 40) / 400, 0, 1);
    this.earth.cloudU.time.value = this.time + minutes * 0.02;
    this.earth.cloudU.opacity.value = THREE.MathUtils.clamp((alt - 250) / 2500, 0, 0.85);
    // 행성 궤도는 멀리서만
    const far = alt > 3e5;
    for (const p of this.space.planets) p.orbit.visible = far;
    this.space.planets.earthOrbit.visible = far;
    let labels = [];
    if (st?.world && (this.mode === 'observe' || this.mode === 'pick-country')) {
      if (st.dirty.settlements || st.dirty.all) {
        this.cities.syncTowns(st);
        const key = [...st.settlements.values()].map(s => `${s.id}:${s.level}:${s.pop}`).join(',');
        if (key !== this.lastLightsKey) { paintLights(this.earth, [...st.settlements.values()]); this.lastLightsKey = key; }
        st.dirty.settlements = false;
      }
      if (st.dirty.all) { for (const sid of st.settlements.keys()) st.dirty.buildings.add(sid); st.dirty.all = false; }
      // 한 프레임에 몇 개씩만 다시 짓는다
      let n = 0;
      for (const sid of st.dirty.buildings) { this.cities.rebuild(st, sid); st.dirty.buildings.delete(sid); if (++n >= 3) break; }
      // 초점 지점의 밤 정도 (창문 불빛)
      const fp = this.rig.focus || this.camera.position;
      const nd = fp.clone().normalize().dot(this.space.sunDir);
      this.space.ambient.position.copy(fp).normalize(); // 채움빛의 '하늘' = 보고 있는 곳의 위쪽
      this.cities.night = THREE.MathUtils.clamp((0.05 - nd) / 0.25, 0, 1);
      // 밤에는 달빛 정도의 푸른 빛
      this.space.ambient.intensity = THREE.MathUtils.lerp(1.05, 0.35, this.cities.night);
      this.space.ambient.color.setHex(this.cities.night > 0.5 ? 0x7f98d8 : 0xcfe0ff);
      labels = this.cities.update(st, this.camera, this.rig, this.time);
      // 마을 가까이에서는 카메라 초점을 실제 땅 높이에 맞춘다 (지구 구면은 마을 땅보다 높을 수 있다)
      const nt = this.cities.nearest?.t;
      if (!this.rig.follow) {
        if (nt && alt < 4 && this.rig.focus) {
          this._inv = (this._inv || new THREE.Matrix4()).copy(nt.g.matrixWorld).invert();
          const lp = this.rig.focus.clone().setLength(R).applyMatrix4(this._inv);
          if (Math.hypot(lp.x, lp.z) < 4000) this.rig.tgt.h = nt.base - R + Math.max(0, nt.hAt(lp.x, lp.z)) / 1000;
        } else if (alt > 8) this.rig.tgt.h = 0;
      }
      // 날씨: 가장 가까운 마을의 하늘
      const wx = this.weather.update(dt, this.camera, this.cities.nearest?.t || null, alt, this.cities.night);
      this.space.light.intensity = 3.2 * (1 - wx.dim) + wx.flash * 6;
      this.space.ambient.intensity *= 1 - wx.dim * 0.5;
      this.space.ambient.intensity += wx.flash * 1.5;
      this.sense = { alt, night: this.cities.night, s: this.cities.nearest?.t?.s || null, flash: wx.flash, mode: this.mode };
    } else {
      this.weather.update(dt, this.camera, null, alt, 0);
      this.space.light.intensity = 3.2;
      this.sense = { alt, night: 0, s: null, flash: 0, mode: this.mode };
    }
    if (alt > 2e5) {
      const wp = new THREE.Vector3();
      for (const p of this.space.planets) { p.mesh.getWorldPosition(wp); labels.push({ key: 'pl' + p.key, pos: wp.clone(), cls: 'planet', html: p.name, pri: 0 }); }
      this.space.sunGroup.getWorldPosition(wp); labels.push({ key: 'sun', pos: wp.clone(), cls: 'planet', html: '태양', pri: 0 });
      this.space.moon.getWorldPosition(wp); labels.push({ key: 'moon', pos: wp.clone(), cls: 'planet', html: '달', pri: 0 });
      labels.push({ key: 'earth', pos: new THREE.Vector3(0, R * 1.6, 0), cls: 'planet', html: '🌍 지구', pri: 0 });
    }
    this.updateRockets();
    this.labels.update(labels, this.camera, this.renderer.domElement.clientWidth, this.renderer.domElement.clientHeight);
    this.renderer.render(this.scene, this.camera);
  }
}
