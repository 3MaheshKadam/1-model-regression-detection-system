"""
eval/compare.py

Compares a current scored run (from scorer.py) against the stored baseline
(results/baseline.json) and flags regressions. This is what step 6's GitHub
Actions workflow will call to decide whether a PR should fail its check.

A case is a REGRESSION if it passed in the baseline but fails now.
A case is an IMPROVEMENT if it failed in the baseline but passes now.
Aggregate latency/cost are flagged separately if they worsen beyond a
configurable percentage threshold — a single case's latency wobble
shouldn't fail a PR, but a systemic slowdown/cost increase should.

Exit code: 0 if no regressions found, 1 if any regression is found
(so CI can use this script's exit code directly).

Usage:
    python eval/compare.py                                    # current = results/latest_scored.json
    python eval/compare.py --current results/scored_....json
    python eval/compare.py --latency-threshold-pct 30 --cost-threshold-pct 30
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
DEFAULT_CURRENT = RESULTS_DIR / "latest_scored.json"
DEFAULT_BASELINE = RESULTS_DIR / "baseline.json"


def load_cases_by_id(path: Path) -> dict[str, dict]:
    record = json.loads(path.read_text(encoding="utf-8"))
    return {c["id"]: c for c in record["cases"]}


def compare_cases(baseline: dict[str, dict], current: dict[str, dict]) -> list[dict]:
    diffs = []
    all_ids = sorted(set(baseline) | set(current))

    for case_id in all_ids:
        base = baseline.get(case_id)
        curr = current.get(case_id)

        if base is None:
            diffs.append({"id": case_id, "status": "NEW_CASE", "category": curr.get("category")})
            continue
        if curr is None:
            diffs.append({"id": case_id, "status": "REMOVED_CASE", "category": base.get("category")})
            continue

        base_pass = bool(base.get("overall_pass"))
        curr_pass = bool(curr.get("overall_pass"))
        base_accuracy = bool(base.get("accuracy_pass"))
        curr_accuracy = bool(curr.get("accuracy_pass"))
        base_length = bool(base.get("length_pass"))
        curr_length = bool(curr.get("length_pass"))

        # Check each dimension independently, not just overall_pass — a case
        # that was already failing (e.g. on length) can still regress on a
        # DIFFERENT dimension (e.g. accuracy), and overall_pass alone would
        # mask that as "still failing" when something real actually broke.
        accuracy_regressed = base_accuracy and not curr_accuracy
        length_regressed = base_length and not curr_length
        overall_regressed = base_pass and not curr_pass

        if accuracy_regressed or length_regressed or overall_regressed:
            status = "REGRESSION"
        elif not base_pass and curr_pass:
            status = "IMPROVEMENT"
        elif curr_pass:
            status = "STILL_PASSING"
        else:
            status = "STILL_FAILING"

        diffs.append({
            "id": case_id,
            "status": status,
            "category": curr.get("category"),
            "baseline_pass": base_pass,
            "current_pass": curr_pass,
            "baseline_length_pass": base_length,
            "current_length_pass": curr_length,
            "baseline_accuracy_pass": base_accuracy,
            "current_accuracy_pass": curr_accuracy,
            "accuracy_regressed": accuracy_regressed,
            "length_regressed": length_regressed,
            "overall_regressed": overall_regressed,
            "baseline_latency_ms": base.get("latency_ms"),
            "current_latency_ms": curr.get("latency_ms"),
        })

    return diffs


def pct_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return round((new - old) / old * 100, 1)


def compare_aggregates(
    baseline_summary: dict, current_summary: dict,
    latency_threshold_pct: float, cost_threshold_pct: float,
) -> dict:
    latency_delta_pct = pct_change(baseline_summary.get("avg_latency_ms"), current_summary.get("avg_latency_ms"))
    cost_delta_pct = pct_change(
        baseline_summary.get("total_estimated_cost_usd"), current_summary.get("total_estimated_cost_usd")
    )

    return {
        "baseline_pass_rate": baseline_summary.get("pass_rate"),
        "current_pass_rate": current_summary.get("pass_rate"),
        "baseline_avg_latency_ms": baseline_summary.get("avg_latency_ms"),
        "current_avg_latency_ms": current_summary.get("avg_latency_ms"),
        "latency_delta_pct": latency_delta_pct,
        "latency_regressed": latency_delta_pct is not None and latency_delta_pct > latency_threshold_pct,
        "baseline_total_cost_usd": baseline_summary.get("total_estimated_cost_usd"),
        "current_total_cost_usd": current_summary.get("total_estimated_cost_usd"),
        "cost_delta_pct": cost_delta_pct,
        "cost_regressed": cost_delta_pct is not None and cost_delta_pct > cost_threshold_pct,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare a scored run against the baseline and flag regressions.")
    parser.add_argument("--current", default=str(DEFAULT_CURRENT), help="Path to the current scored run JSON")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE), help="Path to the baseline scored run JSON")
    parser.add_argument("--latency-threshold-pct", type=float, default=50.0,
                         help="Flag as regressed if avg latency worsens by more than this percent")
    parser.add_argument("--cost-threshold-pct", type=float, default=50.0,
                         help="Flag as regressed if total cost worsens by more than this percent")
    parser.add_argument("--out", default=None, help="Optional path to save the full comparison report JSON")
    args = parser.parse_args()

    current_path = Path(args.current)
    baseline_path = Path(args.baseline)

    if not baseline_path.exists():
        raise SystemExit(
            f"No baseline found at {baseline_path}. Run scorer.py once and save its output as "
            f"results/baseline.json before compare.py has anything to compare against."
        )

    baseline_record = json.loads(baseline_path.read_text(encoding="utf-8"))
    current_record = json.loads(current_path.read_text(encoding="utf-8"))

    baseline_cases = {c["id"]: c for c in baseline_record["cases"]}
    current_cases = {c["id"]: c for c in current_record["cases"]}

    case_diffs = compare_cases(baseline_cases, current_cases)
    aggregate = compare_aggregates(
        baseline_record["summary"], current_record["summary"],
        args.latency_threshold_pct, args.cost_threshold_pct,
    )

    regressions = [d for d in case_diffs if d["status"] == "REGRESSION"]
    improvements = [d for d in case_diffs if d["status"] == "IMPROVEMENT"]

    print(f"Comparing {current_path.name} against {baseline_path.name}")
    print(f"  baseline pass rate: {aggregate['baseline_pass_rate']:.0%}" if aggregate['baseline_pass_rate'] is not None else "  baseline pass rate: n/a")
    print(f"  current  pass rate: {aggregate['current_pass_rate']:.0%}" if aggregate['current_pass_rate'] is not None else "  current  pass rate: n/a")
    print(f"  avg latency: {aggregate['baseline_avg_latency_ms']} ms -> {aggregate['current_avg_latency_ms']} ms "
          f"({aggregate['latency_delta_pct']}%)" if aggregate['latency_delta_pct'] is not None else "  avg latency: n/a")
    print(f"  total cost:  ${aggregate['baseline_total_cost_usd']} -> ${aggregate['current_total_cost_usd']} "
          f"({aggregate['cost_delta_pct']}%)" if aggregate['cost_delta_pct'] is not None else "  total cost: n/a")
    print()

    if regressions:
        print(f"REGRESSIONS ({len(regressions)}):")
        for d in regressions:
            reason = []
            if d["length_regressed"]:
                reason.append("length")
            if d["accuracy_regressed"]:
                reason.append("accuracy")
            if d["overall_regressed"] and not reason:
                reason.append("overall")
            print(f"  - {d['id']} ({d['category']}): regressed on {', '.join(reason)}")
    else:
        print("No per-case regressions.")

    if improvements:
        print(f"\nIMPROVEMENTS ({len(improvements)}):")
        for d in improvements:
            print(f"  - {d['id']} ({d['category']})")

    if aggregate["latency_regressed"]:
        print(f"\nLATENCY REGRESSION: avg latency worsened by {aggregate['latency_delta_pct']}% "
              f"(threshold: {args.latency_threshold_pct}%)")
    if aggregate["cost_regressed"]:
        print(f"\nCOST REGRESSION: total cost worsened by {aggregate['cost_delta_pct']}% "
              f"(threshold: {args.cost_threshold_pct}%)")

    report = {
        "baseline_file": str(baseline_path),
        "current_file": str(current_path),
        "aggregate": aggregate,
        "case_diffs": case_diffs,
        "regressions": regressions,
        "improvements": improvements,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull report saved to {args.out}")

    any_regression = bool(regressions) or aggregate["latency_regressed"] or aggregate["cost_regressed"]
    print(f"\n{'REGRESSION DETECTED' if any_regression else 'NO REGRESSION DETECTED'}")
    raise SystemExit(1 if any_regression else 0)


if __name__ == "__main__":
    main()
