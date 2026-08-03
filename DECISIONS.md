# DECISIONS.md

Everywhere this port doesn't match fuse.js v7.5.0, and why I let it not match.

The bar I set was behavioural equivalence: same results, same order, same match
indices, same errors. Most of the time that was achievable. Where it wasn't, or
where hitting it would have meant writing Python that no reviewer should sign
off on, the trade is written down here along with whatever evidence talked me
into it.

Source: `krisk/Fuse` v7.5.0 @ `45bac9fe2e71fe8c680c861a35a8b226c4ae6d5a`.

---

## 1. CPython and V8 don't agree on `Math.pow`, so scores agree to ~1e-13 rather than to the bit

CPython's `**` goes to the platform libm and comes back correctly rounded.
V8's `Math.pow` goes to `base::ieee754::pow`, an fdlibm derivative, and doesn't.
They are simply different functions:

```
pow(0.1, 0.3846666666666666)
  CPython : 0.4124139370464501     <- correctly rounded
  V8      : 0.41241393704645002
```

I sampled 5,000 random `(base, exponent)` pairs across the range the scorer
actually uses: **10.04% disagree, always by exactly 1 ULP.**
`compute_score_single` does one `pow` per matched key and token search adds an
IDF `log` per term, so it compounds down the chain rather than cancelling.

I did try to close it. `src/fusejs/_fdlibm.py` is a ~200-line transcription of
fdlibm's `__ieee754_pow`, which is the algorithm V8 documents itself as using,
with all 23 constants checked bit-for-bit against their published patterns. It
gets to **95.6% agreement with V8**, up from 90.0% for native `**`.

That's where I stopped, and the reason I stopped is the shape of what's left:

- The residual 4.4% is spread evenly over every branch of the algorithm, 3.5%
  to 5.8% each. A transcription bug would pile up in one branch. Even spread
  means the algorithm is fine and I'm aiming at the wrong target.
- `Math.exp(y*Math.log(x))` only reproduces `Math.pow` 65.2% of the time, and
  `2**(y*Math.log2(x))` 65.7%. So V8 isn't composing it from primitives either
  — it's running its own variant.

Getting the rest means black-box reverse-engineering V8 with nothing to diff
against. Unbounded work, no promise it terminates, and the payoff is a
last-digit cosmetic.

**So:** scores are compared with a relative tolerance of 1e-12. Worst real
divergence across 51k fuzz cases was 8.5e-14, so there's about four orders of
magnitude of headroom in that number. Everything that isn't a float — result
sets, ordering, match indices, keys, refIndex — is compared exactly, and
matches exactly.

The fdlibm version is still there behind `FuseOptions.strict_js_pow`, off by
default. Native `**` is 7.4 ns per call against fdlibm's 12,080 ns — a 1639x
difference — and it's also *more* accurate than fuse.js is. Bit-matching a less
correct implementation is a differential-testing tool, not something you want
in production search.

**The part I got wrong.** I wrote in my notes early on that this only ever
affected the last digits and never the output. That was wrong. 1 ULP is enough
to break an exact score tie, and once the tie is broken the `idx` tie-break
fires in one engine and not the other, so both return the same documents in a
different order. Under a `limit` that changes which document survives. It
happens in **8 of 51,569 fuzz cases (0.016%)**. `fuzz/log.txt` gives it its own
category rather than folding it into "score noise", because it's small but
genuinely user-visible — and because the fuzz run catching my own claim out is
the best argument for having built the fuzz run.

## 2. UTF-16 code units vs. Unicode code points

JS indexes strings by UTF-16 code unit, Python by code point. `"😀".length` is
`2` in JS and `1` in Python, and every index, length and match range in Bitap
inherits that.

**Decision:** use code-point semantics, which is both Python-native and
arguably the more correct reading. Match indices on astral-plane input
therefore differ from fuse.js's.

Reproducing UTF-16 indexing would mean threading a code-unit view through the
entire engine and taxing every ordinary search to serve the emoji-in-corpus
case. The fuzz harness and property tests generate BMP text only, where the two
agree, so this is a bounded and known divergence rather than an untested
corner.

## 3. Emulating 32-bit bitwise wrap in the Bitap scan

JS bitwise operators coerce to signed 32-bit and wrap. Python ints don't, so
`x << 1` just keeps growing where JS drops the overflow bit.

**Decision:** mask with `& 0xFFFFFFFF` after each shift. Unsigned is enough —
no need to reproduce the sign bit — because the scan only ever tests its bit
arrays against a single-bit mask, and `bitArr[j] & mask` is non-zero in JS
exactly when it's non-zero here.

## 4. `Math.round` rounds half up, Python's `round` rounds to even

