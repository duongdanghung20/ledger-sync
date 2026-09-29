"""QuickBooks Online seam.

The single adapter every outbound QBO HTTP call goes through. Import the port
shapes and either implementation from here:

    from app.features.qbo import (
        QboClient, Connection, Entry, JournalLine, Outcome, QboResult,
        HttpxQboClient, FakeQboClient,
    )

No `router.py` / migration in this package — it exposes no routes or tables.
"""

from .client import HttpxQboClient
from .fake import FakeQboClient
from .models import (
    Connection,
    Entry,
    JournalLine,
    Outcome,
    QboClient,
    QboResult,
)

__all__ = [
    "QboClient",
    "Connection",
    "Entry",
    "JournalLine",
    "Outcome",
    "QboResult",
    "HttpxQboClient",
    "FakeQboClient",
]
