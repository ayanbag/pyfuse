# fusejs-python

> A byte-equivalent Python port of **[fuse.js](https://github.com/krisk/Fuse)** —
> the lightweight fuzzy-search engine — built for **Port Mortem 2026**
> (Track H, Open Pair: **TypeScript → Python**).

---

## What this is

fuse.js isn't a string-similarity function — it's a fuzzy-search **engine**: it
searches collections of records with typo-tolerant matching (a modified Bitap
algorithm), weighted keys, nested-path lookups, extended-query operators
(`=exact`, `^prefix`, `!inverse`, …), logical `$and`/`$or` queries, token search
with IDF ranking, and relevance scoring.

This project reimplements that engine in **idiomatic Python**, targeting results
**byte-equivalent** to the JavaScript original — the standard set by the official
[`fuse-swift`](https://github.com/krisk/fuse-swift) port.

It is a genuine reimplementation, **not a wrapper**: the shipped package has zero
Node/JavaScript runtime dependency. fuse.js is used only as a *test oracle* to
prove equivalence.

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

*(Honesty note: the value here is ecosystem and parity, not raw speed — CPython
will not out-run V8. Benchmarks are reported honestly, with methodology.)*

## Build & run

One command (finalized during the port):

```bash
just build          # or: docker compose up
```

Run the ported test suite:

```bash
just test
```

Differential equivalence check against the fuse.js oracle:

```bash
just diff
```

## Equivalence & engineering notes

- **Behavioral equivalence** is proven by a differential harness that runs
  fuse.js (via Node) and this port on shared inputs and asserts identical
  scores, ordering, and match indices — plus property-based (`hypothesis`)
  fuzzing across randomized datasets/queries/options.
- **Architectural divergences** and their rationale are documented in
  [`DECISIONS.md`](./DECISIONS.md).
- **Benchmarks** (p99, RSS, startup, throughput) with methodology live in
  [`bench/`](./bench/).

## Attribution & license

Ported from **fuse.js** ([krisk/Fuse](https://github.com/krisk/Fuse)),
© Kirollos Risk, licensed **Apache-2.0**. This port is likewise **Apache-2.0**.

Pinned source commit: `45bac9f (HEAD, tag: v7.5.0) chore(release): 7.5.0`
