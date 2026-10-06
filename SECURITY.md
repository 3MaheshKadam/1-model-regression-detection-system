# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Report privately using GitHub's **Report a vulnerability** button on the
repository's *Security* tab, or email **maheshkadam9298@gmail.com**.

## Handling secrets

- API keys (`GROQ_API_KEY`, `OPENAI_API_KEY`) are read from the environment. Locally they live in
  an untracked `.env`; in CI they come from GitHub Actions secrets. Never commit them.
- The Docker image never contains keys. Pass them at run time with `--env-file` or `-e`.
- The eval workflow runs on `pull_request`, so secrets are not exposed to pull requests from
  forks. Do not switch it to `pull_request_target`.
- Golden-dataset inputs are sent to a third-party LLM provider. Do not put personal or
  confidential data in `golden_dataset.json`.
