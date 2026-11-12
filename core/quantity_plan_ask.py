"""Ask the AI about one marked spot of a plan: a person circles a part of the plan picture and says
what is wrong ("a wall is missing here", "this beam is 400 x 700"); the AI looks at that part of the
picture and its lines and proposes fixes in the editor's own terms (members added or taken out,
openings), which the person accepts one by one.

What is sent: one picture of the marked part (drawn in the browser from the plan picture), the
member lines inside it (sheet metres, with their colour class and layer) and the person's words.
Nothing is kept on the AI side (store=False); the proposals only become fixes when a person accepts
them. The cost is told before anything is sent (tokens; money when prices are set).
"""
from __future__ import annotations

import json
import math
import os
import re

from . import quantity_edits

MAX_LINES = 1500            # lines of the marked part sent along
MAX_TEXT = 800              # the person's words
MAX_IMAGE_BYTES = 2_000_000
MAX_PROPOSALS = 30
OUTPUT_TOKENS = 1500        # the answer itself
REASONING_TOKENS = 6000     # a rough allowance for the model's thinking at medium effort
MARGIN_M = 1.0              # proposals may reach this far past the marked part
CLASS_NAMES = {"frame": "골조(콘크리트 벽·기둥)", "beam": "보", "masonry": "조적", "alc": "ALC", "dry": "건식벽",
               "opening": "창호", "other": "분류 안 된 선", "removed": "뺀 선"}

SYSTEM = """당신은 한국 아파트·건축물 구조도면의 수량산출을 검토하는 구조 엔지니어입니다.
사람이 평면도의 한 구역을 표시하고 무엇이 문제인지 적었습니다. 그 구역 그림(이미지)과 선 목록을 보고,
사람의 말에 맞게 평면 읽기를 고칠 안을 내세요.

좌표: 도면 틀 왼쪽 아래가 원점, x는 오른쪽, y는 위쪽, 단위는 m입니다. 선 목록의 좌표도 같습니다.
선 분류: frame=골조(콘크리트 벽·기둥 선), beam=보(점선), masonry/alc/dry=비내력벽, opening=창호, other=분류 안 된 선, removed=이미 뺀 선.

고칠 수 있는 것 (fixes 배열의 항목):
- 벽 추가: {"op":"add","kind":"wall","a":[x,y],"b":[x,y],"t":두께m}  (a→b는 벽 중심선)
- 보 추가: {"op":"add","kind":"beam","a":[x,y],"b":[x,y],"w":폭m,"d":춤m}
- 기둥 추가: {"op":"add","kind":"column","c":[x,y],"w":가로m,"h":세로m}
- 개구부: {"op":"add","kind":"opening","a":[x,y],"b":[x,y],"t":벽두께m,"h":높이m}  (a→b는 벽 중심선 위)
- 잘못 읽은 부재 빼기: {"op":"remove","kind":"wall"|"beam","a":[x,y],"b":[x,y],"t" 또는 "w","d"} / {"op":"remove","kind":"column","c":[x,y],"w":..,"h":..}
각 항목에 "reason"(한국어 한 문장)을 붙이세요.

규칙:
- 표시한 구역 안(여백 1m 포함)만 고칩니다.
- 벽은 평행한 두 선(면) 사이 중심선, 두께는 두 선 사이 거리로 잡으세요. 선이 하나뿐이면 사람의 말이나 주변 벽 두께를 따르세요.
- 확실하지 않으면 고치지 말고 answer에 무엇을 확인해야 하는지 적으세요.
- 반드시 JSON 객체 하나만 반환: {"answer": "사람에게 하는 짧은 설명", "fixes": [...]}"""


def svg_lines(svg, box, limit=MAX_LINES):
    """Lines of a plan picture inside ``box`` [x0, y0, x1, y1] (sheet metres): [{"a", "b", "class",
    "layer"}], cut to ``limit`` (the longest first)."""
    height = re.search(r'viewBox="0 0 [\d.]+ ([\d.]+)"', svg or "")
    if not height:
        return []
    height = float(height.group(1))
    x0, y0, x1, y1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
    out = []
    for match in re.finditer(r'<path class="fv-([\w-]+)"(?: data-(?:layer|why)="([^"]*)")? d="([^"]+)"', svg):
        cls, layer, d = match.group(1), match.group(2) or "", match.group(3)
        for part in d.split("M")[1:]:
            points = []
            for pair in part.split("L"):
                values = pair.split()
                if len(values) == 2:
                    points.append((float(values[0]) / 100.0, (height - float(values[1])) / 100.0))
            for (ax, ay), (bx, by) in zip(points, points[1:]):
                if max(ax, bx) < x0 or min(ax, bx) > x1 or max(ay, by) < y0 or min(ay, by) > y1:
                    continue
                out.append({"a": [round(ax, 2), round(ay, 2)], "b": [round(bx, 2), round(by, 2)], "class": cls, "layer": layer[:60]})
    out.sort(key=lambda s: -math.hypot(s["b"][0] - s["a"][0], s["b"][1] - s["a"][1]))
    return out[:limit]


