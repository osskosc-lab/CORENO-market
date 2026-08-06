#!/usr/bin/env python3
"""Collect raw daily closes only; this script never fits or scores the model."""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

SYMBOLS = {
    "SP500": "^GSPC",
    "NIKKEI225": "^N225",
    "DOW": "^DJI",
    "NASDAQ": "^IXIC",
    "VIX": "^VIX",
    "DAX": "^GDAXI",
    "FTSE100": "^FTSE",
    "HANGSENG": "^HSI",
}


def merge_rows(existing: list[dict[str, str]], incoming: list[dict[str, str]]) -> list[dict[str, str]]:
    keyed = {(row["date"], row["market"]): row for row in existing}
    for row in incoming:
        keyed[(row["date"], row["market"])] = row
    return [keyed[key] for key in sorted(keyed)]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "market", "symbol", "close", "source"])
        writer.writeheader()
        writer.writerows(rows)


def fetch_yfinance(start: str, end: str) -> list[dict[str, str]]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is required for live collection") from exc

    output: list[dict[str, str]] = []
    for market, symbol in SYMBOLS.items():
        frame = yf.download(symbol, start=start, end=end, auto_adjust=False, progress=False, threads=False)
        if frame.empty:
            continue
        close_col = "Adj Close" if "Adj Close" in frame.columns else "Close"
        series = frame[close_col]
        if getattr(series, "ndim", 1) > 1:
            series = series.iloc[:, 0]
        for timestamp, value in series.dropna().items():
            output.append(
                {
                    "date": timestamp.date().isoformat(),
                    "market": market,
                    "symbol": symbol,
                    "close": f"{float(value):.10g}",
                    "source": "Yahoo Finance via yfinance",
                }
            )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/raw/market_close.csv")
    parser.add_argument("--lookback-days", type=int, default=10)
    args = parser.parse_args()

    end_date = date.today() + timedelta(days=1)
    start_date = end_date - timedelta(days=args.lookback_days)
    output_path = Path(args.output)
    existing = read_csv(output_path)
    incoming = fetch_yfinance(start_date.isoformat(), end_date.isoformat())
    if not incoming:
        print("No market rows returned; existing file left unchanged.", file=sys.stderr)
        return 3
    merged = merge_rows(existing, incoming)
    write_csv(output_path, merged)
    print(f"Collected {len(incoming)} rows; ledger now has {len(merged)} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
