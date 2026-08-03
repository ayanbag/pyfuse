# fusejs-python

> A behaviourally-equivalent Python port of **[fuse.js](https://github.com/krisk/Fuse)** —
> the lightweight fuzzy-search engine — built for **Port Mortem 2026**
> (Track H, Open Pair: **TypeScript → Python**).

---

## What this is

fuse.js isn't a string-similarity function — it's a fuzzy-search **engine**: it
searches collections of records with typo-tolerant matching (a modified Bitap
algorithm), weighted keys, nested-path lookups, extended-query operators
(`=exact`, `^prefix`, `!inverse`, …), logical `$and`/`$or` queries, token search
with IDF ranking, and relevance scoring.

This project reimplements that engine in **idiomatic Python**. It is a genuine
reimplementation, **not a wrapper**: the shipped package has zero runtime
dependencies and no Node/JavaScript anywhere. fuse.js is used only as a *test
oracle* to prove equivalence.

## Why port it to Python — Track H rationale

Python already has excellent **string-to-string** fuzzy matching — `rapidfuzz`,
`thefuzz`. What it lacks is an equivalent to fuse.js's fuzzy **search engine**:
searching a *collection of records* with weighted fields, nested-path access,
extended and logical query operators, and relevance ranking, with no heavyweight
search backend (Elasticsearch, Typesense) required.

JavaScript serves data-science, ML, and backend-pipeline workflows poorly — so
this capability is effectively trapped outside the ecosystem where Python
dominates. Porting fuse.js gives Python developers client-quality fuzzy search
that drops straight into pandas/Jupyter/FastAPI stacks. That is a real ecosystem
gap, and closing it is this migration's reason to exist.

## Quick start

```python
from fusejs import Fuse

books = [
    {"title": "Old Man's War", "author": "John Scalzi"},
    {"title": "The Lock Artist", "author": "Steve Hamilton"},
]

fuse = Fuse(books, {"keys": ["title", "author"], "include_score": True})
for result in fuse.search("old man"):
    print(result.item["title"], result.score)
```

Options are snake_case, as Python expects; the camelCase spellings from the
JavaScript API are accepted too, so a config shared with a JS codebase works
unchanged.

## Build & run

One command:

```bash
just build      # install + verify it imports
```

or, with no local Python at all:

```bash
docker build -t fusejs-python . && docker run --rm fusejs-python
```

Everything else:

```bash
just test       # the ported pytest suite (417 tests)
just compat     # the ORIGINAL fuse.js vitest suite, run against this port
just diff       # differential checks against the fuse.js oracle
just fuzz       # 60s differential fuzz -> fuzz/log.txt
just bench      # benchmark vs fuse.js -> bench/results.json
just check      # ruff + mypy --strict
just unsafe     # escape-hatch census
```

## The original fuse.js test suite runs against this port

**285 of 297 original tests pass (95.96%) — every test file byte-for-byte
unmodified.**

The suite is JavaScript, run by vitest. Rather than translate it, a vitest
alias redirects the one import every spec shares — `'../dist/fuse.mjs'` — to a
shim that delegates each call to the Python port. Nothing in `tests/original/`
is touched.

```bash
just compat     # see compat/README.md
```

None of the 12 failures is a port bug: ten are JavaScript callables
(`sortFn`, `getFn`, function `tokenize`, `Fuse.use`) that cannot cross a
language boundary, and two are divergences this port chose deliberately and
documented ([DECISIONS.md](./DECISIONS.md) §13 and §19). Every one is
classified in [`compat/README.md`](./compat/README.md); the run output is
committed as [`compat/results.txt`](./compat/results.txt).

## Equivalence: what is claimed, precisely

**Structure is identical.** Result sets, ordering, match indices, keys, and
refIndex match fuse.js exactly. Verified by a 60-second differential fuzz run:

```
cases                  : 51,569  (859/s)
STRUCTURAL DIVERGENCES : 0
```

**Scores agree to ~1e-13 relative, not bit-for-bit.** This is not a porting
shortcut — it is not achievable. CPython and V8 ship different libm
implementations of `pow` and `log`: CPython's are correctly rounded, V8's are
not, and they disagree by 1 ULP on ~10% of calls. Scoring multiplies one `pow`
per matched key, so it compounds. Worst measured divergence across 45k fuzz
cases: **8.5e-14 relative**.

**One user-visible consequence, disclosed.** 1 ULP is enough to split an exact
score tie, and the tie-break then fires in one engine but not the other — so
two engines can return the same documents in a different order, and under a
`limit`, a different document can make the cut. Measured rate: **8 in 51,569
cases (0.016%)**. Reported as its own category in `fuzz/log.txt` rather than
hidden.

The full analysis, including the ~200-line fdlibm `Math.pow` transcription that
reached 95.6% bit-exact and why it stopped there, is in
[DECISIONS.md](./DECISIONS.md).

## Performance — honest numbers

**fuse.js is ~13x faster on throughput.** Expected: V8 JIT-compiles the Bitap
inner loop, CPython interprets it. The port's value is ecosystem reach and
parity, not speed.

| | fusejs-python | fuse.js |
|---|---|---|
| throughput | 7.0 searches/s | 92.8 searches/s |
| latency p50 | 114.9 ms | 9.0 ms |
| latency p99 | 637.0 ms | 49.8 ms |
| startup | 13.7 ms | 7.7 ms |
| peak RSS | **31.3 MiB** | 53.7 MiB |

400 documents, 4 keys, 150 queries, both engines warmed up. Full method,
confounders, and why the RSS figure should not be over-read:
[`bench/methodology.md`](./bench/methodology.md).

## Engineering notes

- **Zero runtime dependencies**, mirroring fuse.js's own zero-dep design.
- **`mypy --strict` clean**, with **zero** `cast`, `type: ignore`, `eval`/`exec`,
  or bare `except` in `src/` — run `just unsafe` to verify.
- **417 native tests** plus **285 of the original suite's 297**, including
  property-based (`hypothesis`) runs against the live oracle.
- Every non-trivial divergence is recorded in [DECISIONS.md](./DECISIONS.md)
  with the evidence behind it.

## Scope

Ported in full: the Bitap core, indexing and key weighting, scoring, extended
search, logical queries, and token search with IDF ranking — ~3,283 lines.

Out of scope: `src/workers/*` (Web Worker / worker-thread parallelism), which
is JS-runtime plumbing rather than search behaviour. See
[DECISIONS.md](./DECISIONS.md).

## Attribution & license

Ported from **fuse.js** ([krisk/Fuse](https://github.com/krisk/Fuse)),
© Kirollos Risk, licensed **Apache-2.0**. This port is likewise **Apache-2.0**.
See [NOTICE](./NOTICE).

Pinned source commit: `45bac9f` (tag `v7.5.0`).
