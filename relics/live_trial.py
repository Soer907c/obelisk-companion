"""Run the guarded auto-reroll session with live or simulated dependencies."""

from dataclasses import dataclass
import ctypes
import os
from pathlib import Path
from time import monotonic, sleep

from core.windows import Desktop, Input, InputUnion, MouseInput, W, user32, wait_for_game_focus
from core.storage import RUNTIME
from obelisk.vision import normalize
from relics.replay import DispatchState, RerollReplay
from relics.replay_vision import ReplayVision
from relics.simulation import Action, Phase, RerollSimulation, Screen


LEGACY_TRIAL_LIMIT = Path(__file__).resolve().parents[1] / 'runtime' / 'relic-live-trial.used'
TRIAL_LIMIT = RUNTIME / 'relic-live-trial.used'
TWO_ROLL_LIMITS = tuple(RUNTIME / f'relic-two-roll-trial-{number}.used' for number in (1, 2))
VERIFICATION_LIMITS = tuple(RUNTIME / f'relic-verification-4000-{number:02}.used'
                            for number in range(1, 21))


def verification_budget_left():
    return 200 * sum(not slot.exists() for slot in VERIFICATION_LIMITS)


POSITIONS = {
    Action.SHOW_SUMMARY: (580, 175),
    Action.DISMISS_RESULT: (824, 640),
    Action.SHOW_PURCHASE: (738, 125),
    Action.ARM_PURCHASE: (1015, 230),
    Action.CONFIRM_PURCHASE: (1015, 230),
    Action.OPEN_ALL: (1005, 125),
}


