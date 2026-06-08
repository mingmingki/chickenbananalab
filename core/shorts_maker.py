import html
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageDraw, ImageFilter, ImageFont


WIDTH = 1080
HEIGHT = 1920
COVER_WIDTH = 900
COVER_HEIGHT = 1600
FPS = 30


def _media_root():
    return Path(getattr(settings, "MEDIA_ROOT", Path(settings.BASE_DIR) / "media"))


def _ffmpeg_bin():
    candidates = [
        shutil.which("ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    raise RuntimeError("ffmpeg를 찾지 못했습니다. 터미널에서 which ffmpeg를 확인하세요.")


def _run_ffmpeg(cmd):
    if cmd and cmd[0] == "ffmpeg":
        cmd[0] = _ffmpeg_bin()

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-3000:])


def _safe_text(value):
    if not value:
        return ""

    text = str(value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _limit(text, max_len):
    text = _safe_text(text)
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def _font_path():
    candidates = [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/Library/Fonts/AppleGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


def _font(size):
    path = _font_path()

    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass

    return ImageFont.load_default()


def _text_len(draw, text, font):
    try:
        return draw.textlength(text, font=font)
    except Exception:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0]


def _wrap_text(draw, text, font, max_width, max_lines=None):
    text = _safe_text(text)
    if not text:
        return []

    lines = []

    for paragraph in re.split(r"\n+", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        line = ""

        for ch in paragraph:
            test = line + ch
            if _text_len(draw, test, font) <= max_width:
                line = test
            else:
                if line:
                    lines.append(line)
                line = ch

        if line:
            lines.append(line)

    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,!?") + "…"

    return lines


def _draw_text_center(draw, lines, font, y, fill, stroke_fill=None, stroke_width=0, line_gap=18):
    for line in lines:
        x = int((WIDTH - _text_len(draw, line, font)) / 2)
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        box = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        y += (box[3] - box[1]) + line_gap

    return y


def _draw_round_rect(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _field_path(field):
    if not field:
        return None

    try:
        if not getattr(field, "name", ""):
            return None

        path = field.path
        if path and os.path.exists(path):
            return path
    except Exception:
        return None

    return None


def _image_candidates(post):
    candidates = [
        _field_path(getattr(post, "thumbnail", None)),
        _field_path(getattr(post, "content_image", None)),
    ]

    return [p for p in candidates if p]


def _sentences(text):
    text = _safe_text(text)
    if not text:
        return []

    raw = re.split(r"(?<=[.!?。！？])\s+|[\n\r]+", text)
    result = []

    for item in raw:
        item = item.strip()
        if len(item) >= 16:
            result.append(item)

    if not result:
        for i in range(0, len(text), 75):
            chunk = text[i:i + 75].strip()
            if chunk:
                result.append(chunk)

    cleaned = []
    for item in result:
        item = re.sub(r"#{1,6}\s*", "", item)
        item = item.strip()
        if item and item not in cleaned:
            cleaned.append(item)

    return cleaned


def _category_name(post):
    category = _safe_text(getattr(post, "category", ""))
    return category.lower()


def _detect_template(post):
    title = _safe_text(getattr(post, "title", ""))
    content = _safe_text(getattr(post, "content", ""))
    category = _category_name(post)
    joined = f"{category} {title} {content}"

    review_words = [
        "제품", "리뷰", "후기", "사용", "추천템", "다이소", "이마트", "쿠팡",
        "언박싱", "구매", "가격", "가성비", "실사용", "장점", "단점",
    ]

    place_words = [
        "맛집", "카페", "여행", "양평", "포항", "김포", "파주", "장소",
        "메뉴", "식당", "드라이브", "아이랑", "가볼만한",
    ]

    info_words = [
        "금융", "부동산", "건축", "건설", "코스피", "환율", "아파트",
        "폐업", "분양", "청약", "집값", "분석", "시장", "금리",
    ]

    if any(w in joined for w in review_words):
        return "review"

    if any(w in joined for w in place_words):
        return "place"

    if any(w in joined for w in info_words):
        return "info"

    return "info"


def _short_title(post):
    title = _safe_text(getattr(post, "title", ""))
    title = re.sub(r"^\s*\[[^\]]+\]\s*", "", title)
    return _limit(title, 44)


def _make_hook(post, template):
    title = _short_title(post)

    if template == "review":
        return _limit(f"{title}\n살까 말까?", 58)

    if template == "place":
        return _limit(f"{title}\n저장할 포인트", 58)

    return _limit(f"{title}\n핵심만 빠르게", 58)


def _extract_numbers(text):
    text = _safe_text(text)
    patterns = [
        r"\d+(?:,\d{3})*(?:\.\d+)?\s?%",
        r"\d+(?:,\d{3})*(?:\.\d+)?\s?억",
        r"\d+(?:,\d{3})*(?:\.\d+)?\s?만",
        r"\d+(?:,\d{3})*(?:\.\d+)?\s?원",
        r"\d+(?:,\d{3})*(?:\.\d+)?\s?가구",
        r"\d+(?:,\d{3})*(?:\.\d+)?\s?건",
        r"\d+(?:,\d{3})*(?:\.\d+)?",
    ]

    found = []

    for pattern in patterns:
        for match in re.findall(pattern, text):
            if match not in found:
                found.append(match)

    return found[:3]


def _pick_sentences(post, count=5):
    title = _safe_text(getattr(post, "title", ""))
    summary = _safe_text(getattr(post, "summary", ""))
    content = _safe_text(getattr(post, "content", ""))
    parts = []

    if summary:
        parts.append(summary)

    parts.extend(_sentences(content))

    usable = []
    for item in parts:
        item = _safe_text(item)
        if not item:
            continue
        if item == title:
            continue
        if len(item) < 14:
            continue
        if item not in usable:
            usable.append(item)

    return usable[:count]


def _build_review_scenes(post):
    title = _short_title(post)
    hook = _make_hook(post, "review")
    sents = _pick_sentences(post, 6)

    first = _limit(sents[0] if len(sents) > 0 else f"{title}의 실제 사용 포인트를 빠르게 정리했습니다.", 58)
    good = _limit(sents[1] if len(sents) > 1 else "좋았던 점은 가격 대비 체감 만족도가 분명하다는 점입니다.", 58)
    bad = _limit(sents[2] if len(sents) > 2 else "아쉬운 점도 있습니다. 모든 사람에게 무조건 맞는 제품은 아닙니다.", 58)
    target = _limit(sents[3] if len(sents) > 3 else "이런 제품은 필요한 상황이 분명한 사람에게 더 잘 맞습니다.", 58)
    conclusion = _limit(sents[4] if len(sents) > 4 else "결론은 가성비를 따진다면 한 번 확인해볼 만합니다.", 58)

    return [
        {"kind": "hook", "label": "리뷰 시작", "title": hook, "subtitle": "3초 안에 핵심만 봅니다", "duration": 2.2},
        {"kind": "point", "label": "첫인상", "title": first, "subtitle": "실사용 기준으로 봤을 때", "duration": 3.0},
        {"kind": "good", "label": "좋았던 점", "title": good, "subtitle": "구매 전 체크 포인트", "duration": 3.2},
        {"kind": "bad", "label": "아쉬운 점", "title": bad, "subtitle": "무조건 추천은 아닙니다", "duration": 3.2},
        {"kind": "target", "label": "추천 대상", "title": target, "subtitle": "이런 사람에게 잘 맞습니다", "duration": 3.2},
        {"kind": "conclusion", "label": "한줄평", "title": conclusion, "subtitle": "자세한 내용은 ChickenBanana Lab", "duration": 3.5},
    ]


def _build_place_scenes(post):
    title = _short_title(post)
    hook = _make_hook(post, "place")
    sents = _pick_sentences(post, 6)

    first = _limit(sents[0] if len(sents) > 0 else f"{title}에서 먼저 봐야 할 포인트를 정리했습니다.", 58)
    menu = _limit(sents[1] if len(sents) > 1 else "대표 메뉴와 분위기를 같이 보고 판단하는 게 좋습니다.", 58)
    point = _limit(sents[2] if len(sents) > 2 else "방문 전에는 위치, 주차, 대기 시간을 같이 확인해야 합니다.", 58)
    target = _limit(sents[3] if len(sents) > 3 else "가볍게 들르기 좋은지, 목적지로 갈 만한지 나눠서 보면 좋습니다.", 58)
    conclusion = _limit(sents[4] if len(sents) > 4 else "저장해두고 근처 갈 때 확인해볼 만한 장소입니다.", 58)

    return [
        {"kind": "hook", "label": "저장 추천", "title": hook, "subtitle": "방문 전 핵심만", "duration": 2.2},
        {"kind": "point", "label": "핵심 포인트", "title": first, "subtitle": "가장 먼저 볼 부분", "duration": 3.0},
        {"kind": "good", "label": "메뉴/분위기", "title": menu, "subtitle": "방문 만족도 체크", "duration": 3.2},
        {"kind": "target", "label": "방문 전 체크", "title": point, "subtitle": "주차·대기·동선 확인", "duration": 3.2},
        {"kind": "target", "label": "추천 대상", "title": target, "subtitle": "이런 분께 잘 맞습니다", "duration": 3.2},
        {"kind": "conclusion", "label": "한줄 결론", "title": conclusion, "subtitle": "자세한 내용은 ChickenBanana Lab", "duration": 3.5},
    ]


def _build_info_scenes(post):
    title = _short_title(post)
    hook = _make_hook(post, "info")
    sents = _pick_sentences(post, 6)
    numbers = _extract_numbers(" ".join([getattr(post, "title", ""), getattr(post, "summary", ""), getattr(post, "content", "")]))

    number_title = "숫자로 보면 더 선명합니다"
    if numbers:
        number_title = " · ".join(numbers[:2])

    first = _limit(sents[0] if len(sents) > 0 else f"{title} 이슈의 핵심은 흐름 변화입니다.", 60)
    reason1 = _limit(sents[1] if len(sents) > 1 else "첫 번째 포인트는 숫자보다 방향성입니다.", 60)
    reason2 = _limit(sents[2] if len(sents) > 2 else "두 번째 포인트는 시장이 반응하는 속도입니다.", 60)
    watch = _limit(sents[3] if len(sents) > 3 else "앞으로는 관련 지표가 이어서 움직이는지 확인해야 합니다.", 60)
    conclusion = _limit(sents[4] if len(sents) > 4 else "결론은 단기 이슈보다 구조적 변화를 봐야 한다는 점입니다.", 60)

    return [
        {"kind": "hook", "label": "핵심 이슈", "title": hook, "subtitle": "복잡한 내용 짧게 정리", "duration": 2.2},
        {"kind": "number", "label": "숫자 체크", "title": number_title, "subtitle": first, "duration": 3.2},
        {"kind": "point", "label": "포인트 1", "title": reason1, "subtitle": "왜 중요한지 봐야 합니다", "duration": 3.2},
        {"kind": "point", "label": "포인트 2", "title": reason2, "subtitle": "흐름이 바뀌는 지점", "duration": 3.2},
        {"kind": "target", "label": "앞으로 볼 것", "title": watch, "subtitle": "다음 지표를 확인하세요", "duration": 3.2},
        {"kind": "conclusion", "label": "결론", "title": conclusion, "subtitle": "자세한 내용은 ChickenBanana Lab", "duration": 3.5},
    ]


def _build_scenes(post):
    template = _detect_template(post)

    if template == "review":
        scenes = _build_review_scenes(post)
    elif template == "place":
        scenes = _build_place_scenes(post)
    else:
        scenes = _build_info_scenes(post)

    total = len(scenes)

    for idx, scene in enumerate(scenes, start=1):
        scene["template"] = template
        scene["index"] = idx
        scene["total"] = total

    return scenes


def _base_background(image_path=None):
    if image_path and os.path.exists(image_path):
        try:
            img = Image.open(image_path).convert("RGB")

            scale = max(WIDTH / img.width, HEIGHT / img.height)
            size = (int(img.width * scale), int(img.height * scale))
            img = img.resize(size, Image.LANCZOS)

            left = int((img.width - WIDTH) / 2)
            top = int((img.height - HEIGHT) / 2)
            img = img.crop((left, top, left + WIDTH, top + HEIGHT))

            blur = img.filter(ImageFilter.GaussianBlur(22))
            dark = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
            bg = Image.blend(blur, dark, 0.38).convert("RGBA")

            sharp = img.convert("RGBA")
            card_w = 900
            card_h = 950
            card_scale = max(card_w / sharp.width, card_h / sharp.height)
            card_size = (int(sharp.width * card_scale), int(sharp.height * card_scale))
            sharp = sharp.resize(card_size, Image.LANCZOS)

            c_left = int((sharp.width - card_w) / 2)
            c_top = int((sharp.height - card_h) / 2)
            sharp = sharp.crop((c_left, c_top, c_left + card_w, c_top + card_h))

            mask = Image.new("L", (card_w, card_h), 0)
            md = ImageDraw.Draw(mask)
            md.rounded_rectangle((0, 0, card_w, card_h), radius=72, fill=168)

            bg.paste(sharp, ((WIDTH - card_w) // 2, 575), mask)
            return bg

        except Exception:
            pass

    img = Image.new("RGBA", (WIDTH, HEIGHT), (15, 18, 30, 255))
    draw = ImageDraw.Draw(img)

    for y in range(HEIGHT):
        r = int(15 + (y / HEIGHT) * 28)
        g = int(18 + (y / HEIGHT) * 24)
        b = int(34 + (y / HEIGHT) * 46)
        draw.line((0, y, WIDTH, y), fill=(r, g, b, 255))

    draw.ellipse((-260, 80, 380, 720), fill=(124, 58, 237, 70))
    draw.ellipse((680, 880, 1320, 1520), fill=(236, 72, 153, 64))
    draw.ellipse((80, 1450, 560, 1930), fill=(34, 197, 94, 45))

    return img


def _draw_brand(draw):
    brand_font = _font(36)
    small_font = _font(27)

    _draw_round_rect(
        draw,
        (52, 48, WIDTH - 52, 132),
        40,
        fill=(0, 0, 0, 96),
        outline=(255, 255, 255, 34),
        width=2,
    )

    draw.text((84, 76), "CHICKENBANANALAB", font=brand_font, fill=(255, 255, 255, 245))

    _draw_round_rect(
        draw,
        (WIDTH - 246, 70, WIDTH - 84, 112),
        21,
        fill=(255, 59, 127, 235),
    )

    draw.text((WIDTH - 215, 80), "CLIP", font=small_font, fill=(255, 255, 255, 255))


def _draw_progress(draw, idx, total):
    y = 1772
    left = 88
    right = WIDTH - 88
    w = right - left
    filled = int(w * idx / max(total, 1))

    _draw_round_rect(draw, (left, y, right, y + 18), 9, fill=(255, 255, 255, 80))
    _draw_round_rect(draw, (left, y, left + filled, y + 18), 9, fill=(255, 59, 127, 235))


def _accent_color(kind):
    if kind == "good":
        return (34, 197, 94, 245)
    if kind == "bad":
        return (239, 68, 68, 245)
    if kind == "number":
        return (59, 130, 246, 245)
    if kind == "conclusion":
        return (255, 59, 127, 245)
    if kind == "target":
        return (245, 158, 11, 245)
    return (124, 58, 237, 245)


def _draw_overlay(scene, transparent=False):
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    title = _safe_text(scene.get("title", ""))
    subtitle = _safe_text(scene.get("subtitle", ""))
    label = _safe_text(scene.get("label", ""))
    kind = scene.get("kind", "point")
    idx = int(scene.get("index", 1))
    total = int(scene.get("total", 1))
    accent = _accent_color(kind)

    brand_font = _font(36)
    label_font = _font(38)
    hook_font = _font(88)
    title_font = _font(72)
    subtitle_font = _font(38)
    small_font = _font(28)
    number_font = _font(96)

    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(0, 0, 0, 44 if transparent else 30))
    _draw_brand(draw)

    # Scene count
    count_text = f"{idx}/{total}"
    _draw_round_rect(draw, (82, 178, 202, 230), 26, fill=(0, 0, 0, 132))
    draw.text((113, 190), count_text, font=small_font, fill=(255, 255, 255, 235))

    if kind == "hook":
        _draw_round_rect(draw, (82, 320, 430, 394), 37, fill=accent)
        draw.text((122, 340), label, font=label_font, fill=(255, 255, 255, 255))

        lines = _wrap_text(draw, title, hook_font, WIDTH - 140, max_lines=3)
        _draw_text_center(
            draw,
            lines,
            hook_font,
            520,
            fill=(255, 255, 255, 255),
            stroke_fill=(0, 0, 0, 210),
            stroke_width=5,
            line_gap=26,
        )

        _draw_round_rect(draw, (108, 1398, WIDTH - 108, 1538), 44, fill=(255, 255, 255, 235))
        sub_lines = _wrap_text(draw, subtitle, subtitle_font, WIDTH - 260, max_lines=2)
        y = 1440
        for line in sub_lines:
            draw.text((152, y), line, font=subtitle_font, fill=(17, 24, 39, 255))
            box = draw.textbbox((0, 0), line, font=subtitle_font)
            y += (box[3] - box[1]) + 12

    elif kind == "number":
        _draw_round_rect(draw, (82, 310, 430, 384), 37, fill=accent)
        draw.text((122, 330), label, font=label_font, fill=(255, 255, 255, 255))

        lines = _wrap_text(draw, title, number_font, WIDTH - 140, max_lines=2)
        _draw_text_center(
            draw,
            lines,
            number_font,
            510,
            fill=(255, 255, 255, 255),
            stroke_fill=(0, 0, 0, 220),
            stroke_width=5,
            line_gap=24,
        )

        _draw_round_rect(draw, (76, 1245, WIDTH - 76, 1515), 52, fill=(255, 255, 255, 236))
        sub_lines = _wrap_text(draw, subtitle, subtitle_font, WIDTH - 190, max_lines=3)
        y = 1315
        for line in sub_lines:
            draw.text((118, y), line, font=subtitle_font, fill=(17, 24, 39, 255))
            box = draw.textbbox((0, 0), line, font=subtitle_font)
            y += (box[3] - box[1]) + 14

    else:
        _draw_round_rect(draw, (82, 290, 460, 364), 37, fill=accent)
        draw.text((122, 310), label, font=label_font, fill=(255, 255, 255, 255))

        # 제목은 중앙보다 약간 아래, 실제 쇼츠 자막처럼 크게
        main_lines = _wrap_text(draw, title, title_font, WIDTH - 130, max_lines=4)
        _draw_text_center(
            draw,
            main_lines,
            title_font,
            465,
            fill=(255, 255, 255, 255),
            stroke_fill=(0, 0, 0, 220),
            stroke_width=5,
            line_gap=24,
        )

        # 하단 자막 카드
        _draw_round_rect(draw, (70, 1310, WIDTH - 70, 1558), 50, fill=(255, 255, 255, 236))

        _draw_round_rect(draw, (112, 1344, 250, 1398), 27, fill=(17, 24, 39, 255))
        draw.text((142, 1357), "POINT", font=small_font, fill=(255, 255, 255, 255))

        sub_lines = _wrap_text(draw, subtitle, subtitle_font, WIDTH - 200, max_lines=2)
        y = 1434
        for line in sub_lines:
            draw.text((112, y), line, font=subtitle_font, fill=(17, 24, 39, 255))
            box = draw.textbbox((0, 0), line, font=subtitle_font)
            y += (box[3] - box[1]) + 14

    _draw_progress(draw, idx, total)

    site_font = _font(32)
    site = "chickenbananalab.com"
    sx = int((WIDTH - _text_len(draw, site, site_font)) / 2)
    draw.text((sx, 1820), site, font=site_font, fill=(255, 255, 255, 226))

    return img


def _make_scene_image(background_path, scene, out_path):
    bg = _base_background(background_path)
    overlay = _draw_overlay(scene, transparent=False)
    bg.alpha_composite(overlay)
    bg.convert("RGB").save(out_path, quality=95)


def _make_cover(first_scene_path, cover_path):
    img = Image.open(first_scene_path).convert("RGB")
    img = img.resize((COVER_WIDTH, COVER_HEIGHT), Image.LANCZOS)
    img.save(cover_path, quality=95)


def _make_image_clip(scene_image, duration, out_path):
    frames = max(1, int(float(duration) * FPS))
    duration_s = f"{float(duration):.2f}"

    # 완전 정지 화면 느낌을 줄이기 위한 미세 줌인
    vf = (
        f"scale={WIDTH}:{HEIGHT},"
        f"zoompan=z='min(zoom+0.0012,1.055)':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"trim=duration={duration_s},"
        f"fade=t=in:st=0:d=0.18,"
        f"fade=t=out:st={max(float(duration)-0.18, 0):.2f}:d=0.18,"
        f"format=yuv420p"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-t", duration_s,
        "-i", str(scene_image),
        "-f", "lavfi",
        "-t", duration_s,
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-vf", vf,
        "-map", "0:v",
        "-map", "1:a",
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-shortest",
        str(out_path),
    ]

    _run_ffmpeg(cmd)


def _concat_clips(clip_paths, out_path, concat_path):
    with open(concat_path, "w", encoding="utf-8") as f:
        for clip in clip_paths:
            f.write(f"file '{Path(clip).as_posix()}'\n")

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_path),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]

    _run_ffmpeg(cmd)


def _make_from_images(post, scenes, work_dir, out_path, cover_path):
    images = _image_candidates(post)
    scene_images = []
    clips = []

    for i, scene in enumerate(scenes):
        bg = images[i % len(images)] if images else None
        scene_path = work_dir / f"scene_{i + 1:02d}.png"
        clip_path = work_dir / f"clip_{i + 1:02d}.mp4"

        _make_scene_image(bg, scene, scene_path)

        if i == 0:
            _make_cover(scene_path, cover_path)

        _make_image_clip(scene_path, scene.get("duration", 3.0), clip_path)

        scene_images.append(scene_path)
        clips.append(clip_path)

    _concat_clips(clips, out_path, work_dir / "concat.txt")


def _make_from_video(post, scenes, work_dir, out_path, cover_path, video_path):
    overlay_paths = []

    # 커버는 첫 장면을 이미지 배경으로 따로 생성
    images = _image_candidates(post)
    first_bg = images[0] if images else None
    cover_scene = work_dir / "cover_scene.png"
    _make_scene_image(first_bg, scenes[0], cover_scene)
    _make_cover(cover_scene, cover_path)

    for i, scene in enumerate(scenes):
        overlay = _draw_overlay(scene, transparent=True)
        overlay_path = work_dir / f"overlay_{i + 1:02d}.png"
        overlay.save(overlay_path)
        overlay_paths.append(overlay_path)

    total_duration = sum(float(s.get("duration", 3.0)) for s in scenes)

    cmd = [
        "ffmpeg",
        "-y",
        "-stream_loop", "-1",
        "-i", str(video_path),
    ]

    for overlay_path in overlay_paths:
        cmd += ["-i", str(overlay_path)]

    audio_index = len(overlay_paths) + 1

    cmd += [
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
    ]

    filters = [
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},setsar=1,"
        f"trim=duration={total_duration:.2f},setpts=PTS-STARTPTS[base]"
    ]

    last = "base"
    current_time = 0.0

    for i, scene in enumerate(scenes):
        start = current_time
        end = current_time + float(scene.get("duration", 3.0))
        current = f"v{i + 1}"

        filters.append(
            f"[{last}][{i + 1}:v]overlay=0:0:enable='between(t,{start:.2f},{end:.2f})'[{current}]"
        )

        last = current
        current_time = end

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{last}]",
        "-map", f"{audio_index}:a",
        "-t", f"{total_duration:.2f}",
        "-r", str(FPS),
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-shortest",
        str(out_path),
    ]

    _run_ffmpeg(cmd)


def make_shorts_for_post(post):
    media_root = _media_root()
    out_dir = media_root / "shorts"
    cover_dir = media_root / "shorts_covers"
    work_root = media_root / "shorts_work"

    out_dir.mkdir(parents=True, exist_ok=True)
    cover_dir.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = work_root / f"post_{post.pk}_{stamp}"
    work_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"shorts_post_{post.pk}_{stamp}.mp4"
    cover_path = cover_dir / f"cover_post_{post.pk}_{stamp}.jpg"

    scenes = _build_scenes(post)

    if not scenes:
        raise RuntimeError("쇼츠를 만들 제목 또는 본문이 없습니다.")

    video_path = _field_path(getattr(post, "video_file", None))

    if video_path:
        _make_from_video(post, scenes, work_dir, out_path, cover_path, video_path)
    else:
        _make_from_images(post, scenes, work_dir, out_path, cover_path)

    return {
        "video": out_path.relative_to(media_root).as_posix(),
        "cover": cover_path.relative_to(media_root).as_posix(),
    }
