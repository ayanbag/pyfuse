"""Guards on the things that break a release rather than a search.

These caught a real drift: `pyproject.toml` said 1.5.0 while
`pyfuse.__version__` still said 7.5.0, so the wheel and the value the library
reports at runtime disagreed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pyfuse

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _pyproject(field: str) -> str:
    match = re.search(rf'^{field} = "([^"]+)"', PYPROJECT, re.M)
    assert match, f"{field} not found in pyproject.toml"
    return match.group(1)


def test_version_matches_pyproject():
    """__version__ and the built distribution must report the same version."""
    assert pyfuse.__version__ == _pyproject("version")


def test_version_is_not_the_upstream_version():
    """The port versions itself independently of fuse.js.

    fuse.js v7.5.0 is what this ports *from* — see DECISIONS.md. Reusing that
    number here would leave no room to release a fix to the port itself.
    """
    assert pyfuse.__version__ != "7.5.0"


def test_py_typed_marker_is_present():
    """PEP 561: without this file, type checkers ignore our annotations.

    The `Typing :: Typed` classifier claims the hints are usable; this is what
    makes the claim true.
    """
    assert (Path(pyfuse.__file__).parent / "py.typed").is_file()


def test_declares_no_runtime_dependencies():
    """Zero runtime dependencies is a headline claim; pin it down."""
    match = re.search(r"^dependencies = \[(.*?)\]", PYPROJECT, re.M | re.S)
    assert match, "dependencies not found in pyproject.toml"
    assert match.group(1).strip() == "", "a runtime dependency crept in"
