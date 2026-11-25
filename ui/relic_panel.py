"""Relic reroll controls, results and estimates."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from queue import Empty, SimpleQueue
from sqlite3 import Error as DatabaseError
from threading import Event, Thread
from tkinter import StringVar

import customtkinter as ctk
from core.storage import load_settings, save_settings
from core.windows import Desktop

from relics.calculator import (
    MAX_TARGET, REROLL_COST, DivineCounts, RerollRules, display_estimate, estimate_rerolls,
)
from relics.capture import capture_current_result
from relics.live_trial import run_auto_reroll
from relics.payments import PAYMENT_STORE_ERROR, PaymentStore
from relics.vision import DIVINE_NAMES as TARGET_NAMES, UNKNOWN_COUNTS, read_image
from .theme import ACCENT, BG, EDGE, MUTED, PANEL, label, session_buttons


ESTIMATE_NAMES = ('Chance per reroll', 'Chance within budget', 'Estimated paid rerolls', 'Estimated gems')
BUDGET_LABEL = 'Gem budget (0 = no limit)'
DEBOUNCE_MS = 120
ESTIMATE_POLL_MS = 16
POLL_MS = 40


@dataclass(frozen=True)
class EstimateInputs:
    minimums: DivineCounts
    regular_chests: int
    divine_chests: int
    budget: int
    rules: RerollRules | None

    @property
    def odds_key(self):
        return self.minimums, self.regular_chests, self.divine_chests, self.rules


def calculate(results, revision, inputs, abort):
    try:
        result = estimate_rerolls(
            inputs.minimums, inputs.regular_chests, inputs.divine_chests,
            inputs.budget, rules=inputs.rules, cancelled=abort.is_set,
        )
    except Exception as error:
        results.put((revision, None, str(error)))
    else:
        results.put((revision, result, None))


def read_current_result(results, desktop):
    try:
        frame = capture_current_result(desktop=desktop)
        captured_at = datetime.now().strftime('%H:%M:%S')
        reading = read_image(frame)
    except Exception as error:
        results.put((None, '', str(error)))
    else:
        results.put((reading, captured_at, None))


def format_chance(chance):
    if chance is None:
        return 'Unknown'
    if 0 < chance < 0.0001:
        return '<0.01%'
    if 0.9999 < chance < 1:
        return '>99.99%'
    return f'{chance * 100:.2f}'.rstrip('0').rstrip('.') + '%'


def format_estimate(value):
    if value < 1_000_000_000:
        return f'~{value:,}'
    mantissa, exponent = format(Decimal(value), '.2E').split('E')
    return f'~{mantissa}e{int(exponent)}'


def parse_field(name, raw, minimum, maximum):
    raw = raw.strip()
    if not raw and name == 'Effective divine cap':
        return None, True
    try:
        number = int(raw) if raw.isascii() and raw.isdecimal() else None
    except ValueError:
        number = None
    valid = number is not None and number >= minimum and (maximum is None or number <= maximum)
    return number, valid


class RelicPanel(ctk.CTkFrame):
    running = False  # Estimates do not acquire the shell's game input lock.

    def __init__(self, parent, app):
        super().__init__(parent, fg_color='transparent')
        self.app = app
        self.fields = {}
        self.metrics = {}
        self.revision = 0
        self.pending = None
        self.debounce_id = None
        self.poll_id = None
        self.worker_busy = False
        self.worker_abort = Event()
        self.worker_key = None
        self.results = SimpleQueue()
        self.read_results = SimpleQueue()
        self.read_poll_id = None
        self.current_reading = None
        self.read_time = ''
        self.read_error = None
        self.trial_abort = Event()
        self.trial_messages = SimpleQueue()
        self.saved = load_settings('relics')
        self.build()
        for name, (value, _, minimum, maximum) in self.fields.items():
            if name in self.saved:
                stored = self.saved[name]
                raw = '' if stored is None else str(stored)
                if parse_field(name, raw, minimum, maximum)[1]:
                    value.set(raw)
            value.trace_add('write', lambda *_, field=name: self.field_changed(field))
        self.input_changed()

    def field_changed(self, name):
        value, _, minimum, maximum = self.fields[name]
        number, valid = parse_field(name, value.get(), minimum, maximum)
        if valid and (name not in self.saved or self.saved[name] != number):
            updated = dict(self.saved)
            updated[name] = number
            try:
                save_settings('relics', updated)
            except OSError:
                pass
            else:
                self.saved = updated
        self.input_changed(name)

    def stop(self):
        self.trial_abort.set()

    def prepare_two_roll_trial(self):
        self.fields[TARGET_NAMES[1]][0].set('10')
        self.fields[BUDGET_LABEL][0].set('400')
        self.trial_status.configure(text='Supervised verification. The session budget applies.')

    def prepare_goal_verification(self):
        self.fields[TARGET_NAMES[1]][0].set('10')
        self.fields[BUDGET_LABEL][0].set('200')
        self.trial_status.configure(text='Goal verification. All game clicks are blocked.')

    def destroy(self):
        self.worker_abort.set()
        for token in (self.debounce_id, self.poll_id, self.read_poll_id):
            if token is not None:
                self.after_cancel(token)
        super().destroy()

    def build(self):
        self.grid_columnconfigure((0, 1), weight=1, uniform='relics')
        label(self, 'Relic rerolls', 30, bold=True).grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(2, 14))

        targets = self.card(2, 0, 'Divine minimums', padx=(0, 8))
        label(targets, '0 ignores that divine. Meet every positive minimum in the same reroll.',
              12, MUTED, wraplength=310, justify='left').pack(anchor='w', pady=(0, 8))
        for name in TARGET_NAMES:
            self.field(targets, name, '0', maximum=MAX_TARGET, stepper=True)

        inventory = self.card(2, 1, 'Chest pool and budget', padx=(8, 0))
        self.field(inventory, 'Relic chests', '0')
        self.field(inventory, 'Divine chests', '0')
        self.field(inventory, BUDGET_LABEL, '0')
        self.field(inventory, 'Effective divine cap', str(MAX_TARGET), minimum=1, maximum=MAX_TARGET)
        label(inventory, 'Use your account cap. Leave blank if unknown.', 12, MUTED,
              wraplength=310, justify='left').pack(anchor='w', pady=(7, 0))
        self.payment_status = label(inventory, '', 12, ACCENT, wraplength=310, justify='left')
        self.refresh_payment_status()

        estimates = self.card(3, 0, 'Reroll estimates', columnspan=2)
        self.status = label(estimates, '', color=ACCENT, wraplength=680, justify='left')
        self.status.pack(anchor='w', pady=(0, 12))
        summary = ctk.CTkFrame(estimates, fg_color='transparent')
        self.metrics_frame = summary
        summary.pack(fill='x')
        for column, name in enumerate(ESTIMATE_NAMES):
            summary.grid_columnconfigure(column, weight=1, uniform='estimate')
            label(summary, name, 12, MUTED).grid(row=0, column=column, sticky='w')
            self.metrics[name] = label(summary, '—', 23, bold=True)
            self.metrics[name].grid(row=1, column=column, sticky='w')
        self.limit = label(estimates, '', 12, MUTED)
        self.limit.pack(anchor='w', pady=(12, 0))
        session = self.card(1, 1, 'Session', padx=(8, 0))
        self.trial_button, self.read_button, self.stop_button = session_buttons(
            session, 'Start auto-reroll', self.start, 'Read current result', self.start_read, self.stop,
        )
        self.trial_status = label(session, 'Ready. No automation is running.',
                                  12, MUTED, wraplength=300, justify='left')
        self.trial_status.pack(anchor='w', padx=20)
        result = self.card(1, 0, 'Current relic result', padx=(0, 8))
        counts = ctk.CTkFrame(result, fg_color='transparent')
        counts.pack(fill='x')
        self.count_labels = []
        counts.grid_columnconfigure(0, weight=1)
        for index, name in enumerate(TARGET_NAMES):
            label(counts, name, 11, MUTED).grid(row=index, column=0, sticky='w')
            count = label(counts, 'Unknown', 14, bold=True)
            count.grid(row=index, column=1, sticky='e')
            self.count_labels.append(count)
        self.read_status = label(result, 'Open the relic overview in the game before reading.',
                                 12, MUTED, wraplength=320, justify='left')
        self.read_status.pack(anchor='w', pady=(4, 0))

    def start(self):
        if self.running or self.read_poll_id is not None:
            self.trial_status.configure(text='Wait for the current relic operation to finish.')
            return
        verify_handoff = self.app.verify_handoff
        minimums = []
        for name in TARGET_NAMES:
            number, valid = parse_field(name, self.fields[name][0].get(), 0, MAX_TARGET)
            if not valid:
                self.trial_status.configure(text=f'{name} requires a whole number from 0 to {MAX_TARGET}.')
                return
            minimums.append(number)
        if not verify_handoff and not any(minimums):
            self.trial_status.configure(text='Set at least one positive divine minimum.')
            return
        budget, valid = parse_field(BUDGET_LABEL, self.fields[BUDGET_LABEL][0].get(), 0, None)
        if not valid:
            self.trial_status.configure(text='Enter a valid gem budget. Use 0 for no limit.')
            return
        if not verify_handoff and 0 < budget < REROLL_COST:
            self.trial_status.configure(text='Use 0 for no limit or at least 200 gems.')
            return
        desktop = Desktop()
        try:
            self.app.handoff_game(desktop)
        except RuntimeError as error:
            self.trial_status.configure(text=str(error))
            return
        self.running = True
        self.trial_abort.clear()
        self.trial_button.configure(state='disabled')
        self.read_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        supervised = self.app.supervised_relic_trial
        Thread(target=run_auto_reroll,
               args=(tuple(minimums), budget, self.trial_abort, self.trial_messages.put),
               kwargs={'supervised': supervised, 'desktop': desktop, 'game_prepared': True,
                       'verify_handoff': verify_handoff, 'block_input': self.app.verify_relic_start},
               daemon=True).start()
        self.after(POLL_MS, self.poll_trial)

    def poll_trial(self):
        while True:
            try:
                message = self.trial_messages.get_nowait()
            except Empty:
                break
            if message is None:
                self.running = False
                self.trial_button.configure(state='normal')
                self.read_button.configure(state='normal')
                self.stop_button.configure(state='disabled')
                self.refresh_payment_status()
                self.app.deiconify()
                return
            if self.app.verify_handoff or self.app.supervised_relic_trial or self.app.verify_relic_start:
                self.app.record_handoff('Relics verification', message=message.message,
                                        spent=message.spent, balance=message.balance,
                                        completed=message.completed, remaining=message.remaining,
                                        counts=message.reading.counts if message.reading else None,
                                        blocked_payment_attempts=message.blocked_payment_attempts)
            balance = f'{message.balance:,}' if message.balance is not None else 'Unknown'
            remaining = f'{message.remaining:,}' if message.remaining is not None else 'No limit'
            status = (f'{message.message}  Rerolls {message.completed}  ·  Spent {message.spent:,} gems'
                      f'  ·  Budget left {remaining}  ·  Balance {balance}')
            if message.blocked_payment_attempts is not None:
                status += f'  ·  Payment attempts {message.blocked_payment_attempts}'
            self.trial_status.configure(text=status)
            if message.reading is not None:
                self.current_reading = message.reading
                self.read_time = datetime.now().strftime('%H:%M:%S')
                self.read_error = None
                self.render_reading()
            print(status, flush=True)
        self.after(POLL_MS, self.poll_trial)

    def refresh_payment_status(self):
        try:
            pending = PaymentStore().pending()
            message = pending.blocked_message() if pending else ''
        except (OSError, DatabaseError):
            message = PAYMENT_STORE_ERROR
        self.payment_status.configure(text=message)
        if message:
            self.payment_status.pack(anchor='w', pady=(6, 0))
        else:
            self.payment_status.pack_forget()

    def start_read(self):
        if self.read_poll_id is not None:
            return
        self.refresh_payment_status()
        self.current_reading = None
        self.read_time = ''
        self.read_error = None
        self.render_reading()
        desktop = Desktop()
        try:
            self.app.handoff_game(desktop)
        except RuntimeError as error:
            self.read_error = str(error)
            self.render_reading()
            return
        self.read_button.configure(state='disabled')
        self.read_status.configure(text='Reading the current game screen.')
        Thread(target=read_current_result, args=(self.read_results, desktop), daemon=True).start()
        self.read_poll_id = self.after(POLL_MS, self.poll_reading)

    def poll_reading(self):
        try:
            self.current_reading, self.read_time, self.read_error = self.read_results.get_nowait()
        except Empty:
            self.read_poll_id = self.after(POLL_MS, self.poll_reading)
            return
        self.read_poll_id = None
        self.read_button.configure(state='normal')
        self.render_reading()
        self.app.deiconify()

    def render_reading(self):
        reading = self.current_reading
        counts = reading.counts if reading is not None else UNKNOWN_COUNTS
        for widget, count in zip(self.count_labels, counts, strict=True):
            widget.configure(text=f'{count:,}' if count is not None else 'Unknown')
        if self.read_error:
            message = f'Result unknown. {self.read_error}'
        elif reading is None:
            message = 'Open the relic overview in the game before reading.'
        elif not reading.recognized:
            message = 'Result unknown. The captured screen is not a recognized relic overview.'
        else:
            raw = [self.fields[name][0].get().strip() for name in TARGET_NAMES]
            try:
                minimums = tuple(int(value) for value in raw)
            except ValueError:
                minimums = ()
            valid = (len(minimums) == len(TARGET_NAMES)
                     and all(value.isascii() and value.isdecimal() for value in raw)
                     and all(0 <= value <= MAX_TARGET for value in minimums))
            match = reading.matches(minimums) if valid else None
            if match is None:
                detail = ('Goal unknown. Set at least one valid minimum.' if not valid or not any(minimums)
                          else 'Result unknown. A required count is unreadable.')
            else:
                detail = 'All minimums met.' if match else 'At least one minimum is unmet.'
                if None in counts:
                    detail += ' Some unselected counts are unreadable.'
            message = f'Captured at {self.read_time}. {detail}'
        self.read_status.configure(text=message)

    def card(self, row, column, title, padx=0, columnspan=1):
        border = ctk.CTkFrame(self, fg_color=PANEL, border_color=EDGE, border_width=1)
        border.grid(row=row, column=column, columnspan=columnspan, sticky='new', padx=padx, pady=(0, 14))
        card = ctk.CTkFrame(border, fg_color='transparent')
        card.pack(fill='both', padx=18, pady=14)
        label(card, title, 17, bold=True).pack(anchor='w', pady=(0, 6))
        return card

    def field(self, parent, name, default, minimum=0, maximum=None, stepper=False):
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', pady=3)
        label(row, name).pack(side='left')
        value = StringVar(master=self, value=default)
        if stepper:
            ctk.CTkButton(row, text='+', width=28, height=28,
                          command=lambda: self.step(value, 1)).pack(side='right', padx=(4, 0))
        entry = ctk.CTkEntry(row, textvariable=value, width=54 if stepper else 118,
                             height=28, fg_color=BG, border_color=EDGE)
        entry.pack(side='right')
        if stepper:
            ctk.CTkButton(row, text='−', width=28, height=28,
                          command=lambda: self.step(value, -1)).pack(side='right', padx=(4, 4))
        self.fields[name] = (value, entry, minimum, maximum)

    def step(self, value, delta):
        try:
            number = int(value.get())
        except ValueError:
            number = 0
        value.set(str(max(0, min(MAX_TARGET, number + delta))))

    def read_fields(self):
        values = {}
        errors = []
        for name, (value, entry, minimum, maximum) in self.fields.items():
            number, valid = parse_field(name, value.get(), minimum, maximum)
            entry.configure(border_color=EDGE if valid else '#ff9494')
            if not valid:
                requirement = f'{minimum}–{maximum}' if maximum is not None else 'a whole number of 0 or more'
                errors.append(f'{name} requires {requirement}.')
            values[name] = number
        return values, errors

    def input_changed(self, field=None):
        if self.current_reading is not None:
            self.render_reading()
        self.revision += 1
        self.pending = None
        if self.debounce_id is not None:
            self.after_cancel(self.debounce_id)
            self.debounce_id = None
        for metric in self.metrics.values():
            metric.configure(text='—')
        self.status.pack(before=self.metrics_frame, anchor='w', pady=(0, 12))
        self.limit.configure(text='')
        self.limit.pack_forget()
        values, errors = self.read_fields()
        if errors:
            if field != BUDGET_LABEL:
                self.worker_abort.set()
            self.status.configure(text=errors[0])
            return
        minimums = tuple(values[name] for name in TARGET_NAMES)
        if not any(minimums):
            self.worker_abort.set()
            self.status.configure(text='Set at least one positive minimum.')
            return
        cap = values['Effective divine cap']
        inputs = EstimateInputs(
            minimums, values['Relic chests'], values['Divine chests'], values[BUDGET_LABEL],
            RerollRules(cap) if cap is not None else None,
        )
        if inputs.odds_key != self.worker_key:
            self.worker_abort.set()
        self.status.configure(text='Calculating…')
        self.debounce_id = self.after(DEBOUNCE_MS, self.set_pending, self.revision, inputs)

    def set_pending(self, revision, inputs):
        self.debounce_id = None
        self.pending = (revision, inputs)
        self.start_pending()

    def start_pending(self):
        if self.worker_busy or self.pending is None:
            return
        revision, inputs = self.pending
        self.pending = None
        self.worker_busy = True
        self.worker_key = inputs.odds_key
        self.worker_abort = Event()
        Thread(target=calculate, args=(self.results, revision, inputs, self.worker_abort), daemon=True).start()
        self.poll_id = self.after(ESTIMATE_POLL_MS, self.poll_result)

    def poll_result(self):
        self.poll_id = None
        try:
            revision, result, error = self.results.get_nowait()
        except Empty:
            self.poll_id = self.after(ESTIMATE_POLL_MS, self.poll_result)
            return
        self.worker_busy = False
        if revision == self.revision:
            if error is None:
                self.show_estimate(result)
            else:
                self.status.configure(text=f'Calculation failed. {error}')
        self.start_pending()

    def show_estimate(self, result):
        display = (display_estimate(result.expected_paid_rerolls)
                   if result.expected_paid_rerolls is not None else None)
        unlimited = result.max_paid_rerolls is None
        unreachable = result.per_reroll_chance == 0 and not result.initial_goal_met
        missing_estimate = '—' if unreachable else 'Unknown'
        values = (
            format_chance(result.per_reroll_chance),
            'No budget limit' if unlimited else format_chance(result.budget_chance),
            format_estimate(display.rerolls) if display else missing_estimate,
            format_estimate(display.gems) if display else missing_estimate,
        )
        for name, value in zip(ESTIMATE_NAMES, values, strict=True):
            self.metrics[name].configure(text=value)
        if unlimited:
            self.limit.configure(text='')
            self.limit.pack_forget()
        else:
            maximum_gems = result.max_paid_rerolls * REROLL_COST
            self.limit.configure(text=f'Maximum charge  {maximum_gems:,} gems across '
                                      f'{result.max_paid_rerolls:,} paid rerolls')
            self.limit.pack(anchor='w', pady=(12, 0))
        if result.initial_goal_met:
            self.status.configure(text='Current allocation meets the target.')
        elif unreachable:
            self.status.configure(text='Target unreachable')
        else:
            self.status.configure(text='')
            self.status.pack_forget()
