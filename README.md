# pyfuse

A Python port of **[fuse.js](https://github.com/krisk/Fuse)**, the fuzzy-search
library, originally built for **Port Mortem 2026** (Track H, Open Pair: TypeScript →
Python). The main goal was behavioural equivalence with the original, not a
lookalike API.

---

## What it is

fuse.js isn't a string-similarity function. It's a small search engine: you
give it a list of records, it gives you ranked matches, and it tolerates typos
along the way. Under the hood that's a modified Bitap scan plus weighted keys,
nested-path lookups, extended-query operators (`=exact`, `^prefix`,
`!inverse`, and friends), `$and`/`$or` composition, token search with IDF
ranking, and a scoring pass to tie it together.

This is a real re-implementation of that engine in Python, not a wrapper. The
package has zero runtime dependencies and there is no Node anywhere in it.
fuse.js does show up in this repo, but only as a test oracle — something to
diff against.

## Why Python needs this

Python's fuzzy-matching story is good if you're comparing two strings.
`rapidfuzz` and `thefuzz` are fast and well maintained. It falls apart the
moment you want to search a *collection*: weighted fields, nested paths, query
operators, relevance ranking. At that point your options are to write scoring
by hand or to stand up Elasticsearch, and neither is proportionate to "find the
right row in these 5,000 records."

fuse.js is exactly the missing middle, and it only runs in JavaScript — an
ecosystem that most data and backend work has already left. So the capability
exists, it's just on the wrong side of a language boundary. Moving it is the
whole point of this port.

## Install

```bash
pip install pyfuse
```

Python 3.10+. No runtime dependencies — the only thing that lands in your
environment is this package.

## Quick start

```python
from pyfuse import Fuse

books = [
    {"title": "Old Man's War", "author": "John Scalzi"},
    {"title": "The Lock Artist", "author": "Steve Hamilton"},
]

fuse = Fuse(books, {"keys": ["title", "author"], "include_score": True})
for result in fuse.search("old man"):
    print(result.item["title"], result.score)
```

Options are snake_case the way Python expects. The camelCase spellings are
accepted too, so a config file shared with a JS codebase works as-is.

## Try it without installing anything

**[→ Open the live playground](https://ayanbag.github.io/pyfuse/)**

The port is pure Python with zero dependencies, which means it runs unmodified
in the browser under Pyodide. That page installs the actual wheel — the same
one you'd `pip install` — and searches with it. Type a query, misspell it on
purpose, drag the threshold, edit the corpus. Nothing is sent anywhere; it all
runs in your tab.

There's also a guided tour in the terminal:

```bash
just demo                  # eight scenes, each printing the code it runs
just demo --interactive    # ...then a live search prompt
just playground            # serve the browser playground locally
```

### Worked examples

Three scripts in [`examples/`](https://github.com/ayanbag/pyfuse/tree/main/examples/) that use the port to solve a real
problem rather than to show off an API. Each documents the option choices it
made and why, and the numbers in the commentary were measured, not guessed.

| | What it does |
|---|---|
| [`ticket_triage.py`](https://github.com/ayanbag/pyfuse/blob/main/examples/ticket_triage.py) | Routes messy free-text support tickets to a runbook, with cutoffs for auto-route / suggest / escalate. Shows why long queries need token search, and why IDF misleads on a small corpus. |
| [`reconcile.py`](https://github.com/ayanbag/pyfuse/blob/main/examples/reconcile.py) | Fuzzy-joins two record sets after a migration drops the key — `ACME CORPORATION LTD` vs `Acme Corp. Limited`. Reports matched, needs-review, and missing in both directions. |
| [`search_json.py`](https://github.com/ayanbag/pyfuse/blob/main/examples/search_json.py) | A general CLI: fuzzy-search any JSON or JSONL file, with inferred or explicit keys, extended operators and highlighted output. |

```bash
python examples/ticket_triage.py
python examples/reconcile.py
python examples/search_json.py tests/original/test/fixtures/books.json "ste ham"
```

## Build and run

One command:

```bash
just build      # install + verify it imports
```

Or, if you'd rather not have Python locally:

```bash
docker build -t pyfuse . && docker run --rm pyfuse
```

The rest:

```bash
just test       # the ported pytest suite (417 tests)
just compat     # the ORIGINAL fuse.js vitest suite, run against this port
just diff       # differential checks against the fuse.js oracle
just fuzz       # 60s differential fuzz -> fuzz/log.txt
just bench      # benchmark vs fuse.js -> bench/results.json
just check      # ruff + mypy --strict
just unsafe     # escape-hatch census
```

## The original fuse.js suite runs against this port

**285 of 297 pass (95.96%), with every test file byte-for-byte unmodified.**

The suite is JavaScript, run by vitest, and the rules say don't touch it. The
way through turned out to be small: all 13 behavioural spec files import the
engine from the same specifier, `'../dist/fuse.mjs'`. Point a vitest alias at a
shim that speaks the fuse.js API and forwards every call to Python, and the
tests run unchanged without ever knowing what's underneath.

```bash
just compat     # see compat/README.md for how it works
```

None of the 12 failures is a bug in the port. Ten of them hand fuse.js a
JavaScript function (`sortFn`, `getFn`, a callable `tokenize`, `Fuse.use`),
and a closure over a live JS heap cannot be serialised into Python at any
price. The other two are divergences this port chose on purpose and wrote down
([DECISIONS.md](https://github.com/ayanbag/pyfuse/blob/main/DECISIONS.md) §13 and §19) — those tests failing is the
documentation being true. All twelve are named individually in
[`compat/README.md`](https://github.com/ayanbag/pyfuse/blob/main/compat/README.md), and the run output is committed as
[`compat/results.txt`](https://github.com/ayanbag/pyfuse/blob/main/compat/results.txt).

## What "equivalent" actually means here

**Structure matches exactly.** Result sets, ordering, match indices, keys,
refIndex. A 60-second differential fuzz run against the live fuse.js oracle:

```
cases                  : 51,569  (859/s)
STRUCTURAL DIVERGENCES : 0
```

**Scores agree to about 1e-13 relative, not to the bit.** This one isn't a
shortcut, it's a wall. CPython and V8 ship different `pow` and `log`: CPython's
are correctly rounded, V8's are not, and on roughly 10% of the calls the
scorer makes they disagree by 1 ULP. Scoring does one `pow` per matched key, so
that error rides through the whole chain. Worst case measured across 45k fuzz
cases was 8.5e-14 relative.

**And yes, that has one visible consequence.** 1 ULP is enough to break an
exact score tie, which means the tie-break fires in one engine and not the
other, and the two return the same documents in a different order. Under a
`limit`, a different document can make the cut. It happened in **8 of 51,569
cases (0.016%)**. `fuzz/log.txt` reports it as its own category instead of
burying it in the score-noise bucket — I'd claimed earlier that ordering was
never affected, and the fuzz run is what proved me wrong.

The full write-up, including the ~200-line fdlibm `Math.pow` transcription that
got to 95.6% bit-exact and why chasing the last 4.4% was the wrong use of the
remaining time, is in [DECISIONS.md](https://github.com/ayanbag/pyfuse/blob/main/DECISIONS.md).

## Performance

**fuse.js is about 13x faster.** That was always going to happen: V8 JITs the
Bitap inner loop and CPython interprets it. What this port buys you is reach
and parity, not speed, and it would be dishonest to present it otherwise.

| | pyfuse | fuse.js |
|---|---|---|
| throughput | 7.0 searches/s | 92.8 searches/s |
| latency p50 | 114.9 ms | 9.0 ms |
| latency p99 | 637.0 ms | 49.8 ms |
| startup | 13.7 ms | 7.7 ms |
| peak RSS | **31.3 MiB** | 53.7 MiB |

400 documents, 4 keys, 150 queries, both engines warmed up first. The method,
the confounders, and why you shouldn't read too much into the RSS number are
all in [`bench/methodology.md`](https://github.com/ayanbag/pyfuse/blob/main/bench/methodology.md).

## Engineering notes

- Zero runtime dependencies, same as fuse.js itself.
- `mypy --strict` clean, and zero `cast`, `type: ignore`, `eval`/`exec` or bare
  `except` anywhere in `src/`. `just unsafe` counts them if you don't believe
  it.
- 417 native tests, plus 285 of the original suite's 297. Some of the native
  ones are property-based (`hypothesis`) and run against the live oracle rather
  than against fixed expectations.
- Anything that diverges is in [DECISIONS.md](https://github.com/ayanbag/pyfuse/blob/main/DECISIONS.md) with the evidence
  that led there.

## Scope

Ported in full: the Bitap core, indexing and key weighting, scoring, extended
search, logical queries, token search with IDF ranking. About 3,283 lines.

Left out: `src/workers/*`, the Web Worker and worker-thread plumbing. It's
JS-runtime machinery rather than search behaviour, and the reasoning is in
[DECISIONS.md](https://github.com/ayanbag/pyfuse/blob/main/DECISIONS.md) §17.

## Attribution and license

Ported from **fuse.js** ([krisk/Fuse](https://github.com/krisk/Fuse)),
© Kirollos Risk, Apache-2.0. This port is Apache-2.0 as well. See
[NOTICE](https://github.com/ayanbag/pyfuse/blob/main/NOTICE).

Pinned source commit: `45bac9f` (tag `v7.5.0`).
