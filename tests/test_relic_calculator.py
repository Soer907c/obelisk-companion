import itertools
import unittest
from fractions import Fraction
from math import comb, isclose
from unittest.mock import patch

from relics.calculator import (
    RerollRules, _allocation_chance, display_estimate, eligible_types, estimate_rerolls, goal_met,
)


class RelicCalculatorTests(unittest.TestCase):
    empty = (0, 0, 0, 0, 0)
    supernova = (0, 1, 0, 0, 0)

    def estimate(self, minimums=None, regular=0, divine=4, budget=400, cap=38, initial=empty):
        return estimate_rerolls(
            self.supernova if minimums is None else minimums, regular, divine, budget,
            rules=RerollRules(cap), initial_counts=initial,
        )

    def test_supernova_acceptance_example(self):
        result = self.estimate()
        self.assertEqual(result.max_paid_rerolls, 2)
        self.assertEqual(result.per_reroll_chance, 0.5)
        self.assertEqual(result.budget_chance, 0.75)
        self.assertEqual(result.expected_paid_rerolls, 1.5)
        self.assertEqual(result.expected_gems, 300)
        display = display_estimate(result.expected_paid_rerolls)
        self.assertEqual((display.rerolls, display.gems), (2, 400))
        self.assertTrue(display.approximate)
        self.assertEqual(result.budget_chance, 0.75)

    def test_budget_boundaries(self):
        for budget, rolls, chance in ((1, 0, 0), (199, 0, 0), (200, 1, 0.5), (399, 1, 0.5)):
            with self.subTest(budget=budget):
                result = self.estimate(budget=budget)
                self.assertEqual(result.max_paid_rerolls, rolls)
                self.assertEqual(result.budget_chance, chance)
                self.assertEqual(result.expected_gems, 200 * rolls)

    def test_zero_budget_estimates_until_success(self):
        result = self.estimate(budget=0)
        self.assertEqual(result.per_reroll_chance, 0.5)
        self.assertIsNone(result.max_paid_rerolls)
        self.assertIsNone(result.budget_chance)
        self.assertEqual(result.expected_paid_rerolls, 2)
        self.assertEqual(result.expected_gems, 400)
        self.assertEqual(display_estimate(result.expected_paid_rerolls).gems, 400)

    def test_unlimited_impossible_and_initial_match(self):
        impossible = self.estimate(divine=3, budget=0)
        self.assertEqual(impossible.per_reroll_chance, 0)
        self.assertIsNone(impossible.expected_paid_rerolls)
        self.assertIsNone(impossible.expected_gems)
        matched = self.estimate(divine=3, budget=0, initial=(3, 1, 0, 0, 0))
        self.assertTrue(matched.initial_goal_met)
        self.assertEqual(matched.expected_paid_rerolls, 0)
        self.assertEqual(matched.expected_gems, 0)

    def test_unlimited_very_rare_goal_uses_finite_reciprocal(self):
        chance = 1e-310
        with patch('relics.calculator._allocation_chance', return_value=chance):
            result = self.estimate(budget=0)
        self.assertEqual(result.expected_paid_rerolls * Fraction(chance), 1)
        display = display_estimate(result.expected_paid_rerolls)
        self.assertEqual(display.gems, display.rerolls * 200)

    def test_array_odds_agree_with_labelled_exact_allocations(self):
        targets = ((0, 2, 0, 0, 0), (24, 1, 0, 2, 1), (0, 2, 2, 2, 2))
        cap, regular, guaranteed = 2, 6, 29
        states = {self.empty: Fraction(1)}
        expected = [Fraction(0) for target in targets]
        for draws in range(guaranteed + regular + 1):
            if draws >= guaranteed:
                k = draws - guaranteed
                weight = Fraction(comb(regular, k) * 24999 ** (regular - k), 25000 ** regular)
                for index, target in enumerate(targets):
                    expected[index] += weight * sum(
                        mass for counts, mass in states.items() if goal_met(counts, target)
                    )
            advanced = {}
            for counts, mass in states.items():
                eligible = eligible_types(counts, cap)
                share = mass / len(eligible)
                for index in eligible:
                    updated = counts[:index] + (counts[index] + 1,) + counts[index + 1:]
                    advanced[updated] = advanced.get(updated, 0) + share
            states = advanced
        for target, chance in zip(targets, expected, strict=True):
            with self.subTest(target=target):
                result = self.estimate(target, regular=regular, divine=guaranteed, cap=cap)
                self.assertTrue(isclose(result.per_reroll_chance, float(chance), rel_tol=1e-13))

    def test_measured_and_rare_reference_odds(self):
        result = self.estimate((0, 15, 0, 0, 0), regular=511_058, divine=3)
        self.assertTrue(isclose(result.per_reroll_chance, 0.0008865189894515919, rel_tol=1e-13))
        result = self.estimate((0, 10, 0, 0, 0), regular=51, divine=0, budget=0)
        self.assertTrue(isclose(result.expected_paid_rerolls, 1.30e50, rel_tol=0.005))

    def test_budget_edit_reuses_per_roll_odds(self):
        before = self.estimate(regular=12_345, budget=0)
        hits = _allocation_chance.cache_info().hits
        after = self.estimate(regular=12_345, budget=400)
        self.assertEqual(_allocation_chance.cache_info().hits, hits + 1)
        self.assertEqual(before.per_reroll_chance, after.per_reroll_chance)
        self.assertIsNotNone(after.budget_chance)

    def test_rare_odds_below_float_range_remain_positive(self):
        target = (38, 38, 38, 0, 0)
        result = self.estimate(target, regular=114, divine=0, budget=200)
        self.assertEqual(float(result.per_reroll_chance), 0.0)
        self.assertGreater(result.per_reroll_chance, 0)
        self.assertEqual(result.budget_chance, result.per_reroll_chance)
        self.assertEqual(result.expected_paid_rerolls, 1)

    def test_display_rounds_halves_up(self):
        self.assertEqual(display_estimate(2.5).rerolls, 3)
        self.assertEqual(display_estimate(2.5).gems, 600)
        self.assertEqual(display_estimate(2.499).gems, 400)

    def test_confirmed_initial_match_costs_nothing(self):
        result = self.estimate(initial=(3, 1, 0, 0, 0))
        self.assertTrue(result.initial_goal_met)
        self.assertEqual(result.budget_chance, 1)
        self.assertEqual(result.expected_paid_rerolls, 0)
        self.assertEqual(display_estimate(result.expected_paid_rerolls).gems, 0)

    def test_impossible_goal(self):
        for result in (self.estimate(divine=3), self.estimate((0, 2, 0, 0, 0), cap=1)):
            self.assertEqual(result.per_reroll_chance, 0)
            self.assertEqual(result.budget_chance, 0)
            self.assertEqual(result.expected_paid_rerolls, 2)

    def test_multiple_targets_must_be_met_in_one_allocation(self):
        target = (4, 1, 0, 0, 0)
        self.assertTrue(goal_met((4, 1, 0, 0, 0), target))
        self.assertFalse(goal_met((3, 2, 0, 0, 0), target))
        self.assertFalse(goal_met((5, 0, 0, 0, 0), target))
        self.assertEqual(self.estimate(target, divine=5).per_reroll_chance, 0.5)
        # One Supernova in draws 4–6 and one Rainbow Floor Multi on draw 7.
        both_capped = (0, 1, 1, 0, 0)
        self.assertAlmostEqual(self.estimate(both_capped, divine=7).per_reroll_chance, 7 / 24)

    def test_unlocks_use_all_allocated_divines(self):
        thresholds = (3, 6, 10, 30)
        for index, threshold in enumerate(thresholds, start=1):
            with self.subTest(index=index):
                self.assertNotIn(index, eligible_types((threshold - 1, 0, 0, 0, 0), 38))
                self.assertIn(index, eligible_types((threshold, 0, 0, 0, 0), 38))
        self.assertEqual(eligible_types((2, 1, 1, 1, 1), 38), (0, 1, 2))

    def test_cap_redistributes_draws_even_with_zero_target(self):
        # With cap 1, either of the two last draws can be the single Supernova.
        rainbow = (4, 0, 0, 0, 0)
        self.assertEqual(self.estimate(rainbow, divine=5, cap=1).per_reroll_chance, 1)
        self.assertEqual(self.estimate(rainbow, divine=5, cap=2).per_reroll_chance, 0.75)

    def test_rainbow_allocation_may_exceed_the_target_limit(self):
        result = self.estimate((38, 0, 0, 0, 0), divine=42, cap=1, initial=(39, 0, 0, 0, 0))
        self.assertTrue(result.initial_goal_met)
        self.assertEqual(result.expected_gems, 0)
        # Only four non-Rainbow types can take a draw each before reaching cap 1.
        self.assertEqual(self.estimate((38, 0, 0, 0, 0), divine=42, cap=1).per_reroll_chance, 1)

    def test_regular_chest_supplies_a_divine_at_the_base_rate(self):
        result = self.estimate(regular=1, divine=3)
        self.assertAlmostEqual(result.per_reroll_chance, 1 / 50_000, places=17)
        self.assertAlmostEqual(result.budget_chance, 1 / 25_000 - (1 / 50_000) ** 2, places=17)
        result = self.estimate((1, 0, 0, 0, 0), regular=511_058, divine=3)
        self.assertAlmostEqual(result.expected_divines, 23.44232)

    def test_unknown_odds_and_unknown_initial_allocation(self):
        result = estimate_rerolls(self.supernova, 0, 4, 399, rules=None)
        self.assertEqual(result.max_paid_rerolls, 1)
        self.assertIsNone(result.per_reroll_chance)
        self.assertIsNone(result.budget_chance)
        self.assertIsNone(result.expected_paid_rerolls)
        self.assertIsNone(result.expected_gems)
        estimate = self.estimate(initial=None)
        self.assertIsNone(estimate.initial_goal_met)
        self.assertEqual(estimate.budget_chance, 0.75)

    def test_empty_pool_allows_no_rerolls(self):
        result = self.estimate(divine=0)
        self.assertEqual(result.max_paid_rerolls, 0)
        self.assertEqual(result.per_reroll_chance, 0)
        self.assertEqual(result.expected_gems, 0)

    def test_rejects_invalid_targets_and_inputs(self):
        for target in (self.empty, (39, 0, 0, 0, 0), (-1, 0, 0, 0, 0), (1, 1)):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.estimate(target)
        for field, value in itertools.product(("regular", "divine", "budget"), (-1, 1.5)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.estimate(**{field: value})
