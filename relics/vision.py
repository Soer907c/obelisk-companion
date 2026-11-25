"""Read divine counts from the relic overview in a single image."""

import re
from unicodedata import normalize as normalize_text
from dataclasses import dataclass

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from obelisk.vision import normalize
from relics.calculator import DivineCounts


DIVINE_NAMES = (
    'Rainbow Floor Chance', 'Supernova Multipliers', 'Rainbow Floor Multi',
    'Fishing Ticks 5x Chance', 'Rainbow Portal Chance',
)
Counts = tuple[int | None, int | None, int | None, int | None, int | None]
UNKNOWN_COUNTS = (None, None, None, None, None)
DIVINE_RATE = re.compile(r'chance\s*:?\s*(?:1\s*/\s*25\s*k|0\.004\s*%)(?![a-z0-9])', re.I)
COUNT = re.compile(r'\(\s*([0-9]+)\s*[x×]\s*\)', re.I)
POPUP_COUNT = re.compile(r'^\s*[([]?\s*([0-9]+)\s*x[)l\]]?\s*', re.I)
LOOT_NAME_PATTERN = re.compile(
    r'rainbowfl[oa]orchance|supernova[mh]ultipliers|rainbowfloormulti|'
    r'fishingticks?5xchance|rainbowportalchance'
)


@dataclass(frozen=True)
class TextLine:
    text: str
    x: float
    y: float
    height: float


@dataclass(frozen=True)
class RelicReading:
    recognized: bool
    counts: Counts = UNKNOWN_COUNTS

    def matches(self, minimums: DivineCounts) -> bool | None:
        required = [(count, minimum) for count, minimum in zip(self.counts, minimums, strict=True)
                    if minimum > 0]
        if not self.recognized or not required or any(count is None for count, _ in required):
            return None
        return all(count >= minimum for count, minimum in required)


def join_rows(lines: list[TextLine]) -> list[TextLine]:
    rows = []
    for line in sorted(lines, key=lambda item: item.y):
        if rows and abs(line.y - rows[-1][0].y) <= min(line.height, rows[-1][0].height) * 0.35:
            rows[-1].append(line)
        else:
            rows.append([line])
    joined = []
    for row in rows:
        row.sort(key=lambda item: item.x)
        joined.append(TextLine(' '.join(item.text for item in row), row[0].x,
                               sum(item.y for item in row) / len(row),
                               max(item.height for item in row)))
    return joined


def read_overview(lines: list[TextLine], read_count=None) -> RelicReading:
    """Require the divine-rate block and the overview footer before reading rows."""
    averages = [line for line in join_rows([line for line in lines
                                           if line.text.lower() in ('your', 'average')
                                           or 'your average' in line.text.lower()])
                if 'your average' in line.text.lower()]
    footers = [line for line in join_rows(lines) if 'tap to close' in line.text.lower()]
    if len(averages) != 1 or len(footers) != 1:
        return RelicReading(False)
    average, footer = averages[0], footers[0]
    sidebar = [line for line in lines if line.x >= average.x - average.height]
    rates = [line for line in join_rows(sidebar) if DIVINE_RATE.search(line.text)]
    if len(rates) != 1:
        return RelicReading(False)
    rate = rates[0]
    if not rate.y < average.y < footer.y or abs(rate.x - average.x) > 2 * rate.height:
        return RelicReading(False)

    rows = join_rows([line for line in lines
                      if rate.y - rate.height <= line.y < footer.y and line.x < average.x - average.height])
    count_starts = [line.x for line in rows if COUNT.match(line.text)]
    count_left = min(count_starts, default=None)
    counts = []
    for name in DIVINE_NAMES:
        pattern = re.escape(name.lower()).replace('ticks', 'ticks?')
        named = [line for line in rows if re.search(pattern + r'\b', line.text.lower())]
        if len(named) != 1:
            counts.append(None)
            continue
        row = named[0]
        prefix = re.split(pattern, row.text.lower(), maxsplit=1)[0]
        count = COUNT.search(prefix)
        if not prefix.strip() and read_count is not None and count_left is not None:
            # A named row may survive detection even when its short count box does not.
            count = COUNT.fullmatch(read_count(count_left, row))
        counts.append(int(count[1]) if count else None)
    return RelicReading(True, tuple(counts))


