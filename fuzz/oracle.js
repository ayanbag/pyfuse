// Node-side oracle for the differential harness.
//
// Reads newline-delimited JSON commands on stdin and writes one
// newline-delimited JSON response per command on stdout. Keeping it a
// long-lived process (rather than one `node -e` per case) is what makes a
// 60-second fuzz run produce tens of thousands of cases instead of hundreds.
//
// This file is oracle-side only. Nothing here ships with the Python package —
// see DECISIONS.md on the "no source-language runtime" rule.

import { createInterface } from 'node:readline'
import Fuse from '../tests/original/dist/fuse.mjs'

const rl = createInterface({ input: process.stdin, terminal: false })

// Sort keys so the JSON text itself is comparable, not just the parsed value.
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((k) => [k, canonical(value[k])])
    )
  }
  return value
}

function handle(cmd) {
  switch (cmd.op) {
    case 'version':
      return { version: Fuse.version }

    case 'match':
      return Fuse.match(cmd.pattern, cmd.text, cmd.options || {})

    // Batch `Math.pow`, for verifying the fdlibm port in src/pyfuse/_fdlibm.py.
    // V8's Math.pow is not correctly rounded, so the Python port has to
    // reproduce *its* rounding rather than the true result.
    //
    // Operands and results travel as little-endian hex float bits: exact in
    // both directions, and it sidesteps JSON's inability to carry Infinity
    // and NaN at all.
    case 'pow': {
      const buf = Buffer.allocUnsafe(8)
      const decode = (hex) => Buffer.from(hex, 'hex').readDoubleLE(0)
      return cmd.pairs.map(([b, e]) => {
        buf.writeDoubleLE(Math.pow(decode(b), decode(e)))
        return buf.toString('hex')
      })
    }

    case 'search': {
      const fuse = new Fuse(cmd.docs, cmd.options || {})
      return fuse.search(cmd.query, cmd.searchOptions || undefined)
    }

    case 'createIndex': {
      const index = Fuse.createIndex(cmd.keys, cmd.docs, cmd.options || {})
      return index.toJSON()
    }

    case 'parseQuery':
      // Strip the attached searcher instances; only the tree shape is
      // comparable across languages.
      return JSON.parse(
        JSON.stringify(Fuse.parseQuery(cmd.query, cmd.options || {}, { auto: false }))
      )

    default:
      throw new Error(`unknown op: ${cmd.op}`)
  }
}

rl.on('line', (line) => {
  if (!line.trim()) return
  let response
  try {
    response = { ok: true, result: canonical(handle(JSON.parse(line))) }
  } catch (err) {
    response = { ok: false, error: err.message }
  }
  process.stdout.write(JSON.stringify(response) + '\n')
})
