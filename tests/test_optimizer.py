from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from coreno_market.optimizer import Candidate, _auc, crisis_crossings, future_event_labels, make_alerts  # noqa: E402


class OptimizerTests(unittest.TestCase):
    def test_crisis_crossing_respects_cooldown(self):
        drawdown = pd.Series([0, -0.05, -0.13, -0.14, -0.05, -0.13, -0.02, -0.13])
        self.assertEqual(crisis_crossings(drawdown, -0.12, 3), [2, 7])

    def test_future_event_labels_are_strictly_before_event(self):
        labels = future_event_labels(10, [5], 3)
        self.assertEqual(labels.tolist(), [0, 0, 1, 1, 1, 0, 0, 0, 0, 0])

    def test_auc_perfect(self):
        labels = np.array([0, 0, 1, 1])
        scores = np.array([0.1, 0.2, 0.8, 0.9])
        self.assertAlmostEqual(_auc(labels, scores), 1.0)

    def test_alert_threshold_uses_past_only(self):
        score = pd.Series(np.linspace(0.0, 1.0, 20))
        candidate = Candidate(20, 20, 0.8, 1, (0.2, 0.2, 0.2, 0.2, 0.2))
        alert, threshold = make_alerts(score, candidate, {"minimum_history_rows": 5})
        self.assertTrue(np.isnan(threshold.iloc[4]))
        self.assertFalse(bool(alert.iloc[4]))
        self.assertTrue(np.isfinite(threshold.iloc[5]))


if __name__ == "__main__":
    unittest.main()
