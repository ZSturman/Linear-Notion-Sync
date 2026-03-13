"""Relation resolver for handling parent-child relationships during sync."""

from dataclasses import dataclass, field
from typing import Any, Optional

from ..models.base import SyncSource
from ..models.project import UnifiedProject
from ..models.milestone import UnifiedMilestone
from ..models.task import UnifiedTask
from ..utils.logging import get_logger
from ..utils.state import SyncStateStore

logger = get_logger(__name__)


@dataclass
class RelationMaps:
    """Lookup maps for resolving relations between entities."""
    
    # Project mappings
    project_notion_to_linear: dict[str, str] = field(default_factory=dict)
    project_linear_to_notion: dict[str, str] = field(default_factory=dict)
    
    # Milestone mappings
    milestone_notion_to_linear: dict[str, str] = field(default_factory=dict)
    milestone_linear_to_notion: dict[str, str] = field(default_factory=dict)
    
    # Task mappings
    task_notion_to_linear: dict[str, str] = field(default_factory=dict)
    task_linear_to_notion: dict[str, str] = field(default_factory=dict)
    
    def add_project(self, notion_id: Optional[str], linear_id: Optional[str]) -> None:
        """Add a project mapping."""
        if notion_id and linear_id:
            self.project_notion_to_linear[notion_id] = linear_id
            self.project_linear_to_notion[linear_id] = notion_id
    
    def add_milestone(self, notion_id: Optional[str], linear_id: Optional[str]) -> None:
        """Add a milestone mapping."""
        if notion_id and linear_id:
            self.milestone_notion_to_linear[notion_id] = linear_id
            self.milestone_linear_to_notion[linear_id] = notion_id
    
    def add_task(self, notion_id: Optional[str], linear_id: Optional[str]) -> None:
        """Add a task mapping."""
        if notion_id and linear_id:
            self.task_notion_to_linear[notion_id] = linear_id
            self.task_linear_to_notion[linear_id] = notion_id
    
    def get_project_linear_id(self, notion_id: str) -> Optional[str]:
        """Get Linear project ID from Notion page ID."""
        return self.project_notion_to_linear.get(notion_id)
    
    def get_project_notion_id(self, linear_id: str) -> Optional[str]:
        """Get Notion page ID from Linear project ID."""
        return self.project_linear_to_notion.get(linear_id)
    
    def get_milestone_linear_id(self, notion_id: str) -> Optional[str]:
        """Get Linear milestone ID from Notion page ID."""
        return self.milestone_notion_to_linear.get(notion_id)
    
    def get_milestone_notion_id(self, linear_id: str) -> Optional[str]:
        """Get Notion page ID from Linear milestone ID."""
        return self.milestone_linear_to_notion.get(linear_id)
    
    def get_task_linear_id(self, notion_id: str) -> Optional[str]:
        """Get Linear issue ID from Notion page ID."""
        return self.task_notion_to_linear.get(notion_id)
    
    def get_task_notion_id(self, linear_id: str) -> Optional[str]:
        """Get Notion page ID from Linear issue ID."""
        return self.task_linear_to_notion.get(linear_id)


