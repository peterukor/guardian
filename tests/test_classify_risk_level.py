"""
Tests for classify_risk_level() and its threshold constants in src/risk_scorer.py.

Coverage:
  - LOW range (score < 4.0)
  - MEDIUM range (4.0 <= score < 7.0)
  - HIGH range (score >= 7.0)
  - Exact boundary values 4.0 and 7.0
  - Extremes: 0.0 and 10.0
  - Threshold constants exported with the expected values
"""

import unittest

from src.risk_scorer import (
    RISK_THRESHOLD_LOW_MEDIUM,
    RISK_THRESHOLD_MEDIUM_HIGH,
    classify_risk_level,
)


class TestClassifyRiskLevel(unittest.TestCase):

    # --- LOW range ----------------------------------------------------------

    def test_zero_is_low(self):
        self.assertEqual(classify_risk_level(0.0), "LOW")

    def test_below_low_medium_threshold_is_low(self):
        self.assertEqual(classify_risk_level(3.9), "LOW")

    def test_just_below_low_medium_boundary_is_low(self):
        """3.99… is still LOW — boundary is at exactly 4.0."""
        self.assertEqual(classify_risk_level(3.999), "LOW")

    # --- Boundary at 4.0 ----------------------------------------------------

    def test_exactly_low_medium_boundary_is_medium(self):
        """4.0 is the first MEDIUM value — inclusive on the lower bound."""
        self.assertEqual(classify_risk_level(4.0), "MEDIUM")

    # --- MEDIUM range -------------------------------------------------------

    def test_mid_medium_is_medium(self):
        self.assertEqual(classify_risk_level(5.5), "MEDIUM")

    def test_just_below_medium_high_boundary_is_medium(self):
        """6.99… is still MEDIUM — the HIGH boundary is at exactly 7.0."""
        self.assertEqual(classify_risk_level(6.999), "MEDIUM")

    # --- Boundary at 7.0 ----------------------------------------------------

    def test_exactly_medium_high_boundary_is_high(self):
        """7.0 is the first HIGH value — inclusive on the lower bound."""
        self.assertEqual(classify_risk_level(7.0), "HIGH")

    # --- HIGH range ---------------------------------------------------------

    def test_above_medium_high_threshold_is_high(self):
        self.assertEqual(classify_risk_level(8.5), "HIGH")

    def test_ten_is_high(self):
        """Maximum possible score must be HIGH."""
        self.assertEqual(classify_risk_level(10.0), "HIGH")

    # --- Return type --------------------------------------------------------

    def test_returns_string(self):
        for score in (0.0, 4.0, 7.0, 10.0):
            result = classify_risk_level(score)
            self.assertIsInstance(result, str)

    def test_only_valid_labels_returned(self):
        """The function must only ever return one of three known strings."""
        valid = {"LOW", "MEDIUM", "HIGH"}
        for score in (0.0, 1.0, 3.9, 4.0, 5.0, 6.9, 7.0, 8.0, 10.0):
            self.assertIn(classify_risk_level(score), valid)

    # --- Threshold constants ------------------------------------------------

    def test_low_medium_threshold_value(self):
        """RISK_THRESHOLD_LOW_MEDIUM must be exactly 4.0."""
        self.assertEqual(RISK_THRESHOLD_LOW_MEDIUM, 4.0)

    def test_medium_high_threshold_value(self):
        """RISK_THRESHOLD_MEDIUM_HIGH must be exactly 7.0."""
        self.assertEqual(RISK_THRESHOLD_MEDIUM_HIGH, 7.0)

    def test_thresholds_are_ordered(self):
        """Low-medium threshold must be strictly less than medium-high."""
        self.assertLess(RISK_THRESHOLD_LOW_MEDIUM, RISK_THRESHOLD_MEDIUM_HIGH)


if __name__ == "__main__":
    unittest.main()
