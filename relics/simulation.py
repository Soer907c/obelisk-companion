"""Offline reroll state machine. Returned actions are simulation events."""

from dataclasses import dataclass
from enum import Enum, auto
import re
from sqlite3 import Error as DatabaseError

from relics.calculator import DivineCounts, MAX_TARGET, REROLL_COST
from relics.payments import ChestCounts, PAYMENT_STORE_ERROR, PaymentStore
from relics.vision import DIVINE_NAMES, RelicReading


class Screen(Enum):
    OVERVIEW = auto()
    LOOT = auto()
    MENU = auto()
    CONFIRM = auto()
    PROCESSING = auto()
    UNKNOWN = auto()


class Action(Enum):
    SHOW_SUMMARY = auto()
    DISMISS_RESULT = auto()
    SHOW_PURCHASE = auto()
    ARM_PURCHASE = auto()
    CONFIRM_PURCHASE = auto()
    OPEN_ALL = auto()


class Phase(Enum):
    RECOVERY = auto()
    INITIAL_RESULT = auto()
    MENU = auto()
    CONFIRMATION = auto()
    PAYMENT = auto()
    OPENING = auto()
    PROCESSING = auto()
    STOPPED = auto()


@dataclass(frozen=True)
class Observation:
    frame_id: int
    screen: Screen
    reading: RelicReading | None = None
    gems: int | None = None
    unopened: ChestCounts | None = None
    refundable: ChestCounts | None = None
    price: int | None = None
    open_all_available: bool = False
    focused: bool = True
    f8: bool = False
    unopened_relic_display: str | None = None
    chest_text: tuple[str, ...] = ()
    purchase_controls_hidden: bool = False
    summary_available: bool = False


