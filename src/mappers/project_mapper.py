"""Project mapper for converting between Notion and Linear formats."""

from typing import Any, Optional

from ..config import Config, FieldMappings, NotionConfig
from ..models.project import UnifiedProject
from ..utils.logging import get_logger

logger = get_logger(__name__)


class ProjectMapper:
    """Maps project data between Notion and Linear formats.
    
    Handles:
    - name ↔ name
    - one liner ↔ description
    - status ↔ state (with mapping)
    - started at ↔ startDate
    """
    
    def __init__(self, config: Config):
        """Initialize mapper.
        
        Args:
            config: Application configuration
        """
        self.config = config
        self.notion_config = config.notion
        self.mappings = config.field_mappings
    
    def notion_to_unified(self, page: dict) -> UnifiedProject:
        """Convert Notion page to UnifiedProject.
        
        Args:
            page: Notion page object
            
        Returns:
            UnifiedProject instance
        """
        return UnifiedProject.from_notion(page, self.notion_config)
    
    def linear_to_unified(self, project: dict) -> UnifiedProject:
        """Convert Linear project to UnifiedProject.
        
        Args:
            project: Linear project object
            
        Returns:
            UnifiedProject instance
        """
        unified = UnifiedProject.from_linear(project)
        
        # Map Linear state to Notion status
        if unified.linear_state:
            unified.status = self.mappings.get_notion_project_status(unified.linear_state)
        
        return unified
    
    def to_notion_properties(
        self,
        project: UnifiedProject,
        include_relations: bool = False,
    ) -> dict:
        """Convert UnifiedProject to Notion properties.
        
        Args:
            project: UnifiedProject instance
            include_relations: Include relation properties
            
        Returns:
            Notion properties dict
        """
        props = project.to_notion_properties(self.notion_config)
        
        # Map status from Linear state if available
        if project.linear_state:
            notion_status = self.mappings.get_notion_project_status(project.linear_state)
            if notion_status:
                props[self.notion_config.project_status_prop] = {
                    "select": {"name": notion_status}
                }
        elif project.status:
            props[self.notion_config.project_status_prop] = {
                 "select": {"name": project.status}
            }
        
        return props
    
    def to_linear_input(
        self,
        project: UnifiedProject,
        for_update: bool = False,
    ) -> dict:
        """Convert UnifiedProject to Linear project input.
        
        Args:
            project: UnifiedProject instance
            for_update: If True, exclude required fields for creation
            
        Returns:
            Linear project input dict
        """
        data = {}
        
        if project.name:
            data["name"] = project.name
        
        if project.description is not None:
            data["description"] = project.description
        
        # Map Notion status to Linear state
        if project.status:
            linear_state = self.mappings.get_linear_project_state(project.status)
            data["state"] = linear_state
        elif project.linear_state:
            data["state"] = project.linear_state
        
        if project.started_at:
            data["startDate"] = project.started_at.date().isoformat()
        
        return data
    
    def detect_changes(
        self,
        notion_project: UnifiedProject,
        linear_project: UnifiedProject,
    ) -> list[str]:
        """Detect which fields have changed between versions.
        
        Args:
            notion_project: Project from Notion
            linear_project: Project from Linear
            
        Returns:
            List of changed field names
        """
        changes = []
        
        if notion_project.name != linear_project.name:
            changes.append("name")
        
        # Compare descriptions (normalize None to empty string)
        notion_desc = notion_project.description or ""
        linear_desc = linear_project.description or ""
        if notion_desc != linear_desc:
            changes.append("description")
        
        # Compare status via mapping
        notion_linear_state = self.mappings.get_linear_project_state(
            notion_project.status or "Active"
        )
        if notion_linear_state != linear_project.linear_state:
            changes.append("status")
        
        # Compare dates
        if notion_project.started_at != linear_project.started_at:
            changes.append("started_at")
        
        return changes
