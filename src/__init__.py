"""Notion-Linear Bidirectional Sync Service.

A bidirectional synchronization service between Notion and Linear
for Projects, Milestones, and Tasks.
"""

from .config import Config, load_config
from .sync import SyncEngine, SyncResult

__all__ = [
    "Config",
    "load_config",
    "SyncEngine",
    "SyncResult",
]

__version__ = "0.1.0"