def chest_bounds(text: str) -> tuple[int, int] | None:
    if re.fullmatch(r'[0-9]+', text):
        value = int(text)
        return value, value
    match = re.fullmatch(r'([0-9]+)(?:\.([0-9]{1,2}))?([km])', text)
    if not match:
        return None
    whole, fraction, suffix = match.groups()
    scale = 1_000 if suffix == 'k' else 1_000_000
    fraction = fraction or ''
    unit = scale // 10 ** len(fraction)
    value = int(whole) * scale + int(fraction or 0) * unit
    # Allow either rounding to nearest or truncation at the displayed resolution.
    return max(0, value - unit // 2), value + unit - 1


class RerollSimulation:
    """Consume fresh observations and emit at most one fake click per observation."""

    def __init__(self, minimums: DivineCounts, budget: int, *, now: float,
                 transition_timeout: float = 8, opening_timeout: float = 60,
                 session_timeout: float = 3600, payments: PaymentStore | None = None):
        if len(minimums) != 5 or not any(minimums) or any(
            type(value) is not int or not 0 <= value <= MAX_TARGET for value in minimums
        ):
            raise ValueError('Set five minimums from 0 to 38, with at least one positive target.')
        if type(budget) is not int or budget < 0:
            raise ValueError('Budget must be a nonnegative whole number.')
        self.minimums = minimums
        self.budget = budget
        self.transition_timeout = transition_timeout
        self.opening_timeout = opening_timeout
        self.session_deadline = now + session_timeout if budget else float('inf')
        self.deadline = now + transition_timeout
        self.phase = Phase.INITIAL_RESULT
        self.stop_reason = None
        self.last_frame_id = -1
        self.spent = 0
        self.reserved = 0
        self.completed_rolls = 0
        self.latest_result = None
        self.balance_before = None
        self.expected_pool = None
        self.unopened_before = None
        self.refundable_before = None
        self.payments = payments if payments is not None else PaymentStore()
        self.pending_payment = None
        self.purchase_controls_requested = False
        self.summary_requested = False
        try:
            self.pending_payment = self.payments.pending()
        except (OSError, DatabaseError):
            self.stop(PAYMENT_STORE_ERROR)
        if self.pending_payment is not None:
            self.phase = Phase.RECOVERY

    def stop(self, reason: str) -> None:
        if self.phase is not Phase.STOPPED:
            if self.pending_payment is not None:
                reason = f'{reason} {self.pending_payment.blocked_message()}'
            self.stop_reason = reason
            self.phase = Phase.STOPPED

    def advance(self, phase: Phase, now: float, action: Action) -> Action:
        self.phase = phase
        wait = self.opening_timeout if phase is Phase.OPENING else self.transition_timeout
        self.deadline = now + wait
        return action

    def observe(self, frame: Observation, *, now: float) -> Action | None:
        if self.phase is Phase.STOPPED:
            return None
        if frame.f8:
            return self.stop('Stopped with F8.')
        if not frame.focused:
            return self.stop('Game lost focus.')
        if now >= self.session_deadline or now >= self.deadline:
            return self.stop(f'Timed out while waiting in {self.phase.name.lower()}.')
        if frame.frame_id <= self.last_frame_id:
            return None
        self.last_frame_id = frame.frame_id

        if self.phase is Phase.RECOVERY:
            return self.recover_payment(frame)
        if self.phase is Phase.INITIAL_RESULT and frame.screen in (Screen.OVERVIEW, Screen.LOOT):
            return self.check_result(frame, now)
        if self.phase is Phase.INITIAL_RESULT and frame.screen is Screen.MENU and frame.summary_available:
            if not self.summary_requested:
                self.summary_requested = True
                return Action.SHOW_SUMMARY
            return None
        if self.phase is Phase.MENU:
            if frame.screen in (Screen.OVERVIEW, Screen.LOOT):
                return None  # Dismissal is still in progress.
            if frame.screen is Screen.MENU:
                if frame.purchase_controls_hidden:
                    if not self.purchase_controls_requested:
                        self.purchase_controls_requested = True
                        return Action.SHOW_PURCHASE
                    return None
                return self.arm_purchase(frame, now)
        if self.phase is Phase.CONFIRMATION:
            if frame.screen is Screen.MENU:
                return None  # Wait for the orange button without clicking again.
            if frame.screen is Screen.CONFIRM:
                return self.confirm_purchase(frame, now)
        if self.phase is Phase.PAYMENT and frame.screen in (Screen.CONFIRM, Screen.MENU):
            return self.confirm_payment(frame, now)
        if self.phase in (Phase.OPENING, Phase.PROCESSING):
            if frame.screen is Screen.PROCESSING or self.observed_opening(frame):
                self.phase = Phase.PROCESSING
                return None
            if self.phase is Phase.OPENING and frame.screen is Screen.MENU:
                return None  # Open All has been sent, but processing has not appeared.
            if (self.phase is Phase.PROCESSING and frame.screen is Screen.MENU
                    and frame.gems == self.balance_before - REROLL_COST):
                # While opening, OCR can miss a changing chest counter. Await loot
                # within the existing deadline without issuing another click.
                return None
            if self.phase is Phase.PROCESSING and frame.screen is Screen.LOOT:
                if frame.reading is not None and frame.reading.recognized:
                    self.completed_rolls += 1
                return self.check_result(frame, now)
        return self.stop(f'Unexpected {frame.screen.name.lower()} screen during {self.phase.name.lower()}.')

    def check_result(self, frame: Observation, now: float) -> Action | None:
        self.latest_result = frame.reading
        reading = self.latest_result
        if reading is None or not reading.recognized:
            return self.stop('Current relic result is unreadable.')
        missing = [name for name, minimum, count in zip(
            DIVINE_NAMES, self.minimums, reading.counts, strict=True
        ) if minimum > 0 and count is None]
        if missing:
            return self.stop(f'Required divine count unreadable or missing: {", ".join(missing)}.')
        if reading.matches(self.minimums):
            return self.stop('All minimums met.')
        if self.budget and self.budget - self.spent - self.reserved < REROLL_COST:
            return self.stop('Remaining budget is below 200 gems.')
        if frame.gems is not None and frame.gems < REROLL_COST:
            return self.stop('Insufficient gems for another reroll.')
        self.purchase_controls_requested = False
        return self.advance(Phase.MENU, now, Action.DISMISS_RESULT)

    def arm_purchase(self, frame: Observation, now: float) -> Action | None:
        if frame.gems is None:
            return self.stop('Gem balance is unreadable.')
        if frame.gems < REROLL_COST or (
            self.budget and self.budget - self.spent - self.reserved < REROLL_COST
        ):
            return self.stop('Insufficient gems or remaining budget.')
        if frame.price != REROLL_COST:
            return self.stop('The 200-gem price is unconfirmed.')
        if frame.unopened is None or frame.refundable is None or not any(frame.refundable):
            details = ', '.join(value or 'Unknown' for value in frame.chest_text)
            return self.stop('The refundable chest pool is unconfirmed.'
                             + (f' OCR (refundable, then unopened): {details}.' if details else ''))
        self.balance_before = frame.gems
        self.unopened_before = frame.unopened
        self.refundable_before = frame.refundable
        self.expected_pool = tuple(a + b for a, b in zip(frame.unopened, frame.refundable, strict=True))
        self.reserved = REROLL_COST
        self.latest_result = None
        return self.advance(Phase.CONFIRMATION, now, Action.ARM_PURCHASE)

    def confirm_purchase(self, frame: Observation, now: float) -> Action | None:
        if frame.gems != self.balance_before or frame.price != REROLL_COST:
            return self.stop('Gem balance or price changed before confirmation.')
        if frame.unopened != self.unopened_before or frame.refundable != self.refundable_before:
            return self.stop('Chest inventory changed before confirmation.')
        try:
            self.pending_payment = self.payments.reserve(self.balance_before, self.expected_pool)
        except (OSError, DatabaseError):
            return self.stop(PAYMENT_STORE_ERROR)
        if self.pending_payment is None:
            return self.stop('Next purchase blocked: another session has an unresolved payment.')
        return self.advance(Phase.PAYMENT, now, Action.CONFIRM_PURCHASE)

    def returned_pool(self, frame: Observation, expected: ChestCounts) -> bool:
        if frame.screen is not Screen.MENU or frame.refundable != (0, 0, 0):
            return False
        if frame.unopened is not None:
            return frame.unopened == expected
        if not frame.open_all_available or self.unopened_before is None or len(frame.chest_text) != 6:
            return False
        increased = False
        for text, before, count in zip(frame.chest_text[3:], self.unopened_before, expected, strict=True):
            bounds = chest_bounds(text)
            if bounds is None or not bounds[0] <= count <= bounds[1]:
                return False
            increased |= bounds[0] > before
        return increased

    def observed_opening(self, frame: Observation) -> bool:
        if frame.screen is not Screen.MENU or frame.gems != self.balance_before - REROLL_COST:
            return False
        if len(frame.chest_text) != 6:
            return False
        return any(bounds[1] < count
                   for text, count in zip(frame.chest_text[3:], self.expected_pool, strict=True)
                   if (bounds := chest_bounds(text)) is not None)

    def settle_payment(self) -> bool:
        try:
            confirmed = self.payments.confirm(self.pending_payment)
        except (OSError, DatabaseError):
            self.stop(PAYMENT_STORE_ERROR)
            return False
        self.pending_payment = None
        if not confirmed:
            self.stop('Another session has already reconciled this payment.')
        return confirmed

    def recover_payment(self, frame: Observation) -> None:
        payment = self.pending_payment
        if frame.gems == payment.balance_after and self.returned_pool(frame, payment.expected_pool):
            if self.settle_payment():
                self.stop('Previous 200-gem payment confirmed. Start again from a fresh relic result.')

    def confirm_payment(self, frame: Observation, now: float) -> Action | None:
        if frame.gems is None:
            return None
        if frame.gems == self.balance_before and self.reserved:
            return None  # A delayed balance update never causes another click.
        if frame.gems != self.balance_before - REROLL_COST:
            return self.stop('The payment outcome is ambiguous.')
        if not self.returned_pool(frame, self.expected_pool):
            return None
        if self.pending_payment is not None:
            if not self.settle_payment():
                return None
            self.spent += self.reserved
            self.reserved = 0
        if frame.open_all_available:
            return self.advance(Phase.OPENING, now, Action.OPEN_ALL)
        return None
