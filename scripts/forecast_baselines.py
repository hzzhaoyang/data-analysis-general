"""Fixed naive/seasonal-naive forecasts and rolling evaluation of one snapshot."""
import argparse
from datetime import date, timedelta
import json
import math
import sys
from method_io import InputError, keys, load, nonempty, payload_hash, positive_int

METHOD = "forecast-baselines/0.2.0"


def day(value):
    if not isinstance(value, str):
        raise InputError("date must be YYYY-MM-DD")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise InputError("invalid date") from exc
    if result.isoformat() != value:
        raise InputError("date must be YYYY-MM-DD")
    return result


def predictions(train, horizon, season):
    return {"naive": [train[-1]] * horizon,
            "seasonal_naive": [train[-season:][i % season] for i in range(horizon)]}


def average(values):
    try:
        result = math.fsum(values) / len(values)
    except OverflowError as exc:
        raise InputError("numeric range exceeded while computing metrics") from exc
    if not math.isfinite(result):
        raise InputError("numeric range exceeded while computing metrics")
    return result


def calculate(payload):
    keys(payload, ("schema_version", "source_ref", "definition_ref", "entity_ref",
        "unit", "cutoff", "frequency_days", "season_length", "horizon",
        "backtest_windows", "step_size", "target", "observations"))
    if payload["schema_version"] != "1":
        raise InputError("unsupported schema_version")
    for field in ("source_ref", "definition_ref", "entity_ref", "unit"):
        nonempty(payload[field], field)
    for field in ("frequency_days", "season_length", "horizon", "backtest_windows", "step_size"):
        positive_int(payload[field], field)
    cutoff = day(payload["cutoff"])
    base = {"method_version": METHOD, "input_sha256": payload_hash(payload),
            **{k: payload[k] for k in ("source_ref", "definition_ref", "entity_ref",
               "unit", "cutoff", "frequency_days", "season_length", "horizon")},
            "backtest_kind": "retrospective_current_snapshot",
            "historical_as_of_availability_verified": False,
            "prediction_intervals": None, "selected_model": None}
    if payload["target"] != "observed_sales":
        return {**base, "status": "unsupported", "reason": "only_observed_sales_supported_not_latent_demand"}
    rows = payload["observations"]
    if not isinstance(rows, list):
        raise InputError("observations must be a list")
    values, dates, stockouts, unknown_stockouts, missing = [], [], 0, 0, False
    for row in rows:
        keys(row, ("date", "value", "stockout"))
        dt = day(row["date"])
        if dt > cutoff:
            raise InputError("observation is after cutoff")
        if dates and (dt - dates[-1]).days != payload["frequency_days"]:
            raise InputError("dates must be ordered, unique, and at the declared fixed day interval")
        value = row["value"]
        if value is None:
            missing = True
        elif type(value) not in (int, float):
            raise InputError("value must be numeric or null; boolean is not numeric")
        else:
            try:
                value = float(value)
            except OverflowError as exc:
                raise InputError("value outside numeric range") from exc
            if not math.isfinite(value):
                raise InputError("value must be finite")
        if row["stockout"] is None:
            unknown_stockouts += 1
        elif type(row["stockout"]) is not bool:
            raise InputError("stockout must be boolean or null for unknown")
        else:
            stockouts += row["stockout"]
        values.append(value)
        dates.append(dt)
    if not dates or dates[-1] != cutoff:
        return {**base, "status": "unsupported", "reason": "complete_series_through_cutoff_required"}
    if missing:
        return {**base, "status": "unsupported", "reason": "missing_values_require_explicit_preprocessing"}
    horizon, season = payload["horizon"], payload["season_length"]
    first_origin = len(values) - horizon - (payload["backtest_windows"] - 1) * payload["step_size"]
    if first_origin < season:
        return {**base, "status": "unsupported", "reason": "insufficient_training_history_for_requested_windows"}
    folds = []
    errors = {name: [[] for _ in range(horizon)] for name in ("naive", "seasonal_naive")}
    for origin in range(first_origin, len(values) - horizon + 1, payload["step_size"]):
        pred = predictions(values[:origin], horizon, season)
        actual = values[origin:origin + horizon]
        folds.append({"train_cutoff": dates[origin - 1].isoformat(),
                      "dates": [d.isoformat() for d in dates[origin:origin + horizon]],
                      "actual": actual, "predictions": pred})
        for name, forecasts in pred.items():
            for i, (forecast, observed) in enumerate(zip(forecasts, actual)):
                error = forecast - observed
                if not math.isfinite(error):
                    raise InputError("numeric range exceeded while computing errors")
                errors[name][i].append(error)
    metrics = {}
    for name, by_horizon in errors.items():
        flat = [e for group in by_horizon for e in group]
        metrics[name] = {"mae": average([abs(e) for e in flat]),
                         "bias_forecast_minus_actual": average(flat),
                         "mae_by_horizon": [average([abs(e) for e in group]) for group in by_horizon],
                         "forecast_origin_target_pairs": len(flat)}
    try:
        future_dates = [(cutoff + timedelta(days=payload["frequency_days"] * (i + 1))).isoformat()
                        for i in range(horizon)]
    except OverflowError as exc:
        raise InputError("forecast dates outside supported date range") from exc
    return {**base, "status": "ok", "forecast_dates": future_dates,
            "forecasts": predictions(values, horizon, season),
            "backtest_windows": folds, "metrics": metrics,
            "stockout_metadata": {"flagged_periods": stockouts, "unknown_periods": unknown_stockouts},
            "warnings": ["snapshot_backtest_does_not_verify_historical_data_revisions",
                         "backtest_windows_may_overlap_and_are_not_independent_samples"]
                         + (["stockout_present_observed_sales_only"] if stockouts else [])
                         + (["stockout_status_unknown_observed_sales_only"] if unknown_stockouts else [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="normalized single-series JSON path")
    args = parser.parse_args()
    try:
        result = calculate(load(args.input))
    except (InputError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        result = {"status": "error", "method_version": METHOD, "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0 if result["status"] == "ok" else (2 if result["status"] == "unsupported" else 1)


if __name__ == "__main__":
    sys.exit(main())
