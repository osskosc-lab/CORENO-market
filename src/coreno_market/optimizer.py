from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

EQUITY_MARKETS = ["SP500", "NIKKEI225", "DOW", "NASDAQ", "DAX", "FTSE100", "HANGSENG"]
FEATURE_NAMES = ["drawdown_stress", "vol_percentile", "vix_percentile", "sync_percentile", "breadth_pressure"]


class OptimizationError(ValueError):
    pass


@dataclass(frozen=True)
class Candidate:
    vol_window: int
    corr_window: int
    threshold_quantile: float
    persistence_days: int
    weights: tuple[float, float, float, float, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "vol_window": self.vol_window,
            "corr_window": self.corr_window,
            "threshold_quantile": self.threshold_quantile,
            "persistence_days": self.persistence_days,
            "weights": dict(zip(FEATURE_NAMES, self.weights)),
        }


def load_spec(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if sum(raw["weight_sets"][0]) <= 0:
        raise OptimizationError("weight sets must have positive mass")
    return raw


def load_market_data(path: str | Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    required = {"date", "market", "close"}
    missing = required - set(raw.columns)
    if missing:
        raise OptimizationError(f"missing market data columns: {sorted(missing)}")
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    raw = raw.dropna(subset=["date", "market", "close"])
    pivot = raw.pivot_table(index="date", columns="market", values="close", aggfunc="last").sort_index()
    if "SP500" not in pivot:
        raise OptimizationError("SP500 is required as the target market")
    pivot = pivot.loc[pivot["SP500"].notna()].copy()
    other_cols = [column for column in pivot.columns if column != "SP500"]
    pivot[other_cols] = pivot[other_cols].ffill(limit=3)
    if len(pivot) < 756:
        raise OptimizationError("at least 756 SP500 observations are required")
    return pivot


def _rolling_last_percentile(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    min_periods = min_periods or max(60, window // 3)

    def rank_last(values: np.ndarray) -> float:
        finite = values[np.isfinite(values)]
        if len(finite) < min_periods:
            return np.nan
        last = finite[-1]
        return float(np.mean(finite <= last))

    return series.rolling(window, min_periods=min_periods).apply(rank_last, raw=True)


def _average_pairwise_corr(returns: pd.DataFrame, window: int) -> pd.Series:
    available = [column for column in EQUITY_MARKETS if column in returns.columns]
    pairs = list(itertools.combinations(available, 2))
    if not pairs:
        return pd.Series(np.nan, index=returns.index)
    values = [returns[a].rolling(window, min_periods=max(10, window // 2)).corr(returns[b]) for a, b in pairs]
    return pd.concat(values, axis=1).mean(axis=1, skipna=True)


def build_feature_frame(prices: pd.DataFrame, spec: dict[str, Any], vol_window: int, corr_window: int) -> pd.DataFrame:
    target = prices["SP500"].astype(float)
    returns = np.log(prices[[column for column in EQUITY_MARKETS if column in prices.columns]]).diff()
    drawdown = target / target.rolling(252, min_periods=60).max() - 1.0
    drawdown_stress = (-drawdown / 0.25).clip(0.0, 1.0)

    realized_vol = returns["SP500"].rolling(vol_window, min_periods=max(10, vol_window // 2)).std() * math.sqrt(252.0)
    vol_percentile = _rolling_last_percentile(realized_vol, int(spec["rolling_percentile_rows"]))

    if "VIX" in prices:
        vix_percentile = _rolling_last_percentile(prices["VIX"].astype(float), int(spec["rolling_percentile_rows"]))
    else:
        vix_percentile = pd.Series(np.nan, index=prices.index)

    sync = _average_pairwise_corr(returns, corr_window).clip(-1.0, 1.0)
    sync_percentile = _rolling_last_percentile(sync, int(spec["rolling_percentile_rows"]))
    breadth_pressure = (returns < 0).mean(axis=1).rolling(5, min_periods=3).mean()

    frame = pd.DataFrame(
        {
            "close": target,
            "drawdown_252": drawdown,
            "drawdown_stress": drawdown_stress,
            "vol_percentile": vol_percentile,
            "vix_percentile": vix_percentile,
            "sync_percentile": sync_percentile,
            "breadth_pressure": breadth_pressure,
        },
        index=prices.index,
    )
    frame[FEATURE_NAMES] = frame[FEATURE_NAMES].fillna(0.5).clip(0.0, 1.0)
    return frame


def crisis_crossings(drawdown: pd.Series, threshold: float, cooldown: int) -> list[int]:
    values = drawdown.fillna(0.0).to_numpy(dtype=float)
    events: list[int] = []
    last = -10**9
    for idx, value in enumerate(values):
        previous = values[idx - 1] if idx else 0.0
        if previous > threshold and value <= threshold and idx - last > cooldown:
            events.append(idx)
            last = idx
    return events


def future_event_labels(length: int, event_positions: Iterable[int], window: int) -> np.ndarray:
    labels = np.zeros(length, dtype=int)
    for event in event_positions:
        labels[max(0, event - window) : event] = 1
    return labels


def _episode_starts(alerts: np.ndarray, cooldown: int) -> list[int]:
    starts: list[int] = []
    last = -10**9
    active_previous = False
    for idx, active in enumerate(alerts.astype(bool)):
        if active and not active_previous and idx - last > cooldown:
            starts.append(idx)
            last = idx
        active_previous = active
    return starts


def _auc(labels: np.ndarray, scores: np.ndarray) -> float | None:
    valid = np.isfinite(scores)
    y = labels[valid]
    s = scores[valid]
    positives = int(y.sum())
    negatives = int(len(y) - positives)
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    sorted_scores = s[order]
    start = 0
    while start < len(s):
        end = start + 1
        while end < len(s) and sorted_scores[end] == sorted_scores[start]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end
    rank_sum = ranks[y == 1].sum()
    return float((rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives))


def make_score(frame: pd.DataFrame, candidate: Candidate) -> pd.Series:
    matrix = frame[FEATURE_NAMES].to_numpy(dtype=float)
    score = matrix @ np.asarray(candidate.weights, dtype=float)
    return pd.Series(score, index=frame.index, name="risk_score")


def make_alerts(score: pd.Series, candidate: Candidate, spec: dict[str, Any]) -> tuple[pd.Series, pd.Series]:
    threshold = score.expanding(min_periods=int(spec["minimum_history_rows"])).quantile(candidate.threshold_quantile).shift(1)
    raw = score >= threshold
    if candidate.persistence_days <= 1:
        alert = raw
    else:
        alert = raw.rolling(candidate.persistence_days, min_periods=candidate.persistence_days).sum() >= candidate.persistence_days
    return alert.fillna(False), threshold


def evaluate_period(frame: pd.DataFrame, score: pd.Series, alert: pd.Series, spec: dict[str, Any], start: str, end: str | None) -> dict[str, Any]:
    mask = frame.index >= pd.Timestamp(start)
    if end:
        mask &= frame.index <= pd.Timestamp(end)
    positions = np.flatnonzero(np.asarray(mask))
    if len(positions) == 0:
        raise OptimizationError(f"no observations in period {start} to {end}")
    lo, hi = int(positions[0]), int(positions[-1])

    all_events = crisis_crossings(frame["drawdown_252"], float(spec["event_drawdown_lte"]), int(spec["event_cooldown_rows"]))
    events = [event for event in all_events if lo <= event <= hi]
    all_episodes = _episode_starts(alert.to_numpy(dtype=bool), int(spec["alert_cooldown_rows"]))
    episodes = [episode for episode in all_episodes if lo <= episode <= hi]
    forecast_window = int(spec["forecast_window_rows"])

    captured = 0
    lead_times: list[int] = []
    for event in events:
        eligible = [episode for episode in all_episodes if max(0, event - forecast_window) <= episode < event]
        if eligible:
            captured += 1
            lead_times.append(event - max(eligible))

    resolved_episodes = [episode for episode in episodes if episode <= hi - forecast_window]
    true_episodes = sum(any(episode < event <= episode + forecast_window for event in all_events) for episode in resolved_episodes)
    false_alarms = len(resolved_episodes) - true_episodes
    elapsed_years = max((frame.index[hi] - frame.index[lo]).days / 365.2425, 1 / 365.2425)

    labels = future_event_labels(len(frame), all_events, forecast_window)[lo : hi + 1]
    auc = _auc(labels, score.iloc[lo : hi + 1].to_numpy(dtype=float))
    recall = captured / len(events) if events else None
    precision = true_episodes / len(resolved_episodes) if resolved_episodes else None
    fapy = false_alarms / elapsed_years
    median_lead = float(np.median(lead_times)) if lead_times else None
    objective = (recall if recall is not None else 0.0) - 0.25 * fapy + 0.05 * min((median_lead or 0.0) / max(forecast_window, 1), 1.0) + 0.05 * (auc if auc is not None else 0.5)
    return {
        "start": frame.index[lo].date().isoformat(),
        "end": frame.index[hi].date().isoformat(),
        "observations": hi - lo + 1,
        "events": len(events),
        "captured_events": captured,
        "event_recall": recall,
        "alert_episodes": len(episodes),
        "resolved_alert_episodes": len(resolved_episodes),
        "unresolved_alert_episodes": len(episodes) - len(resolved_episodes),
        "true_alert_episodes": true_episodes,
        "false_alarms": false_alarms,
        "false_alarms_per_year": fapy,
        "event_precision": precision,
        "median_lead_rows": median_lead,
        "auc": auc,
        "objective": objective,
    }


def _development_folds(frame: pd.DataFrame, development_end: str) -> list[tuple[str, str]]:
    end_year = pd.Timestamp(development_end).year
    folds = []
    for start_year in [2000, 2005, 2010, 2015]:
        if start_year <= end_year and frame.index.min().year <= start_year:
            folds.append((f"{start_year}-01-01", f"{min(start_year + 4, end_year)}-12-31"))
    return folds or [(frame.index.min().date().isoformat(), development_end)]


def iter_candidates(spec: dict[str, Any]) -> Iterable[Candidate]:
    for vol_window, corr_window, threshold, persistence, weights in itertools.product(spec["vol_windows"], spec["corr_windows"], spec["threshold_quantiles"], spec["persistence_days"], spec["weight_sets"]):
        normalized = np.asarray(weights, dtype=float)
        normalized = normalized / normalized.sum()
        yield Candidate(int(vol_window), int(corr_window), float(threshold), int(persistence), tuple(float(value) for value in normalized))


def circular_shift_p_value(labels: np.ndarray, scores: np.ndarray, trials: int, seed: int, minimum_shift: int = 60) -> float | None:
    observed = _auc(labels, scores)
    if observed is None or len(labels) < minimum_shift * 3:
        return None
    rng = np.random.default_rng(seed)
    valid_shifts = np.arange(minimum_shift, len(labels) - minimum_shift)
    if len(valid_shifts) == 0:
        return None
    null_values = []
    for shift in rng.choice(valid_shifts, size=trials, replace=True):
        value = _auc(labels, np.roll(scores, int(shift)))
        if value is not None:
            null_values.append(value)
    if not null_values:
        return None
    return float((1 + sum(value >= observed for value in null_values)) / (1 + len(null_values)))


def optimize(prices: pd.DataFrame, spec: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    folds = _development_folds(prices, str(spec["development_end"]))
    feature_cache: dict[tuple[int, int], pd.DataFrame] = {}
    rankings = []
    for candidate in iter_candidates(spec):
        key = (candidate.vol_window, candidate.corr_window)
        if key not in feature_cache:
            feature_cache[key] = build_feature_frame(prices, spec, *key)
        frame = feature_cache[key]
        score = make_score(frame, candidate)
        alert, threshold = make_alerts(score, candidate, spec)
        fold_metrics = [evaluate_period(frame, score, alert, spec, start, end) for start, end in folds]
        valid_recall = [item["event_recall"] for item in fold_metrics if item["event_recall"] is not None]
        rankings.append({
            "candidate": candidate,
            "mean_objective": float(np.mean([item["objective"] for item in fold_metrics])),
            "median_objective": float(np.median([item["objective"] for item in fold_metrics])),
            "development_event_recall": float(np.mean(valid_recall)) if valid_recall else 0.0,
            "development_false_alarms_per_year": float(np.mean([item["false_alarms_per_year"] for item in fold_metrics])),
            "folds": fold_metrics, "score": score, "alert": alert, "threshold": threshold, "frame": frame,
        })

    gates = spec["selection_gates"]
    eligible = [row for row in rankings if row["development_event_recall"] >= float(gates["development_event_recall_gte"]) and row["development_false_alarms_per_year"] <= float(gates["development_false_alarms_per_year_lte"])]
    best = max(eligible or rankings, key=lambda row: (row["median_objective"], row["development_event_recall"], -row["development_false_alarms_per_year"], -row["candidate"].threshold_quantile))

    candidate = best["candidate"]
    frame, score, alert, threshold = best["frame"], best["score"], best["alert"], best["threshold"]
    holdout_start = str(spec["locked_holdout_start"])
    holdout = evaluate_period(frame, score, alert, spec, holdout_start, None)
    all_events = crisis_crossings(frame["drawdown_252"], float(spec["event_drawdown_lte"]), int(spec["event_cooldown_rows"]))
    lo = int(np.flatnonzero(np.asarray(frame.index >= pd.Timestamp(holdout_start)))[0])
    labels = future_event_labels(len(frame), all_events, int(spec["forecast_window_rows"]))[lo:]
    holdout_scores = score.iloc[lo:].to_numpy(dtype=float)
    p_value = circular_shift_p_value(labels, holdout_scores, int(spec["circular_shift_trials"]), int(spec["random_seed"]), int(spec["forecast_window_rows"]))
    vix_auc = _auc(labels, frame["vix_percentile"].iloc[lo:].to_numpy(dtype=float))
    auc_advantage = None if holdout["auc"] is None or vix_auc is None else float(holdout["auc"] - vix_auc)

    confirmation = spec["confirmation_gates"]
    checks = {
        "minimum_holdout_events": holdout["events"] >= int(confirmation["minimum_holdout_events"]),
        "holdout_event_recall": holdout["event_recall"] is not None and holdout["event_recall"] >= float(confirmation["holdout_event_recall_gte"]),
        "holdout_false_alarms_per_year": holdout["false_alarms_per_year"] <= float(confirmation["holdout_false_alarms_per_year_lte"]),
        "circular_shift_p": p_value is not None and p_value < float(confirmation["circular_shift_p_lt"]),
        "auc_advantage_over_vix": auc_advantage is not None and auc_advantage >= float(confirmation["auc_advantage_over_vix_gte"]),
    }
    status = "OPTIMAL_CONFIRMED" if all(checks.values()) else "NO_CONFIRMED_OPTIMUM"
    top_candidates = []
    for rank, row in enumerate(sorted(rankings, key=lambda item: item["median_objective"], reverse=True)[:20], start=1):
        top_candidates.append({"rank": rank, **row["candidate"].as_dict(), "median_objective": row["median_objective"], "mean_objective": row["mean_objective"], "development_event_recall": row["development_event_recall"], "development_false_alarms_per_year": row["development_false_alarms_per_year"]})

    result = {
        "spec_id": spec["spec_id"], "status": status,
        "selection_pool": "gated_candidates" if eligible else "all_candidates_no_gate_passed",
        "candidate_count": len(rankings), "eligible_candidate_count": len(eligible),
        "best_candidate": candidate.as_dict(),
        "development": {"folds": best["folds"], "median_objective": best["median_objective"], "mean_objective": best["mean_objective"], "event_recall": best["development_event_recall"], "false_alarms_per_year": best["development_false_alarms_per_year"]},
        "locked_holdout": holdout, "vix_baseline": {"auc": vix_auc},
        "auc_advantage_over_vix": auc_advantage, "circular_shift_p_value": p_value,
        "confirmation_checks": checks,
        "interpretation": "The pre-registered gates were all satisfied on the untouched holdout." if status == "OPTIMAL_CONFIRMED" else "At least one pre-registered confirmation gate failed; no optimal value is claimed.",
        "top_candidates": top_candidates,
    }
    timeseries = pd.DataFrame({"date": frame.index.date.astype(str), "close": frame["close"].to_numpy(), "drawdown_252": frame["drawdown_252"].to_numpy(), "risk_score": score.to_numpy(), "dynamic_threshold": threshold.to_numpy(), "alert": alert.astype(int).to_numpy()})
    return result, timeseries
