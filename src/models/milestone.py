"""Unified Milestone model for sync operations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from .base import SyncRecord, SyncSource


@dataclass
class UnifiedMilestone(SyncRecord):
    """Unified representation of a Milestone across Notion and Linear.
    
    Notion: Milestone page with title, description, project relation, etc.
    Linear: ProjectMilestone with name, description, targetDate, etc.
    """
    
    # Core fields
    name: str = ""
    description: Optional[str] = None
    
    # Parent project
    project_notion_id: Optional[str] = None
    project_linear_id: Optional[str] = None
    
    # Dates
    target_date: Optional[datetime] = None
    due_date_manual: Optional[datetime] = None  # For writing to Notion
    
    # Status/completion
    is_complete: bool = False
    all_tasks_complete: Optional[bool] = None  # From Notion formula
    
    # Linear-specific
    linear_url: Optional[str] = None
    
    @classmethod
    def from_notion(cls, page: dict, config: Any) -> "UnifiedMilestone":
        """Create UnifiedMilestone from Notion page data.
        
        Args:
            page: Notion page object from API
            config: NotionConfig with property names
            
        Returns:
            UnifiedMilestone instance
        """
        props = page.get("properties", {})
        
        # Extract title
        title_prop = props.get(config.milestone_title_prop, {})
        name = ""
        if title_prop.get("type") == "title":
            title_items = title_prop.get("title", [])
            name = "".join(item.get("plain_text", "") for item in title_items)
        
        # Extract linear id
        linear_id_prop = props.get(config.milestone_linear_id_prop, {})
        linear_id = None
        if linear_id_prop.get("type") == "rich_text":
            text_items = linear_id_prop.get("rich_text", [])
            linear_id = "".join(item.get("plain_text", "") for item in text_items) or None
        
        # Extract description
        desc_prop = props.get(config.milestone_description_prop, {})
        description = None
        if desc_prop.get("type") == "rich_text":
            text_items = desc_prop.get("rich_text", [])
            description = "".join(item.get("plain_text", "") for item in text_items) or None
        
        # Extract project relation
        project_notion_id = None
        project_prop = props.get(config.milestone_project_prop, {})
        if project_prop.get("type") == "relation":
            relations = project_prop.get("relation", [])
            if relations:
                project_notion_id = relations[0].get("id")  # First related project
        
        # Extract target date from formula or manual
        target_date = None
        
        # Try effective due date formula first
        effective_date_prop = props.get(config.milestone_due_date_formula_prop, {})
        if effective_date_prop.get("type") == "formula":
            formula_result = effective_date_prop.get("formula", {})
            if formula_result.get("type") == "date" and formula_result.get("date"):
                date_obj = formula_result["date"]
                if date_obj.get("start"):
                    target_date = datetime.fromisoformat(
                        date_obj["start"].replace("Z", "+00:00")
                    )
        
        # Fallback to manual due date
        due_date_manual = None
        manual_date_prop = props.get(config.milestone_due_date_manual_prop, {})
        if manual_date_prop.get("type") == "date":
            date_data = manual_date_prop.get("date")
            if date_data and date_data.get("start"):
                due_date_manual = datetime.fromisoformat(
                    date_data["start"].replace("Z", "+00:00")
                )
                if target_date is None:
                    target_date = due_date_manual
        
        # Extract all tasks complete formula
        all_tasks_complete = None
        all_complete_prop = props.get(config.milestone_all_complete_prop, {})
        if all_complete_prop.get("type") == "formula":
            formula_result = all_complete_prop.get("formula", {})
            if formula_result.get("type") == "boolean":
                all_tasks_complete = formula_result.get("boolean", False)
        
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
            name=name,
            description=description,
            project_notion_id=project_notion_id,
            target_date=target_date,
            due_date_manual=due_date_manual,
            is_complete=all_tasks_complete or False,
            all_tasks_complete=all_tasks_complete,
        )
    
    @classmethod
    def from_linear(cls, milestone: dict) -> "UnifiedMilestone":
        """Create UnifiedMilestone from Linear ProjectMilestone data.
        
        Args:
            milestone: Linear ProjectMilestone object from GraphQL API
            
        Returns:
            UnifiedMilestone instance
        """
        # Parse target date
        target_date = None
        if milestone.get("targetDate"):
            target_date = datetime.fromisoformat(
                milestone["targetDate"].replace("Z", "+00:00")
            )
        
        # Parse updated at
        updated_at = None
        if milestone.get("updatedAt"):
            updated_at = datetime.fromisoformat(
                milestone["updatedAt"].replace("Z", "+00:00")
            )
        
        # Get project ID
        project_linear_id = None
        if milestone.get("project"):
            project_linear_id = milestone["project"].get("id")
        
        return cls(
            linear_id=milestone.get("id"),
            linear_last_modified=updated_at,
            source=SyncSource.LINEAR,
            name=milestone.get("name", ""),
            description=milestone.get("description"),
            project_linear_id=project_linear_id,
            target_date=target_date,
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
            properties[config.milestone_title_prop] = {
                "title": [{"text": {"content": self.name}}]
            }
        
        # Linear ID
        if self.linear_id:
            properties[config.milestone_linear_id_prop] = {
                "rich_text": [{"text": {"content": self.linear_id}}]
            }
        
        # Description
        if self.description is not None:
            properties[config.milestone_description_prop] = {
                "rich_text": [{"text": {"content": self.description}}]
            }
        
        # Project relation - will be set by relation resolver
        # properties[config.milestone_project_prop] handled separately
        
        # Due date (manual) - write to the writable field
        if self.target_date:
            properties[config.milestone_due_date_manual_prop] = {
                "date": {"start": self.target_date.date().isoformat()}
            }
        
        return properties
    
    def to_linear_input(self, team_id: str) -> dict:
        """Convert to Linear ProjectMilestone input for create/update.
        
        Args:
            team_id: Linear team ID (required for creation)
            
        Returns:
            Linear milestone input dict for GraphQL mutation
        """
        data = {}
        
        if self.name:
            data["name"] = self.name
        
        if self.description is not None:
            data["description"] = self.description
        
        if self.target_date:
            data["targetDate"] = self.target_date.date().isoformat()
        
        return data
    
    def to_sync_hash_dict(self) -> dict[str, Any]:
        """Get dictionary of fields for content hash."""
        return {
            "name": self.name,
            "description": self.description,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "is_complete": self.is_complete,
        }
