"""
Unit tests for src/risk_scorer.py.

Focus: the math that is easy to get subtly wrong and expensive to discover
wrong at demo time — percentile rank computation, weight application,
score formula, edge cases (single file, all-equal values, zero signals,
ownership clamping). Formatting and glue code are not tested here.
"""

import unittest

from src.risk_scorer import FileSignals, FileRiskResult, score_files, _percentile_rank


# ---------------------------------------------------------------------------
# _percentile_rank — unit tests (pure math, no file I/O)
# ---------------------------------------------------------------------------

class TestPercentileRank(unittest.TestCase):

    def test_lowest_value_ranks_zero(self):
        """The smallest value in the distribution has nothing below it: rank = 0."""
        self.assertEqual(_percentile_rank(1.0, [1.0, 2.0, 3.0]), 0.0)

    def test_highest_value_ranks_one(self):
        """The highest value in the batch gets exactly 1.0 (endpoint-preserving)."""
        rank = _percentile_rank(3.0, [1.0, 2.0, 3.0])
        self.assertAlmostEqual(rank, 1.0)  # 2 / (3-1) = 1.0

    def test_middle_value(self):
        """Middle value: 1 strictly below it, n-1=2 → rank = 0.5."""
        rank = _percentile_rank(2.0, [1.0, 2.0, 3.0])
        self.assertAlmostEqual(rank, 1/2)

    def test_single_element_returns_zero(self):
        """Single-element batch has no distribution — rank is 0.0 by definition."""
        self.assertEqual(_percentile_rank(5.0, [5.0]), 0.0)

    def test_all_equal_values_rank_zero(self):
        """When all values are equal, nothing is strictly below anything: all rank 0."""
        self.assertEqual(_percentile_rank(4.0, [4.0, 4.0, 4.0]), 0.0)

    def test_duplicates_counted_strictly(self):
        """Ties: only values *strictly less* than target count toward rank."""
        # [1, 2, 2, 3]: for value=2, only 1 is strictly below → 1/(4-1) = 1/3
        rank = _percentile_rank(2.0, [1.0, 2.0, 2.0, 3.0])
        self.assertAlmostEqual(rank, 1/3)


# ---------------------------------------------------------------------------
# score_files — formula and weight correctness
# ---------------------------------------------------------------------------

