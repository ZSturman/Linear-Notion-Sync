"""Sync engine orchestrating the full sync workflow."""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..config import Config, SyncDirection
from ..clients.notion_client import NotionClient
from ..clients.linear_client import LinearClient
from ..mappers.milestone_mapper import MilestoneMapper
from ..mappers.project_mapper import ProjectMapper
from ..mappers.task_mapper import TaskMapper
from ..models.base import SyncSource
from ..models.milestone import UnifiedMilestone
from ..models.project import UnifiedProject
from ..models.task import UnifiedTask
from ..utils.logging import get_logger
from ..utils.state import SyncState, SyncStateStore
from .reconciler import ChangeSet, Change, ChangeType, Reconciler
from .relation_resolver import RelationResolver

logger = get_logger(__name__)


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamps returned by Notion and Linear."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class SyncResult:
    """Result of a sync operation."""

    success: bool = True
    projects_created: int = 0
    milestones_created: int = 0
    tasks_created: int = 0
    projects_updated: int = 0
    milestones_updated: int = 0
    tasks_updated: int = 0
    projects_deleted: int = 0
    milestones_deleted: int = 0
    tasks_deleted: int = 0
    duration_seconds: Optional[float] = None
    errors: list[str] = field(default_factory=list)

    @property
    def total_created(self) -> int:
        return self.projects_created + self.milestones_created + self.tasks_created

    @property
    def total_updated(self) -> int:
        return self.projects_updated + self.milestones_updated + self.tasks_updated

    @property
    def total_deleted(self) -> int:
        return self.projects_deleted + self.milestones_deleted + self.tasks_deleted


