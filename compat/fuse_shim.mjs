// A drop-in stand-in for `dist/fuse.mjs` that delegates to the **Python port**.
//
// The vendored vitest suite imports `Fuse from '../dist/fuse.mjs'`. A vitest
// alias points that specifier here, so the original test files run — byte for
// byte unmodified — against fusejs-python instead of fuse.js.
//
// Everything is synchronous, because the tests are.

import { callPython } from './py_bridge.mjs'

// Options carrying functions cannot be serialised. Flag them so the Python
// side can refuse the call loudly instead of silently searching with defaults
// and passing a test for the wrong reason.
const FUNCTION_OPTIONS = ['getFn', 'sortFn', 'tokenize']

function prepareOptions(options) {
  if (!options) return {}
  const copy = { ...options }
  let hasFunctions = false

  // A regex tokenizer *can* cross the boundary — send its source and flags and
  // let Python rebuild it. A function tokenizer cannot.
  if (copy.tokenize instanceof RegExp) {
    copy.tokenize = {
      __regex__: true,
      source: copy.tokenize.source,
      flags: copy.tokenize.flags
    }
  }

  for (const name of FUNCTION_OPTIONS) {
    if (typeof copy[name] === 'function') {
      hasFunctions = true
      delete copy[name]
    }
  }
  // `keys` may contain per-key getFn callbacks too.
  if (Array.isArray(copy.keys)) {
    copy.keys = copy.keys.map((key) => {
      if (key && typeof key === 'object' && typeof key.getFn === 'function') {
        hasFunctions = true
        const { getFn, ...rest } = key
        return rest
      }
      return key
    })
  }
  if (hasFunctions) copy.__hasFunctions__ = true
  return copy
}

// `null` for "no index", a handle id for a real one, and a marker otherwise so
// the Python port raises its own InvalidIndexTypeError rather than the shim
// quietly dropping bad input.
function encodeIndex(index) {
  if (index === undefined || index === null) return null
  if (index instanceof FuseIndexHandle) return index._id
  return '__invalid__'
}

class FuseIndexHandle {
  constructor(id) {
    this._id = id
  }

  // `records` and `keys` are plain properties on the original, and the suite
  // reads them after mutations, so they must be live rather than snapshotted.
  get records() {
    return callPython({ op: 'indexRecords', id: this._id })
  }

  get keys() {
    return callPython({ op: 'indexKeys', id: this._id })
  }

  toJSON() {
    return callPython({ op: 'indexToJSON', id: this._id })
  }

  size() {
    return callPython({ op: 'indexSize', id: this._id })
  }

  removeAt(idx) {
    return callPython({ op: 'indexRemoveAt', id: this._id, idx })
  }

  removeAll(indices) {
    return callPython({ op: 'indexRemoveAll', id: this._id, indices })
  }

  add(doc, docIndex) {
    return callPython({ op: 'indexAdd', id: this._id, doc, docIndex })
  }

  setKeys(keys) {
    return callPython({ op: 'indexSetKeys', id: this._id, keys })
  }
}

export default class Fuse {
  constructor(docs, options, index) {
    this._id = callPython({
      op: 'new',
      docs,
      options: prepareOptions(options),
      // An index that isn't a FuseIndexHandle must still reach Python, so the
      // port's own type check fires. Collapsing it to `null` here would
      // silently accept the invalid value.
      index: encodeIndex(index)
    })
  }

  search(query, searchOptions) {
    return callPython({
      op: 'search',
      id: this._id,
      query,
      searchOptions: searchOptions || null
    })
  }

  add(doc) {
    return callPython({ op: 'add', id: this._id, doc })
  }

  // `remove` takes a JavaScript predicate, which cannot be serialised. Instead
  // of refusing, evaluate it *here*: pull the current documents across, run the
  // predicate in JS where it belongs, and send back the matching indices. The
  // observable behaviour is identical.
  remove(predicate = () => false) {
    const docs = callPython({ op: 'docs', id: this._id })
    const indices = []
    const removed = []
    docs.forEach((doc, idx) => {
      if (predicate(doc, idx)) {
        indices.push(idx)
        removed.push(doc)
      }
    })
    if (indices.length) {
      callPython({ op: 'removeIndices', id: this._id, indices })
    }
    return removed
  }

  removeAt(idx) {
    return callPython({ op: 'removeAt', id: this._id, idx })
  }

  setCollection(docs, index) {
    return callPython({
      op: 'setCollection',
      id: this._id,
      docs,
      // An index that isn't a FuseIndexHandle must still reach Python, so the
      // port's own type check fires. Collapsing it to `null` here would
      // silently accept the invalid value.
      index: encodeIndex(index)
    })
  }

  getIndex() {
    return new FuseIndexHandle(callPython({ op: 'getIndex', id: this._id }))
  }

  // The suite reaches into these private fields in several places. The shim
  // exposes them because the original does — presenting the Python port
  // through the JavaScript API is the whole job.
  get _docs() {
    return callPython({ op: 'docs', id: this._id })
  }

  get _invertedIndex() {
    const raw = callPython({ op: 'invertedIndex', id: this._id })
    if (!raw) return null
    // These are `Map`s in the original and the suite calls `.has()`, `.size`
    // and compares them with `toEqual(new Map(...))`, so plain objects will
    // not do. Numeric keys have to be restored from their string form.
    return {
      fieldCount: raw.fieldCount,
      df: new Map(Object.entries(raw.df)),
      docFieldCount: new Map(
        Object.entries(raw.docFieldCount).map(([k, v]) => [Number(k), v])
      ),
      docTermFieldHits: new Map(
        Object.entries(raw.docTermFieldHits).map(([k, v]) => [
          Number(k),
          new Map(Object.entries(v))
        ])
      )
    }
  }

  static get version() {
    return callPython({ op: 'version' })
  }

  static get config() {
    return callPython({ op: 'config' })
  }

  static createIndex(keys, docs) {
    return new FuseIndexHandle(callPython({ op: 'createIndex', keys, docs }))
  }

  static parseIndex(data) {
    return new FuseIndexHandle(callPython({ op: 'parseIndex', data }))
  }

  static match(pattern, text, options) {
    return callPython({
      op: 'match',
      pattern,
      text,
      options: prepareOptions(options)
    })
  }

  static parseQuery(query, options) {
    return callPython({ op: 'parseQuery', query, options: prepareOptions(options) })
  }

  static use() {
    throw new Error(
      'bridge limitation: Fuse.use(plugin) registers a JavaScript class, ' +
        'which cannot be transferred to the Python port'
    )
  }
}

export { FuseIndexHandle as FuseIndex }
