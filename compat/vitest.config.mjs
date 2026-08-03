// Runs the **unmodified** fuse.js vitest suite against the Python port.
//
// The only thing this changes is *which module* the tests import as `Fuse`:
// the alias below swaps `dist/fuse.mjs` for a shim that delegates every call
// to fusejs-python. No test file is touched — that is the whole point, and the
// competition rules require it.
//
//   npx vitest run --config compat/vitest.config.mjs
//
// Excluded specs are JavaScript-packaging concerns with no Python meaning:
// CommonJS interop, Web Worker URLs, TypeScript typings, build feature flags.
// They are listed explicitly rather than quietly skipped.

import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const original = path.resolve(here, '..', 'tests', 'original')

export default defineConfig({
  root: original,
  resolve: {
    alias: [
      {
        find: /^\.\.\/dist\/fuse\.mjs$/,
        replacement: path.join(here, 'fuse_shim.mjs')
      }
    ]
  },
  define: {
    __VERSION__: JSON.stringify('7.5.0'),
    __WORKER_IS_CJS__: 'false'
  },
  test: {
    globals: true,
    include: ['test/**/*.test.js'],
    exclude: [
      // Worker-thread parallelism — out of scope for the port (DECISIONS.md).
      'test/workers.test.js',
      'test/fuse-worker.test.js',
      'test/worker-url.test.js',
      // CommonJS/ESM packaging — a JavaScript distribution concern.
      'test/cjs-interop.test.js',
      // Build-time feature flags — this port ships a single build.
      'test/feature-flags.test.js',
      // Imports internal TS source directly rather than the public API.
      'test/cache-invalidation.test.js'
    ],
    testTimeout: 30_000,
    hookTimeout: 30_000,
    // Worker threads, not forks. The bridge blocks the calling thread with
    // `Atomics.wait`, which a forked child cannot service; a worker thread can,
    // because the bridge's own worker runs alongside it. Each test file gets
    // its own thread and therefore its own Python process, so instance handles
    // never interleave and files still run in parallel.
    pool: 'threads'
  }
})
