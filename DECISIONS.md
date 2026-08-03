# DECISIONS.md

Every non-trivial divergence between this port and fuse.js v7.5.0, and why.

The port's standard is **behavioural equivalence**: same results, same order,
same match indices, same errors. Where that was not achievable, or where
achieving it would have produced code a Python reviewer should reject, the
divergence is recorded here with the evidence behind it.

Source: `krisk/Fuse` v7.5.0 @ `45bac9fe2e71fe8c680c861a35a8b226c4ae6d5a`.

---

## 1. `Math.pow` and `Math.log` differ between CPython and V8 — scores agree to ~1e-13, not to the bit

**The problem.** CPython's `**` delegates to the platform libm, which is
correctly rounded. V8's `Math.pow` routes to `base::ieee754::pow`, an fdlibm
derivative that is not. They are different functions:

```
pow(0.1, 0.3846666666666666)
  CPython : 0.4124139370464501     <- correctly rounded
  V8      : 0.41241393704645002
```

Measured over 5,000 random `(base, exponent)` pairs in the range the scorer
uses: **10.04% disagree, always by exactly 1 ULP.** `compute_score_single`
multiplies one `pow` per matched key, and token search adds an IDF `log` per
term — so the error compounds through the scoring chain.

**What was tried.** `src/fusejs/_fdlibm.py` is a ~200-line transcription of
fdlibm's `__ieee754_pow`, the algorithm V8 is documented to use. All 23 of its
constants were verified bit-exact against their published patterns. It reaches
**95.6% bit-exact agreement with V8**, up from 90.0% for native `**`.

It does not reach 100%, and the evidence says it cannot easily:

- The residual ~4.4% is *uniformly* distributed across every branch of the
  algorithm (3.5–5.8% per branch). A structural bug would cluster in one
  branch; uniform error means the algorithm is right and the target is not.
- `Math.exp(y*Math.log(x))` reproduces `Math.pow` only 65.2% of the time;
  `2**(y*Math.log2(x))` only 65.7%. So V8 is running its own fdlibm variant.

Closing the gap means black-box reverse-engineering V8's exact implementation
with no reference to diff against. Unbounded work, no guarantee.

**Decision.** Scores are compared with a **relative tolerance of 1e-12**
(measured worst case across 51k fuzz cases: 8.5e-14). Everything else —
result sets, ordering, match indices, keys, refIndex — is compared exactly and
matches exactly.

`FuseOptions.strict_js_pow` opts into the fdlibm transcription. It is **off by
default**: the native result is both faster (7.4 ns vs 12,080 ns per call —
**1639x**) and strictly more accurate than fuse.js's. Turning it on is for
differential work, not production search.

**Consequence, stated plainly.** 1 ULP is enough to split an exact score tie.
When it does, the `idx` tie-break fires in one engine and not the other, and
the two engines return the same documents in a different order. Under a
`limit` that can change which document makes the cut. Measured rate:
**8 in 51,569 fuzz cases (0.016%)**. This is reported as its own category in
`fuzz/log.txt` rather than folded into "score noise", because it is a real if
rare user-visible effect. An earlier claim in this project's notes that
ordering was never affected was wrong, and the fuzz run is what caught it.

## 2. UTF-16 code units vs. Unicode code points

JS strings index by UTF-16 code unit; Python strings index by code point.
`"😀".length` is `2` in JS and `1` in Python, and every index, length, and
match range in the Bitap algorithm inherits that difference.

**Decision.** The port uses code-point semantics — the Python-native, and
arguably more correct, reading. Match indices for astral-plane input therefore
differ from fuse.js's. Reproducing UTF-16 indexing would mean a code-unit view
layer threaded through the entire engine, penalising every ordinary search to
serve emoji-in-search-corpus.

The fuzz harness and property tests restrict generated text to the BMP, where
the two agree. This is a known, bounded divergence, not an untested corner.

## 3. 32-bit bitwise emulation in the Bitap scan

JS bitwise operators coerce to signed 32-bit and wrap; Python ints are
arbitrary precision, so `x << 1` grows without bound where JS drops the
overflow bit.

**Decision.** Mask with `& 0xFFFFFFFF` after each shift. Unsigned emulation is
sufficient — and simpler than reproducing the sign bit — because the scan only
ever tests its bit arrays against a single-bit mask, so `bitArr[j] & mask` is
non-zero in JS exactly when it is non-zero here.

## 4. `Math.round` is half-up; Python's `round` is banker's

