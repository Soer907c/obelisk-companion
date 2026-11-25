import time
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest
from threading import Event
from unittest.mock import Mock, patch

import bootstrap
from features import FEATURES
from relics.calculator import estimate_rerolls
from relics.vision import RelicReading
from ui import relic_panel
from ui.shell import Companion
from core.windows import StopHotkey, centered_bounds


class StartRoutingTests(unittest.TestCase):
    def test_centering_uses_work_area_at_negative_monitor_origin(self):
        work = (-1920, 20, 0, 1040)
        self.assertEqual(centered_bounds(work, 1240, 900), (-1580, 80, 1240, 900))
        self.assertEqual(centered_bounds(work, 2200, 1200), (-1920, 20, 1920, 1020))

    def test_fallback_emits_once_per_hold_across_focus_transfer(self):
        with patch('core.windows.user32') as win:
            win.RegisterHotKey.return_value = False
            win.GetAsyncKeyState.side_effect = [0, 0x8001, 0x8000, 0x8001, 0, 0x8001]
            hotkey = StopHotkey()
            self.assertEqual([hotkey.pressed() for _ in range(5)], [True, False, False, False, True])

    def test_f8_routes_only_to_selected_panel_and_stops_in_game(self):
        relic, fight = Mock(running=False), Mock(running=False)
        shell = Mock(active='relics', panels={'relics': relic, 'fight': fight})
        Companion.handle_f8(shell, False)
        relic.start.assert_not_called()
        Companion.handle_f8(shell, True)
        relic.start.assert_called_once_with()
        relic.running = True
        Companion.handle_f8(shell, False)
        relic.stop.assert_called_once_with()
        fight.start.assert_not_called()
        shell.active = 'fight'
        Companion.handle_f8(shell, True)
        fight.start.assert_called_once_with()


class RelicPanelTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch('core.storage.RUNTIME', Path(directory)))
        try:
            self.app = Companion()
        except tk.TclError as error:
            if 'no display' in str(error).lower():
                self.skipTest(str(error))
            raise
        self.app.withdraw()
        self.addCleanup(self.close_app)
        feature = next(feature for feature in FEATURES if feature.id == 'relics')
        self.app.show_feature(feature)
        self.panel = self.app.panels['relics']

    def close_app(self):
        # CustomTkinter has timers on the root and on child widgets.
        for token in self.app.tk.call('after', 'info'):
            self.app.tk.call('after', 'cancel', token)
        self.app.close()

    def test_button_and_f8_use_identical_settings_and_handoff(self):
        self.panel.fields[relic_panel.TARGET_NAMES[1]][0].set('10')
        self.panel.fields[relic_panel.BUDGET_LABEL][0].set('200')
        with patch.object(self.app, 'handoff_game') as handoff, \
             patch.object(self.panel, 'after'), patch('ui.relic_panel.Thread') as worker:
            self.panel.trial_button.invoke()
            first = worker.call_args.kwargs
            self.app.handle_f8(False)
            self.assertTrue(self.panel.trial_abort.is_set())
            self.panel.running = False
            self.panel.trial_button.configure(state='normal')
            self.app.handle_f8(True)
            second = worker.call_args.kwargs
        self.assertEqual(first['args'][:2], ((0, 10, 0, 0, 0), 200))
        self.assertEqual(first['args'][:2], second['args'][:2])
        self.assertTrue(all(call.args[0] is request['kwargs']['desktop']
                            for call, request in zip(handoff.call_args_list, (first, second), strict=True)))

    def set_field(self, name, value):
        self.panel.fields[name][0].set(value)

    def pump_until(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.update()
            if condition():
                return
            time.sleep(0.005)
        self.fail('Timed out waiting for the relic panel')

    def metrics(self):
        return tuple(self.panel.metrics[name].cget('text') for name in relic_panel.ESTIMATE_NAMES)

    def set_acceptance_inputs(self):
        self.set_field('Divine chests', '4')
        self.set_field(relic_panel.BUDGET_LABEL, '400')
        self.set_field('Supernova Multipliers', '1')

    def test_acceptance_estimates_and_invalid_edits(self):
        self.set_acceptance_inputs()
        self.assertEqual(self.panel.status.cget('text'), 'Calculating…')
        self.pump_until(lambda: self.metrics()[0] == '50%')
        self.assertEqual(self.metrics(), ('50%', '75%', '~2', '~400'))
        self.assertIn('400 gems across 2', self.panel.limit.cget('text'))
        self.assertEqual(self.panel.status.cget('text'), '')

        for text in ('', '1.5', '-1'):
            with self.subTest(text=text):
                self.set_field(relic_panel.BUDGET_LABEL, text)
                self.assertIn(f'{relic_panel.BUDGET_LABEL} requires', self.panel.status.cget('text'))
                self.assertEqual(self.metrics(), ('—',) * 4)
        self.set_field(relic_panel.BUDGET_LABEL, '399')
        self.set_field('Supernova Multipliers', '39')
        self.assertIn('Supernova Multipliers requires', self.panel.status.cget('text'))
        self.set_field('Supernova Multipliers', '1')
        self.set_field('Effective divine cap', '')
        self.pump_until(lambda: self.metrics()[0] == 'Unknown')
        self.assertEqual(self.metrics(), ('Unknown',) * 4)
        self.assertIn('200 gems across 1', self.panel.limit.cget('text'))

    def test_changed_target_cancels_in_flight_odds(self):
        started, cancelled = Event(), Event()

        def estimate(minimums, *args, **kwargs):
            if minimums[1] == 15:
                started.set()
                until = time.monotonic() + 2
                while not kwargs['cancelled']() and time.monotonic() < until:
                    time.sleep(0.005)
                if kwargs['cancelled']():
                    cancelled.set()
                    raise InterruptedError()
                raise TimeoutError('Old estimate was not cancelled.')
            return estimate_rerolls(minimums, *args, **kwargs)

        with patch.object(relic_panel, 'estimate_rerolls', side_effect=estimate):
            self.set_field('Divine chests', '4')
            self.set_field('Supernova Multipliers', '15')
            self.pump_until(started.is_set)
            self.set_field('Supernova Multipliers', '16')
            self.pump_until(cancelled.is_set)
            self.pump_until(lambda: self.panel.status.cget('text') == 'Target unreachable')

    def test_latest_edit_wins_while_calculation_is_in_flight(self):
        first_started, release_first = Event(), Event()
        second_started, release_second = Event(), Event()
        self.addCleanup(release_first.set)
        self.addCleanup(release_second.set)
        calls = []

        def controlled_estimate(minimums, *_args, **_kwargs):
            target = minimums[1]
            calls.append(target)
            if target == 15:
                first_started.set()
                if not release_first.wait(5):
                    raise TimeoutError('Test failed to release the first estimate')
            else:
                second_started.set()
                if not release_second.wait(5):
                    raise TimeoutError('Test failed to release the latest estimate')
            return estimate_rerolls(minimums, 0, 4 if target == 15 else 0, 400,
                                    rules=relic_panel.RerollRules(38))

        with patch.object(relic_panel, 'estimate_rerolls', side_effect=controlled_estimate):
            self.set_field('Divine chests', '4')
            self.set_field(relic_panel.BUDGET_LABEL, '400')
            self.set_field('Supernova Multipliers', '15')
            self.pump_until(first_started.is_set)

            # The input trace and the event loop stay usable while the worker waits.
            self.set_field('Supernova Multipliers', '')
            self.set_field('Supernova Multipliers', '1')
            self.set_field('Supernova Multipliers', '16')
            self.pump_until(lambda: self.panel.pending is not None)
            self.assertEqual(calls, [15])
            self.assertEqual(self.panel.fields['Supernova Multipliers'][0].get(), '16')
            self.assertEqual(self.panel.status.cget('text'), 'Calculating…')

            release_first.set()
            self.pump_until(second_started.is_set)
            self.assertEqual(self.metrics(), ('—',) * 4)
            self.assertEqual(self.panel.status.cget('text'), 'Calculating…')
            release_second.set()
            self.pump_until(lambda: self.metrics()[0] == '0%')
            self.assertEqual(calls, [15, 16])
            self.assertEqual(self.metrics(), ('0%', '0%', '~0', '~0'))

    def test_unlimited_estimates_and_unreachable_target(self):
        self.set_acceptance_inputs()
        self.set_field(relic_panel.BUDGET_LABEL, '199')
        self.pump_until(lambda: self.metrics()[0] == '50%')
        self.assertEqual(self.metrics(), ('50%', '0%', '~0', '~0'))

        self.set_field(relic_panel.BUDGET_LABEL, '0')
        self.pump_until(lambda: self.metrics()[1] == 'No budget limit')
        self.assertEqual(self.metrics(), ('50%', 'No budget limit', '~2', '~400'))
        self.assertEqual(self.panel.limit.cget('text'), '')

        self.set_field('Divine chests', '3')
        self.pump_until(lambda: self.panel.status.cget('text') == 'Target unreachable')
        self.assertEqual(self.metrics(), ('0%', 'No budget limit', '—', '—'))

    def test_rare_estimates_fit_at_minimum_width(self):
        self.app.deiconify()
        self.app.update()
        self.app.geometry('1080x560')
        self.set_field('Relic chests', '51')
        self.set_field('Supernova Multipliers', '10')
        self.pump_until(lambda: self.metrics()[2] == '~1.30e50')
        self.assertEqual(self.metrics(), ('<0.01%', 'No budget limit', '~1.30e50', '~2.60e52'))
        self.assertFalse(self.panel.status.winfo_manager())
        self.app.canvas.yview_moveto(1)
        self.app.update()
        self.assertEqual(self.app.canvas.yview()[1], 1.0)
        for index, metric in enumerate(self.panel.metrics.values()):
            x, _, width, _ = self.panel.metrics_frame.grid_bbox(index, 1)
            self.assertLessEqual(metric.winfo_x() + metric.winfo_width(), x + width)

    def test_relic_settings_restore_zero_blank_and_last_valid_field(self):
        self.set_field('Relic chests', '51')
        self.set_field('Supernova Multipliers', '10')
        self.set_field(relic_panel.BUDGET_LABEL, '400')
        self.set_field(relic_panel.BUDGET_LABEL, '0')
        self.set_field(relic_panel.BUDGET_LABEL, '')
        self.set_field('Effective divine cap', '')
        restored = relic_panel.RelicPanel(self.app.content, self.app)
        self.addCleanup(restored.destroy)
        values = {name: var.get() for name, (var, *_rest) in restored.fields.items()}
        self.assertEqual([values[name] for name in ('Relic chests', 'Supernova Multipliers',
                                                    relic_panel.BUDGET_LABEL, 'Effective divine cap')],
                         ['51', '10', '0', ''])
        self.assertFalse(restored.running)

    def test_budget_switch_rejects_in_flight_results_in_both_directions(self):
        self.set_field('Divine chests', '4')
        budget_label = relic_panel.BUDGET_LABEL
        for old, new, expected in ((400, 0, 'No budget limit'), (0, 400, '75%')):
            with self.subTest(old=old, new=new):
                started, released = Event(), Event()
                calls = []
                self.addCleanup(released.set)

                def delayed_estimate(minimums, regular, divine, budget, **kwargs):
                    calls.append(budget)
                    if budget == old:
                        started.set()
                        if not released.wait(5):
                            raise TimeoutError('Test failed to release the old estimate')
                    return estimate_rerolls(minimums, regular, divine, budget, **kwargs)

                with patch.object(relic_panel, 'estimate_rerolls', side_effect=delayed_estimate):
                    self.set_field(budget_label, str(old))
                    self.set_field('Supernova Multipliers', '1')
                    self.pump_until(started.is_set)
                    self.set_field(budget_label, str(new))
                    self.pump_until(lambda: self.panel.pending is not None)
                    self.assertEqual(self.metrics(), ('—',) * 4)
                    self.assertEqual(calls, [old])
                    with patch.object(self.panel, 'show_estimate', wraps=self.panel.show_estimate) as shown:
                        released.set()
                        self.pump_until(lambda: self.metrics()[1] == expected)
                        shown.assert_called_once()
                    self.assertEqual(calls, [old, new])
                    self.assertEqual(self.metrics(), ('50%', expected, '~2', '~400'))

    @patch.object(Companion, 'handoff_game')
    def test_fresh_wrong_screen_clears_previous_counts(self, handoff):
        self.set_field('Rainbow Floor Chance', '10')
        self.set_field('Supernova Multipliers', '9')
        valid = RelicReading(True, (10, 9, 8, 3, 0))
        with patch.object(relic_panel, 'capture_current_result', side_effect=[object(), object()]) as capture:
            with patch.object(relic_panel, 'read_image', side_effect=[valid, RelicReading(False)]):
                self.panel.read_button.invoke()
                self.pump_until(lambda: self.panel.read_poll_id is None)
                self.assertIn('All minimums met', self.panel.read_status.cget('text'))
                self.assertEqual([result.cget('text') for result in self.panel.count_labels],
                                 ['10', '9', '8', '3', '0'])
                self.panel.read_button.invoke()
                self.assertEqual([result.cget('text') for result in self.panel.count_labels],
                                 ['Unknown'] * 5)
                self.pump_until(lambda: self.panel.read_poll_id is None)
                self.assertIn('Result unknown', self.panel.read_status.cget('text'))
                self.assertEqual([result.cget('text') for result in self.panel.count_labels],
                                 ['Unknown'] * 5)
                self.assertEqual(capture.call_count, 2)
                self.assertEqual(handoff.call_count, 2)
                for focus, read in zip(handoff.call_args_list, capture.call_args_list, strict=True):
                    self.assertIs(focus.args[0], read.kwargs['desktop'])
