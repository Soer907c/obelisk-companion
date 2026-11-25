import unittest
from unittest.mock import Mock, patch

import bootstrap
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from relics.capture import capture_current_result
from relics.vision import (
    DIVINE_NAMES, RelicReading, TextLine, read_image, read_loot_popup, read_loot_rows,
    read_overview, read_summary_viewport,
)


def overview(counts=('(10x)', '(9x)', '(8x)', '(3x)', '(0x)')):
    anchors = [
        TextLine('Chance: 1/25k', 430, 15, 18),
        TextLine('Your Average', 430, 40, 18),
        TextLine('Tap to close', 220, 150, 18),
    ]
    return anchors + [
        TextLine(f'{count} {name}', 25, 15 + index * 24, 18)
        for index, (name, count) in enumerate(zip(DIVINE_NAMES, counts, strict=True))
        if count is not None
    ]


class RelicVisionTests(unittest.TestCase):
    def test_loot_recovers_name_and_count_from_the_same_row(self):
        rgb = np.zeros((800, 1200, 3), dtype=np.uint8)
        rgb[600:605, 740:760] = (255, 255, 0)
        rgb[614:619, 740:760] = (255, 255, 0)
        ocr = Mock()

        def responses(portal_counts, footer='Tap anywhere to close'):
            values = [
                ['Loot'], ['from:'], [footer],
                ['(l4x) Supernava Multipliers'], ['Supernova Multipliers'],
                ['[14x]', '', '', '', '', '14x', ''],
                ['l1xl Rainbow Portal Chance'],
                portal_counts,
            ]
            return [([(value, .85) for value in line], None) for line in values]

        with patch('relics.vision.normalize', return_value=(rgb, None)):
            ocr.text_recognizer.side_effect = responses(['[1x]', '', '', '', '', '1x', ''])
            self.assertEqual(read_loot_popup(None, ocr).counts, (None, 14, None, None, 1))
            ocr.text_recognizer.side_effect = responses(['[1x]', '', '', '', '', '7x', ''])
            reading = read_loot_popup(None, ocr)
            self.assertEqual(reading.counts, (None, 14, None, None, None))
            self.assertIsNone(reading.matches((0, 14, 0, 0, 1)))
            ocr.text_recognizer.side_effect = responses([], footer='Tap to close')
            self.assertFalse(read_loot_popup(None, ocr).recognized)

    def test_summary_ocr_variants_and_independent_count_crops(self):
        image = Image.new('RGB', (1200, 800))
        ocr = Mock()
        rows = ['Tap to clase', 'Chante:1/25k', 'Your Averase:',
                '[1ix] Rainbow FLoor Chance +1%', '[10x] Supernova Multipliers +2x',
                '[4x] Rainbow Floor Multi +20%', '[5x] Fishing Tick 5x Chance +2%', '']

        def results(values):
            return [(value, .8) for value in values], None

        first = results(rows)
        first[0][2] = (rows[2], .651)
        with patch('relics.vision.normalize', return_value=(np.asarray(image), None)):
            ocr.text_recognizer.side_effect = [first, results(['[11x]', '[11x]'])]
            self.assertEqual(read_summary_viewport(image, ocr).counts, (11, 10, 4, 5, None))
            ocr.text_recognizer.side_effect = [first, results(['[11x]', '[1x]'])]
            self.assertEqual(read_summary_viewport(image, ocr).counts, (None, 10, 4, 5, None))
            ocr.text_recognizer.side_effect = [results(['Tap anywhere to close', *rows[1:]])]
            self.assertFalse(read_summary_viewport(image, ocr).recognized)

    def test_completed_loot_popup_counts_preserve_missing_type(self):
        rows = [
            '6x rainbow flaor chance +ls',
            '(3xl supernova multipliers +2x',
            '6xlrainbowfloor multi+20%',
            '4xlfishing tick 5x chance +2%',
        ]
        self.assertEqual(read_loot_rows(rows).counts, (6, 3, 6, 4, None))

    def test_same_reading_must_meet_all_positive_minimums(self):
        reading = read_overview(overview())
        self.assertEqual(reading.counts, (10, 9, 8, 3, 0))
        self.assertTrue(reading.matches((10, 9, 0, 0, 0)))
        self.assertFalse(reading.matches((10, 10, 0, 0, 0)))

    def test_open_all_overview_values_and_singular_fishing_label(self):
        lines = overview(('(27x)', '(20x)', '(20x)', '(12x)', '(16x)'))
        lines[-2] = TextLine('(12x) Fishing Tick 5x Chance +2%', 25, 87, 18)
        reading = read_overview(lines)
        self.assertEqual(reading.counts, (27, 20, 20, 12, 16))
        self.assertTrue(reading.matches((0, 20, 0, 12, 16)))

    def test_unreadable_count_and_missing_type_are_not_zero(self):
        reading = read_overview(overview(('(10x)', '(?x)', '(8x)', '(3x)', None)))
        self.assertEqual(reading.counts, (10, None, 8, 3, None))
        self.assertIsNone(reading.matches((0, 1, 0, 0, 0)))
        self.assertIsNone(reading.matches((0, 0, 0, 0, 1)))
        self.assertTrue(reading.matches((10, 0, 0, 0, 0)))

    def test_fresh_identical_images_provide_no_completion_evidence(self):
        first = read_overview(overview())
        second = read_overview(overview())
        # These are simulated overview observations without a transition signal.
        self.assertEqual(first, second)
        self.assertEqual(second, RelicReading(True, (10, 9, 8, 3, 0)))

    def test_wrong_screen_even_if_it_contains_named_loot(self):
        self.assertEqual(read_overview(overview()[3:]), RelicReading(False))

    def test_ocr_on_simulated_overview_image(self):
        image = Image.new('RGB', (680, 210), '#6b5152')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('consola.ttf', 19)
        for line in overview():
            x, y = int(line.x), int(line.y)
            draw.rectangle((x - 3, y - 3, x + round(draw.textlength(line.text, font)) + 3, y + 22),
                           fill='#11161b')
            draw.text((x, y), line.text, font=font, fill='white')
        self.assertEqual(read_image(image), RelicReading(True, (10, 9, 8, 3, 0)))

    @patch('relics.capture.Desktop')
    def test_capture_requires_foreground_and_sends_no_input(self, desktop_class):
        desktop = desktop_class.return_value
        desktop.bounds.return_value = (0, 0, 1200, 800)
        desktop.foreground.return_value = True
        desktop.capture.return_value = Mock()
        self.assertIs(capture_current_result(), desktop.capture.return_value)
        desktop.capture.assert_called_once_with()
        desktop.foreground.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'foreground'):
            capture_current_result(wait_seconds=0)
        desktop.capture.assert_called_once_with()
        for method in ('focus', 'click', 'hold', 'release', 'point'):
            getattr(desktop, method).assert_not_called()
