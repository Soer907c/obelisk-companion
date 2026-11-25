"""Local image recognition; no screen contents are sent to a service."""
from base64 import b64decode
from io import BytesIO
from pathlib import Path
import re
import cv2
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from .model import Observation, number, timer

TEMPLATES = Path(__file__).resolve().parents[1] / 'assets' / 'obelisk'
SHORTCUT_REGION = (495, 220, 545, 270)
SHORTCUT_SIZES = ((26, 32), (26, 34), (27, 33), (28, 32), (28, 34))
SHORTCUT_SCORE = .70
MENU_COOLDOWN_REGION = (859, 561, 899, 579)
MENU_COOLDOWN_DIGITS = (864, 562, 894, 578)
MENU_TITLE_REGION = (760, 145, 910, 180)


def load_shortcut_icons():
    """Read the 15 gallery sprites in motif order, retaining their alpha masks."""
    lines = (TEMPLATES / 'shortcut-icons.b64').read_bytes().splitlines()
    if len(lines) != 15:
        raise ValueError('Expected 15 Obelisk shortcut motifs')
    icons = []
    for line in lines:
        with Image.open(BytesIO(b64decode(line, validate=True))) as icon:
            icons.append(icon.convert('RGBA'))
    if len({icon.tobytes() for icon in icons}) != len(icons):
        raise ValueError('Duplicate Obelisk shortcut motif')
    return icons


def has_shortcut_button(search, x, y):
    """Require the red sides of the game button around the matched sprite."""
    if x < 15 or x + 15 >= search.shape[1] or y < 6 or y + 7 > search.shape[0]:
        return False
    for rail in (search[y-6:y+7, x-15:x-12], search[y-6:y+7, x+12:x+15]):
        red, green, blue = rail.astype(np.int16).transpose(2, 0, 1)
        if np.count_nonzero((red >= 70) & (red - np.maximum(green, blue) >= 35)) < 12:
            return False
    return True


def menu_cooldown_seconds(text):
    # Small menu digits sometimes render as Q. Use that only to keep waiting.
    if text.startswith('Q:'):
        return timer('0' + text[1:]) or None
    return timer(text)


def normalize(image):
    pixels = np.asarray(image.convert('RGB'))
    mask = np.max(np.abs(pixels.astype(np.int16) - [107, 81, 82]), axis=2) <= 15
    rows = np.flatnonzero(mask.sum(axis=1) > pixels.shape[1] * .55)
    if len(rows) < 10:
        raise ValueError('The game viewport could not be recognized')
    columns = np.flatnonzero(mask[int(np.argmax(mask.sum(axis=1)))])
    runs = np.split(columns, np.flatnonzero(np.diff(columns) > 1) + 1)
    columns = max(runs, key=len)
    left, right = int(columns[0]), int(columns[-1]) + 1
    bottom = int(rows[-1]) + 1
    top = bottom - round((right-left) / 1.5)
    if top < 0 or not 1.46 < (right-left)/(bottom-top) < 1.54:
        raise ValueError('The game viewport does not match the expected 3:2 layout')
    rect = (left, top, right, bottom)
    return np.array(image.crop(rect).resize((1200, 800), Image.Resampling.LANCZOS).convert('RGB')), rect

class Vision:
    def __init__(self):
        self.ocr = RapidOCR()
        self.templates = {}
        for name in ('shortcut', 'fight', 'exit', 'attack', 'return', 'menu'):
            self.templates[name] = np.asarray(Image.open(TEMPLATES / (name + '.png')).convert('L'))
        self.templates['menu-soft'] = cv2.GaussianBlur(self.templates['menu'], (3, 3), .65)
        self.templates['shortcut-cooldown'] = np.asarray(Image.open(TEMPLATES / 'shortcut-cooldown.pgm'))
        self.shortcut_icons = []
        for motif, icon in enumerate(load_shortcut_icons(), 1):
            for size in SHORTCUT_SIZES:
                rgba = np.asarray(icon.resize(size, Image.Resampling.LANCZOS))
                pixels = np.ascontiguousarray(rgba[:, :, :3])
                mask = (rgba[:, :, 3] >= 225).astype(np.uint8)
                self.shortcut_icons.append((motif, pixels, mask))

    def match_shortcut(self, frame):
        x1, y1, x2, y2 = SHORTCUT_REGION
        search = frame[y1:y2, x1:x2]
        matches = []
        for motif, pixels, mask in self.shortcut_icons:
            scores = cv2.matchTemplate(search, pixels, cv2.TM_CCOEFF_NORMED, mask=mask)
            scores[~np.isfinite(scores)] = -1
            _, score, _, (left, top) = cv2.minMaxLoc(scores)
            h, w = mask.shape
            x, y = left + w // 2, top + h // 2
            if score >= SHORTCUT_SCORE and has_shortcut_button(search, x, y):
                matches.append((score, motif, x, y))
        if not matches:
            return None
        _, motif, x, y = max(matches)
        if any(abs(mx-x) > 3 or abs(my-y) > 3 for _, _, mx, my in matches):
            return None, None
        return (x1+x, y1+y), motif

    def match(self, gray, name, region):
        x1, y1, x2, y2 = region
        template = self.templates[name]
        search = gray[y1:y2, x1:x2]
        if min(search.shape) < min(template.shape):
            return None
        result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, pos = cv2.minMaxLoc(result)
        if score < .78:
            return None
        return (x1 + pos[0] + template.shape[1]//2,
                y1 + pos[1] + template.shape[0]//2)

    def read_lines(self, frame, boxes, source=None, viewport=None, *, interpolation=cv2.INTER_NEAREST):
        crops = []
        for x1, y1, x2, y2 in boxes.values():
            if source is not None:
                l, t, r, b = viewport
                crop = np.array(source.crop((round(l+x1*(r-l)/1200), round(t+y1*(b-t)/800),
                                             round(l+x2*(r-l)/1200), round(t+y2*(b-t)/800))).convert('RGB'))
            else:
                crop = frame[y1:y2, x1:x2]
            rgb = cv2.resize(crop, None, fx=2, fy=2, interpolation=interpolation)
            crops.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        # Small timers lose glyph detail if padded to a long HP line in one batch.
        results = [self.ocr.text_recognizer([crop])[0][0] for crop in crops]
        return {name: text.strip() if score >= .60 else ''
                for name, (text, score) in zip(boxes, results)}

    def observe(self, image):
        frame, rect = normalize(image)
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        obs = Observation(viewport=rect)
        regions = {'exit': SHORTCUT_REGION,
                   'fight': (285, 122, 375, 151), 'menu': MENU_TITLE_REGION,
                   'attack': (790, 525, 950, 580), 'return': (790, 525, 950, 580)}
        obs.targets = {name: point for name, region in regions.items()
                       if (point := self.match(gray, name, region)) is not None}
        if 'menu' not in obs.targets and ('attack' in obs.targets or 'return' in obs.targets):
            # Small viewports soften title edges. Still require a separate button match.
            title = self.match(gray, 'menu-soft', MENU_TITLE_REGION)
            if title is not None:
                obs.targets['menu'] = title
        if 'exit' not in obs.targets:
            icon = self.match_shortcut(frame)
            if icon is not None:
                point, obs.shortcut_motif = icon
                if point is not None:
                    obs.targets['shortcut'] = point
            else:
                for reference in ('shortcut', 'shortcut-cooldown'):
                    point = self.match(gray, reference, SHORTCUT_REGION)
                    x1, y1, x2, y2 = SHORTCUT_REGION
                    if point is not None and has_shortcut_button(
                            frame[y1:y2, x1:x2], point[0]-x1, point[1]-y1):
                        obs.targets['shortcut'] = point
                        break
        if 'menu' in obs.targets and 'return' in obs.targets:
            obs.scene = 'return'
        elif 'menu' in obs.targets and 'attack' in obs.targets:
            obs.scene = 'menu'
            obs.raw = self.read_lines(frame, {
                'hp': (810, 215, 974, 234),
                'level': (810, 184, 896, 201),
            })
            # A second resampling of the tiny digits can turn a 0 into a Q.
            obs.raw.update(self.read_lines(frame, {'cooldown': MENU_COOLDOWN_REGION}, image, rect))
            obs.seconds = menu_cooldown_seconds(obs.raw['cooldown'])
            if obs.seconds is None:
                # Refocus OCR on the digits at small sizes. Alternate readings
                # can extend the wait, but cannot establish a ready state.
                retry = self.read_lines(frame, {'cooldown': MENU_COOLDOWN_DIGITS},
                                        image, rect, interpolation=cv2.INTER_CUBIC)['cooldown']
                if seconds := menu_cooldown_seconds(retry):
                    obs.raw['cooldown'], obs.seconds = retry, seconds
            hp_text = re.sub(r'^\s*[Hh][Pp]\s*[:.]?\s*', '', obs.raw['hp'])
            obs.hp = number(hp_text.split('/')[0])
            level = re.search(r'Obelisk\s*[:.]?\s*(\d+)', obs.raw['level'], re.I)
            obs.level = int(level[1]) if level else None
        elif 'fight' in obs.targets and 'exit' in obs.targets:
            obs.scene = 'fight'
            obs.targets['hold'] = (270, 380)
            obs.raw = self.read_lines(frame, {
                'total': (118, 91, 220, 110),
                'dps': (354, 91, 451, 109),
                'timer': (318, 149, 359, 169),
                'hp': (255, 189, 321, 210),
            }, image, rect)
            # In a timer-only crop these are common OCR variants of the zero glyph.
            timer_text = obs.raw['timer'].translate(str.maketrans({'O': '0', 'o': '0', 'D': '0'}))
            obs.seconds = timer(timer_text)
            obs.total = number(obs.raw['total'])
            obs.dps = number(obs.raw['dps'])
            obs.hp = number(obs.raw['hp'])
        elif 'shortcut' in obs.targets:
            obs.scene = 'navigation'
        return obs, Image.fromarray(frame)

