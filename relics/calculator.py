"""Offline estimates for the owner-confirmed divine allocation rules."""

from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
from math import comb, expm1, floor, fsum, log1p, ulp

import bootstrap
import numpy as np


REROLL_COST = 200
MAX_TARGET = 38
UNLOCK_THRESHOLDS = (0, 3, 6, 10, 30)
REGULAR_DIVINE_DENOMINATOR = 25_000
_cancelled = ContextVar("allocation_cancelled", default=None)

DivineCounts = tuple[int, int, int, int, int]


@dataclass(frozen=True)
class RerollRules:
    effective_cap: int
    source: str = "Owner-confirmed game rules, with independent draws and rerolls"

    def __post_init__(self):
        if type(self.effective_cap) is not int or not 1 <= self.effective_cap <= MAX_TARGET:
            raise ValueError("Effective divine cap must be an integer from 1 to 38.")


@dataclass(frozen=True)
class RerollEstimate:
    max_paid_rerolls: int | None
    expected_divines: float
    odds_source: str | None
    initial_goal_met: bool | None
    per_reroll_chance: float | Fraction | None
    budget_chance: float | Fraction | None
    expected_paid_rerolls: float | Fraction | None
    expected_gems: float | Fraction | None


@dataclass(frozen=True)
class DisplayEstimate:
    rerolls: int
    gems: int
    approximate: bool = True


def display_estimate(expected_paid_rerolls: float | Fraction) -> DisplayEstimate:
    """Round ties up and use the same whole count for both display values."""
    if expected_paid_rerolls < 0:
        raise ValueError("Expected paid rerolls must be nonnegative.")
    whole = floor(expected_paid_rerolls)
    rerolls = whole + int(expected_paid_rerolls - whole >= 0.5)
    return DisplayEstimate(rerolls, rerolls * REROLL_COST)


