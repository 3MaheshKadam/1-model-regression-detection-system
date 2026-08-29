# Containerizes the eval pipeline so it runs identically in CI and locally.
# Matches the Python version used in .github/workflows/eval.yml.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer is cached unless requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the pipeline actually needs at runtime.
COPY eval/ eval/
COPY prompts/ prompts/
COPY golden_dataset.json .
COPY results/baseline.json results/baseline.json

# API keys (GROQ_API_KEY / OPENAI_API_KEY) are supplied at `docker run` time via
# --env-file or -e — never baked into the image. Mount ./results as a volume so
# raw/scored output written by the container lands back on the host:
#
#   docker build -t model-regression-eval .
#   docker run --rm --env-file .env -v "$(pwd)/results:/app/results" \
#       model-regression-eval eval/runner.py --quick
#   docker run --rm --env-file .env -v "$(pwd)/results:/app/results" \
#       model-regression-eval eval/scorer.py
#   docker run --rm -v "$(pwd)/results:/app/results" \
#       model-regression-eval eval/compare.py
#
# Default: run the fast quick-tier suite if no command is given.
ENTRYPOINT ["python"]
CMD ["eval/runner.py", "--quick"]
