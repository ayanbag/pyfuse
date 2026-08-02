# CLAUDE.md — fuse.js → Python port (Port Mortem 2026, Track H)

> Project instructions for Claude Code. Read this fully before writing any code.
> The win condition is a **working, byte-equivalent Python port** of fuse.js with
> honest numbers and a defensible decision log — not a compile, not a demo.

---

## 1. Mission

Port **krisk/Fuse (fuse.js)** — TypeScript, Apache-2.0, v7.5.0 — to **idiomatic
Python** for the Port Mortem 2026 hackathon.

- **Track:** H (Open Pair). Pair: **TypeScript → Python.**
- **Goal:** behavioral equivalence with the JavaScript original, modeled on the
  official `fuse-swift` port's "byte-equivalent results" standard.
- **Source of truth:** the fuse.js source + its (kickoff-hashed) test suite.

**README defensibility rationale (Track H requires this):** Python has
string-to-string fuzzy matching (rapidfuzz, thefuzz) but *no* equivalent fuzzy
**search engine** — weighted keys, nested-path resolution, extended-query
operators, logical `$and`/`$or` queries, and IDF/BM25-style token ranking on a
Bitap core. This is a real gap in the Python ecosystem, which JavaScript serves
poorly for data/ML and backend pipelines. That is the migration's reason to
exist. State it plainly in README.md.

---

## 2. Hard rules (disqualification-level — never violate)

- **New code only.** Every line of the port is written during the 72-hour
  window. Standard library, OSS Python deps, and AI assistance are all allowed.
  Pre-existing partial ports are not.
- **Commits prove the window.** The port repo is created at/after kickoff; the
  first commit is timestamped after kickoff; history is incremental. A single
  "initial dump" commit, or any code predating kickoff, is disqualifying. See
  Section 9 for commit discipline.
- **No source-language runtime.** The shipped Python package must NOT call,
  embed, shell out to, or FFI into Node/JavaScript. The fuse.js original is used
  ONLY as a test oracle inside the differential harness (run via Node there),
  never as a runtime dependency of the port.
- **Single-command build.** The project must build/run via one documented
  command (`make`, `docker compose up`, etc.). If a judge has to read CI to
  figure out how to build, the rule is failed.
- **Original test suite is sacred.** The fuse.js vitest suite is file-hashed at
  kickoff. Commit it UNMODIFIED into `tests/original/`. Any edit is a
  Test-Parity scoring hit (not an auto-DQ) and MUST be named in DECISIONS.md.
  Silent edits are penalized heavily.
- **Public + OSI-licensed at submission.** Keep the Apache-2.0 license and
  attribution intact.

---

## 3. Repo (pin it)

- Source: `https://github.com/krisk/Fuse`, tag/commit **v7.5.0** (or the exact
  commit specified at kickoff). Clone into `tests/original/` (or `oracle/`) and
  record the pinned commit + kickoff hash in `.port-mortem.toml` and
  DECISIONS.md.
- License: Apache-2.0. Source: TypeScript (use the type annotations to drive
  Python type hints — the port is largely mechanical from types).

### Source map (what to port)
- `src/search/bitap/*` — Bitap core: `search`, `computeScore`,
  `createPatternAlphabet`, `convertMaskToIndices`, constants. **This is the
  scoring heart.**
- `src/search/token/*` — token search: `InvertedIndex`, `analyzer` (IDF).
- `src/search/extended/*` — extended search: `parseQuery`, `matchers`.
- `src/tools/*` — `KeyStore`, `FuseIndex`, `fieldNorm`, `MaxHeap`.
- `src/core/*` — `config`, `queryParser`, `computeScore`, `format`,
  `formatMatches`.
- `src/helpers/*` — `get` (nested path resolution), `diacritics`, `typeGuards`,
  `mergeIndices`.

### Out of scope (document the exclusion in DECISIONS.md)
- `src/workers/*` (FuseWorker / Web Worker / worker-thread parallelism) —
  JS-runtime-specific. Skip for the core port, or optionally map to
  `multiprocessing`/`concurrent.futures` later. Excluding it does not affect LOC
  eligibility; the core alone clears the floor.

---

## 4. Target architecture (idiomatic Python)

- **Bitap core:** bitwise approximate matching, 32-char pattern limit, 0-1
  fuzziness score. Python ints are arbitrary-precision, so the bitmask maps
  cleanly. Preserve the exact algorithm; do not "optimize" it into divergence.
- **FuseIndex / KeyStore:** record indexing, weighted keys (normalized
  internally), nested-path resolution via dot/array notation (port
  `helpers/get`).
- **Token search:** `InvertedIndex` + IDF / BM25-style ranking.
- **Extended search:** query parser + matchers — `=exact`, `^prefix`, `!inverse`,
  `'include`, suffix, plus `$and` / `$or` logical composition.
- **Scoring:** combine Bitap score x key weight x field-length norm. This is the
  exact-parity hotspot (see Section 5).
