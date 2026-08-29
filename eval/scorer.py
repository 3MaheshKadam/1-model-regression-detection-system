"""
eval/scorer.py

Reads a raw run (from runner.py) and scores each case across dimensions:
  - length:   programmatic check against expected_criteria.max_words
  - accuracy: LLM-as-judge check against expected_criteria.must_include /
              must_not_include (semantic, not exact-string — model wording
              varies run to run, so this can't be a plain string match)
  - latency / cost: passed through from the raw run, unchanged

A case's overall_pass is True only if BOTH length and accuracy pass.

Usage:
    python eval/scorer.py                                   # score results/latest_raw.json
    python eval/scorer.py --raw results/raw_2026....json     # score a specific run
    python eval/scorer.py --judge-model openai/gpt-oss-120b
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from llm_client import PROVIDERS, call_with_retry, estimate_cost, get_client

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
DEFAULT_RAW = RESULTS_DIR / "latest_raw.json"

JUDGE_PROMPT_TEMPLATE = """You are a strict fact-checking judge for a news summarizer eval. \
You will be shown the ORIGINAL ARTICLE, the SUMMARY a model produced from it, and a CHECKLIST \
of criteria the summary must satisfy. Judge only what is asked — do not grade writing style.

For each item in "must_include": does the SUMMARY convey that fact/idea, even if worded \
differently than the article? Mark satisfied=true only if a reader of the summary alone would \
come away knowing that fact.

For each item in "must_not_include": does the SUMMARY violate this (state it, imply it, or \
fail to avoid it as instructed)? Mark violated=true if the summary does the disallowed thing.

Respond with ONLY a single JSON object, no other text, no markdown code fences, in exactly \
this shape:
{{
  "must_include_results": [{{"criterion": "...", "satisfied": true, "reason": "..."}}],
  "must_not_include_results": [{{"criterion": "...", "violated": false, "reason": "..."}}],
  "overall_accuracy_pass": true
}}
"overall_accuracy_pass" must be true only if EVERY must_include item is satisfied AND NO \
must_not_include item is violated.

ORIGINAL ARTICLE:
\"\"\"
{article}
\"\"\"

SUMMARY:
\"\"\"
{summary}
\"\"\"