class TestScoreFormula(unittest.TestCase):

    def _make(self, path, fan_in, bug_fix_count, ownership, staleness=0):
        return FileSignals(
            path=path,
            fan_in=fan_in,
            bug_fix_count=bug_fix_count,
            ownership_concentration=ownership,
            days_since_last_touch=staleness,
        )

    def test_empty_batch_returns_empty(self):
        """Scoring an empty list must return an empty list without error."""
        self.assertEqual(score_files([]), [])

    def test_single_file_scores_zero_rank(self):
        """
        A single file has no distribution to compare against. All percentile
        ranks are 0.0. Score comes only from ownership_concentration.

        Renormalized ownership weight = 0.20 / (0.40+0.30+0.20) = 0.20/0.90 = 2/9.
        score = 10 * (2/9) * 0.8 = 1.777...  rounds to 1.78.
        """
        result = score_files([self._make("a.py", fan_in=10, bug_fix_count=5, ownership=0.8)])
        self.assertEqual(len(result), 1)
        r = result[0]
        expected = round(10 * (0.20 / 0.90) * 0.8, 2)
        self.assertAlmostEqual(r.risk_score, expected, places=2)
        self.assertEqual(r.percentile_bug_fix_count, 0.0)
        self.assertEqual(r.percentile_fan_in, 0.0)

    def test_output_order_matches_input_order(self):
        """Results must be returned in the same order as the input."""
        files = [
            self._make("a.py", 1, 1, 0.1),
            self._make("b.py", 5, 5, 0.5),
            self._make("c.py", 2, 2, 0.2),
        ]
        results = score_files(files)
        self.assertEqual([r.path for r in results], ["a.py", "b.py", "c.py"])

    def test_highest_signals_gets_highest_score(self):
        """The file with the highest values across all signals must score highest."""
        files = [
            self._make("low.py",  fan_in=1,  bug_fix_count=1,  ownership=0.1),
            self._make("mid.py",  fan_in=5,  bug_fix_count=5,  ownership=0.5),
            self._make("high.py", fan_in=10, bug_fix_count=10, ownership=0.9),
        ]
        results = score_files(files)
        scores = {r.path: r.risk_score for r in results}
        self.assertGreater(scores["high.py"], scores["mid.py"])
        self.assertGreater(scores["mid.py"], scores["low.py"])

    def test_all_equal_signals_produce_equal_scores(self):
        """Files with identical signals must all receive the same score."""
        files = [self._make(f"f{i}.py", fan_in=3, bug_fix_count=3, ownership=0.5)
                 for i in range(4)]
        results = score_files(files)
        scores = [r.risk_score for r in results]
        self.assertEqual(len(set(scores)), 1)

    def test_score_bounded_zero_to_ten(self):
        """Risk score must always be in [0.0, 10.0]."""
        files = [
            self._make("zero.py", fan_in=0, bug_fix_count=0, ownership=0.0),
            self._make("max.py",  fan_in=999, bug_fix_count=999, ownership=1.0),
        ]
        for r in score_files(files):
            self.assertGreaterEqual(r.risk_score, 0.0)
            self.assertLessEqual(r.risk_score, 10.0)

    def test_zero_ownership_lowers_score(self):
        """Two otherwise identical files: lower ownership_concentration → lower score."""
        files = [
            self._make("a.py", fan_in=5, bug_fix_count=5, ownership=0.9),
            self._make("b.py", fan_in=5, bug_fix_count=5, ownership=0.1),
        ]
        results = score_files(files)
        scores = {r.path: r.risk_score for r in results}
        # fan_in and bug_fix_count are identical so percentile ranks are equal;
        # ownership is the only differentiator.
        self.assertGreater(scores["a.py"], scores["b.py"])

    def test_staleness_has_zero_weight_in_phase1(self):
        """Varying days_since_last_touch must not change the score in Phase 1."""
        # Two files identical except for staleness.
        files_low  = [self._make("a.py", 5, 5, 0.5, staleness=0),
                      self._make("b.py", 5, 5, 0.5, staleness=0)]
        files_high = [self._make("a.py", 5, 5, 0.5, staleness=0),
                      self._make("b.py", 5, 5, 0.5, staleness=365)]

        scores_low  = {r.path: r.risk_score for r in score_files(files_low)}
        scores_high = {r.path: r.risk_score for r in score_files(files_high)}
        # b.py has much higher staleness in the second batch — score must not change.
        self.assertEqual(scores_high["b.py"], scores_low["b.py"])

    def test_explicit_formula_two_files(self):
        """
        Manual calculation to verify the formula is applied exactly.

        Two files: A (low signals) and B (high signals), n=2.
            fan_in:    A=2, B=8  -> p_fanin(A) = 0/(2-1) = 0.0
                                     p_fanin(B) = 1/(2-1) = 1.0
            bug_fix:   A=1, B=9  -> p_bug(A)   = 0/(2-1) = 0.0
                                     p_bug(B)   = 1/(2-1) = 1.0
            ownership: A=0.2, B=0.8 -> used directly, not ranked

        Renormalized weights (staleness inactive):
            w_bug = 0.40/0.90 = 4/9,  w_fan = 0.30/0.90 = 3/9,  w_own = 0.20/0.90 = 2/9

        score(A) = 10 * (4/9*0 + 3/9*0 + 2/9*0.2) = 10 * (0.4/9) ≈ 0.44
        score(B) = 10 * (4/9*1 + 3/9*1 + 2/9*0.8) = 10 * (7/9 + 1.6/9)
                 = 10 * 8.6/9 ≈ 9.56
        """
        w_bug = 0.40 / 0.90
        w_fan = 0.30 / 0.90
        w_own = 0.20 / 0.90

        files = [
            self._make("A.py", fan_in=2, bug_fix_count=1, ownership=0.2),
            self._make("B.py", fan_in=8, bug_fix_count=9, ownership=0.8),
        ]
        results = score_files(files)
        a = next(r for r in results if r.path == "A.py")
        b = next(r for r in results if r.path == "B.py")

        expected_a = round(10 * (w_bug * 0.0 + w_fan * 0.0 + w_own * 0.2), 2)
        expected_b = round(10 * (w_bug * 1.0 + w_fan * 1.0 + w_own * 0.8), 2)
        self.assertAlmostEqual(a.risk_score, expected_a, places=1)
        self.assertAlmostEqual(b.risk_score, expected_b, places=1)

    def test_result_contains_contributing_factors(self):
        """Every result must include the raw signal values for explainability."""
        files = [self._make("f.py", fan_in=3, bug_fix_count=7, ownership=0.6, staleness=14)]
        r = score_files(files)[0]
        self.assertEqual(r.fan_in, 3)
        self.assertEqual(r.bug_fix_count, 7)
        self.assertAlmostEqual(r.ownership_concentration, 0.6)
        self.assertEqual(r.days_since_last_touch, 14)
        self.assertIsInstance(r.percentile_bug_fix_count, float)
        self.assertIsInstance(r.percentile_fan_in, float)
        self.assertIsInstance(r.percentile_days_since_last_touch, float)


# ---------------------------------------------------------------------------
# FileRiskResult path passthrough
# ---------------------------------------------------------------------------

class TestResultMetadata(unittest.TestCase):

    def test_path_preserved_in_result(self):
        """The result's path must match the input FileSignals path exactly."""
        files = [FileSignals("src/payment.py", 5, 3, 0.7)]
        r = score_files(files)[0]
        self.assertEqual(r.path, "src/payment.py")

    def test_large_batch_all_scored(self):
        """Every file in the batch must appear in the output — no silent drops."""
        files = [FileSignals(f"file_{i}.py", i, i, i / 100) for i in range(50)]
        results = score_files(files)
        self.assertEqual(len(results), 50)
        self.assertEqual([r.path for r in results], [s.path for s in files])


if __name__ == "__main__":
    unittest.main()
