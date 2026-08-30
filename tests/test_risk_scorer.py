"""
Unit tests for src/risk_scorer.py -- the math that's easy to get subtly wrong:
percentile rank, weight renormalization, the score formula, and classification
thresholds. Formatting/glue code is not covered here.
"""

import unittest

from src.risk_scorer import (
    RISK_THRESHOLD_LOW_MEDIUM,
    RISK_THRESHOLD_MEDIUM_HIGH,
    FileSignals,
    _percentile_rank,
    classify_risk_level,
    score_files,
)


class TestPercentileRank(unittest.TestCase):

    def test_endpoints_and_middle_are_endpoint_preserving(self):
        self.assertEqual(_percentile_rank(1.0, [1.0, 2.0, 3.0]), 0.0)
        self.assertAlmostEqual(_percentile_rank(3.0, [1.0, 2.0, 3.0]), 1.0)
        self.assertAlmostEqual(_percentile_rank(2.0, [1.0, 2.0, 3.0]), 0.5)

    def test_single_element_and_all_equal_rank_zero(self):
        """No distribution to compare against -> 0.0, the required n=1 special case."""
        self.assertEqual(_percentile_rank(5.0, [5.0]), 0.0)
        self.assertEqual(_percentile_rank(4.0, [4.0, 4.0, 4.0]), 0.0)

    def test_ties_counted_strictly_below_not_equal(self):
        """[1,2,2,3] at value=2: only the single 1 counts -> 1/3, not 2/3."""
        self.assertAlmostEqual(_percentile_rank(2.0, [1.0, 2.0, 2.0, 3.0]), 1 / 3)


class TestScoreFormula(unittest.TestCase):

    def _make(self, path, fan_in, bug_fix_count, ownership, staleness=0):
        return FileSignals(path, fan_in, bug_fix_count, ownership, staleness)

    def test_empty_batch_returns_empty(self):
        self.assertEqual(score_files([]), [])

    def test_single_file_uses_only_ownership_not_percentile_rank(self):
        """No distribution -> percentile ranks are 0; score comes from ownership alone."""
        r = score_files([self._make("a.py", fan_in=10, bug_fix_count=5, ownership=0.8)])[0]
        expected = round(10 * (0.20 / 0.90) * 0.8, 2)
        self.assertAlmostEqual(r.risk_score, expected, places=2)
        self.assertEqual((r.percentile_bug_fix_count, r.percentile_fan_in), (0.0, 0.0))

    def test_output_order_matches_input_order(self):
        files = [self._make(p, i, i, 0.1) for i, p in enumerate(["a.py", "b.py", "c.py"], 1)]
        self.assertEqual([r.path for r in score_files(files)], ["a.py", "b.py", "c.py"])

    def test_higher_signals_across_batch_score_higher(self):
        files = [self._make("low.py", 1, 1, 0.1), self._make("mid.py", 5, 5, 0.5),
                 self._make("high.py", 10, 10, 0.9)]
        scores = {r.path: r.risk_score for r in score_files(files)}
        self.assertGreater(scores["high.py"], scores["mid.py"])
        self.assertGreater(scores["mid.py"], scores["low.py"])

    def test_score_always_bounded_zero_to_ten(self):
        files = [self._make("zero.py", 0, 0, 0.0), self._make("max.py", 999, 999, 1.0)]
        for r in score_files(files):
            self.assertGreaterEqual(r.risk_score, 0.0)
            self.assertLessEqual(r.risk_score, 10.0)

    def test_staleness_has_zero_weight_in_phase1(self):
        """Varying days_since_last_touch must not move the score in Phase 1."""
        low = score_files([self._make("a.py", 5, 5, 0.5, 0), self._make("b.py", 5, 5, 0.5, 0)])
        high = score_files([self._make("a.py", 5, 5, 0.5, 0), self._make("b.py", 5, 5, 0.5, 365)])
        self.assertEqual(low[1].risk_score, high[1].risk_score)

    def test_explicit_formula_matches_manual_calculation(self):
        """n=2 batch, hand-computed expected scores from the documented weights."""
        w_bug, w_fan, w_own = 0.40 / 0.90, 0.30 / 0.90, 0.20 / 0.90
        a, b = score_files([self._make("A.py", 2, 1, 0.2), self._make("B.py", 8, 9, 0.8)])
        self.assertAlmostEqual(a.risk_score, round(10 * w_own * 0.2, 2), places=1)
        self.assertAlmostEqual(b.risk_score, round(10 * (w_bug + w_fan + w_own * 0.8), 2), places=1)

    def test_result_contains_contributing_factors_for_explainability(self):
        r = score_files([self._make("f.py", 3, 7, 0.6, 14)])[0]
        self.assertEqual((r.fan_in, r.bug_fix_count, r.days_since_last_touch), (3, 7, 14))
        self.assertAlmostEqual(r.ownership_concentration, 0.6)


class TestClassifyRiskLevel(unittest.TestCase):

    def test_boundaries_are_inclusive_on_the_lower_bound(self):
        self.assertEqual(classify_risk_level(3.999), "LOW")
        self.assertEqual(classify_risk_level(4.0), "MEDIUM")
        self.assertEqual(classify_risk_level(6.999), "MEDIUM")
        self.assertEqual(classify_risk_level(7.0), "HIGH")

    def test_extremes(self):
        self.assertEqual(classify_risk_level(0.0), "LOW")
        self.assertEqual(classify_risk_level(10.0), "HIGH")

    def test_threshold_constants(self):
        self.assertEqual(RISK_THRESHOLD_LOW_MEDIUM, 4.0)
        self.assertEqual(RISK_THRESHOLD_MEDIUM_HIGH, 7.0)
        self.assertLess(RISK_THRESHOLD_LOW_MEDIUM, RISK_THRESHOLD_MEDIUM_HIGH)


if __name__ == "__main__":
    unittest.main()
