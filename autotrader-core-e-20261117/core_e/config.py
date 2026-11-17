"""Core E(초단타 엔진) 설정.

이 패키지는 '모의 매매(paper)' 전용이다. mode에 'paper' 말고 다른 값을 넣으면
시작 자체가 거부된다 - 실주문 코드는 이 패키지 어디에도 없다.

단위
- bp: 0.01% (10bp = 0.1%)
- *_sec: 초, *_ms: 밀리초
- 금액: USDT
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace

STYLES = ("momentum", "fade")


@dataclass(frozen=True)
class CoreEConfig:
    symbol: str = "ZEC-USDT-SWAP"
    mode: str = "paper"

    # OKX 계약 규격 (ZEC-USDT-SWAP: 1계약 = 0.01 ZEC, 호가 단위 0.01 USDT)
    ct_val: float = 0.01
    tick_size: float = 0.01
    lot_size: float = 1.0

    # 한 번 진입할 때의 포지션 금액(레버리지 포함 명목가)과 레버리지
    notional_usdt: float = 300.0
    leverage: int = 3

    # 비용 - OKX 일반 등급 기준. 진입·익절은 지정가(maker), 손절·시간청산은 시장가(taker)
    maker_fee_bp: float = 2.0
    taker_fee_bp: float = 5.0
    taker_slippage_bp: float = 1.0
    # 판단 후 주문이 거래소에 닿기까지 걸린다고 가정하는 시간
    order_latency_ms: int = 150
    # 체결을 이 단위(ms)로 묶어서 본다
    bar_ms: int = 100

    # 신호: 최근 window_sec 동안 가격이 min_move_bp 이상 움직이고, 한쪽 체결이
    # min_imbalance 이상 몰리고, 거래량이 평소(baseline_sec 평균)의 min_volume_ratio배 이상
    style: str = "momentum"  # momentum = 따라가기, fade = 반대로 받기
    window_sec: float = 20.0
    baseline_sec: float = 600.0
    min_volume_ratio: float = 3.0
    min_imbalance: float = 0.4
    min_move_bp: float = 15.0
    max_move_bp: float = 80.0

    # 진입: 현재가보다 entry_offset_bp 유리한 가격에 지정가를 걸고 entry_timeout_sec 안에
    # 안 잡히면 취소
    entry_offset_bp: float = 3.0
    entry_timeout_sec: float = 10.0

    # 청산
    take_profit_bp: float = 15.0
    stop_loss_bp: float = 12.0
    max_hold_sec: float = 180.0

    # 위험 관리 (하루 = 한국 시간 0시 기준)
    cooldown_sec: float = 30.0
    max_trades_per_day: int = 150
    daily_loss_limit_usdt: float = 15.0
    max_consecutive_losses: int = 5
    loss_pause_sec: float = 1800.0

    def __post_init__(self) -> None:
        if self.mode != "paper":
            raise ValueError(
                f"Core E는 모의 매매 전용입니다(mode='paper'만 허용). 받은 값: {self.mode!r}"
            )
        if self.style not in STYLES:
            raise ValueError(f"style은 {STYLES} 중 하나여야 합니다: {self.style!r}")
        positive = (
            "ct_val", "tick_size", "lot_size", "notional_usdt", "bar_ms",
            "window_sec", "baseline_sec", "min_volume_ratio", "min_move_bp",
            "entry_timeout_sec", "take_profit_bp", "stop_loss_bp", "max_hold_sec",
            "daily_loss_limit_usdt", "max_trades_per_day", "max_consecutive_losses",
            "leverage",
        )
        for name in positive:
            if not getattr(self, name) > 0:
                raise ValueError(f"{name}는 0보다 커야 합니다: {getattr(self, name)!r}")
        non_negative = (
            "maker_fee_bp", "taker_fee_bp", "taker_slippage_bp", "order_latency_ms",
            "entry_offset_bp", "cooldown_sec", "loss_pause_sec",
        )
        for name in non_negative:
            if getattr(self, name) < 0:
                raise ValueError(f"{name}는 음수일 수 없습니다: {getattr(self, name)!r}")
        if not 0 <= self.min_imbalance <= 1:
            raise ValueError(f"min_imbalance는 0~1 사이여야 합니다: {self.min_imbalance!r}")
        if self.max_move_bp < self.min_move_bp:
            raise ValueError("max_move_bp는 min_move_bp보다 작을 수 없습니다")
        if self.window_sec >= self.baseline_sec:
            raise ValueError("window_sec는 baseline_sec보다 짧아야 합니다")

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, **overrides) -> "CoreEConfig":
        _reject_unknown(overrides)
        return replace(self, **overrides)

    @classmethod
    def from_dict(cls, data: dict) -> "CoreEConfig":
        _reject_unknown(data)
        return cls(**data)

    @classmethod
    def from_json_file(cls, path: str) -> "CoreEConfig":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


def _reject_unknown(data: dict) -> None:
    known = {f.name for f in fields(CoreEConfig)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"알 수 없는 설정 이름: {unknown}")


def parse_override(text: str) -> tuple[str, object]:
    """'take_profit_bp=20' 같은 문자열을 (이름, 알맞은 타입의 값)으로 바꾼다."""
    if "=" not in text:
        raise ValueError(f"'이름=값' 형식이어야 합니다: {text!r}")
    name, _, raw = text.partition("=")
    name = name.strip()
    types = {f.name: f.type for f in fields(CoreEConfig)}
    if name not in types:
        raise ValueError(f"알 수 없는 설정 이름: {name!r}")
    kind = types[name]
    raw = raw.strip()
    if kind in ("int", int):
        return name, int(raw)
    if kind in ("float", float):
        return name, float(raw)
    return name, raw
