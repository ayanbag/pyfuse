# compat/ — running the original fuse.js test suite against the Python port

**285 of 297 original tests pass (95.96%), with every test file byte-for-byte
unmodified.**

```bash
npx vitest run --config compat/vitest.config.mjs
# or
just compat
```

## The problem

Deliverable 3 asks for "the original test suite passing against your port."
If you ported Rust to Rust that's a build flag. Going TypeScript → Python it
looks flatly impossible: the suite is JavaScript, vitest runs it, and the rules
forbid editing a single character of it.

## What made it possible

Every behavioural spec pulls the engine in from the same place:

```javascript
import Fuse from '../dist/fuse.mjs'   // 13 of the test files
```

One specifier, thirteen files. So don't touch the tests — move what that
specifier resolves to. `vitest.config.mjs` aliases it to `fuse_shim.mjs`, which
presents the fuse.js API and forwards every call down to Python. The tests
never find out.

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
    server.py             Python: drives the real pyfuse port
```

## The hard part: the calls have to be synchronous

The tests look like this:

```javascript
const result = fuse.search('old man')   // no await anywhere
expect(result.length).toBe(1)
```

There's no `await`, and adding one means editing the tests. So the bridge has
to genuinely block.

Node won't let you block on a pipe. `subprocess.stdin.fd` isn't exposed and the
pipes are non-blocking, so there's no `readSync` to reach for. The way through
is `Atomics.wait`:

1. The main thread posts the request to a worker thread, then calls
   `Atomics.wait` on a `SharedArrayBuffer`. That really does block it.
2. The worker does the pipe I/O asynchronously, which Node is perfectly happy
   with.
3. When the reply lands, the worker stores `1` and calls `Atomics.notify`.
4. The main thread wakes up, reads the message, returns it. As far as the test
   can tell, `search()` was synchronous.

This is also why the config says `pool: 'threads'`. Under `pool: 'forks'` the
runner just hangs forever — a forked child can't service the `Atomics.wait`,
because the bridge worker lives in the same process. That cost an evening.

## What's excluded, and why

Six spec files are excluded in `vitest.config.mjs`. All six are JavaScript
*packaging* concerns with no Python meaning at all — none of them is testing
search behaviour:

| File | Why |
|---|---|
| `workers.test.js`, `fuse-worker.test.js`, `worker-url.test.js` | Web Worker / worker-thread parallelism — out of scope (DECISIONS.md §17) |
| `cjs-interop.test.js` | CommonJS vs ESM module interop |
| `feature-flags.test.js` | Build-time flags; this port ships one build |
| `cache-invalidation.test.js` | Imports internal TypeScript source rather than the public API |

`internals.test.ts`, `package-types.test.ts` and `typings.test.ts` aren't
collected either — they assert things about TypeScript declaration files.

## The 12 that fail

None of them is a bug in the port. Each is either a hard language-boundary
limit or a divergence this project picked deliberately and wrote down.

### Ten: you can't send a JavaScript function to Python

A function is a closure over a live JS heap. There is no JSON encoding of that.
Where the suite supplies one, the bridge refuses the call loudly rather than
quietly falling back to a default — a default would make the test go green for
entirely the wrong reason, which is worse than failing.

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

Regexes, unlike functions, *do* cross. A tokenizer like `/[\w.+-]+/g` is sent
as `{source, flags}` and rebuilt on the Python side with `re.ASCII` — because
JavaScript's `\w` is ASCII-only always, even under `u`, while Python's is
Unicode-aware. Those tests pass.

The port supports every one of these features natively. You just can't hand it
a Python function *from JavaScript*.

### One: the deliberate document-list copy

`Add object to Index` asserts `refIndex === Books.length - 1` after adding a
document. That only holds because fuse.js keeps the caller's array by reference
and `add()` pushes into it, so `Books.length` grows as a side effect of the
call.

This port copies the list instead (DECISIONS.md §13). The test failing *is* the
divergence being observable, precisely as documented.

### One: an error message corrected for Python

`Fuse.match(..., {useTokenSearch: true})` throws, correctly, with the same
message apart from four characters:

```
fuse.js : "... Use new Fuse(...).search(...) instead."
port    : "... Use Fuse(...).search(...) instead."
```

`new` is JavaScript syntax. See DECISIONS.md §19.

## Honesty notes

- This bridge is **test infrastructure and nothing else**. Nothing under `src/`
  imports it, and the shipped package has no Node dependency. The
  "no source-language runtime" rule is about the *port*, not its test harness —
  same status as `fuzz/oracle.js`, which runs fuse.js as an oracle.
- Every call is a JSON round-trip, so this run is much slower than the native
  `pytest` suite. It measures behaviour. Never read a timing off it.
- Failures are surfaced, never swallowed. There's no try/catch anywhere in here
  that turns a bridge limitation into a pass.
