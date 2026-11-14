#!/usr/bin/env python3
"""강과 호수 자료 만들기 — Natural Earth (퍼블릭 도메인) → public/geo/hydro.json

  강:   ne_10m_rivers_lake_centerlines (크기 등급 scalerank 0~7 과 압록강·낙동강·두만강, 약 450m 오차로 단순화)
  호수: ne_50m_lakes (한국어 이름 포함)

좌표는 0.002도(약 220m) 단위 정수로, 각 점은 앞 점과의 차이로 적어 파일을 줄인다.
  rivers: [[등급, x0, y0, dx1, dy1, dx2, dy2, …], …]      (x = 경도×500, y = 위도×500)
  lakes:  [[등급, 이름, [고리1], [고리2], …], …]           (고리도 같은 방식)

실행: python3 scripts/make-hydro.py <Natural Earth geojson 폴더>
  (github.com/nvkelso/natural-earth-vector 의 geojson/ 에서
   ne_10m_rivers_lake_centerlines.geojson, ne_50m_lakes.geojson 두 파일)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'public' / 'geo' / 'hydro.json'
Q = 500            # 1도 = 500 칸
TOL = 0.004        # 단순화 허용 오차 (도)
MAX_RANK = 7
EXTRA = {'Yalu', 'Nakdong', 'Tumen'}   # 등급은 작아도 한반도의 큰 강 (압록강·낙동강·두만강)은 넣는다


def simplify(pts, tol):
    """더글러스-포이커: 선 모양을 지키며 점을 줄인다"""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = (dx * dx + dy * dy) ** 0.5
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            # 고리처럼 처음과 끝이 같은 점이면 그 점에서의 거리로 잰다
            d = abs((px - ax) * dy - (py - ay) * dx) / L if L > 1e-12 else ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            if d > best:
                best, bi = d, i
        if best > tol:
            keep[bi] = True
            stack.append((a, bi))
            stack.append((bi, b))
    return [p for p, k in zip(pts, keep) if k]


def encode(pts):
    out, px, py = [], None, None
    for lon, lat in pts:
        x, y = round(lon * Q), round(lat * Q)
        if px is None:
            out += [x, y]
        else:
            if x == px and y == py:
                continue
            out += [x - px, y - py]
        px, py = x, y
    return out


def lines_of(geom):
    if geom['type'] == 'LineString':
        return [geom['coordinates']]
    if geom['type'] == 'MultiLineString':
        return geom['coordinates']
    return []


def polys_of(geom):
    if geom['type'] == 'Polygon':
        return [geom['coordinates']]
    if geom['type'] == 'MultiPolygon':
        return geom['coordinates']
    return []


src = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
rv = json.loads((src / 'ne_10m_rivers_lake_centerlines.geojson').read_text())
lk = json.loads((src / 'ne_50m_lakes.geojson').read_text())

rivers, npts = [], 0
for f in rv['features']:
    r = f['properties'].get('scalerank')
    if r is None or not f.get('geometry'):
        continue
    if r > MAX_RANK:
        if f['properties'].get('name') not in EXTRA:
            continue
        r = MAX_RANK
    for ln in lines_of(f['geometry']):
        s = simplify([(p[0], p[1]) for p in ln], TOL)
        e = encode(s)
        if len(e) >= 4:
            rivers.append([int(r)] + e)
            npts += len(e) // 2

lakes = []
for f in lk['features']:
    p = f['properties']
    if not f.get('geometry'):
        continue
    rings = []
    for poly in polys_of(f['geometry']):
        for ring in poly:
            s = simplify([(q[0], q[1]) for q in ring], TOL * 0.75)
            e = encode(s)
            if len(e) >= 6:
                rings.append(e)
    if rings:
        lakes.append([int(p.get('scalerank') or 0), p.get('name_ko') or p.get('name') or ''] + rings)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({'q': Q, 'rivers': rivers, 'lakes': lakes}, ensure_ascii=False, separators=(',', ':')))
print(f'강 {len(rivers)}줄 · 점 {npts} · 호수 {len(lakes)} → {OUT} ({OUT.stat().st_size // 1024} KB)')
