import unittest
from decimal import Decimal
from pathlib import Path
import bootstrap
from PIL import Image
from obelisk.vision import Vision, load_shortcut_icons, normalize

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
        # The preserved reference is grayscale. Restore the red rails from
        # the original hub capture so the fixture also exercises button color.
        with Image.open(FIXTURES / 'hub.png') as hub:
            _, viewport = self.vision.observe(hub)
        for x in (507, 534):
            image.paste(viewport.crop((x, 238, x+3, 251)), (x, 238))
        obs, _ = self.vision.observe(image)
        self.assertEqual(obs.scene, 'navigation')
        self.assertEqual(obs.targets['shortcut'], (521, 244))

    def test_gallery_motifs_on_the_game_button_at_two_window_sizes(self):
        with Image.open(FIXTURES / 'hub.png') as original:
            obs, viewport = self.vision.observe(original)
        self.assertEqual(obs.shortcut_motif, 10)
        # Keep the real navigation scene and button frame. Replace only the
        # sprite area, then resize the viewport to exercise capture normalization.
        viewport.paste((154, 25, 47), (509, 229, 535, 262))
        icons = load_shortcut_icons()
        self.assertEqual(len(icons), 15)
        for motif, icon in enumerate(icons, 1):
            for size in ((1200, 800), (1116, 744)):
                with self.subTest(motif=motif, size=size):
                    sprite = icon.resize((26, 33), Image.Resampling.BILINEAR)
                    frame = viewport.copy()
                    frame.paste(sprite, (509, 229), sprite)
                    obs, _ = self.vision.observe(frame.resize(size, Image.Resampling.LANCZOS))
                    self.assertEqual(obs.scene, 'navigation')
                    self.assertEqual(obs.shortcut_motif, motif)
                    x, y = obs.targets['shortcut']
                    self.assertLessEqual(abs(x-522) + abs(y-245), 3)

    def test_gallery_sprite_without_a_button_and_fight_exit_are_not_shortcuts(self):
        frame = Image.new('RGB', (1200, 800), (107, 81, 82))
        sprite = load_shortcut_icons()[10].resize((26, 32))
        frame.paste(sprite, (509, 229), sprite)
        obs, _ = self.vision.observe(frame)
        self.assertNotIn('shortcut', obs.targets)
        with Image.open(FIXTURES / 'live-fight.png') as fight:
            obs, _ = self.vision.observe(fight)
        self.assertEqual(obs.scene, 'fight')
        self.assertNotIn('shortcut', obs.targets)

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

