# WordSwift — Semantic word search engine
# Works with both Docker and Podman.
#
#   docker build -t wordswift .
#   podman build -t wordswift .
#
FROM python:3.11-slim

# Build deps for plyvel (LevelDB bindings) + curl for healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        libleveldb-dev \
        libsnappy-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy the application.
COPY . .
RUN pip install --no-cache-dir -e . \
    && mkdir -p /app/data

# Run as non-root user.
RUN useradd --create-home --shell /bin/bash wordswift \
    && chown -R wordswift:wordswift /app
USER wordswift

EXPOSE 7700
VOLUME ["/app/data"]

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD curl -fsS http://localhost:7700/health || exit 1

# Use `python -m src` (the package __main__), NOT `python -m src.api`.
# Running src.api directly executes its module body twice (once via src's
# __init__ import, once as __main__), constructing two IndexRegistries that
# fight over the LevelDB locks. See src/__main__.py.
ENTRYPOINT ["python", "-m", "src"]
CMD ["--host", "0.0.0.0", "--port", "7700", "--data-dir", "/app/data"]
