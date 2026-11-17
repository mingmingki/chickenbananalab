"""OKX가 공개하는 하루 단위 체결 기록(zip/csv)을 받아서 봉으로 만든다.

파일 주소: https://www.okx.com/cdn/okex/traderecords/trades/daily/YYYYMMDD/{종목}-trades-YYYY-MM-DD.zip
OKX의 '하루'는 UTC+8 기준이다. 연속된 날짜 파일은 빈틈없이 이어진다.
"""
from __future__ import annotations

import csv
import io
import os
import pickle
import urllib.request
import zipfile
from datetime import date, timedelta
from typing import Iterator

from .bars import Bar, Bucketer

BASE_URL = "https://www.okx.com/cdn/okex/traderecords/trades/daily"
CACHE_VERSION = 1


def daily_file_name(symbol: str, day: date) -> str:
    return f"{symbol}-trades-{day.isoformat()}.zip"


def daily_url(symbol: str, day: date) -> str:
    return f"{BASE_URL}/{day.strftime('%Y%m%d')}/{daily_file_name(symbol, day)}"


def date_range(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("끝 날짜가 시작 날짜보다 빠릅니다")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def download_day(symbol: str, day: date, dest_dir: str, timeout: float = 120.0) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, daily_file_name(symbol, day))
    if os.path.exists(path) and zipfile.is_zipfile(path):
        return path
    tmp = path + ".part"
    req = urllib.request.Request(daily_url(symbol, day), headers={"User-Agent": "core-e-backtest/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    if not zipfile.is_zipfile(tmp):
        os.remove(tmp)
        raise RuntimeError(f"zip 파일이 아닙니다: {daily_url(symbol, day)}")
    os.replace(tmp, path)
    return path


def iter_trades_from_zip(path: str) -> Iterator[tuple[int, float, float, str]]:
    """(시각ms, 가격, 수량(계약), 'buy'|'sell')을 파일 순서대로 돌려준다."""
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"zip 안에 csv가 정확히 1개여야 합니다: {names}")
        with zf.open(names[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
            header = next(reader)
            col = {name: i for i, name in enumerate(header)}
            for need in ("side", "price", "size", "created_time"):
                if need not in col:
                    raise RuntimeError(f"csv에 {need} 열이 없습니다: {header}")
            i_side, i_px, i_sz, i_ts = col["side"], col["price"], col["size"], col["created_time"]
            for row in reader:
                if not row:
                    continue
                yield int(row[i_ts]), float(row[i_px]), float(row[i_sz]), row[i_side]


def bars_from_zip(path: str, bar_ms: int, cache_dir: str | None = None) -> list[Bar]:
    cache_path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        st = os.stat(path)
        key = f"{os.path.basename(path)}.{bar_ms}ms.{st.st_size}.v{CACHE_VERSION}.pkl"
        cache_path = os.path.join(cache_dir, key)
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                return [Bar(*row) for row in pickle.load(f)]
    bucketer = Bucketer(bar_ms)
    bars: list[Bar] = []
    for ts, px, sz, side in iter_trades_from_zip(path):
        done = bucketer.add(ts, px, sz, side)
        if done is not None:
            bars.append(done)
    last = bucketer.flush()
    if last is not None:
        bars.append(last)
    if cache_path:
        rows = [(b.start, b.end, b.open, b.high, b.low, b.close, b.buy_vol, b.sell_vol, b.count) for b in bars]
        tmp = cache_path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(rows, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, cache_path)
    return bars


def load_days(symbol: str, days: list[date], data_dir: str, bar_ms: int, *, download: bool = False) -> list[Bar]:
    bars: list[Bar] = []
    cache_dir = os.path.join(data_dir, "bars-cache")
    for day in days:
        path = os.path.join(data_dir, daily_file_name(symbol, day))
        if not os.path.exists(path):
            if not download:
                raise FileNotFoundError(f"{path} 가 없습니다(--download 를 붙이면 받아옵니다)")
            path = download_day(symbol, day, data_dir)
        day_bars = bars_from_zip(path, bar_ms, cache_dir)
        if bars and day_bars and day_bars[0].start <= bars[-1].start:
            # 날짜 경계에서 같은 구간이 두 파일에 나뉘어 있으면 합친다
            first = day_bars.pop(0)
            prev = bars[-1]
            prev.high = max(prev.high, first.high)
            prev.low = min(prev.low, first.low)
            prev.close = first.close
            prev.buy_vol += first.buy_vol
            prev.sell_vol += first.sell_vol
            prev.count += first.count
        bars.extend(day_bars)
    return bars
