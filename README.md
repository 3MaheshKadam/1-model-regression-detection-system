# Model Regression Detection System

CI/CD-style regression testing for LLM prompts. Every time a prompt changes, this
pipeline re-runs it against a hand-curated golden dataset, scores the output with
an LLM-as-judge across multiple dimensions, and flags regressions against a stored
baseline — the same way a test suite catches broken code, but for probabilistic
LLM behavior instead of deterministic functions.

## How it works

```
golden_dataset.json  →  runner.py sends each `input` through prompts/*.txt + model
                                              ↓
                                        raw output (results/raw_*.json)
                                              ↓
                     scorer.py: LLM-as-judge checks output against `expected_criteria`
                                              ↓
                                    scored run (results/scored_*.json)
                                              ↓
                     compare.py: scored run vs. results/baseline.json → regression?
```

- **Length** is checked programmatically (word count vs. each case's `max_words`).
- **Accuracy** is checked by an LLM judge against each case's `must_include` /
  `must_not_include` criteria — exact string matching doesn't work here since LLM
  wording varies run to run.
- A case only passes overall if **both** dimensions pass.

## Project structure

```
model-regression-detection-system/
├── golden_dataset.json        62 hand-written test cases (see below)
├── prompts/
│   └── summarizer_v1.txt      the prompt under test
├── eval/
│   ├── llm_client.py          shared provider/retry logic (Groq + OpenAI)
│   ├── runner.py              runs the dataset through a prompt + model
│   ├── scorer.py              LLM-as-judge scoring against expected_criteria
│   └── compare.py             diffs a scored run against the baseline
├── results/
│   ├── baseline.json          committed "known good" reference (tracked in git)
│   ├── latest_raw.json        most recent raw run (gitignored, regenerated)
│   └── latest_scored.json     most recent scored run (gitignored, regenerated)
├── requirements.txt
├── .env.example
└── .gitignore
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env
```

Then add a free Groq API key to `.env` — get one at
[console.groq.com/keys](https://console.groq.com/keys), no card required:

```
GROQ_API_KEY=gsk_...
```

(`OPENAI_API_KEY` in `.env.example` is only needed if you switch to
`--provider openai`, which requires billing set up on that account.)

## Usage

```bash
# Run the fast 15-case subset through the current prompt (tier: "quick")
python eval/runner.py --quick

# Run the full 62-case suite
python eval/runner.py

# Test a different prompt version
python eval/runner.py --prompt prompts/summarizer_v2.txt

# Score the most recent run (results/latest_raw.json by default)
python eval/scorer.py

# Compare the most recent scored run against the baseline
python eval/compare.py
```

`compare.py` exits with code `1` if any regression is detected (per-case, on
either dimension, or an aggregate latency/cost worsening beyond a threshold) —
this is what a CI check can key off of.

### Updating the baseline

After a deliberate, verified prompt improvement, promote the new run to be the
reference point everything else is compared against:

```bash
python eval/runner.py && python eval/scorer.py
cp results/latest_scored.json results/baseline.json
```

## The golden dataset

`golden_dataset.json` holds 62 hand-designed test cases, each shaped like:

```json
{
  "id": "news-006",
  "category": "misleading_headline",
  "difficulty": "hard",
  "failure_mode": "summarizing the headline's claim instead of the body's actual findings",
  "notes": "Classic clickbait-vs-content hallucination trap.",
  "input": "...",
  "expected_criteria": {
    "must_include": ["small study", "..."],
    "must_not_include": ["coffee cures insomnia stated as fact"],
    "max_words": 40
  },
  "tier": "quick"
}
```

`category`/`difficulty`/`failure_mode`/`notes` are human-facing only — they're
never sent to the model. `tier: "quick"` marks 15 cases (spanning the most
distinct failure modes: prompt injection, empty input, causal-fallacy traps,
stale/evolving data, sarcasm, etc.) for fast iteration; the full 62 cover
everything from numeric fidelity to legal precision to adversarial input.

## Status

- [x] 1. Golden dataset (62 cases)
- [x] 2. Prompt file (`prompts/summarizer_v1.txt`)
- [x] 3. Test runner (`eval/runner.py`)
- [x] 4. Scoring engine (`eval/scorer.py`)
- [x] 5. Diff/comparison logic (`eval/compare.py`)
- [ ] 6. GitHub Actions workflow (`.github/workflows/eval.yml`)
- [ ] 7. Dockerfile
- [ ] 8. Slack alerting (`eval/notify.py`)
