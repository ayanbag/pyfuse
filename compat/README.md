# compat/ — running the original fuse.js test suite against the Python port

**Result: 285 of 297 original tests pass (95.96%), with every test file
byte-for-byte unmodified.**

```bash
npx vitest run --config compat/vitest.config.mjs
# or
just compat
```

## The problem this solves

Deliverable 3 asks for "the original test suite passing against your port".
For a same-language port that's trivial. For TypeScript → Python it looks
impossible: the suite is JavaScript, run by vitest, and the rules forbid
editing it.

## The approach

The suite imports the engine from exactly one place:

```javascript
import Fuse from '../dist/fuse.mjs'   // 13 of the test files
```

So: point that specifier somewhere else. `vitest.config.mjs` aliases it to
`fuse_shim.mjs`, which implements the fuse.js API and forwards every call to
the Python port. The test files are never touched — only the module they
resolve `Fuse` to.

```
 test/*.test.js  (UNMODIFIED)
        │  import Fuse from '../dist/fuse.mjs'
        ▼  ← vitest alias
   fuse_shim.mjs          JS: presents the fuse.js API
        │
   py_bridge.mjs          JS: blocks the calling thread (Atomics.wait)
        │
   py_worker.mjs          JS: owns the Python process, async I/O
        │  newline-delimited JSON
        ▼
    server.py             Python: drives the real fusejs port
```

## The hard part: the calls must be synchronous

The tests do this:

```javascript
const result = fuse.search('old man')   // synchronous
expect(result.length).toBe(1)
```

There is no `await`, and adding one would mean editing the tests. So the
bridge has to **block**.

Node cannot block on a pipe: `subprocess.stdin.fd` is not exposed, and its
pipes are non-blocking. The way through is `Atomics.wait`:

1. The main thread posts the request to a worker thread and calls
   `Atomics.wait` on a `SharedArrayBuffer` — this genuinely blocks it.
2. The worker does the pipe I/O asynchronously, which Node is happy to do.
3. When the reply arrives, the worker stores `1` and calls `Atomics.notify`.
4. The main thread wakes, reads the message, and returns it — synchronously,
   as far as the test can tell.

This also dictates `pool: 'threads'` in the vitest config. Under
`pool: 'forks'` the runner deadlocks: a forked child cannot service the
`Atomics.wait` because the bridge worker lives in the same process.

## What is excluded, and why

Six spec files are excluded in `vitest.config.mjs`. All are JavaScript
*packaging* concerns with no Python meaning — not behaviour:

| File | Why |
|---|---|
| `workers.test.js`, `fuse-worker.test.js`, `worker-url.test.js` | Web Worker / worker-thread parallelism — out of scope (DECISIONS.md §17) |
| `cjs-interop.test.js` | CommonJS vs ESM module interop |
| `feature-flags.test.js` | Build-time flags; this port ships one build |
| `cache-invalidation.test.js` | Imports internal TypeScript source, not the public API |

`internals.test.ts`, `package-types.test.ts` and `typings.test.ts` are not
collected either — they test TypeScript type declarations.

## The 12 remaining failures

**None is a bug in the port.** Every one is either a language-boundary
limitation or a divergence this project chose deliberately and documented.

### Ten: JavaScript callables cannot cross a language boundary

A function is a closure over a live JS heap. It cannot be serialised to JSON
and rebuilt in Python. Where the suite supplies one, the bridge refuses the
call loudly rather than quietly substituting a default — which would make a
test pass for the wrong reason.

| Test | Callable |
|---|---|
| `limit honors a custom sortFn at a score tie` | `sortFn` |
| `limit matches a stable sort when a custom sortFn ties distinct items` | `sortFn` |
| `limit matches unlimited slice on a reordered index with a tying sortFn` | `sortFn` |
| `createIndex: ensure keys can be created with getFn` | `getFn` |
| `parseIndex: search with getFn` | `getFn` |
| `toJSON: strips getFn from keys for serialization` | `getFn` |
| `function tokenizer is exercised on a fixture` | `tokenize` |
| `Intl.Segmenter recipe with isWordLike filter…` | `tokenize` |
| `same custom tokenizer applied at index time and query time` | `tokenize` |
| `registers a custom searcher plugin` | `Fuse.use(class)` |

Note the bridge *does* carry **regex** tokenizers — `/[\w.+-]+/g` is sent as
`{source, flags}` and rebuilt with `re.ASCII`, because JavaScript's `\w` is
always ASCII-only while Python's is Unicode-aware. Those tests pass.

The Python port supports all of these features natively; you simply cannot
hand a Python function to it *from JavaScript*.

### One: the deliberate document-list copy

`Add object to Index` asserts `refIndex === Books.length - 1` **after** adding
a document. That only holds because fuse.js keeps the caller's array by
reference and `add()` pushes into it — so `Books.length` grows too.

This port copies the list instead (DECISIONS.md §13), because silently
mutating an argument is surprising in Python. The test failing is the
divergence being observable, exactly as documented — not a defect.

### One: an error message corrected for Python

`Fuse.match(..., {useTokenSearch: true})` throws, correctly, and with the same
message except for four characters:

```
fuse.js : "... Use new Fuse(...).search(...) instead."
port     : "... Use Fuse(...).search(...) instead."
```

`new` is JavaScript syntax. Telling a Python user to type it would be wrong.
See DECISIONS.md §19.

## Honesty notes

- This bridge is **test infrastructure only**. Nothing under `src/` imports
  it, and the shipped package has no Node dependency. The "no source-language
  runtime" rule applies to the *port*, not to its test harness — the same way
  `fuzz/oracle.js` runs fuse.js as an oracle.
- The bridge adds a JSON round-trip per call, so these tests are slower than
  the native `pytest` suite. They measure *behaviour*, never performance.
- Failures are surfaced, never suppressed. There is no try/catch that turns a
  bridge limitation into a pass.
