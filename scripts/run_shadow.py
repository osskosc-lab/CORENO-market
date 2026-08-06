#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from coreno_market.shadow import SnapshotError, evaluate_ledger, ingest_snapshot  # noqa: E402

DEFAULT_SPEC = ROOT / "config" / "frozen_spec_v1.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CORENO forward shadow ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="validate and append one frozen-score snapshot")
    ingest.add_argument("--snapshot", required=True)
    ingest.add_argument("--ledger", default=str(ROOT / "data" / "shadow" / "ledger.csv"))
    ingest.add_argument("--spec", default=str(DEFAULT_SPEC))

    evaluate = sub.add_parser("evaluate", help="evaluate prospective observations without retuning")
    evaluate.add_argument("--ledger", default=str(ROOT / "data" / "shadow" / "ledger.csv"))
    evaluate.add_argument("--spec", default=str(DEFAULT_SPEC))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "ingest":
            result = ingest_snapshot(args.snapshot, args.ledger, args.spec)
        else:
            result = evaluate_ledger(args.ledger, args.spec)
    except (OSError, json.JSONDecodeError, SnapshotError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
