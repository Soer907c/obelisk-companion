from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

import bootstrap
from PIL import Image

from relics.live_trial import TrialClicks, run_auto_reroll
from relics.payments import PaymentStore
from relics.replay import DispatchState, FakeClicks, RerollReplay
from relics.simulation import Action, RerollSimulation, Screen
from relics.vision import RelicReading


POOL = (12, 0, 4)
EMPTY = (0, 0, 0)
MISS = RelicReading(True, (6, 3, 6, 4, None))
FRAMES = {
    1: dict(screen=Screen.LOOT, reading=MISS),
    2: dict(screen=Screen.MENU, gems=400, price=200, unopened=EMPTY, refundable=POOL),
    3: dict(screen=Screen.CONFIRM, gems=400, price=200, unopened=EMPTY, refundable=POOL),
    4: dict(screen=Screen.MENU, gems=200, unopened=POOL, refundable=EMPTY, open_all_available=True),
    5: dict(screen=Screen.PROCESSING),
}


class RelicReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))

    def test_goal_verification_blocks_and_counts_payment_at_dispatch(self):
        desktop = Mock()
        clicks = TrialClicks(desktop, lambda: False, block_input=True)
        with self.assertRaisesRegex(RuntimeError, 'blocked DISMISS_RESULT'):
            clicks.record(Action.DISMISS_RESULT)
        self.assertEqual(clicks.blocked_payment_attempts, 0)
        with self.assertRaisesRegex(RuntimeError, 'blocked CONFIRM_PURCHASE'):
            clicks.record(Action.CONFIRM_PURCHASE)
        self.assertEqual(clicks.blocked_payment_attempts, 1)
        desktop.point.assert_not_called()
        desktop.click_current.assert_not_called()

    def test_denied_handoff_never_captures_or_clicks(self):
        desktop = Mock()
        desktop.foreground.return_value = False
        clicks, updates = FakeClicks(), []
        with patch('core.windows.time.monotonic', side_effect=[0, 3]):
            run_auto_reroll((0, 1, 0, 0, 0), 200, Event(), updates.append,
                            desktop=desktop, clicks=clicks)
        desktop.focus.assert_called_once()
        desktop.capture.assert_not_called()
        self.assertEqual(clicks.actions, [])
        self.assertIn('foreground', updates[-2].message)

    def test_free_summary_dismissal_requires_fresh_menu(self):
        desktop = Mock()
        desktop.capture.side_effect = [Image.new('L', (1, 1), i) for i in range(3)]
        clicks, updates = FakeClicks(), []
        screens = [Screen.OVERVIEW, Screen.OVERVIEW, Screen.MENU]
        with patch('relics.live_trial.normalize', return_value=(None, (0, 0, 1, 1))):
            run_auto_reroll((0, 1, 0, 0, 0), 200, Event(), updates.append,
                            desktop=desktop, clicks=clicks, verify_handoff='dismiss',
                            recognize=lambda frame: {'screen': screens[frame.getpixel((0, 0))]})
        self.assertEqual(clicks.actions, [Action.DISMISS_RESULT])
        self.assertEqual(updates[-2].spent, 0)
        self.assertIn('fresh Relics menu', updates[-2].message)

    def start(self, name):
        self.clicks = FakeClicks()
        self.sim = RerollSimulation((0, 4, 0, 0, 0), 200, now=0,
                                    payments=PaymentStore(self.directory / name))
        self.replay = RerollReplay(self.sim, 'game', lambda im: FRAMES[im.getpixel((0, 0))],
                                   lambda: self.state, self.clicks)
        self.state = DispatchState(self.replay.session, 'game')

    def capture(self, number):
        return self.replay.capture(lambda: Image.new('L', (1, 1), number))

    def submit(self, number):
        self.assertTrue(self.replay.observe(self.replay.read(self.capture(number)), now=1))
        return self.replay.dispatch()

    def test_delayed_old_ocr_is_rejected_and_identical_fresh_loot_is_accepted(self):
        self.start('old-ocr')
        self.assertTrue(self.submit(1))
        self.assertTrue(self.submit(2))
        self.assertTrue(self.submit(3))
        self.assertTrue(self.replay.observe(self.replay.read(self.capture(4)), now=1))
        old_loot = self.capture(1)  # Captured before the pending Open All is dispatched.
        old_processing = self.capture(5)
        self.assertTrue(self.replay.dispatch())
        self.assertEqual(self.clicks.actions[-1], Action.OPEN_ALL)
        self.assertFalse(self.replay.observe(self.replay.read(old_processing), now=1))
        self.assertFalse(self.submit(5))

        delayed_result = self.replay.read(old_loot)
        self.assertEqual(delayed_result.observation.reading, MISS)
        self.assertEqual(delayed_result.observation.frame_id, old_loot.id)
        self.assertFalse(self.replay.observe(delayed_result, now=1))
        self.assertEqual(self.sim.completed_rolls, 0)
        self.assertFalse(self.submit(1))
        self.assertEqual(self.sim.completed_rolls, 1)
        self.assertEqual(self.sim.latest_result, MISS)

    def test_dispatch_rechecks_f8_focus_and_session_after_payment_decision(self):
        for name, reason in (('f8', 'F8'), ('focus', 'focus'), ('session', 'session changed')):
            with self.subTest(name=name):
                self.start(name)
                self.assertTrue(self.submit(1))
                self.assertTrue(self.submit(2))
                self.clicks.actions.clear()
                self.assertTrue(self.replay.observe(self.replay.read(self.capture(3)), now=1))
                self.assertEqual(self.replay.pending_action, Action.CONFIRM_PURCHASE)
                self.state = DispatchState(
                    object() if name == 'session' else self.replay.session,
                    'other window' if name == 'focus' else 'game',
                    stopped=name == 'f8',
                )
                self.assertFalse(self.replay.dispatch())
                self.assertIn(reason, self.sim.stop_reason)
                self.state = DispatchState(self.replay.session, 'game')
                self.assertFalse(self.replay.dispatch())
                self.assertEqual(self.clicks.actions, [])

    def test_live_trial_payment_limit_survives_new_receiver(self):
        desktop = Mock()
        desktop.point.return_value = (100, 100)
        desktop.abort.is_set.return_value = False
        def cursor(pointer):
            pointer._obj.x = pointer._obj.y = 100
            return True
        with (patch('relics.live_trial.TRIAL_LIMIT', self.directory / 'trial.used'),
              patch('relics.live_trial.user32.SetCursorPos', return_value=True),
              patch('relics.live_trial.user32.GetCursorPos', side_effect=cursor),
              patch('relics.live_trial.sleep')):
            TrialClicks(desktop, lambda: False).record(Action.CONFIRM_PURCHASE)
            with self.assertRaisesRegex(RuntimeError, 'already used'):
                TrialClicks(desktop, lambda: False).record(Action.CONFIRM_PURCHASE)
        self.assertEqual(desktop.click_current.call_count, 1)

    def test_auto_reroll_through_panel_runner(self):
        match = RelicReading(True, (6, 4, 6, 4, None))
        for budget, initial, final, buys, remaining, reason in (
            (600, MISS, match, 2, 200, 'All minimums met.'),
            (0, MISS, match, 2, None, 'All minimums met.'),
            (0, match, match, 0, None, 'All minimums met.'),
            (450, MISS, MISS, 2, 50, 'Remaining budget is below 200 gems.'),
            (600, match, match, 0, 600, 'All minimums met.'),
        ):
            with self.subTest(budget=budget, initial=initial):
                frames = [dict(screen=Screen.OVERVIEW, reading=initial)]
                for i in range(buys):
                    balance = 1000 - i * 200
                    frames.extend((
                        dict(screen=Screen.MENU, gems=balance, price=200,
                             unopened=EMPTY, refundable=(511223, 74, 3)),
                        dict(screen=Screen.CONFIRM, gems=balance, price=200,
                             unopened=EMPTY, refundable=(511223, 74, 3)),
                        dict(screen=Screen.MENU, gems=balance-200, refundable=EMPTY,
                             chest_text=('0', '0', '0', '511k', '74', '3'),
                             open_all_available=True),
                        dict(screen=Screen.MENU, gems=balance-200,
                             chest_text=('0', '0', '0', '396k', '74', '3')),
                        dict(screen=Screen.MENU, gems=balance-200,
                             chest_text=('372282', '0', '0', '', '74', '3')),
                        dict(screen=Screen.LOOT, reading=final if i == buys-1 else MISS),
                    ))
                desktop = Mock()
                desktop.capture.side_effect = [Image.new('L', (1, 1), i) for i in range(len(frames))]
                receiver, updates = FakeClicks(), []
                run_auto_reroll((0, 4, 0, 0, 0), budget, Event(), updates.append,
                                desktop=desktop, hotkey=Mock(pressed=lambda: False), clicks=receiver,
                                recognize=lambda image: frames[image.getpixel((0, 0))], pause=0,
                                payments=PaymentStore(self.directory / f'auto-{budget}-{buys}'))
                self.assertEqual(receiver.actions.count(Action.CONFIRM_PURCHASE), buys)
                self.assertEqual(receiver.actions.count(Action.OPEN_ALL), buys)
                end = updates[-2]
                self.assertEqual((end.completed, end.spent, end.remaining, end.message),
                                 (buys, buys * 200, remaining, reason))

    def test_unlimited_runner_stops_on_low_balance_or_f8(self):
        for low_balance in (True, False):
            with self.subTest(low_balance=low_balance):
                desktop = Mock()
                desktop.capture.return_value = Image.new('L', (1, 1))
                abort, receiver, updates = Event(), FakeClicks(), []

                def recognize(_):
                    if not low_balance:
                        abort.set()  # The shared F8 handler sets the same abort event.
                    return dict(screen=Screen.OVERVIEW, reading=MISS,
                                gems=199 if low_balance else 1000)

                run_auto_reroll((0, 4, 0, 0, 0), 0, abort, updates.append, desktop=desktop,
                                clicks=receiver, recognize=recognize, pause=0,
                                payments=PaymentStore(self.directory / f'stop-{low_balance}'))
                self.assertEqual(receiver.actions, [])
                self.assertEqual(updates[-2].spent, 0)
                self.assertIsNone(updates[-2].remaining)
                self.assertIn('Insufficient gems' if low_balance else 'F8', updates[-2].message)

    def test_two_roll_trial_slots_are_durable_and_bounded(self):
        desktop = Mock()
        desktop.point.return_value = (100, 100)
        desktop.abort.is_set.return_value = False

        def cursor(pointer):
            pointer._obj.x = pointer._obj.y = 100
            return True

        slots = tuple(self.directory / f'trial-{number}.used' for number in (1, 2))
        with (patch('relics.live_trial.TWO_ROLL_LIMITS', slots),
              patch('relics.live_trial.user32.SetCursorPos', return_value=True),
              patch('relics.live_trial.user32.GetCursorPos', side_effect=cursor),
              patch('relics.live_trial.sleep')):
            clicks = TrialClicks(desktop, lambda: False, supervised=2)
            clicks.record(Action.CONFIRM_PURCHASE)
            with self.assertRaisesRegex(RuntimeError, 'already used'):
                TrialClicks(desktop, lambda: False, supervised=2).record(Action.CONFIRM_PURCHASE)
            clicks.record(Action.CONFIRM_PURCHASE)
            with self.assertRaisesRegex(RuntimeError, 'Both'):
                clicks.record(Action.CONFIRM_PURCHASE)
        self.assertTrue(all(slot.exists() for slot in slots))
        self.assertEqual(desktop.click_current.call_count, 2)

    def test_verification_allowance_is_shared_across_receivers(self):
        desktop = Mock()
        desktop.point.return_value = (100, 100)
        desktop.abort.is_set.return_value = False

        def cursor(pointer):
            pointer._obj.x = pointer._obj.y = 100
            return True

        slots = (self.directory / 'first.used', self.directory / 'second.used')
        with (patch('relics.live_trial.VERIFICATION_LIMITS', slots),
              patch('relics.live_trial.user32.SetCursorPos', return_value=True),
              patch('relics.live_trial.user32.GetCursorPos', side_effect=cursor),
              patch('relics.live_trial.sleep')):
            for _ in slots:
                TrialClicks(desktop, lambda: False, supervised=20).record(Action.CONFIRM_PURCHASE)
            with self.assertRaisesRegex(RuntimeError, 'allowance has been used'):
                TrialClicks(desktop, lambda: False, supervised=20).record(Action.CONFIRM_PURCHASE)
        self.assertEqual(desktop.click_current.call_count, len(slots))

    def test_rounded_refund_requires_debit_and_consistent_pool(self):
        self.start('rounding')
        self.submit(1)
        self.submit(2)
        self.submit(3)
        from relics.simulation import Observation

        def refund(frame_id, gems, relics, *, available=True):
            return self.sim.observe(Observation(
                frame_id, Screen.MENU, gems=gems, refundable=EMPTY,
                open_all_available=available, chest_text=('0', '0', '0', relics, '0', '4'),
            ), now=1)

        self.assertIsNone(refund(4, 400, '12k'))
        self.assertIsNone(refund(5, 200, '12k'))
        self.assertIsNone(refund(6, 200, '0k', available=False))
        self.assertEqual(self.sim.spent, 0)
        self.assertIsNotNone(self.sim.payments.pending())
        self.assertEqual(refund(7, 200, '12'), Action.OPEN_ALL)
        self.assertEqual(self.sim.spent, 200)
