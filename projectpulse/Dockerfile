# ProjectPulseAI, as a server.
#
# Build context is `projectpulse/`, not the repository root - the root also holds
# `devlake/` and `gpt2sp/`, which are read-only reference repos and have no
# business in an image.
#
# The front end needs no build step here: `app/api/static/app/` is the committed
# Vite output, which is the whole reason it is committed. No node, no npm.
#
# The demo timeline is generated rather than copied. `data/demo/*.xlsx` is not in
# git - `scripts.replay` writes those sheets from code and then ingests them
# through the real reader, differ and identity resolver. So the deployed app has
# a genuine history rather than seeded domain rows, which is invariant 4.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies in their own layer, so editing application code does not reinstall
# them. `pyproject.toml` alone is not installable, so the two source trees come
# first - they are small, and they change together anyway.
COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts
# Traceability run snapshots. Unlike everything under `data/`, these cannot be
# regenerated in the container - producing one costs money and needs an API key
# the image does not have - so they ship as artifacts. See traceability_runs/README.md.
COPY traceability_runs ./traceability_runs

# Editable, deliberately: it leaves the code at /app so `_bootstrap.REPO`,
# `settings.data_root` and `scripts.replay`'s subprocess cwd all point at a real
# writable tree. A normal install puts them under site-packages, where the demo
# generator would be writing spreadsheets into the interpreter's own directory.
#
# `llm` (Anthropic) is in because narration and the tool-use tile builder
# (app/dashboard/agent.py, Anthropic-only) are both configured against Claude
# - PULSE_NARRATION_PROVIDER=anthropic is set in `fly.toml`, and the agent's
# tool loop calls the `anthropic` SDK directly, so it needs the package present
# regardless of which provider narration itself is pointed at. Switching
# narration's *own* provider is still a one-word change in `.env`
# (PULSE_NARRATION_PROVIDER) plus swapping this extra to `.[llm-gemini]` or
# `.[llm-openai]` - but the tile builder would then fall back to its
# plain-parse path, since it does not follow that switch.
# `report` is in because DEPLOY.md lists the .docx status report as always
# live, not gated behind a key - python-docx has no fallback otherwise.
RUN pip install -e ".[llm,report]"

# The demo generator writes here. Created up front and owned by the runtime user
# so the container does not need to run as root to seed itself.
RUN mkdir -p /app/data/demo /app/models \
    && useradd --create-home --uid 10001 pulse \
    && chown -R pulse:pulse /app
USER pulse

# Fly, Railway and Render all inject PORT. 8080 is the value they inject most
# often, and `scripts.serve` reads the variable rather than this default.
ENV PORT=8080
EXPOSE 8080

# Schema, seed-if-empty, then uvicorn. See scripts/serve.py for why the seeding
# is conditional rather than unconditional.
CMD ["python", "-m", "scripts.serve"]