def _nonnegative_integer(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer.")


def _validate_counts(counts: DivineCounts, name: str) -> None:
    if len(counts) != len(UNLOCK_THRESHOLDS):
        raise ValueError(f"{name} must contain five counts.")
    for count in counts:
        _nonnegative_integer(count, name)


def goal_met(counts: DivineCounts, minimums: DivineCounts) -> bool:
    return all(count >= minimum for count, minimum in zip(counts, minimums, strict=True))


def eligible_types(counts: DivineCounts, effective_cap: int) -> tuple[int, ...]:
    allocated = sum(counts)
    return (0,) + tuple(
        index for index, threshold in enumerate(UNLOCK_THRESHOLDS[1:], start=1)
        if allocated >= threshold and counts[index] < effective_cap
    )


def _binomial_weights(chests: int, pivot: int | None = None) -> tuple[int, list[float]]:
    """Return mode-relative weights and the offset of the first represented count."""
    # Ratios between adjacent masses avoid underflow from starting at zero draws.
    mode = (chests + 1) // REGULAR_DIVINE_DENOMINATOR if pivot is None else pivot
    odds_denominator = REGULAR_DIVINE_DENOMINATOR - 1
    lower = []
    weight = 1.0
    for k in range(mode if pivot is None else 0, 0, -1):
        weight *= k * odds_denominator / (chests - k + 1)
        if not weight:
            break
        lower.append(weight)
    upper = []
    weight = 1.0
    for k in range(mode, chests):
        weight *= (chests - k) / ((k + 1) * odds_denominator)
        if not weight:
            break
        upper.append(weight)
    return mode - len(lower), [*reversed(lower), 1.0, *upper]


def _binomial_tails(chests: int, minimum: int):
    offset, weights = _binomial_weights(chests)
    normalizer = fsum(weights)
    scale = None
    index = max(0, minimum - offset)
    mode = (chests + 1) // REGULAR_DIVINE_DENOMINATOR
    if minimum > mode and (index >= len(weights) or weights[index] < 1e-200):
        # Re-anchor an extreme upper tail at the smallest possible success.
        # Keep the absolute mass separate so the float DP sees normal-sized weights.
        offset, weights = _binomial_weights(chests, pivot=minimum)
        normalizer = 1.0
        with localcontext() as context:
            context.prec = 35
            p = Decimal(1) / REGULAR_DIVINE_DENOMINATOR
            scale = Fraction(comb(chests, minimum) * p ** minimum * (1 - p) ** (chests - minimum))
    tails = [0.0] * (len(weights) + 1)
    for index in range(len(weights) - 1, -1, -1):
        tails[index] = weights[index] + tails[index + 1]
    return offset, tails, normalizer, scale


@lru_cache(maxsize=32)
def _allocation_chance(
    minimums: DivineCounts, regular_chests: int, divine_chests: int, effective_cap: int
) -> float | Fraction:
    if any(minimum > effective_cap for minimum in minimums[1:]):
        return 0.0
    if not any(minimums[1:]):
        rival_draws = sum(min(effective_cap, max(0, divine_chests - unlock))
                          for unlock in UNLOCK_THRESHOLDS[1:])
        guaranteed_rainbow = max(min(divine_chests, UNLOCK_THRESHOLDS[1]),
                                 divine_chests - rival_draws)
        if guaranteed_rainbow >= minimums[0]:
            return 1.0
    # Before the draw that unlocks a type, all preceding draws went to earlier types.
    minimum_draws = max(
        threshold + sum(minimums[index:])
        for index, threshold in enumerate(UNLOCK_THRESHOLDS) if minimums[index]
    )
    if minimum_draws > regular_chests + divine_chests:
        return 0.0
    offset, tails, total_weight, scale = _binomial_tails(
        regular_chests, max(0, minimum_draws - divine_chests),
    )

    first_total = offset + divine_chests
    last_total = first_total + len(tails) - 2
    # Rainbow Floor Chance is the draw number minus the four capped counts.
    # A single capped target is absorbed as soon as it reaches its minimum.
    single_target = (next(i for i, n in enumerate(minimums[1:]) if n)
                     if not minimums[0] and sum(bool(n) for n in minimums) == 1 else None)
    caps = tuple(
        min(minimum - 1 if index == single_target else effective_cap,
            max(0, last_total - unlock))
        for index, (minimum, unlock) in enumerate(zip(minimums[1:], UNLOCK_THRESHOLDS[1:]))
    )
    shape = tuple(cap + 1 for cap in caps)
    states = np.zeros(shape)
    states[(0,) * 4] = 1.0
    next_states = np.zeros_like(states)
    grids = np.ogrid[tuple(slice(size) for size in shape)]
    capped_sum = sum(grids) if minimums[0] else None
    match = tuple(slice(minimum, None) for minimum in minimums[1:])
    reciprocal = None
    active = -1
    contributions = []
    probability = 0.0
    live = 1.0
    cancelled = _cancelled.get()
    for draw in range(1, last_total + 1):
        if cancelled is not None and cancelled():
            raise InterruptedError("Estimate superseded by a newer input.")
        tail = tails[max(0, draw - first_total)] / total_weight
        # All future hits must come from currently unmatched allocations and
        # require at least this many draws. Discard at most half an ulp.
        if not live or (probability > 0 and live * tail < ulp(probability) / 2):
            break
        unlocked = sum(draw > threshold for threshold in UNLOCK_THRESHOLDS[1:])
        if unlocked != active:
            active = unlocked
            reciprocal = 1.0 / (1 + sum(
                1 if cap < effective_cap else (grid < effective_cap).astype(np.uint8)
                for cap, grid in zip(caps[:active], grids[:active])
            ))
        previous = tuple(slice(min(size, max(1, draw - unlock)))
                         for size, unlock in zip(shape, UNLOCK_THRESHOLDS[1:]))
        current = tuple(slice(min(size, max(1, draw - unlock + 1)))
                        for size, unlock in zip(shape, UNLOCK_THRESHOLDS[1:]))
        states[previous] *= np.broadcast_to(reciprocal, shape)[previous]
        next_states[previous] = states[previous]  # Rainbow Floor Chance.
        for index in range(active):
            end = current[index].stop
            if end > previous[index].stop:
                face = current[:index] + (end - 1,) + current[index + 1:]
                next_states[face] = 0.0
        for index in range(active):
            end = current[index].stop
            src = previous[:index] + (slice(end - 1),) + previous[index + 1:]
            dst = previous[:index] + (slice(1, end),) + previous[index + 1:]
            next_states[dst] += states[src]
        if single_target is not None and draw >= minimum_draws:
            hit_face = previous[:single_target] + (-1,) + previous[single_target + 1:]
            hits = float(states[hit_face].sum())
        states, next_states = next_states, states
        if draw < minimum_draws:
            continue
        if single_target is None:
            matched = states[match]
            if capped_sum is None:
                hits = float(matched.sum())
                matched.fill(0)
            else:
                mask = capped_sum[match] <= draw - minimums[0]
                hits = float(matched.sum(where=mask))
                np.copyto(matched, 0.0, where=mask)
        if hits:
            contributions.append(hits * tail)
            probability = fsum(contributions)
            if draw % 8 == 0 or hits == 1.0:
                live = float(states[current].sum())
    if scale is not None:
        return scale * Fraction(probability)
    return min(1.0, probability)


def estimate_rerolls(
    minimums: DivineCounts,
    regular_chests: int,
    divine_chests: int,
    budget_gems: int,
    *,
    rules: RerollRules | None,
    initial_counts: DivineCounts | None = None,
    cancelled=None,
) -> RerollEstimate:
    """Estimate paid rerolls conditional on an initial miss if counts are unavailable."""
    _validate_counts(minimums, "Minimums")
    if not any(minimums) or max(minimums) > MAX_TARGET:
        raise ValueError("Set at least one positive minimum. Each minimum must be at most 38.")
    for value, name in ((regular_chests, "Regular chests"), (divine_chests, "Divine chests"),
                        (budget_gems, "Gem budget")):
        _nonnegative_integer(value, name)
    initial_match = None
    if initial_counts is not None:
        _validate_counts(initial_counts, "Initial allocation")
        initial_match = goal_met(initial_counts, minimums)

    has_chests = regular_chests + divine_chests > 0
    affordable = None if budget_gems == 0 else budget_gems // REROLL_COST if has_chests else 0
    expected_divines = divine_chests + regular_chests / REGULAR_DIVINE_DENOMINATOR
    source = rules.source if rules is not None else None
    chance = None
    if not has_chests:
        chance = 0.0
    elif rules is not None:
        token = _cancelled.set(cancelled)
        try:
            chance = _allocation_chance(tuple(minimums), regular_chests, divine_chests, rules.effective_cap)
        finally:
            _cancelled.reset(token)

    if affordable is None:
        budget_chance = None
        expected_rolls = 0.0 if initial_match else None
        if not initial_match and chance:
            # Keep the reciprocal finite even when it exceeds the range of a float.
            expected_rolls = 1 / Fraction(chance)
    elif initial_match:
        budget_chance, expected_rolls = 1.0, 0.0
    elif affordable == 0:
        budget_chance, expected_rolls = 0.0, 0.0
    elif chance is None:
        budget_chance, expected_rolls = None, None
    elif chance == 0:
        budget_chance, expected_rolls = 0.0, float(affordable)
    elif chance == 1:
        budget_chance, expected_rolls = 1.0, 1.0
    elif isinstance(chance, Fraction):
        # In this extreme tail, -log(1-p) differs from p by less than p
        # in relative terms. For x <= 1e-16, using x for
        # -expm1(-x) adds at most x/2 relative error and preserves tiny positives.
        exposure = affordable * chance
        budget_chance = (exposure if exposure <= 1e-16 else
                         -expm1(-float(exposure)) if exposure < 40 else 1.0)
        expected_rolls = min(Fraction(affordable), Fraction(budget_chance) / chance)
    else:
        budget_chance = -expm1(affordable * log1p(-chance))
        expected_rolls = min(float(affordable), budget_chance / chance)

    expected_gems = expected_rolls * REROLL_COST if expected_rolls is not None else None
    return RerollEstimate(
        affordable, expected_divines, source, initial_match, chance,
        budget_chance, expected_rolls, expected_gems,
    )
