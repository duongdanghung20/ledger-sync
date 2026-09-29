"""Per-module configuration.

Feature modules read their OWN environment variables through `env()` instead of
editing a central settings class. Read the value where you use it (or wrap it in
an `@lru_cache` function) — never at import time — so the app still boots before
your variable is configured. Document every new variable in `.env.example`.
"""

import os

_MISSING = object()


def env(name: str, default=_MISSING):
    """Return environment variable `name`.

    Raises if it is unset and no `default` is given, so a misconfigured deploy
    fails loudly instead of limping along with `None`.
    """
    value = os.environ.get(name, default)
    if value is _MISSING:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return value
