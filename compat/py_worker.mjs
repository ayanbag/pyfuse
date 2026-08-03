// Worker side of the synchronous bridge.
//
// This thread owns the Python process and talks to it asynchronously, which is
// the only way Node will do pipe I/O. The *main* thread blocks on
// `Atomics.wait` until we signal, which is what makes the call look synchronous
// to the vendored vitest suite.

import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { parentPort, workerData } from 'node:worker_threads'

const { port, signal } = workerData
const here = path.dirname(fileURLToPath(import.meta.url))

const python = spawn(
  process.env.FUSEJS_PYTHON || 'python',
  ['-u', path.join(here, 'server.py')],
  {
    stdio: ['pipe', 'pipe', 'inherit'],
    cwd: path.resolve(here, '..'),
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' }
  }
)

const lines = createInterface({ input: python.stdout })
const waiting = []
lines.on('line', (line) => {
  const resolve = waiting.shift()
  if (resolve) resolve(line)
})

function request(payload) {
  return new Promise((resolve) => {
    waiting.push(resolve)
    python.stdin.write(JSON.stringify(payload) + '\n')
  })
}

port.on('message', async (payload) => {
  let response
  try {
    response = JSON.parse(await request(payload))
  } catch (err) {
    response = { ok: false, error: `bridge failure: ${err.message}` }
  }
  port.postMessage(response)
  // Release the blocked main thread.
  Atomics.store(signal, 0, 1)
  Atomics.notify(signal, 0)
})

parentPort.on('message', (msg) => {
  if (msg === 'shutdown') {
    python.kill()
    process.exit(0)
  }
})
