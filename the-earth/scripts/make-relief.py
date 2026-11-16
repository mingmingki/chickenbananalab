#!/usr/bin/env python3
"""지구 전체 그림(aux-2k.png)의 높이(R)와 바다 깊이(B)를 실제 고도 자료로 다시 만든다.

가까이 볼 때 쓰는 실제 지형 타일(server/tiles.js · public/js/scene/terrain.js)과 같은 자료에서 뽑아야,
멀리서 본 지구와 가까이서 본 지형의 산 높이·바다 깊이가 이어진다 (확대 영역의 경계가 보이지 않게).

자료: AWS 공개 자료 'Terrain Tiles' (terrarium 형식, z4 = 4096칸 메르카토르) — SRTM·GMTED2010·ETOPO1 등을 모은 것.
      출처 표기는 README '지형 자료' 항목 참고.

  R = 높이 (0~1 = 0~6,400m, 바다는 0)
  G = 물(바다·호수) — 그대로 둔다
  B = 바다 깊이 × 0.6  (셰이더와 같은 식: pow(smoothstep(0, 4200m, 깊이), 0.6)), 호수는 1 — 호수는 그대로 둔다
  위도 85° 너머(메르카토르 타일이 없는 곳)는 예전 값을 그대로 둔다.

실행: python3 scripts/make-relief.py      (타일은 scripts/.cache/terrarium 에 받아 둔다)
"""
import io
import os
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TEX = ROOT / 'public' / 'textures' / 'earth'
CACHE = ROOT / 'scripts' / '.cache' / 'terrarium'
URL = os.environ.get('TERRAIN_TILES_URL', 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png')
Z = 4
N = 2 ** Z
TS = 256


def tile(x, y):
    f = CACHE / str(Z) / str(x) / f'{y}.png'
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        url = URL.format(z=Z, x=x, y=y)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    data = r.read()
                break
            except Exception as e:  # 잠깐 끊기면 다시
                if attempt == 3:
                    raise
                print('다시 받기', url, e)
                time.sleep(2 ** attempt)
        f.write_bytes(data)
    a = np.asarray(Image.open(io.BytesIO(f.read_bytes())).convert('RGB')).astype(np.float64)
    return a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0


def main():
    print(f'z{Z} 타일 {N * N}장 이어 붙이기')
    mosaic = np.zeros((N * TS, N * TS), dtype=np.float32)
    for y in range(N):
        for x in range(N):
            mosaic[y * TS:(y + 1) * TS, x * TS:(x + 1) * TS] = tile(x, y)
    M = N * TS

    aux = np.asarray(Image.open(TEX / 'aux-2k.png').convert('RGB')).astype(np.float32) / 255.0
    H, W = aux.shape[:2]

    # 등장방형 칸마다 2×2 로 나눠 메르카토르 지도에서 선형 보간으로 읽고 평균
    def sample(lat, lon):
        mx = (lon + 180.0) / 360.0 * M - 0.5
        r = np.radians(np.clip(lat, -85.05, 85.05))
        my = (0.5 - np.log(np.tan(np.pi / 4 + r / 2)) / (2 * np.pi)) * M - 0.5
        x0 = np.floor(mx).astype(int); y0 = np.clip(np.floor(my).astype(int), 0, M - 2)
        fx = mx - np.floor(mx); fy = np.clip(my - y0, 0, 1)
        x0m, x1m = x0 % M, (x0 + 1) % M
        a = mosaic[y0, x0m] * (1 - fx) + mosaic[y0, x1m] * fx
        b = mosaic[y0 + 1, x0m] * (1 - fx) + mosaic[y0 + 1, x1m] * fx
        return a * (1 - fy) + b * fy

    acc = np.zeros((H, W), dtype=np.float64)
    for sy in (0.25, 0.75):
        for sx in (0.25, 0.75):
            lat = 90.0 - (np.arange(H)[:, None] + sy) / H * 180.0
            lon = -180.0 + (np.arange(W)[None, :] + sx) / W * 360.0
            acc += sample(np.broadcast_to(lat, (H, W)), np.broadcast_to(lon, (H, W)))
    h = acc / 4.0

    lat = 90.0 - (np.arange(H) + 0.5) / H * 180.0
    ok = (np.abs(lat) < 85.0)[:, None].repeat(W, axis=1)
    lake = aux[..., 2] > 0.99

    height = np.clip(h, 0, None) / 6400.0
    t = np.clip(-h / 4200.0, 0, 1)
    depth = np.power(t * t * (3 - 2 * t), 0.6) * 0.6
    R = np.where(ok, height, aux[..., 0])          # 바다는 높이가 0 아래라 저절로 0
    B = np.where(lake, 1.0, np.where(ok, depth, aux[..., 2]))
    out = np.stack([R, aux[..., 1], B], axis=-1)
    Image.fromarray(np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB').save(TEX / 'aux-2k.png', optimize=True)
    print('저장:', TEX / 'aux-2k.png', '최고', round(float(h.max())), 'm, 가장 깊은 곳', round(float(h.min())), 'm')


if __name__ == '__main__':
    main()
