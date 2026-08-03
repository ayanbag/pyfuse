# 5-minute presentation / demo video

Working script for the Port Mortem 2026 submission. Everything below is a
command that runs today — nothing here needs to be faked or edited in post.

**Rule for the whole video: never show a claim you don't immediately prove.**
Judges have seen a hundred demos assert "100% compatible". Show the test runner.

---

## Before you record

```bash
just build                      # deps installed, package importable
npm install                     # once, for the compat run
just check && just test         # confirm green so nothing surprises you live
python -m http.server 8000 --directory docs   # if demoing the playground locally
```

- Terminal at ~16pt, dark theme, window wide enough that `just compat`
  output doesn't wrap.
- Have these tabs open: the GitHub repo, the live playground, `DECISIONS.md`.
- Do one dry run. The fuzz and compat runs take real time — know exactly how
  long you're sitting there so you can talk over it.

---

## The script (5:00)

### 0:00–0:35 — The gap, not the library

> "Python has excellent fuzzy string matching. `rapidfuzz` will tell you how
> close two strings are. What Python has *nothing* for is fuzzy **search** —
> give me a collection of records, weighted fields, nested paths, query
> operators, and rank them by relevance.
>
> That's what fuse.js is, and it only exists in JavaScript. So the capability
> isn't missing from computing — it's trapped in the wrong ecosystem. I moved
> it."

Don't say "I ported a library." Say what was missing and for whom.

### 0:35–1:20 — It solves a real problem

```bash
python examples/ticket_triage.py
```

> "Ten thousand support tickets a month, written by humans. Misspelled service
> names, half a stack trace pasted in. This routes them against a runbook
> catalogue — five auto-routed, one suggested, two escalated.
>
> The two escalated ones are about a coffee machine and a VPN request. That
> matters: a fuzzy matcher *always* returns its best guess, so the score
> cutoff, not the search, is what stops a confident wrong answer."

If you have time for a second, `reconcile.py` is the stronger data-engineering
story (`ACME CORPORATION LTD` vs `Acme Corp. Limited`). Pick one, not both.

### 1:20–2:30 — The proof: the *original* suite runs against Python

This is the most important 70 seconds in the video. Slow down.

```bash
just compat
```

> "This is the fuse.js test suite. JavaScript, run by vitest, byte-for-byte
> unmodified — I never touched a test file. A vitest alias redirects the one
> import every spec shares to a shim that forwards every call into Python.
>
> 285 of 297 pass."

Then pre-empt the obvious question:

> "Twelve fail. Ten of them hand fuse.js a JavaScript *function* — a `sortFn`,
> a `getFn` — and a closure over a live JS heap cannot be serialised into
> Python at any price. The other two are divergences I chose on purpose and
> documented. None of the twelve is a bug in the port."

### 2:30–3:20 — Behavioural equivalence, and the honest limit

```bash
just fuzz 60
```

Talk while it runs:

> "This generates random datasets, queries and options, runs them through both
> engines, and compares. Fifty-one thousand cases, **zero structural
> divergences** — same results, same order, same match indices.
>
> Scores agree to about 1e-13, not bit-for-bit. That's not a shortcut, it's a
> wall: CPython's `pow` is correctly rounded and V8's isn't, and they disagree
> by one unit in the last place on about 10% of calls.
>
> And that has one visible consequence. One ULP is enough to break a score
> tie, so occasionally the two engines return the same documents in a
> different order — eight times in fifty-one thousand. I claimed early on that
> ordering was never affected. The fuzz run proved me wrong, so I corrected
> it. That's in DECISIONS.md."

**Owning a corrected mistake on camera is worth more than a clean claim.**

### 3:20–4:10 — Play with it live

Open the playground (or `just demo --interactive`).

> "The port is pure Python with zero dependencies, which means it runs
> unmodified in the browser under Pyodide. This page installs the actual wheel
> — the same one you'd `pip install`."

Type `ste ham` → point at **Ste**ve / **Ham**ilton highlighting.
Drag threshold to 0 → results vanish. Tick `use_extended_search`, type `^the`.

> "Same dataset as the official fuse.js demo — their own `books.json` — so you
> can put the two side by side and compare scores."

### 4:10–4:40 — Engineering quality

```bash
just unsafe
```

> "Zero runtime dependencies, matching fuse.js's own design. `mypy --strict`
> clean. Zero casts, zero `type: ignore`, zero bare excepts. Twenty entries in
> DECISIONS.md, each with the evidence behind it."

Scroll `DECISIONS.md` for two seconds — don't read it.

### 4:40–5:00 — Close on the honest number

> "One number I'm not hiding: fuse.js is about 13 times faster. V8 JITs the
> Bitap inner loop, CPython interprets it. The port's value is reach and
> parity, not speed, and the benchmark methodology is in the repo.
>
> Everything you saw is one command each: `just test`, `just compat`,
> `just fuzz`. Thanks."

---

## Cheat sheet — the six numbers

| | |
|---|---|
| Original suite | **285 / 297** (95.96%), files unmodified |
| Fuzz | **0** structural divergences in **51,569** cases |
| Score agreement | ~**1e-13** relative (worst 8.5e-14) |
| Tie-order flips | **8 / 51,569** (0.016%) — disclosed |
| Escape hatches | **0** |
| Speed | fuse.js **~13x** faster — stated, not buried |

## If you make slides

Six slides, one idea each. Don't read them aloud.

1. **The gap** — `rapidfuzz` = strings. Elasticsearch = a server. Nothing in
   between. fuse.js is the missing middle, and it's JS-only.
2. **What it is** — Bitap + weighted keys + extended queries + IDF token search.
3. **285/297** — the original JS suite, unmodified, running against Python.
4. **0 / 51,569** — differential fuzzing against the live oracle.
5. **The 1-ULP wall** — one honest limit, with the consequence disclosed.
6. **13x slower, 0 dependencies, 0 escape hatches** — the trade, stated plainly.

## Recording notes

- OBS or the built-in screen recorder; 1080p is plenty.
- Terminal only. No talking-head, no intro animation — you have 300 seconds.
- If `just fuzz 60` is too long to sit through, run `just fuzz 20` on camera
  and say you shortened it; the committed `fuzz/log.txt` is the 60-second run.
- Upload unlisted to YouTube, paste the link in the form.
