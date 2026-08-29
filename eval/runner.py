"""
eval/runner.py

Loads the golden dataset, runs each test case through a prompt template and
an LLM API, and saves the RAW outputs (no scoring yet — that's scorer.py).

Provider/client details (Groq vs OpenAI, retry-on-rate-limit, model-specific
fixes) live in llm_client.py and are shared with scorer.py.

Usage:
    python eval/runner.py                                            # full suite, v1 prompt, Groq
    python eval/runner.py --quick                                    # only tier == "quick" cases
    python eval/runner.py --prompt prompts/summarizer_v2.txt         # test a different prompt version
    python eval/runner.py --provider openai --model gpt-4o-mini      # switch back to OpenAI
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from llm_client import PROVIDERS, call_with_retry, estimate_cost, get_client

# ---- Paths ---------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "golden_dataset.json"
DEFAULT_PROMPT = ROOT / "prompts" / "summarizer_v1.txt"
RESULTS_DIR = ROOT / "results"


def load_dataset(path: Path, tier: str | None) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)
    if tier:
        cases = [c for c in cases if c.get("tier") == tier]
    return cases


def build_prompt(template: str, article: str, max_words: int) -> str:
    return template.replace("{{ARTICLE}}", article).replace("{{MAX_WORDS}}", str(max_words))


def run_case(client, model: str, template: str, case: dict) -> dict:
    max_words = case["expected_criteria"]["max_words"]
    prompt = build_prompt(template, case["input"], max_words)

    response, latency_ms = call_with_retry(client, model, prompt)

    output_text = (response.choices[0].message.content or "").strip()
    usage = response.usage

    return {
        "id": case["id"],
        "category": case["category"],
        "tier": case.get("tier"),
        "input": case["input"],
        "expected_criteria": case["expected_criteria"],
        "output": output_text,
        "model": model,
        "latency_ms": latency_ms,
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
        "total_tokens": usage.total_tokens if usage else None,
        "estimated_cost_usd": estimate_cost(
            model, usage.prompt_tokens, usage.completion_tokens
        ) if usage else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the golden dataset through a prompt + model, saving raw outputs."
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Path to golden_dataset.json")
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT), help="Path to the prompt template file")
    parser.add_argument("--provider", default="groq", choices=sorted(PROVIDERS), help="Which API to call")
    parser.add_argument("--model", default=None, help="Model name (defaults to the provider's default model)")
    parser.add_argument("--quick", action="store_true", help="Only run cases tagged tier == 'quick'")
    parser.add_argument(
        "--out", default=None,
        help="Output path for the raw results JSON (default: results/raw_<timestamp>.json)"
    )
    args = parser.parse_args()

    load_dotenv()
    client, provider = get_client(args.provider)
    model = args.model or provider["default_model"]

    dataset_path = Path(args.dataset)
    prompt_path = Path(args.prompt)
    tier = "quick" if args.quick else None

    cases = load_dataset(dataset_path, tier)
    template = prompt_path.read_text(encoding="utf-8")

    print(f"Running {len(cases)} case(s) | prompt={prompt_path.name} | provider={args.provider} | model={model} | tier={tier or 'all'}")

    results = []
    for i, case in enumerate(cases, start=1):
        print(f"  [{i}/{len(cases)}] {case['id']} ({case['category']}) ...", end=" ", flush=True)
        try:
            result = run_case(client, model, template, case)
            results.append(result)
            print(f"ok ({result['latency_ms']} ms, {result['total_tokens']} tokens)")
        except Exception as exc:
            print(f"FAILED: {exc}")
            results.append({
                "id": case["id"],
                "category": case["category"],
                "tier": case.get("tier"),
                "input": case["input"],
                "expected_criteria": case["expected_criteria"],
                "output": None,
                "error": str(exc),
            })

    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"raw_{timestamp}.json"

    run_record = {
        "timestamp": timestamp,
        "prompt_file": str(prompt_path),
        "provider": args.provider,
        "model": model,
        "tier": tier or "all",
        "dataset_file": str(dataset_path),
        "results": results,
    }

    payload = json.dumps(run_record, indent=2, ensure_ascii=False)
    out_path.write_text(payload, encoding="utf-8")
    (RESULTS_DIR / "latest_raw.json").write_text(payload, encoding="utf-8")

    ok = sum(1 for r in results if r.get("output") is not None)
    print(f"\n{ok}/{len(results)} succeeded. Saved to {out_path} (and results/latest_raw.json)")


if __name__ == "__main__":
    main()