class SyncEngine:
    """Orchestrates the bidirectional sync between Notion and Linear."""

    def __init__(
        self,
        config: Config,
        notion: NotionClient,
        linear: LinearClient,
        state_store: SyncStateStore,
    ) -> None:
        self.config = config
        self.notion = notion
        self.linear = linear
        self.state_store = state_store
        self.project_mapper = ProjectMapper(config)
        self.milestone_mapper = MilestoneMapper(config)
        self.task_mapper = TaskMapper(config)
        self.reconciler = Reconciler(config, state_store)
        self.relation_resolver = RelationResolver(state_store)

    async def sync(
        self,
        direction: SyncDirection = SyncDirection.BIDIRECTIONAL,
        entity_types: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> SyncResult:
        if entity_types is None:
            entity_types = ["projects", "milestones", "tasks"]

        start_time = time.monotonic()
        result = SyncResult()

        logger.info(
            f"Starting sync: direction={direction.value}, "
            f"entity_types={entity_types}, dry_run={dry_run}"
        )

        try:
            notion_projects, notion_all_projects, linear_projects = [], [], []
            notion_milestones, notion_all_milestones, linear_milestones = [], [], []
            notion_tasks, notion_all_tasks, linear_tasks = [], [], []

            fetch_from_linear = direction != SyncDirection.NOTION_TO_LINEAR

            if "projects" in entity_types:
                notion_all_projects = [
                    self.project_mapper.notion_to_unified(p)
                    for p in self.notion.query_projects(synced_only=False)
                ]
                notion_projects = [
                    self.project_mapper.notion_to_unified(p)
                    for p in self.notion.query_projects()
                ]
                if fetch_from_linear:
                    linear_projects = [
                        self.project_mapper.linear_to_unified(p)
                        for p in self.linear.get_projects()
                    ]

            if "milestones" in entity_types:
                notion_all_milestones = [
                    self.milestone_mapper.notion_to_unified(m)
                    for m in self.notion.query_milestones(synced_only=False)
                ]
                notion_milestones = [
                    self.milestone_mapper.notion_to_unified(m)
                    for m in self.notion.query_milestones()
                ]
                if fetch_from_linear:
                    linear_milestones = [
                        self.milestone_mapper.linear_to_unified(m)
                        for m in self.linear.get_milestones()
                    ]

            if "tasks" in entity_types:
                notion_all_tasks = [
                    self.task_mapper.notion_to_unified(t)
                    for t in self.notion.query_tasks(synced_only=False)
                ]
                notion_tasks = [
                    self.task_mapper.notion_to_unified(t)
                    for t in self.notion.query_tasks()
                ]
                if fetch_from_linear:
                    linear_tasks = [
                        self.task_mapper.linear_to_unified(t)
                        for t in self.linear.get_issues()
                    ]

            self.relation_resolver.build_maps(
                notion_projects + linear_projects,
                notion_milestones + linear_milestones,
                notion_tasks + linear_tasks,
            )

            changeset = self.reconciler.compute_changes(
                notion_projects,
                linear_projects if fetch_from_linear else [],
                notion_milestones,
                linear_milestones if fetch_from_linear else [],
                notion_tasks,
                linear_tasks if fetch_from_linear else [],
                existing_notion_project_ids={
                    project.notion_page_id
                    for project in notion_all_projects
                    if project.notion_page_id
                },
                existing_notion_milestone_ids={
                    milestone.notion_page_id
                    for milestone in notion_all_milestones
                    if milestone.notion_page_id
                },
                existing_notion_task_ids={
                    task.notion_page_id
                    for task in notion_all_tasks
                    if task.notion_page_id
                },
            )

            if dry_run:
                self._tally_changeset(changeset, result)
                logger.info(
                    f"DRY RUN: {changeset.creates} creates, "
                    f"{changeset.updates} updates, {changeset.conflicts} conflicts"
                )
            else:
                await self._apply_changeset(changeset, direction, result)
                self.state_store.set_last_sync_time()

        except Exception as e:
            logger.error(f"Sync failed: {e}", exc_info=True)
            result.success = False
            result.errors.append(str(e))

        result.duration_seconds = time.monotonic() - start_time
        logger.info(
            f"Sync complete: created={result.total_created}, "
            f"updated={result.total_updated}, deleted={result.total_deleted}, "
            f"errors={len(result.errors)}"
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tally_changeset(self, changeset: ChangeSet, result: SyncResult) -> None:
        for change in changeset.project_changes:
            if change.change_type == ChangeType.CREATE:
                result.projects_created += 1
            elif change.change_type == ChangeType.DELETE:
                result.projects_deleted += 1
            elif change.change_type in (ChangeType.UPDATE, ChangeType.CONFLICT):
                result.projects_updated += 1
        for change in changeset.milestone_changes:
            if change.change_type == ChangeType.CREATE:
                result.milestones_created += 1
            elif change.change_type == ChangeType.DELETE:
                result.milestones_deleted += 1
            elif change.change_type in (ChangeType.UPDATE, ChangeType.CONFLICT):
                result.milestones_updated += 1
        for change in changeset.task_changes:
            if change.change_type == ChangeType.CREATE:
                result.tasks_created += 1
            elif change.change_type == ChangeType.DELETE:
                result.tasks_deleted += 1
            elif change.change_type in (ChangeType.UPDATE, ChangeType.CONFLICT):
                result.tasks_updated += 1

    async def _apply_changeset(
        self, changeset: ChangeSet, direction: SyncDirection, result: SyncResult
    ) -> None:
        for change in changeset.project_changes:
            if not self._should_apply(change, direction):
                continue
            try:
                await self._apply_project_change(change)
                if change.change_type == ChangeType.CREATE:
                    result.projects_created += 1
                elif change.change_type == ChangeType.DELETE:
                    result.projects_deleted += 1
                else:
                    result.projects_updated += 1
            except Exception as e:
                logger.error(f"Failed project change ({change.identifier}): {e}", exc_info=True)
                result.errors.append(f"project {change.identifier}: {e}")

        for change in changeset.milestone_changes:
            if not self._should_apply(change, direction):
                continue
            try:
                await self._apply_milestone_change(change)
                if change.change_type == ChangeType.CREATE:
                    result.milestones_created += 1
                elif change.change_type == ChangeType.DELETE:
                    result.milestones_deleted += 1
                else:
                    result.milestones_updated += 1
            except Exception as e:
                logger.error(f"Failed milestone change ({change.identifier}): {e}", exc_info=True)
                result.errors.append(f"milestone {change.identifier}: {e}")

        # Apply tasks in topological order to respect parent-child dependencies
        task_records = [
            c.record for c in changeset.task_changes if isinstance(c.record, UnifiedTask)
        ]
        sorted_tasks = self.relation_resolver.topological_sort_tasks(task_records)
        sort_order = {
            (t.notion_page_id or t.linear_id): i for i, t in enumerate(sorted_tasks)
        }
        sorted_changes = sorted(
            changeset.task_changes,
            key=lambda c: sort_order.get(c.record.notion_page_id or c.record.linear_id, 0),
        )
        for change in sorted_changes:
            if not self._should_apply(change, direction):
                continue
            try:
                await self._apply_task_change(change)
                if change.change_type == ChangeType.CREATE:
                    result.tasks_created += 1
                elif change.change_type == ChangeType.DELETE:
                    result.tasks_deleted += 1
                else:
                    result.tasks_updated += 1
            except Exception as e:
                logger.error(f"Failed task change ({change.identifier}): {e}", exc_info=True)
                result.errors.append(f"task {change.identifier}: {e}")

    @staticmethod
    def _should_apply(change: Change, direction: SyncDirection) -> bool:
        if direction == SyncDirection.NOTION_TO_LINEAR and change.source != SyncSource.NOTION:
            return False
        if direction == SyncDirection.LINEAR_TO_NOTION and change.source != SyncSource.LINEAR:
            return False
        return True

    async def _apply_project_change(self, change: Change) -> None:
        project = change.record
        assert isinstance(project, UnifiedProject)

        if change.change_type == ChangeType.CREATE:
            if change.target == SyncSource.LINEAR:
                linear_input = self.project_mapper.to_linear_input(project)
                linear_project = self.linear.create_project(
                    name=linear_input["name"],
                    description=linear_input.get("description"),
                    state=linear_input.get("state", "started"),
                    start_date=linear_input.get("startDate"),
                )
                project.linear_id = linear_project["id"]
                project.linear_last_modified = _parse_timestamp(linear_project.get("updatedAt"))
                if project.notion_page_id:
                    self.notion.set_linear_id(project.notion_page_id, project.linear_id, "project")
                self.relation_resolver.maps.add_project(project.notion_page_id, project.linear_id)
            else:
                props = self.project_mapper.to_notion_properties(project)
                page = self.notion.create_page(self.config.notion.projects_db_id, props)
                project.notion_page_id = page["id"]
                project.notion_last_modified = _parse_timestamp(page.get("last_edited_time"))
                self.relation_resolver.maps.add_project(project.notion_page_id, project.linear_id)

        elif change.change_type in (ChangeType.UPDATE, ChangeType.CONFLICT):
            if change.target == SyncSource.LINEAR and project.linear_id:
                linear_input = self.project_mapper.to_linear_input(project, for_update=True)
                linear_project = self.linear.update_project(project.linear_id, **linear_input)
                project.linear_last_modified = _parse_timestamp(linear_project.get("updatedAt"))
            elif change.target == SyncSource.NOTION and project.notion_page_id:
                props = self.project_mapper.to_notion_properties(project)
                page = self.notion.update_page(project.notion_page_id, props)
                project.notion_last_modified = _parse_timestamp(page.get("last_edited_time"))
        elif change.change_type == ChangeType.DELETE:
            if change.target == SyncSource.LINEAR and project.linear_id:
                self.linear.delete_project(project.linear_id)
            elif change.target == SyncSource.NOTION and project.notion_page_id:
                self.notion.trash_page(project.notion_page_id)
            self._delete_sync_state("project", project)
            return

        self._save_sync_state("project", project)

    async def _apply_milestone_change(self, change: Change) -> None:
        milestone = change.record
        assert isinstance(milestone, UnifiedMilestone)

        self.relation_resolver.resolve_milestone_relations(milestone, change.source)

        if change.change_type == ChangeType.CREATE:
            if change.target == SyncSource.LINEAR:
                if not milestone.project_linear_id:
                    logger.warning(
                        f"Skipping milestone '{milestone.name}': no Linear project ID resolved"
                    )
                    return
                linear_input = self.milestone_mapper.to_linear_input(milestone)
                linear_milestone = self.linear.create_milestone(
                    project_id=milestone.project_linear_id,
                    name=linear_input["name"],
                    description=linear_input.get("description"),
                    target_date=linear_input.get("targetDate"),
                )
                milestone.linear_id = linear_milestone["id"]
                milestone.linear_last_modified = _parse_timestamp(
                    linear_milestone.get("updatedAt")
                )
                if milestone.notion_page_id:
                    self.notion.set_linear_id(
                        milestone.notion_page_id, milestone.linear_id, "milestone"
                    )
                self.relation_resolver.maps.add_milestone(
                    milestone.notion_page_id, milestone.linear_id
                )
            else:
                props = self.milestone_mapper.to_notion_properties(milestone)
                props.update(
                    self.relation_resolver.get_milestone_relation_properties(
                        milestone, self.config.notion
                    )
                )
                page = self.notion.create_page(self.config.notion.milestones_db_id, props)
                milestone.notion_page_id = page["id"]
                milestone.notion_last_modified = _parse_timestamp(page.get("last_edited_time"))
                self.relation_resolver.maps.add_milestone(
                    milestone.notion_page_id, milestone.linear_id
                )

        elif change.change_type in (ChangeType.UPDATE, ChangeType.CONFLICT):
            if change.target == SyncSource.LINEAR and milestone.linear_id:
                linear_input = self.milestone_mapper.to_linear_input(milestone, for_update=True)
                linear_milestone = self.linear.update_milestone(milestone.linear_id, **linear_input)
                milestone.linear_last_modified = _parse_timestamp(
                    linear_milestone.get("updatedAt")
                )
            elif change.target == SyncSource.NOTION and milestone.notion_page_id:
                props = self.milestone_mapper.to_notion_properties(milestone)
                page = self.notion.update_page(milestone.notion_page_id, props)
                milestone.notion_last_modified = _parse_timestamp(page.get("last_edited_time"))
        elif change.change_type == ChangeType.DELETE:
            if change.target == SyncSource.LINEAR and milestone.linear_id:
                self.linear.delete_milestone(milestone.linear_id)
            elif change.target == SyncSource.NOTION and milestone.notion_page_id:
                self.notion.trash_page(milestone.notion_page_id)
            self._delete_sync_state("milestone", milestone)
            return

        self._save_sync_state("milestone", milestone)

    async def _apply_task_change(self, change: Change) -> None:
        task = change.record
        assert isinstance(task, UnifiedTask)

        self.relation_resolver.resolve_task_relations(task, change.source)

        if change.change_type == ChangeType.CREATE:
            if change.target == SyncSource.LINEAR:
                state_id = self._resolve_linear_state_id(task)
                label_ids = self._resolve_linear_label_ids(task) or None
                linear_issue = self.linear.create_issue(
                    title=task.title,
                    description=task.description,
                    priority=task.priority_linear or 0,
                    due_date=task.due_date.date().isoformat() if task.due_date else None,
                    project_id=task.project_linear_id,
                    milestone_id=task.milestone_linear_id,
                    parent_id=task.parent_task_linear_id,
                    state_id=state_id,
                    label_ids=label_ids,
                )
                task.linear_id = linear_issue["id"]
                task.linear_url = linear_issue.get("url")
                task.linear_last_modified = _parse_timestamp(linear_issue.get("updatedAt"))
                state = linear_issue.get("state") or {}
                task.linear_state_id = state.get("id")
                task.linear_state_name = state.get("name")
                task.linear_state_type = state.get("type")
                if task.notion_page_id:
                    update_props: dict = {
                        self.config.notion.task_linear_id_prop: {
                            "rich_text": [{"text": {"content": task.linear_id}}]
                        }
                    }
                    if task.linear_url:
                        update_props[self.config.notion.task_url_prop] = {"url": task.linear_url}
                    self.notion.update_page(task.notion_page_id, update_props)
                self.relation_resolver.maps.add_task(task.notion_page_id, task.linear_id)
            else:
                props = self.task_mapper.to_notion_properties(task)
                props.update(
                    self.relation_resolver.get_notion_relation_properties(task, self.config.notion)
                )
                page = self.notion.create_page(self.config.notion.tasks_db_id, props)
                task.notion_page_id = page["id"]
                task.notion_last_modified = _parse_timestamp(page.get("last_edited_time"))
                self.relation_resolver.maps.add_task(task.notion_page_id, task.linear_id)

        elif change.change_type in (ChangeType.UPDATE, ChangeType.CONFLICT):
            if change.target == SyncSource.LINEAR and task.linear_id:
                update_input: dict = {}
                if task.title:
                    update_input["title"] = task.title
                if task.description is not None:
                    update_input["description"] = task.description
                if task.priority_linear is not None:
                    update_input["priority"] = task.priority_linear
                if task.due_date:
                    update_input["dueDate"] = task.due_date.date().isoformat()
                state_id = self._resolve_linear_state_id(task)
                if state_id:
                    update_input["stateId"] = state_id
                label_ids = self._resolve_linear_label_ids(task) or None
                if label_ids:
                    update_input["labelIds"] = label_ids
                linear_issue = self.linear.update_issue(task.linear_id, **update_input)
                task.linear_last_modified = _parse_timestamp(linear_issue.get("updatedAt"))
                task.linear_url = linear_issue.get("url") or task.linear_url
                state = linear_issue.get("state") or {}
                task.linear_state_id = state.get("id")
                task.linear_state_name = state.get("name")
                task.linear_state_type = state.get("type")
            elif change.target == SyncSource.NOTION and task.notion_page_id:
                props = self.task_mapper.to_notion_properties(task)
                page = self.notion.update_page(task.notion_page_id, props)
                task.notion_last_modified = _parse_timestamp(page.get("last_edited_time"))
        elif change.change_type == ChangeType.DELETE:
            if change.target == SyncSource.LINEAR and task.linear_id:
                self.linear.delete_issue(task.linear_id)
            elif change.target == SyncSource.NOTION and task.notion_page_id:
                self.notion.trash_page(task.notion_page_id)
            self._delete_sync_state("task", task)
            return

        self._save_sync_state("task", task)

    def _resolve_linear_state_id(self, task: UnifiedTask) -> Optional[str]:
        workflow_states = self.linear.get_workflow_states()
        return self.task_mapper.resolve_linear_state_id(task, workflow_states)

    def _resolve_linear_label_ids(self, task: UnifiedTask) -> list[str]:
        if not task.task_type:
            return []
        return self.task_mapper.get_label_ids_for_type(task.task_type, self.linear.get_labels())

    def _save_sync_state(self, entity_type: str, record) -> None:
        if not record.notion_page_id and not record.linear_id:
            return
        self.state_store.upsert(
            SyncState(
                entity_type=entity_type,
                notion_id=record.notion_page_id,
                linear_id=record.linear_id,
                notion_last_modified=record.notion_last_modified,
                linear_last_modified=record.linear_last_modified,
                content_hash=SyncState.compute_hash(record.to_sync_hash_dict()),
                last_synced=datetime.utcnow(),
            )
        )

    def _delete_sync_state(self, entity_type: str, record) -> None:
        if record.notion_page_id:
            self.state_store.delete(entity_type, notion_id=record.notion_page_id)
            return
        if record.linear_id:
            self.state_store.delete(entity_type, linear_id=record.linear_id)
