from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import bootstrap
from relics.payments import PaymentStore
from relics.simulation import Action, Observation, Phase, RerollSimulation, Screen
from relics.vision import RelicReading


MISS = RelicReading(True, (6, 3, 6, 4, None))
MATCH = RelicReading(True, (6, 4, 6, 4, None))
EMPTY = (0, 0, 0)
CHESTS = (511_221, 74, 3)
TARGET = (0, 4, 0, 4, 0)


class RerollSimulationTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.payment_path = Path(directory) / 'payments.sqlite3'

    def start(self, budget=450, minimums=TARGET, **timeouts):
        self.sim = RerollSimulation(minimums, budget, now=0,
                                    payments=PaymentStore(self.payment_path), **timeouts)
        self.frame_id = 0
        self.actions = []

    def see(self, screen, *, now=0, frame_id=None, **fields):
        self.frame_id += 1
        action = self.sim.observe(
            Observation(self.frame_id if frame_id is None else frame_id, screen, **fields),
            now=now,
        )
        if action:
            self.actions.append(action)
        if self.sim.budget:
            self.assertLessEqual(self.sim.spent + self.sim.reserved, self.sim.budget)
        return action

    def menu(self, gems=1000, **fields):
        return self.see(Screen.MENU, gems=gems, **{'unopened': EMPTY, 'refundable': CHESTS,
                                                  'price': 200, **fields})

    def orange(self, gems=1000, **fields):
        return self.see(Screen.CONFIRM, gems=gems, **{
            'unopened': EMPTY, 'refundable': CHESTS, 'price': 200, **fields,
        })

    def refund(self, gems=800, **fields):
        return self.see(Screen.MENU, gems=gems, **{'unopened': CHESTS,
                                                  'refundable': EMPTY,
                                                  'open_all_available': True, **fields})

    def roll(self, before=1000, result=MISS):
        self.assertEqual(self.menu(before), Action.ARM_PURCHASE)
        self.assertEqual(self.orange(before), Action.CONFIRM_PURCHASE)
        self.assertEqual(self.refund(before - 200), Action.OPEN_ALL)
        self.assertIsNone(self.see(Screen.PROCESSING))
        return self.see(Screen.LOOT, reading=result)

    def test_already_met_and_unlimited_budget_never_click(self):
        for budget, reason in ((400, 'All minimums met'), (0, 'All minimums met')):
            with self.subTest(budget=budget):
                self.start(budget)
                self.assertIsNone(self.see(Screen.OVERVIEW, reading=MATCH))
                self.assertIn(reason, self.sim.stop_reason)
                self.assertEqual(self.actions, [])
                self.assertEqual((self.sim.spent, self.sim.reserved), (0, 0))

    def test_budget_limits_199_200_and_450(self):
        for budget, attempts in ((199, 0), (200, 1), (450, 2)):
            with self.subTest(budget=budget):
                self.start(budget)
                self.see(Screen.OVERVIEW, reading=MISS)
                for index in range(attempts):
                    self.roll(1000 - 200 * index)
                self.assertEqual(self.actions.count(Action.CONFIRM_PURCHASE), attempts)
                self.assertEqual(self.actions.count(Action.OPEN_ALL), attempts)
                self.assertEqual(self.sim.spent, attempts * 200)
                self.assertIn('Remaining budget', self.sim.stop_reason)

    def test_delays_allow_no_duplicate_confirmation_or_opening(self):
        self.start()
        self.see(Screen.OVERVIEW, reading=MISS)
        self.assertEqual(self.menu(), Action.ARM_PURCHASE)
        self.assertEqual(self.sim.reserved, 200)
        self.assertIsNone(self.menu())
        self.assertEqual(self.orange(), Action.CONFIRM_PURCHASE)
        self.assertIsNone(self.orange())
        self.assertIsNone(self.menu())
        self.assertIsNone(self.refund(None))
        self.assertEqual((self.sim.spent, self.sim.reserved), (0, 200))
        self.assertIsNone(self.refund(unopened=(511_220, 74, 3)))
        self.assertEqual((self.sim.spent, self.sim.reserved), (0, 200))
        self.assertEqual(self.refund(), Action.OPEN_ALL)
        self.assertIsNone(self.refund())
        self.assertEqual((self.sim.spent, self.sim.reserved), (200, 0))
        self.assertEqual(self.actions, [Action.DISMISS_RESULT, Action.ARM_PURCHASE,
                                        Action.CONFIRM_PURCHASE, Action.OPEN_ALL])

    def test_identical_divines_are_new_only_after_processing_and_fresh_loot(self):
        self.start()
        self.see(Screen.LOOT, reading=MISS)
        self.menu()
        self.orange()
        self.refund()
        self.assertIsNone(self.sim.latest_result)
        self.see(Screen.PROCESSING)
        self.assertIsNone(self.see(Screen.LOOT, reading=MISS, frame_id=1))
        self.assertEqual(self.sim.completed_rolls, 0)
        self.assertEqual(self.see(Screen.LOOT, reading=MISS), Action.DISMISS_RESULT)
        self.assertEqual(self.sim.completed_rolls, 1)
        self.assertIsNone(self.roll(800, MATCH))
        self.assertEqual(self.sim.completed_rolls, 2)
        self.assertIn('All minimums met', self.sim.stop_reason)

    def test_loot_without_processing_stops_and_cannot_reuse_result(self):
        self.start()
        self.see(Screen.LOOT, reading=MISS)
        self.menu()
        self.orange()
        self.refund()
        self.assertIsNone(self.see(Screen.LOOT, reading=MISS))
        self.assertIn('Unexpected loot', self.sim.stop_reason)
        self.assertEqual(self.sim.completed_rolls, 0)
        self.assertIsNone(self.sim.latest_result)

    def test_missing_portal_stops_only_when_required(self):
        self.start(minimums=(0, 4, 0, 0, 1))
        self.assertIsNone(self.see(Screen.OVERVIEW, reading=MISS))
        self.assertIn('Rainbow Portal Chance', self.sim.stop_reason)
        self.assertEqual(self.actions, [])
        self.start(minimums=(0, 4, 0, 0, 1))
        self.see(Screen.OVERVIEW, reading=RelicReading(True, (6, 3, 6, 4, 1)))
        self.assertIsNone(self.roll())
        self.assertIn('Rainbow Portal Chance', self.sim.stop_reason)
        self.assertEqual(self.sim.spent, 200)

    def test_unreadable_or_insufficient_balance_never_arms(self):
        for gems in (None, 199):
            with self.subTest(gems=gems):
                self.start()
                self.see(Screen.OVERVIEW, reading=MISS)
                self.assertIsNone(self.menu(gems))
                self.assertEqual(self.actions, [Action.DISMISS_RESULT])
                self.assertEqual(self.sim.reserved, 0)

    def test_ambiguous_payment_keeps_reservation_and_never_retries(self):
        for gems, time, reason in ((900, 1, 'ambiguous'), (None, 8, 'Timed out')):
            with self.subTest(reason=reason):
                self.payment_path = self.payment_path.with_name(f'payment-{time}.sqlite3')
                self.start()
                self.see(Screen.OVERVIEW, reading=MISS)
                self.menu()
                self.orange()
                self.assertIsNone(self.see(Screen.MENU, now=time, gems=gems))
                self.assertIn(reason, self.sim.stop_reason)
                self.assertEqual((self.sim.spent, self.sim.reserved), (0, 200))
                self.assertIsNone(self.refund())
                self.assertEqual(self.actions.count(Action.CONFIRM_PURCHASE), 1)

    def test_abort_before_and_after_arming(self):
        failures = [
            (Screen.MENU, {'f8': True}, 'F8'),
            (Screen.MENU, {'focused': False}, 'focus'),
            (Screen.MENU, {'now': 2}, 'Timed out'),
            (Screen.UNKNOWN, {}, 'Unexpected'),
        ]
        for armed in (False, True):
            for screen, fields, reason in failures:
                with self.subTest(armed=armed, reason=reason):
                    self.start(session_timeout=2)
                    self.see(Screen.OVERVIEW, reading=MISS)
                    if armed:
                        self.menu()
                    self.assertIsNone(self.see(screen, **fields))
                    self.assertEqual(self.sim.phase, Phase.STOPPED)
                    self.assertIn(reason, self.sim.stop_reason)
                    self.assertIsNone(self.orange())
                    self.assertNotIn(Action.CONFIRM_PURCHASE, self.actions)

    def test_f8_while_payment_is_pending_preserves_reservation(self):
        self.start()
        self.see(Screen.OVERVIEW, reading=MISS)
        self.menu()
        self.orange()
        self.assertIsNone(self.see(Screen.MENU, f8=True, gems=800, unopened=CHESTS,
                                   open_all_available=True))
        self.assertIn('F8', self.sim.stop_reason)
        self.assertEqual((self.sim.spent, self.sim.reserved), (0, 200))
        self.assertIsNone(self.refund())

    def test_opening_deadline_stops_on_unfinished_or_late_result(self):
        self.start(opening_timeout=2)
        self.see(Screen.OVERVIEW, reading=MISS)
        self.menu()
        self.orange()
        self.refund()
        self.assertIsNone(self.see(Screen.PROCESSING, now=1))
        self.assertIsNone(self.see(Screen.LOOT, now=2, reading=MATCH))
        self.assertIn('Timed out', self.sim.stop_reason)
        self.assertEqual(self.sim.completed_rolls, 0)
        self.assertEqual((self.sim.spent, self.sim.reserved), (200, 0))

    def test_restart_blocks_second_click_and_reconciles_once(self):
        self.start(budget=200)
        self.see(Screen.LOOT, reading=MISS)
        self.menu()
        self.orange()
        self.see(Screen.MENU, f8=True)
        first_clicks = self.actions.count(Action.CONFIRM_PURCHASE)
        self.assertEqual(first_clicks, 1)
        reopened = subprocess.run(
            [sys.executable, '-c',
             'import bootstrap, sys; from pathlib import Path; '
             'from relics.payments import PaymentStore; '
             'print(PaymentStore(Path(sys.argv[1])).pending().balance_after)',
             str(self.payment_path)],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(reopened.stdout.strip(), '800')

        self.start(budget=200)
        self.see(Screen.LOOT, reading=MISS)
        self.menu()
        self.orange()
        self.assertEqual(self.sim.phase, Phase.RECOVERY)
        self.assertIn('800 gems', PaymentStore(self.payment_path).pending().blocked_message())
        self.assertEqual(first_clicks + self.actions.count(Action.CONFIRM_PURCHASE), 1)
        self.assertIsNone(self.refund(refundable=CHESTS))
        self.assertEqual(self.sim.phase, Phase.RECOVERY)
        self.assertIsNone(self.refund())
        self.assertIn('payment confirmed', self.sim.stop_reason)
        self.assertIsNone(self.refund())
        self.assertEqual(self.actions, [])
        self.assertIsNone(PaymentStore(self.payment_path).pending())
        with closing(sqlite3.connect(self.payment_path)) as db:
            self.assertEqual(db.execute('SELECT pending FROM payments').fetchall(), [(None,)])

    def test_write_failure_blocks_payment_click(self):
        self.start()
        self.see(Screen.OVERVIEW, reading=MISS)
        self.menu()
        self.payment_path.mkdir()  # The database path cannot be opened for writing.
        self.assertIsNone(self.orange())
        self.assertIn('payment record could not be accessed', self.sim.stop_reason)
        self.assertEqual(self.actions.count(Action.CONFIRM_PURCHASE), 0)

    def test_transferred_chests_with_same_total_block_payment_click(self):
        self.start()
        self.see(Screen.OVERVIEW, reading=MISS)
        self.menu()
        self.assertIsNone(self.orange(unopened=CHESTS, refundable=EMPTY))
        self.assertIn('Chest inventory changed', self.sim.stop_reason)
        self.assertEqual(self.actions.count(Action.CONFIRM_PURCHASE), 0)
        self.assertIsNone(PaymentStore(self.payment_path).pending())