def read_loot_popup(image, ocr) -> RelicReading:
    """Recognize the finished Open All popup in the normalized game viewport."""
    try:
        rgb, _ = normalize(image)
    except ValueError:
        return RelicReading(False)
    frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    def text_at(x1, y1, x2, y2):
        crop = cv2.resize(frame[y1:y2, x1:x2], None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        results, _ = ocr.text_recognizer([crop])
        text, confidence = results[0]
        return text.strip().lower() if float(confidence) >= 0.70 else ''

    if text_at(717, 201, 778, 220) != 'loot' or text_at(717, 218, 778, 236).rstrip(':') != 'from':
        return RelicReading(False)

    # Yellow text identifies divine rows near the end of the loot list.
    red, green, blue = rgb[235:700, 734:1044].transpose(2, 0, 1).astype(np.int16)
    yellow = (red > 130) & (green > 120) & (blue < 120) & (red - blue > 75) & (green - blue > 65)
    ys = np.flatnonzero(yellow.sum(axis=1) > 4)
    bands = [band for band in np.split(ys, np.flatnonzero(np.diff(ys) > 1) + 1) if len(band) >= 3]
    if not bands:
        return RelicReading(False)
    last = 235 + int(bands[-1][-1]) + 1
    if text_at(780, last + 1, 981, last + 18) != 'tap anywhere to close':
        return RelicReading(False)

    rows = []
    for band in bands:
        top, bottom = 233 + int(band[0]), 238 + int(band[-1])
        row = text_at(737, top, 1040, bottom).replace(' ', '')
        count = POPUP_COUNT.match(row)
        if count and LOOT_NAME_PATTERN.match(row, count.end()):
            rows.append(row)
            continue
        # The whole line can garble the name or its small count. Recover each
        # from the same row's separate crops, with agreement for the count.
        named = LOOT_NAME_PATTERN.search(row)
        if named is None:
            named = LOOT_NAME_PATTERN.match(text_at(780, top, 1040, bottom).replace(' ', ''))
        if named is None:
            continue
        recovered = read_popup_count(frame[top:bottom], ocr)
        if recovered is not None:
            rows.append(f'({recovered}x) {named[0]}')
    return read_loot_rows(rows)


def read_popup_count(row, ocr):
    bracketed = r'[([]([0-9]+)(?:x[)\]]?|\))'
    count_with_x = r'[([]?([0-9]+)x[)\]]?'
    formats = ((739, 774, 3, bracketed), (739, 774, 5, bracketed),
               (739, 782, 3, bracketed), (739, 789, 5, count_with_x),
               (739, 789, 7, count_with_x), (737, 779, 3, count_with_x),
               (738, 782, 3, count_with_x))
    crops = [cv2.resize(row[:, left:right], None, fx=scale, fy=scale,
                        interpolation=cv2.INTER_NEAREST) for left, right, scale, _ in formats]
    readings, _ = ocr.text_recognizer(crops)
    candidates = [
        int(match[1]) for (value, confidence), (_, _, _, pattern) in zip(readings, formats, strict=True)
        if float(confidence) >= .50
        and (match := re.fullmatch(
            pattern,
            normalize_text('NFKC', value).strip().lower().replace('【', '[').replace('】', ']'),
        ))
    ]
    if len(candidates) >= 2 and len(set(candidates)) == 1:
        return candidates[0]
    return None


def read_loot_rows(rows: list[str]) -> RelicReading:
    counts = []
    for name in DIVINE_NAMES:
        compact_name = name.lower().replace(' ', '')
        pattern = compact_name.replace('ticks', 'ticks?').replace('floorchance', 'fl[oa]orchance')
        pattern = pattern.replace('supernovamultipliers', 'supernova[mh]ultipliers')
        matches = []
        for row in rows:
            count = POPUP_COUNT.match(row)
            if count and re.match(pattern, row[count.end():].replace(' ', '')):
                matches.append(int(count[1]))
        counts.append(matches[0] if len(matches) == 1 else None)
    return RelicReading(True, tuple(counts))


def read_summary_viewport(image, ocr) -> RelicReading:
    """Read the small Summary text with separate, enlarged row crops."""
    try:
        rgb, _ = normalize(image)
    except ValueError:
        return RelicReading(False)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    boxes = [(802, 685, 938, 712), (959, 601, 1075, 620), (963, 625, 1072, 642)]
    boxes.extend((690, 601 + row * 17, 963, 621 + row * 17) for row in range(5))
    crops = [cv2.resize(bgr[y1:y2, x1:x2], None, fx=3, fy=3,
                        interpolation=cv2.INTER_NEAREST) for x1, y1, x2, y2 in boxes]
    results, _ = ocr.text_recognizer(crops)
    # The average label is small. Accept its observed low-confidence spelling
    # only alongside the separate footer and exact rate value.
    text = [value.strip().lower() if float(confidence) >= (0.63 if index == 2 else 0.68) else ''
            for index, (value, confidence) in enumerate(results)]
    footer, rate, average, *rows = text
    if (not re.fullmatch(r'tap to cl[oa]se', footer)
            or not re.fullmatch(r'(?:chance|chante)\s*:\s*1\s*/\s*25\s*k', rate)
            or not re.fullmatch(r'your avera[gs]e:?', average)):
        return RelicReading(False)
    for index, row in enumerate(rows):
        if POPUP_COUNT.match(row):
            continue
        prefix = re.match(r'^[([]?[0-9iol]+x[)\]]\s*', row)
        if prefix is None:
            continue
        y = 603 + index * 17
        narrow = [cv2.resize(bgr[y:y+17, 690:732], None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_NEAREST) for scale in (3, 5)]
        readings, _ = ocr.text_recognizer(narrow)
        counts = [re.fullmatch(r'[([]([0-9]+)x[)\]]', value.strip().lower())
                  if float(confidence) >= 0.68 else None for value, confidence in readings]
        if all(counts) and counts[0][1] == counts[1][1]:
            rows[index] = f'({counts[0][1]}x) {row[prefix.end():]}'
    return read_loot_rows(rows)


def read_image(image) -> RelicReading:
    """OCR pixels from the captured client area without changing the game window."""
    ocr = RapidOCR()
    popup = read_loot_popup(image, ocr)
    if popup.recognized:
        return popup
    summary = read_summary_viewport(image, ocr)
    if summary.recognized:
        return summary
    frame = cv2.cvtColor(np.asarray(image.convert('RGB')), cv2.COLOR_RGB2BGR)
    # Upscale small glyphs before text detection. Coordinates remain relative to the frame.
    scale = 2 if image.height < 1000 else 1
    if scale > 1:
        frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    boxes, _ = ocr(frame, use_cls=False)
    lines = []
    for points, text, confidence in boxes or ():
        if float(confidence) < 0.70:
            continue
        xs, ys = zip(*points)
        lines.append(TextLine(text.strip(), min(xs), sum(ys) / len(ys), max(ys) - min(ys)))

    def read_count(left, row):
        x1 = max(0, round(left - row.height * 0.15))
        x2 = round(row.x - 2)
        if not x1 < x2:
            return ''
        y1 = max(0, round(row.y - row.height * 0.8))
        y2 = round(row.y + row.height * 0.7)
        result, _ = ocr.text_recognizer([frame[y1:y2, x1:x2]])
        text, confidence = result[0]
        return text.strip() if float(confidence) >= 0.70 else ''

    return read_overview(lines, read_count)
