"""Reconciler for detecting changes between Notion and Linear."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from ..config import Config, ConflictStrategy
from ..models.base import SyncRecord, SyncSource
from ..models.project import UnifiedProject
from ..models.milestone import UnifiedMilestone
from ..models.task import UnifiedTask
from ..utils.logging import get_logger
from ..utils.state import SyncState, SyncStateStore

logger = get_logger(__name__)


class ChangeType(Enum):
    """Type of change detected."""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    CONFLICT = "conflict"
    NONE = "none"


@dataclass
class Change:
    """Represents a detected change."""
    change_type: ChangeType
    entity_type: str  # "project", "milestone", "task"
    source: SyncSource  # Where the change originated
    target: SyncSource  # Where to apply the change
    
    # Record data
    record: SyncRecord
    
    # For updates/conflicts
    changes: list[str] = field(default_factory=list)  # Changed field names
    
    # For conflict resolution
    notion_record: Optional[SyncRecord] = None
    linear_record: Optional[SyncRecord] = None
    resolved_winner: Optional[SyncSource] = None
    
    @property
    def identifier(self) -> str:
        """Get unique identifier for this change."""
        if self.record.notion_page_id:
            return f"notion:{self.record.notion_page_id}"
        if self.record.linear_id:
            return f"linear:{self.record.linear_id}"
        return f"unknown:{id(self.record)}"


@dataclass
class ChangeSet:
    """Set of changes to be applied during sync."""
    
    # Changes by entity type
    project_changes: list[Change] = field(default_factory=list)
    milestone_changes: list[Change] = field(default_factory=list)
    task_changes: list[Change] = field(default_factory=list)
    
    # Summary counts
    creates: int = 0
    updates: int = 0
    conflicts: int = 0
    skipped: int = 0
    
    def add(self, change: Change) -> None:
        """Add a change to the set."""
        if change.entity_type == "project":
            self.project_changes.append(change)
        elif change.entity_type == "milestone":
            self.milestone_changes.append(change)
        elif change.entity_type == "task":
            self.task_changes.append(change)
        
        # Update counts
        if change.change_type == ChangeType.CREATE:
            self.creates += 1
        elif change.change_type in (ChangeType.UPDATE, ChangeType.DELETE):
            self.updates += 1
        elif change.change_type == ChangeType.CONFLICT:
            self.conflicts += 1
    
    @property
    def total(self) -> int:
        """Total number of changes."""
        return len(self.project_changes) + len(self.milestone_changes) + len(self.task_changes)
    
    def is_empty(self) -> bool:
        """Check if no changes detected."""
        return self.total == 0


class Reconciler:
    """Detects and reconciles changes between Notion and Linear.
    
    Compares records from both systems and generates a ChangeSet
    describing what needs to be synced.
    """
    
    def __init__(self, config: Config, state_store: SyncStateStore):
        """Initialize reconciler.
        
        Args:
            config: Application configuration
            state_store: Sync state persistence store
        """
        self.config = config
        self.state_store = state_store
        self.conflict_strategy = config.conflict_strategy
    
    def compute_changes(
        self,
        notion_projects: list[UnifiedProject],
        linear_projects: list[UnifiedProject],
        notion_milestones: list[UnifiedMilestone],
        linear_milestones: list[UnifiedMilestone],
        notion_tasks: list[UnifiedTask],
        linear_tasks: list[UnifiedTask],
        existing_notion_project_ids: Optional[set[str]] = None,
        existing_notion_milestone_ids: Optional[set[str]] = None,
        existing_notion_task_ids: Optional[set[str]] = None,
    ) -> ChangeSet:
        """Compute all changes needed to sync both systems.
        
        Args:
            notion_projects: Projects from Notion
            linear_projects: Projects from Linear
            notion_milestones: Milestones from Notion
            linear_milestones: Milestones from Linear
            notion_tasks: Tasks from Notion
            linear_tasks: Issues from Linear
            
        Returns:
            ChangeSet with all detected changes
        """
        changeset = ChangeSet()
        
        # Process projects
        project_changes = self._reconcile_entities(
            notion_projects,
            linear_projects,
            "project",
            existing_notion_ids=existing_notion_project_ids,
        )
        for change in project_changes:
            changeset.add(change)
        
        # Process milestones
        milestone_changes = self._reconcile_entities(
            notion_milestones,
            linear_milestones,
            "milestone",
            existing_notion_ids=existing_notion_milestone_ids,
        )
        for change in milestone_changes:
            changeset.add(change)
        
        # Process tasks
        task_changes = self._reconcile_entities(
            notion_tasks,
            linear_tasks,
            "task",
            existing_notion_ids=existing_notion_task_ids,
        )
        for change in task_changes:
            changeset.add(change)
        
        logger.info(
            f"Reconciliation complete: {changeset.creates} creates, "
            f"{changeset.updates} updates, {changeset.conflicts} conflicts"
        )
        
        return changeset
    
    def _reconcile_entities(
        self,
        notion_records: list[SyncRecord],
        linear_records: list[SyncRecord],
        entity_type: str,
        existing_notion_ids: Optional[set[str]] = None,
    ) -> list[Change]:
        """Reconcile a single entity type.
        
        Args:
            notion_records: Records from Notion
            linear_records: Records from Linear
            entity_type: Type of entity
            
        Returns:
            List of changes
        """
        changes = []
        
        # Build lookup maps
        notion_by_linear_id: dict[str, SyncRecord] = {}
        notion_by_page_id: dict[str, SyncRecord] = {}
        
        for record in notion_records:
            if record.notion_page_id:
                notion_by_page_id[record.notion_page_id] = record
            if record.linear_id:
                notion_by_linear_id[record.linear_id] = record
        
        linear_by_id: dict[str, SyncRecord] = {}
        for record in linear_records:
            if record.linear_id:
                linear_by_id[record.linear_id] = record
        
        # Track processed records
        processed_linear_ids = set()
        
        # 1. Process Notion records
        for notion_record in notion_records:
            linear_record = None
            
            # Try to find matching Linear record
            if notion_record.linear_id:
                linear_record = linear_by_id.get(notion_record.linear_id)
                if linear_record:
                    processed_linear_ids.add(notion_record.linear_id)
            
            if linear_record:
                # Both exist - check for changes
                change = self._detect_change(
                    notion_record, linear_record, entity_type
                )
                if change:
                    changes.append(change)
            else:
                sync_state = None
                if notion_record.notion_page_id:
                    sync_state = self.state_store.get_by_notion_id(
                        entity_type,
                        notion_record.notion_page_id,
                    )

                if sync_state and sync_state.linear_id == notion_record.linear_id:
                    changes.append(
                        Change(
                            change_type=ChangeType.DELETE,
                            entity_type=entity_type,
                            source=SyncSource.LINEAR,
                            target=SyncSource.NOTION,
                            record=notion_record,
                        )
                    )
                    logger.debug(
                        f"Deleted {entity_type} in Linear: {self._get_record_name(notion_record)}"
                    )
                else:
                    change = Change(
                        change_type=ChangeType.CREATE,
                        entity_type=entity_type,
                        source=SyncSource.NOTION,
                        target=SyncSource.LINEAR,
                        record=notion_record,
                    )
                    changes.append(change)
                    logger.debug(
                        f"New {entity_type} in Notion: {self._get_record_name(notion_record)}"
                    )
        
        # 2. Process Linear records not in Notion
        for linear_record in linear_records:
            if linear_record.linear_id in processed_linear_ids:
                continue
            
            # Check if there's a Notion record with this linear_id that we missed
            if linear_record.linear_id in notion_by_linear_id:
                continue

            sync_state = self.state_store.get_by_linear_id(
                entity_type,
                linear_record.linear_id,
            )

            if sync_state and sync_state.notion_id:
                if existing_notion_ids and sync_state.notion_id in existing_notion_ids:
                    logger.debug(
                        f"Skipping {entity_type} with disabled Notion sync: "
                        f"{self._get_record_name(linear_record)}"
                    )
                    continue

                linear_record.notion_page_id = sync_state.notion_id
                changes.append(
                    Change(
                        change_type=ChangeType.DELETE,
                        entity_type=entity_type,
                        source=SyncSource.NOTION,
                        target=SyncSource.LINEAR,
                        record=linear_record,
                    )
                )
                logger.debug(
                    f"Deleted {entity_type} in Notion: {self._get_record_name(linear_record)}"
                )
                continue
            
            # Only in Linear - create in Notion
            change = Change(
                change_type=ChangeType.CREATE,
                entity_type=entity_type,
                source=SyncSource.LINEAR,
                target=SyncSource.NOTION,
                record=linear_record,
            )
            changes.append(change)
            logger.debug(
                f"New {entity_type} in Linear: {self._get_record_name(linear_record)}"
            )
        
        return changes
    
    def _detect_change(
        self,
        notion_record: SyncRecord,
        linear_record: SyncRecord,
        entity_type: str,
    ) -> Optional[Change]:
        """Detect if a synced record has changed and needs update.
        
        Args:
            notion_record: Record from Notion
            linear_record: Record from Linear
            entity_type: Type of entity
            
        Returns:
            Change object if update needed, None if in sync
        """
        # Get last sync state
        sync_state = None
        if notion_record.notion_page_id:
            sync_state = self.state_store.get_by_notion_id(
                entity_type, notion_record.notion_page_id
            )
        
        # Determine what changed since last sync
        notion_changed = False
        linear_changed = False
        
        if sync_state:
            # Compare with last known state
            if (notion_record.notion_last_modified and 
                sync_state.notion_last_modified and
                notion_record.notion_last_modified > sync_state.notion_last_modified):
                notion_changed = True
            
            if (linear_record.linear_last_modified and
                sync_state.linear_last_modified and
                linear_record.linear_last_modified > sync_state.linear_last_modified):
                linear_changed = True
        else:
            # No sync state - treat as both potentially changed
            # Use content comparison
            notion_hash = SyncState.compute_hash(notion_record.to_sync_hash_dict())
            linear_hash = SyncState.compute_hash(linear_record.to_sync_hash_dict())
            
            if notion_hash != linear_hash:
                # Content differs - determine newer
                if notion_record.notion_last_modified and linear_record.linear_last_modified:
                    if notion_record.notion_last_modified > linear_record.linear_last_modified:
                        notion_changed = True
                    else:
                        linear_changed = True
                else:
                    # Can't determine - use conflict strategy
                    notion_changed = True
                    linear_changed = True
        
        # Both changed - conflict
        if notion_changed and linear_changed:
            return self._resolve_conflict(
                notion_record, linear_record, entity_type
            )
        
        # Only Notion changed - update Linear
        if notion_changed:
            # Merge Linear ID into Notion record for update
            notion_record.linear_id = linear_record.linear_id
            notion_record.linear_last_modified = linear_record.linear_last_modified
            
            return Change(
                change_type=ChangeType.UPDATE,
                entity_type=entity_type,
                source=SyncSource.NOTION,
                target=SyncSource.LINEAR,
                record=notion_record,
                notion_record=notion_record,
                linear_record=linear_record,
            )
        
        # Only Linear changed - update Notion
        if linear_changed:
            # Merge Notion ID into Linear record for update
            linear_record.notion_page_id = notion_record.notion_page_id
            linear_record.notion_last_modified = notion_record.notion_last_modified
            
            return Change(
                change_type=ChangeType.UPDATE,
                entity_type=entity_type,
                source=SyncSource.LINEAR,
                target=SyncSource.NOTION,
                record=linear_record,
                notion_record=notion_record,
                linear_record=linear_record,
            )
        
        # No changes
        return None
    
    def _resolve_conflict(
        self,
        notion_record: SyncRecord,
        linear_record: SyncRecord,
        entity_type: str,
    ) -> Change:
        """Resolve a conflict when both systems have changes.
        
        Args:
            notion_record: Record from Notion
            linear_record: Record from Linear
            entity_type: Type of entity
            
        Returns:
            Change with resolved winner
        """
        logger.warning(
            f"Conflict detected for {entity_type}: "
            f"notion={notion_record.notion_page_id}, linear={linear_record.linear_id}"
        )
        
        winner: SyncSource
        winning_record: SyncRecord
        
        if self.conflict_strategy == ConflictStrategy.NOTION_PRIMARY:
            winner = SyncSource.NOTION
            winning_record = notion_record
        elif self.conflict_strategy == ConflictStrategy.LINEAR_PRIMARY:
            winner = SyncSource.LINEAR
            winning_record = linear_record
        else:
            # Last write wins
            notion_time = notion_record.notion_last_modified
            linear_time = linear_record.linear_last_modified
            
            if notion_time and linear_time:
                if notion_time >= linear_time:
                    winner = SyncSource.NOTION
                    winning_record = notion_record
                else:
                    winner = SyncSource.LINEAR
                    winning_record = linear_record
            elif notion_time:
                winner = SyncSource.NOTION
                winning_record = notion_record
            else:
                winner = SyncSource.LINEAR
                winning_record = linear_record
        
        # Merge identifiers
        winning_record.notion_page_id = notion_record.notion_page_id
        winning_record.linear_id = linear_record.linear_id
        winning_record.notion_last_modified = notion_record.notion_last_modified
        winning_record.linear_last_modified = linear_record.linear_last_modified
        
        target = SyncSource.LINEAR if winner == SyncSource.NOTION else SyncSource.NOTION
        
        return Change(
            change_type=ChangeType.CONFLICT,
            entity_type=entity_type,
            source=winner,
            target=target,
            record=winning_record,
            notion_record=notion_record,
            linear_record=linear_record,
            resolved_winner=winner,
        )
    
    def _get_record_name(self, record: SyncRecord) -> str:
        """Get display name for a record."""
        if isinstance(record, UnifiedProject):
            return record.name
        elif isinstance(record, UnifiedMilestone):
            return record.name
        elif isinstance(record, UnifiedTask):
            return record.title
        return str(record.notion_page_id or record.linear_id)
