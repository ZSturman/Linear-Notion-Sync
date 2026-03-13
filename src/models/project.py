"""Unified Project model for sync operations."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .base import SyncRecord, SyncSource


@dataclass
class UnifiedProject(SyncRecord):
    """Unified representation of a Project across Notion and Linear.
    
    Notion: Project page with title, one liner, status, etc.
    Linear: Project with name, description, state, etc.
    """
    
    # Core fields
    name: str = ""
    description: Optional[str] = None
    
    # Status
    status: Optional[str] = None  # Notion status value
    is_complete: bool = False
    
    # Dates
    started_at: Optional[datetime] = None
    
    # Relations (stored as IDs for later resolution)
    milestone_ids: list[str] = field(default_factory=list)  # notion_page_ids
    task_ids: list[str] = field(default_factory=list)  # notion_page_ids
    
    # Linear-specific
    linear_state: Optional[str] = None  # "planned", "started", "paused", "completed", "canceled"
    linear_url: Optional[str] = None
    
    @classmethod
    def from_notion(cls, page: dict, config: Any) -> "UnifiedProject":
        """Create UnifiedProject from Notion page data.
        
        Args:
            page: Notion page object from API
            config: NotionConfig with property names
            
        Returns:
            UnifiedProject instance
        """
        props = page.get("properties", {})
        
        # Extract title
        title_prop = props.get(config.project_title_prop, {})
        name = ""
        if title_prop.get("type") == "title":
            title_items = title_prop.get("title", [])
            name = "".join(item.get("plain_text", "") for item in title_items)
        
        # Extract linear id
        linear_id_prop = props.get(config.project_linear_id_prop, {})
        linear_id = None
        if linear_id_prop.get("type") == "rich_text":
            text_items = linear_id_prop.get("rich_text", [])
            linear_id = "".join(item.get("plain_text", "") for item in text_items) or None
        
        # Extract description (one liner)
        desc_prop = props.get(config.project_description_prop, {})
        description = None
        if desc_prop.get("type") == "rich_text":
            text_items = desc_prop.get("rich_text", [])
            description = "".join(item.get("plain_text", "") for item in text_items) or None
        
        # Extract status
        status_prop = props.get(config.project_status_prop, {})
        status = None
        if status_prop.get("type") == "status":
            status_data = status_prop.get("status")
            status = status_data.get("name") if status_data else None
        elif status_prop.get("type") == "select":
            select_data = status_prop.get("select")
            status = select_data.get("name") if select_data else None
        
        # Extract started_at
        started_at = None
        started_prop = props.get(config.project_started_at_prop, {})
        if started_prop.get("type") == "date":
            date_data = started_prop.get("date")
            if date_data and date_data.get("start"):
                started_at = datetime.fromisoformat(
                    date_data["start"].replace("Z", "+00:00")
                )
        
        # Extract milestone relation IDs
        milestone_ids = []
        milestone_prop = props.get(config.project_milestones_prop, {})
        if milestone_prop.get("type") == "relation":
            milestone_ids = [
                rel.get("id") for rel in milestone_prop.get("relation", [])
                if rel.get("id")
            ]
        
        # Extract task relation IDs
        task_ids = []
        task_prop = props.get(config.project_tasks_prop, {})
        if task_prop.get("type") == "relation":
            task_ids = [
                rel.get("id") for rel in task_prop.get("relation", [])
                if rel.get("id")
            ]
        
        # Parse last edited time
        last_edited = None
        if page.get("last_edited_time"):
            last_edited = datetime.fromisoformat(
                page["last_edited_time"].replace("Z", "+00:00")
            )
        
        # Determine completion
        is_complete = status in ("Complete", "Abandoned")
        
        return cls(
            notion_page_id=page.get("id"),
            linear_id=linear_id,
            notion_last_modified=last_edited,
            source=SyncSource.NOTION,
            name=name,
            description=description,
            status=status,
            is_complete=is_complete,
            started_at=started_at,
            milestone_ids=milestone_ids,
            task_ids=task_ids,
        )
    
    @classmethod
    def from_linear(cls, project: dict) -> "UnifiedProject":
        """Create UnifiedProject from Linear project data.
        
        Args:
            project: Linear project object from GraphQL API
            
        Returns:
            UnifiedProject instance
        """
        # Parse dates
        started_at = None
        if project.get("startDate"):
            started_at = datetime.fromisoformat(
                project["startDate"].replace("Z", "+00:00")
            )
        
        updated_at = None
        if project.get("updatedAt"):
            updated_at = datetime.fromisoformat(
                project["updatedAt"].replace("Z", "+00:00")
            )
        
        # Get state
        state = project.get("state")  # "planned", "started", "paused", "completed", "canceled"
        is_complete = state in ("completed", "canceled")
        
        return cls(
            linear_id=project.get("id"),
            linear_last_modified=updated_at,
            source=SyncSource.LINEAR,
            name=project.get("name", ""),
            description=project.get("description"),
            linear_state=state,
            is_complete=is_complete,
            started_at=started_at,
            linear_url=project.get("url"),
        )
    
    def to_notion_properties(self, config: Any) -> dict:
        """Convert to Notion page properties for create/update.
        
        Args:
            config: NotionConfig with property names
            
        Returns:
            Notion properties dict
        """
        properties = {}
        
        # Title
        if self.name:
            properties[config.project_title_prop] = {
                "title": [{"text": {"content": self.name}}]
            }
        
        # Linear ID
        if self.linear_id:
            properties[config.project_linear_id_prop] = {
                "rich_text": [{"text": {"content": self.linear_id}}]
            }
        
        # Description (one liner)
        if self.description is not None:
            properties[config.project_description_prop] = {
                "rich_text": [{"text": {"content": self.description}}]
            }
        
        # Status - map from Linear state
        if self.linear_state:
            # This will be mapped by the caller using FieldMappings
            pass  # Status mapping handled by mapper
        
        # Started at
        if self.started_at:
            properties[config.project_started_at_prop] = {
                "date": {"start": self.started_at.date().isoformat()}
            }
        
        return properties
    
    def to_linear_input(self) -> dict:
        """Convert to Linear project input for create/update.
        
        Returns:
            Linear project input dict for GraphQL mutation
        """
        data = {}
        
        if self.name:
            data["name"] = self.name
        
        if self.description is not None:
            data["description"] = self.description
        
        if self.linear_state:
            data["state"] = self.linear_state
        
        if self.started_at:
            data["startDate"] = self.started_at.date().isoformat()
        
        return data
    
    def to_sync_hash_dict(self) -> dict[str, Any]:
        """Get dictionary of fields for content hash."""
        return {
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "is_complete": self.is_complete,
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }
