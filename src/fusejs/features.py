"""Build feature flags.

fuse.js ships several builds — `fuse.basic` omits extended, logical and token
search, and its entry point guards each with an environment variable. This
port ships a single build with everything enabled, but the guards are kept so
the basic-build contract stays representable and its error paths remain
reachable and testable.

Toggle these only to exercise the reduced-build behaviour; the shipped default
is the equivalent of fuse.js's full build.
"""

from __future__ import annotations

#: Whether ``use_extended_search`` is available.
EXTENDED_SEARCH_ENABLED = True

#: Whether object-expression (``$and`` / ``$or``) queries are available.
LOGICAL_SEARCH_ENABLED = True

#: Whether ``use_token_search`` is available.
TOKEN_SEARCH_ENABLED = True
