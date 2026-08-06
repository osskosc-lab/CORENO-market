from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

ALLOWED_QUALITIES = {"HIGH", "MEDIUM", "LOW", "UNSCORABLE"}
ALLOWED_BH_REGIONS = {"INSIDE", "OUTSIDE", "BOUNDARY", "UNKNOWN"}
LEDGER_FIELDS = [
    "date",
    "risk_percentile",
    "drawdown_252",
    "data_quality",
    "state",
    "bh_region",
    "M",
    "K",
    "JH",
    "D_EH",
    "V_esc",
    "recovery_margin",
    "source",
    "source_sha256",
]


class SnapshotError(ValueError):
    """Raised when an incoming shadow snapshot violates the frozen contract."""


@dataclass(frozen=True)
class FrozenSpec:
    stable_lt: float
    watch_lt: float
    critical_gte: float
    critical_days_required: int
    critical_window_days: int
    active_crisis_drawdown_lte: float
    crisis_crossing_drawdown_lte: float
    event_cooldown_rows: int
    forecast_window_rows: int
    minimum_non_crisis_rows_before_event: int


def load_spec(path: str | Path) -> FrozenSpec:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    s = raw["state_thresholds"]
    e = raw["event_definition"]
    return FrozenSpec(
        stable_lt=float(s["stable_lt"]),
        watch_lt=float(s["watch_lt"]),
        critical_gte=float(s["critical_gte"]),
        critical_days_required=int(s["critical_days_required"]),
        critical_window_days=int(s["critical_window_days"]),
        active_crisis_drawdown_lte=float(s["active_crisis_drawdown_lte"]),
        crisis_crossing_drawdown_lte=float(e["crisis_crossing_drawdown_lte"]),
        event_cooldown_rows=int(e["event_cooldown_rows"]),
        forecast_window_rows=int(e["forecast_window_rows"]),
        minimum_non_crisis_rows_before_event=int(e["minimum_non_crisis_rows_before_event"]),
    )


def _parse_date(value: Any) -> date:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise SnapshotError("date must use YYYY-MM-DD") from exc


