from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from coreno_market.shadow import SnapshotError, determine_state, ingest_snapshot, load_spec  # noqa: E402
from collect_market_data import merge_rows  # noqa: E402

SPEC_PATH = ROOT / "config" / "frozen_spec_v1.json"


class ShadowTests(unittest.TestCase):
    def test_baseline_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp) / "ledger.csv"
            result = ingest_snapshot(ROOT / "incoming" / "baseline_2026-07-31.json", ledger, SPEC_PATH)
            self.assertEqual(result["state"], "WARNING")
            self.assertEqual(result["bh_region"], "OUTSIDE")

    def test_critical_requires_three_of_five(self) -> None:
        spec = load_spec(SPEC_PATH)
        prior = [
            {"risk_percentile": "0.90"},
            {"risk_percentile": "0.20"},
            {"risk_percentile": "0.86"},
            {"risk_percentile": "0.40"},
        ]
        snapshot = {"risk_percentile": 0.87, "drawdown_252": -0.02, "data_quality": "HIGH"}
        self.assertEqual(determine_state(snapshot, prior, spec), "CRITICAL")

    def test_active_crisis_overrides_critical(self) -> None:
        spec = load_spec(SPEC_PATH)
        prior = [{"risk_percentile": "0.99"}] * 4
        snapshot = {"risk_percentile": 0.99, "drawdown_252": -0.09, "data_quality": "HIGH"}
        self.assertEqual(determine_state(snapshot, prior, spec), "ACTIVE_CRISIS")

    def test_append_only_rejects_duplicate_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp) / "ledger.csv"
            snapshot = Path(temp) / "snapshot.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "date": "2026-08-03",
                        "risk_percentile": 0.7,
                        "drawdown_252": -0.03,
                        "data_quality": "HIGH",
                        "bh_region": "UNKNOWN",
                    }
                ),
                encoding="utf-8",
            )
            ingest_snapshot(snapshot, ledger, SPEC_PATH)
            with self.assertRaises(SnapshotError):
                ingest_snapshot(snapshot, ledger, SPEC_PATH)

    def test_market_rows_merge_by_date_and_market(self) -> None:
        existing = [{"date": "2026-08-01", "market": "SP500", "symbol": "^GSPC", "close": "1", "source": "x"}]
        incoming = [{"date": "2026-08-01", "market": "SP500", "symbol": "^GSPC", "close": "2", "source": "y"}]
        merged = merge_rows(existing, incoming)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["close"], "2")

    def test_ledger_header_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp) / "ledger.csv"
            ingest_snapshot(ROOT / "incoming" / "baseline_2026-07-31.json", ledger, SPEC_PATH)
            with ledger.open(encoding="utf-8", newline="") as handle:
                fields = next(csv.reader(handle))
            self.assertIn("source_sha256", fields)
            self.assertIn("state", fields)


if __name__ == "__main__":
    unittest.main()
