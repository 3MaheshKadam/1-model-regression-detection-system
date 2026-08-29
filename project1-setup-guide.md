# Project 1: Model Regression Detection System — Setup Guide

## What this project is

A CI/CD-style evaluation pipeline for LLM prompts. Every time a prompt changes, the system automatically re-runs it against a golden dataset, scores the output on multiple dimensions, and flags regressions — the same way a test suite catches broken code, except for probabilistic LLM behavior instead of deterministic functions.

**Why this project:** it's Module 2 in your syllabus, sitting directly on top of everything in Module 1 (tokens/context for cost tracking, prompting techniques for building the eval harness itself, and eval theory — golden datasets, LLM-as-judge, eval dimensions — for the actual scoring logic).

---

## Tech stack

| Piece | Tool |
|---|---|
| Language | Python |
| LLM calls | OpenAI API (or Anthropic — pick one to start) |
| CI trigger | GitHub Actions |
| Containerization | Docker |
| Alerting | Slack webhook |

---

## What you're building — feature by feature

### 1. Golden dataset
A hand-curated set of test cases: input prompt/question → expected behavior or acceptable answer range. Not LLM-generated — you write these yourself, including deliberate edge cases (ambiguous inputs, tricky phrasing, known failure-prone cases).

**File:** `golden_dataset.json` (or `.yaml`) — a list of `{id, input, expected_criteria, category}` objects.

### 2. Prompt files (version-controlled)
Your actual prompts, stored as separate files/templates (not hardcoded inline), so changes to them show up as git diffs.

**Files:** `prompts/v1_summarizer.txt`, `prompts/v2_summarizer.txt`, etc. — or a single prompt file that gets versioned via git history/tags rather than filename suffixes (your call).

### 3. Test runner
Loads the golden dataset, runs each test case through the current prompt + model, collects outputs.

**File:** `eval/runner.py`

### 4. Scoring engine — multi-dimensional
For each output, score across dimensions: accuracy (via LLM-as-judge against expected_criteria), latency (time the API call), token usage/cost (from the API response's usage field).

**File:** `eval/scorer.py`

### 5. Diff/comparison logic
Compare current run's scores against a stored baseline (previous run's results) — flag any dimension that regressed beyond a threshold you define.

**File:** `eval/compare.py`

### 6. GitHub Actions workflow
Triggers the eval runner automatically on every PR that touches `prompts/` or `eval/`. Fails the check if a regression is detected.

**File:** `.github/workflows/eval.yml`

### 7. Slack alerting
On regression, post a structured message to a Slack channel via webhook — which test cases failed, which dimension regressed, old score vs new score.

**File:** `eval/notify.py`

### 8. Dockerfile
Containerize the eval runner so it runs identically in CI and locally.

**File:** `Dockerfile`

---

## Suggested project structure

```
model-regression-detector/
├── prompts/
│   └── summarizer_v1.txt
├── golden_dataset.json
├── eval/
│   ├── runner.py
│   ├── scorer.py
│   ├── compare.py
│   └── notify.py
├── results/
│   └── baseline.json        # last known-good scores
├── .github/
│   └── workflows/
│       └── eval.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```

---

## Order of implementation (learn → understand → implement, one piece at a time)

1. **Golden dataset** — write 10–15 real test cases by hand first, before any code exists. This is a thinking exercise, not a coding one.
2. **Prompt file** — pick one real task (e.g., summarization or classification), write the first version as a template.
3. **Test runner** — script that loops through the dataset, calls the API, saves raw outputs.
4. **Scorer** — add LLM-as-judge scoring + latency/cost tracking.
5. **Compare/baseline logic** — store first run as baseline, then simulate a prompt change and confirm it correctly flags a regression.
6. **Dockerize** — wrap it so it runs the same anywhere.
7. **GitHub Actions** — wire it to trigger on PRs.
8. **Slack notify** — last step, since it depends on everything above working first.

---

## What to bring to VS Code / Claude Code

- This document, for context on the full scope.
- Your `.env` setup for API keys (never commit this — add to `.gitignore` immediately).
- Ask Claude Code to scaffold one numbered step at a time, not the whole project at once — read and understand each file before moving to the next, so you're not vibe-coding.

---

## Before you start coding: environment setup checklist

- [ ] Python 3.10+ virtual environment created
- [ ] `pip install openai python-dotenv` (add `instructor`, `pydantic` if using structured output)
- [ ] `.env` file with your API key, added to `.gitignore`
- [ ] Git repo initialized
- [ ] Docker installed and running locally
- [ ] A Slack workspace + incoming webhook URL ready (can be added later — not needed for step 1)
