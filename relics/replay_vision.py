"""Read the purchase panel in the recorded relic viewport."""

import re

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from obelisk.vision import normalize
from relics.simulation import Screen
from relics.vision import read_loot_popup, read_summary_viewport


GEMS = (365, 43, 430, 62)
PRICE = (994, 226, 1044, 251)
REFUNDABLE = ((738, 232, 811, 253), (827, 233, 871, 252), (897, 232, 928, 251))
UNOPENED = ((1124, 163, 1170, 180), (1124, 184, 1160, 201), (1124, 202, 1160, 221))
OPEN_ALL = (964, 120, 1045, 147)
REFUND_LABEL = (684, 263, 1050, 288)
REROLL = (696, 118, 779, 146)
SUMMARY = (548, 164, 610, 187)
SMALL_COUNTS = ((4, 915, 243), (5, 1140, 175), (6, 1140, 195), (7, 1140, 215))


def exact_count(text):
    return int(text) if re.fullmatch(r'[0-9]+', text) else None


def inventory(texts):
    counts = tuple(map(exact_count, texts))
    return counts if all(count is not None for count in counts) else None


class ReplayVision:
    def __init__(self):
        self.ocr = RapidOCR()

    def small_count(self, frame, x, y, *, dimmed_zero=False):
        crops = [cv2.resize(frame[y-7:y+7, x-w:x+w], None, fx=5, fy=5,
                            interpolation=cv2.INTER_NEAREST) for w in (4, 6, 8)]
        results, _ = self.ocr.text_recognizer(crops)
        if dimmed_zero and all(value.strip() == '0' and float(confidence) >= .20
                               for value, confidence in results):
            return '0'
        digits = [value.strip() for value, confidence in results
                  if float(confidence) >= .40 and re.fullmatch(r'[0-9]', value.strip())]
        return digits[0] if len(digits) >= 2 and len(set(digits)) == 1 else ''

    def __call__(self, image):
        loot = read_loot_popup(image, self.ocr)
        try:
            rgb, _ = normalize(image)
        except ValueError:
            return dict(screen=Screen.UNKNOWN)
        frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        boxes = (GEMS, PRICE, *REFUNDABLE, *UNOPENED, OPEN_ALL, REFUND_LABEL, REROLL, SUMMARY)
        crops = [cv2.resize(frame[y1:y2, x1:x2], None, fx=4, fy=4,
                            interpolation=cv2.INTER_NEAREST) for x1, y1, x2, y2 in boxes]
        readings, _ = self.ocr.text_recognizer(crops)
        text = [value.strip().lower() if float(confidence) >= 0.60 else ''
                for value, confidence in readings]
        gems = exact_count(text[0])
        if loot.recognized:
            return dict(screen=Screen.LOOT, reading=loot, gems=gems)
        summary = read_summary_viewport(image, self.ocr)
        if summary.recognized:
            return dict(screen=Screen.OVERVIEW, reading=summary, gems=gems)
        open_all_text = text[8].replace(' ', '')
        open_all_label = open_all_text in ('openall', 'openalt', 'openatl')
        summary_available = bool(open_all_label and text[11] in ('summary', 'sumnary', 'sumray'))
        red, green, blue = map(int, rgb[112, 978])
        open_all = open_all_label and red >= 105 and red >= green + 45 and red >= blue + 45
        open_all_dimmed = ((open_all_label or open_all_text == 'lopenalz')
                           and 45 <= red < 105 and red >= max(green, blue) + 20)
        refund_label = text[9] in ('refunds your relics so you can open them again!',
                                   'refunds your relics so you can opethem again!')
        if not refund_label or text[1] != '200':
            r, g, b = map(int, rgb[110, 710])
            reroll_text, reroll_confidence = readings[10]
            reroll_button = (float(reroll_confidence) >= .45
                             and reroll_text.strip().lower() in ('reroll', 'rerollp'))
            if (not (refund_label or text[1] == '200') and (open_all or open_all_dimmed)
                    and (summary_available or reroll_button)
                    and r >= 105 and r >= max(g, b) + 45):
                return dict(screen=Screen.MENU, gems=gems, purchase_controls_hidden=True,
                            summary_available=summary_available)
            return dict(screen=Screen.UNKNOWN, gems=gems)

        red, green, blue = rgb[219, 982]
        if red >= 180 and 75 <= green <= 170 and blue <= 85:
            screen = Screen.CONFIRM
        elif red >= 70 and green <= 65 and blue <= 85:
            screen = Screen.MENU
        else:
            screen = Screen.UNKNOWN
        # The recorded refund state dims the 200 control and all three zero tiles.
        dimmed_refund = screen is Screen.MENU and 70 <= red <= 100 and open_all
        if dimmed_refund and all(np.percentile(rgb[y:Y, x:X], 99) < 115
                                 for x, y, X, Y in REFUNDABLE):
            if all(value in ('', '0') for value in text[2:5]):
                text[2:5] = ['0'] * 3
        for index, x, y in SMALL_COUNTS:
            if text[index]:
                continue
            if index >= 6 and text[5] and np.median(
                    np.max(rgb[y-3:y+4, 1175:1195], axis=2)) > 50:
                # Unopened counters have a dark badge. The lower slots are omitted at zero.
                text[index] = '0'
            else:
                text[index] = self.small_count(frame, x, y, dimmed_zero=index == 5 and open_all_dimmed)
        return dict(screen=screen, gems=gems, price=200,
                    refundable=inventory(text[2:5]), unopened=inventory(text[5:8]),
                    open_all_available=open_all, unopened_relic_display=text[5] or None,
                    chest_text=tuple(text[2:8]), summary_available=summary_available)
