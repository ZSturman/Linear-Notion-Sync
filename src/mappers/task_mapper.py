"""Task mapper for converting between Notion and Linear formats."""

from typing import Optional

from ..config import Config
from ..models.task import UnifiedTask
from ..utils.logging import get_logger

logger = get_logger(__name__)


class TaskMapper:
    """Maps task data between Notion and Linear formats.

    Handles:
    - task ↔ title
    - complete ↔ state (completed/not completed)
    - priority ↔ priority (with mapping)
    - due date ↔ dueDate
    - type ↔ labels
    - project ↔ projectId
    - milestones ↔ projectMilestoneId
    - parent task ↔ parentId
    """

    def __init__(self, config: Config):
        """Initialize mapper.

        Args:
            config: Application configuration
        """
        self.config = config
        self.notion_config = config.notion
        self.mappings = config.field_mappings

    def notion_to_unified(self, page: dict) -> UnifiedTask:
        """Convert Notion page to UnifiedTask.

        Args:
            page: Notion page object

        Returns:
            UnifiedTask instance
        """
        task = UnifiedTask.from_notion(page, self.notion_config)

        # Map Notion priority to Linear priority
        if task.priority:
            task.priority_linear = self.mappings.get_linear_priority(task.priority)

        return task

    def linear_to_unified(self, issue: dict) -> UnifiedTask:
        """Convert Linear Issue to UnifiedTask.

        Args:
            issue: Linear issue object

        Returns:
            UnifiedTask instance
        """
        task = UnifiedTask.from_linear(issue)

        task.status = self.mappings.get_notion_task_status(
            task.linear_state_name,
            task.linear_state_type,
        )

        # Map Linear priority to Notion priority
        if task.priority_linear is not None:
            task.priority = self.mappings.get_notion_priority(task.priority_linear)

        # Map type from labels (first matching task type label)
        for label in task.linear_labels:
            if label in self.mappings.task_types:
                task.task_type = label
                break

        return task

    def _notion_option_value(self, value: Optional[str]) -> Optional[str]:
        """Normalize Notion option values.

        Args:
            value: Raw option value

        Returns:
            Trimmed value or None
        """
        if value is None:
            return None

        cleaned = value.strip()
        return cleaned or None

    def to_notion_properties(
        self,
        task: UnifiedTask,
        include_relations: bool = False,
    ) -> dict:
        """Convert UnifiedTask to Notion properties.

        Notes:
            This mapper assumes the Notion task schema uses:
            - task: Title
            - priority: Select
            - type: Select

            If your database currently uses Status for priority/type, either
            update the schema to Select or adjust the payload shapes here.

        Args:
            task: UnifiedTask instance
            include_relations: Include relation properties

        Returns:
            Notion properties dict
        """
        props = {}

        # Title - task must be a title property in Notion
        title_value = self._notion_option_value(task.title)
        if title_value:
            props[self.notion_config.task_title_prop] = {
                "title": [{"text": {"content": title_value}}]
            }

        # Linear ID
        if task.linear_id:
            props[self.notion_config.task_linear_id_prop] = {
                "rich_text": [{"text": {"content": task.linear_id}}]
            }

        # Complete checkbox
        props[self.notion_config.task_complete_prop] = {
            "checkbox": task.is_complete
        }

        status_value = self._notion_option_value(task.status)
        if not status_value:
            status_value = self._notion_option_value(
                self.mappings.get_notion_task_status(
                    task.linear_state_name,
                    task.linear_state_type,
                )
            )
        if status_value:
            props[self.notion_config.task_status_prop] = {
                "select": {"name": status_value}
            }

        # Priority - recommend Notion Select values: Low, Medium, High, Urgent
        priority_value = self._notion_option_value(task.priority)
        if not priority_value and task.priority_linear is not None:
            priority_value = self._notion_option_value(
                self.mappings.get_notion_priority(task.priority_linear)
            )

        if priority_value:
            props[self.notion_config.task_priority_prop] = {
                "select": {"name": priority_value}
            }

        # Due date
        if task.due_date:
            props[self.notion_config.task_due_date_prop] = {
                "date": {"start": task.due_date.date().isoformat()}
            }

        # Type - recommend Notion Select values matching mapped Linear labels
        type_value = self._notion_option_value(task.task_type)
        if type_value:
            props[self.notion_config.task_type_prop] = {
                "select": {"name": type_value}
            }

        # URL - store Linear URL
        if task.linear_url:
            props[self.notion_config.task_url_prop] = {
                "url": task.linear_url
            }

        if include_relations:
            if task.project_notion_id:
                props[self.notion_config.task_project_prop] = {
                    "relation": [{"id": task.project_notion_id}]
                }

            if task.milestone_notion_id:
                props[self.notion_config.task_milestones_prop] = {
                    "relation": [{"id": task.milestone_notion_id}]
                }

            if task.parent_task_notion_id:
                props[self.notion_config.task_parent_prop] = {
                    "relation": [{"id": task.parent_task_notion_id}]
                }

        return props

    def to_linear_input(
        self,
        task: UnifiedTask,
        team_id: str,
        state_id: Optional[str] = None,
        label_ids: Optional[list[str]] = None,
        for_update: bool = False,
    ) -> dict:
        """Convert UnifiedTask to Linear issue input.

        Args:
            task: UnifiedTask instance
            team_id: Linear team ID
            state_id: Workflow state ID for completion status
            label_ids: Label IDs for task type
            for_update: If True, exclude required fields for creation

        Returns:
            Linear issue input dict
        """
        data = {}

        if not for_update:
            data["teamId"] = team_id

        if task.title:
            data["title"] = task.title

        if task.description is not None:
            data["description"] = task.description

        # Priority
        if task.priority_linear is not None:
            data["priority"] = task.priority_linear
        elif task.priority:
            data["priority"] = self.mappings.get_linear_priority(task.priority)

        # Due date
        if task.due_date:
            data["dueDate"] = task.due_date.date().isoformat()

        # State for completion
        if state_id:
            data["stateId"] = state_id

        # Labels for task type
        if label_ids:
            data["labelIds"] = label_ids

        # Relations (set by relation resolver)
        if task.project_linear_id:
            data["projectId"] = task.project_linear_id

        if task.milestone_linear_id:
            data["projectMilestoneId"] = task.milestone_linear_id

        if task.parent_task_linear_id:
            data["parentId"] = task.parent_task_linear_id

        return data

    def resolve_linear_state_id(
        self,
        task: UnifiedTask,
        workflow_states: dict[str, dict],
    ) -> Optional[str]:
        """Resolve the best Linear workflow state ID for a task."""
        if task.linear_state_id:
            return task.linear_state_id

        if task.status:
            status_name = task.status.strip().lower()
            for state in workflow_states.values():
                name = (state.get("name") or "").strip().lower()
                if name == status_name:
                    return state.get("id")

            state_type = self.mappings.get_linear_task_state_type(task.status)
            if state_type:
                for state in workflow_states.values():
                    if state.get("type") == state_type:
                        return state.get("id")

        if task.linear_state_name:
            state_name = task.linear_state_name.strip().lower()
            for state in workflow_states.values():
                name = (state.get("name") or "").strip().lower()
                if name == state_name:
                    return state.get("id")

        if task.linear_state_type:
            for state in workflow_states.values():
                if state.get("type") == task.linear_state_type:
                    return state.get("id")

        fallback_type = "completed" if task.is_complete else "backlog"
        for state in workflow_states.values():
            if state.get("type") == fallback_type:
                return state.get("id")

        if not task.is_complete:
            for fallback_type in ("unstarted", "started"):
                for state in workflow_states.values():
                    if state.get("type") == fallback_type:
                        return state.get("id")

        if task.is_complete:
            for state in workflow_states.values():
                if state.get("type") == "canceled":
                    return state.get("id")

        return None

    def statuses_match(
        self,
        notion_task: UnifiedTask,
        linear_task: UnifiedTask,
    ) -> bool:
        """Check whether Notion and Linear status values describe the same workflow state."""
        return self.mappings.task_statuses_match(
            notion_task.status,
            linear_task.linear_state_name,
            linear_task.linear_state_type,
        )

    def detect_changes(
        self,
        notion_task: UnifiedTask,
        linear_task: UnifiedTask,
    ) -> list[str]:
        """Detect which fields have changed between versions.

        Args:
            notion_task: Task from Notion
            linear_task: Task from Linear

        Returns:
            List of changed field names
        """
        changes = []

        if notion_task.title != linear_task.title:
            changes.append("title")

        # Compare descriptions
        notion_desc = notion_task.description or ""
        linear_desc = linear_task.description or ""
        if notion_desc != linear_desc:
            changes.append("description")

        # Compare completion status
        if notion_task.is_complete != linear_task.is_complete:
            changes.append("is_complete")

        if not self.statuses_match(notion_task, linear_task):
            changes.append("status")

        # Compare priority via mapping
        notion_linear_priority = self.mappings.get_linear_priority(notion_task.priority)
        if notion_linear_priority != (linear_task.priority_linear or 0):
            changes.append("priority")

        # Compare due dates
        notion_date = notion_task.due_date.date() if notion_task.due_date else None
        linear_date = linear_task.due_date.date() if linear_task.due_date else None
        if notion_date != linear_date:
            changes.append("due_date")

        # Compare task type (labels)
        if notion_task.task_type != linear_task.task_type:
            changes.append("task_type")

        # Compare relations (IDs)
        if notion_task.project_notion_id != linear_task.project_notion_id:
            changes.append("project")

        if notion_task.milestone_notion_id != linear_task.milestone_notion_id:
            changes.append("milestone")

        if notion_task.parent_task_notion_id != linear_task.parent_task_notion_id:
            changes.append("parent_task")

        return changes

    def get_label_ids_for_type(
        self,
        task_type: Optional[str],
        available_labels: dict[str, str],
    ) -> list[str]:
        """Get label IDs for a task type.

        Args:
            task_type: Task type name
            available_labels: Dict of label name to ID

        Returns:
            List of label IDs
        """
        if not task_type:
            return []

        if task_type in available_labels:
            return [available_labels[task_type]]

        return []