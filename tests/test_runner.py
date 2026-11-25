import itertools
import queue
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import bootstrap
from PIL import Image
from obelisk.model import Observation, Settings
from obelisk.runner import Runner

class FakeDesktop:
    def __init__(self):
        self.abort = threading.Event()
        self.held = False
        self.actions = []

    def find(self): return 'Test game'
    def focus(self): pass
    def capture(self): return Image.new('RGB', (8, 8))
    def release(self): self.held = False
    def hold(self, target, viewport):
        self.held = True
        self.actions.append('hold')
    def click(self, target, viewport):
        self.actions.append(target)

class FakeVision:
    def __init__(self, observations):
        self.frames = iter(observations)
        self.last = observations[-1]

    def observe(self, image):
        self.last = next(self.frames, self.last)
        return self.last, image

def screen(scene, **kwargs):
    return Observation(scene=scene, targets={key: key for key in ('shortcut', 'attack', 'exit', 'return', 'hold')}, **kwargs)

class FightWorkflow(unittest.TestCase):
    def run_scenario(self, observations, settings):
        runner = Runner(queue.Queue())
        runner.desktop = FakeDesktop()
        runner.vision = FakeVision(observations)
        runner.cancel.wait = lambda seconds: runner.cancel.is_set()
        clock = itertools.count(0, 1)
        with tempfile.TemporaryDirectory() as temporary, \
             patch('obelisk.runner.RUNTIME', Path(temporary)), \
             patch('obelisk.runner.keep_awake'), \
             patch.object(runner, '_watchdog', lambda: None), \
             patch('obelisk.runner.time.monotonic', lambda: next(clock)):
            runner._run(settings)
        self.assertFalse(runner.desktop.held)
        return runner

    def test_full_cycle_and_attempt_limit(self):
        observations = [screen('navigation'),
                        screen('menu', seconds=0, level=70, hp=Decimal(100)),
                        screen('menu', seconds=0, level=70, hp=Decimal(100)),
                        screen('fight', seconds=3, total=Decimal(1)),
                        screen('fight', seconds=2, total=Decimal(10)),
                        screen('fight', seconds=0, total=Decimal(20)),
                        screen('fight', seconds=0, total=Decimal(20)),
                        screen('return'), screen('navigation'),
                        screen('menu', seconds=0, level=70, hp=Decimal(80))]
        runner = self.run_scenario(observations, Settings(max_attempts=1))
        self.assertEqual(runner.stats.attempts, 1)
        self.assertEqual(runner.stats.damages[0], 20)
        self.assertEqual(runner.stop_reason, 'Fight limit reached')
        self.assertEqual([a for a in runner.desktop.actions if a != 'hold'],
                         ['shortcut', 'attack', 'exit', 'return', 'shortcut'])

    def test_second_fight_after_return_and_cooldown(self):
        cycle = [screen('menu', seconds=0, level=70)] * 2 + [
            screen('fight', seconds=2, total=Decimal(1)),
            screen('fight', seconds=0, total=Decimal(10)),
            screen('fight', seconds=0, total=Decimal(10)),
            screen('return'), screen('navigation'),
            screen('menu', seconds=35, level=70)]
        runner = self.run_scenario(cycle * 2, Settings(max_attempts=2))
        self.assertEqual(runner.stop_reason, 'Fight limit reached')
        self.assertEqual(runner.stats.attempts, 2)
        self.assertEqual(runner.desktop.actions.count('attack'), 2)

    def test_unknown_screen_never_clicks(self):
        runner = self.run_scenario([screen('unknown')], Settings())
        self.assertEqual(runner.desktop.actions, [])
        self.assertIn('unrecognized', runner.stop_reason)

    def test_missing_cooldown_never_starts(self):
        runner = self.run_scenario([screen('menu', level=70)], Settings())
        self.assertEqual(runner.desktop.actions, [])
        self.assertIn('Cooldown', runner.stop_reason)

    def test_damage_stall_releases_and_stops(self):
        runner = self.run_scenario([screen('fight', seconds=30, total=Decimal(0))], Settings())
        self.assertIn('No confirmed damage progress', runner.stop_reason)

    def test_new_obelisk_stops_before_next_attack(self):
        runner = self.run_scenario([screen('menu', seconds=2, level=70),
                                    screen('menu', seconds=0, level=71)], Settings())
        self.assertEqual(runner.desktop.actions, [])
        self.assertIn('Obelisk defeated', runner.stop_reason)
