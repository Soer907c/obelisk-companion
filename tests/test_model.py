import unittest
from decimal import Decimal
from obelisk.model import number, timer, compact, Statistics, FightTracker, Observation

class Numbers(unittest.TestCase):
    def test_suffixes_and_scale(self):
        self.assertEqual(number('33.5qadc') / number('100tdc'), Decimal(335))
        self.assertEqual(number('761udc'), Decimal('761e36'))
        self.assertEqual(number('60,8ddc'), Decimal('60.8e39'))

    def test_uncertain_readings_are_rejected(self):
        for bad in ('33.5qade', '60.8doo', '', '33.5qadc1', '1O0', '-1', 'NaN'):
            self.assertIsNone(number(bad))

    def test_timer_is_strict(self):
        self.assertEqual(timer('1:17'), 77)
        self.assertEqual(timer('0:00'), 0)
        self.assertIsNone(timer('0:70'))
        self.assertIsNone(timer('00'))
        self.assertIsNone(timer(''))

class Tracking(unittest.TestCase):
    def test_stale_dps_does_not_count_as_progress(self):
        tracker = FightTracker(0)
        tracker.update(Observation(total=Decimal(10), dps=Decimal(5), seconds=10), 1)
        tracker.update(Observation(total=Decimal(10), dps=Decimal(5), seconds=9), 4)
        self.assertEqual(tracker.last_progress, 1)

    def test_end_requires_consecutive_zero_readings(self):
        tracker = FightTracker(0)
        for seconds in (0, None, 1, 0):
            tracker.update(Observation(seconds=seconds), 1)
        self.assertEqual(tracker.zero_frames, 1)
        tracker.update(Observation(seconds=0), 2)
        self.assertEqual(tracker.zero_frames, 2)

    def test_eta_uses_measured_full_cycles(self):
        stats = Statistics()
        self.assertEqual(stats.estimate(Decimal(100)), (None, None))
        stats.started(0)
        stats.finished(Decimal(20))
        stats.started(70)
        stats.finished(Decimal(30))
        self.assertEqual(stats.estimate(Decimal(101)), (5, 350))

    def test_missing_damage_is_not_zero_damage_sample(self):
        stats = Statistics()
        stats.finished(None)
        self.assertEqual(stats.attempts, 1)
        self.assertEqual(stats.estimate(100), (None, None))

if __name__ == '__main__':
    unittest.main()

