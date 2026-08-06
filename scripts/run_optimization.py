#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from coreno_market.optimizer import OptimizationError, load_market_data, load_spec, optimize  # noqa: E402


def append_history(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = result["best_candidate"]
    holdout = result["locked_holdout"]
    row = {
        "run_at_utc": result["run_at_utc"],
        "data_end": result["data_end"],
        "status": result["status"],
        "threshold_quantile": candidate["threshold_quantile"],
        "vol_window": candidate["vol_window"],
        "corr_window": candidate["corr_window"],
        "persistence_days": candidate["persistence_days"],
        "holdout_events": holdout["events"],
        "holdout_event_recall": holdout["event_recall"],
        "holdout_false_alarms_per_year": holdout["false_alarms_per_year"],
        "holdout_auc": holdout["auc"],
        "vix_auc": result["vix_baseline"]["auc"],
        "circular_shift_p_value": result["circular_shift_p_value"],
    }
    fields = list(row)
    existing_keys = set()
    if path.exists() and path.stat().st_size:
        with path.open("r", encoding="utf-8", newline="") as handle:
            existing = list(csv.DictReader(handle))
        existing_keys = {
            (
                item["data_end"], item["status"], item["threshold_quantile"],
                item["vol_window"], item["corr_window"], item["persistence_days"],
            )
            for item in existing
        }
    key = (
        str(row["data_end"]), str(row["status"]), str(row["threshold_quantile"]),
        str(row["vol_window"]), str(row["corr_window"]), str(row["persistence_days"]),
    )
    if key in existing_keys:
        return
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if handle.tell() == 0:
            writer.writeheader()
        writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(ROOT / "data/raw/market_close.csv"))
    parser.add_argument("--spec", default=str(ROOT / "config/optimization_spec_v1.json"))
    parser.add_argument("--output", default=str(ROOT / "results/latest.json"))
    parser.add_argument("--timeseries", default=str(ROOT / "results/latest_timeseries.csv"))
    parser.add_argument("--history", default=str(ROOT / "results/history.csv"))
    args = parser.parse_args()

    try:
        spec = load_spec(args.spec)
        prices = load_market_data(args.data)
        result, timeseries = optimize(prices, spec)
    except (OSError, json.JSONDecodeError, OptimizationError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    result["run_at_utc"] = datetime.now(timezone.utc).isoformat()
    result["data_start"] = prices.index.min().date().isoformat()
    result["data_end"] = prices.index.max().date().isoformat()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    timeseries.to_csv(args.timeseries, index=False)
    append_history(Path(args.history), result)
    print(json.dumps({"ok": True, "status": result["status"], "best_candidate": result["best_candidate"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
