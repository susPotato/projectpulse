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

# git, for the one feature that needs a binary rather than a package: a delivery
# project's repository is cloned by `tracelink.source.resolve`, which shells out.
# `--no-install-recommends` keeps this to git and its direct libraries rather
# than pulling perl's documentation and a mail transport agent. The lists are
# removed in the same layer, or they stay in the image whatever the next line
# deletes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Dependencies in their own layer, so editing application code does not reinstall
# them. `pyproject.toml` alone is not installable, so the two source trees come
# first - they are small, and they change together anyway.
COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts
# The traceability pipeline, as a committed copy - see scripts/vendor_tracelink.py
# for why it is a copy rather than an install, and tests/test_vendored_tracelink.py
# for what stops it drifting. `pyproject.toml`'s `packages.find` includes only
# `app*` and `scripts*`, so this is deliberately not part of the distribution:
# it is source on the path, which is all `import tracelink` needs.
COPY tracelink ./tracelink
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

# Where `app/ingest/sources/git/source.py` looks for the pipeline. `/app` is on
# the path anyway under `python -m`, so this is belt and braces - but the
# alternative to being explicit is a feature that works or not depending on the
# interpreter's idea of the working directory, and reports "pipeline not
# installed" on a server that is plainly carrying it.
ENV PULSE_TRACELINK_HOME=/app

# The demo generator writes here, and `.pulse/repos` is where a registered
# repository is cloned - container-local and disposable by design, see
# `app/models/repo.py`. Created up front and owned by the runtime user so the
# container does not need to run as root to seed or clone.
RUN mkdir -p /app/data/demo /app/models /app/.pulse/repos \
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