`Math.round(0.5)` is `1`. `round(0.5)` is `0`. The field-length norm quantises
through this, and the norm multiplies into every single score, so it isn't a
detail you get to skip.

**Decision:** `_js.js_round`, which is `math.floor(value + 0.5)`. Used only
where parity demands it, never as a blanket replacement for `round`.

## 5. The diacritic ranges are frozen, not derived

The original strips combining marks using a hand-written regex character class
of 223 ranges. The obvious Python move is `unicodedata.category(c) == "Mn"`.

**Decision:** transcribe the 223 ranges instead. I extracted them from the TS
source by script and verified the result consumes the original character class
exactly, rather than eyeballing it.

The two sets aren't the same. The original includes several spacing-combining
(`Mc`) marks and it's a snapshot of one particular Unicode version. Deriving it
live would mean the set of strings that compare equal quietly changes whenever
Python's bundled Unicode data does — a scoring change arriving in a point
release that nobody asked for and nobody would think to look for.

## 6. Errors are a typed hierarchy instead of a message string

fuse.js throws bare `Error` and you tell failures apart by reading the message.

**Decision:** give each failure mode a class — `PatternLengthError`,
`InvalidKeyWeightError`, `InvalidDocIndexError`, and so on — each inheriting
both `FuseError` and whichever builtin a Python caller would instinctively
reach for (`ValueError`, `IndexError`, `TypeError`). The message text stays
byte-identical to the original so the differential harness can compare it
directly.

Catching a specific exception is table stakes in Python. Asking callers to
string-match on `str(exc)` is not something a reviewer should let through.

## 7. `merge_indices` doesn't mutate its input

The original sorts the caller's list in place and mutates the range objects it
hands back.

**Decision:** return fresh tuples, leave the input alone. fuse.js gets away
with it because its ranges are freshly-allocated arrays. Here a `RangeTuple` is
an immutable tuple that might be shared with a `SearchResult` the caller is
still holding, and aliasing bugs of that shape are miserable to track down.

## 8. An empty Bitap pattern raises instead of hanging

`search("", ...)` loops forever in the original — `indexOf("", n)` returns `n`
without advancing, so `bestLocation` never moves.

**Decision:** raise `ValueError`. You can't reach it through the public API
because `BitapSearch` builds no chunks for an empty pattern, but a free guard
against an infinite loop is worth the two lines.

## 9. Index serialisation is wire-compatible rather than pretty

**Decision:** `FuseIndex.to_dict()` emits fuse.js's exact on-the-wire shape,
`$` field name and terse `v`/`i`/`n` keys included, instead of a friendlier
Python schema.

An index built by either engine loads in the other, which matters more than
readable JSON — and it's what lets the differential harness compare *index
builds* directly instead of only comparing search results. Currently 8/8
wire-identical.

## 10. Not using `functools.cmp_to_key`

Its typeshed stub only accepts `-> int` comparators. fuse.js's `sortFn`
contract allows any number, and the natural Python spelling is
`a.score - b.score`, which is a float.

**Decision:** a ten-line `ComparatorKey` class. Burning Zero-Unsafe budget on a
`cast` to work around a stub being narrower than the runtime would have been a
bad trade for ten lines.

## 11. Not using `heapq` for top-N

`heapq` is a min-heap ordered by `<`. Driving it from a three-way comparator
means wrapping every element on the way in and unwrapping on the way out.

**Decision:** port the original's explicit max-heap. It skips an allocation in
the hot path of every limited search, and more importantly it keeps the
eviction order provably identical to the original's — which is the thing that
makes `search(q, limit=n)` actually equal `search(q)[:n]`.

## 12. `get()` resolves attributes as well as mapping keys

The original resolves key paths against plain JS objects.

**Decision:** `_prop` falls back to `getattr` when the document isn't a dict.
Python documents are as likely to be dataclasses, ORM rows or Pydantic models
as they are dicts, and a search engine that only speaks dict would force a
conversion pass at every call site.

Missing values still resolve to `None` rather than raising, matching JS's
`undefined`, so a half-populated document returns the fields it does have
instead of failing the whole search.

## 13. `Fuse` copies the document list

fuse.js keeps the caller's array by reference and pushes straight into it, so
`fuse.add(doc)` grows the caller's list as a side effect.

**Decision:** take a shallow copy. Mutating an argument is surprising in
Python, and the index would be stale for externally-applied mutations anyway,
so the aliasing was buying a footgun and nothing else. The documents themselves
are shared rather than deep-copied, so results still hand back the caller's own
objects.

This is one of the two compat-suite failures, and it fails for exactly the
reason described here. See §20.

## 14. Detection regexes anchor with `\A`/`\Z` rather than `^`/`$`

Python's `$` also matches *before* a trailing newline. JS's doesn't.