`Math.round(0.5)` is `1`; `round(0.5)` is `0`. The field-length norm quantises
through this function, and the norm multiplies into every score.

**Decision.** `_js.js_round` implements `math.floor(value + 0.5)`. Used only
where JS parity requires it, not as a general replacement for `round`.

## 5. Diacritic ranges are frozen, not derived

The original strips combining marks with a hand-written regex character class
of 223 ranges. The tempting Python equivalent is
`unicodedata.category(c) == "Mn"`.

**Decision.** Transcribe the 223 ranges mechanically from the TS source
(extracted by script, verified to consume the original character class
exactly) rather than deriving them.

They are **not the same set**: the original includes several spacing-combining
(`Mc`) marks and is a frozen snapshot of one Unicode version. Deriving the set
live would silently change which strings compare equal whenever Python's
bundled Unicode data changed — a scoring change nobody asked for, arriving
with a point release.

## 6. Errors are a typed hierarchy, not a message string

fuse.js throws bare `Error` and distinguishes failures only by message text.

**Decision.** Each failure mode gets a class (`PatternLengthError`,
`InvalidKeyWeightError`, `InvalidDocIndexError`, ...), each inheriting both
`FuseError` and the built-in a Python caller would instinctively catch
(`ValueError`, `IndexError`, `TypeError`). Message text is kept byte-identical
to the original so a differential run can compare it directly.

Catching a specific failure is table stakes in Python; string-matching on
`str(exc)` is not something a reviewer should accept.

## 7. `merge_indices` is pure

The original sorts the caller's list in place and mutates the range objects it
returns.

**Decision.** Return fresh tuples and leave the input alone. fuse.js gets away
with mutation because its ranges are freshly-allocated arrays; here a
`RangeTuple` is an immutable tuple that may be shared with a `SearchResult` the
caller still holds. Aliasing bugs of that shape are extremely hard to find.

## 8. An empty Bitap pattern raises instead of hanging

`search("", ...)` loops forever in the original: `indexOf("", n)` returns `n`
without advancing, so `bestLocation` never moves.

**Decision.** Raise `ValueError`. Unreachable through the public API —
`BitapSearch` builds no chunks for an empty pattern — but a free guard against
an infinite loop is worth having.

## 9. Index serialisation is wire-compatible, not idiomatic

**Decision.** `FuseIndex.to_dict()` emits fuse.js's exact on-the-wire shape,
including the `$` field name and the terse `v`/`i`/`n` keys, rather than a
friendlier Python schema.

An index built by either engine loads in the other. That interoperability is
worth more than prettier JSON — and it is what lets the differential harness
compare *index builds* directly rather than only search results (8/8
wire-identical).

## 10. `functools.cmp_to_key` is not used

Its typeshed stub accepts only `-> int` comparators. fuse.js's `sortFn`
contract allows any number, and the obvious Python spelling of a comparator is
`a.score - b.score`, which returns a float.

**Decision.** A ten-line `ComparatorKey` class. Spending Zero-Unsafe budget on
a `cast` to satisfy a stub's narrowness would have been the wrong trade.

## 11. `heapq` is not used for top-N selection

`heapq` is a min-heap ordered by `<`. Driving it with a three-way comparator
means wrapping every element on the way in and out.

**Decision.** Port the original's explicit max-heap. It avoids an allocation in
the hot path of every limited search, and — more importantly — keeps the
eviction order provably identical to the original's, which is what makes
`search(q, limit=n)` equal `search(q)[:n]`.

## 12. `get()` resolves attributes as well as mapping keys

The original resolves key paths against plain JS objects.

**Decision.** `_prop` falls back to `getattr` when the document is not a dict.
Python documents are as likely to be dataclasses, ORM rows, or Pydantic models
as dicts, and a fuzzy-search engine that only accepts dicts would need a
conversion pass at every call site.

Missing values still resolve to `None` rather than raising, matching JS's
`undefined`, so a partially-shaped document yields the fields it does have
instead of failing the whole search.

## 13. `Fuse` copies the document list

fuse.js assigns the caller's array by reference and pushes straight into it, so
`fuse.add(doc)` mutates the caller's list as a side effect.

**Decision.** Take a shallow copy. Mutating an argument is surprising in
Python, and the index would be stale for externally-applied mutations anyway,
so the aliasing bought nothing but a footgun. Documents themselves are shared,
not deep-copied — results still return the caller's own objects.

## 14. Detection regexes anchor with `\A`/`\Z`, not `^`/`$`

