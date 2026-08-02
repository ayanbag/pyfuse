"""Python-side driver for the Node fuse.js oracle.

Spawns ``fuzz/oracle.js`` once and talks newline-delimited JSON to it, so a
differential run costs one process for the whole session instead of one per
case.

This module is *test infrastructure only*. The shipped ``fusejs`` package has
no Node dependency and never imports anything from here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import TracebackType
from typing import Any

ORACLE_JS = Path(__file__).resolve().parent / "oracle.js"


class OracleError(RuntimeError):
    """The Node oracle rejected a command."""


class Oracle:
    """A long-lived fuse.js process answering queries over stdin/stdout."""

    def __init__(self, node: str = "node") -> None:
        self._proc = subprocess.Popen(
            [node, str(ORACLE_JS)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(ORACLE_JS.parent),
        )

    def __enter__(self) -> Oracle:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Shut the Node process down."""
        if self._proc.poll() is None:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def call(self, **command: Any) -> Any:
        """Send one command and return its result."""
        stdin, stdout = self._proc.stdin, self._proc.stdout
        if stdin is None or stdout is None:
            raise OracleError("oracle process has no pipes")

        stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
        stdin.flush()

        line = stdout.readline()
        if not line:
            stderr = self._proc.stderr.read() if self._proc.stderr else ""
            raise OracleError(f"oracle exited unexpectedly: {stderr.strip()}")

        response = json.loads(line)
        if not response.get("ok"):
            raise OracleError(response.get("error", "unknown oracle error"))
        return response["result"]

    # ── Convenience wrappers ───────────────────────────────────────

    def version(self) -> str:
        """The fuse.js version backing this oracle."""
        version: str = self.call(op="version")["version"]
        return version

    def match(self, pattern: str, text: str, options: dict[str, Any]) -> Any:
        """``Fuse.match(pattern, text, options)``."""
        return self.call(op="match", pattern=pattern, text=text, options=options)

    def search(
        self,
        docs: list[Any],
        query: Any,
        options: dict[str, Any],
        search_options: dict[str, Any] | None = None,
    ) -> Any:
        """``new Fuse(docs, options).search(query, searchOptions)``."""
        return self.call(
            op="search",
            docs=docs,
            query=query,
            options=options,
            searchOptions=search_options,
        )

    def create_index(
        self, keys: list[Any], docs: list[Any], options: dict[str, Any]
    ) -> Any:
        """``Fuse.createIndex(keys, docs, options).toJSON()``."""
        return self.call(op="createIndex", keys=keys, docs=docs, options=options)