- **MaxHeap:** top-N selection with the same tie-break semantics as the original.
- **Config:** dataclass mirroring fuse.js defaults (`threshold=0.6`,
  `distance=100`, `location=0`, `ignoreLocation=False`, `minMatchCharLength`,
  `fieldNormWeight`, etc.).

Idioms: full type hints (from the TS types), `dataclasses` for config/results,
explicit `Enum`s, list/dict comprehensions, `__slots__` where hot. **Zero runtime
dependencies** to mirror fuse.js's zero-dep design (pytest/hypothesis are
dev-only).

---

## 5. Equivalence hotspots (where divergence hides — test these hardest)

- **Float scoring math.** JS uses IEEE-754 doubles; Python `float` is the same
  double, BUT operation ORDER changes rounding. Mirror the exact arithmetic
  order from `computeScore` — do not algebraically "simplify."
- **Diacritic stripping.** Port fuse.js's three-step strip literally
  (NFD normalize -> scalar-range filter -> NON_DECOMPOSABLE_MAP). Follow the
  fuse-swift notes as the reference implementation.
- **Sort stability + tie-breaking.** JS `Array.prototype.sort` vs Python
  `sorted` — ensure identical tie-breaks (e.g., by `refIndex`) so result
  ordering matches exactly.
- **Default option values.** Any drift in defaults silently changes every score.
  Pin them to fuse.js exactly and cover with tests.
- **Field-length norm & IDF weighting.** Match the normalization formulae and
  rounding precisely.

---

## 6. Differential workflow — REQUIRED per module

Behavioral Equivalence is 30% of the score and is judged on **property tests +
differential fuzzing on shared inputs**, not example tests alone. For every
module:

1. Implement the Python piece.
2. Port the matching vitest cases (and snapshot expectations) to `tests/port/`
   as pytest.
3. Run `just diff`: execute fuse.js in Node on identical inputs, assert the
   Python port produces identical output — full-precision scores, result
   ordering, and match indices.
4. Add a **hypothesis** (property-based) generator for that module; assert
   JS == Python across thousands of random `(dataset, query, options)` triples.
   Log any mismatch in DECISIONS.md BEFORE fixing — that log is Bug-Catcher
   evidence.
5. A module is not "done" until `just diff` and the hypothesis run are green.

