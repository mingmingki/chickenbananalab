#!/usr/bin/env python3
"""지구 지형 데이터 만들기 — 위성 사진을 '그림'이 아니라 '자료'로만 쓴다.

화면의 땅과 바다는 셰이더가 직접 그린다 (public/js/scene/earth.js). 그때 어디가 숲이고 사막이고
얼음인지, 바다가 얕은지 깊은지만 알면 되므로, NASA 블루 마블 사진(퍼블릭 도메인, 7월·1월)의 색에서
그 값을 뽑아 작은 그림 세 장에 담는다. (투명도 칸은 브라우저가 지워 버릴 수 있어 RGB만 쓴다)

  aux-2k.png     R = 높이 · G = 물(바다·호수) · B = 바다 깊이 × 0.6 (0 얕음 ~ 0.6 깊음), 호수는 1
  terrain-2k.png R = 식생 (0 맨땅 · 0.5 초원 · 1 숲) · G = 건조함 (1 사막) · B = 늘 덮인 얼음·만년설
  climate-1k.png R = 겨울에만 덮이는 눈 (북반구 1월, 남반구 7월) · G = 붉은 정도 (붉은 사막·황토) · B = 0

땅의 값은 바다 쪽으로, 바다 깊이는 땅 쪽으로 번지게 채워 둔다 — 해안에서 두 값이 섞여도 어색하지 않게.

실행: python3 scripts/make-terrain.py
"""
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
TEX = ROOT / 'public' / 'textures' / 'earth'
SRC = ROOT / 'scripts' / 'earth-src'   # 원본 사진 (게임은 받지 않는다)


def load(name, mode='RGB'):
    return np.asarray(Image.open((SRC if name.startswith('day-') else TEX) / name).convert(mode)).astype(np.float32) / 255.0


def smooth(x, r=1.2):
    im = Image.fromarray(np.clip(x * 255.0 + 0.5, 0, 255).astype(np.uint8), 'L')
    return np.asarray(im.filter(ImageFilter.GaussianBlur(r))).astype(np.float32) / 255.0


def sstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


summer = load('day-summer-2k.jpg')   # 7월
winter = load('day-winter-2k.jpg')   # 1월
aux = load('aux-2k.png', 'RGB')
H, W = aux.shape[:2]
height = aux[..., 0]
water = aux[..., 1] > 0.5

# ── 호수: 바다와 이어지지 않은 물 (가장 큰 물 덩어리 = 바다, 좌우는 이어져 있다) ──
seen = np.zeros((H, W), dtype=bool)
comps = []
for y0 in range(H):
    for x0 in range(W):
        if not water[y0, x0] or seen[y0, x0]:
            continue
        q = deque([(y0, x0)])
        seen[y0, x0] = True
        pix = []
        while q:
            y, x = q.popleft()
            pix.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, (x + dx) % W
                if 0 <= ny < H and water[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        comps.append(pix)
comps.sort(key=len, reverse=True)
lake = np.zeros((H, W), dtype=np.float32)
for pix in comps[1:]:
    if len(pix) < 6:          # 한두 칸짜리 물웅덩이는 확대하면 네모로 보이므로 땅으로 둔다
        continue
    ys, xs = zip(*pix)
    lake[list(ys), list(xs)] = 1.0
print('물 덩어리', len(comps), '바다', len(comps[0]), '호수 칸', int(lake.sum()))

# ── 땅의 성질 ──
def fields(img):
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    lum = 0.3 * r + 0.59 * g + 0.11 * b
    white = np.minimum(np.minimum(r, g), b)
    gr = g / (r + 0.02)
    return r, g, b, lum, white, gr


rs, gs, bs, lum_s, white_s, gr_s = fields(summer)
rw, gw, bw, lum_w, white_w, gr_w = fields(winter)
lat = 90.0 - (np.arange(H) + 0.5) / H * 180.0
north = (lat > 0)[:, None].repeat(W, axis=1)
# 계절이 맞는 쪽(눈이 없는 여름) 사진으로 식생을 본다: 북반구는 7월, 남반구는 1월
gr = np.where(north, gr_s, gr_w)
lum = np.where(north, lum_s, lum_w)
red = np.where(north, rs / (gs + 0.02), rw / (gw + 0.02))
forest = sstep(1.18, 1.55, gr) * sstep(0.36, 0.12, lum)
grass = sstep(0.84, 1.12, gr)
veg = np.clip(grass * 0.55 + forest * 0.45, 0, 1)
dry = sstep(1.02, 0.72, gr) * sstep(0.08, 0.3, lum)
ice = sstep(0.55, 0.8, np.minimum(white_s, white_w))
snow_n = np.clip(sstep(0.45, 0.72, white_w) - sstep(0.45, 0.72, white_s), 0, 1)
snow_s = np.clip(sstep(0.45, 0.72, white_s) - sstep(0.45, 0.72, white_w), 0, 1)
snow = np.where(north, snow_n, snow_s)
veg = np.where(ice > 0.5, 0, veg)
redness = sstep(1.05, 1.6, red)

# ── 바다 깊이: 대륙붕은 밝고 깊은 바다는 어둡다 ──
sea_l = (summer[..., 1] + summer[..., 2]) * 0.5
depth = 1.0 - sstep(0.08, 0.36, sea_l)

def spread(val, valid, steps=10, default=0.0):
    """valid 가 아닌 칸을 가까운 valid 칸의 평균으로 채운다 (좌우는 이어져 있다)"""
    v = np.where(valid, val, 0.0).astype(np.float32)
    ok = valid.astype(np.float32)
    for _ in range(steps):
        acc = np.zeros_like(v); cnt = np.zeros_like(v)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            acc += np.roll(np.roll(v * ok, dy, 0), dx, 1); cnt += np.roll(np.roll(ok, dy, 0), dx, 1)
        fill = (ok == 0) & (cnt > 0)
        v = np.where(fill, acc / np.maximum(cnt, 1), v)
        ok = np.where(fill, 1.0, ok)
    return np.where(ok > 0, v, default)


land = ~water
veg_f, dry_f, ice_f, snow_f, red_f = (spread(x, land) for x in (veg, dry, ice, snow, redness))
depth_f = spread(depth, water & (lake < 0.5), steps=6, default=0.15)
def save(arr, name, size=None):
    im = Image.fromarray(np.clip(arr * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB')
    if size:
        im = im.resize(size, Image.LANCZOS)
    im.save(TEX / name, optimize=True)


depth_b = np.where(lake > 0.5, 1.0, smooth(depth_f, 1.0) * 0.6)
save(np.stack([height, aux[..., 1], depth_b], axis=-1), 'aux-2k.png')
save(np.stack([smooth(veg_f), smooth(dry_f, 0.8), smooth(ice_f, 0.8)], axis=-1), 'terrain-2k.png')
save(np.stack([smooth(snow_f, 2.0), smooth(red_f, 2.0), np.zeros_like(snow_f)], axis=-1), 'climate-1k.png', (W // 2, H // 2))
print('저장:', TEX)
