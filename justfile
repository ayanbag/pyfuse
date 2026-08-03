# pyfuse — task runner.
#
# `just build` is the one documented command that produces a runnable
# artifact. Everything else is a convenience on top of it.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

python := "python"

# List the available tasks.
default:
    @just --list

# ── The one command ────────────────────────────────────────────────

# Install the package with its dev tooling and verify it imports.
build:
    {{python}} -m pip install --quiet --upgrade pip
    {{python}} -m pip install --quiet -e ".[dev]"
    {{python}} -c "import pyfuse; print('pyfuse', pyfuse.__version__, 'ready')"

# Build the Docker image instead (no local Python needed).
docker:
    docker build -t pyfuse .

# ── Demo ───────────────────────────────────────────────────────────

# Guided tour in the terminal. Add --interactive for a live search prompt.
demo *ARGS:
    {{python}} examples/demo.py {{ARGS}}

# Build the wheel and serve the in-browser playground on localhost:8000.
playground:
    {{python}} -m pip install --quiet --upgrade build
    {{python}} -m build --wheel --outdir docs
    @echo "  http://localhost:8000  (ctrl-c to stop)"
    {{python}} -m http.server 8000 --directory docs

# ── Verification ───────────────────────────────────────────────────

# Run the ported pytest suite.
test:
    {{python}} -m pytest tests/port

# Differential equivalence check against the Node fuse.js oracle.
diff:
    {{python}} -m pytest tests/port -m differential -v

# Run the ORIGINAL, unmodified fuse.js vitest suite against the port.
compat:
    npx vitest run --config compat/vitest.config.mjs

# Differential fuzz run. Override the duration: `just fuzz 120`.
fuzz seconds="60":
    {{python}} fuzz/harness.py --seconds {{seconds}} --log fuzz/log.txt

# Lint and type-check.
check:
    {{python}} -m ruff check src tests/port fuzz bench examples
    {{python}} -m ruff format --check src tests/port fuzz bench examples
    {{python}} -m mypy
    {{python}} -m mypy --strict examples

# Count the escape hatches the Zero-Unsafe bonus is scored on.
unsafe:
    @{{python}} bench/count_unsafe.py

# Benchmark the port against fuse.js and write bench/results.json.
bench:
    {{python}} bench/run.py --out bench/results.json

# Everything a reviewer would want to see, in order.
all: build check test fuzz bench