The differential fuzz harness is a required deliverable; a template is provided
at kickoff (Trail of Bits' DIFFER or equivalent). Target a 60s+ continuous run
with zero divergences on the shared public API for the Differential Fuzz
Survivor bonus.

---

## 7. Deliverables (submit all 7)

1. Public GitHub repo with the port.
2. One-command build producing a runnable artifact (Dockerfile / `just build`).
3. The original test suite (`tests/original/`, hashed, unmodified) passing
   against the port — partial passes still score, proportionally.
4. Differential fuzz harness (`fuzz/harness.*`) + `fuzz/log.txt` (60s+ run).
5. DECISIONS.md — every non-trivial architectural divergence + rationale
   (aim for 10+; see Section 8 and the Decision Log bonus).
6. Benchmark report (`bench/methodology.md` + `bench/results.json`) — original
   vs port on a shared workload, with p99, RSS, startup, throughput.
7. 5-minute demo video showing the test suite passing live against the port.

### On-disk layout (advisory, but follow it)
```
your-port/
|-- README.md            <- migration rationale + one-command build
|-- DECISIONS.md         <- every non-trivial divergence + why (10+)
|-- Dockerfile           <- one command -> runnable artifact
|-- pyproject.toml       <- package + deps (zero runtime deps ideally)
|-- justfile             <- build / test / diff / bench / check
|-- src/                 <- idiomatic Python port
|-- tests/original/      <- fuse.js vitest suite, hashed at kickoff, UNMODIFIED
|-- tests/port/          <- your pytest suite (ported + new)
|-- fuzz/
|   |-- harness.py       <- differential fuzzer (JS oracle vs Python)
|   `-- log.txt          <- 60s+ run, zero divergences (bonus)
|-- bench/
|   |-- methodology.md   <- how you measured
|   `-- results.json     <- p99, RSS, startup, throughput
`-- .port-mortem.toml    <- track letter (H), source URL, kickoff hash
```

---

## 8. Scoring model — optimize in this order

- **Functionality & Reliability (40%).** One-command build + original suite
  passing (file-hash verified). A 99% pass rate with zero test edits beats a
  100% claim with suspicious deletions. -> Get the build + differential harness
  green early; never edit the hashed suite.
- **Behavioral Equivalence (30%).** Differential fuzz survival on shared inputs;
  property tests (not example-only); honest p99/RSS/startup with methodology;
  distributions compared, confounders named. -> Invest in hypothesis-based
  differential testing.
- **Code Quality (20%).** Idiomatic Python to a senior reviewer; escape-hatch
  ratio (see Section 10); decision-log quality; native error handling. -> Type
  everything, keep `Any`/`type: ignore` near zero, write a real DECISIONS.md.
- **Innovation (10%).** A Track-H pair that defends itself; latent bugs caught
  upstream; decisions a senior would adopt. -> The ecosystem-gap rationale + any
  bug you find via diffing.

### Bonus (pick ONE and nail it)
- **Differential Fuzz Survivor (+5, hard):** 60s+ fuzz, zero divergences,
  publish the log.
- **Zero Unsafe (+5, hard):** for Python this means the escape-hatch count
  (`Any`, `# type: ignore`, `cast`, `eval`/`exec`, bare `except`) under the
  per-pair threshold published at kickoff. Very achievable here — target it.
- **Bug Catcher (+3, medium):** find a latent fuse.js bug via differential
  testing, file it upstream during the event.
- **Decision Log (+3, medium):** 10+ non-trivial divergences with rationale in
  DECISIONS.md. Empty bullets don't count.

**Recommended:** aim for **Zero Unsafe** + a strong **Decision Log** (they
compound with Code Quality), and opportunistically bank **Bug Catcher** if the
diff surfaces one.

---

## 9. Commit discipline (evidence, not bookkeeping)

> **REMINDER BEHAVIOR (do this actively):** After completing ANY meaningful unit
> of work — a ported module, a bug fix, a batch of translated tests, a newly
> green `just diff` or hypothesis run — STOP and remind me to commit, proposing a
> specific conventional-style message. Surface a commit checkpoint whenever a
> logical piece is done and again before switching tasks or files. Never let many
> changes pile up uncommitted (that trends toward a disqualifying "dump" commit).
> If you are making the changes yourself, offer to stage and commit them for me
> with the proposed message rather than continuing silently.

- First commit **after kickoff**; repo created at/after kickoff.
- **Incremental, frequent, small commits** — one per module/sub-step. NEVER a
  single large dump; that is disqualifying.
- Conventional style, descriptive scope, e.g.:
  - `feat(bitap): port core scan + pattern alphabet`
  - `feat(scoring): field-length norm matching fuse.js order`
  - `test(port): translate fuzzy-search snapshots to pytest`
  - `test(diff): hypothesis generator for extended queries`
  - `fix(sort): match refIndex tie-break for stable ordering`
  - `docs(decisions): log diacritic-strip divergence`
- Commit `tests/original/` unmodified in an early, clearly-labeled commit.
- Push regularly so incremental history is visible upstream.
- Do not squash the port into one commit before submission.

---

## 10. Conventions

- **Typing:** full hints ported from the TS source; run `mypy --strict`. Keep
  `Any`, `cast`, and `# type: ignore` to a documented minimum (Zero-Unsafe
  bonus). No `eval`/`exec`.
- **Lint/format:** `ruff` clean; consistent formatting.
- **Errors:** native Python patterns — raise specific exceptions, no bare
  `except`, no silent happy-path shortcuts (judges check error paths).
- **Deps:** zero runtime dependencies (match fuse.js). Dev-only: pytest,
  hypothesis, ruff, mypy.
- **Docstrings** on public API; keep doctests runnable where sensible.

---

## 11. Commands (justfile is the single source of truth)

- `just build`  — build the package / Docker artifact (one command)
- `just test`   — pytest (ported + new suite)
- `just diff`   — differential harness vs the Node fuse.js oracle
- `just fuzz`   — 60s+ differential fuzz run, writes fuzz/log.txt
- `just bench`  — timing vs fuse.js -> bench/results.json (p99, RSS, startup)
- `just check`  — ruff + mypy --strict

---

## 12. Benchmark honesty (read this)

Python will almost certainly be **slower** than fuse.js on hot loops. That is
expected and fine — the migration's value is ecosystem/parity, not speed. Report
honest numbers with methodology: p99 and RSS and startup, distributions not just
averages, confounders named. An honest regression scores ABOVE a throughput-only
cherry-pick. Do not hide or game the benchmark.

---

## 13. Do NOT

- Call/embed/FFI/shell-out to Node or JS at runtime in the shipped package.
- Copy rapidfuzz/thefuzz code (different algorithm anyway).
- Edit the hashed `tests/original/` suite silently.
- Ship a single "initial dump" commit or any pre-kickoff code.
- Cherry-pick happy-path tests; cover error paths.
- Guess scoring/diacritic behavior — read the source or run the oracle.
- Leave DECISIONS.md as empty bullets — judges read it and it's scored.

---

## 14. FYI / open items for the human (not Claude Code's call)

- **Cross-language test-suite mechanics.** fuse.js's suite is vitest (JS). "The
  original suite passing against your Python port" is straightforward for
  same-language ports but ambiguous JS->Python. Plan: keep `tests/original/`
  hashed + unmodified for provenance, demonstrate equivalence via the
  differential harness (JS oracle vs Python) + the ported pytest suite in
  `tests/port/`. **Confirm on the Raptors Discord how they want cross-language
  "suite passing" demonstrated for the demo video** — this affects the 40%
  Functionality score.
- **Kickoff artifacts to record immediately:** pinned commit, the published
  Zero-Unsafe threshold for this pair, the provided fuzz-harness template, and
  the kickoff hash -> put them in `.port-mortem.toml` + DECISIONS.md.
- **Verify no faithful fuse.js Python port exists** on PyPI before committing
  (rapidfuzz/thefuzz don't count).
