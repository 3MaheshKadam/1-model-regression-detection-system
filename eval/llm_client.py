"""
eval/llm_client.py

Shared LLM-calling plumbing used by both runner.py (runs the summarizer) and
scorer.py (runs the LLM-as-judge). Kept in one place so provider config,
retry/backoff behavior, and model-specific fixes only need to live once.

Supports two providers behind the same OpenAI-compatible client:
  - groq   (default): free tier, https://console.groq.com — no card required
  - openai: paid, requires OPENAI_API_KEY with billing/credits set up
"""

from __future__ import annotations

import os
import re
import time

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

# Transient failures worth retrying: rate limits, dropped connections, brief
# server hiccups. NOT retried: auth errors, bad requests, model-not-found —
# those need a human to fix, not a backoff loop.
RETRYABLE_ERRORS = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)

# ---- Providers -------------------------------------------------------------
# Groq exposes an OpenAI-compatible endpoint, so the same `openai` SDK client
# works for both — only base_url, the API key env var, and the default model
# differ. See https://console.groq.com/docs/models for current model names.
PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "default_model": "openai/gpt-oss-20b",
        "default_judge_model": "openai/gpt-oss-120b",  # bigger model for judging
    },
    "openai": {
        "base_url": None,  # SDK default (api.openai.com)
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "default_judge_model": "gpt-4o",
    },
}

# ---- Pricing (USD per 1M tokens) ------------------------------------------
# Used only to *estimate* cost per call from the response's usage field.
# Groq's free tier is $0 — update these if you move to a paid Groq plan.
PRICING = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/gpt-oss-20b": {"input": 0.0, "output": 0.0},
    "openai/gpt-oss-120b": {"input": 0.0, "output": 0.0},
    "qwen/qwen3.8-27b": {"input": 0.0, "output": 0.0},
}


def get_client(provider_name: str) -> tuple[OpenAI, dict]:
    """Build an OpenAI-SDK client configured for the given provider name."""
    provider = PROVIDERS[provider_name]
    api_key = os.getenv(provider["api_key_env"])
    if not api_key:
        hint = (
            " — get a free key at https://console.groq.com/keys"
            if provider_name == "groq" else ""
        )
        raise SystemExit(
            f"{provider['api_key_env']} not set. Add it to your .env file (see .env.example){hint}."
        )
    client = OpenAI(api_key=api_key, base_url=provider["base_url"])
    return client, provider


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    rates = PRICING.get(model)
    if not rates:
        return None
    return (prompt_tokens / 1_000_000) * rates["input"] + (completion_tokens / 1_000_000) * rates["output"]


def _parse_retry_after(message: str) -> float | None:
    """Pull a 'try again in 6.3s' style hint out of a rate-limit error message."""
    match = re.search(r"try again in ([\d.]+)s", message)
    return float(match.group(1)) + 0.5 if match else None


def extra_model_kwargs(model: str) -> dict:
    """
    Model-specific request tweaks.

    openai/gpt-oss-* (Groq) are reasoning models: left at default effort, they
    can spend their entire completion-token budget on hidden reasoning and
    return an EMPTY final answer for some inputs. Forcing low reasoning effort
    fixes this for tasks as simple as summarization/judging, and cuts token
    usage dramatically (observed ~2000 tokens -> ~35-100 tokens per case).
    """
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}
    return {}


def call_with_retry(client: OpenAI, model: str, prompt: str, max_retries: int = 5):
    """Call the chat API, retrying with backoff on rate limits (common on free tiers).

    Returns (response, latency_ms) where latency_ms times only the successful
    attempt itself — retry backoff sleeps are excluded so they don't pollute
    the latency scoring dimension.
    """
    for attempt in range(max_retries):
        start = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,  # deterministic-as-possible output for reproducible evals
                **extra_model_kwargs(model),
            )
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            return response, latency_ms
        except RETRYABLE_ERRORS as exc:
            if attempt == max_retries - 1:
                raise
            wait = _parse_retry_after(str(exc)) or (2 ** attempt)
            reason = type(exc).__name__
            print(f"[{reason}, waiting {wait:.1f}s, retry {attempt + 1}/{max_retries}]", end=" ", flush=True)
            time.sleep(wait)