class RelationResolver:
    """Resolves relations between entities during sync.
    
    Handles:
    - Project → Milestone relationships
    - Project → Task relationships
    - Milestone → Task relationships
    - Task → Parent Task relationships
    
    Uses a two-pass approach:
    1. First pass: Sync entities without relations
    2. Second pass: Update relations using lookup maps
    """
    
    def __init__(self, state_store: SyncStateStore):
        """Initialize relation resolver.
        
        Args:
            state_store: Sync state persistence store
        """
        self.state_store = state_store
        self.maps = RelationMaps()
    
    def build_maps(
        self,
        projects: list[UnifiedProject],
        milestones: list[UnifiedMilestone],
        tasks: list[UnifiedTask],
    ) -> RelationMaps:
        """Build lookup maps from current records.
        
        Args:
            projects: All synced projects
            milestones: All synced milestones
            tasks: All synced tasks
            
        Returns:
            RelationMaps with all mappings
        """
        self.maps = RelationMaps()
        
        for project in projects:
            self.maps.add_project(project.notion_page_id, project.linear_id)
        
        for milestone in milestones:
            self.maps.add_milestone(milestone.notion_page_id, milestone.linear_id)
        
        for task in tasks:
            self.maps.add_task(task.notion_page_id, task.linear_id)
        
        logger.debug(
            f"Built relation maps: {len(self.maps.project_notion_to_linear)} projects, "
            f"{len(self.maps.milestone_notion_to_linear)} milestones, "
            f"{len(self.maps.task_notion_to_linear)} tasks"
        )
        
        return self.maps
    
    def resolve_milestone_relations(
        self,
        milestone: UnifiedMilestone,
        source: SyncSource,
    ) -> UnifiedMilestone:
        """Resolve project relation for a milestone.
        
        Args:
            milestone: Milestone to resolve
            source: Source system of the milestone
            
        Returns:
            Milestone with resolved relations
        """
        if source == SyncSource.NOTION:
            # Resolve Notion project ID to Linear project ID
            if milestone.project_notion_id:
                linear_project_id = self.maps.get_project_linear_id(
                    milestone.project_notion_id
                )
                if linear_project_id:
                    milestone.project_linear_id = linear_project_id
                else:
                    logger.warning(
                        f"Could not resolve project relation for milestone "
                        f"{milestone.name}: notion_id={milestone.project_notion_id}"
                    )
        else:
            # Resolve Linear project ID to Notion project ID
            if milestone.project_linear_id:
                notion_project_id = self.maps.get_project_notion_id(
                    milestone.project_linear_id
                )
                if notion_project_id:
                    milestone.project_notion_id = notion_project_id
        
        return milestone
    
    def resolve_task_relations(
        self,
        task: UnifiedTask,
        source: SyncSource,
    ) -> UnifiedTask:
        """Resolve all relations for a task.
        
        Args:
            task: Task to resolve
            source: Source system of the task
            
        Returns:
            Task with resolved relations
        """
        if source == SyncSource.NOTION:
            # Resolve Notion IDs to Linear IDs
            if task.project_notion_id:
                linear_project_id = self.maps.get_project_linear_id(
                    task.project_notion_id
                )
                if linear_project_id:
                    task.project_linear_id = linear_project_id
            
            if task.milestone_notion_id:
                linear_milestone_id = self.maps.get_milestone_linear_id(
                    task.milestone_notion_id
                )
                if linear_milestone_id:
                    task.milestone_linear_id = linear_milestone_id
            
            if task.parent_task_notion_id:
                linear_parent_id = self.maps.get_task_linear_id(
                    task.parent_task_notion_id
                )
                if linear_parent_id:
                    task.parent_task_linear_id = linear_parent_id
        else:
            # Resolve Linear IDs to Notion IDs
            if task.project_linear_id:
                notion_project_id = self.maps.get_project_notion_id(
                    task.project_linear_id
                )
                if notion_project_id:
                    task.project_notion_id = notion_project_id
            
            if task.milestone_linear_id:
                notion_milestone_id = self.maps.get_milestone_notion_id(
                    task.milestone_linear_id
                )
                if notion_milestone_id:
                    task.milestone_notion_id = notion_milestone_id
            
            if task.parent_task_linear_id:
                notion_parent_id = self.maps.get_task_notion_id(
                    task.parent_task_linear_id
                )
                if notion_parent_id:
                    task.parent_task_notion_id = notion_parent_id
        
        return task
    
    def topological_sort_tasks(
        self,
        tasks: list[UnifiedTask],
    ) -> list[UnifiedTask]:
        """Sort tasks so parents come before children.
        
        This ensures that when creating tasks in Linear, parent
        issues are created before their sub-issues.
        
        Args:
            tasks: List of tasks to sort
            
        Returns:
            Sorted list with parents first
        """
        # Build parent-child graph
        task_by_notion_id: dict[str, UnifiedTask] = {}
        task_by_linear_id: dict[str, UnifiedTask] = {}
        
        for task in tasks:
            if task.notion_page_id:
                task_by_notion_id[task.notion_page_id] = task
            if task.linear_id:
                task_by_linear_id[task.linear_id] = task
        
        # Identify root tasks and children. If a referenced parent is not
        # present in the current task set, treat the task as a root so it
        # still participates in the sorted output.
        #
        # When both a parent Notion ID and parent Linear ID are known, store
        # the child under both keys so traversal can reach it regardless of
        # which identifier the parent task is encountered with.
        root_tasks: list[UnifiedTask] = []
        children_by_parent: dict[str, list[UnifiedTask]] = {}

        for task in tasks:
            parent_notion_id = task.parent_task_notion_id
            parent_linear_id = task.parent_task_linear_id

            parent_keys: list[str] = []

            if parent_notion_id and parent_notion_id in task_by_notion_id:
                parent_keys.append(parent_notion_id)

            if parent_linear_id and parent_linear_id in task_by_linear_id:
                parent_keys.append(parent_linear_id)

            if not parent_keys:
                root_tasks.append(task)
            else:
                for parent_key in parent_keys:
                    if parent_key not in children_by_parent:
                        children_by_parent[parent_key] = []
                    children_by_parent[parent_key].append(task)
        
        # BFS to get topologically sorted order
        sorted_tasks: list[UnifiedTask] = []
        queue = list(root_tasks)
        seen: set[str] = set()
        
        while queue:
            task = queue.pop(0)
            task_id = task.notion_page_id or task.linear_id

            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            
            sorted_tasks.append(task)
            
            # Add children to queue
            if task.notion_page_id and task.notion_page_id in children_by_parent:
                queue.extend(children_by_parent[task.notion_page_id])
            if task.linear_id and task.linear_id in children_by_parent:
                queue.extend(children_by_parent[task.linear_id])
        
        # Add any tasks not in the sorted list (typically circular refs)
        for task in tasks:
            task_id = task.notion_page_id or task.linear_id
            if task_id and task_id not in seen:
                sorted_tasks.append(task)
                logger.warning(f"Task not in sorted tree: {task.title}")
        
        return sorted_tasks
    
    def get_notion_relation_properties(
        self,
        task: UnifiedTask,
        notion_config: Any,
    ) -> dict:
        """Get Notion relation properties for a task.
        
        Args:
            task: Task with resolved relations
            notion_config: Notion configuration
            
        Returns:
            Notion properties dict for relations
        """
        properties = {}
        
        # Project relation
        if task.project_notion_id:
            properties[notion_config.task_project_prop] = {
                "relation": [{"id": task.project_notion_id}]
            }
        
        # Milestone relation
        if task.milestone_notion_id:
            properties[notion_config.task_milestones_prop] = {
                "relation": [{"id": task.milestone_notion_id}]
            }
        
        # Parent task relation
        if task.parent_task_notion_id:
            properties[notion_config.task_parent_prop] = {
                "relation": [{"id": task.parent_task_notion_id}]
            }
        
        return properties
    
    def get_milestone_relation_properties(
        self,
        milestone: UnifiedMilestone,
        notion_config: Any,
    ) -> dict:
        """Get Notion relation properties for a milestone.
        
        Args:
            milestone: Milestone with resolved relations
            notion_config: Notion configuration
            
        Returns:
            Notion properties dict for relations
        """
        properties = {}
        
        if milestone.project_notion_id:
            properties[notion_config.milestone_project_prop] = {
                "relation": [{"id": milestone.project_notion_id}]
            }
        
        return properties