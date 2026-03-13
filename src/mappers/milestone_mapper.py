"""Milestone mapper for converting between Notion and Linear formats."""

from typing import Any, Optional

from ..config import Config, NotionConfig
from ..models.milestone import UnifiedMilestone
from ..utils.logging import get_logger

logger = get_logger(__name__)


class MilestoneMapper:
    """Maps milestone data between Notion and Linear formats.
    
    Handles:
    - milestone ↔ name
    - description ↔ description
    - effective due date / due date (manual) ↔ targetDate
    - project relation ↔ projectId
    """
    
    def __init__(self, config: Config):
        """Initialize mapper.
        
        Args:
            config: Application configuration
        """
        self.config = config
        self.notion_config = config.notion
        self.mappings = config.field_mappings
    
    def notion_to_unified(self, page: dict) -> UnifiedMilestone:
        """Convert Notion page to UnifiedMilestone.
        
        Args:
            page: Notion page object
            
        Returns:
            UnifiedMilestone instance
        """
        return UnifiedMilestone.from_notion(page, self.notion_config)
    
    def linear_to_unified(self, milestone: dict) -> UnifiedMilestone:
        """Convert Linear ProjectMilestone to UnifiedMilestone.
        
        Args:
            milestone: Linear milestone object
            
        Returns:
            UnifiedMilestone instance
        """
        return UnifiedMilestone.from_linear(milestone)
    
    def to_notion_properties(
        self,
        milestone: UnifiedMilestone,
        include_relations: bool = False,
    ) -> dict:
        """Convert UnifiedMilestone to Notion properties.
        
        Args:
            milestone: UnifiedMilestone instance
            include_relations: Include relation properties
            
        Returns:
            Notion properties dict
        """
        props = milestone.to_notion_properties(self.notion_config)
        
        # Note: Project relation is handled separately by relation resolver
        
        return props
    
    def to_linear_input(
        self,
        milestone: UnifiedMilestone,
        project_linear_id: Optional[str] = None,
        for_update: bool = False,
    ) -> dict:
        """Convert UnifiedMilestone to Linear milestone input.
        
        Args:
            milestone: UnifiedMilestone instance
            project_linear_id: Linear project ID for creation
            for_update: If True, exclude required fields for creation
            
        Returns:
            Linear milestone input dict
        """
        data = {}
        
        if milestone.name:
            data["name"] = milestone.name
        
        if milestone.description is not None:
            data["description"] = milestone.description
        
        if milestone.target_date:
            data["targetDate"] = milestone.target_date.date().isoformat()
        
        # Project ID needed for creation
        if not for_update:
            if milestone.project_linear_id:
                data["projectId"] = milestone.project_linear_id
            elif project_linear_id:
                data["projectId"] = project_linear_id
        
        return data
    
    def detect_changes(
        self,
        notion_milestone: UnifiedMilestone,
        linear_milestone: UnifiedMilestone,
    ) -> list[str]:
        """Detect which fields have changed between versions.
        
        Args:
            notion_milestone: Milestone from Notion
            linear_milestone: Milestone from Linear
            
        Returns:
            List of changed field names
        """
        changes = []
        
        if notion_milestone.name != linear_milestone.name:
            changes.append("name")
        
        # Compare descriptions
        notion_desc = notion_milestone.description or ""
        linear_desc = linear_milestone.description or ""
        if notion_desc != linear_desc:
            changes.append("description")
        
        # Compare target dates (date only, not time)
        notion_date = notion_milestone.target_date.date() if notion_milestone.target_date else None
        linear_date = linear_milestone.target_date.date() if linear_milestone.target_date else None
        if notion_date != linear_date:
            changes.append("target_date")
        
        return changes