**Decision:** the extended-search matcher regexes use `\A...\Z`. Without it
`"=term\n"` would satisfy an exact-match operator that fuse.js rejects. In
practice `parse_query` trims first so you can only observe the difference on
the patterns directly, but the anchors are right on their own terms and there's
a test pinning them.

## 15. JS truthiness, in the one place the parser depends on it

`[]` and `{}` are truthy in JavaScript and falsy in Python.

**Decision:** `_js_truthy` in the query parser. Not pedantry — fuse.js treats
`{"$or": []}` as a valid empty operator node, and Python's `bool([])` would
have classified it as a *leaf* and then rejected it for having a non-string
pattern. I found this because a test written against the oracle's real output
disagreed with what I'd assumed.

## 16. The default tokenizer evaluates Unicode categories directly

The original tokenizes with `/[\p{L}\p{M}\p{N}_]+/gu`. Python's `re` has no
`\p{...}`, and pulling in the third-party `regex` module would break the
zero-runtime-dependency promise.

**Decision:** evaluate the class against `unicodedata.category` directly, with
a per-character memo so it isn't slow. Including Mark (`\p{M}`) is
load-bearing: leave it out and Devanagari and NFD-normalised Latin shatter into
fragments.

A compiled pattern passed as `tokenize` is always applied globally. JS
distinguishes a regex with the `g` flag from one without; Python patterns carry
no such flag, so the global reading is the only coherent one available.

## 17. `workers/*` is out of scope

`src/workers/*` is 335 lines of Web Worker and worker-thread parallelism.

**Decision:** excluded, per the project brief. It's JS-runtime plumbing rather
than search behaviour, and the Python equivalent would be a
`concurrent.futures` design with genuinely different semantics — not a port,
a rewrite. Worth noting that fuse.js's own `FuseWorker` refuses token search,
because per-shard corpus statistics don't agree with single-threaded results.
The remaining ~3,283 lines are ported in full.

## 18. Build feature flags are kept

fuse.js ships several builds. `fuse.basic` drops extended, logical and token
search, and the entry point guards each one behind an environment variable.

**Decision:** `features.py` keeps all three flags, all enabled. The
reduced-build contract stays representable and its error paths stay reachable
and testable, instead of quietly becoming code paths that were never ported at
all and that nobody would notice were missing.

## 19. One error message is corrected rather than copied

Every error message in this port is byte-identical to fuse.js's, with exactly
one exception:

```
fuse.js : "... Use new Fuse(...).search(...) instead."
port    : "... Use Fuse(...).search(...) instead."
```

**Decision:** drop the `new`. It's JavaScript syntax. Telling a Python
developer to type it would be actively wrong, and the entire job of an error
message is to help whoever is reading it.

It costs one test in the compatibility run — `throws when useTokenSearch is
true` asserts the exact string. That failure is this decision being observable,
and it's listed as such in `compat/README.md` rather than quietly patched
around.

## 20. The original JS test suite runs against the port through a bridge

**The problem.** "The original test suite passing against your port" is a
one-liner for a same-language port and looks impossible across languages. The
suite is JavaScript, vitest runs it, and editing it is off the table.

**Decision:** don't translate the tests, redirect them. All 13 behavioural spec
files import the engine from one specifier, `'../dist/fuse.mjs'`. A vitest
alias points that at a shim implementing the fuse.js API, which forwards every
call to the Python port over a JSON bridge. No test file is modified.

The genuinely awkward part is that the tests call `fuse.search(...)`
synchronously, and Node can't block on a pipe — `subprocess.stdin.fd` isn't
exposed and the pipes are non-blocking. So the bridge blocks the calling thread
with `Atomics.wait` on a `SharedArrayBuffer` while a worker thread does the
actual I/O. That in turn forces `pool: 'threads'`; under `pool: 'forks'` the
whole runner deadlocks, which took a while to work out.

**Result: 285 of 297 pass (95.96%).** None of the 12 failures is a bug in the
port. Ten of them pass fuse.js a JavaScript function (`sortFn`, `getFn`, a
callable `tokenize`, `Fuse.use`) — a closure over a live JS heap doesn't
serialise, and the bridge refuses those calls loudly instead of substituting a
default and letting the test pass for the wrong reason. The other two are §13
and §19. Every one is named in `compat/README.md`.

Regex tokenizers *do* make it across: they're sent as `{source, flags}` and
rebuilt with `re.ASCII`, because JavaScript's `\w` is ASCII-only always, even
under the `u` flag, while Python's is Unicode-aware by default. Miss that and
non-Latin text tokenises differently on the two sides.

Same status as `fuzz/oracle.js`: this is test infrastructure. Nothing under
`src/` imports it and the shipped package still has no Node dependency.
