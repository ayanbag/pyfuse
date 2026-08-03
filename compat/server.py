"""Python side of the compatibility bridge.

Serves the `pyfuse` Python port to the *unmodified* fuse.js vitest suite, so
the original tests can execute against this port. One JSON request per line on
stdin, one JSON response per line on stdout.

Instances are held by integer handle, because the JS side needs to call
`fuse.search(...)`, `fuse.add(...)` and so on against a specific object across
several round-trips.

Test infrastructure only — nothing under `src/` imports this.
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pyfuse import (  # noqa: E402
    Fuse,
    FuseOptions,
    __version__,
    create_index,
    parse_index,
)
from pyfuse.core.query_parser import (  # noqa: E402
    ParsedLeaf,
    ParsedOperator,
)
from pyfuse.core.query_parser import parse as parse_query  # noqa: E402

_instances: dict[int, Fuse] = {}
_indexes: dict[int, Any] = {}
_next_handle = 0


def _handle() -> int:
    global _next_handle
    _next_handle += 1
    return _next_handle


def _to_js_result(result: Any) -> dict[str, Any]:
    """One `FuseResult` in the exact shape fuse.js returns."""
    data: dict[str, Any] = {"item": result.item, "refIndex": result.ref_index}
    if result.score is not None:
        data["score"] = result.score
    if result.matches is not None:
        matches = []
        for match in result.matches:
            entry: dict[str, Any] = {"indices": [list(r) for r in match.indices]}
            if match.value is not None:
                entry["value"] = match.value
            if match.key is not None:
                entry["key"] = match.key
            if match.ref_index is not None:
                entry["refIndex"] = match.ref_index
            matches.append(entry)
        data["matches"] = matches
    return data


def _record_to_js(record: Any) -> dict[str, Any]:
    """One index record in fuse.js's shape."""
    if record.fields is None:
        return {"v": record.v, "i": record.i, "n": record.n}

    fields: dict[str, Any] = {}
    for key_index, value in record.fields.items():
        if isinstance(value, list):
            fields[str(key_index)] = [
                {"v": s.v, "n": s.n, **({"i": s.i} if s.i is not None else {})}
                for s in value
            ]
        else:
            fields[str(key_index)] = {"v": value.v, "n": value.n}
    return {"i": record.i, "$": fields}


def _to_js_node(node: Any) -> dict[str, Any]:
    if isinstance(node, ParsedLeaf):
        return {"keyId": node.key_id, "pattern": node.pattern}
    operator: ParsedOperator = node
    return {
        "children": [_to_js_node(c) for c in operator.children],
        "operator": operator.operator,
    }


def _js_regex_to_python(source: str, flags: str) -> re.Pattern[str]:
    """Rebuild a JavaScript regex on the Python side.

    ``re.ASCII`` is deliberate. JavaScript's ``\\w``, ``\\d`` and ``\\s`` are
    ASCII-only *always* — even under the ``u`` flag — while Python's are
    Unicode-aware by default. Without ``re.ASCII`` a tokenizer like
    ``/[\\w.+-]+/g`` would split non-Latin text differently in the two engines.
    """
    py_flags = re.ASCII
    if "i" in flags:
        py_flags |= re.IGNORECASE
    if "m" in flags:
        py_flags |= re.MULTILINE
    if "s" in flags:
        py_flags |= re.DOTALL
    return re.compile(source, py_flags)


def _options(raw: Any) -> dict[str, Any]:
    """Normalise options, refusing what genuinely cannot cross the boundary.

    A regex tokenizer arrives as ``{source, flags}`` and is rebuilt. Function
    values (`getFn`, `sortFn`, a callable `tokenize`) cannot cross at all, so
    the call is refused outright rather than silently falling back to defaults
    — which would make a test pass for the wrong reason.
    """
    if not isinstance(raw, dict):
        return {}

    tokenize = raw.get("tokenize")
    if isinstance(tokenize, dict) and tokenize.get("__regex__"):
        raw["tokenize"] = _js_regex_to_python(
            tokenize["source"], tokenize.get("flags", "")
        )

    if raw.pop("__hasFunctions__", False):
        raise ValueError(
            "bridge limitation: function-valued options (getFn / sortFn / "
            "tokenize) cannot cross the JS-Python boundary"
        )
    return raw


def _resolve_index(raw: Any) -> Any:
    """Turn an index handle into a real FuseIndex, preserving bad input.

    The JS shim sends ``"__invalid__"`` for anything that is not a real index
    handle, so the port's own type check runs instead of the bridge silently
    swallowing it.
    """
    if raw is None:
        return None
    if raw == "__invalid__":
        return object()  # not a FuseIndex -> the port raises
    return _indexes.get(raw)


