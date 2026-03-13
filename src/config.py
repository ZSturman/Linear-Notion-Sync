"""Configuration management for Notion-Linear sync service.

Loads configuration from environment variables and provides
field mappings between Notion and Linear.
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pathlib import Path

from dotenv import load_dotenv


class ConflictStrategy(Enum):
    """Strategy for resolving conflicts when both systems have changes."""
    LAST_WRITE_WINS = "last-write-wins"
    NOTION_PRIMARY = "notion-primary"
    LINEAR_PRIMARY = "linear-primary"


class SyncDirection(Enum):
    """Direction for sync operations."""
    BIDIRECTIONAL = "bidirectional"
    NOTION_TO_LINEAR = "notion-to-linear"
    LINEAR_TO_NOTION = "linear-to-notion"


@dataclass
class NotionConfig:
    """Notion API configuration."""
    token: str
    projects_db_id: str
    milestones_db_id: str
    tasks_db_id: str
    
    # Property names in Notion databases
    # Projects
    project_title_prop: str = "title"
    project_linear_id_prop: str = "linear id"
    project_linear_sync_prop: str = "linear sync"
    project_status_prop: str = "status"
    project_description_prop: str = "one liner"
    project_started_at_prop: str = "started at"
    project_milestones_prop: str = "milestones"
    project_tasks_prop: str = "tasks"
    
    # Milestones
    milestone_title_prop: str = "milestone"
    milestone_linear_id_prop: str = "linear id"
    milestone_linear_sync_prop: str = "linear sync"
    milestone_description_prop: str = "description"
    milestone_project_prop: str = "project"
    milestone_tasks_prop: str = "tasks"
    milestone_due_date_formula_prop: str = "effective due date"
    milestone_due_date_manual_prop: str = "due date (manual)"
    milestone_all_complete_prop: str = "all tasks complete?"
    
    # Tasks
    task_title_prop: str = "task"
    task_linear_id_prop: str = "linear id"
    task_linear_sync_prop: str = "linear sync"
    task_complete_prop: str = "complete"
    task_project_prop: str = "project"
    task_milestones_prop: str = "milestones"
    task_parent_prop: str = "parent task"
    task_subtasks_prop: str = "subtasks"
    task_status_prop: str = "status"
    task_priority_prop: str = "priority"
    task_due_date_prop: str = "due date"
    task_type_prop: str = "type"
    task_url_prop: str = "url"


@dataclass
class LinearConfig:
    """Linear API configuration."""
    api_key: str
    team_id: str = ""
    team_key: str = ""  # Team key like "PROJ" for filtering
    api_url: str = "https://api.linear.app/graphql"


@dataclass
class FieldMappings:
    """Bidirectional field mappings between Notion and Linear."""
    
    # Priority: Notion select → Linear integer (0-4)
    # Linear: 0=none, 1=urgent, 2=high, 3=medium, 4=low
    priority_notion_to_linear: dict = field(default_factory=lambda: {
        None: 0,
        "": 0,
        "Low": 4,
        "Medium": 3,
        "High": 2,
        "Urgent": 1,  # Optional in Notion
    })
    
    priority_linear_to_notion: dict = field(default_factory=lambda: {
        0: None,
        1: "Urgent",
        2: "High",
        3: "Medium",
        4: "Low",
    })
    
    # Project status: Notion status → Linear project state
    project_status_notion_to_linear: dict = field(default_factory=lambda: {
        "Predevelopment": "planned",
        "Active": "started",
        "Dormant": "paused",
        "Complete": "completed",
        "Abandoned": "canceled",
    })
    
    project_status_linear_to_notion: dict = field(default_factory=lambda: {
        "planned": "Predevelopment",
        "started": "Active",
        "paused": "Dormant",
        "completed": "Complete",
        "canceled": "Abandoned",
    })
    
    # Task types to sync as Linear labels
    task_types: list = field(default_factory=lambda: [
        "Redo", "Plan", "Fix", "Test", "Design", "Research",
        "Write", "Build", "Organize", "Study", "Watch", "Share",
    ])

    # Task status aliases: Notion select values -> Linear workflow state type
    task_status_notion_to_linear_type: dict = field(default_factory=lambda: {
        "backlog": "backlog",
        "unstarted": "unstarted",
        "todo": "backlog",
        "to do": "backlog",
        "to-do": "backlog",
        "not started": "unstarted",
        "planned": "backlog",
        "triage": "backlog",
        "started": "started",
        "in progress": "started",
        "in-progress": "started",
        "inprogress": "started",
        "active": "started",
        "doing": "started",
        "in review": "started",
        "review": "started",
        "done": "completed",
        "complete": "completed",
        "completed": "completed",
        "canceled": "canceled",
        "cancelled": "canceled",
    })

    task_status_linear_type_to_notion: dict = field(default_factory=lambda: {
        "backlog": "Backlog",
        "unstarted": "Unstarted",
        "started": "In Progress",
        "completed": "Done",
        "canceled": "Canceled",
    })
    
    def get_linear_priority(self, notion_priority: Optional[str]) -> int:
        """Convert Notion priority to Linear priority integer."""
        return self.priority_notion_to_linear.get(notion_priority, 0)
    
    def get_notion_priority(self, linear_priority: int) -> Optional[str]:
        """Convert Linear priority integer to Notion priority string."""
        return self.priority_linear_to_notion.get(linear_priority)
    
    def get_linear_project_state(self, notion_status: str) -> str:
        """Convert Notion project status to Linear project state."""
        return self.project_status_notion_to_linear.get(notion_status, "started")
    
    def get_notion_project_status(self, linear_state: str) -> str:
        """Convert Linear project state to Notion project status."""
        return self.project_status_linear_to_notion.get(linear_state, "Active")
    
    def is_project_complete(self, notion_status: str) -> bool:
        """Check if a Notion project status indicates completion."""
        return notion_status in ("Complete", "Abandoned")
    
    def is_linear_state_complete(self, state: str) -> bool:
        """Check if a Linear state indicates completion."""
        return state in ("completed", "canceled")

    def get_linear_task_state_type(self, notion_status: Optional[str]) -> Optional[str]:
        """Convert a Notion task status value to a Linear workflow state type."""
        if not notion_status:
            return None
        normalized = notion_status.strip().lower()
        return self.task_status_notion_to_linear_type.get(normalized)

    def task_statuses_match(
        self,
        notion_status: Optional[str],
        linear_state_name: Optional[str],
        linear_state_type: Optional[str],
    ) -> bool:
        """Check whether a Notion status is semantically equivalent to a Linear state."""
        if not notion_status and not linear_state_name and not linear_state_type:
            return True

        normalized_notion_status = notion_status.strip().lower() if notion_status else None
        normalized_linear_name = (
            linear_state_name.strip().lower() if linear_state_name else None
        )

        if normalized_notion_status and normalized_linear_name:
            if normalized_notion_status == normalized_linear_name:
                return True

        notion_state_type = self.get_linear_task_state_type(notion_status)
        if notion_state_type and linear_state_type:
            return notion_state_type == linear_state_type

        if normalized_linear_name:
            linear_name_state_type = self.task_status_notion_to_linear_type.get(
                normalized_linear_name
            )
            if notion_state_type and linear_name_state_type:
                return notion_state_type == linear_name_state_type

        return False

    def get_notion_task_status(
        self,
        linear_state_name: Optional[str],
        linear_state_type: Optional[str],
    ) -> Optional[str]:
        """Convert Linear workflow state info to a Notion task status value."""
        if linear_state_name:
            return linear_state_name
        if linear_state_type:
            return self.task_status_linear_type_to_notion.get(linear_state_type)
        return None


@dataclass
class Config:
    """Main configuration container."""
    notion: NotionConfig
    linear: LinearConfig
    field_mappings: FieldMappings
    
    # Sync settings
    state_db_path: Path = field(default_factory=lambda: Path("./sync_state.db"))
    log_level: str = "INFO"
    dry_run: bool = False
    sync_direction: SyncDirection = SyncDirection.BIDIRECTIONAL
    conflict_strategy: ConflictStrategy = ConflictStrategy.LAST_WRITE_WINS
    
    # When creating Notion pages from Linear, default linear_sync to true
    import_linear_sync_default: bool = True

    @property
    def mappings(self) -> FieldMappings:
        """Backward-compatible alias used by mapper classes."""
        return self.field_mappings
    
    @classmethod
    def from_env(cls, env_path: Optional[str] = None) -> "Config":
        """Load configuration from environment variables.
        
        Args:
            env_path: Optional path to .env file. If not provided,
                     will look for .env in current directory.
        
        Returns:
            Config instance with all settings loaded.
            
        Raises:
            ValueError: If required environment variables are missing.
        """
        # Load .env file if it exists
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()
        
        # Validate required variables
        required_vars = [
            "NOTION_TOKEN",
            "NOTION_PROJECTS_DB_ID",
            "NOTION_MILESTONES_DB_ID", 
            "NOTION_TASKS_DB_ID",
            "LINEAR_API_KEY",
        ]
        
        missing = [var for var in required_vars if not os.getenv(var)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        
        notion_config = NotionConfig(
            token=os.getenv("NOTION_TOKEN", ""),
            projects_db_id=os.getenv("NOTION_PROJECTS_DB_ID", ""),
            milestones_db_id=os.getenv("NOTION_MILESTONES_DB_ID", ""),
            tasks_db_id=os.getenv("NOTION_TASKS_DB_ID", ""),
        )
        
        linear_config = LinearConfig(
            api_key=os.getenv("LINEAR_API_KEY", ""),
            team_id=os.getenv("LINEAR_TEAM_ID", ""),
            team_key=os.getenv("LINEAR_TEAM_KEY", ""),
        )
        
        # Parse optional settings
        conflict_strategy_str = os.getenv("CONFLICT_STRATEGY", "last-write-wins")
        try:
            conflict_strategy = ConflictStrategy(conflict_strategy_str)
        except ValueError:
            conflict_strategy = ConflictStrategy.LAST_WRITE_WINS
        
        sync_state_path = Path(os.getenv("SYNC_STATE_PATH", "./sync_state.db"))
        
        return cls(
            notion=notion_config,
            linear=linear_config,
            field_mappings=FieldMappings(),
            state_db_path=sync_state_path,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            dry_run=os.getenv("DRY_RUN", "").lower() == "true",
            conflict_strategy=conflict_strategy,
            import_linear_sync_default=os.getenv(
                "IMPORT_LINEAR_SYNC_DEFAULT", "true"
            ).lower() == "true",
        )


def load_config(env_path: Optional[str] = None) -> Config:
    """Load configuration from environment variables.
    
    Convenience function that wraps Config.from_env().
    
    Args:
        env_path: Optional path to .env file.
        
    Returns:
        Config instance.
    """
    return Config.from_env(env_path)
