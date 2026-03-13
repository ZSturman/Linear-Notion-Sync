"""Unified Task model for sync operations."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .base import SyncRecord, SyncSource


@dataclass
class UnifiedTask(SyncRecord):
    """Unified representation of a Task across Notion and Linear.
    
    Notion: Task page with title, complete checkbox, project/milestone relations, etc.
    Linear: Issue with title, state, project, projectMilestone, parent, etc.
    """
    
    # Core fields
    title: str = ""
    description: Optional[str] = None
    
    # Completion
    is_complete: bool = False
    status: Optional[str] = None
    
    # Relations (stored as IDs for resolution)
    project_notion_id: Optional[str] = None
    project_linear_id: Optional[str] = None
    milestone_notion_id: Optional[str] = None
    milestone_linear_id: Optional[str] = None
    parent_task_notion_id: Optional[str] = None
    parent_task_linear_id: Optional[str] = None
    subtask_notion_ids: list[str] = field(default_factory=list)
    
    # Priority
    priority: Optional[str] = None  # Notion: "Low", "Medium", "High"
    priority_linear: Optional[int] = None  # Linear: 0-4
    
    # Dates
    due_date: Optional[datetime] = None
    
    # Task type (maps to Linear labels)
    task_type: Optional[str] = None
    
    # Linear-specific
    linear_state_id: Optional[str] = None
    linear_state_name: Optional[str] = None
    linear_state_type: Optional[str] = None  # "backlog", "unstarted", "started", "completed", "canceled"
    linear_url: Optional[str] = None
    linear_identifier: Optional[str] = None  # e.g., "TEAM-123"
    linear_labels: list[str] = field(default_factory=list)  # Label names
    
    @classmethod
    def from_notion(cls, page: dict, config: Any) -> "UnifiedTask":
        """Create UnifiedTask from Notion page data.
        
        Args:
            page: Notion page object from API
            config: NotionConfig with property names
            
        Returns:
            UnifiedTask instance
        """
        props = page.get("properties", {})
        
        # Extract title
        title_prop = props.get(config.task_title_prop, {})
        title = ""
        if title_prop.get("type") == "title":
            title_items = title_prop.get("title", [])
            title = "".join(item.get("plain_text", "") for item in title_items)
        elif title_prop.get("type") == "rich_text":
            title_items = title_prop.get("rich_text", [])
            title = "".join(item.get("plain_text", "") for item in title_items)
        
        # Extract linear id
        linear_id_prop = props.get(config.task_linear_id_prop, {})
        linear_id = None
        if linear_id_prop.get("type") == "rich_text":
            text_items = linear_id_prop.get("rich_text", [])
            linear_id = "".join(item.get("plain_text", "") for item in text_items) or None
        
        # Extract complete checkbox
        is_complete = False
        complete_prop = props.get(config.task_complete_prop, {})
        if complete_prop.get("type") == "checkbox":
            is_complete = complete_prop.get("checkbox", False)

        # Extract status
        status = None
        status_prop = props.get(config.task_status_prop, {})
        if status_prop.get("type") == "status":
            status_data = status_prop.get("status")
            status = status_data.get("name") if status_data else None
        elif status_prop.get("type") == "select":
            select_data = status_prop.get("select")
            status = select_data.get("name") if select_data else None
        
        # Extract project relation
        project_notion_id = None
        project_prop = props.get(config.task_project_prop, {})
        if project_prop.get("type") == "relation":
            relations = project_prop.get("relation", [])
            if relations:
                project_notion_id = relations[0].get("id")
        
        # Extract milestone relation
        milestone_notion_id = None
        milestone_prop = props.get(config.task_milestones_prop, {})
        if milestone_prop.get("type") == "relation":
            relations = milestone_prop.get("relation", [])
            if relations:
                milestone_notion_id = relations[0].get("id")
        
        # Extract parent task relation
        parent_task_notion_id = None
        parent_prop = props.get(config.task_parent_prop, {})
        if parent_prop.get("type") == "relation":
            relations = parent_prop.get("relation", [])
            if relations:
                parent_task_notion_id = relations[0].get("id")
        
        # Extract subtask relation IDs
        subtask_notion_ids = []
        subtask_prop = props.get(config.task_subtasks_prop, {})
        if subtask_prop.get("type") == "relation":
            subtask_notion_ids = [
                rel.get("id") for rel in subtask_prop.get("relation", [])
                if rel.get("id")
            ]
        
        # Extract priority
        priority = None
        priority_prop = props.get(config.task_priority_prop, {})
        if priority_prop.get("type") == "status":
            status_data = priority_prop.get("status")
            priority = status_data.get("name") if status_data else None
        elif priority_prop.get("type") == "select":
            select_data = priority_prop.get("select")
            priority = select_data.get("name") if select_data else None
        
        # Extract due date
        due_date = None
        due_date_prop = props.get(config.task_due_date_prop, {})
        if due_date_prop.get("type") == "date":
            date_data = due_date_prop.get("date")
            if date_data and date_data.get("start"):
                due_date = datetime.fromisoformat(
                    date_data["start"].replace("Z", "+00:00")
                )
        
        # Extract task type
        task_type = None
        type_prop = props.get(config.task_type_prop, {})
        if type_prop.get("type") == "status":
            status_data = type_prop.get("status")
            task_type = status_data.get("name") if status_data else None
        elif type_prop.get("type") == "select":
            select_data = type_prop.get("select")
            task_type = select_data.get("name") if select_data else None
        
        # Parse last edited time
        last_edited = None
        if page.get("last_edited_time"):
            last_edited = datetime.fromisoformat(
                page["last_edited_time"].replace("Z", "+00:00")
            )
        
        return cls(
            notion_page_id=page.get("id"),
            linear_id=linear_id,
            notion_last_modified=last_edited,
            source=SyncSource.NOTION,
            title=title,
            is_complete=is_complete,
            status=status,
            project_notion_id=project_notion_id,
            milestone_notion_id=milestone_notion_id,
            parent_task_notion_id=parent_task_notion_id,
            subtask_notion_ids=subtask_notion_ids,
            priority=priority,
            due_date=due_date,
            task_type=task_type,
        )
    
    @classmethod
    def from_linear(cls, issue: dict) -> "UnifiedTask":
        """Create UnifiedTask from Linear Issue data.
        
        Args:
            issue: Linear Issue object from GraphQL API
            
        Returns:
            UnifiedTask instance
        """
        # Parse dates
        due_date = None
        if issue.get("dueDate"):
            due_date = datetime.fromisoformat(
                issue["dueDate"].replace("Z", "+00:00")
            )
        
        updated_at = None
        if issue.get("updatedAt"):
            updated_at = datetime.fromisoformat(
                issue["updatedAt"].replace("Z", "+00:00")
            )
        
        # Get state info
        state = issue.get("state", {})
        state_id = state.get("id") if state else None
        state_name = state.get("name") if state else None
        state_type = state.get("type") if state else None
        
        # Determine completion from state type
        is_complete = state_type in ("completed", "canceled")
        
        # Get project ID
        project_linear_id = None
        if issue.get("project"):
            project_linear_id = issue["project"].get("id")
        
        # Get milestone ID
        milestone_linear_id = None
        if issue.get("projectMilestone"):
            milestone_linear_id = issue["projectMilestone"].get("id")
        
        # Get parent issue ID
        parent_task_linear_id = None
        if issue.get("parent"):
            parent_task_linear_id = issue["parent"].get("id")
        
        # Get labels
        labels = []
        if issue.get("labels") and issue["labels"].get("nodes"):
            labels = [label.get("name") for label in issue["labels"]["nodes"] if label.get("name")]
        
        return cls(
            linear_id=issue.get("id"),
            linear_last_modified=updated_at,
            source=SyncSource.LINEAR,
            title=issue.get("title", ""),
            description=issue.get("description"),
            is_complete=is_complete,
            status=state_name,
            project_linear_id=project_linear_id,
            milestone_linear_id=milestone_linear_id,
            parent_task_linear_id=parent_task_linear_id,
            priority_linear=issue.get("priority", 0),
            due_date=due_date,
            linear_state_id=state_id,
            linear_state_name=state_name,
            linear_state_type=state_type,
            linear_url=issue.get("url"),
            linear_identifier=issue.get("identifier"),
            linear_labels=labels,
        )
    
    def to_notion_properties(self, config: Any) -> dict:
        """Convert to Notion page properties for create/update.
        
        Args:
            config: NotionConfig with property names
            
        Returns:
            Notion properties dict
        """
        properties = {}
        
        # Title - check if it's a title or rich_text property
        if self.title:
            # Assuming task uses rich_text based on user's schema
            properties[config.task_title_prop] = {
                "rich_text": [{"text": {"content": self.title}}]
            }
        
        # Linear ID
        if self.linear_id:
            properties[config.task_linear_id_prop] = {
                "rich_text": [{"text": {"content": self.linear_id}}]
            }
        
        # Complete checkbox
        properties[config.task_complete_prop] = {
            "checkbox": self.is_complete
        }
        
        # Priority - will be mapped from linear_priority by mapper
        # properties[config.task_priority_prop] handled by mapper
        
        # Due date
        if self.due_date:
            properties[config.task_due_date_prop] = {
                "date": {"start": self.due_date.date().isoformat()}
            }
        
        # URL - store Linear URL
        if self.linear_url:
            properties[config.task_url_prop] = {
                "url": self.linear_url
            }
        
        # Relations (project, milestone, parent) handled by relation resolver
        
        return properties
    
    def to_linear_input(self, team_id: str) -> dict:
        """Convert to Linear Issue input for create/update.
        
        Args:
            team_id: Linear team ID (required for creation)
            
        Returns:
            Linear issue input dict for GraphQL mutation
        """
        data = {
            "teamId": team_id,
        }
        
        if self.title:
            data["title"] = self.title
        
        if self.description is not None:
            data["description"] = self.description
        
        if self.priority_linear is not None:
            data["priority"] = self.priority_linear
        
        if self.due_date:
            data["dueDate"] = self.due_date.date().isoformat()
        
        # Project and milestone IDs
        if self.project_linear_id:
            data["projectId"] = self.project_linear_id
        
        if self.milestone_linear_id:
            data["projectMilestoneId"] = self.milestone_linear_id
        
        if self.parent_task_linear_id:
            data["parentId"] = self.parent_task_linear_id
        
        # State ID for completion
        if self.linear_state_id:
            data["stateId"] = self.linear_state_id
        
        return data
    
    def to_sync_hash_dict(self) -> dict[str, Any]:
        """Get dictionary of fields for content hash."""
        return {
            "title": self.title,
            "description": self.description,
            "is_complete": self.is_complete,
            "status": self.status,
            "priority": self.priority,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "task_type": self.task_type,
        }
