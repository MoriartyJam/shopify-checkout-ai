"""Aggregate anonymized checkout recovery outcomes for administrators and ML."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


FINAL_RESULTS = {"recovered", "not_recovered"}


def build_outcome_analytics(
    interventions: Iterable[dict[str, Any]],
    checkout_cases: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Build per-action recovery statistics without using customer PII.

    Only the latest intervention for each checkout is counted. This prevents an
    administrator correcting a pending record from inflating the statistics.
    Completed Shopify checkouts can automatically resolve a pending action as
    recovered, while abandoned cases remain pending until the observation
    window is closed explicitly.
    """
    cases_by_id = {case["case_id"]: case for case in checkout_cases}
    latest_by_case: dict[str, dict[str, Any]] = {}

    for intervention in interventions:
        case_id = intervention.get("case_id")
        if not case_id or case_id not in cases_by_id:
            continue
        previous = latest_by_case.get(case_id)
        current_time = intervention.get("recorded_at", "")
        if previous is None or current_time >= previous.get("recorded_at", ""):
            latest_by_case[case_id] = intervention

    action_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "recovered": 0, "not_recovered": 0, "pending": 0}
    )
    records = []

    for case_id, intervention in sorted(latest_by_case.items()):
        case = cases_by_id[case_id]
        result = intervention.get("result", "pending")
        result_source = "administrator"
        if result not in FINAL_RESULTS and case.get("outcome") == "completed":
            result = "recovered"
            result_source = "shopify"

        action = intervention["action"]
        bucket = action_totals[action]
        bucket["total"] += 1
        bucket[result if result in FINAL_RESULTS else "pending"] += 1
        records.append(
            {
                "case_id": case_id,
                "action": action,
                "result": result,
                "result_source": result_source,
                "customer_segment": case.get("customer_segment", "unknown"),
                "detected_pattern": case.get("detected_pattern", "unknown"),
            }
        )

    by_action = []
    for action, counts in sorted(action_totals.items()):
        observed = counts["recovered"] + counts["not_recovered"]
        by_action.append(
            {
                "action": action,
                **counts,
                "recovery_rate": round(counts["recovered"] / observed, 4)
                if observed
                else None,
            }
        )

    recovered = sum(item["recovered"] for item in by_action)
    not_recovered = sum(item["not_recovered"] for item in by_action)
    observed = recovered + not_recovered
    return {
        "summary": {
            "total_interventions": len(records),
            "recovered": recovered,
            "not_recovered": not_recovered,
            "pending": sum(item["pending"] for item in by_action),
            "recovery_rate": round(recovered / observed, 4) if observed else None,
        },
        "by_action": by_action,
        "records": records,
    }
