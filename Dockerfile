# fusejs-python — reproducible build and verification.
#
# The shipped package has zero runtime dependencies and no JavaScript. Node is
# present in this image *only* to run the fuse.js differential oracle, which is
# test infrastructure: nothing under src/ imports or executes it. See
# DECISIONS.md on the "no source-language runtime" rule.
#
#   docker build -t fusejs-python .
#   docker run --rm fusejs-python              # build + checks + tests
#   docker run --rm fusejs-python just fuzz    # 60s differential fuzz
#   docker run --rm fusejs-python just bench   # benchmark vs fuse.js

FROM python:3.12-slim

# Node for the oracle, `just` for the task runner.
RUN apt-get update \
    && apt-get install --no-install-recommends -y nodejs ca-certificates curl \
    && curl -fsSL https://github.com/casey/just/releases/download/1.36.0/just-1.36.0-x86_64-unknown-linux-musl.tar.gz \
       | tar -xz -C /usr/local/bin just \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency metadata first, so edits to the source don't bust the layer.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e ".[dev]"

COPY justfile ./
COPY tests/ ./tests/
COPY fuzz/ ./fuzz/
COPY bench/ ./bench/

# Fail the build if the vendored oracle is missing — every differential
# guarantee this project makes depends on it.
RUN test -f tests/original/dist/fuse.mjs \
    || (echo "FATAL: vendored fuse.js oracle not found" && exit 1)

CMD ["just", "check", "test"]