def image_tokens(width, height):
    """Image tokens as the vision models count them: 85 plus 170 per 512 px tile (after fitting the
    long side into 2048 and the short side into 768)."""
    if not width or not height:
        return 0
    scale = min(1.0, 2048.0 / max(width, height))
    w, h = width * scale, height * scale
    scale = min(1.0, 768.0 / min(w, h))
    w, h = w * scale, h * scale
    return 85 + 170 * math.ceil(w / 512.0) * math.ceil(h / 512.0)


def prices():
    """(USD per million input tokens, per million output tokens, KRW per USD) from the environment,
    or Nones when not set: the money is then not guessed."""
    def number(name):
        try:
            value = float(os.environ.get(name, ""))
        except ValueError:
            return None
        return value if value > 0 else None
    return number("OPENAI_QUANTITY_PRICE_IN"), number("OPENAI_QUANTITY_PRICE_OUT"), number("QTY_KRW_PER_USD") or 1400.0


def estimate(text, lines, image_size):
    """What a question will send and roughly cost: {"input_tokens", "output_tokens", "krw" or None}."""
    prompt = len(SYSTEM) + len(text or "") + len(json.dumps(lines, ensure_ascii=False)) + 400
    tokens_in = int(prompt / 2.2) + image_tokens(*image_size)  # Korean and numbers: about 2 characters a token
    tokens_out = OUTPUT_TOKENS + REASONING_TOKENS
    price_in, price_out, krw = prices()
    money = None
    if price_in and price_out:
        money = round((tokens_in * price_in + tokens_out * price_out) / 1_000_000 * krw)
    return {"input_tokens": tokens_in, "output_tokens": tokens_out, "krw": money}


def prompt(text, lines, box, plan_note):
    return (f"표시한 구역: x {box[0]:.2f}~{box[2]:.2f} m, y {box[1]:.2f}~{box[3]:.2f} m.\n"
            f"이 평면: {plan_note or '구조평면'}\n"
            f"사람이 적은 말: {text}\n"
            "구역 안 선 목록 (class는 선 분류, layer는 CAD 레이어):\n"
            + json.dumps(lines, ensure_ascii=False, separators=(",", ":")))


def proposals(raw_text, plan, box):
    """The AI's answer as checked fixes on ``plan``, kept to the marked part (plus MARGIN_M)."""
    try:
        data = json.loads(raw_text or "{}")
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", raw_text or "", re.S)
        data = json.loads(match.group(0)) if match else {}
    if not isinstance(data, dict):
        data = {}
    x0, y0, x1, y1 = box[0] - MARGIN_M, box[1] - MARGIN_M, box[2] + MARGIN_M, box[3] + MARGIN_M

    def inside(point):
        return point is not None and x0 <= point[0] <= x1 and y0 <= point[1] <= y1
    fixes, reasons = [], []
    for raw in (data.get("fixes") or [])[:MAX_PROPOSALS]:
        if not isinstance(raw, dict):
            continue
        cleaned = quantity_edits.clean([dict(raw, plan=plan)])
        if not cleaned:
            continue
        fix = cleaned[0]
        points = [fix["c"]] if fix["kind"] == "column" else [fix["a"], fix["b"]]
        if not all(inside(p) for p in points):
            continue
        fix["reason"] = str(raw.get("reason") or "")[:200]
        fix["by"] = "ai"
        fixes.append(fix)
    return {"answer": str(data.get("answer") or "")[:1000], "fixes": fixes}


def ask(client, text, lines, box, image, plan, plan_note=""):
    """One question to the AI (``client``: quantity_ai client). Returns the checked proposals and
    the tokens used."""
    from . import quantity_ai
    contents = [prompt(text, lines, box, plan_note)]
    if image:
        contents.append(quantity_ai.image_part(image[0], image[1]))
    response = client.generate(contents=contents, system=SYSTEM, max_output_tokens=OUTPUT_TOKENS, effort="medium", timeout=240)
    result = proposals(response.text, plan, box)
    result["usage"] = {"input_tokens": response.input_tokens, "output_tokens": response.output_tokens + response.reasoning_tokens}
    price_in, price_out, krw = prices()
    if price_in and price_out:
        result["usage"]["krw"] = round((response.input_tokens * price_in + (response.output_tokens + response.reasoning_tokens) * price_out) / 1_000_000 * krw)
    return result
