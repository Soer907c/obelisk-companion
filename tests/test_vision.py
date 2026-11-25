import unittest
from decimal import Decimal
from pathlib import Path
import bootstrap
from PIL import Image
from obelisk.vision import Vision, normalize

FIXTURES = Path(__file__).parent / 'fixtures'

class ScreenRecognition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vision = Vision()

    def test_known_navigation_screens(self):
        for filename, scene in [('hub', 'navigation'), ('ready', 'menu'), ('return', 'return'),
                                ('live-menu', 'menu'), ('live-return', 'return')]:
            with self.subTest(filename=filename):
                obs, _ = self.vision.observe(Image.open(FIXTURES / (filename+'.png')))
                self.assertEqual(obs.scene, scene)

    def test_post_fight_cooldown_shortcut(self):
        # Reconstruct just the failed search area on an otherwise empty viewport.
        image = Image.new('RGB', (1200, 800), (107, 81, 82))
        obs, _ = self.vision.observe(image)
        self.assertEqual(obs.scene, 'unknown')
        with Image.open(FIXTURES / 'shortcut-cooldown-region.pgm') as region:
            image.paste(region, (495, 220))
        obs, _ = self.vision.observe(image)
        self.assertEqual(obs.scene, 'navigation')
        self.assertEqual(obs.targets['shortcut'], (521, 244))

    def test_ready_level_hp_and_timer(self):
        obs, _ = self.vision.observe(Image.open(FIXTURES / 'ready.png'))
        self.assertEqual(obs.seconds, 0)
        self.assertEqual(obs.level, 70)
        self.assertEqual(obs.hp, Decimal('33.5e45'))

    def test_actual_capture_damage_and_timer(self):
        obs, _ = self.vision.observe(Image.open(FIXTURES / 'live-fight.png'))
        self.assertEqual(obs.scene, 'fight')
        self.assertEqual(obs.total, Decimal('83.9e42'))
        self.assertEqual(obs.dps, Decimal('324e39'))
        self.assertEqual(obs.seconds, 26)

    def test_finished_timer(self):
        obs, _ = self.vision.observe(Image.open(FIXTURES / 'finished.png'))
        self.assertEqual(obs.scene, 'fight')
        self.assertEqual(obs.seconds, 0)

    def test_unknown_viewport_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize(Image.new('RGB', (1200, 800), '#202020'))

