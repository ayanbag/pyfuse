# fusejs-python — task runner.
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
    {{python}} -c "import fusejs; print('fusejs', fusejs.__version__, 'ready')"

# Build the Docker image instead (no local Python needed).
docker:
    docker build -t fusejs-python .

# ── Verification ───────────────────────────────────────────────────

# Run the ported pytest suite.
test:
    {{python}} -m pytest tests/port

# Differential equivalence check against the Node fuse.js oracle.
diff:
    {{python}} -m pytest tests/port -m differential -v

# Differential fuzz run. Override the duration: `just fuzz 120`.
fuzz seconds="60":
    {{python}} fuzz/harness.py --seconds {{seconds}} --log fuzz/log.txt

# Lint and type-check.
check:
    {{python}} -m ruff check src tests/port fuzz bench
    {{python}} -m ruff format --check src tests/port fuzz bench
    {{python}} -m mypy

# Count the escape hatches the Zero-Unsafe bonus is scored on.
unsafe:
    @{{python}} bench/count_unsafe.py

# Benchmark the port against fuse.js and write bench/results.json.
bench:
    {{python}} bench/run.py --out bench/results.json

# Everything a reviewer would want to see, in order.
all: build check test fuzz bench