CHECKLIST:
{checklist}
"""


def build_judge_prompt(article: str, summary: str, criteria: dict) -> str:
    checklist = json.dumps(
        {
            "must_include": criteria.get("must_include", []),
            "must_not_include": criteria.get("must_not_include", []),
        },
        indent=2,
    )
    return JUDGE_PROMPT_TEMPLATE.format(article=article, summary=summary, checklist=checklist)


def parse_judge_response(text: str) -> dict:
    """Judges sometimes wrap JSON in ```json fences despite instructions — strip those first."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def score_length(output: str | None, max_words: int) -> dict:
    word_count = len(output.split()) if output else 0
    return {
        "word_count": word_count,
        "max_words": max_words,
        "length_pass": output is not None and word_count <= max_words,
    }


def score_case(client, judge_model: str, case: dict) -> dict:
    output = case.get("output")
    criteria = case["expected_criteria"]
    length_result = score_length(output, criteria["max_words"])

    if output is None:
        # The summarizer itself failed (e.g. API error) — nothing to judge.
        return {
            "id": case["id"],
            "category": case["category"],
            "tier": case.get("tier"),
            **length_result,
            "accuracy_pass": False,
            "judge_error": "no output to judge (runner failed on this case)",
            "overall_pass": False,
            "latency_ms": case.get("latency_ms"),
            "summarizer_tokens": case.get("total_tokens"),
            "summarizer_cost_usd": case.get("estimated_cost_usd"),
            "judge_tokens": None,
            "judge_cost_usd": None,
        }

    prompt = build_judge_prompt(case["input"], output, criteria)
    response, judge_latency_ms = call_with_retry(client, judge_model, prompt)
    raw_judge_text = (response.choices[0].message.content or "").strip()
    usage = response.usage

    try:
        judged = parse_judge_response(raw_judge_text)
        accuracy_pass = bool(judged.get("overall_accuracy_pass"))
        judge_error = None
    except (json.JSONDecodeError, AttributeError) as exc:
        judged = {"must_include_results": [], "must_not_include_results": []}
        accuracy_pass = False  # unparseable judge output is treated as a failed case, not a pass
        judge_error = f"could not parse judge response: {exc}"

    return {
        "id": case["id"],
        "category": case["category"],
        "tier": case.get("tier"),
        **length_result,
        "accuracy_pass": accuracy_pass,
        "must_include_results": judged.get("must_include_results", []),
        "must_not_include_results": judged.get("must_not_include_results", []),
        "judge_error": judge_error,
        "judge_raw_response": raw_judge_text if judge_error else None,
        "overall_pass": length_result["length_pass"] and accuracy_pass,
        "latency_ms": case.get("latency_ms"),
        "summarizer_tokens": case.get("total_tokens"),
        "summarizer_cost_usd": case.get("estimated_cost_usd"),
        "judge_tokens": usage.total_tokens if usage else None,
        "judge_cost_usd": estimate_cost(
            judge_model, usage.prompt_tokens, usage.completion_tokens
        ) if usage else None,
    }


def summarize_scores(scored: list[dict]) -> dict:
    total = len(scored)
    passed = sum(1 for s in scored if s["overall_pass"])
    by_category: dict[str, dict] = {}
    for s in scored:
        cat = by_category.setdefault(s["category"], {"total": 0, "passed": 0})
        cat["total"] += 1
        cat["passed"] += int(s["overall_pass"])

    latencies = [s["latency_ms"] for s in scored if s.get("latency_ms") is not None]
    total_cost = sum(
        (s.get("summarizer_cost_usd") or 0) + (s.get("judge_cost_usd") or 0) for s in scored
    )

    return {
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else None,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "total_estimated_cost_usd": round(total_cost, 6),
        "by_category": {
            cat: {**v, "pass_rate": round(v["passed"] / v["total"], 4)}
            for cat, v in by_category.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a raw eval run against the golden dataset's criteria.")
    parser.add_argument("--raw", default=str(DEFAULT_RAW), help="Path to a raw results JSON from runner.py")
    parser.add_argument("--provider", default="groq", choices=sorted(PROVIDERS), help="Which API to call for judging")
    parser.add_argument("--judge-model", default=None, help="Judge model (defaults to the provider's default judge model)")
    parser.add_argument("--out", default=None, help="Output path (default: results/scored_<timestamp>.json)")
    args = parser.parse_args()

    load_dotenv()
    client, provider = get_client(args.provider)
    judge_model = args.judge_model or provider["default_judge_model"]

    raw_path = Path(args.raw)
    raw_record = json.loads(raw_path.read_text(encoding="utf-8"))
    cases = raw_record["results"]

    print(f"Scoring {len(cases)} case(s) from {raw_path.name} | judge={judge_model}")

    scored = []
    for i, case in enumerate(cases, start=1):
        print(f"  [{i}/{len(cases)}] {case['id']} ...", end=" ", flush=True)
        try:
            result = score_case(client, judge_model, case)
            scored.append(result)
            mark = "PASS" if result["overall_pass"] else "FAIL"
            print(mark)
        except Exception as exc:
            print(f"ERROR: {exc}")
            scored.append({
                "id": case["id"],
                "category": case["category"],
                "tier": case.get("tier"),
                "overall_pass": False,
                "judge_error": str(exc),
            })

    summary = summarize_scores(scored)

    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"scored_{timestamp}.json"

    score_record = {
        "timestamp": timestamp,
        "raw_source": str(raw_path),
        "judge_model": judge_model,
        "summary": summary,
        "cases": scored,
    }

    payload = json.dumps(score_record, indent=2, ensure_ascii=False)
    out_path.write_text(payload, encoding="utf-8")
    (RESULTS_DIR / "latest_scored.json").write_text(payload, encoding="utf-8")

    print(f"\n{summary['passed']}/{summary['total_cases']} passed ({summary['pass_rate']:.0%})")
    print(f"avg latency: {summary['avg_latency_ms']} ms | total cost: ${summary['total_estimated_cost_usd']}")
    print(f"Saved to {out_path} (and results/latest_scored.json)")


if __name__ == "__main__":
    main()