def handle(command: dict[str, Any]) -> Any:
    op = command["op"]

    if op == "version":
        return __version__

    if op == "config":
        defaults = FuseOptions()
        return {
            "isCaseSensitive": defaults.is_case_sensitive,
            "ignoreDiacritics": defaults.ignore_diacritics,
            "includeScore": defaults.include_score,
            "shouldSort": defaults.should_sort,
            "includeMatches": defaults.include_matches,
            "findAllMatches": defaults.find_all_matches,
            "minMatchCharLength": defaults.min_match_char_length,
            "location": defaults.location,
            "threshold": defaults.threshold,
            "distance": defaults.distance,
            "useExtendedSearch": defaults.use_extended_search,
            "useTokenSearch": defaults.use_token_search,
            "tokenMatch": defaults.token_match,
            "ignoreLocation": defaults.ignore_location,
            "ignoreFieldNorm": defaults.ignore_field_norm,
            "fieldNormWeight": defaults.field_norm_weight,
        }

    if op == "new":
        index = _resolve_index(command.get("index"))
        handle_id = _handle()
        _instances[handle_id] = Fuse(
            command["docs"], _options(command.get("options")), index
        )
        return handle_id

    if op == "search":
        fuse = _instances[command["id"]]
        limit = (command.get("searchOptions") or {}).get("limit", -1)
        return [_to_js_result(r) for r in fuse.search(command["query"], limit=limit)]

    if op == "add":
        _instances[command["id"]].add(command["doc"])
        return None

    if op == "removeAt":
        return _instances[command["id"]].remove_at(command["idx"])

    if op == "setCollection":
        index = _resolve_index(command.get("index"))
        _instances[command["id"]].set_collection(command["docs"], index)
        return None

    if op == "getIndex":
        handle_id = _handle()
        _indexes[handle_id] = _instances[command["id"]].get_index()
        return handle_id

    if op == "docs":
        return list(_instances[command["id"]]._docs)

    if op == "invertedIndex":
        data = _instances[command["id"]]._inverted_index
        if data is None:
            return None
        return {
            "df": dict(data.df),
            "fieldCount": data.field_count,
            "docFieldCount": {str(k): v for k, v in data.doc_field_count.items()},
            "docTermFieldHits": {
                str(k): dict(v) for k, v in data.doc_term_field_hits.items()
            },
        }

    if op == "removeIndices":
        # The JS side already evaluated the user predicate; it just tells us
        # which document indices matched.
        wanted = set(command["indices"])
        _instances[command["id"]].remove(lambda _doc, idx: idx in wanted)
        return None

    if op == "indexToJSON":
        return _indexes[command["id"]].to_dict()

    if op == "indexSize":
        return _indexes[command["id"]].size()

    if op == "indexRecords":
        # Shaped like fuse.js's records: `v`/`i`/`n` for strings, `$` for
        # objects. The suite reads `.v` and `.i` off these directly.
        return [
            _record_to_js(record) for record in _indexes[command["id"]].records
        ]

    if op == "indexKeys":
        return [
            {"path": k.path, "id": k.id, "weight": k.weight, "src": k.src}
            for k in _indexes[command["id"]].keys
        ]

    if op == "indexRemoveAt":
        _indexes[command["id"]].remove_at(command["idx"])
        return None

    if op == "indexRemoveAll":
        _indexes[command["id"]].remove_all(command["indices"])
        return None

    if op == "indexAdd":
        _indexes[command["id"]].add(command["doc"], command["docIndex"])
        return None

    if op == "indexSetKeys":
        from pyfuse.tools.key_store import create_key

        _indexes[command["id"]].set_keys([create_key(k) for k in command["keys"]])
        return None

    if op == "createIndex":
        handle_id = _handle()
        _indexes[handle_id] = create_index(command["keys"], command["docs"])
        return handle_id

    if op == "parseIndex":
        handle_id = _handle()
        _indexes[handle_id] = parse_index(command["data"])
        return handle_id

    if op == "match":
        result = Fuse.match(
            command["pattern"], command["text"], _options(command.get("options"))
        )
        payload: dict[str, Any] = {
            "isMatch": result.is_match,
            "score": result.score,
        }
        if result.indices is not None:
            payload["indices"] = [list(r) for r in result.indices]
        return payload

    if op == "parseQuery":
        return _to_js_node(
            parse_query(command["query"], _options(command.get("options")), auto=False)
        )

    raise ValueError(f"unknown op: {op}")


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = {"ok": True, "result": handle(json.loads(line))}
        except Exception as exc:  # noqa: BLE001 - a server reports, never dies
            response = {
                "ok": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