Python's `$` also matches *before* a trailing newline; JS's does not.

**Decision.** The extended-search matcher regexes use `\A...\Z`. Without it,
`"=term\n"` would satisfy an exact-match operator that fuse.js rejects. (In
practice `parse_query` trims its input first, so the difference is only
observable on the patterns directly — but the anchors are correct on their own
terms, and the test pins them.)

## 15. JS truthiness where the parser depends on it

`[]` and `{}` are truthy in JavaScript and falsy in Python.

**Decision.** `_js_truthy` in the query parser. This is not pedantry: fuse.js
treats `{"$or": []}` as a valid empty operator node, and Python's `bool([])`
would have classified it as a *leaf* and then rejected it for having a
non-string pattern. Caught by a test written against the oracle's actual
output.

## 16. The default tokenizer evaluates Unicode categories directly

The original tokenizes with `/[\p{L}\p{M}\p{N}_]+/gu`. Python's `re` has no
`\p{...}` support, and the third-party `regex` module would break the
zero-runtime-dependency guarantee.

**Decision.** Evaluate the class against `unicodedata.category` directly, with
a per-character memo. Including Mark (`\p{M}`) is load-bearing — without it,
Devanagari and NFD-normalised Latin shatter into fragments.

A compiled pattern passed as `tokenize` is always applied globally: JS
distinguishes a regex with the `g` flag from one without, and Python patterns
carry no such flag, so the global reading is the only coherent one.

## 17. `workers/*` is out of scope

`src/workers/*` (335 LOC) implements Web Worker and worker-thread parallelism.

**Decision.** Excluded, per the project brief. It is JS-runtime-specific
plumbing, not search behaviour; the Python equivalent would be a
`concurrent.futures` design with entirely different semantics, and fuse.js's
own `FuseWorker` already refuses token search because per-shard corpus
statistics diverge from single-threaded results. The remaining ~3,283 lines
are ported in full.

## 18. Build feature flags are retained

fuse.js ships several builds; `fuse.basic` omits extended, logical and token
search, and its entry point guards each with an environment variable.

**Decision.** `features.py` keeps the three flags, all enabled, so the
reduced-build contract stays representable and its error paths remain
reachable and testable rather than becoming dead code paths that were never
ported at all.

## 19. One error message is corrected rather than copied

Every error message in this port is byte-identical to fuse.js's — with one
deliberate exception:

```
fuse.js : "... Use new Fuse(...).search(...) instead."
port     : "... Use Fuse(...).search(...) instead."
```

**Decision.** Drop the `new`. It is JavaScript syntax; telling a Python
developer to type it would be actively wrong, and an error message exists to
help the person reading it.

This costs one test in the compatibility run (`throws when useTokenSearch is
true`), which asserts the exact string. That failure is the divergence being
observable, and is listed as such in `compat/README.md` rather than papered
over.

## 20. The original JS test suite runs against the port via a bridge

**The problem.** "The original test suite passing against your port" is
trivial for a same-language port and looks impossible across languages: the
suite is JavaScript run by vitest, and the rules forbid editing it.

**Decision.** Don't translate the tests — redirect them. All 13 behavioural
spec files import the engine from one specifier, `'../dist/fuse.mjs'`. A
vitest alias points that at a shim implementing the fuse.js API and delegating
every call to the Python port over a JSON bridge. **No test file is modified.**

The awkward constraint is that the tests call `fuse.search(...)`
*synchronously*. Node cannot block on a pipe — `subprocess.stdin.fd` is not
exposed and its pipes are non-blocking — so the bridge blocks the calling
thread with `Atomics.wait` on a `SharedArrayBuffer` while a worker thread does
the actual I/O. That in turn forces `pool: 'threads'`; under `pool: 'forks'`
the runner deadlocks.

**Result: 285 of 297 pass (95.96%).** None of the 12 failures is a port bug —
ten are JavaScript callables (`sortFn`, `getFn`, function `tokenize`,
`Fuse.use`) which cannot cross a language boundary at all, and two are the
divergences documented in §13 and §19. Full classification in
`compat/README.md`.

Regex tokenizers *do* cross: they are sent as `{source, flags}` and rebuilt
with `re.ASCII`, because JavaScript's `\w` is always ASCII-only while
Python's is Unicode-aware by default.

Like `fuzz/oracle.js`, this is test infrastructure. Nothing under `src/`
imports it and the shipped package still has no Node dependency.