class TrialClicks:
    def __init__(self, desktop, stopped, supervised=True, *, block_input=False):
        self.desktop = desktop
        self.stopped = stopped
        self.viewport = None
        self.actions = []
        self.supervised = supervised
        self.block_input = block_input
        self.blocked_payment_attempts = 0

    def record(self, action):
        if self.block_input:
            if action is Action.CONFIRM_PURCHASE:
                self.blocked_payment_attempts += 1
            raise RuntimeError(f'Verification blocked {action.name} before input dispatch.')
        if self.supervised and action is Action.CONFIRM_PURCHASE:
            if self.supervised == 20:
                for slot in VERIFICATION_LIMITS:
                    try:
                        self.claim_attempt(slot)
                        break
                    except FileExistsError:
                        continue
                else:
                    raise RuntimeError('The 4,000-gem verification allowance has been used.')
            elif self.supervised == 2:
                dispatched = self.actions.count(Action.CONFIRM_PURCHASE)
                if dispatched >= len(TWO_ROLL_LIMITS):
                    raise RuntimeError('Both supervised payment attempts have been used.')
                self.claim_fixed_attempt(TWO_ROLL_LIMITS[dispatched])
            else:
                if LEGACY_TRIAL_LIMIT.exists():
                    raise RuntimeError('The live trial payment authorization was already used.')
                self.claim_fixed_attempt(TRIAL_LIMIT)
        if self.stopped() or self.desktop.abort.is_set() or not self.desktop.foreground():
            raise RuntimeError('The trial stopped before click dispatch.')
        x, y = self.desktop.point(POSITIONS[action], self.viewport)
        ctypes.set_last_error(0)
        if not user32.SetCursorPos(x, y):
            cursor_error = ctypes.get_last_error()
            left, top, width, height = (user32.GetSystemMetrics(index) for index in (76, 77, 78, 79))
            move = MouseInput((x-left)*65536//width + 65536//(2*width),
                              (y-top)*65536//height + 65536//(2*height), 0, 0xC001, 0, 0)
            event = Input(type=0, u=InputUnion(mi=move))
            if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
                raise RuntimeError(f'Windows rejected movement to {(x, y)}. '
                                   f'SetCursorPos error {cursor_error}, SendInput error {ctypes.get_last_error()}.')
        else:
            cursor_error = 0
        sleep(.12)
        if self.stopped() or not self.desktop.foreground():
            raise RuntimeError('The trial stopped before mouse down.')
        point = W.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)) or (point.x, point.y) != (x, y):
            raise RuntimeError(f'Cursor target {(x, y)}, observed {(point.x, point.y)}. '
                               f'SetCursorPos error {cursor_error}.')
        self.desktop.point(POSITIONS[action], self.viewport)
        if self.stopped():
            raise RuntimeError('The session stopped before mouse down.')
        self.actions.append(action)
        self.desktop.click_current()

    @staticmethod
    def claim_attempt(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as record:
            record.write('One payment dispatch authorized for the supervised trial.\n')
            record.flush()
            os.fsync(record.fileno())

    @staticmethod
    def claim_fixed_attempt(path):
        try:
            TrialClicks.claim_attempt(path)
        except FileExistsError as error:
            raise RuntimeError('The live trial payment authorization was already used.') from error


@dataclass(frozen=True)
class Progress:
    message: str
    completed: int
    spent: int
    remaining: int | None
    reading: object
    balance: int | None
    blocked_payment_attempts: int | None = None


def run_auto_reroll(minimums, budget, abort, report, *, supervised=False, desktop=None,
                    hotkey=None, clicks=None, recognize=None, payments=None, pause=.25,
                    game_prepared=False, verify_handoff='', block_input=False):
    desktop = desktop if desktop is not None else Desktop()
    desktop.abort = abort
    stopped = lambda: abort.is_set() or (hotkey is not None and hotkey.pressed())
    if clicks is None or block_input:
        clicks = TrialClicks(desktop, stopped, supervised, block_input=block_input)
    sim = None
    capture = None
    reading = balance = None

    def update(message):
        spent = sim.spent if sim else 0
        report(Progress(message, sim.completed_rolls if sim else 0, spent,
                        budget - spent if budget else None, reading, balance,
                        clicks.blocked_payment_attempts if block_input else None))

    try:
        if stopped():
            return
        if not game_prepared:
            desktop.find()
            desktop.focus()
        if not wait_for_game_focus(desktop, stopped):
            update('Stopped during game handoff.')
            return
        recognize = recognize if recognize is not None else ReplayVision()
        if verify_handoff:
            image = desktop.capture()
            observation = recognize(image)
            balance, reading = observation.get('gems'), observation.get('reading')
            screen = observation['screen']
            update(f'Verified foreground {desktop.hwnd}, screen {screen.name}.')
            if verify_handoff == 'dismiss':
                image = desktop.capture()
                if recognize(image)['screen'] != Screen.OVERVIEW:
                    raise RuntimeError('A recognized Summary is required before dismissal.')
                _, clicks.viewport = normalize(image)
                clicks.record(Action.DISMISS_RESULT)
                deadline = monotonic() + 3
                while not stopped() and desktop.foreground() and monotonic() < deadline:
                    if recognize(desktop.capture())['screen'] == Screen.MENU:
                        update('Summary dismissal verified on a fresh Relics menu capture.')
                        return
                    abort.wait(.1)
                raise RuntimeError('Summary dismissal was not verified.')
            while not stopped() and desktop.foreground():
                abort.wait(.05)
            update('Stopped after foreground verification.')
            return
        if supervised == 20 and not 200 <= budget <= verification_budget_left():
            raise RuntimeError('Session budget exceeds the remaining verification allowance.')
        trial_limits = TWO_ROLL_LIMITS if supervised == 2 else (TRIAL_LIMIT, LEGACY_TRIAL_LIMIT)
        if supervised in (1, 2) and budget != 200 * int(supervised):
            raise RuntimeError(f'The supervised trial requires a {200 * int(supervised)}-gem budget.')
        if supervised in (2, 20) and minimums != (0, 10, 0, 0, 0):
            raise RuntimeError('The trial requires Supernova 10 and all other minimums zero.')
        sim = RerollSimulation(minimums, budget, now=monotonic(), transition_timeout=14,
                               payments=payments)
        if (supervised in (1, 2) and any(path.exists() for path in trial_limits)) or sim.phase in (Phase.STOPPED, Phase.RECOVERY):
            raise RuntimeError(sim.stop_reason or (
                sim.pending_payment.blocked_message() if sim.pending_payment else
                'The live trial payment authorization was already used.'
            ))
        replay = RerollReplay(
            sim, desktop.hwnd, recognize,
            lambda: DispatchState(replay.session if not abort.is_set() else None,
                                  desktop.hwnd if desktop.foreground() else None, stopped()),
            clicks,
        )
        first_capture = True
        while sim.phase is not Phase.STOPPED:
            if stopped() or not desktop.foreground():
                raise RuntimeError('The session stopped on F8, Stop, or focus loss.')
            capture = replay.capture(desktop.capture)
            result = replay.read(capture)
            prior_completed = sim.completed_rolls
            replay.observe(result, now=monotonic())
            if supervised and sim.completed_rolls > prior_completed:
                save_trial_frame(capture.image, f'relic-trial-loot-{sim.completed_rolls}.png')
            if result.observation.gems is not None:
                balance = result.observation.gems
            if sim.latest_result is not None:
                reading = sim.latest_result
            if first_capture:
                update(f'Initial game screen: {result.observation.screen.name}.')
                first_capture = False
            if replay.pending_action is not None:
                action = replay.pending_action
                if isinstance(clicks, TrialClicks):
                    _, clicks.viewport = normalize(capture.image)
                sent = replay.dispatch()
                if supervised and sent:
                    update(f'Dispatched {action.name}.')
                    if action in (Action.CONFIRM_PURCHASE, Action.OPEN_ALL):
                        save_trial_frame(capture.image, f'relic-trial-{action.name.lower()}-{sim.completed_rolls + 1}.png')
            update(sim.stop_reason or sim.phase.name.replace('_', ' ').capitalize())
            abort.wait(pause)
    except Exception as error:
        if sim:
            sim.stop(str(error))
        update(sim.stop_reason if sim else str(error))
    finally:
        try:
            if supervised and capture is not None:
                save_trial_frame(capture.image, 'latest-relic-trial.png')
            if block_input and capture is not None:
                save_trial_frame(capture.image, 'latest-relic-goal-verification.png')
            desktop.release()
        finally:
            if hotkey is not None:
                hotkey.close()
            report(None)


def save_trial_frame(image, name):
    try:
        image.save(RUNTIME / name)
    except OSError:
        pass
