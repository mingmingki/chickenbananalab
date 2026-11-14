// 3D 세계: 렌더러, 우주, 지구, 도시, 카메라, 선택, 인트로 연출
import * as THREE from 'three';
import { EarthVectors } from './vectors.js';
import { R, D2R, vecToLL, llToVec } from './geo.js';
import { buildEarthTextures, makeEarth, paintLights, countryIndexAt, loadEarthData } from './earth.js';
import { geoLabels } from './geolabels.js';
import { dateOf } from '../state.js';
import { makeSpace, glowTexture } from './space.js';
import { CameraRig } from './camera.js';
import { Cities } from './cities.js';
import { Labels } from './labels.js';
import { Weather } from './weather.js';
import { PHONE, TOUCH } from '../device.js';

export class World3D {
  constructor(container, meta, countries, borders = null) {
    this.meta = meta;
    this.countries = countries;
    const renderer = this.renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true, powerPreference: 'high-performance' });
    // 휴대폰은 화면 밀도가 3배라 그대로 그리면 느리고 뜨거워진다 → 1.5배까지만
    renderer.setPixelRatio(Math.min(PHONE ? 1.5 : 2, devicePixelRatio));
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x03060d);
    this.camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.001, 3e10);

    // 우주(별·행성·태양·달)는 보여 주지 않는다. 낮과 밤을 만드는 햇빛만 쓴다
    this.space = makeSpace();
    this.scene.add(this.space.equatorial, this.space.ambient);
    for (const o of [this.space.sunMesh, this.space.sunGlow, this.space.sunFar, this.space.moon, this.space.planets.earthOrbit]) if (o) o.visible = false;
    for (const p of this.space.planets) { p.mesh.visible = false; if (p.orbit) p.orbit.visible = false; }
    this.earthRoot = new THREE.Group();
    this.scene.add(this.earthRoot);
    this.tex = buildEarthTextures(countries);
    this.earth = makeEarth(this.tex);
    // 땅과 바다는 셰이더가 직접 그린다. 어디가 숲·사막·얼음인지, 바다가 얼마나 깊은지 담은 자료만 받는다
    this.ready = loadEarthData().then(d => { if (d) this.earth.setData(d); });
    this.earthRoot.add(this.earth.group);
    // 국경선·나라 테두리는 선으로 (확대해도 또렷하게)
    this.vec = new EarthVectors(this.earth.group, countries, borders, renderer);
    this.cities = new Cities(this.earthRoot, meta, this.earth.mesh);
    this.weather = new Weather(this.scene);
    this.rig = new CameraRig(this.camera, renderer.domElement);
    this.rig.pickLL = (x, y) => this.earthHit({ clientX: x, clientY: y });
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
      this.vec.resize();
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
    // 지구는 공이므로 삼각형 13만 개를 뒤지지 않고 공과 직선의 만남을 바로 계산한다 (휴대폰에서 끌 때 가볍게)
    const o = this.ray.ray.origin.clone().sub(this.earthRoot.position), d = this.ray.ray.direction;
    const b = o.dot(d), c = o.lengthSq() - R * R, disc = b * b - c;
    if (disc < 0) return null;
    const t = -b - Math.sqrt(disc);
    if (t < 0) return null;
    return vecToLL(o.addScaledVector(d, t));
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
    this.vec.setMine(this.myCountry);
  }
  setSelectedCountry(idx) {
    if (idx === this.selectedCountry) return;
    this.selectedCountry = idx;
    this.earth.uniforms.hoverId.value = -1;
    this.tex.paintOverlay(idx ?? -1, this.myCountry ?? -1);
    this.vec.setSelected(idx ?? -1);
  }

  flyToCountry(c, alt = 3200) {
    this.rig.flyTo({ lat: c.centroid[1], lon: c.centroid[0], alt, heading: 0 });
  }

  // ── 인트로: 어둠 속에서 지구가 천천히 떠오른다 (우주 장면은 없다) ──
  playIntro(onLanded) {
    this.mode = 'intro';
    this.rig.enabled = false;
    this.clockOverride = 172 * 1440 + 395; // 북반구 여름 아침 무렵의 빛
    this.rig.jump({ lat: 22, lon: 118, alt: 30000, tilt: 90, heading: 0 });
    this.rig.flyTo({ lat: 22, lon: 128, alt: 21000, tilt: 90, heading: 0 });
    this.lift = this.liftTarget = 0.13;
    const el = this.renderer.domElement;
    el.style.transition = 'none'; el.style.opacity = '0';
    requestAnimationFrame(() => { el.style.transition = 'opacity 1.4s ease'; el.style.opacity = '1'; });
    const t0 = performance.now();
    this.introSkip = () => { this.introT = 99; };
    this.introStep = () => {
      const t = this.introT ?? (performance.now() - t0) / 1000;
      if (t < 1.2) return;
      el.style.opacity = '1';
      this.introStep = null;
      this.mode = 'menu';
      onLanded?.();
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
    // 휴대폰: 만지거나 움직이는 동안은 초당 60장으로 부드럽게, 가만히 있을 땐 30장 (배터리·발열)
    if (PHONE && !this.rig.busy() && ts - (this.lastTs || 0) < 30) return;
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
    const U = this.earth.uniforms;
    if (this.earth.imageryOn && U.imagery.value < 1) U.imagery.value = Math.min(1, U.imagery.value + dt * 1.2);
    // 계절: 1월 = 북반구 한겨울(1), 7월 = 한여름(0). 게임 속 날짜(관찰 중) 또는 오늘 날짜(메뉴)
    const doy = this.mode === 'observe' && st?.world ? dateOf(minutes).doy : Math.floor((Date.now() - Date.UTC(new Date().getUTCFullYear(), 0, 1)) / 864e5);
    U.season.value = 0.5 + 0.5 * Math.cos((doy - 15) / 365 * Math.PI * 2);
    U.time.value = this.time;
    // 구름은 게임 속 20일에 지구를 한 바퀴 (그림자도 같이)
    U.cloudShift.value = ((minutes / (1440 * 20)) + this.time * 0.0004) % 1;
    U.cloudAmt.value = this.earth.cloudU.opacity.value;
    // 국경선: 나라를 고를 때는 또렷하게, 마을 가까이 내려가면 사라진다
    // (국경 그림은 4K라 1,500km 아래로 내려가면 계단처럼 보인다 → 그 전에 사라지게)
    // (예전엔 국경을 그림으로 그려 가까이서 계단처럼 깨졌다 → 이제 선으로 그린다: vectors.js)
    U.borderAlpha.value = 0;
    this.vec.update(alt, this.mode);
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
    this.updateRockets();
    if (this.mode === 'observe' || this.mode === 'pick-country') labels.push(...geoLabels(alt));
    this.labels.update(labels, this.camera, this.renderer.domElement.clientWidth, this.renderer.domElement.clientHeight);
    this.renderer.render(this.scene, this.camera);
  }
}