def _finite_number(value: Any, name: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise SnapshotError(f"{name} must be finite")
    return number


def validate_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    required = {"date", "risk_percentile", "drawdown_252", "data_quality", "bh_region"}
    missing = sorted(required - raw.keys())
    if missing:
        raise SnapshotError(f"missing required fields: {', '.join(missing)}")

    parsed_date = _parse_date(raw["date"])
    quality = str(raw["data_quality"]).upper()
    if quality not in ALLOWED_QUALITIES:
        raise SnapshotError(f"data_quality must be one of {sorted(ALLOWED_QUALITIES)}")
    region = str(raw["bh_region"]).upper()
    if region not in ALLOWED_BH_REGIONS:
        raise SnapshotError(f"bh_region must be one of {sorted(ALLOWED_BH_REGIONS)}")

    risk = _finite_number(raw["risk_percentile"], "risk_percentile")
    drawdown = _finite_number(raw["drawdown_252"], "drawdown_252")
    assert risk is not None and drawdown is not None
    if not 0.0 <= risk <= 1.0:
        raise SnapshotError("risk_percentile must be between 0 and 1")
    if drawdown > 0.0:
        raise SnapshotError("drawdown_252 must be zero or negative")

    normalized: dict[str, Any] = {
        "date": parsed_date.isoformat(),
        "risk_percentile": risk,
        "drawdown_252": drawdown,
        "data_quality": quality,
        "bh_region": region,
        "source": str(raw.get("source", "unspecified")),
    }
    for name in ("M", "K", "JH", "D_EH", "V_esc", "recovery_margin"):
        normalized[name] = _finite_number(raw.get(name), name, optional=True)
    return normalized


def read_ledger(path: str | Path) -> list[dict[str, str]]:
    ledger = Path(path)
    if not ledger.exists() or ledger.stat().st_size == 0:
        return []
    with ledger.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def determine_state(snapshot: dict[str, Any], prior_rows: Iterable[dict[str, str]], spec: FrozenSpec) -> str:
    if snapshot["data_quality"] == "UNSCORABLE":
        return "UNSCORABLE"
    if snapshot["drawdown_252"] <= spec.active_crisis_drawdown_lte:
        return "ACTIVE_CRISIS"

    recent_risks: list[float] = []
    for row in list(prior_rows)[-(spec.critical_window_days - 1) :]:
        try:
            recent_risks.append(float(row["risk_percentile"]))
        except (KeyError, TypeError, ValueError):
            continue
    recent_risks.append(float(snapshot["risk_percentile"]))
    critical_count = sum(value >= spec.critical_gte for value in recent_risks)
    if critical_count >= spec.critical_days_required:
        return "CRITICAL"

    risk = float(snapshot["risk_percentile"])
    if risk < spec.stable_lt:
        return "STABLE"
    if risk < spec.watch_lt:
        return "WATCH"
    return "WARNING"


def ingest_snapshot(snapshot_path: str | Path, ledger_path: str | Path, spec_path: str | Path) -> dict[str, Any]:
    source_bytes = Path(snapshot_path).read_bytes()
    raw = json.loads(source_bytes.decode("utf-8"))
    snapshot = validate_snapshot(raw)
    spec = load_spec(spec_path)
    rows = read_ledger(ledger_path)

    if rows:
        latest = _parse_date(rows[-1]["date"])
        incoming = _parse_date(snapshot["date"])
        if incoming == latest or any(row.get("date") == snapshot["date"] for row in rows):
            raise SnapshotError(f"date {snapshot['date']} already exists; ledger is append-only")
        if incoming < latest:
            raise SnapshotError("incoming date is earlier than the latest ledger date")

    state = determine_state(snapshot, rows, spec)
    output = {
        **snapshot,
        "state": state,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
    }

    ledger = Path(ledger_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    write_header = not ledger.exists() or ledger.stat().st_size == 0
    with ledger.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({field: "" if output.get(field) is None else output.get(field, "") for field in LEDGER_FIELDS})
    return output


def _crisis_crossings(drawdowns: list[float], spec: FrozenSpec) -> list[int]:
    crossings: list[int] = []
    last_event = -10**9
    for index, value in enumerate(drawdowns):
        previous = drawdowns[index - 1] if index else 0.0
        crossed = previous > spec.crisis_crossing_drawdown_lte and value <= spec.crisis_crossing_drawdown_lte
        if crossed and index - last_event > spec.event_cooldown_rows:
            crossings.append(index)
            last_event = index
    return crossings


def evaluate_ledger(ledger_path: str | Path, spec_path: str | Path) -> dict[str, Any]:
    rows = read_ledger(ledger_path)
    spec = load_spec(spec_path)
    if not rows:
        return {
            "observations": 0,
            "events": 0,
            "alerts": 0,
            "resolved_alerts": 0,
            "captured_events": 0,
            "event_recall": None,
            "event_precision": None,
            "false_alarms": 0,
            "false_alarms_per_year": None,
            "note": "No shadow observations are available.",
        }

    drawdowns = [float(row["drawdown_252"]) for row in rows]
    states = [row["state"] for row in rows]
    events = _crisis_crossings(drawdowns, spec)
    alerts = [i for i, state in enumerate(states) if state == "CRITICAL"]

    eligible_events: list[int] = []
    captured = 0
    for event in events:
        start = max(0, event - spec.forecast_window_rows)
        non_crisis_before = sum(drawdowns[i] > spec.active_crisis_drawdown_lte for i in range(start, event))
        if non_crisis_before < spec.minimum_non_crisis_rows_before_event:
            continue
        eligible_events.append(event)
        if any(start <= alert < event for alert in alerts):
            captured += 1

    resolved_cutoff = len(rows) - spec.forecast_window_rows
    resolved_alerts = [alert for alert in alerts if alert < resolved_cutoff]
    true_alerts = 0
    for alert in resolved_alerts:
        if any(alert < event <= alert + spec.forecast_window_rows for event in eligible_events):
            true_alerts += 1
    false_alarms = len(resolved_alerts) - true_alerts

    elapsed_years = max(((_parse_date(rows[-1]["date"]) - _parse_date(rows[0]["date"])).days / 365.2425), 0.0)
    return {
        "observations": len(rows),
        "events": len(eligible_events),
        "alerts": len(alerts),
        "resolved_alerts": len(resolved_alerts),
        "captured_events": captured,
        "event_recall": captured / len(eligible_events) if eligible_events else None,
        "event_precision": true_alerts / len(resolved_alerts) if resolved_alerts else None,
        "false_alarms": false_alarms,
        "false_alarms_per_year": false_alarms / elapsed_years if elapsed_years > 0 else None,
        "unresolved_alerts": len(alerts) - len(resolved_alerts),
        "note": "Last forecast-window observations are right-censored for false-alarm evaluation.",
    }
