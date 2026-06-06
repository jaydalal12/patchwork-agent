# Deploy-shaped image. Runs the agent as a non-root user with git available.
FROM python:3.12-slim

# git is a hard runtime dependency — the agent shells out to it in a sandbox.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[all]"

# Non-root: the agent runs untrusted repo test suites; drop privileges.
RUN useradd --create-home --uid 10001 patchwork \
    && mkdir -p /work && chown patchwork:patchwork /work
USER patchwork
ENV PATCHWORK_SANDBOX_ROOT=/work \
    PATCHWORK_LOG_JSON=true

ENTRYPOINT ["patchwork"]
CMD ["--help"]
