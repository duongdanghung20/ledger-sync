"""QuickBooks connection feature (ticket 05).

Admin-only in-app OAuth authorization-code flow that links a Book to a QBO
company, with the refresh token encrypted at rest and access tokens refreshed
on demand through the qbo port.

Public seam for later tickets (import from here)::

    from app.features.qbo_connection import (
        assert_connected, get_valid_connection, refresh_access_token, to_connection,
    )
"""

from .service import (
    assert_connected,
    get_valid_connection,
    refresh_access_token,
    to_connection,
)

__all__ = [
    "assert_connected",
    "get_valid_connection",
    "refresh_access_token",
    "to_connection",
]
