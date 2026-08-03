// Synchronous Node -> Python bridge.
//
// The vendored fuse.js test suite calls `fuse.search(...)` synchronously and
// asserts on the return value. Making it async would require editing the tests,
// which the competition rules forbid — the suite must stay byte-for-byte
// unmodified. So the call has to *block*.
//
// Node cannot block on a pipe directly (`subprocess.stdin.fd` is not exposed,
// and pipes are non-blocking). The standard workaround: put the async I/O in a
// worker thread and have the main thread block on `Atomics.wait` until the
// worker signals completion.

import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { MessageChannel, Worker, receiveMessageOnPort } from 'node:worker_threads'

const here = path.dirname(fileURLToPath(import.meta.url))

const signal = new Int32Array(new SharedArrayBuffer(4))
const { port1, port2 } = new MessageChannel()

const worker = new Worker(path.join(here, 'py_worker.mjs'), {
  workerData: { port: port2, signal },
  transferList: [port2]
})
// Don't hold the event loop open once the tests finish.
worker.unref()

let calls = 0

export function callPython(payload) {
  calls += 1
  Atomics.store(signal, 0, 0)
  port1.postMessage(payload)

  // Block this thread until the worker stores 1 and notifies.
  Atomics.wait(signal, 0, 0)

  const message = receiveMessageOnPort(port1)
  if (!message) {
    throw new Error('python bridge returned no message')
  }

  const response = message.message
  if (!response.ok) {
    // Surface Python-side errors as JS errors so `expect(...).toThrow()` in
    // the original suite still behaves.
    const error = new Error(response.error)
    error.name = response.error_type || 'Error'
    throw error
  }
  return response.result
}

export function bridgeCallCount() {
  return calls
}

export function shutdownBridge() {
  worker.postMessage('shutdown')
}
