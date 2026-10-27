"""N-day first-purchase cohort, normalized order snapshots; Python 3.9+."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from method_io import InputError, keys, load, nonempty, payload_hash

METHOD = "cohort-repurchase/0.2.0"


def timestamp(value):
    if not isinstance(value, str):
        raise InputError("timestamp must be an ISO-8601 string with offset")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError("invalid ISO-8601 timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise InputError("timestamp offset is required")
    try:
        return dt.astimezone(timezone.utc)
    except OverflowError as exc:
        raise InputError("timestamp outside UTC representable range") from exc


def calculate(payload):
    keys(payload, ("schema_version", "source_ref", "definition_ref", "timezone",
                   "cutoff", "validity_as_of", "history_complete", "window_days",
                   "cohort_start", "cohort_end", "orders", "bounds_policy"), ("stability_ref",))
    if payload["schema_version"] != "2":
        raise InputError("unsupported schema_version")
    if payload["bounds_policy"] not in ("unverified", "fixed_cohort_irreversible_success"):
        raise InputError("unknown bounds_policy")
    if payload["bounds_policy"] == "fixed_cohort_irreversible_success":
        nonempty(payload.get("stability_ref"), "stability_ref")
    elif "stability_ref" in payload:
        raise InputError("unverified policy must not supply stability_ref")
    for field in ("source_ref", "definition_ref", "timezone"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise InputError(field + " must be a nonempty string")
    try:
        ZoneInfo(payload["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InputError("unknown business timezone") from exc
    if type(payload["history_complete"]) is not bool:
        raise InputError("history_complete must be boolean")
    if type(payload["window_days"]) is not int or payload["window_days"] <= 0:
        raise InputError("window_days must be a positive integer")
    try:
        window = timedelta(days=payload["window_days"])
    except OverflowError as exc:
        raise InputError("window_days out of range") from exc
    start, end, cutoff, validity = map(timestamp, (payload["cohort_start"],
        payload["cohort_end"], payload["cutoff"], payload["validity_as_of"]))
    if not start < end <= cutoff:
        raise InputError("require cohort_start < cohort_end <= cutoff")
    if validity != cutoff:
        raise InputError("order validity must be normalized as of cutoff")
    if not isinstance(payload["orders"], list):
        raise InputError("orders must be a list")
    base = {"method_version": METHOD, "source_ref": payload["source_ref"],
            "definition_ref": payload["definition_ref"],
            "input_sha256": payload_hash(payload),
            "bounds_policy": payload["bounds_policy"],
            "stability_ref": payload.get("stability_ref"),
            "scope": {k: payload[k] for k in ("cohort_start", "cohort_end", "cutoff",
                       "timezone", "window_days")}}
    if not payload["history_complete"]:
        return {**base, "status": "unsupported", "reason": "first_purchase_history_not_verified"}

    seen, by_customer = {}, defaultdict(list)
    quality = Counter({"input_rows": len(payload["orders"]), "exact_duplicates": 0,
                       "invalid_orders": 0, "after_cutoff": 0})
    for row in payload["orders"]:
        keys(row, ("order_id", "customer_id", "purchased_at", "valid"))
        for field in ("order_id", "customer_id"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise InputError(field + " must be a nonempty string")
        if type(row["valid"]) is not bool:
            raise InputError("valid must be boolean; normalize cancellation/refunds first")
        event = (row["customer_id"], timestamp(row["purchased_at"]), row["valid"])
        oid = row["order_id"]
        if oid in seen:
            if seen[oid] != event:
                raise InputError("conflicting duplicate order_id; reconcile snapshot first")
            quality["exact_duplicates"] += 1
            continue
        seen[oid] = event
        customer, when, valid = event
        if when > cutoff:
            quality["after_cutoff"] += 1
        elif not valid:
            quality["invalid_orders"] += 1
        else:
            by_customer[customer].append(when)
    counts = Counter({"cohort_customers": 0, "mature_customers": 0,
                      "mature_repeat_customers": 0, "immature_customers": 0,
                      "immature_observed_repeat_customers": 0})
    for dates in by_customer.values():
        dates.sort()
        first = dates[0]
        if not start <= first < end:
            continue
        try:
            deadline = first + window
        except OverflowError as exc:
            raise InputError("purchase date plus window out of range") from exc
        mature = deadline <= cutoff
        repeat = len(dates) > 1 and dates[1] <= deadline
        counts["cohort_customers"] += 1
        counts["mature_customers" if mature else "immature_customers"] += 1
        if repeat:
            counts["mature_repeat_customers" if mature else "immature_observed_repeat_customers"] += 1
    total, mature = counts["cohort_customers"], counts["mature_customers"]
    observed = counts["mature_repeat_customers"] + counts["immature_observed_repeat_customers"]
    unresolved = counts["immature_customers"] - counts["immature_observed_repeat_customers"]
    stable = payload["bounds_policy"] == "fixed_cohort_irreversible_success"
    return {**base, "status": "ok", "counts": dict(counts), "quality": dict(quality),
            "mature_repeat_rate": counts["mature_repeat_customers"] / mature if mature else None,
            "full_cohort_rate_bounds": {"lower": observed / total if total else None,
                                       "upper": (observed + unresolved) / total if total else None} if stable else None,
            "bounds_type": "conditional_identification_bounds" if stable else "not_established",
            "warnings": (["immature_followup"] if counts["immature_customers"] else [])
                        + (["empty_cohort"] if not total else [])
                        + ([] if stable else ["future_refunds_or_history_revisions_not_ruled_out"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="normalized JSON snapshot path")
    args = parser.parse_args()
    try:
        payload = load(args.input)
        result = calculate(payload)
    except (InputError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        result = {"status": "error", "method_version": METHOD, "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0 if result["status"] == "ok" else (2 if result["status"] == "unsupported" else 1)


if __name__ == "__main__":
    sys.exit(main())
