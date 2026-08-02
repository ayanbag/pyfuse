// Node side of the benchmark. Reads one JSON payload on stdin, runs the same
// workload the Python side runs, and writes one JSON result on stdout.
//
// Run via `python bench/run.py`, not directly — the point is that both engines
// see byte-identical input.

import Fuse from '../tests/original/dist/fuse.mjs'

function percentile(values, fraction) {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const index = Math.min(
    sorted.length - 1,
    Math.round(fraction * (sorted.length - 1))
  )
  return sorted[index]
}

function mean(values) {
  return values.reduce((a, b) => a + b, 0) / values.length
}

async function readStdin() {
  const chunks = []
  for await (const chunk of process.stdin) chunks.push(chunk)
  return Buffer.concat(chunks).toString('utf8')
}

const { corpus, queries, options } = JSON.parse(await readStdin())

const startupStart = performance.now()
const fuse = new Fuse(corpus, options)
const startupMs = performance.now() - startupStart

// Warm up so the JIT has compiled the hot loop; the Python side warms up too.
for (const query of queries.slice(0, Math.min(50, queries.length))) {
  fuse.search(query)
}

const latencies = []
let totalHits = 0
const benchStart = performance.now()
for (const query of queries) {
  const callStart = performance.now()
  const results = fuse.search(query)
  latencies.push(performance.now() - callStart)
  totalHits += results.length
}
const elapsedMs = performance.now() - benchStart

process.stdout.write(
  JSON.stringify({
    engine: 'fuse.js',
    runtime: `Node ${process.versions.node} / V8 ${process.versions.v8}`,
    startup_ms: Number(startupMs.toFixed(3)),
    searches: queries.length,
    total_hits: totalHits,
    throughput_per_s: Number((queries.length / (elapsedMs / 1000)).toFixed(1)),
    latency_ms: {
      mean: Number(mean(latencies).toFixed(4)),
      p50: Number(percentile(latencies, 0.5).toFixed(4)),
      p95: Number(percentile(latencies, 0.95).toFixed(4)),
      p99: Number(percentile(latencies, 0.99).toFixed(4)),
      max: Number(Math.max(...latencies).toFixed(4))
    },
    peak_rss_bytes: process.memoryUsage().rss
  })
)
