"""Which PDF page shows which drawing, from drawing numbers printed on the pages and the CAD sheet list.

Drawing sets are bound in drawing-number order. A page whose title block number survives as
PDF text is known directly. A run of pages without readable text between two known pages is
given the CAD sheets numbered between them, but only when the number of sheets equals the
number of pages, so a page is never guessed.
"""
from __future__ import annotations

import re

NUMBER = re.compile(r"\b([A-Z]{1,3})\s*-\s*(\d{2,4})([A-Z0-9]*)\b")


def number_key(number):
    """Sort key of a drawing number such as "A-410" or "S-101A"; None when it is not one."""
    match = NUMBER.fullmatch(str(number or "").strip())
    if not match:
        return None
    return match.group(1), int(match.group(2)), match.group(3)


def _normal(prefix, digits, suffix):
    return f"{prefix}-{digits}{suffix}"


def page_numbers(texts):
    """{page: [drawing numbers printed on it]}, in drawing-number order."""
    found = {}
    for page, text in (texts or {}).items():
        numbers = {_normal(*match) for match in NUMBER.findall(text or "")}
        if numbers:
            found[page] = sorted(numbers, key=number_key)
    return found


def cad_sheet_titles(dwg_data):
    """{drawing number: title} of the sheets read from the CAD set."""
    titles = {}
    for parsed in (dwg_data or {}).values():
        for sheet in (parsed or {}).get("sheets") or []:
            number = re.sub(r"\s+", "", str(sheet.get("drawing_number") or ""))
            if number_key(number) and number not in titles:
                titles[number] = str(sheet.get("drawing_title") or "")
    return titles


def _main_prefix(numbers):
    counts = {}
    for number in numbers:
        prefix = number_key(number)[0]
        counts[prefix] = counts.get(prefix, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _increasing(candidates):
    """Longest run of (page, number) pairs whose numbers rise with the page (one stray
    number cannot push the pages after it out)."""
    keys = [number_key(number) for _page, number in candidates]
    best, previous = [], [None] * len(candidates)
    tails = []  # tails[k]: index ending the best increasing run of length k + 1
    for index, key in enumerate(keys):
        low, high = 0, len(tails)
        while low < high:
            middle = (low + high) // 2
            if keys[tails[middle]] < key:
                low = middle + 1
            else:
                high = middle
        previous[index] = tails[low - 1] if low else None
        if low == len(tails):
            tails.append(index)
        else:
            tails[low] = index
    index = tails[-1] if tails else None
    while index is not None:
        best.append(candidates[index])
        index = previous[index]
    return dict(reversed(best))


def map_pages(texts, sheet_titles, total_pages):
    """{page: drawing number} for the pages that can be identified.

    A page counts as known when it prints exactly one drawing number of the set's prefix
    (the CAD sheets' prefix, else the most common one), and the known pages keep the
    drawing-number order. Runs of unknown pages, including those before the first and after
    the last known page, are filled from the CAD sheet list when the counts are equal.
    """
    printed = page_numbers(texts)
    prefix = _main_prefix(sheet_titles) or _main_prefix(n for numbers in printed.values() for n in numbers)
    candidates = []
    for page, numbers in sorted(printed.items()):
        own = [n for n in numbers if number_key(n)[0] == prefix]
        if len(own) == 1:
            candidates.append((page, own[0]))
    known = _increasing(candidates)
    if not sheet_titles:
        return known

    cad = sorted((n for n in sheet_titles if number_key(n)[0] == prefix), key=number_key)
    mapped = dict(known)
    anchors = [(0, None)] + sorted(known.items()) + [(total_pages + 1, None)]
    for (page_a, number_a), (page_b, number_b) in zip(anchors, anchors[1:]):
        gap = list(range(page_a + 1, page_b))
        if not gap:
            continue
        low = number_key(number_a) if number_a else None
        high = number_key(number_b) if number_b else None
        between = [n for n in cad if (low is None or number_key(n) > low) and (high is None or number_key(n) < high)]
        if len(between) == len(gap):
            mapped.update(zip(gap, between))
    return mapped


def pages_titled(page_map, sheet_titles, pattern):
    """Pages whose drawing title matches the pattern."""
    return sorted(page for page, number in page_map.items() if pattern.search(sheet_titles.get(number, "")))


def page_label(page, page_map, sheet_titles):
    number = page_map.get(page)
    if not number:
        return f"{page}쪽"
    return f"{page}쪽 · {number} {sheet_titles.get(number, '')}".rstrip()
