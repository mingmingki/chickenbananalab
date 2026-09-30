"""검토 주기(POLL_INTERVAL_SECONDS)에 맞춰 함께 볼 캔들 타임프레임을 정하는 사다리.

검토 주기가 길수록 더 큰 타임프레임까지 포함한다.
예: 5분 주기 -> 1분+3분+5분, 15분 주기 -> 1분+3분+5분+15분, 1시간 주기 -> ...+1시간.

여기에 더해, "얼마나 자주 재평가할지(검토 주기)"와 "얼마나 큰 흐름까지 볼지"는 서로 다른
문제라서 - 검토 주기가 짧아도(예: 5분마다 재평가) 1시간/4시간봉처럼 큰 추세를 계속 같이
보여줘서, 짧은 타임프레임으로 진입 타이밍만 잡고 방향은 큰 흐름을 따르게 한다.
"""

LADDER = [
    ("1m", "1분봉", 60),
    ("3m", "3분봉", 180),
    ("5m", "5분봉", 300),
    ("15m", "15분봉", 900),
    ("1h", "1시간봉", 3600),
    ("4h", "4시간봉", 14400),
    ("1d", "1일봉", 86400),
]

# 검토 주기와 무관하게 항상 같이 보여주는 큰 추세 판단용 타임프레임.
# 1d(일봉)는 시장 전체 레짐(regime) 판단의 최상위 기준으로 쓰인다 - 1분/3분봉 같은 낮은
# 타임프레임의 일시적 움직임이 이 방향을 뒤집으면 안 된다는 게 프롬프트의 핵심 규칙이다.
TREND_ANCHORS = ["1h", "4h", "1d"]


def for_interval(poll_seconds: int) -> list[str]:
    tfs = [code for code, _, secs in LADDER if secs <= poll_seconds]
    if not tfs:
        tfs = ["1m"]
    for anchor in TREND_ANCHORS:
        if anchor not in tfs:
            tfs.append(anchor)
    return tfs


def label(code: str) -> str:
    for tf_code, kr, _ in LADDER:
        if tf_code == code:
            return kr
    return code


def describe(poll_seconds: int) -> str:
    return ", ".join(label(tf) for tf in for_interval(poll_seconds))
