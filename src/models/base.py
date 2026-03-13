"""Base classes and types for unified data models."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class SyncSource(Enum):
    """Source of a sync record."""
    NOTION = "notion"
    LINEAR = "linear"


@dataclass
class SyncRecord:
    """Base class for all sync records.
    
    Provides common fields and methods for records that can be
    synced between Notion and Linear.
    """
    
    # Primary identifiers
    notion_page_id: Optional[str] = None
    linear_id: Optional[str] = None
    
    # Timestamps for change detection
    notion_last_modified: Optional[datetime] = None
    linear_last_modified: Optional[datetime] = None
    
    # Source tracking
    source: Optional[SyncSource] = None
    
    @property
    def is_linked(self) -> bool:
        """Check if record exists in both systems."""
        return self.notion_page_id is not None and self.linear_id is not None
    
    @property
    def exists_in_notion(self) -> bool:
        """Check if record exists in Notion."""
        return self.notion_page_id is not None
    
    @property
    def exists_in_linear(self) -> bool:
        """Check if record exists in Linear."""
        return self.linear_id is not None
    
    @property
    def last_modified(self) -> Optional[datetime]:
        """Get the most recent modification time from either system."""
        times = [t for t in [self.notion_last_modified, self.linear_last_modified] if t]
        return max(times) if times else None
    
    def to_sync_hash_dict(self) -> dict[str, Any]:
        """Get dictionary of fields to include in sync hash.
        
        Override in subclasses to include relevant fields.
        """
        return {}
    
    def merge_from(self, other: "SyncRecord") -> None:
        """Merge identifiers from another record.
        
        Used when linking records from different sources.
        """
        if other.notion_page_id and not self.notion_page_id:
            self.notion_page_id = other.notion_page_id
            self.notion_last_modified = other.notion_last_modified
        if other.linear_id and not self.linear_id:
            self.linear_id = other.linear_id
            self.linear_last_modified = other.linear_last_modified


@dataclass
class ChangeRecord:
    """Record of a change to be applied during sync."""
    
    entity_type: str  # "project", "milestone", "task"
    action: str  # "create", "update", "delete"
    source: SyncSource  # Where the change originated
    target: SyncSource  # Where to apply the change
    
    # Record data
    record: SyncRecord
    
    # Change details
    changed_fields: list[str] = field(default_factory=list)
    
    # Dependency tracking for ordering
    depends_on: list[str] = field(default_factory=list)  # List of linear_ids or notion_ids
    
    @property
    def identifier(self) -> str:
        """Get unique identifier for this change."""
        return f"{self.entity_type}:{self.record.notion_page_id or self.record.linear_id}"
